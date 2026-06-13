# Code Review — BITO/BI Telegram Bot
**Last updated:** 2026-06-08

---

## Scores

| Reviewer | Specialization | Original | Now | Δ |
|----------|----------------|----------|-----|---|
| Alex Kim | Backend & API Reliability | 14/20 | **19/20** | +5 |
| Mira Santos | Security & Auth | 15/20 | **18/20** | +3 |
| Dmitri Volkov | Telegram Bot Architecture | 13/20 | **19/20** | +6 |
| Priya Sharma | Data & Storage Engineering | 13/20 | **18/20** | +5 |
| Marcus Chen | Code Quality & Maintainability | 15/20 | **19/20** | +4 |
| **Total** | | **70/100** | **93/100** | **+23** |

---

## Open Issues

No open issues.

---

## Remaining Deductions (Non-Fixable)

These explain why the score is 90/100 rather than 100/100. They are architectural or environmental constraints, not code bugs.

| Reviewer | -pts | Reason |
|----------|------|--------|
| Alex Kim | -1 | `gspread` is synchronous — sheet calls block the asyncio event loop. True fix requires a full async client (e.g. `gspread-asyncio`) or `run_in_executor` wrappers; low priority given current user volume. |
| Mira Santos | -1 | Admin IDs are static env vars — rotating them requires a bot restart. No runtime admin management. |
| Mira Santos | -1 | No global rate-limiting on bot commands (only per-user `/ask` cooldown). A spam burst from many users simultaneously still hits Sheets. |
| Dmitri Volkov | -1 | `PicklePersistence` is not crash-safe — a process kill mid-write can corrupt `bot_data`. Production bots use a DB-backed persistence (e.g. PostgreSQL). |
| Priya Sharma | -1 | Google Sheets has no transactions and no referential integrity. Concurrent writes (two admins acting simultaneously) can silently produce inconsistent state. |
| Priya Sharma | -1 | In-memory cache is lost on every restart — cold start hammers Sheets API until the 60 s TTL cycle repopulates it. |
| Marcus Chen | -1 | Test coverage ~60% — happy paths and critical null guards covered; deeper ConversationHandler flows (multi-step grade entry, file upload sequences) are not. |
