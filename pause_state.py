"""
pause_state.py — Shared pause/resume state for the entire bot.

Uses an asyncio.Event so the history reader can literally BLOCK mid-loop
when paused and continue from the exact same point when resumed.

Usage:
    import pause_state

    pause_state.pause()          # pause everything
    pause_state.resume()         # resume everything
    pause_state.is_paused()      # True if currently paused

    await pause_state.wait_if_paused()  # non-blocking if running, blocks if paused
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("pause_state")

# Set = running, Clear = paused
_running = asyncio.Event()
_running.set()

_paused_at: Optional[datetime] = None
_resumed_at: Optional[datetime] = None


def is_paused() -> bool:
    return not _running.is_set()


def pause() -> None:
    global _paused_at
    if not is_paused():
        _running.clear()
        _paused_at = datetime.now(timezone.utc)
        logger.info("Bot PAUSED at %s", _paused_at.strftime("%H:%M:%S UTC"))


def resume() -> None:
    global _resumed_at
    if is_paused():
        _running.set()
        _resumed_at = datetime.now(timezone.utc)
        logger.info("Bot RESUMED at %s", _resumed_at.strftime("%H:%M:%S UTC"))


async def wait_if_paused() -> None:
    """
    If the bot is running, returns immediately.
    If paused, blocks here until resume() is called.
    The history reader calls this before each message so it freezes
    naturally at the current position when paused.
    """
    await _running.wait()


def paused_since() -> Optional[str]:
    """Human-readable pause duration, or None if not paused."""
    if not is_paused() or _paused_at is None:
        return None
    delta = datetime.now(timezone.utc) - _paused_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        return f"{total_seconds // 60}m {total_seconds % 60}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"
