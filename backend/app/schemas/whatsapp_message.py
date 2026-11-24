
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class MessageKind(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    LOCATION = "LOCATION"
    INTERACTIVE = "INTERACTIVE"
    UNSUPPORTED = "UNSUPPORTED"


class InteractiveReplyType(str, Enum):
    BUTTON = "BUTTON"
    LIST = "LIST"


class InteractiveReply(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    reply_type: InteractiveReplyType
    reply_id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class IncomingWhatsAppMessage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    whatsapp_number: str = Field(min_length=1)
    whatsapp_message_id: str = Field(min_length=1)

    timestamp: datetime
    message_type: MessageKind

    text_content: str | None = None

    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None

    latitude: Decimal | None = None
    longitude: Decimal | None = None

    location_name: str | None = None
    location_address: str | None = None

    interactive_reply: InteractiveReply | None = None

    raw_type: str | None = None

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must be timezone-aware")

        return value.astimezone(UTC)

    @field_validator("latitude")
    @classmethod
    def validate_latitude(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is not None and not Decimal(-90) <= value <= Decimal(90):
            raise ValueError("Latitude must be between -90 and 90")

        return value

    @field_validator("longitude")
    @classmethod
    def validate_longitude(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is not None and not Decimal(-180) <= value <= Decimal(180):
            raise ValueError("Longitude must be between -180 and 180")

        return value

    @model_validator(mode="after")
    def validate_message_content(self):
        if self.message_type == MessageKind.TEXT:
            if not self.text_content:
                raise ValueError("Text messages require text_content")

        elif self.message_type in {
            MessageKind.IMAGE,
            MessageKind.AUDIO,
        }:
            if not self.media_id:
                raise ValueError("Media messages require media_id")

        elif self.message_type == MessageKind.LOCATION:
            if self.latitude is None or self.longitude is None:
                raise ValueError(
                    "Location messages require latitude and longitude"
                )

        elif (
            self.message_type == MessageKind.INTERACTIVE
            and self.interactive_reply is None
        ):
            raise ValueError(
                "Interactive messages require interactive_reply"
            )

        return self

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
