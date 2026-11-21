
from app.api.routes.webhooks import router as webhook_router
from fastapi import FastAPI

app = FastAPI(
    title="WhatsApp Complaint Registration System",
    description="Backend API for registering and managing complaints through WhatsApp.",
    version="1.0.0",
)

app.include_router(webhook_router, prefix="/api/webhooks")


@app.get("/")
def root():
    return {"message": "Complaint Registration System API is running"}
