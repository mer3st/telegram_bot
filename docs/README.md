# BITO & BI Program — Telegram Bot

Telegram bot for a data-analytics training program (two tracks: **BITO** and **BI / Power BI**).
Uses **Google Sheets** as the database and **Gemini AI** to auto-grade homework.

---

## 1. Install Python libraries

```
pip install "python-telegram-bot[job-queue]" gspread google-auth python-dotenv requests openpyxl python-docx pypdf pytest pytest-asyncio
```

(`[job-queue]` is required — the bot uses a scheduled job. `openpyxl` / `python-docx` / `pypdf` are for reading BITO file submissions. `pytest` / `pytest-asyncio` are for running the test suite.)

---

## 2. Create the Telegram bot

1. Message **@BotFather** → `/newbot` → pick a name and username.
2. Copy the token → put it in `.env` as `BOT_TOKEN`.

Get your numeric Telegram ID from **@userinfobot** → put it in `.env` as `ADMIN_IDS` (comma-separated for multiple admins).

---

## 3. Create the two channels

1. Make a **BITO** channel and a **BI** channel.
2. **Add your bot as an ADMIN in both** — this is mandatory; without admin rights the membership check fails and every user is treated as a non-member.
3. Get each channel's ID (forward a channel post to **@userinfobot**). IDs look like `-1001234567890`.
4. Put the IDs and invite links in `.env` (see section 6).

---

## 4. Google Sheets — service account

> Note: service-account *keys* are blocked by org policy inside an organization. Create the project under a **personal Gmail / "No organization"** so key creation is allowed.

1. console.cloud.google.com → create a project with **No organization**.
2. APIs & Services → enable **Google Sheets API** and **Google Drive API**.
3. IAM & Admin → Service Accounts → create → **Keys → Add Key → JSON** → download.
4. Rename the file to **`credentials.json`**, place it next to `bot.py`.
5. Open `credentials.json`, copy the `client_email`, then in your Sheet → **Share** → paste that email → **Editor**.

---

## 5. Google Sheet — tabs and headers

Create one spreadsheet with these **six tabs** (exact names and column order matter — rows are written by position):

**Students**
```
PersonalID | FullName | Yosh | Universitet | Telefon nomer | TelegramID | Telegram_username | Date | Stage | ExamRegistered | BitoJoinDate | BiJoinDate
```
- `Stage` drives access: `pending` = awaiting approval, `bito` = BITO track, `bi` = promoted to BI track.
- `ExamRegistered`, `BitoJoinDate`, `BiJoinDate` are set automatically — leave them empty on setup.

**Form_Responses** (the Google Form writes here)
- The form's own question columns. The bot reads these exact question titles: `To'liq ism familiya (Passportdagidek)`, `Telefon nomer`, `Qaysi universitetni bitirgansiz?`, `Yosh`, plus a `Code` column.
- Column **L** must be `PersonalID` and column **M** must be `TelegramID` — the bot fills these when it links a response to a student.

**Codes**
```
Code | TelegramID
```

**Grades**
```
StudentID | AssignmentID | Score | Feedback | Date
```

**Submissions**
```
SubmissionID | StudentID | AssignmentID | Content | FileID | FileType | Date | Grade | Feedback
```

**QA_History**
```
QuestionID | StudentID | Question | Answer | AnsweredBy | DateAsked | DateAnswered
```

---

## 6. `.env` file

```
BOT_TOKEN=123456:your-token
ADMIN_IDS=111111111,222222222
GOOGLE_SHEET_ID=your-sheet-id
GEMINI_API_KEY=your-gemini-key
FORM_URL=https://docs.google.com/forms/d/e/XXXX/viewform?usp=pp_url&entry.915249461={}

BITO_CHANNEL_ID=-1001111111111
BI_CHANNEL_ID=-1002222222222
BITO_CHANNEL_LINK=https://t.me/+yourBitoLink
BI_CHANNEL_LINK=https://t.me/+yourBiLink
```

- Keep the trailing `{}` in `FORM_URL` — the bot inserts each student's code there.
- On Railway you can instead set `GOOGLE_CREDENTIALS` to the full contents of `credentials.json` (the code reads it before falling back to the file).

---

## 7. Run

```
py bot.py
```

You should see `Bot ishga tushdi...`. Then in Telegram: `/start` → join the BITO channel → register.

---

## 8. How it works

- **Channel gate:** every user must be in the BITO channel to use the bot. After `/promote`, they must also be in the BI channel. Admins bypass. Non-members are told to join and wait; admins get notified.
- **Registration:** `/start` or "Ro'yxatdan o'tish" gives a pre-filled Google Form link with the user's code. A background job (every 60s) links new form responses to students.
- **BITO homework:** student uploads file(s) → if an answer key (`expected`) is set in `homework_bito.py`, the AI compares and grades; otherwise it's graded manually.
- **BI homework:** student uploads a `.pbit` → the AI parses the model and grades it.
- **Q&A:** students ask; admins answer via "🔍 Savollar".

### Admin commands
```
/promote STU-0001   move a student to BI stage (after the midterm)
/demote  STU-0001   move back to BITO stage
/refresh            clear the sheet cache
/students /grades /questions /broadcast /add_grade
```

**Broadcast targeting:** "📢 Xabar yuborish" now asks who to send to first — "👤 Bir talabaga" (pick one student from a list) or "👥 Barcha talabalarga" (send to all). Both paths then ask for the message text before sending.

---

## 9. Project structure

```
telegram_bot/
├── bot.py              entry point — registers all handlers and starts the bot
├── config.py           env loader, secrets, admin IDs, channel IDs
├── states.py           state-machine constants (HW_UPLOADING, ADMIN_APPROVE_PICK, …)
├── pytest.ini          test runner config
├── .env                tokens & IDs  ← keep secret, never commit
├── .env.example        template showing every required env var
├── credentials.json    Google service-account key  ← keep secret, never commit
│
├── core/               shared infrastructure used by handlers and jobs
│   ├── sheets.py           Google Sheets data-access layer (all DB calls live here)
│   ├── keyboards.py        reply-keyboard builders for every user stage
│   └── deadline_checker.py background job — sends deadline reminders every 15 min
│
├── curriculum/         course content and AI grading engine
│   ├── homework_bito.py    BITO assignment definitions + answer keys
│   ├── homework_bi.py      BI assignment definitions + grading criteria
│   ├── grader.py           PowerBIMentor AI class — grades .pbit files via Gemini
│   └── extractors.py       xlsx / docx / pdf → plain text (fed to the grader)
│
├── handlers/           Telegram update handlers, split by role
│   ├── common.py           /start, /help, /myid, /reset, /refresh, error handler
│   ├── student.py          registration, homework upload, grades, Q&A, deadlines
│   ├── admin.py            student list, grading, broadcast, promote/demote/expel
│   ├── form_processor.py   background job — links Google Form responses to students
│   └── channels.py         channel-membership gate (runs before every handler)
│
└── tests/              pytest test suite — 47 tests, all mocked at sheets boundary
    ├── test_sheets.py      data-layer regression tests
    ├── test_handlers.py    handler routing and null-guard tests
    └── test_security.py    code generation, auth, input validation tests
```

**Quick navigation:**
- "Why is user X stuck?" → [handlers/channels.py](handlers/channels.py) (gate) + [handlers/common.py](handlers/common.py) (/myid, /reset)
- "Add a new homework assignment" → [curriculum/homework_bito.py](curriculum/homework_bito.py) or [curriculum/homework_bi.py](curriculum/homework_bi.py)
- "Change how grades are stored" → [core/sheets.py](core/sheets.py)
- "Change a keyboard layout" → [core/keyboards.py](core/keyboards.py)
- "Add a new admin command" → [handlers/admin.py](handlers/admin.py) + [bot.py](bot.py) (register the handler)

---

## 10. Run the tests

```
pytest

cd D:\telegram_bot
& "C:\Users\haymad\AppData\Local\Python\bin\python.exe" -m pytest tests/ -v
```

All 47 tests mock Google Sheets at the `get_sheet()` boundary — no real network calls needed. Run after any change to `core/sheets.py` or `handlers/` to catch regressions.

---

## 11. Keep secrets out of git

Add a `.gitignore` with:
```
.env
credentials.json
token.pickle
client_secret.json
temp_*
```
Google auto-disables any service-account key it finds in a public repo, so never commit `credentials.json`.

---

## 12. Deploy 24/7 (Railway)

1. Push to GitHub **without** `.env` / `credentials.json`.
2. railway.app → New Project → Deploy from GitHub.
3. Add every `.env` variable in the Railway dashboard.
4. Either upload `credentials.json` as a file, or set `GOOGLE_CREDENTIALS` to its full JSON contents.
5. Make sure the bot is still an admin in both channels.
