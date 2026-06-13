import logging
from collections import defaultdict
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, Message
from telegram.ext import ContextTypes, ConversationHandler
import states as S
from core import sheets
from config import is_admin, ADMIN_IDS, BITO_CHANNEL_LINK, BI_CHANNEL_LINK, admin_contact
from core.keyboards import admin_keyboard, student_keyboard
from handlers.common import stage_keyboard, student_contact_link

# ─── STATES ───────────────────────────────────────────────────────────────────
ADMIN_GRADE_SCORE, ADMIN_GRADE_FEEDBACK = range(40, 42)
BROADCAST_MSG = 60
BROADCAST_TARGET = 61
BROADCAST_PICK_STUDENT = 62


_PAGE_SIZE = 10


def _paginated_rows(items: list, page: int, make_btn) -> tuple[list, int, int]:
    """Returns (keyboard_rows, total_pages, clamped_page) for items[page]."""
    total = len(items)
    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]
    rows = [[make_btn(item)] for item in chunk]
    nav = []
    if page > 0:
        nav.append(KeyboardButton("⬅️ Oldingi"))
    if page < pages - 1:
        nav.append(KeyboardButton("➡️ Keyingi"))
    if nav:
        rows.append(nav)
    rows.append([KeyboardButton("🔙 Orqaga")])
    return rows, pages, page


def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True and sends error if not admin."""
    if not is_admin(update.effective_user.id):
        return True
    return False


# ─── BARCHA TALABALAR ─────────────────────────────────────────────────────────

async def admin_students(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    students = sheets.get_all_students()
    if not students:
        await update.message.reply_text("Hali hech kim ro'yxatdan o'tmagan.")
        return
    chunk = f"👥 Barcha talabalar ({len(students)} nafar)\n\n"
    for s in students:
        stage = str(s.get("Stage", "")).strip() or "bito"
        exam_flag = " [Imtihon✓]" if str(s.get("ExamRegistered", "")).strip().upper() == "TRUE" else ""
        line = f"{s['PersonalID']} — {s['FullName']} [{stage}]{exam_flag}\n"
        if len(chunk) + len(line) > 4000:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += line
    if chunk:
        await update.message.reply_text(chunk)


# ─── BAHOLAR (VIEW) ───────────────────────────────────────────────────────────

async def admin_grades_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    context.user_data["state"] = S.ADMIN_GRADES_MENU
    await update.message.reply_text(
        "Qaysi ko'rinishda ko'rmoqchisiz?",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("👤 Talaba bo'yicha"), KeyboardButton("📚 Vazifa bo'yicha")],
            [KeyboardButton("🔙 Orqaga")],
        ], resize_keyboard=True)
    )


async def admin_grades_by_student(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show student list → pick one → see their grades."""
    students = sheets.get_all_students()
    if not students:
        await update.message.reply_text("Talabalar yo'q.")
        return
    rows, total_pages, page = _paginated_rows(
        students, page,
        lambda s: KeyboardButton(f"👤 {s['PersonalID']}: {s['FullName']}")
    )
    context.user_data["admin_list_page"] = page
    context.user_data["state"] = S.ADMIN_GRADES_PICK_STUDENT
    await update.message.reply_text(
        f"Talabani tanlang ({page + 1}/{total_pages}):",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def admin_grades_by_homework(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_subs = sheets.get_submissions()
    if not all_subs:
        await update.message.reply_text("Hozircha topshiriqlar yo'q.")
        return
    grouped = defaultdict(list)
    for s in all_subs:
        grouped[s["AssignmentID"]].append(s)
    rows = [[KeyboardButton(f"📚 {hw_id} ({len(ss)} ta)")] for hw_id, ss in grouped.items()]
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.ADMIN_GRADES_PICK_HW
    await update.message.reply_text(
        "Vazifani tanlang:", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def admin_grades_show_student(update: Update, context: ContextTypes.DEFAULT_TYPE, student_id: str):
    grade_list = sheets.get_grades(student_id)
    student = sheets.find_student_by_id(student_id)
    name = student["FullName"] if student else student_id
    if not grade_list:
        await update.message.reply_text(f"{name} — hozircha baholar yo'q.")
        return
    chunk = f"🎓 {name} baholar\n\n"
    for g in grade_list:
        chunk += f"Vazifa: {g['AssignmentID']}\nBall: {g['Score']}\nIzoh: {g['Feedback']}\nSana: {g['Date']}\n\n"
    await update.message.reply_text(chunk[:4000])


async def admin_grades_show_hw(update: Update, context: ContextTypes.DEFAULT_TYPE, hw_id: str):
    all_grades = sheets.get_grades()
    hw_grades = [g for g in all_grades if str(g["AssignmentID"]) == hw_id]
    if not hw_grades:
        await update.message.reply_text("Bu vazifa uchun baholar yo'q.")
        return
    chunk = f"📚 {hw_id} — barcha baholar\n\n"
    for g in hw_grades:
        student = sheets.find_student_by_id(g["StudentID"])
        name = student["FullName"] if student else g["StudentID"]
        chunk += f"{name}: {g['Score']} — {g['Feedback'][:80]}\n"
    await update.message.reply_text(chunk[:4000])


# ─── BAHO QO'SHISH ────────────────────────────────────────────────────────────

async def add_grade_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return ConversationHandler.END
    all_subs = sheets.get_submissions()
    ungraded = [s for s in all_subs if not s.get("Grade")]
    if not ungraded:
        await update.message.reply_text("Baholanmagan topshiriqlar yo'q.")
        return ConversationHandler.END
    # Group by student+assignment, show only first SUB id as key
    seen = set()
    rows = []
    for s in ungraded:
        key = (s["StudentID"], s["AssignmentID"])
        if key in seen:
            continue
        seen.add(key)
        student = sheets.find_student_by_id(s["StudentID"])
        name = student["FullName"] if student else s["StudentID"]
        rows.append([KeyboardButton(f"📝 {s['SubmissionID']}: {name} — {s['AssignmentID']}")])
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = "add_grade_pick_sub"
    await update.message.reply_text(
        "Topshiriqni tanlang:", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )
    return ADMIN_GRADE_SCORE


async def add_grade_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await update.message.reply_text("Bekor qilindi.", reply_markup=admin_keyboard())
        return ConversationHandler.END

    state = context.user_data.get("state")

    # Step 1: submission picked → show its content, ask score
    if state == "add_grade_pick_sub" and text.startswith("📝 "):
        sub_id = text.split(" ")[1].rstrip(":")
        all_subs = sheets.get_submissions()
        s = next((x for x in all_subs if x["SubmissionID"] == sub_id), None)
        if not s:
            await update.message.reply_text("Topshiriq topilmadi.")
            return ADMIN_GRADE_SCORE
        context.user_data["grade_sub_id"] = sub_id
        context.user_data["grade_student"] = s["StudentID"]
        context.user_data["grade_hw"] = s["AssignmentID"]
        student = sheets.find_student_by_id(s["StudentID"])
        name = student["FullName"] if student else s["StudentID"]
        # Show all submissions from same student+assignment
        all_subs = sheets.get_submissions(
            assignment_id=s["AssignmentID"],
            student_id=s["StudentID"]
        )
        msg = f"👤 {name}\nVazifa: {s['AssignmentID']}\nSana: {s['Date']}\n({len(all_subs)} ta fayl)"
        await update.message.reply_text(msg)
        for sub in all_subs:
            if sub["FileType"] == "photo" and sub["FileID"]:
                await context.bot.send_photo(update.effective_chat.id, sub["FileID"],
                    caption=sub["Content"] or "")
            elif sub["FileType"] == "document" and sub["FileID"]:
                await context.bot.send_document(update.effective_chat.id, sub["FileID"],
                    caption=sub["Content"] or "")
            elif sub["FileType"] == "text" and sub["Content"]:
                await update.message.reply_text(f"📝 {sub['Content']}")
        context.user_data["state"] = "add_grade_score"
        await update.message.reply_text(
            "Ballni kiriting (masalan: 85/100 yoki A):",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
        )
        return ADMIN_GRADE_SCORE

    # Step 3: score entered → ask feedback
    if state == "add_grade_score":
        context.user_data["grade_score"] = text
        context.user_data["state"] = "add_grade_feedback"
        await update.message.reply_text(
            "Izoh yozing:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
        )
        return ADMIN_GRADE_FEEDBACK

    await update.message.reply_text("Tugmalardan foydalaning.")
    return ADMIN_GRADE_SCORE


async def add_grade_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await update.message.reply_text("Bekor qilindi.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    student_id = context.user_data.get("grade_student")
    hw_id = context.user_data.get("grade_hw")
    score = context.user_data.get("grade_score")
    if not all([student_id, hw_id, score]):
        await update.message.reply_text("Sessiya tugadi. Qaytadan boshlang.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    sub_id = context.user_data.get("grade_sub_id")
    if sub_id:
        sheets.grade_submission(sub_id, score, text)
    sheets.upsert_grade(student_id, hw_id, score, text)
    student = sheets.find_student_by_id(student_id)
    if student:
        try:
            await context.bot.send_message(
                chat_id=int(student["TelegramID"]),
                text=f"🎓 Bahongiz qo'yildi!\n\nVazifa: {hw_id}\nBall: {score}\nIzoh: {text}"
            )
        except Exception:
            pass
    await update.message.reply_text("✅ Baho saqlandi va talabaga xabar yuborildi!", reply_markup=admin_keyboard())
    return ConversationHandler.END


# ─── BROADCAST ────────────────────────────────────────────────────────────────

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Xabarni kimga yuborasiz?",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("👤 Bir talabaga"), KeyboardButton("👥 Barcha talabalarga")],
            [KeyboardButton("🔙 Orqaga")],
        ], resize_keyboard=True)
    )
    return BROADCAST_TARGET


async def broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await update.message.reply_text("Bekor qilindi.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    if text == "👥 Barcha talabalarga":
        context.user_data["broadcast_target"] = "all"
        await update.message.reply_text(
            "Barcha talabalarga yubormoqchi bo'lgan xabarni yozing:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
        )
        return BROADCAST_MSG
    if text == "👤 Bir talabaga":
        await _show_broadcast_student_list(update, context, 0)
        return BROADCAST_PICK_STUDENT
    return BROADCAST_TARGET


async def _show_broadcast_student_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    students = [s for s in sheets.get_all_students() if str(s.get("TelegramID", "")).strip()]
    rows, total_pages, page = _paginated_rows(
        students, page,
        lambda s: KeyboardButton(f"👤 {s['FullName']} ({s['PersonalID']})")
    )
    context.user_data["admin_list_page"] = page
    await update.message.reply_text(
        f"Qaysi talabaga xabar yubormoqchisiz? ({page + 1}/{total_pages})",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def broadcast_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await update.message.reply_text(
            "Xabarni kimga yuborasiz?",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("👤 Bir talabaga"), KeyboardButton("👥 Barcha talabalarga")],
                [KeyboardButton("🔙 Orqaga")],
            ], resize_keyboard=True)
        )
        return BROADCAST_TARGET
    if text == "⬅️ Oldingi":
        await _show_broadcast_student_list(update, context, context.user_data.get("admin_list_page", 0) - 1)
        return BROADCAST_PICK_STUDENT
    if text == "➡️ Keyingi":
        await _show_broadcast_student_list(update, context, context.user_data.get("admin_list_page", 0) + 1)
        return BROADCAST_PICK_STUDENT
    if text.startswith("👤 "):
        students = sheets.get_all_students()
        for s in students:
            if text == f"👤 {s['FullName']} ({s['PersonalID']})":
                context.user_data["broadcast_target"] = "one"
                context.user_data["broadcast_student_id"] = str(s["TelegramID"])
                context.user_data["broadcast_student_name"] = s["FullName"]
                await update.message.reply_text(
                    f"*{s['FullName']}* ga yubormoqchi bo'lgan xabarni yozing:",
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
                )
                return BROADCAST_MSG
    return BROADCAST_PICK_STUDENT


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await update.message.reply_text("Bekor qilindi.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    target = context.user_data.pop("broadcast_target", "all")
    if target == "one":
        tg_id = context.user_data.pop("broadcast_student_id", None)
        name = context.user_data.pop("broadcast_student_name", "")
        if not tg_id:
            await update.message.reply_text("Xatolik: talaba topilmadi.", reply_markup=admin_keyboard())
            return ConversationHandler.END
        try:
            await context.bot.send_message(chat_id=int(tg_id), text=f"📢 E'lon\n\n{text}")
            await update.message.reply_text(f"✅ *{name}* ga xabar yuborildi!", parse_mode="Markdown", reply_markup=admin_keyboard())
        except Exception:
            await update.message.reply_text("❌ Xabar yuborilmadi.", reply_markup=admin_keyboard())
        return ConversationHandler.END
    students = sheets.get_all_students()
    blacklist = sheets.get_blacklist()
    sent, failed = 0, 0
    for s in students:
        if str(s.get("TelegramID", "")) in blacklist:
            continue
        try:
            await context.bot.send_message(chat_id=int(s["TelegramID"]), text=f"📢 E'lon\n\n{text}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"Xabar yuborildi\n✅ Yuborildi: {sent}\n❌ Yuborilmadi: {failed}",
        reply_markup=admin_keyboard()
    )
    return ConversationHandler.END


# ─── SAVOLLAR ─────────────────────────────────────────────────────────────────

async def admin_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    questions = sheets.get_unanswered_questions()
    if not questions:
        await update.message.reply_text("Javobsiz savollar yo'q!")
        return
    grouped = defaultdict(list)
    for q in questions:
        grouped[q["StudentID"]].append(q)
    rows = []
    for student_id, qs in grouped.items():
        student = sheets.find_student_by_id(student_id)
        name = student["FullName"] if student else student_id
        rows.append([KeyboardButton(f"👤 {student_id}: {name} ({len(qs)} ta savol)")])
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.QUESTIONS_LIST
    await update.message.reply_text(
        "Ochiq savollari bor talabalar:",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )

# ─── STAGE PROMOTION (midterm → BI) ───────────────────────────────────────────

async def promote_to_bi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/promote STU-0001 — grant BI-stage access (must be in BI channel from now on)."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /promote STU-0001")
        return
    pid = context.args[0].strip()
    ok, reason = sheets.set_student_stage(pid, "bi")
    if ok:
        await update.message.reply_text(f"✅ {pid} BI bosqichiga o'tkazildi.")
        s = sheets.find_student_by_id(pid)
        if s:
            try:
                await context.bot.send_message(
                    int(s["TelegramID"]),
                    "🎉 Tabriklaymiz! Siz BI bosqichiga o'tdingiz.\n"
                    "Endi BI kanaliga ham a'zo bo'ling."
                )
            except Exception:
                pass
    elif reason == "no_column":
        await update.message.reply_text(
            "⚠️ Students jadvalida 'Stage' ustuni yo'q. Avval shu ustunni qo'shing."
        )
    else:
        await update.message.reply_text(f"❌ {pid} topilmadi.")


async def demote_to_bito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/demote STU-0001 — move back to BITO-only stage."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /demote STU-0001")
        return
    pid = context.args[0].strip()
    s = sheets.find_student_by_id(pid)
    if s and str(s.get("Stage", "")).strip().lower() == "pending":
        await update.message.reply_text(
            f"❌ {pid} hali tasdiqlanmagan (pending). "
            "Tasdiqlash uchun /approve dan foydalaning."
        )
        return
    ok, reason = sheets.set_student_stage(pid, "bito")
    if ok:
        await update.message.reply_text(f"✅ {pid} BITO bosqichiga qaytarildi.")
    elif reason == "no_column":
        await update.message.reply_text(
            "⚠️ Students jadvalida 'Stage' ustuni yo'q. Avval shu ustunni qo'shing."
        )
    else:
        await update.message.reply_text(f"❌ {pid} topilmadi.")


async def approve_student_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/approve STU-0001 — promote pending → bito."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /approve STU-0001")
        return
    pid = context.args[0].strip()
    ok, result = sheets.approve_student(pid)
    if ok:
        telegram_id = result
        await update.message.reply_text(f"✅ {pid} BITO bosqichiga tasdiqlandi.")
        try:
            msg = "🎉 Tabriklaymiz! Siz imtihondan o'tdingiz va BITO dasturiga qabul qilindingiz.\n\n"
            if BITO_CHANNEL_LINK:
                msg += f"BITO kanaliga a'zo bo'ling: {BITO_CHANNEL_LINK}"
            await context.bot.send_message(int(telegram_id), msg, reply_markup=student_keyboard())
        except Exception:
            pass
    elif result == "wrong_stage":
        await update.message.reply_text(f"❌ {pid} hozir 'pending' bosqichida emas.")
    elif result == "no_column":
        await update.message.reply_text("⚠️ Students jadvalida 'Stage' ustuni yo'q.")
    else:
        await update.message.reply_text(f"❌ {pid} topilmadi.")


async def exam_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/exam_list — show all students signed up for the external exam."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    signups = sheets.get_exam_signups()
    if not signups:
        await update.message.reply_text("Imtihonga yozilgan talabalar yo'q.")
        return
    chunk = f"📋 Imtihonga yozilganlar ({len(signups)} nafar)\n\n"
    for s in signups:
        line = (
            f"{s['PersonalID']} — {s['FullName']}\n"
            f"  Tel: {s.get('Telefon nomer', '')}\n"
            f"  Telegram: {student_contact_link(s)}\n\n"
        )
        if len(chunk) + len(line) > 4000:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += line
    if chunk:
        await update.message.reply_text(chunk)


async def expel_student_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/expel STU-0001 [reason] — add student to blacklist."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /expel STU-0001 [sabab]")
        return
    pid = context.args[0].strip()
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    ok, result = sheets.add_to_blacklist(pid, reason)
    if ok:
        await update.message.reply_text(f"🚫 {pid} botdan chiqarildi.")
        try:
            contact = admin_contact()
            msg = "🚫 Siz botdan chiqarildingiz."
            if contact:
                msg += f"\nBatafsil ma'lumot uchun: {contact}"
            await context.bot.send_message(int(result), msg)
        except Exception:
            pass
    elif result == "already_blacklisted":
        await update.message.reply_text(f"⚠️ {pid} allaqachon blacklistda.")
    else:
        await update.message.reply_text(f"❌ {pid} topilmadi.")


async def unexpel_student_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unexpel STU-0001 — remove student from blacklist."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /unexpel STU-0001")
        return
    pid = context.args[0].strip()
    removed, telegram_id = sheets.remove_from_blacklist(pid)
    if removed:
        await update.message.reply_text(f"✅ {pid} blacklistdan chiqarildi.")
        if telegram_id:
            try:
                await context.bot.send_message(
                    int(telegram_id),
                    "✅ Sizning botdan foydalanish huquqingiz tiklandi. Endi botdan foydalanishingiz mumkin."
                )
            except Exception:
                pass
    else:
        await update.message.reply_text(f"❌ {pid} blacklistda topilmadi.")


async def expel_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show keyboard of all active students for admin to pick and expel."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    students = sheets.get_all_students()
    blacklist = sheets.get_blacklist()
    active = [s for s in students if str(s.get("TelegramID", "")) not in blacklist]
    if not active:
        await update.message.reply_text("Faol talabalar yo'q.", reply_markup=admin_keyboard())
        return
    rows, total_pages, page = _paginated_rows(
        active, page,
        lambda s: KeyboardButton(f"🚫 {s['PersonalID']}: {s['FullName']}")
    )
    context.user_data["admin_list_page"] = page
    context.user_data["state"] = S.ADMIN_EXPEL_PICK
    await update.message.reply_text(
        f"Botdan chiqarish uchun talabani tanlang ({page + 1}/{total_pages}):",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def unexpel_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show keyboard of blacklisted students for admin to pick and unban."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    students = sheets.get_all_students()
    blacklist = sheets.get_blacklist()
    banned = [s for s in students if str(s.get("TelegramID", "")) in blacklist]
    if not banned:
        await update.message.reply_text("Blacklistda talabalar yo'q.", reply_markup=admin_keyboard())
        return
    rows = [[KeyboardButton(f"✅ {s['PersonalID']}: {s['FullName']}")] for s in banned]
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.ADMIN_UNEXPEL_PICK
    await update.message.reply_text(
        "Blacklistdan chiqarish uchun talabani tanlang:",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def admin_excuses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending excuses for admin to review."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    try:
        pending = sheets.get_pending_excuses()
    except Exception:
        await update.message.reply_text("Xatolik yuz berdi.")
        return
    if not pending:
        await update.message.reply_text("Ko'rib chiqilmagan uzrlar yo'q!", reply_markup=admin_keyboard())
        return
    rows = []
    for r in pending:
        student = sheets.find_student_by_id(str(r.get("StudentID", "")))
        name = student["FullName"] if student else str(r.get("StudentID", ""))
        rows.append([KeyboardButton(
            f"📝 {r['StudentID']}|{r['Program']}|{r['HWID']}: {name}"
        )])
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.ADMIN_EXCUSE_LIST
    await update.message.reply_text(
        f"📋 Ko'rib chiqilmagan uzrlar ({len(pending)} ta):",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True),
    )


async def approve_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show keyboard of pending students for admin to approve."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    students = sheets.get_all_students()
    pending = [s for s in students if str(s.get("Stage", "")).strip().lower() == "pending"]
    if not pending:
        await update.message.reply_text("Tasdiqlash kutayotgan talabalar yo'q.", reply_markup=admin_keyboard())
        return

    def _make_btn(s):
        exam_flag = " ✓" if str(s.get("ExamRegistered", "")).strip().upper() == "TRUE" else ""
        return KeyboardButton(f"✅ {s['PersonalID']}: {s['FullName']}{exam_flag}")

    rows, total_pages, page = _paginated_rows(pending, page, _make_btn)
    context.user_data["admin_list_page"] = page
    context.user_data["state"] = S.ADMIN_APPROVE_PICK
    await update.message.reply_text(
        f"BITO ga tasdiqlash uchun talabani tanlang ({page + 1}/{total_pages}):",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def promote_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show keyboard of BITO students for admin to pick and promote to BI."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    students = sheets.get_all_students()
    bito_students = [
        s for s in students
        if str(s.get("Stage", "")).strip().lower() in ("bito", "")
    ]
    if not bito_students:
        await update.message.reply_text("BITO bosqichida talabalar yo'q.", reply_markup=admin_keyboard())
        return
    rows, total_pages, page = _paginated_rows(
        bito_students, page,
        lambda s: KeyboardButton(f"⬆️ {s['PersonalID']}: {s['FullName']}")
    )
    context.user_data["admin_list_page"] = page
    context.user_data["state"] = S.ADMIN_PROMOTE_PICK
    await update.message.reply_text(
        f"BI bosqichiga ko'tarish uchun talabani tanlang ({page + 1}/{total_pages}):",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def demote_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    """Show keyboard of BI students for admin to pick and demote to BITO."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    students = sheets.get_all_students()
    bi_students = [s for s in students if str(s.get("Stage", "")).strip().lower() == "bi"]
    if not bi_students:
        await update.message.reply_text("BI bosqichida talabalar yo'q.", reply_markup=admin_keyboard())
        return
    rows, total_pages, page = _paginated_rows(
        bi_students, page,
        lambda s: KeyboardButton(f"⬇️ {s['PersonalID']}: {s['FullName']}")
    )
    context.user_data["admin_list_page"] = page
    context.user_data["state"] = S.ADMIN_DEMOTE_PICK
    await update.message.reply_text(
        f"BITO bosqichiga qaytarish uchun talabani tanlang ({page + 1}/{total_pages}):",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


# ─── STATE HANDLERS (admin) ───────────────────────────────────────────────────

async def _sh_admin_grades_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.effective_user.id
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(uid))
        return
    if text == "👤 Talaba bo'yicha":
        await admin_grades_by_student(update, context)
    elif text == "📚 Vazifa bo'yicha":
        await admin_grades_by_homework(update, context)


async def _sh_admin_grades_pick_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await admin_grades_menu(update, context)
        return
    if text == "⬅️ Oldingi":
        await admin_grades_by_student(update, context, context.user_data.get("admin_list_page", 0) - 1)
        return
    if text == "➡️ Keyingi":
        await admin_grades_by_student(update, context, context.user_data.get("admin_list_page", 0) + 1)
        return
    if text.startswith("👤 "):
        student_id = text.split(" ")[1].rstrip(":")
        await admin_grades_show_student(update, context, student_id)
        context.user_data["state"] = S.MAIN


async def _sh_admin_grades_pick_hw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await admin_grades_menu(update, context)
        return
    if text.startswith("📚 "):
        hw_id = text.split(" ")[1].rstrip("(").strip()
        await admin_grades_show_hw(update, context, hw_id)
        context.user_data["state"] = S.MAIN


async def _sh_questions_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if not text.startswith("👤 "):
        return
    student_id = text.split(" ")[1].rstrip(":")
    questions = sheets.get_unanswered_questions()
    student_qs = sorted(
        [q for q in questions if q["StudentID"] == student_id],
        key=lambda q: q["DateAsked"]
    )
    rows = [[KeyboardButton(f"❓ {q['QuestionID']}: {q['Question'][:40]}")] for q in student_qs]
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.QUESTION_DETAIL_LIST
    await update.message.reply_text("Savollar:", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))


async def _sh_question_detail_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await admin_questions(update, context)
        return
    if not text.startswith("❓ "):
        return
    q_id = text.split(" ")[1].rstrip(":")
    questions = sheets.get_unanswered_questions()
    q = next((x for x in questions if x["QuestionID"] == q_id), None)
    if not q:
        await update.message.reply_text("Savol topilmadi.")
        return
    context.user_data["answer_q_id"] = q_id
    context.user_data["waiting_for_answer"] = True
    context.user_data["state"] = S.MAIN
    await update.message.reply_text(
        f"Savol {q_id}\nTalaba: {q['StudentID']}\nSana: {q['DateAsked']}\n\nSavol: {q['Question']}\n\nJavobingizni yozing (matn, rasm yoki fayl):",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
    )
    # Show student's attachment if present
    s_file_id = str(q.get("StudentFileID", "") or "").strip()
    s_file_type = str(q.get("StudentFileType", "") or "").strip()
    if s_file_id:
        from handlers.student import _send_qa_file
        await _send_qa_file(context.bot, update.effective_chat.id, s_file_id, s_file_type)


async def _sh_admin_approve_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if text == "⬅️ Oldingi":
        await approve_pick_student(update, context, context.user_data.get("admin_list_page", 0) - 1)
        return
    if text == "➡️ Keyingi":
        await approve_pick_student(update, context, context.user_data.get("admin_list_page", 0) + 1)
        return
    if not text.startswith("✅ "):
        return
    pid = text[2:].strip().split(":")[0].strip()
    ok, result = sheets.approve_student(pid)
    if ok:
        logging.info("APPROVED: student_id=%s", pid)
        await update.message.reply_text(f"✅ {pid} BITO bosqichiga tasdiqlandi.", reply_markup=admin_keyboard())
        try:
            msg = "🎉 Tabriklaymiz! Siz imtihondan o'tdingiz va BITO dasturiga qabul qilindingiz.\n\n"
            if BITO_CHANNEL_LINK:
                msg += f"BITO kanaliga a'zo bo'ling: {BITO_CHANNEL_LINK}"
            await context.bot.send_message(int(result), msg, reply_markup=student_keyboard())
        except Exception:
            pass
    elif result == "wrong_stage":
        await update.message.reply_text(f"❌ {pid} hozir 'pending' bosqichida emas.", reply_markup=admin_keyboard())
    else:
        await update.message.reply_text(f"❌ {pid} topilmadi.", reply_markup=admin_keyboard())
    context.user_data["state"] = S.MAIN


async def _sh_admin_promote_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if text == "⬅️ Oldingi":
        await promote_pick_student(update, context, context.user_data.get("admin_list_page", 0) - 1)
        return
    if text == "➡️ Keyingi":
        await promote_pick_student(update, context, context.user_data.get("admin_list_page", 0) + 1)
        return
    if not text.startswith("⬆️ "):
        return
    pid = text[2:].strip().split(":")[0].strip()
    ok, reason = sheets.set_student_stage(pid, "bi")
    if ok:
        logging.info("PROMOTED_TO_BI: student_id=%s", pid)
        await update.message.reply_text(f"✅ {pid} BI bosqichiga o'tkazildi.", reply_markup=admin_keyboard())
        s = sheets.find_student_by_id(pid)
        if s:
            try:
                msg = "🎉 Tabriklaymiz! Siz BI bosqichiga o'tdingiz.\nEndi BI kanaliga ham a'zo bo'ling."
                if BI_CHANNEL_LINK:
                    msg += f"\n{BI_CHANNEL_LINK}"
                await context.bot.send_message(int(s["TelegramID"]), msg)
            except Exception:
                pass
    else:
        await update.message.reply_text(f"❌ Xatolik: {reason}", reply_markup=admin_keyboard())
    context.user_data["state"] = S.MAIN


async def _sh_admin_demote_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if text == "⬅️ Oldingi":
        await demote_pick_student(update, context, context.user_data.get("admin_list_page", 0) - 1)
        return
    if text == "➡️ Keyingi":
        await demote_pick_student(update, context, context.user_data.get("admin_list_page", 0) + 1)
        return
    if not text.startswith("⬇️ "):
        return
    pid = text[2:].strip().split(":")[0].strip()
    ok, reason = sheets.set_student_stage(pid, "bito")
    if ok:
        await update.message.reply_text(f"✅ {pid} BITO bosqichiga qaytarildi.", reply_markup=admin_keyboard())
        s = sheets.find_student_by_id(pid)
        if s:
            try:
                contact = admin_contact()
                msg = "ℹ️ Sizning bosqichingiz BITO ga qaytarildi."
                if contact:
                    msg += f"\nBatafsil ma'lumot uchun: {contact}"
                await context.bot.send_message(int(s["TelegramID"]), msg)
            except Exception:
                pass
    else:
        await update.message.reply_text(f"❌ Xatolik: {reason}", reply_markup=admin_keyboard())
    context.user_data["state"] = S.MAIN


async def _sh_admin_expel_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if text == "⬅️ Oldingi":
        await expel_pick_student(update, context, context.user_data.get("admin_list_page", 0) - 1)
        return
    if text == "➡️ Keyingi":
        await expel_pick_student(update, context, context.user_data.get("admin_list_page", 0) + 1)
        return
    if not text.startswith("🚫 "):
        return
    pid = text[2:].strip().split(":")[0].strip()
    ok, result = sheets.add_to_blacklist(pid)
    if ok:
        logging.info("EXPELLED: student_id=%s", pid)
        await update.message.reply_text(f"🚫 {pid} botdan chiqarildi.", reply_markup=admin_keyboard())
        try:
            contact = admin_contact()
            msg = "🚫 Siz botdan chiqarildingiz."
            if contact:
                msg += f"\nBatafsil ma'lumot uchun: {contact}"
            await context.bot.send_message(int(result), msg)
        except Exception:
            pass
    else:
        await update.message.reply_text(f"❌ Xatolik: {result}", reply_markup=admin_keyboard())
    context.user_data["state"] = S.MAIN


async def _sh_admin_unexpel_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if not text.startswith("✅ "):
        return
    pid = text[2:].strip().split(":")[0].strip()
    removed, telegram_id = sheets.remove_from_blacklist(pid)
    if removed:
        await update.message.reply_text(f"✅ {pid} blacklistdan chiqarildi.", reply_markup=admin_keyboard())
        if telegram_id:
            try:
                await context.bot.send_message(
                    int(telegram_id),
                    "✅ Sizning botdan foydalanish huquqingiz tiklandi. Endi botdan foydalanishingiz mumkin."
                )
            except Exception:
                pass
    else:
        await update.message.reply_text("❌ Xatolik: topilmadi.", reply_markup=admin_keyboard())
    context.user_data["state"] = S.MAIN


async def _sh_admin_excuse_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if not text.startswith("📝 "):
        return
    raw = text[2:].strip()
    key_part = raw.split(":")[0].strip()
    parts = key_part.split("|")
    if len(parts) != 3:
        return
    pid, prog, hw_id = parts
    excuses = sheets.get_pending_excuses()
    row = next((r for r in excuses if
                str(r.get("StudentID", "")) == pid and
                str(r.get("Program", "")) == prog and
                str(r.get("HWID", "")) == hw_id), None)
    if not row:
        return
    excuse_text = row.get("ExcuseText", "") or "(matn yo'q)"
    s = sheets.find_student_by_id(pid)
    name = s["FullName"] if s else pid
    context.user_data["excuse_review_key"] = (pid, prog, hw_id)
    context.user_data["state"] = S.ADMIN_EXCUSE_REVIEW
    await update.message.reply_text(
        f"👤 {name}\nVazifa: {hw_id}\n\n📝 Uzr matni:\n{excuse_text}",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("✅ Uzrli"), KeyboardButton("❌ Sababsiz")],
            [KeyboardButton("🔙 Orqaga")],
        ], resize_keyboard=True),
    )


async def _sh_admin_excuse_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await admin_excuses(update, context)
        return
    if text not in ("✅ Uzrli", "❌ Sababsiz"):
        return
    key = context.user_data.get("excuse_review_key")
    if not key or len(key) != 3:
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Sessiya tugadi. Qaytadan urinib ko'ring.", reply_markup=admin_keyboard())
        return
    pid, prog, hw_id = key
    status = "uzrli" if text == "✅ Uzrli" else "sababsiz"
    admin_name = update.effective_user.first_name
    sheets.set_excuse_status(pid, prog, hw_id, status, admin_name)
    if status == "sababsiz":
        unexcused = sheets.count_unexcused(pid, prog)
        if unexcused >= 3:
            s = sheets.find_student_by_id(pid)
            if s:
                try:
                    contact = admin_contact()
                    warn_msg = (
                        f"⚠️ Siz {prog.upper()} dasturida 3 ta sababsiz deadline o'tkazib yubordingiz.\n"
                        "Admin ko'rib chiqadi."
                    )
                    if contact:
                        warn_msg += f"\nSavol bo'lsa: {contact}"
                    await context.bot.send_message(int(s["TelegramID"]), warn_msg)
                except Exception:
                    pass
            await update.message.reply_text(
                f"⚠️ {pid} — {prog.upper()}da 3 ta sababsiz miss! Flaglandi.",
                reply_markup=admin_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"✅ Sababsiz ({unexcused}/3 uchun {prog.upper()})",
                reply_markup=admin_keyboard(),
            )
    else:
        await update.message.reply_text("✅ Uzrli deb belgilandi.", reply_markup=admin_keyboard())
    context.user_data["state"] = S.MAIN


async def _handle_waiting_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or update.message.caption or ""
    uid = update.effective_user.id
    if text == "🔙 Orqaga":
        context.user_data["waiting_for_answer"] = False
        await update.message.reply_text("Bekor qilindi.", reply_markup=stage_keyboard(uid))
        return

    # Detect file attachment from admin
    file_id, file_type = "", ""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"

    if not text.strip() and not file_id:
        await update.message.reply_text("❌ Javob matni yozing yoki fayl yuboring.")
        return

    q_id = context.user_data.get("answer_q_id")
    if not q_id:
        context.user_data["waiting_for_answer"] = False
        await update.message.reply_text("Sessiya tugadi. Qaytadan boshlang.", reply_markup=stage_keyboard(uid))
        return

    admin_name = update.effective_user.first_name
    student_id, _ = sheets.answer_question(q_id, text, admin_name, file_id, file_type)
    context.user_data["waiting_for_answer"] = False
    if student_id:
        student = sheets.find_student_by_id(student_id)
        if student:
            try:
                await context.bot.send_message(
                    chat_id=int(student["TelegramID"]),
                    text=f"✅ Savolingizga javob berildi!\n\nSavol ID: `{q_id}`\nJavob: {text}",
                    parse_mode="Markdown",
                )
                if file_id:
                    from handlers.student import _send_qa_file
                    await _send_qa_file(context.bot, int(student["TelegramID"]), file_id, file_type)
            except Exception:
                pass
    await update.message.reply_text("✅ Javob saqlandi va talabaga yuborildi!", reply_markup=stage_keyboard(uid))

# ─── GRADE REVIEW / OVERRIDE ─────────────────────────────────────────────────

async def checkgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/checkgrade STU-0001 bito-01 — show AI feedback + current score for review."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if len(context.args or []) < 2:
        await update.message.reply_text("Foydalanish: /checkgrade STU-0001 bito-01")
        return
    sid, hw_id = context.args[0].strip(), context.args[1].strip()
    student = sheets.find_student_by_id(sid)
    name = student["FullName"] if student else sid
    grade_list = sheets.get_grades(sid)
    grade = next((g for g in grade_list if str(g.get("AssignmentID", "")) == hw_id), None)
    if not grade:
        await update.message.reply_text(
            f"❌ {name} — {hw_id} uchun baho topilmadi.\n"
            f"Ball qo'shish uchun: /setgrade {sid} {hw_id} <ball>"
        )
        return
    feedback = str(grade.get("Feedback", "") or "")
    score = str(grade.get("Score", ""))
    date = str(grade.get("Date", ""))
    report = (
        f"📊 *{name}* — {hw_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💯 Ball: {score}\n"
        f"📅 Sana: {date}\n\n"
        f"📝 AI izohi:\n{feedback[:800]}\n\n"
        f"Ballni o'zgartirish uchun:\n`/setgrade {sid} {hw_id} <yangi_ball>`"
    )
    await update.message.reply_text(report[:4000], parse_mode="Markdown")


async def setgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setgrade STU-0001 bito-01 90 — override score, notify student."""
    if _guard(update, context):
        await update.message.reply_text("Faqat admin uchun.")
        return
    if len(context.args or []) < 3:
        await update.message.reply_text("Foydalanish: /setgrade STU-0001 bito-01 90")
        return
    sid, hw_id, score = context.args[0].strip(), context.args[1].strip(), context.args[2].strip()
    note = " ".join(context.args[3:]) if len(context.args) > 3 else ""
    feedback_note = f"Admin tomonidan o'zgartirildi: {score}" + (f" — {note}" if note else "")
    sheets.upsert_grade(sid, hw_id, score, feedback_note)
    await update.message.reply_text(f"✅ {sid} — {hw_id}: ball *{score}* ga o'zgartirildi.", parse_mode="Markdown")
    student = sheets.find_student_by_id(sid)
    if student:
        hw = sheets.get_homework(hw_id)
        hw_title = str(hw.get("Title", hw_id)) if hw else hw_id
        try:
            msg = (
                f"📊 *Balingiz yangilandi!*\n\n"
                f"Vazifa: {hw_id} — {hw_title}\n"
                f"Yangi ball: *{score}*\n\n"
                "Ustoz ishingizni ko'rib chiqib, ballingizni o'zgartirdi."
            )
            if note:
                msg += f"\nIzoh: {note}"
            await context.bot.send_message(int(student["TelegramID"]), msg, parse_mode="Markdown")
        except Exception:
            pass


# ─── HOMEWORK MANAGEMENT ─────────────────────────────────────────────────────

def _get_file_from_msg(msg: Message) -> tuple[str | None, str | None]:
    if msg.document:
        return msg.document.file_id, "document"
    if msg.photo:
        return msg.photo[-1].file_id, "photo"
    if msg.video:
        return msg.video.file_id, "video"
    if msg.audio:
        return msg.audio.file_id, "audio"
    return None, None


def _hw_list_keyboard(hws: list) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(f"📚 {h['HWID']}: {h.get('Title', h['HWID'])}")] for h in hws]
    rows.append([KeyboardButton("➕ Yangi vazifa"), KeyboardButton("🗑️ O'chirish")])
    rows.append([KeyboardButton("🔙 Orqaga")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def _go_to_hw_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    program = context.user_data.get("admin_hw_program", "bito")
    hws = sheets.get_homeworks_by_program(program)
    context.user_data["state"] = S.ADMIN_HW_LIST
    label = "BITO" if program == "bito" else "BI"
    await update.message.reply_text(
        f"📚 {label} vazifalari:",
        reply_markup=_hw_list_keyboard(hws),
    )


async def _go_to_hw_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hw_id = context.user_data.get("admin_hw_file_target", "")
    program = context.user_data.get("admin_hw_program", "bito")
    hw = sheets.get_homework(hw_id)
    has_q = bool(str(hw.get("FileID", "") if hw else "").strip())
    has_a = bool(str(hw.get("AnswerFileID", "") if hw else "").strip())
    q_label = "📎 Savol fayli ✅" if has_q else "📎 Savol fayli"
    buttons = [[KeyboardButton(q_label)]]
    if program == "bito":
        a_label = "📋 Javob fayli ✅" if has_a else "📋 Javob fayli"
        buttons.append([KeyboardButton(a_label)])
    buttons.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.ADMIN_HW_DETAIL
    status = ("✅ Savol" if has_q else "❌ Savol")
    if program == "bito":
        status += (" | ✅ Javob" if has_a else " | ❌ Javob")
    await update.message.reply_text(
        f"*{hw_id}* — {status}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )


async def set_hw_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _guard(update, context):
        return
    context.user_data["state"] = S.ADMIN_HW_PROGRAM_PICK
    await update.message.reply_text(
        "Qaysi dastur?",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("BITO"), KeyboardButton("BI")],
            [KeyboardButton("🔙 Orqaga")],
        ], resize_keyboard=True),
    )


async def _sh_admin_hw_program_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=admin_keyboard())
        return
    if text not in ("BITO", "BI"):
        return
    context.user_data["admin_hw_program"] = text.lower()
    await _go_to_hw_list(update, context)


async def _sh_admin_hw_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    program = context.user_data.get("admin_hw_program", "bito")

    if text == "🔙 Orqaga":
        await set_hw_file_start(update, context)
        return

    if text == "➕ Yangi vazifa":
        context.user_data["state"] = S.ADMIN_HW_NEW_TITLE
        await update.message.reply_text(
            "Yangi vazifa sarlavhasini kiriting:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )
        return

    if text == "🗑️ O'chirish":
        hws = sheets.get_homeworks_by_program(program)
        if not hws:
            await update.message.reply_text("Hozircha vazifalar yo'q.")
            return
        rows = [[KeyboardButton(f"🗑️ {h['HWID']}: {h.get('Title', h['HWID'])}")] for h in hws]
        rows.append([KeyboardButton("🔙 Orqaga")])
        context.user_data["state"] = S.ADMIN_HW_DELETE_PICK
        await update.message.reply_text(
            "Qaysi vazifani o'chirmoqchisiz?",
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True),
        )
        return

    if text.startswith("📚 "):
        hw_id = text[2:].strip().split(":")[0].strip()
        context.user_data["admin_hw_file_target"] = hw_id
        await _go_to_hw_detail(update, context)


async def _sh_admin_hw_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await _go_to_hw_list(update, context)
        return
    if "Savol fayli" in text:
        hw_id = context.user_data.get("admin_hw_file_target", "")
        context.user_data["admin_pending_q_files"] = []
        context.user_data["state"] = S.ADMIN_HW_FILE_UPLOAD
        await update.message.reply_text(
            f"*{hw_id}* savol fayl(lar)ini yuboring.\nHammasi tayyor bo'lgach «✅ Saqlash» bosing.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )
    elif "Javob fayli" in text:
        hw_id = context.user_data.get("admin_hw_file_target", "")
        context.user_data["state"] = S.ADMIN_HW_ANSWER_UPLOAD
        await update.message.reply_text(
            f"*{hw_id}* javob faylini yuboring:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )


async def _sh_admin_hw_new_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await _go_to_hw_list(update, context)
        return
    context.user_data["admin_hw_new_title"] = text.strip()
    context.user_data["state"] = S.ADMIN_HW_NEW_DESC
    await update.message.reply_text(
        "Tavsif kiriting (yoki « - » o'tkazib yuborish uchun):",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("-")], [KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
    )


async def _sh_admin_hw_new_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.ADMIN_HW_NEW_TITLE
        await update.message.reply_text(
            "Sarlavha kiriting:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )
        return
    context.user_data["admin_hw_new_desc"] = "" if text.strip() == "-" else text.strip()
    context.user_data["state"] = S.ADMIN_HW_NEW_DAYS
    await update.message.reply_text(
        "Deadline necha kun? (masalan: 7)",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
    )


async def _sh_admin_hw_new_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.ADMIN_HW_NEW_DESC
        await update.message.reply_text(
            "Tavsif kiriting:",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("-")], [KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )
        return
    try:
        days = int(text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ Raqam kiriting (masalan: 7)")
        return

    program = context.user_data.get("admin_hw_program", "bito")
    title = context.user_data.pop("admin_hw_new_title", "")
    desc = context.user_data.pop("admin_hw_new_desc", "")

    existing = sheets.get_homeworks_by_program(program)
    all_ids = {str(h.get("HWID", "")) for h in sheets.get_all_homeworks()}
    n = len(existing) + 1
    hw_id = f"{program}-{n:02d}"
    while hw_id in all_ids:
        n += 1
        hw_id = f"{program}-{n:02d}"

    sheets.add_homework(hw_id, program, title, desc, days)
    await update.message.reply_text(f"✅ *{hw_id}* yaratildi!", parse_mode="Markdown")
    await _go_to_hw_list(update, context)


async def _sh_admin_hw_delete_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await _go_to_hw_list(update, context)
        return
    if not text.startswith("🗑️ "):
        return
    hw_id = text[2:].strip().split(":")[0].strip()
    ok = sheets.delete_homework(hw_id)
    if ok:
        await update.message.reply_text(f"✅ *{hw_id}* o'chirildi.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ *{hw_id}* topilmadi.", parse_mode="Markdown")
    await _go_to_hw_list(update, context)


async def _sh_admin_hw_file_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # kept for backwards compat with state dispatch


async def _sh_admin_hw_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data.pop("admin_pending_q_files", None)
        await _go_to_hw_detail(update, context)
        return

    hw_id = context.user_data.get("admin_hw_file_target")
    if not hw_id:
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Sessiya tugadi.", reply_markup=admin_keyboard())
        return

    if text == "✅ Saqlash":
        pending = context.user_data.pop("admin_pending_q_files", [])
        if not pending:
            await update.message.reply_text("⚠️ Hech fayl yuborilmagan.")
            return
        file_ids = "|".join(f["file_id"] for f in pending)
        file_types = "|".join(f["file_type"] for f in pending)
        ok = sheets.set_homework_file(hw_id, file_ids, file_types)
        if ok:
            await update.message.reply_text(f"✅ {len(pending)} ta savol fayli saqlandi!")
        else:
            await update.message.reply_text(f"⚠️ Jadvalda *{hw_id}* topilmadi.", parse_mode="Markdown")
        await _go_to_hw_detail(update, context)
        return

    file_id, file_type = _get_file_from_msg(update.message)
    if not file_id:
        await update.message.reply_text("⚠️ Fayl yuboring.")
        return

    pending = context.user_data.setdefault("admin_pending_q_files", [])
    pending.append({"file_id": file_id, "file_type": file_type})
    count = len(pending)
    await update.message.reply_text(
        f"✅ {count} ta qabul qilindi. Yana yuborishingiz yoki «✅ Saqlash» bosing.",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("✅ Saqlash")],
            [KeyboardButton("🔙 Orqaga")],
        ], resize_keyboard=True),
    )


async def _sh_admin_hw_answer_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        await _go_to_hw_detail(update, context)
        return
    hw_id = context.user_data.get("admin_hw_file_target")
    if not hw_id:
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Sessiya tugadi.", reply_markup=admin_keyboard())
        return
    file_id, file_type = _get_file_from_msg(update.message)
    if not file_id:
        await update.message.reply_text("⚠️ Fayl yuboring.")
        return
    ok = sheets.set_homework_answer_file(hw_id, file_id, file_type)
    if ok:
        await update.message.reply_text("✅ Javob fayli saqlandi!")
    else:
        await update.message.reply_text("⚠️ AnswerFileID/AnswerFileType ustunlari topilmadi.", reply_markup=admin_keyboard())
    await _go_to_hw_detail(update, context)
