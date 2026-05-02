# Telegram Userbot — Link Collector

A Telegram userbot that automatically monitors **all** your groups and channels, collects Telegram and WhatsApp group invite links, deduplicates them via MongoDB, and sends `.docx` files when 100 links are collected.

A separate control bot (with a clean single-message UI) lets you check stats, manage excluded chats, and trigger file sends — all without cluttering your chat.

## Setup

### 1. Install Python dependencies

```bash
cd bots/telegram-userbot
pip install -r requirements.txt
```

### 2. Get Telegram API credentials

1. Go to [https://my.telegram.org/apps](https://my.telegram.org/apps)
2. Log in and create an app
3. Copy your **API ID** and **API Hash**

### 3. Create a control bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the steps
3. Copy the **bot token**

### 4. Set up MongoDB

Make sure MongoDB is running locally or use a cloud instance (e.g. MongoDB Atlas).

### 5. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+1234567890
BOT_TOKEN=123456789:ABCdef...
MONGODB_URI=mongodb://localhost:27017
TARGET_CHAT_ID=me
LINKS_PER_FILE=100
HISTORY_MONTHS=3

# Optional: chats to skip (comma-separated usernames or numeric IDs)
EXCLUDED_CHATS=@somechannel,-1001234567890
```

### 6. Run

```bash
python main.py
```

On the first run you'll be asked for your Telegram login code. After that a session file is saved — you won't need to log in again.

---

## How It Works

### Auto-discovery

The userbot automatically discovers **every group and channel** you are a member of. No need to list them. You only need to specify chats you want to **exclude** (blacklist).

### Excluding chats

Two ways to exclude a chat:
- Add its username or ID to `EXCLUDED_CHATS` in `.env` before starting
- **Forward any message from that chat to the control bot** — it toggles exclusion on/off. Forwarding again re-includes it.

### Link Classification

| Link Type | Collected? | Reason |
|-----------|-----------|--------|
| `t.me/joinchat/XXX` | ✅ | Telegram private group invite |
| `t.me/+XXX` | ✅ | Telegram private group invite (new format) |
| `t.me/channelname` (channel/group) | ✅ | Resolved via API — confirmed group |
| `t.me/username` (user account) | ❌ | Resolved via API — identified as person |
| `chat.whatsapp.com/XXX` | ✅ | WhatsApp group invite |
| `wa.me/XXXXX` | ❌ | WhatsApp individual contact |

### Output Files

- **`telegramLinks.docx`** — Telegram group/channel invite links
- **`whatsappLinks.docx`** — WhatsApp group invite links

Files are sent to `TARGET_CHAT_ID` when the count reaches `LINKS_PER_FILE`, then deleted from disk.

### Timing (human-like)

| Event | Delay |
|-------|-------|
| Between individual messages | 0.3–1.5 s |
| Between batches | 3–10 s |
| After sending a file | 15–45 s |
| Off-hours extra (midnight–7 AM) | 1–5 s extra |

---

## Control Bot (Single-Message UI)

Open a DM with your control bot. Send `/start` to see the dashboard.

All interactions **edit the same message** — no new messages are ever sent. If you send the bot a message (link, text, etc.), your message is **deleted immediately** and the dashboard updates silently.

### Commands

| Command | Action |
|---------|--------|
| `/start` | Open the full dashboard |
| `/stats` | Quick snapshot — disappears after 10 seconds |
| `/pause` | Freeze all processing; auto-saves current read position |
| `/resume` | Continue from the exact point where it was paused |
| `/reset` | Wipe collected links with a confirmation step |
| `/schedule 9 23` | Auto-pause outside 09:00–23:00 every day |
| `/schedule off` | Disable the schedule (run continuously) |
| `/schedule` | Show the current schedule setting |

### Dashboard buttons

| Button | Action |
|--------|--------|
| 📊 Refresh | Update stats in place |
| 📤 Send Files Now | Force-send .docx to all configured targets |
| 🚫 Excluded Chats | View chats excluded via forwarded message |
| 🔗 Recent Links | Show last 10 links found |
| ⛔ Block by Link | Enter block mode — send group links to block those chats |
| 📍 Set Target Chat | Set where your .docx files are delivered |
| 📋 View Blocked Links | View chats blocked by link |
| ⏸️ Pause / ▶️ Resume | Toggle processing on/off |

### Adding links manually

Send any message containing Telegram or WhatsApp group links to the control bot. It will:
1. Delete your message immediately
2. Add any links found to the database
3. Update the dashboard with new counts

### Excluding/re-including chats (three separate lists)

There are three independent exclusion mechanisms, kept separate for different purposes:

| Mechanism | How to add | DB collection |
|-----------|-----------|---------------|
| **.env blacklist** | Edit `EXCLUDED_CHATS` in `.env` | None (config only) |
| **Forwarded-message exclusions** | Forward any message from the chat to the bot | `excluded` |
| **Block by link** | Press ⛔ Block by Link, then send the group's `t.me/` link | `blocked_links` |

All three prevent the bot from monitoring those chats. They are stored separately so you can query or use them independently in the future.

### Per-user target chat

Each person using the bot sets their own delivery destination:
- Press **📍 Set Target Chat** on the dashboard
- Send a `@username`, `t.me/` link, numeric ID, or forward any message from the target chat
- Files are delivered to **every** configured user target when the threshold is reached
- Fallback: if no user has set a target, files go to `TARGET_CHAT_ID` from `.env` (default: Saved Messages)

### Auto-schedule

Set a daily active window so the bot pauses itself outside working hours:

```
/schedule 9 23    → run 09:00–23:00, auto-pause at night
/schedule 22 6    → overnight window (22:00–06:00)
/schedule off     → run continuously, no schedule
/schedule         → show current setting
```

The schedule is checked every 60 seconds by a background task and applies globally.

---

## Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — runs userbot, control bot, and scheduler together |
| `config.py` | Loads all settings from `.env` |
| `database.py` | MongoDB: links, offsets, exclusions, blocked links, user settings |
| `link_extractor.py` | Detects and classifies Telegram/WhatsApp links |
| `document_manager.py` | Builds and saves `.docx` files |
| `history_reader.py` | Reads past N months per chat with resume support |
| `message_handler.py` | Core link pipeline (extract → resolve → store → send) |
| `human_behavior.py` | Randomised sleep patterns |
| `bot_interface.py` | Control bot — single-message dashboard UI |
| `scheduler.py` | Background task for auto-pause/resume by time window |
| `pause_state.py` | Shared `asyncio.Event` for pause/resume across all coroutines |
