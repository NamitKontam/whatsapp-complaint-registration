import asyncio
import logging

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.whatsapp_outbox_service import dispatch_due_outbound_messages

logger = logging.getLogger(__name__)


async def run_outbox_retry_worker() -> None:
    """Poll the durable outbox and dispatch only rows whose retry time has arrived."""

    settings = get_settings()
    while True:
        try:
            db = SessionLocal()
            try:
                await dispatch_due_outbound_messages(db)
            finally:
                db.close()
        # A failed poll must be visible but must not stop later retry cycles.
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Outbox retry dispatch failed with %s.",
                type(exc).__name__,
            )

        await asyncio.sleep(settings.whatsapp_outbox_poll_interval_seconds)
