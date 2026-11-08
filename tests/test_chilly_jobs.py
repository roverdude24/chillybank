"""A failing ChillyBank job tells the family once per streak, and once when it recovers."""

from __future__ import annotations

import pytest

from backend.services import chilly_jobs


async def test_one_notice_per_failure_streak_and_one_on_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def fake_notify(message, channels=None, title=None):
        sent.append(message)
        return {}

    monkeypatch.setattr(chilly_jobs, "notify", fake_notify)
    monkeypatch.setattr(chilly_jobs, "_failing", {})
    outcomes = iter([RuntimeError("401 unauthorized"), RuntimeError("401 unauthorized"), None, None])

    async def job():
        err = next(outcomes)
        if err:
            raise err

    run = chilly_jobs.guarded("Đồng bộ Notion", job)
    for _ in range(4):
        await run()

    assert sent == ["Đồng bộ Notion đang lỗi: 401 unauthorized", "Đồng bộ Notion chạy lại bình thường."]
