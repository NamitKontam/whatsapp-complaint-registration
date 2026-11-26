import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app import main
from app.services import whatsapp_outbox_worker


@pytest.mark.anyio
async def test_worker_continues_after_dispatch_exception(
    monkeypatch,
    caplog,
):
    created_sessions = [MagicMock(), MagicMock()]
    sessions = created_sessions.copy()
    monkeypatch.setattr(
        whatsapp_outbox_worker,
        "SessionLocal",
        lambda: sessions.pop(0),
    )
    monkeypatch.setattr(
        whatsapp_outbox_worker,
        "get_settings",
        lambda: SimpleNamespace(whatsapp_outbox_poll_interval_seconds=1),
    )
    dispatch = AsyncMock(
        side_effect=[
            RuntimeError("temporary test failure"),
            asyncio.CancelledError(),
        ]
    )
    monkeypatch.setattr(
        whatsapp_outbox_worker,
        "dispatch_due_outbound_messages",
        dispatch,
    )

    async def stop_after_one_poll(_):
        return None

    monkeypatch.setattr(
        whatsapp_outbox_worker,
        "asyncio",
        SimpleNamespace(sleep=stop_after_one_poll),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        await whatsapp_outbox_worker.run_outbox_retry_worker()

    assert dispatch.await_count == 2
    assert sessions == []
    assert all(db_session.close.called for db_session in created_sessions)
    assert "RuntimeError" in caplog.text


@pytest.mark.anyio
async def test_lifespan_cancels_outbox_worker_on_shutdown(monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def worker():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr(main, "run_outbox_retry_worker", worker)

    async with main.lifespan(FastAPI()):
        await started.wait()

    assert stopped.is_set()
