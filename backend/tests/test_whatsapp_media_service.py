import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services import whatsapp_media_service as media_service

RealAsyncClient = httpx.AsyncClient
MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/media123"


def make_settings(tmp_path, **overrides):
    values = {
        "whatsapp_access_token": "test-access-token",
        "whatsapp_api_version": "v23.0",
        "media_storage_dir": tmp_path,
        "whatsapp_media_max_size_bytes": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    timeout_values = []

    class MockAsyncClient(RealAsyncClient):
        def __init__(self, timeout):
            timeout_values.append(timeout)
            super().__init__(transport=transport, timeout=timeout)

    monkeypatch.setattr(
        media_service.httpx,
        "AsyncClient",
        MockAsyncClient,
    )
    return timeout_values


def metadata(
    content,
    *,
    mime_type="image/jpeg",
    file_size=None,
    url=MEDIA_URL,
):
    return {
        "url": url,
        "mime_type": mime_type,
        "file_size": len(content) if file_size is None else file_size,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_downloads_media_with_auth_and_stores_atomically(
    monkeypatch,
    tmp_path,
):
    content = b"valid image data"
    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.host == "graph.facebook.com":
            lookup_data = metadata(content)
            lookup_data["file_size"] = str(len(content))
            return httpx.Response(
                200,
                json=lookup_data,
                request=request,
            )
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    timeout_values = install_transport(monkeypatch, handler)
    settings = make_settings(tmp_path)

    result = asyncio.run(media_service.download_whatsapp_media("media123", settings))

    stored_path = tmp_path / result.storage_key
    assert result.media_id == "media123"
    assert result.mime_type == "image/jpeg"
    assert result.size_bytes == len(content)
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert stored_path.is_file()
    assert stored_path.read_bytes() == content
    assert Path(result.storage_key).name == result.storage_key
    assert not Path(result.storage_key).is_absolute()
    assert list(tmp_path.iterdir()) == [stored_path]
    assert len(calls) == 2
    assert calls[0].url == ("https://graph.facebook.com/v23.0/media123")
    assert all(
        call.headers["Authorization"] == "Bearer test-access-token" for call in calls
    )
    assert timeout_values == [media_service.HTTP_TIMEOUT_SECONDS]


def test_media_lookup_http_failure_leaves_no_files(monkeypatch, tmp_path):
    async def handler(request):
        return httpx.Response(401, request=request)

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="lookup failed with HTTP 401",
    ) as exc_info:
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert not isinstance(
        exc_info.value,
        media_service.WhatsAppMediaTransientError,
    )
    assert list(tmp_path.iterdir()) == []


def test_media_lookup_timeout_is_reported(monkeypatch, tmp_path):
    async def handler(request):
        raise httpx.ConnectTimeout("connection timed out")

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="lookup timed out",
    ) as exc_info:
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert isinstance(exc_info.value, media_service.WhatsAppMediaTransientError)
    assert list(tmp_path.iterdir()) == []


def test_media_download_http_failure_leaves_no_files(monkeypatch, tmp_path):
    async def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json=metadata(b"image"),
                request=request,
            )
        return httpx.Response(503, request=request)

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="download failed with HTTP 503",
    ) as exc_info:
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert isinstance(exc_info.value, media_service.WhatsAppMediaTransientError)
    assert list(tmp_path.iterdir()) == []


def test_storage_directory_error_is_reported(monkeypatch, tmp_path):
    storage_path = tmp_path / "not-a-directory"
    storage_path.write_text("occupied")

    async def handler(request):
        return httpx.Response(
            200,
            json=metadata(b"image"),
            request=request,
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="Could not store WhatsApp media locally",
    ):
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(storage_path),
            )
        )

    assert storage_path.read_text() == "occupied"


@pytest.mark.parametrize(
    ("lookup_data", "expected_error"),
    [
        ("not-json", "invalid JSON"),
        ([], "malformed data"),
        (
            {
                "url": "http://lookaside.fbsbx.com/file",
                "mime_type": "image/jpeg",
                "file_size": 5,
            },
            "invalid URL",
        ),
        (
            {
                "url": MEDIA_URL,
                "mime_type": "image/jpeg",
                "file_size": "not-a-number",
            },
            "invalid file size",
        ),
    ],
)
def test_invalid_lookup_data_leaves_no_files(
    monkeypatch,
    tmp_path,
    lookup_data,
    expected_error,
):
    async def handler(request):
        if lookup_data == "not-json":
            return httpx.Response(
                200,
                content=b"not-json",
                headers={"content-type": "application/json"},
                request=request,
            )
        return httpx.Response(200, json=lookup_data, request=request)

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match=expected_error,
    ):
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert list(tmp_path.iterdir()) == []


def test_unsupported_mime_type_is_rejected(monkeypatch, tmp_path):
    async def handler(request):
        return httpx.Response(
            200,
            json=metadata(b"pdf", mime_type="application/pdf"),
            request=request,
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="unsupported MIME type",
    ):
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert list(tmp_path.iterdir()) == []


def test_incoming_mime_type_must_match_meta_media_type(
    monkeypatch,
    tmp_path,
):
    async def handler(request):
        return httpx.Response(
            200,
            json=metadata(b"image", mime_type="image/jpeg"),
            request=request,
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="does not match the incoming message metadata",
    ):
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
                expected_mime_type="image/png",
            )
        )

    assert list(tmp_path.iterdir()) == []


def test_oversized_metadata_is_rejected_before_download(
    monkeypatch,
    tmp_path,
):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=metadata(b"x" * 101),
            request=request,
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="configured maximum",
    ):
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert len(requests) == 1
    assert list(tmp_path.iterdir()) == []


def test_oversized_download_removes_temporary_file(monkeypatch, tmp_path):
    async def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json=metadata(b"small"),
                request=request,
            )
        return httpx.Response(
            200,
            content=b"x" * 101,
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(
        media_service.WhatsAppMediaDownloadError,
        match="configured maximum",
    ):
        asyncio.run(
            media_service.download_whatsapp_media(
                "media123",
                make_settings(tmp_path),
            )
        )

    assert list(tmp_path.iterdir()) == []


def test_downloads_supported_ogg_opus_audio(monkeypatch, tmp_path):
    content = b"ogg opus audio"

    async def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json=metadata(
                    content,
                    mime_type="audio/ogg; codecs=opus",
                ),
                request=request,
            )
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": "audio/ogg; codecs=opus"},
            request=request,
        )

    install_transport(monkeypatch, handler)
    result = asyncio.run(
        media_service.download_whatsapp_media(
            "media123",
            make_settings(tmp_path),
        )
    )

    assert result.mime_type == "audio/ogg"
    assert (tmp_path / result.storage_key).suffix == ".ogg"
    assert (tmp_path / result.storage_key).read_bytes() == content


def test_upload_guidance_uses_configured_settings(tmp_path):
    settings = make_settings(
        tmp_path,
        whatsapp_media_max_size_bytes=2_500_000,
    )
    guidance = media_service.get_media_upload_guidance(settings)

    assert guidance.image_types == ("JPEG", "PNG", "static WebP")
    assert guidance.audio_types == (
        "AAC",
        "AMR",
        "MP4",
        "MPEG",
        "OGG (Opus)",
    )
    assert guidance.max_size_bytes == 2_500_000
    assert guidance.max_size_display == "2.5 MB"
    assert guidance.format_message() == (
        "Supported images: JPEG, PNG, static WebP. "
        "Supported audio: AAC, AMR, MP4, MPEG, OGG (Opus). "
        "Maximum file size: 2.5 MB per file."
    )
