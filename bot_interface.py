"""
bot_interface.py — Single-message control UI using a bot token.

Design principle:
  - Every interaction edits ONE persistent "dashboard" message per user.
  - Sending new messages to the bot is handled silently:
      the user's message is deleted immediately, the dashboard is updated.
  - No chat clutter — the user always sees only one message from the bot.

Commands / buttons:
  /start          → show dashboard (or create it)
  /stats          → send a compact snapshot that self-destructs in 10 seconds
  /export         → export current unsent links as .docx files, sent to you here
  /export all     → export ALL links (including already-sent) as .docx files
  /reset          → confirm then wipe all collected links (with optional history re-read)
  /pause          → freeze all processing; auto-saves current position
  /resume         → continue from the exact point where it was paused
  📊 Refresh      → update stats in place
  📤 Send Now     → force-send .docx files to target chat even if < threshold
  📥 Export to Me → send .docx files directly to this chat (does NOT mark as sent)
  🚫 Excluded     → view/manage excluded chats
  🔗 Recent Links → show last 10 links found
  ← Back          → return to main dashboard
"""

import asyncio
import logging
import os
import re
from typing import Optional

from telethon import TelegramClient, events, Button
from telethon.tl.types import Message, UpdateBotCallbackQuery, Channel, Chat

import config
import database
import document_manager
import human_behavior
import message_handler
import pause_state

logger = logging.getLogger("bot_interface")

# In-memory cache of the shared userbot client (set by main.py after startup)
_userbot: Optional[TelegramClient] = None
# Tracks how many chats the userbot is monitoring
_monitored_chat_count: int = 0

# Per-user interaction mode: "default" | "blocking" | "setting_target"
# Modes reset on bot restart (in-memory only by design)
_user_modes: dict[int, str] = {}


def set_userbot(client: TelegramClient, chat_count: int) -> None:
    global _userbot, _monitored_chat_count
    _userbot = client
    _monitored_chat_count = chat_count


# ---------------------------------------------------------------------------
# Dashboard text builder
# ---------------------------------------------------------------------------

async def _build_dashboard_text(user_id: Optional[int] = None) -> str:
    s = await database.get_stats()
    paused = pause_state.is_paused()
    since = pause_state.paused_since()

    if paused:
        status_line = f"⏸️ **PAUSED** (for {since})\n"
    else:
        status_line = "▶️ **Running**\n"

    # Per-user target chat
    if user_id:
        settings = await database.get_user_settings(user_id)
        target_title = (
            settings.get("target_chat_title")
            or settings.get("target_chat_id")
            or config.TARGET_CHAT_ID
        )
    else:
        target_title = config.TARGET_CHAT_ID

    # Global schedule
    schedule = await database.get_global_schedule()
    if schedule and schedule.get("enabled"):
        sh = schedule["schedule_start"]
        eh = schedule["schedule_end"]
        sch_line = f"⏰ Schedule: **{sh:02d}:00 – {eh:02d}:00** (daily)\n"
    else:
        sch_line = ""

    return (
        "🤖 **Link Collector Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_line}"
        f"{sch_line}"
        f"📡 Monitoring: **{_monitored_chat_count}** chats "
        f"({s['excluded_count']} excluded)\n"
        f"📤 Delivering to: **{target_title}**\n\n"
        f"🔵 Telegram links\n"
        f"   Unsent: **{s['tg_unsent']}** / Total: **{s['tg_total']}**\n\n"
        f"🟢 WhatsApp links\n"
        f"   Unsent: **{s['wa_unsent']}** / Total: **{s['wa_total']}**\n\n"
        f"📁 Files sent: **{s['files_sent']}**\n"
        f"🕐 Last activity: {s['last_activity']}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "_Send me any message containing links and I'll collect them. "
        "Your messages are deleted automatically to keep this chat clean._"
    )


def _dashboard_buttons() -> list:
    paused = pause_state.is_paused()
    pause_btn = (
        Button.inline("▶️ Resume", b"resume_btn")
        if paused
        else Button.inline("⏸️ Pause", b"pause_btn")
    )
    return [
        [Button.inline("📊 Refresh", b"refresh"), Button.inline("📤 Send Now", b"send_now")],
        [Button.inline("📥 Export to Me", b"export_now"), Button.inline("🔗 Recent Links", b"recent")],
        [Button.inline("🚫 Excluded Chats", b"excluded"), Button.inline("⛔ Block by Link", b"block_link_btn")],
        [Button.inline("📍 Set Target", b"set_target_btn"), Button.inline("📋 Blocked Links", b"view_blocked")],
        [pause_btn],
    ]


# ---------------------------------------------------------------------------
# Dashboard send / edit helpers
# ---------------------------------------------------------------------------

async def _show_dashboard(bot: TelegramClient, user_id: int, chat_id: int) -> None:
    """Send or edit the dashboard message for this user."""
    text = await _build_dashboard_text(user_id=user_id)
    buttons = _dashboard_buttons()

    existing_msg_id = await database.get_dashboard_message_id(user_id)
    if existing_msg_id:
        try:
            await bot.edit_message(chat_id, existing_msg_id, text, buttons=buttons, parse_mode="md")
            return
        except Exception:
            pass  # Message was deleted or too old — send a fresh one

    msg = await bot.send_message(chat_id, text, buttons=buttons, parse_mode="md")
    await database.set_dashboard_message_id(user_id, msg.id)


async def _edit_dashboard(bot: TelegramClient, event, text: str, buttons: Optional[list] = None) -> None:
    """Edit the dashboard message in response to a callback query."""
    if buttons is None:
        buttons = _dashboard_buttons()
    try:
        await event.edit(text, buttons=buttons, parse_mode="md")
    except Exception as exc:
        logger.debug("Could not edit dashboard: %s", exc)


async def _edit_or_show(bot: TelegramClient, event, text: str) -> None:
    """
    Used by command handlers (/pause, /resume) that receive a NewMessage event
    (not a CallbackQuery). Tries to edit the existing dashboard; falls back to
    sending a fresh one.
    """
    user_id = event.sender_id
    chat_id = event.chat_id
    buttons = _dashboard_buttons()
    existing_msg_id = await database.get_dashboard_message_id(user_id)
    if existing_msg_id:
        try:
            await bot.edit_message(chat_id, existing_msg_id, text, buttons=buttons, parse_mode="md")
            return
        except Exception:
            pass
    msg = await bot.send_message(chat_id, text, buttons=buttons, parse_mode="md")
    await database.set_dashboard_message_id(user_id, msg.id)


# ---------------------------------------------------------------------------
# Helpers for /stats
# ---------------------------------------------------------------------------

def _progress_bar(pct: int, width: int = 8) -> str:
    """Return a compact text progress bar, e.g. [████░░░░] 50%"""
    filled = round(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct}%"


async def _self_destruct(bot: TelegramClient, chat_id: int, message_id: int, delay: int) -> None:
    """Wait `delay` seconds then silently delete the message."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_messages(chat_id, [message_id])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Register all handlers on the bot client
# ---------------------------------------------------------------------------

def register_handlers(bot: TelegramClient) -> None:

    # ------------------------------------------------------------------
    # Internal helper: export .docx files directly to the user's chat
    # Does NOT mark links as sent — purely for the user's own copy.
    # ------------------------------------------------------------------
    async def _do_export(chat_id: int, user_id: int, include_sent: bool = False) -> None:
        sent_any = False
        label = "all" if include_sent else "unsent"
        for link_type in ("telegram", "whatsapp"):
            try:
                filepath, count = await document_manager.build_export_doc(
                    link_type, include_sent=include_sent
                )
                caption = (
                    f"📥 **{link_type.capitalize()} links** — {label} ({count} links)\n"
                    f"_This export does not mark links as sent._"
                )
                await bot.send_file(chat_id, filepath, caption=caption, parse_mode="md")
                os.remove(filepath)
                sent_any = True
            except ValueError:
                pass  # No links of this type — skip silently
            except Exception as exc:
                logger.error("Export error for %s: %s", link_type, exc)

        if not sent_any:
            await bot.send_message(
                chat_id,
                "ℹ️ **No links to export yet.** "
                "Use `/export all` to include previously sent links too.",
                parse_mode="md",
            )
        await _show_dashboard(bot, user_id, chat_id)

    # ------------------------------------------------------------------
    # /start command — show or refresh the dashboard
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/start$", func=lambda e: e.is_private))
    async def on_start(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        await _show_dashboard(bot, event.sender_id, event.chat_id)

    # ------------------------------------------------------------------
    # /export command — export .docx files directly to this chat right now
    #   /export       → unsent links only  (does NOT mark them as sent)
    #   /export all   → every link ever collected
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/export(?:\s+(.*))?$", func=lambda e: e.is_private))
    async def on_export_cmd(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        include_sent = "all" in (event.pattern_match.group(1) or "").lower()
        await _do_export(event.chat_id, event.sender_id, include_sent=include_sent)

    # ------------------------------------------------------------------
    # /stats command — compact snapshot, self-destructs in 10 seconds
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/stats$", func=lambda e: e.is_private))
    async def on_stats(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        s = await database.get_stats()
        tg_pct = min(100, round((s["tg_unsent"] / config.LINKS_PER_FILE) * 100))
        wa_pct = min(100, round((s["wa_unsent"] / config.LINKS_PER_FILE) * 100))
        tg_bar = _progress_bar(tg_pct)
        wa_bar = _progress_bar(wa_pct)
        text = (
            "⚡ **Quick Stats** _(disappears in 10 s)_\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔵 Telegram  {tg_bar} {s['tg_unsent']}/{config.LINKS_PER_FILE}\n"
            f"🟢 WhatsApp  {wa_bar} {s['wa_unsent']}/{config.LINKS_PER_FILE}\n"
            f"📡 Chats monitored: **{_monitored_chat_count}**\n"
            f"📁 Files sent: **{s['files_sent']}**\n"
            f"🕐 Last activity: {s['last_activity']}"
        )
        snap = await bot.send_message(event.chat_id, text, parse_mode="md")
        asyncio.create_task(_self_destruct(bot, event.chat_id, snap.id, delay=10))

    # ------------------------------------------------------------------
    # /pause command — freeze everything and auto-save current position
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/pause$", func=lambda e: e.is_private))
    async def on_pause_cmd(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        if pause_state.is_paused():
            text = await _build_dashboard_text()
            await _show_dashboard(bot, event.sender_id, event.chat_id)
            return
        pause_state.pause()
        await database.save_bot_paused(True)
        text = await _build_dashboard_text()
        note = "⏸️ **Paused.** Current read positions saved. Send /resume to continue."
        await _edit_or_show(bot, event, text + f"\n\n{note}")

    # ------------------------------------------------------------------
    # /resume command — continue from the saved position
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/resume$", func=lambda e: e.is_private))
    async def on_resume_cmd(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        if not pause_state.is_paused():
            await _show_dashboard(bot, event.sender_id, event.chat_id)
            return
        pause_state.resume()
        await database.save_bot_paused(False)
        text = await _build_dashboard_text()
        note = "▶️ **Resumed.** Continuing from saved position."
        await _edit_or_show(bot, event, text + f"\n\n{note}")

    # ------------------------------------------------------------------
    # /schedule command — set or show the daily active time window
    # Usage:
    #   /schedule 9 23   → active 09:00–23:00 every day
    #   /schedule off    → disable schedule
    #   /schedule        → show current setting
    # ------------------------------------------------------------------
    _SCHED_RE = re.compile(r"^/schedule(?:\s+(\d{1,2})\s+(\d{1,2})|\s+(off))?$")

    @bot.on(events.NewMessage(pattern=r"^/schedule", func=lambda e: e.is_private))
    async def on_schedule_cmd(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        user_id = event.sender_id
        chat_id = event.chat_id
        text_in = event.raw_text.strip()
        m = _SCHED_RE.match(text_in)

        if m and m.group(1) and m.group(2):
            start_h = int(m.group(1))
            end_h = int(m.group(2))
            if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
                note = "❌ Invalid hours. Use 0–23, e.g. `/schedule 9 23`."
            elif start_h == end_h:
                note = "❌ Start and end hour must differ."
            else:
                await database.set_user_schedule(user_id, start_h, end_h, enabled=True)
                note = (
                    f"⏰ Schedule set: **{start_h:02d}:00 – {end_h:02d}:00** daily.\n"
                    "The bot will auto-pause outside this window and auto-resume inside it."
                )
        elif m and m.group(3) == "off":
            await database.disable_all_schedules()
            note = "⏰ Schedule disabled. The bot will run continuously."
        else:
            sched = await database.get_global_schedule()
            if sched and sched.get("enabled"):
                sh = sched["schedule_start"]
                eh = sched["schedule_end"]
                note = (
                    f"⏰ Current schedule: **{sh:02d}:00 – {eh:02d}:00** daily.\n"
                    "Use `/schedule 9 23` to change, `/schedule off` to disable."
                )
            else:
                note = (
                    "⏰ No schedule set (running continuously).\n"
                    "Use `/schedule 9 23` to set a daily active window."
                )

        text = await _build_dashboard_text(user_id=user_id)
        await _edit_or_show(bot, event, text + f"\n\n{note}")

    # ------------------------------------------------------------------
    # Inline button: Pause (from dashboard)
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"pause_btn"))
    async def on_pause_btn(event) -> None:
        await event.answer("Paused — position saved.")
        pause_state.pause()
        await database.save_bot_paused(True)
        text = await _build_dashboard_text(user_id=event.sender_id)
        await _edit_dashboard(bot, event, text)

    # ------------------------------------------------------------------
    # Inline button: Resume (from dashboard)
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"resume_btn"))
    async def on_resume_btn(event) -> None:
        await event.answer("Resumed!")
        pause_state.resume()
        await database.save_bot_paused(False)
        text = await _build_dashboard_text(user_id=event.sender_id)
        await _edit_dashboard(bot, event, text)

    # ------------------------------------------------------------------
    # /reset command — asks for confirmation before wiping anything
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/reset$", func=lambda e: e.is_private))
    async def on_reset(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        s = await database.get_stats()
        text = (
            "⚠️ **Reset Confirmation**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "This will permanently delete collected links from the database.\n\n"
            f"🔵 Telegram links: **{s['tg_total']}** total\n"
            f"🟢 WhatsApp links: **{s['wa_total']}** total\n\n"
            "**Choose what to reset:**\n"
            "• **Wipe links** — delete all links, keep read history (bot won't re-read old messages)\n"
            "• **Wipe everything** — delete all links AND reset read positions "
            "(bot will re-read the last 3 months from scratch on next start)\n\n"
            "_This cannot be undone._"
        )
        buttons = [
            [Button.inline("🗑️ Wipe links only", b"confirm_reset")],
            [Button.inline("🗑️ Wipe everything + re-read history", b"confirm_reset_full")],
            [Button.inline("❌ Cancel", b"cancel_reset")],
        ]
        await _edit_dashboard(bot, event, text, buttons)

    # ------------------------------------------------------------------
    # Inline button: Confirm reset (links only)
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"confirm_reset"))
    async def on_confirm_reset(event) -> None:
        await event.answer("Wiping links…")
        result = await database.reset_all_links(also_reset_offsets=False)
        note = (
            f"✅ **Done.** Deleted **{result['telegram']}** Telegram "
            f"and **{result['whatsapp']}** WhatsApp links.\n"
            "_Read positions kept — bot will not re-read old messages._"
        )
        text = await _build_dashboard_text()
        await _edit_dashboard(bot, event, text + f"\n\n{note}")

    # ------------------------------------------------------------------
    # Inline button: Confirm reset (links + offsets)
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"confirm_reset_full"))
    async def on_confirm_reset_full(event) -> None:
        await event.answer("Wiping everything…")
        result = await database.reset_all_links(also_reset_offsets=True)
        note = (
            f"✅ **Done.** Deleted **{result['telegram']}** Telegram "
            f"and **{result['whatsapp']}** WhatsApp links.\n"
            "_Read positions also cleared — history will be re-read from scratch on next start._"
        )
        text = await _build_dashboard_text()
        await _edit_dashboard(bot, event, text + f"\n\n{note}")

    # ------------------------------------------------------------------
    # Inline button: Cancel reset
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"cancel_reset"))
    async def on_cancel_reset(event) -> None:
        await event.answer("Cancelled.")
        text = await _build_dashboard_text()
        await _edit_dashboard(bot, event, text)

    # ------------------------------------------------------------------
    # Any plain message sent by the user (links, text, etc.)
    # Delete it immediately, process any links found, update dashboard.
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(func=lambda e: e.is_private and not e.message.text.startswith("/")))
    async def on_user_message(event: events.NewMessage.Event) -> None:
        msg: Message = event.message
        user_id = event.sender_id
        chat_id = event.chat_id

        # Delete the user's message right away — keep the chat clean
        await _delete_user_message(bot, event)

        if not msg.text:
            return

        mode = _user_modes.get(user_id, "default")
        client = _userbot or bot

        # ----------------------------------------------------------
        # MODE: blocking — user is sending a group link to block
        # ----------------------------------------------------------
        if mode == "blocking":
            raw = msg.text.strip()
            # Extract a t.me link from whatever the user sent
            tme = re.search(r"(?:https?://)?t\.me/(?:joinchat/|invite/|[+])?([\w/-]+)", raw)
            if not tme:
                note = "⚠️ That doesn't look like a Telegram group link. Try again or press ✅ Done."
                text = await _build_dashboard_text(user_id=user_id)
                buttons = [[Button.inline("✅ Done Blocking", b"done_mode")]]
                existing = await database.get_dashboard_message_id(user_id)
                if existing:
                    try:
                        await bot.edit_message(chat_id, existing, text + f"\n\n{note}", buttons=buttons, parse_mode="md")
                        return
                    except Exception:
                        pass
                m2 = await bot.send_message(chat_id, text + f"\n\n{note}", buttons=buttons, parse_mode="md")
                await database.set_dashboard_message_id(user_id, m2.id)
                return

            url = raw if raw.startswith("http") else f"https://t.me/{tme.group(1)}"
            # Try to resolve the link to get a real chat entity
            resolved_id = ""
            resolved_title = url
            try:
                entity = await client.get_entity(url)
                resolved_id = str(entity.id)
                resolved_title = getattr(entity, "title", url)
            except Exception:
                resolved_id = tme.group(1)
                resolved_title = tme.group(1)

            await database.add_blocked_link(
                chat_id=resolved_id,
                title=resolved_title,
                url=url,
                added_by=user_id,
            )
            logger.info("Blocked by link: %s (%s) by user %s", resolved_title, resolved_id, user_id)
            note = (
                f"⛔ **Blocked:** {resolved_title}\n"
                "_This chat will no longer be monitored. Send another link or press ✅ Done._"
            )
            text = await _build_dashboard_text(user_id=user_id)
            buttons = [[Button.inline("✅ Done Blocking", b"done_mode")]]
            existing = await database.get_dashboard_message_id(user_id)
            if existing:
                try:
                    await bot.edit_message(chat_id, existing, text + f"\n\n{note}", buttons=buttons, parse_mode="md")
                    return
                except Exception:
                    pass
            m2 = await bot.send_message(chat_id, text + f"\n\n{note}", buttons=buttons, parse_mode="md")
            await database.set_dashboard_message_id(user_id, m2.id)
            return

        # ----------------------------------------------------------
        # MODE: setting_target — user is sending the target chat identifier
        # ----------------------------------------------------------
        if mode == "setting_target":
            raw = msg.text.strip()
            resolved_id = raw
            resolved_title = raw
            try:
                entity = await client.get_entity(raw)
                resolved_id = str(entity.id)
                resolved_title = getattr(entity, "title", None) or getattr(entity, "first_name", raw)
            except Exception:
                pass  # Store as-is if unresolvable (may still work for send_file)

            await database.set_user_target_chat(user_id, resolved_id, resolved_title)
            _user_modes.pop(user_id, None)
            logger.info("User %s set target chat: %s (%s)", user_id, resolved_title, resolved_id)
            note = f"📍 **Target set:** {resolved_title}\nFiles will be delivered here when the threshold is reached."
            text = await _build_dashboard_text(user_id=user_id)
            await _show_dashboard(bot, user_id, chat_id)
            return

        # ----------------------------------------------------------
        # MODE: default — collect any links in the message
        # ----------------------------------------------------------
        new_count = await message_handler.process_message(client, msg, f"dm_{user_id}")

        if new_count:
            logger.info("DM: %d new link(s) collected from user %s", new_count, user_id)

        # Refresh the dashboard to show updated counts
        await _show_dashboard(bot, user_id, chat_id)

    # ------------------------------------------------------------------
    # Inline button: 📥 Export to Me — export .docx to THIS chat (no mark-sent)
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"export_now"))
    async def on_export_now(event) -> None:
        await event.answer("Preparing export…")
        await _do_export(event.chat_id, event.sender_id, include_sent=False)

    # ------------------------------------------------------------------
    # Inline button: Refresh stats
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"refresh"))
    async def on_refresh(event) -> None:
        await event.answer("Refreshed!")
        text = await _build_dashboard_text(user_id=event.sender_id)
        await _edit_dashboard(bot, event, text)

    # ------------------------------------------------------------------
    # Inline button: Force-send files now
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"send_now"))
    async def on_send_now(event) -> None:
        await event.answer("Sending files…")
        client = _userbot or bot
        sent = []

        # Determine delivery targets (per-user settings, fallback to global)
        all_targets = await database.get_all_target_chats()
        target_ids = [t["target_chat_id"] for t in all_targets] if all_targets else [config.TARGET_CHAT_ID]

        for link_type in ("telegram", "whatsapp"):
            count = await database.count_unsent(link_type)
            if count == 0:
                continue
            try:
                filepath = await document_manager.build_and_save(link_type)
                caption = f"📎 {link_type.capitalize()} links ({count} total)"
                for target in target_ids:
                    try:
                        await client.send_file(target, filepath, caption=caption)
                        await database.log_sent_file(filepath, link_type, count, str(target))
                    except Exception as exc:
                        logger.error("Force-send to %s failed: %s", target, exc)
                await database.mark_links_sent(link_type)
                await document_manager.cleanup_file(link_type)
                sent.append(f"{link_type} ({count} links)")
            except Exception as exc:
                logger.error("Force-send failed for %s: %s", link_type, exc)

        if sent:
            note = "✅ Sent: " + ", ".join(sent)
        else:
            note = "ℹ️ No unsent links available to send."

        text = await _build_dashboard_text(user_id=event.sender_id)
        full_text = text + f"\n\n{note}"
        await _edit_dashboard(bot, event, full_text)

    # ------------------------------------------------------------------
    # Inline button: Excluded chats
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"excluded"))
    async def on_excluded(event) -> None:
        await event.answer()
        chats = await database.get_excluded_chats()

        if chats:
            lines = [f"  • {c['title']} (`{c['chat_id']}`)" for c in chats]
            body = "\n".join(lines)
        else:
            body = "_No chats excluded. All your groups/channels are being monitored._"

        text = (
            "🚫 **Excluded Chats**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{body}\n\n"
            "_To exclude a chat, forward any message from it here. "
            "To re-include it, forward again._"
        )
        buttons = [[Button.inline("← Back to Dashboard", b"back")]]
        await _edit_dashboard(bot, event, text, buttons)

    # ------------------------------------------------------------------
    # Inline button: Recent links
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"recent"))
    async def on_recent(event) -> None:
        await event.answer()
        links = await database.get_recent_links(limit=10)

        if links:
            lines = []
            for lnk in links:
                icon = "🔵" if lnk["link_type"] == "telegram" else "🟢"
                lines.append(f"{icon} {lnk['url']}")
            body = "\n".join(lines)
        else:
            body = "_No links collected yet._"

        text = (
            "🔗 **Recent Links (last 10)**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{body}"
        )
        buttons = [[Button.inline("← Back to Dashboard", b"back")]]
        await _edit_dashboard(bot, event, text, buttons)

    # ------------------------------------------------------------------
    # Inline button: Back to dashboard
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"back"))
    async def on_back(event) -> None:
        await event.answer()
        _user_modes.pop(event.sender_id, None)  # exit any active mode
        text = await _build_dashboard_text(user_id=event.sender_id)
        await _edit_dashboard(bot, event, text)

    # ------------------------------------------------------------------
    # Inline button: ⛔ Block by Link — enter blocking mode
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"block_link_btn"))
    async def on_block_link_btn(event) -> None:
        await event.answer()
        user_id = event.sender_id
        _user_modes[user_id] = "blocking"
        text = (
            "⛔ **Block by Link**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Send me a Telegram group link (e.g. `t.me/groupname` or `t.me/+xxxxx`).\n\n"
            "I'll resolve it and add it to the **blocked** list — "
            "the bot will skip that chat entirely.\n\n"
            "_You can send multiple links one at a time. "
            "This list is separate from the forwarded-message exclusions "
            "and from the `.env` blacklist._\n\n"
            "Press **✅ Done** when you're finished."
        )
        buttons = [[Button.inline("✅ Done Blocking", b"done_mode")]]
        await _edit_dashboard(bot, event, text, buttons)

    # ------------------------------------------------------------------
    # Inline button: 📍 Set Target Chat — enter target-setting mode
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"set_target_btn"))
    async def on_set_target_btn(event) -> None:
        await event.answer()
        user_id = event.sender_id
        _user_modes[user_id] = "setting_target"

        settings = await database.get_user_settings(user_id)
        current = settings.get("target_chat_title") or settings.get("target_chat_id") or config.TARGET_CHAT_ID

        text = (
            "📍 **Set Target Chat**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"Current target: **{current}**\n\n"
            "Send me any of:\n"
            "• A `@username` or `t.me/groupname` link\n"
            "• A numeric chat ID (e.g. `-1001234567890`)\n"
            "• Or forward **any message** from the target chat\n\n"
            "_Each user can have their own separate delivery target. "
            "When the threshold is reached, files are sent to every configured target._\n\n"
            "Press **❌ Cancel** to keep the current setting."
        )
        buttons = [[Button.inline("❌ Cancel", b"done_mode")]]
        await _edit_dashboard(bot, event, text, buttons)

    # ------------------------------------------------------------------
    # Inline button: 📋 View Blocked Links
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"view_blocked"))
    async def on_view_blocked(event) -> None:
        await event.answer()
        blocked = await database.get_blocked_links()

        if blocked:
            lines = [f"  • {b['title']} — `{b['url']}`" for b in blocked]
            body = "\n".join(lines)
        else:
            body = "_No chats blocked by link yet._"

        text = (
            "⛔ **Blocked by Link**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"{body}\n\n"
            "_These chats were blocked by sending their link to the bot. "
            "Separate from forwarded-message exclusions and `.env` blacklist._"
        )
        buttons = [[Button.inline("← Back to Dashboard", b"back")]]
        await _edit_dashboard(bot, event, text, buttons)

    # ------------------------------------------------------------------
    # Inline button: ✅ Done — exit any active mode, return to dashboard
    # ------------------------------------------------------------------
    @bot.on(events.CallbackQuery(data=b"done_mode"))
    async def on_done_mode(event) -> None:
        await event.answer()
        user_id = event.sender_id
        _user_modes.pop(user_id, None)
        text = await _build_dashboard_text(user_id=user_id)
        await _edit_dashboard(bot, event, text)

    # ------------------------------------------------------------------
    # Forwarded message — behaviour depends on current user mode:
    #   setting_target → capture this chat as the delivery target
    #   default        → toggle exclude/include (existing behaviour)
    # ------------------------------------------------------------------
    @bot.on(events.NewMessage(func=lambda e: e.is_private and e.message.fwd_from is not None))
    async def on_forwarded(event: events.NewMessage.Event) -> None:
        await _delete_user_message(bot, event)
        fwd = event.message.fwd_from
        user_id = event.sender_id
        chat_id = event.chat_id

        from_chat = getattr(fwd, "from_id", None) or getattr(fwd, "channel_id", None)
        if from_chat is None:
            await _show_dashboard(bot, user_id, chat_id)
            return

        raw_id = getattr(from_chat, "channel_id", None) or getattr(from_chat, "chat_id", None)
        if raw_id is None:
            await _show_dashboard(bot, user_id, chat_id)
            return

        chat_str = str(raw_id)
        title = getattr(fwd, "from_name", None) or chat_str
        mode = _user_modes.get(user_id, "default")

        if mode == "setting_target":
            # Use the forwarded message's origin chat as the delivery target
            await database.set_user_target_chat(user_id, chat_str, title)
            _user_modes.pop(user_id, None)
            action = f"📍 **Target set:** {title}\nFiles will be delivered here when the threshold is reached."
        else:
            # Default: toggle exclude / include
            already = await database.is_excluded(chat_str)
            if already:
                await database.remove_excluded_chat(chat_str)
                action = "✅ Re-included — this chat will now be monitored."
            else:
                await database.add_excluded_chat(chat_str, title)
                action = f"🚫 Excluded — **{title}** will no longer be monitored."

        text = await _build_dashboard_text(user_id=user_id)
        await _edit_dashboard(bot, event, text + f"\n\n{action}")


# ---------------------------------------------------------------------------
# Helper: delete the user's incoming message silently
# ---------------------------------------------------------------------------

async def _delete_user_message(bot: TelegramClient, event) -> None:
    try:
        await bot.delete_messages(event.chat_id, [event.message.id])
    except Exception:
        pass  # Might fail if already deleted or no permission — ignore silently
