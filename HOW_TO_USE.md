# Telegram Link Collector Bot — How to Use
# بوت جمع الروابط على تيليغرام — دليل الاستخدام

---

## 🇬🇧 English Guide

### What does this bot do?

This bot runs silently in the background on your Telegram account. It automatically monitors **all groups and channels** you are a member of, extracts any **Telegram group invite links** and **WhatsApp group links** it finds, removes duplicates, and saves them to a database. When it collects 100 unique links of each type, it automatically packages them into a `.docx` file and sends it to your configured delivery chat.

---

### Setup

1. Fill in your `.env` file with:
   - `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` — from [my.telegram.org/apps](https://my.telegram.org/apps)
   - `TELEGRAM_PHONE` — your phone number with country code (e.g. `+1234567890`)
   - `BOT_TOKEN` — create a bot via [@BotFather](https://t.me/BotFather) and paste the token
   - `MONGODB_URI` — your MongoDB connection string (Atlas or local)

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Start the bot:
   ```
   python3 main.py
   ```
   On the **first run**, Telegram will send a verification code to your phone. Enter it in the console. After that, a session file is saved and you will not need to enter it again.

4. Open a private chat with your control bot (the one you created via @BotFather) and send `/start`.

---

### Commands

| Command | What it does |
|---------|-------------|
| `/start` | Opens the main dashboard. Always use this to see the bot's status. |
| `/stats` | Shows a quick snapshot of link counts. **Disappears automatically after 10 seconds.** |
| `/pause` | Freezes all processing. The bot saves its exact read position so nothing is missed. |
| `/resume` | Continues from exactly where it was paused. |
| `/reset` | Wipes collected links. Shows a confirmation menu with two options: wipe links only, or wipe links AND reset read history. |
| `/schedule 9 23` | Sets a daily active window. The bot will run from 09:00 to 23:00 and auto-pause outside those hours. |
| `/schedule 22 6` | Overnight window example (22:00 to 06:00). Works across midnight correctly. |
| `/schedule off` | Disables the schedule. The bot runs continuously. |
| `/schedule` | Shows the current schedule setting without changing it. |

---

### Dashboard Buttons

When you send `/start`, you see the main dashboard — a **single message** that gets updated in place. All interactions edit this one message; no new messages are ever sent.

| Button | What it does |
|--------|-------------|
| **📊 Refresh** | Reloads the dashboard with the latest link counts and status. |
| **📤 Send Files Now** | Immediately packages and sends all unsent links to your target chats, even if the 100-link threshold has not been reached. |
| **🚫 Excluded Chats** | Shows the list of chats you have excluded by forwarding a message from them. |
| **🔗 Recent Links** | Displays the last 10 links the bot collected. |
| **⛔ Block by Link** | Enters "block mode". Send a `t.me/` group link and the bot will add it to the blocked list. You can send multiple links one by one. Press **✅ Done Blocking** when finished. |
| **📍 Set Target Chat** | Sets where your `.docx` files are delivered. You can send a @username, a link, a numeric ID, or forward any message from the target chat. Each user can have their own personal delivery target. |
| **📋 View Blocked Links** | Shows all chats you have blocked using the Block by Link feature. |
| **⏸️ Pause / ▶️ Resume** | Toggles processing on and off. Same as the `/pause` and `/resume` commands. |

---

### How to Exclude / Block Chats

There are **three independent methods**, kept completely separate:

#### Method 1 — `.env` Blacklist (permanent, config-based)
Add usernames or numeric IDs to `EXCLUDED_CHATS` in your `.env` file, separated by commas:
```
EXCLUDED_CHATS=groupname,anotherchat,-1001234567890
```
Use this for chats you always want to skip, regardless of who is running the bot.

#### Method 2 — Forward a Message (per-chat toggle via UI)
Forward any message from a group or channel to the control bot. The bot will immediately exclude that chat. Forward again to re-include it. These are stored in the `excluded` database collection.

#### Method 3 — Block by Link (UI, paste the link)
Press the **⛔ Block by Link** button, then paste a Telegram group link (e.g. `t.me/groupname` or `t.me/+xxxxxxxx`). The bot resolves the link to find the real chat and stores it in the `blocked_links` database collection, completely separate from Method 2.

All three methods are stored separately so you can use them for different purposes in the future.

---

### Per-User Target Chat

If multiple people are using the same control bot, each person can set their own delivery destination:

1. Press **📍 Set Target Chat** on the dashboard.
2. Send one of the following:
   - A `@username`
   - A `t.me/` link
   - A numeric chat ID (e.g. `-1001234567890`)
   - Forward any message from the target chat
3. The bot confirms and stores it for your user account.

When the 100-link threshold is reached, files are sent to **every user's** configured target. If nobody has set a target, files go to the `TARGET_CHAT_ID` in `.env` (default: Saved Messages).

---

### Auto-Schedule

You can tell the bot to work only during certain hours:

```
/schedule 9 23    → active 09:00–23:00, pauses at night
/schedule 22 6    → active overnight from 22:00 to 06:00
/schedule off     → no schedule, runs 24/7
```

The schedule is saved to the database and checked every minute by a background task. It automatically pauses and resumes the bot without any action from you.

---

### Important Notes

- **Your messages to the bot are deleted immediately** — the chat stays clean with only the one dashboard message visible.
- **Pause state is saved** — if the bot restarts while paused, it wakes up still paused.
- **Read position is saved** — offsets are saved every 10 messages so a crash loses at most 10 messages of progress.
- **History reading** — on first start, the bot reads the last 3 months of messages from all monitored chats (configurable via `HISTORY_MONTHS` in `.env`).
- **Deduplication** — the same link is never stored twice, regardless of how many chats it appears in.

---
---

## 🇸🇦 الدليل بالعربية

### ماذا يفعل هذا البوت؟

يعمل هذا البوت بصمت في الخلفية على حسابك في تيليغرام. يقوم تلقائياً بمراقبة **جميع المجموعات والقنوات** التي أنت عضو فيها، ويستخرج روابط الدعوة إلى مجموعات تيليغرام وروابط مجموعات واتساب، ويزيل المكرر منها، ويحفظها في قاعدة البيانات. عندما يجمع 100 رابط فريد من كل نوع، يقوم تلقائياً بتجميعها في ملف `.docx` وإرساله إلى المحادثة التي حددتها للتسليم.

---

### الإعداد

1. أملأ ملف `.env` بالبيانات التالية:
   - `TELEGRAM_API_ID` و `TELEGRAM_API_HASH` — من [my.telegram.org/apps](https://my.telegram.org/apps)
   - `TELEGRAM_PHONE` — رقم هاتفك مع رمز البلد (مثال: `+966501234567`)
   - `BOT_TOKEN` — أنشئ بوت عبر [@BotFather](https://t.me/BotFather) وانسخ التوكن
   - `MONGODB_URI` — رابط قاعدة بيانات MongoDB (Atlas أو محلي)

2. ثبّت المتطلبات:
   ```
   pip install -r requirements.txt
   ```

3. شغّل البوت:
   ```
   python3 main.py
   ```
   في **أول تشغيل**، سيرسل تيليغرام كود تحقق إلى هاتفك. أدخله في الكونسول. بعد ذلك سيُحفظ ملف الجلسة ولن تحتاج لإدخاله مجدداً.

4. افتح محادثة خاصة مع بوت التحكم (الذي أنشأته عبر @BotFather) وأرسل `/start`.

---

### الأوامر

| الأمر | ما الذي يفعله |
|-------|--------------|
| `/start` | يفتح لوحة التحكم الرئيسية. استخدمه دائماً لرؤية حالة البوت. |
| `/stats` | يعرض لمحة سريعة عن عدد الروابط. **يختفي تلقائياً بعد 10 ثوانٍ.** |
| `/pause` | يجمّد جميع العمليات. يحفظ البوت موضعه الدقيق في القراءة حتى لا يفوته شيء. |
| `/resume` | يستأنف العمل من المكان الدقيق الذي توقف فيه. |
| `/reset` | يحذف الروابط المجموعة. يعرض قائمة تأكيد بخيارين: حذف الروابط فقط، أو حذف الروابط وإعادة ضبط سجل القراءة. |
| `/schedule 9 23` | يضع نافذة نشاط يومية. يعمل البوت من 09:00 إلى 23:00 ويتوقف تلقائياً خارج هذه الساعات. |
| `/schedule 22 6` | مثال على نافذة ليلية (من 22:00 إلى 06:00). يعمل عبر منتصف الليل بشكل صحيح. |
| `/schedule off` | يلغي الجدول الزمني. يعمل البوت بشكل مستمر. |
| `/schedule` | يعرض الجدول الزمني الحالي دون تغييره. |

---

### أزرار لوحة التحكم

عند إرسال `/start`، ستظهر لوحة التحكم الرئيسية — وهي **رسالة واحدة** يتم تحديثها في مكانها. جميع التفاعلات تعدّل هذه الرسالة الواحدة؛ لا ترسل رسائل جديدة أبداً.

| الزر | ما الذي يفعله |
|------|--------------|
| **📊 Refresh** | يعيد تحميل لوحة التحكم بأحدث أعداد الروابط والحالة. |
| **📤 Send Files Now** | يجمع الروابط غير المرسلة على الفور ويرسلها إلى المحادثات المستهدفة، حتى لو لم يصل العدد إلى 100. |
| **🚫 Excluded Chats** | يعرض قائمة المحادثات التي استثنيتها عن طريق تحويل رسالة منها. |
| **🔗 Recent Links** | يعرض آخر 10 روابط جمعها البوت. |
| **⛔ Block by Link** | يدخل في "وضع الحظر". أرسل رابط مجموعة `t.me/` وسيضيفه البوت إلى قائمة المحظورات. يمكنك إرسال روابط متعددة واحداً تلو الآخر. اضغط **✅ Done Blocking** عند الانتهاء. |
| **📍 Set Target Chat** | يحدد أين تُسلَّم ملفات `.docx`. يمكنك إرسال @اسم_المستخدم، رابط، معرّف رقمي، أو تحويل أي رسالة من المحادثة المستهدفة. لكل مستخدم وجهة تسليم خاصة به. |
| **📋 View Blocked Links** | يعرض جميع المحادثات التي حظرتها باستخدام ميزة الحظر بالرابط. |
| **⏸️ Pause / ▶️ Resume** | تشغيل/إيقاف المعالجة. نفس وظيفة أوامر `/pause` و `/resume`. |

---

### كيفية استثناء / حظر المحادثات

هناك **ثلاث طرق مستقلة** يتم الاحتفاظ بها منفصلة تماماً:

#### الطريقة الأولى — القائمة السوداء في `.env` (دائمة، تعتمد على الإعدادات)
أضف أسماء المستخدمين أو المعرّفات الرقمية إلى `EXCLUDED_CHATS` في ملف `.env`، مفصولة بفواصل:
```
EXCLUDED_CHATS=groupname,anotherchat,-1001234567890
```
استخدم هذا للمحادثات التي تريد تخطيها دائماً، بغض النظر عمّن يشغّل البوت.

#### الطريقة الثانية — تحويل رسالة (تبديل لكل محادثة عبر واجهة المستخدم)
حوّل أي رسالة من مجموعة أو قناة إلى بوت التحكم. سيستثني البوت تلك المحادثة فوراً. حوّل مرة أخرى لإعادة تضمينها. تُحفظ هذه البيانات في مجموعة `excluded` في قاعدة البيانات.

#### الطريقة الثالثة — الحظر برابط (واجهة المستخدم، لصق الرابط)
اضغط على زر **⛔ Block by Link**، ثم الصق رابط مجموعة تيليغرام (مثل `t.me/groupname` أو `t.me/+xxxxxxxx`). يحل البوت الرابط للعثور على المحادثة الحقيقية ويحفظها في مجموعة `blocked_links` في قاعدة البيانات، منفصلة تماماً عن الطريقة الثانية.

الطرق الثلاث محفوظة بشكل منفصل حتى تتمكن من استخدامها لأغراض مختلفة في المستقبل.

---

### محادثة التسليم المخصصة لكل مستخدم

إذا كان عدة أشخاص يستخدمون نفس بوت التحكم، يمكن لكل شخص تعيين وجهة تسليمه الخاصة:

1. اضغط على **📍 Set Target Chat** في لوحة التحكم.
2. أرسل أحد ما يلي:
   - @اسم_المستخدم
   - رابط `t.me/`
   - معرّف رقمي للمحادثة (مثال: `-1001234567890`)
   - حوّل أي رسالة من المحادثة المستهدفة
3. يؤكد البوت ويحفظه لحساب المستخدم الخاص بك.

عند الوصول إلى حد 100 رابط، ترسل الملفات إلى **محادثة التسليم الخاصة بكل مستخدم**. إذا لم يعيّن أحد محادثة مستهدفة، تذهب الملفات إلى `TARGET_CHAT_ID` في ملف `.env` (الافتراضي: الرسائل المحفوظة).

---

### الجدولة التلقائية

يمكنك إخبار البوت بالعمل خلال ساعات معينة فقط:

```
/schedule 9 23    → نشط من 09:00 إلى 23:00، يتوقف في الليل
/schedule 22 6    → نشط في الليل من 22:00 إلى 06:00
/schedule off     → لا جدول، يعمل 24/7
```

يُحفظ الجدول في قاعدة البيانات ويتم التحقق منه كل دقيقة بواسطة مهمة خلفية. يوقف البوت ويستأنفه تلقائياً دون أي إجراء منك.

---

### ملاحظات مهمة

- **رسائلك إلى البوت تُحذف فوراً** — تبقى المحادثة نظيفة مع ظهور رسالة لوحة التحكم الواحدة فقط.
- **حالة الإيقاف تُحفظ** — إذا أُعيد تشغيل البوت أثناء الإيقاف المؤقت، يستيقظ في حالة إيقاف مؤقت.
- **موضع القراءة يُحفظ** — تُحفظ الإزاحات كل 10 رسائل، لذا فإن العطل لا يفقد أكثر من 10 رسائل من التقدم.
- **قراءة السجل** — عند أول تشغيل، يقرأ البوت آخر 3 أشهر من الرسائل من جميع المحادثات المراقبة (قابل للتكوين عبر `HISTORY_MONTHS` في `.env`).
- **إزالة التكرار** — لا يُحفظ نفس الرابط مرتين أبداً، بغض النظر عن عدد المحادثات التي يظهر فيها.
