from app.core.config import Settings


def test_complaint_draft_expiry_hours_can_be_configured(monkeypatch):
    monkeypatch.setenv("COMPLAINT_DRAFT_EXPIRY_HOURS", "36")

    settings = Settings(_env_file=None)

    assert settings.complaint_draft_expiry_hours == 36
