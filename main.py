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

    # Build both clients separately — do NOT use `async with both` together
    # to avoid any session cross-contamination.
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

    # -----------------------------------------------------------------------
    # Start the userbot (user account — phone number auth)
    # -----------------------------------------------------------------------
    logger.info("Starting userbot with phone: %s", config.PHONE_NUMBER)
    await userbot.start(phone=config.PHONE_NUMBER)

    # Critical guard: make sure we logged in as a USER, not a bot.
    me = await userbot.get_me()
    if getattr(me, "bot", False):
        logger.error("=" * 60)
        logger.error("FATAL: userbot session contains a BOT TOKEN, not a user account!")
        logger.error("Logged in as: @%s", me.username or "unknown")
        logger.error("Auto-fixing: deleting corrupted session file and exiting.")
        logger.error("Please restart the bot — you will be prompted for your phone number.")
        logger.error("IMPORTANT: When prompted, enter your PHONE (+%s), NOT the bot token.", config.PHONE_NUMBER.lstrip("+"))
        logger.error("=" * 60)
        await userbot.disconnect()
        # Delete the corrupted session file so next start is clean
        import os as _os
        session_file = f"{config.SESSION_NAME}.session"
        if _os.path.exists(session_file):
            _os.remove(session_file)
            logger.error("Deleted corrupted session file: %s", session_file)
        await database.disconnect()
        sys.exit(1)

    logger.info(
        "Userbot logged in as: %s %s (@%s)",
        me.first_name or "",
        me.last_name or "",
        me.username or "no username",
    )

    # -----------------------------------------------------------------------
    # Start the control bot (bot token auth) — completely separate client
    # -----------------------------------------------------------------------
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

    try:
        # Run history reading, scheduler, and control bot concurrently
        await asyncio.gather(
            history_reader.read_history_for_all(userbot, chats),
            scheduler.run_scheduler(),
            control_bot.run_until_disconnected(),
        )
    finally:
        await userbot.disconnect()
        await control_bot.disconnect()
        await database.disconnect()
        logger.info("=== Telegram Userbot Stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
