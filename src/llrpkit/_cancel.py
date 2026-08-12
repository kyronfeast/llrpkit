"""Internal: resurface task cancellations consumed by third-party code.

Python 3.11's ``asyncio.wait_for`` can catch an external ``Task.cancel()``
that races the awaited future completing and return the value instead of
re-raising (python/cpython#86296). llrpkit's own waits use ``asyncio.timeout``
and are immune, but dependencies (aiomqtt acks, HTTP clients) may still use
``wait_for`` internally. A swallowed cancel leaves ``Task.cancelling() > 0``
with nothing pending; calling :func:`resurface_swallowed_cancel` after each
third-party await turns "silently un-cancelled" back into prompt
cancellation. Background: QA-9/QA-11 in ``QA_REPORT.md``.
"""

from __future__ import annotations

import asyncio

__all__ = ["resurface_swallowed_cancel"]


def resurface_swallowed_cancel() -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
