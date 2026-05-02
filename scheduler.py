"""
scheduler.py — Auto-pause / auto-resume based on a daily time window.

One global schedule is stored in MongoDB (set by any user via /schedule).
A background task wakes every minute, checks the current hour, and
automatically pauses or resumes the bot if the window has been crossed.

Schedule logic:
  - If current_hour is inside [start_hour, end_hour), bot should be RUNNING.
  - Otherwise, bot should be PAUSED.
  - Handles overnight windows (e.g. 22 to 6) correctly.
"""

import asyncio
import logging
from datetime import datetime, timezone

import database
import pause_state

logger = logging.getLogger("scheduler")

_CHECK_INTERVAL = 60  # seconds between checks


def _in_window(current_hour: int, start: int, end: int) -> bool:
    """Return True if current_hour is inside [start, end)."""
    if start <= end:
        return start <= current_hour < end
    else:
        # Overnight window e.g. 22 → 6
        return current_hour >= start or current_hour < end


async def run_scheduler() -> None:
    """Background task. Runs forever, sleeping between checks."""
    logger.info("Scheduler started (checks every %ds)", _CHECK_INTERVAL)
    while True:
        try:
            await _tick()
        except Exception as exc:
            logger.error("Scheduler tick error: %s", exc)
        await asyncio.sleep(_CHECK_INTERVAL)


async def _tick() -> None:
    schedule = await database.get_global_schedule()
    if not schedule or not schedule.get("enabled"):
        return  # No schedule configured

    start = schedule["start_hour"]
    end = schedule["end_hour"]
    current_hour = datetime.now().hour

    should_run = _in_window(current_hour, start, end)

    if should_run and pause_state.is_paused():
        logger.info("Schedule: entering active window (%02d:00). Resuming.", current_hour)
        pause_state.resume()
        await database.save_bot_paused(False)

    elif not should_run and not pause_state.is_paused():
        logger.info("Schedule: outside active window (%02d:00). Pausing.", current_hour)
        pause_state.pause()
        await database.save_bot_paused(True)
