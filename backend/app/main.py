import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.webhooks import router as webhook_router
from app.services.whatsapp_outbox_worker import run_outbox_retry_worker


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    retry_worker = asyncio.create_task(run_outbox_retry_worker())
    try:
        yield
    finally:
        retry_worker.cancel()
        try:
            await retry_worker
        except asyncio.CancelledError:
            pass


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
