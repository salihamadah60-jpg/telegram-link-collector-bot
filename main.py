"""
main.py — Entry point for the Telegram Userbot.

Startup sequence:
  1. Connect to MongoDB
  2. Connect userbot (user account) — monitors all groups/channels automatically
  3. Connect control bot (bot token) — handles user interactions via single-message UI
  4. Discover all chats the user is in, filter out excluded ones
  5. Read 3 months of history per chat (resume-aware)
  6. Run real-time listener + control bot concurrently
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, Message

import config
import database
import history_reader
import message_handler
import human_behavior
import bot_interface
import pause_state
import scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("userbot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

_shutdown_event = asyncio.Event()


def _handle_signal(sig) -> None:
    logger.info("Signal %s received — shutting down…", sig)
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Chat discovery — all groups/channels the user is in
# ---------------------------------------------------------------------------

async def discover_chats(client: TelegramClient) -> list:
    """
    Returns all group/channel entities the user is a member of,
    minus any chats that are in any exclusion list:
      1. EXCLUDED_CHATS in .env (static config blacklist)
      2. 'excluded' DB collection (added by forwarding a message from the chat)
      3. 'blocked_links' DB collection (added by sending a group link to the bot)
    All three are kept separate for different future uses.
    """
    db_excluded = {row["chat_id"] for row in await database.get_excluded_chats()}
    db_blocked = {row["chat_id"] for row in await database.get_blocked_links()}

    chats = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity

        if not isinstance(entity, (Channel, Chat)):
            continue

        chat_id = str(entity.id)
        username = (getattr(entity, "username", None) or "").lower()

        # 1. .env static blacklist
        if username and username in config.EXCLUDED_CHATS:
            continue
        if chat_id.lstrip("-") in config.EXCLUDED_CHATS:
            continue
        # 2. DB exclusions (forwarded-message method)
        if chat_id in db_excluded:
            continue
        # 3. DB blocked-by-link
        if chat_id in db_blocked:
            continue

        chats.append(entity)

    logger.info("Discovered %d monitored chat(s) (excluding all blacklists)", len(chats))
    return chats


# ---------------------------------------------------------------------------
# Real-time listener (userbot)
# ---------------------------------------------------------------------------

def register_userbot_handlers(client: TelegramClient, monitored_ids: set[int]) -> None:
    """Register the real-time new-message handler for all monitored chats."""

    @client.on(events.NewMessage())
    async def on_message(event: events.NewMessage.Event) -> None:
        # Only process messages from chats we're monitoring
        if event.chat_id not in monitored_ids:
            return
        if not human_behavior.should_process_now():
            return

        msg: Message = event.message
        chat_id = str(event.chat_id)

        try:
            new = await message_handler.process_message(client, msg, chat_id)
            if new:
                logger.info("Real-time: %d new link(s) from chat %s", new, chat_id)
            await human_behavior.between_messages()
        except Exception as exc:
            logger.error("Error handling message from %s: %s", chat_id, exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("=== Telegram Userbot Starting ===")

    await database.connect()

    # Build both clients
    userbot = TelegramClient(
        config.SESSION_NAME,
        config.API_ID,
        config.API_HASH,
        app_version="9.3.3",
        device_model="PC 64bit",
        system_version="Windows 10",
        lang_code="en",
        system_lang_code="en-US",
    )
    control_bot = TelegramClient(
        config.BOT_SESSION_NAME,
        config.API_ID,
        config.API_HASH,
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            pass

    async with userbot, control_bot:
        # Start the userbot (user account)
        await userbot.start(phone=config.PHONE_NUMBER)
        me = await userbot.get_me()
        logger.info(
            "Userbot logged in as: %s %s (@%s)",
            me.first_name or "",
            me.last_name or "",
            me.username or "no username",
        )

        # Start the control bot (bot token)
        await control_bot.start(bot_token=config.BOT_TOKEN)
        logger.info("Control bot started")

        # Restore pause state from the previous session
        was_paused = await database.get_bot_paused()
        if was_paused:
            pause_state.pause()
            logger.info("Restored PAUSED state from previous session. Send /resume to continue.")
        else:
            logger.info("Bot starting in RUNNING state.")

        # Discover all chats the user is in
        chats = await discover_chats(userbot)
        monitored_ids = {entity.id for entity in chats}

        # Give the bot interface a reference to the userbot + chat count
        bot_interface.set_userbot(userbot, len(chats))

        # Register handlers
        register_userbot_handlers(userbot, monitored_ids)
        bot_interface.register_handlers(control_bot)

        logger.info("=== Both clients running. Starting history phase… ===")

        # Run history reading, scheduler, and control bot concurrently
        await asyncio.gather(
            history_reader.read_history_for_all(userbot, chats),
            scheduler.run_scheduler(),
            control_bot.run_until_disconnected(),
        )

    await database.disconnect()
    logger.info("=== Telegram Userbot Stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
