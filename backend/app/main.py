import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.webhooks import router as webhook_router
from app.services.whatsapp_inbox_worker import run_inbound_inbox_worker
from app.services.whatsapp_outbox_worker import run_outbox_retry_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    workers = [
        asyncio.create_task(run_outbox_retry_worker()),
        asyncio.create_task(run_inbound_inbox_worker()),
    ]
    try:
        yield
    finally:
        for worker in workers:
            worker.cancel()
        results = await asyncio.gather(*workers, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result,
                asyncio.CancelledError,
            ):
                logger.error(
                    "Background worker stopped with %s.",
                    type(result).__name__,
                )


app = FastAPI(
    title="WhatsApp Complaint Registration System",
    description="Backend API for registering and managing complaints through WhatsApp.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router, prefix="/api/webhooks")


@app.get("/")
def root():
    return {"message": "Complaint Registration System API is running"}
