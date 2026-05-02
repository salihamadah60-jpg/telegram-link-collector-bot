"""
database.py — All MongoDB operations.

Collections:
  links         — every unique link ever collected
  offsets       — last processed message ID per chat (for resuming)
  sent_files    — log of every .docx file sent
  entity_cache  — resolved Telegram usernames → type (channel/group/user)
  excluded      — chats excluded from monitoring (managed via bot UI)
  ui_state      — stores the dashboard message ID per user for single-message UI
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Literal

import motor.motor_asyncio
from pymongo import IndexModel, ASCENDING

import config

logger = logging.getLogger("database")

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None


async def connect() -> None:
    global _client, _db
    _client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URI)
    _db = _client[config.MONGODB_DB_NAME]
    await _ensure_indexes()
    logger.info("Connected to MongoDB: %s / %s", config.MONGODB_URI, config.MONGODB_DB_NAME)


async def disconnect() -> None:
    if _client:
        _client.close()
        logger.info("MongoDB connection closed")


def _get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected. Call database.connect() first.")
    return _db


async def _ensure_indexes() -> None:
    db = _get_db()
    await db.links.create_indexes([
        IndexModel([("url", ASCENDING)], unique=True),
        IndexModel([("link_type", ASCENDING)]),
        IndexModel([("sent", ASCENDING)]),
        IndexModel([("added_at", ASCENDING)]),
    ])
    await db.offsets.create_indexes([
        IndexModel([("chat_id", ASCENDING)], unique=True),
    ])
    await db.entity_cache.create_indexes([
        IndexModel([("username", ASCENDING)], unique=True),
    ])
    await db.excluded.create_indexes([
        IndexModel([("chat_id", ASCENDING)], unique=True),
    ])
    await db.blocked_links.create_indexes([
        IndexModel([("chat_id", ASCENDING)], unique=True),
    ])
    await db.user_settings.create_indexes([
        IndexModel([("user_id", ASCENDING)], unique=True),
    ])
    await db.ui_state.create_indexes([
        IndexModel([("user_id", ASCENDING)], unique=True),
    ])
    logger.debug("MongoDB indexes ensured")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

async def add_link(
    url: str,
    link_type: Literal["telegram", "whatsapp"],
    source_chat: str,
    message_id: int,
    message_date: Optional[datetime],
) -> bool:
    """Insert a link. Returns True if new, False if duplicate."""
    db = _get_db()
    doc = {
        "url": url,
        "link_type": link_type,
        "source_chat": str(source_chat),
        "message_id": message_id,
        "message_date": message_date,
        "added_at": datetime.now(timezone.utc),
        "sent": False,
    }
    try:
        await db.links.insert_one(doc)
        logger.debug("New %s link stored: %s", link_type, url)
        return True
    except Exception:
        logger.debug("Duplicate link ignored: %s", url)
        return False


async def get_unsent_links(link_type: Literal["telegram", "whatsapp"]) -> list[dict]:
    db = _get_db()
    cursor = db.links.find(
        {"link_type": link_type, "sent": False},
        {"_id": 0, "url": 1, "source_chat": 1, "message_date": 1},
    ).sort("added_at", ASCENDING)
    return await cursor.to_list(length=None)


async def get_recent_links(limit: int = 10) -> list[dict]:
    """Return the most recently added links regardless of type."""
    db = _get_db()
    cursor = db.links.find(
        {},
        {"_id": 0, "url": 1, "link_type": 1, "source_chat": 1, "added_at": 1},
    ).sort("added_at", -1).limit(limit)
    return await cursor.to_list(length=None)


async def count_unsent(link_type: Literal["telegram", "whatsapp"]) -> int:
    db = _get_db()
    return await db.links.count_documents({"link_type": link_type, "sent": False})


async def count_total(link_type: Literal["telegram", "whatsapp"]) -> int:
    db = _get_db()
    return await db.links.count_documents({"link_type": link_type})


async def mark_links_sent(link_type: Literal["telegram", "whatsapp"]) -> None:
    db = _get_db()
    result = await db.links.update_many(
        {"link_type": link_type, "sent": False},
        {"$set": {"sent": True, "sent_at": datetime.now(timezone.utc)}},
    )
    logger.info("Marked %d %s links as sent", result.modified_count, link_type)


async def reset_all_links(also_reset_offsets: bool = False) -> dict:
    """
    Wipe all collected links from the database and optionally clear the
    per-chat read offsets (so history will be re-read from scratch next startup).

    Returns a dict with counts of what was deleted.
    """
    db = _get_db()
    tg_count = await count_total("telegram")
    wa_count = await count_total("whatsapp")

    await db.links.delete_many({})

    if also_reset_offsets:
        await db.offsets.delete_many({})

    logger.warning(
        "RESET: deleted %d telegram + %d whatsapp links. Offsets reset: %s",
        tg_count, wa_count, also_reset_offsets,
    )
    return {
        "telegram": tg_count,
        "whatsapp": wa_count,
        "offsets_reset": also_reset_offsets,
    }


# ---------------------------------------------------------------------------
# Bot pause state (persists across restarts)
# ---------------------------------------------------------------------------

async def save_bot_paused(paused: bool) -> None:
    """Persist the paused flag so it survives a bot restart."""
    db = _get_db()
    await db.bot_state.update_one(
        {"key": "paused"},
        {
            "$set": {
                "value": paused,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def get_bot_paused() -> bool:
    """Return True if the bot was paused before the last shutdown."""
    db = _get_db()
    doc = await db.bot_state.find_one({"key": "paused"})
    return bool(doc["value"]) if doc else False


# ---------------------------------------------------------------------------
# Stats (for dashboard)
# ---------------------------------------------------------------------------

async def get_stats() -> dict:
    """Return a summary dict for the dashboard UI."""
    db = _get_db()
    tg_unsent = await count_unsent("telegram")
    tg_total = await count_total("telegram")
    wa_unsent = await count_unsent("whatsapp")
    wa_total = await count_total("whatsapp")
    files_sent = await db.sent_files.count_documents({})
    excluded_count = await db.excluded.count_documents({})

    last_link = await db.links.find_one({}, sort=[("added_at", -1)])
    last_activity = "Never"
    if last_link and last_link.get("added_at"):
        last_activity = last_link["added_at"].strftime("%Y-%m-%d %H:%M UTC")

    return {
        "tg_unsent": tg_unsent,
        "tg_total": tg_total,
        "wa_unsent": wa_unsent,
        "wa_total": wa_total,
        "files_sent": files_sent,
        "excluded_count": excluded_count,
        "last_activity": last_activity,
    }


# ---------------------------------------------------------------------------
# Offsets
# ---------------------------------------------------------------------------

async def get_offset(chat_id: str) -> Optional[int]:
    db = _get_db()
    doc = await db.offsets.find_one({"chat_id": str(chat_id)})
    return doc["last_message_id"] if doc else None


async def set_offset(chat_id: str, last_message_id: int) -> None:
    db = _get_db()
    await db.offsets.update_one(
        {"chat_id": str(chat_id)},
        {"$set": {"last_message_id": last_message_id, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Sent file log
# ---------------------------------------------------------------------------

async def log_sent_file(
    filename: str,
    link_type: Literal["telegram", "whatsapp"],
    link_count: int,
    target_chat: str,
) -> None:
    db = _get_db()
    await db.sent_files.insert_one({
        "filename": filename,
        "link_type": link_type,
        "link_count": link_count,
        "target_chat": str(target_chat),
        "sent_at": datetime.now(timezone.utc),
    })


# ---------------------------------------------------------------------------
# Excluded chats (managed via bot UI)
# ---------------------------------------------------------------------------

async def add_excluded_chat(chat_id: str, title: str) -> None:
    db = _get_db()
    await db.excluded.update_one(
        {"chat_id": str(chat_id)},
        {"$set": {"chat_id": str(chat_id), "title": title, "added_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def remove_excluded_chat(chat_id: str) -> None:
    db = _get_db()
    await db.excluded.delete_one({"chat_id": str(chat_id)})


async def get_excluded_chats() -> list[dict]:
    db = _get_db()
    cursor = db.excluded.find({}, {"_id": 0, "chat_id": 1, "title": 1})
    return await cursor.to_list(length=None)


async def is_excluded(chat_id: str) -> bool:
    db = _get_db()
    doc = await db.excluded.find_one({"chat_id": str(chat_id)})
    return doc is not None


# ---------------------------------------------------------------------------
# UI state — tracks the single dashboard message per user
# ---------------------------------------------------------------------------

async def get_dashboard_message_id(user_id: int) -> Optional[int]:
    db = _get_db()
    doc = await db.ui_state.find_one({"user_id": user_id})
    return doc["message_id"] if doc else None


async def set_dashboard_message_id(user_id: int, message_id: int) -> None:
    db = _get_db()
    await db.ui_state.update_one(
        {"user_id": user_id},
        {"$set": {"message_id": message_id, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Blocked-by-link chats (separate from forwarded-message exclusions)
# ---------------------------------------------------------------------------

async def add_blocked_link(chat_id: str, title: str, url: str, added_by: int) -> None:
    db = _get_db()
    await db.blocked_links.update_one(
        {"chat_id": str(chat_id)},
        {
            "$set": {
                "chat_id": str(chat_id),
                "title": title,
                "url": url,
                "added_by": added_by,
                "added_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def remove_blocked_link(chat_id: str) -> None:
    db = _get_db()
    await db.blocked_links.delete_one({"chat_id": str(chat_id)})


async def get_blocked_links() -> list[dict]:
    db = _get_db()
    cursor = db.blocked_links.find({}, {"_id": 0, "chat_id": 1, "title": 1, "url": 1})
    return await cursor.to_list(length=None)


async def is_blocked_by_link(chat_id: str) -> bool:
    db = _get_db()
    doc = await db.blocked_links.find_one({"chat_id": str(chat_id)})
    return doc is not None


# ---------------------------------------------------------------------------
# User settings — per-user target chat + schedule
# ---------------------------------------------------------------------------

async def get_user_settings(user_id: int) -> dict:
    db = _get_db()
    doc = await db.user_settings.find_one({"user_id": user_id})
    return doc or {}


async def set_user_target_chat(user_id: int, chat_id: str, chat_title: str) -> None:
    db = _get_db()
    await db.user_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "target_chat_id": chat_id,
                "target_chat_title": chat_title,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def get_all_target_chats() -> list[dict]:
    """Return all configured per-user target chats."""
    db = _get_db()
    cursor = db.user_settings.find(
        {"target_chat_id": {"$exists": True}},
        {"_id": 0, "user_id": 1, "target_chat_id": 1, "target_chat_title": 1},
    )
    return await cursor.to_list(length=None)


async def set_user_schedule(
    user_id: int, start_hour: int, end_hour: int, enabled: bool
) -> None:
    db = _get_db()
    await db.user_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "schedule_start": start_hour,
                "schedule_end": end_hour,
                "schedule_enabled": enabled,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def get_global_schedule() -> Optional[dict]:
    """
    Return the first enabled schedule found across all users.
    (Schedule is treated as global — whoever last set it applies to all.)
    """
    db = _get_db()
    doc = await db.user_settings.find_one(
        {"schedule_enabled": True},
        {"_id": 0, "schedule_start": 1, "schedule_end": 1, "schedule_enabled": 1},
    )
    return doc


async def disable_all_schedules() -> None:
    db = _get_db()
    await db.user_settings.update_many(
        {"schedule_enabled": True},
        {"$set": {"schedule_enabled": False}},
    )


# ---------------------------------------------------------------------------
# Entity cache
# ---------------------------------------------------------------------------

async def get_cached_entity_type(username: str) -> Optional[str]:
    db = _get_db()
    doc = await db.entity_cache.find_one({"username": username.lower()})
    return doc["entity_type"] if doc else None


async def cache_entity_type(username: str, entity_type: str) -> None:
    db = _get_db()
    await db.entity_cache.update_one(
        {"username": username.lower()},
        {"$set": {"entity_type": entity_type, "resolved_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
