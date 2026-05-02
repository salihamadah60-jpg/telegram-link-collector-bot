"""
message_handler.py — Processes a single Telegram message.

Shared by the real-time listener and the history reader.
Handles:
  - Extracting links from message text
  - Resolving public t.me/username entities (channel vs user)
  - Storing new links in the database
  - Triggering file send when threshold is reached
"""

import logging
from datetime import datetime
from typing import Optional

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User, Message

import config
import database
import document_manager
import human_behavior
import pause_state
from link_extractor import extract_links

logger = logging.getLogger("message_handler")


async def process_message(
    client: TelegramClient,
    message: Message,
    source_chat_id: str,
) -> int:
    """
    Process one message: extract links, resolve entities, store new links.
    Returns the number of NEW links added.
    Returns 0 immediately if the bot is paused.
    """
    if pause_state.is_paused():
        return 0

    text = message.text or message.message or ""
    if not text.strip():
        return 0

    raw_links = extract_links(text)
    if not raw_links:
        return 0

    new_count = 0
    for link in raw_links:
        if not link.is_invite and link.username:
            entity_type = await _resolve_username(client, link.username)
            if entity_type not in ("channel", "megagroup", "gigagroup"):
                logger.debug("Skipping user/bot link: %s", link.url)
                continue

        added = await database.add_link(
            url=link.url,
            link_type=link.link_type,
            source_chat=source_chat_id,
            message_id=message.id,
            message_date=message.date,
        )
        if added:
            new_count += 1

    if new_count > 0:
        await _check_and_send(client, "telegram")
        await _check_and_send(client, "whatsapp")

    return new_count


async def _resolve_username(client: TelegramClient, username: str) -> Optional[str]:
    """Resolve a Telegram username → entity type, using cache."""
    cached = await database.get_cached_entity_type(username)
    if cached is not None:
        return cached

    try:
        await human_behavior.jitter(0.8)
        entity = await client.get_entity(username)

        if isinstance(entity, Channel):
            etype = "megagroup" if (entity.megagroup or entity.gigagroup) else "channel"
        elif isinstance(entity, Chat):
            etype = "megagroup"
        elif isinstance(entity, User):
            etype = "bot" if entity.bot else "user"
        else:
            etype = "unknown"

        await database.cache_entity_type(username, etype)
        logger.debug("Resolved @%s → %s", username, etype)
        return etype

    except Exception as exc:
        logger.debug("Could not resolve @%s: %s", username, exc)
        await database.cache_entity_type(username, "unknown")
        return None


async def _check_and_send(client: TelegramClient, link_type: str) -> None:
    """
    If unsent links hit the threshold, build and send the .docx file
    to every configured per-user target chat (falls back to config.TARGET_CHAT_ID).
    """
    count = await database.count_unsent(link_type)
    if count < config.LINKS_PER_FILE:
        return

    logger.info("Threshold reached for %s (%d links). Building .docx…", link_type, count)
    try:
        filepath = await document_manager.build_and_save(link_type)
        caption = (
            f"📎 {link_type.capitalize()} links file\n"
            f"Contains {count} unique group/channel invite links."
        )

        # Collect all delivery targets — per-user settings take priority
        targets = await database.get_all_target_chats()
        if targets:
            target_ids = [t["target_chat_id"] for t in targets]
        else:
            target_ids = [config.TARGET_CHAT_ID]

        sent_targets: list[str] = []
        for target in target_ids:
            try:
                await client.send_file(target, filepath, caption=caption)
                sent_targets.append(str(target))
                logger.info("Sent %s to %s", filepath, target)
            except Exception as exc:
                logger.error("Failed to send to target %s: %s", target, exc)

        await database.mark_links_sent(link_type)
        for target in sent_targets:
            await database.log_sent_file(
                filename=filepath,
                link_type=link_type,
                link_count=count,
                target_chat=target,
            )
        await document_manager.cleanup_file(link_type)
        await human_behavior.after_sending_file()

    except Exception as exc:
        logger.error("Failed to send %s file: %s", link_type, exc)
