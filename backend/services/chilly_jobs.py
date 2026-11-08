"""ChillyBank background jobs that must not fail silently.

The Notion sync and the TV push run every few minutes on a Mac nobody watches. A revoked Notion
token or a hub that refuses the push used to show up only in a log file while the TV quietly
went stale. Now the first failure of a streak raises one in-app notification, and the first
success after it says it recovered. A streak sends nothing more until it ends.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from backend.services.notify import notify

logger = logging.getLogger(__name__)

_failing: dict[str, bool] = {}


def guarded(label: str, job: Callable[[], Awaitable[object]]) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        try:
            await job()
        except Exception as exc:  # noqa: BLE001 — reported to the family instead of lost in a log
            logger.warning("%s failed: %s", label, exc, exc_info=True)
            if not _failing.get(label):
                _failing[label] = True
                await notify(f"{label} đang lỗi: {exc}", channels=["in_app"], title="ChillyBank")
            return
        if _failing.pop(label, False):
            await notify(f"{label} chạy lại bình thường.", channels=["in_app"], title="ChillyBank")

    return run
