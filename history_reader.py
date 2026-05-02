"""
history_reader.py — Reads historical messages from a list of chats.

For each chat, reads up to HISTORY_MONTHS months of messages,
resuming from the stored offset in MongoDB if available.

Pause behaviour:
  - Calls pause_state.wait_if_paused() before every message.
  - When paused, the loop freezes at the current position.
  - The current offset is saved immediately when a pause is detected,
    guaranteeing that resume continues from exactly that message.
  - Offsets are also saved every OFFSET_SAVE_INTERVAL messages for safety.
"""

import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient

import config
import database
import human_behavior
import message_handler
import pause_state

logger = logging.getLogger("history_reader")

BATCH_SIZE = 200
OFFSET_SAVE_INTERVAL = 10   # save offset to DB every N messages (not just every batch)


async def read_history_for_all(client: TelegramClient, chats: list) -> None:
    """
    Read history for a pre-built list of chat entities.
    Each chat is processed sequentially with a brief rest between them.
    """
    if not chats:
        logger.warning("No chats to read history for.")
        return

    logger.info("History phase: %d chat(s) to process", len(chats))
    for entity in chats:
        await _read_one_chat(client, entity)
        # Brief rest between chats — also yields to the event loop
        await human_behavior.jitter(5.0)

    logger.info("History phase complete.")


async def _read_one_chat(client: TelegramClient, entity) -> None:
    chat_id = str(entity.id)
    chat_name = getattr(entity, "title", None) or getattr(entity, "username", chat_id)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=config.HISTORY_MONTHS * 30)
    resume_offset = await database.get_offset(chat_id)

    logger.info(
        "Reading history for: %s (id=%s, resume_offset=%s)",
        chat_name, chat_id, resume_offset,
    )

    total_processed = 0
    total_new_links = 0
    last_message_id = resume_offset or 0
    _was_paused = False   # track transitions to avoid double-saving

    kwargs: dict = {"limit": BATCH_SIZE}
    if resume_offset:
        kwargs["min_id"] = resume_offset

    async for message in client.iter_messages(entity, reverse=bool(resume_offset), **kwargs):

        # ── Pause checkpoint ──────────────────────────────────────────────
        if pause_state.is_paused():
            if not _was_paused:
                # First frame of a new pause — save the offset RIGHT NOW
                if last_message_id:
                    await database.set_offset(chat_id, last_message_id)
                    logger.info(
                        "[%s] Paused — offset saved at message_id=%d",
                        chat_name, last_message_id,
                    )
                _was_paused = True
            # Block here until resumed; the loop picks up from this message
            await pause_state.wait_if_paused()
            _was_paused = False
            logger.info("[%s] Resumed — continuing from message_id=%d", chat_name, message.id)
        # ─────────────────────────────────────────────────────────────────

        if message.date and message.date.replace(tzinfo=timezone.utc) < cutoff_date:
            logger.info("[%s] Reached cutoff date, stopping.", chat_name)
            break

        total_processed += 1
        new = await message_handler.process_message(client, message, chat_id)
        total_new_links += new

        if message.id > last_message_id:
            last_message_id = message.id

        # Save offset frequently so a crash/stop loses minimal progress
        if total_processed % OFFSET_SAVE_INTERVAL == 0 and last_message_id:
            await database.set_offset(chat_id, last_message_id)

        if total_processed % BATCH_SIZE == 0:
            logger.info(
                "[%s] %d messages processed, %d new links",
                chat_name, total_processed, total_new_links,
            )
            await human_behavior.between_batches()
            await human_behavior.off_hours_check()

        await human_behavior.between_messages()

    # Final offset save
    if last_message_id:
        await database.set_offset(chat_id, last_message_id)

    logger.info(
        "Done with %s: %d messages, %d new links",
        chat_name, total_processed, total_new_links,
    )
