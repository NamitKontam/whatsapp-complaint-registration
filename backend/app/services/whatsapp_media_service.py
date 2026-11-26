import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings, get_settings

HTTP_TIMEOUT_SECONDS = 15.0
MEDIA_LOOKUP_HOST = "graph.facebook.com"
MEDIA_DOWNLOAD_HOST = "lookaside.fbsbx.com"


@dataclass(frozen=True)
class _SupportedMediaType:
    mime_type: str
    label: str
    extension: str


_IMAGE_TYPES = (
    _SupportedMediaType("image/jpeg", "JPEG", ".jpg"),
    _SupportedMediaType("image/png", "PNG", ".png"),
    _SupportedMediaType("image/webp", "static WebP", ".webp"),
)
_AUDIO_TYPES = (
    _SupportedMediaType("audio/aac", "AAC", ".aac"),
    _SupportedMediaType("audio/amr", "AMR", ".amr"),
    _SupportedMediaType("audio/mp4", "MP4", ".m4a"),
    _SupportedMediaType("audio/mpeg", "MPEG", ".mp3"),
    _SupportedMediaType("audio/ogg", "OGG (Opus)", ".ogg"),
)
_SUPPORTED_TYPES = {item.mime_type: item for item in (*_IMAGE_TYPES, *_AUDIO_TYPES)}

SUPPORTED_IMAGE_MIME_TYPES = tuple(item.mime_type for item in _IMAGE_TYPES)
SUPPORTED_AUDIO_MIME_TYPES = tuple(item.mime_type for item in _AUDIO_TYPES)
SUPPORTED_MEDIA_MIME_TYPES = frozenset(_SUPPORTED_TYPES)


class WhatsAppMediaDownloadError(Exception):
    """Raised when WhatsApp media cannot be safely downloaded and stored."""


@dataclass(frozen=True)
class WhatsAppMediaDownload:
    storage_key: str
    media_id: str
    mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class MediaUploadGuidance:
    image_types: tuple[str, ...]
    audio_types: tuple[str, ...]
    max_size_bytes: int

    @property
    def max_size_display(self) -> str:
        size_mb = self.max_size_bytes / 1_000_000
        formatted_size = f"{size_mb:.1f}".rstrip("0").rstrip(".")
        return f"{formatted_size} MB"

    def format_message(self) -> str:
        image_types = ", ".join(self.image_types)
        audio_types = ", ".join(self.audio_types)
        return (
            f"Supported images: {image_types}. "
            f"Supported audio: {audio_types}. "
            f"Maximum file size: {self.max_size_display} per file."
        )


def get_media_upload_guidance(
    settings: Settings | None = None,
) -> MediaUploadGuidance:
    """Return user-facing file type and size guidance from service rules."""
    settings = settings or get_settings()
    return MediaUploadGuidance(
        image_types=tuple(item.label for item in _IMAGE_TYPES),
        audio_types=tuple(item.label for item in _AUDIO_TYPES),
        max_size_bytes=settings.whatsapp_media_max_size_bytes,
    )


def _validate_mime_type(value: object) -> tuple[str, _SupportedMediaType]:
    if not isinstance(value, str) or not value.strip():
        raise WhatsAppMediaDownloadError(
            "WhatsApp media metadata has no valid MIME type"
        )

    parts = value.split(";")
    mime_type = parts[0].strip().lower()
    media_type = _SUPPORTED_TYPES.get(mime_type)
    if media_type is None:
        raise WhatsAppMediaDownloadError("WhatsApp media has an unsupported MIME type")

    if mime_type == "audio/ogg":
        parameters = [part.strip().lower() for part in parts[1:] if part.strip()]
        if any(parameter != "codecs=opus" for parameter in parameters):
            raise WhatsAppMediaDownloadError(
                "WhatsApp OGG audio must use the Opus codec"
            )
    elif any(part.strip() for part in parts[1:]):
        raise WhatsAppMediaDownloadError(
            "WhatsApp media metadata has malformed MIME parameters"
        )

    return mime_type, media_type


def _validate_media_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned an invalid URL"
        )

    try:
        parsed_url = urlsplit(value)
        port = parsed_url.port
    except ValueError as err:
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned an invalid URL"
        ) from err

    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != MEDIA_DOWNLOAD_HOST
        or parsed_url.username is not None
        or parsed_url.password is not None
        or port not in (None, 443)
        or not parsed_url.path
        or parsed_url.fragment
    ):
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned an invalid URL"
        )

    return value


def _validate_file_size(value: object, max_size_bytes: int) -> int:
    if isinstance(value, bool):
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned an invalid file size"
        )
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if not isinstance(value, int) or value <= 0:
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned an invalid file size"
        )
    if value > max_size_bytes:
        raise WhatsAppMediaDownloadError(
            "WhatsApp media exceeds the configured maximum file size"
        )
    return value


async def _lookup_media(
    client: httpx.AsyncClient,
    media_id: str,
    api_version: str,
    headers: dict[str, str],
    max_size_bytes: int,
) -> tuple[str, str, int, str | None]:
    lookup_url = (
        f"https://{MEDIA_LOOKUP_HOST}/"
        f"{quote(api_version, safe='')}/{quote(media_id, safe='')}"
    )
    try:
        response = await client.get(lookup_url, headers=headers)
        response.raise_for_status()
    except httpx.TimeoutException as err:
        raise WhatsAppMediaDownloadError("WhatsApp media lookup timed out") from err
    except httpx.HTTPStatusError as err:
        raise WhatsAppMediaDownloadError(
            f"WhatsApp media lookup failed with HTTP {err.response.status_code}"
        ) from err
    except httpx.RequestError as err:
        raise WhatsAppMediaDownloadError(
            "Could not connect to WhatsApp media lookup"
        ) from err

    try:
        data = response.json()
    except ValueError as err:
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned invalid JSON"
        ) from err

    if not isinstance(data, dict):
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned malformed data"
        )

    media_url = _validate_media_url(data.get("url"))
    mime_type, _ = _validate_mime_type(data.get("mime_type"))
    size_bytes = _validate_file_size(
        data.get("file_size"),
        max_size_bytes,
    )

    expected_sha256 = data.get("sha256")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256) is None
    ):
        raise WhatsAppMediaDownloadError(
            "WhatsApp media lookup returned an invalid SHA-256 digest"
        )

    return media_url, mime_type, size_bytes, expected_sha256


async def download_whatsapp_media(
    media_id: str,
    settings: Settings | None = None,
    *,
    expected_mime_type: str | None = None,
) -> WhatsAppMediaDownload:
    """Download a supported WhatsApp image/audio file to local storage."""
    settings = settings or get_settings()
    if not settings.whatsapp_access_token:
        raise WhatsAppMediaDownloadError("WhatsApp access token is not configured")
    if not settings.whatsapp_api_version:
        raise WhatsAppMediaDownloadError("WhatsApp API version is not configured")
    if not isinstance(media_id, str) or not media_id.strip():
        raise WhatsAppMediaDownloadError("WhatsApp media ID is invalid")
    media_id = media_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", media_id) is None:
        raise WhatsAppMediaDownloadError("WhatsApp media ID is invalid")

    max_size_bytes = settings.whatsapp_media_max_size_bytes
    if (
        isinstance(max_size_bytes, bool)
        or not isinstance(max_size_bytes, int)
        or max_size_bytes <= 0
    ):
        raise WhatsAppMediaDownloadError("WhatsApp media maximum file size is invalid")

    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    storage_dir = Path(settings.media_storage_dir)
    temporary_path: Path | None = None

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
        ) as client:
            media_url, mime_type, expected_size, expected_sha256 = await _lookup_media(
                client,
                media_id,
                settings.whatsapp_api_version,
                headers,
                max_size_bytes,
            )
            if expected_mime_type is not None:
                incoming_mime_type, _ = _validate_mime_type(expected_mime_type)
                if incoming_mime_type != mime_type:
                    raise WhatsAppMediaDownloadError(
                        "WhatsApp media MIME type does not match the "
                        "incoming message metadata"
                    )

            storage_dir.mkdir(parents=True, exist_ok=True)
            media_type = _SUPPORTED_TYPES[mime_type]
            storage_key = f"{uuid.uuid4().hex}{media_type.extension}"
            final_path = storage_dir / storage_key
            digest = hashlib.sha256()
            downloaded_size = 0

            async with client.stream(
                "GET",
                media_url,
                headers=headers,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as err:
                    raise WhatsAppMediaDownloadError(
                        f"WhatsApp media download failed with HTTP "
                        f"{err.response.status_code}"
                    ) from err

                download_mime_type = response.headers.get("content-type")
                if download_mime_type:
                    download_mime_type = (
                        download_mime_type.split(
                            ";",
                            maxsplit=1,
                        )[0]
                        .strip()
                        .lower()
                    )
                    if download_mime_type != "application/octet-stream":
                        actual_mime_type, _ = _validate_mime_type(download_mime_type)
                        if actual_mime_type != mime_type:
                            raise WhatsAppMediaDownloadError(
                                "Downloaded media MIME type does not match "
                                "WhatsApp media metadata"
                            )

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".whatsapp-",
                    suffix=".part",
                    dir=storage_dir,
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    async for chunk in response.aiter_bytes():
                        downloaded_size += len(chunk)
                        if downloaded_size > max_size_bytes:
                            raise WhatsAppMediaDownloadError(
                                "Downloaded media exceeds the configured "
                                "maximum file size"
                            )
                        if downloaded_size > expected_size:
                            raise WhatsAppMediaDownloadError(
                                "Downloaded media is larger than its declared file size"
                            )
                        digest.update(chunk)
                        temporary_file.write(chunk)

            if downloaded_size != expected_size:
                raise WhatsAppMediaDownloadError(
                    "Downloaded media size does not match WhatsApp metadata"
                )

            sha256 = digest.hexdigest()
            if (
                expected_sha256 is not None
                and sha256.lower() != expected_sha256.lower()
            ):
                raise WhatsAppMediaDownloadError(
                    "Downloaded media failed SHA-256 validation"
                )

            os.replace(temporary_path, final_path)
            temporary_path = None

            return WhatsAppMediaDownload(
                storage_key=storage_key,
                media_id=media_id,
                mime_type=mime_type,
                size_bytes=downloaded_size,
                sha256=sha256,
            )
    except WhatsAppMediaDownloadError:
        raise
    except httpx.TimeoutException as err:
        raise WhatsAppMediaDownloadError("WhatsApp media download timed out") from err
    except httpx.RequestError as err:
        raise WhatsAppMediaDownloadError(
            "Could not connect to WhatsApp media download"
        ) from err
    except OSError as err:
        raise WhatsAppMediaDownloadError(
            "Could not store WhatsApp media locally"
        ) from err
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as err:
                raise WhatsAppMediaDownloadError(
                    "Could not remove temporary WhatsApp media file"
                ) from err


def remove_stored_whatsapp_media(
    storage_key: str,
    settings: Settings | None = None,
) -> None:
    """Remove a previously downloaded file using its generated storage key."""
    settings = settings or get_settings()
    if (
        re.fullmatch(
            r"[0-9a-f]{32}\.(?:jpg|png|webp|aac|amr|m4a|mp3|ogg)",
            storage_key,
        )
        is None
    ):
        raise WhatsAppMediaDownloadError("WhatsApp media storage key is invalid")

    try:
        (Path(settings.media_storage_dir) / storage_key).unlink(missing_ok=True)
    except OSError as err:
        raise WhatsAppMediaDownloadError(
            "Could not remove stored WhatsApp media"
        ) from err
