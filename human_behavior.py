"""
human_behavior.py — Randomised sleep patterns to mimic human-like timing.

Tuned to be fast but not robotic:
  - 0.3–1.5 s between individual messages
  - 3–10 s between batches
  - 15–45 s after sending a file
  - Small extra delay during off-hours
"""

import asyncio
import random
import logging
from datetime import datetime

logger = logging.getLogger("human_behavior")


async def between_messages() -> None:
    """Short pause between reading individual messages."""
    await asyncio.sleep(random.uniform(0.3, 1.5))


async def between_batches() -> None:
    """Pause between processing batches of messages."""
    delay = random.uniform(3.0, 10.0)
    logger.debug("Resting between batches for %.1fs", delay)
    await asyncio.sleep(delay)


async def after_sending_file() -> None:
    """Brief pause after sending a .docx file."""
    delay = random.uniform(15.0, 45.0)
    logger.info("Pausing %.0fs after file send", delay)
    await asyncio.sleep(delay)


async def off_hours_check() -> None:
    """Slightly slower processing between midnight and 7 AM."""
    hour = datetime.now().hour
    if 0 <= hour < 7:
        extra = random.uniform(1.0, 5.0)
        await asyncio.sleep(extra)


async def jitter(base: float, variance: float = 0.3) -> None:
    """Sleep for base ± variance*base seconds."""
    lo = base * (1 - variance)
    hi = base * (1 + variance)
    await asyncio.sleep(random.uniform(lo, hi))


def should_process_now() -> bool:
    """
    Returns True almost always; occasionally False during deep night
    to simulate a slightly idle account.
    """
    hour = datetime.now().hour
    if 0 <= hour < 7:
        return random.random() > 0.15
    return True
