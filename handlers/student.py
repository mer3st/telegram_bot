import os
import time
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from core import sheets
import states as S
from config import make_code, is_admin, REG_CODES, FORM_URL, GEMINI_KEY, ADMIN_IDS
from core.keyboards import student_keyboard, pending_keyboard
from handlers.common import stage_keyboard
from ai.grader import PowerBIMentor
from ai.extractors import extract_text, detect_ext

_ask_cooldown: dict[int, float] = {}
_ASK_COOLDOWN_SECS = 60


def _ai_err_msg(e: Exception) -> str:
    """Return a user-friendly Uzbek message for an AI grading failure."""
    s = str(e).lower()
    if "503" in s or "unavailable" in s or "high demand" in s or "high traffic" in s:
        return (
            "🚦 Teacher AI hozir juda ko'p so'rovlarni qayta ishlayapti "
            "(serverda trafik yuqori). Bu vaqtinchalik muammo — "
            "bir necha daqiqadan so'ng qayta urinib ko'ring."
        )
    return "⚠️ Teacher AI baholashda xato yuz berdi."

mentor = PowerBIMentor(api_key=GEMINI_KEY)

# ─── STATES ───────────────────────────────────────────────────────────────────
ASK_QUESTION = 10
PBI_CHOOSING_HW, PBI_AWAITING_FILE = range(70, 72)


# ─── REGISTER ─────────────────────────────────────────────────────────────────

async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    student = sheets.find_student_by_telegram_id(user_id)
    if student:
        stage = str(student.get("Stage", "")).strip().lower()
        if stage == "pending":
            exam_done = str(student.get("ExamRegistered", "")).strip().upper() == "TRUE"
            if exam_done:
                msg = (
                    f"Siz allaqachon ro'yxatdan o'tgansiz!\nShaxsiy ID: `{student['PersonalID']}`\n\n"
                    "Imtihonga yozilgansiz. Admin tasdig'ini kuting."
                )
            else:
                msg = (
                    f"Siz allaqachon ro'yxatdan o'tgansiz!\nShaxsiy ID: `{student['PersonalID']}`\n\n"
                    "Imtihonga yozilish uchun «📋 Imtihonga yozilish» tugmasini bosing."
                )
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=pending_keyboard())
        else:
            await update.message.reply_text(
                f"Siz allaqachon ro'yxatdan o'tgansiz!\nShaxsiy ID: `{student['PersonalID']}`",
                parse_mode="Markdown", reply_markup=stage_keyboard(user_id)
            )
        return ConversationHandler.END
    code = make_code(user_id)
    REG_CODES[code] = user_id
    sheets.save_code(code, user_id)
    form_link = FORM_URL.format(code)
    await update.message.reply_text(
        f"Ro'yxatdan o'tish uchun shaklni to'ldiring:\n[Bu yerga bosing]({form_link})\n\n"
        "Kodingiz avtomatik to'ldirilgan. Yuboring — bot tasdiqlaydi.",
        parse_mode="Markdown", reply_markup=stage_keyboard(user_id)
    )
    return ConversationHandler.END


async def exam_registration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show exam info screen. Actual registration happens in exam_confirm_handler."""
    user_id = update.effective_user.id
    student = sheets.find_student_by_telegram_id(user_id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    stage = str(student.get("Stage", "")).strip().lower()
    if stage != "pending":
        await update.message.reply_text(
            "Siz allaqachon tasdiqlangansiz!", reply_markup=stage_keyboard(user_id)
        )
        return

    if str(student.get("ExamRegistered", "")).strip().upper() == "TRUE":
        await update.message.reply_text(
            "Siz allaqachon imtihonga yozilgansiz. Admin tasdig'ini kuting.",
            reply_markup=pending_keyboard()
        )
        return

    exam = sheets.get_active_exam()
    if not exam:
        await update.message.reply_text(
            "⏳ Hozircha faol imtihon mavjud emas.\n"
            "Admin imtihon e'lon qilgach, bu tugma orqali yozilishingiz mumkin.",
            reply_markup=pending_keyboard()
        )
        return

    max_slots = int(exam.get("MaxSlots", 0) or 0)
    avail_raw = exam.get("AvailableSlots", "")
    try:
        available = max(0, int(avail_raw)) if str(avail_raw).strip() else max_slots
    except (ValueError, TypeError):
        available = max_slots

    if available == 0:
        await update.message.reply_text(
            f"📋 *Imtihon ma'lumotlari*\n\n"
            f"📅 Sana: {exam.get('ExamDate', '')}\n"
            f"🕐 Vaqt: {exam.get('ExamTime', '')}\n\n"
            "❌ Afsuski, barcha joylar band. Keyingi imtihon uchun kuting.",
            parse_mode="Markdown",
            reply_markup=pending_keyboard()
        )
        return

    context.user_data["state"] = S.EXAM_CONFIRM
    await update.message.reply_text(
        f"📋 *Imtihon ma'lumotlari*\n\n"
        f"📅 Sana: {exam.get('ExamDate', '')}\n"
        f"🕐 Vaqt: {exam.get('ExamTime', '')}\n"
        f"🪑 Bo'sh joylar: {available} ta\n\n"
        "Imtihonga yozilishni tasdiqlaysizmi?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("✅ Ha, yozilaman")],
            [KeyboardButton("🔙 Orqaga")],
        ], resize_keyboard=True)
    )


async def exam_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirm/back buttons on the exam info screen."""
    user_id = update.effective_user.id
    text = update.message.text or ""

    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=pending_keyboard())
        return

    if text != "✅ Ha, yozilaman":
        return

    student = sheets.find_student_by_telegram_id(user_id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return

    context.user_data["state"] = S.MAIN
    ok, result = sheets.register_for_exam(user_id)
    if ok:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"📋 Imtihonga yozildi!\n"
                    f"Talaba: {student['FullName']}\n"
                    f"ID: {student['PersonalID']}\n"
                    f"Telefon: {student.get('Telefon nomer', '')}",
                )
            except Exception:
                pass
        await update.message.reply_text(
            "✅ Imtihonga muvaffaqiyatli yozildingiz!\n\n"
            "Admin tez orada siz bilan bog'lanadi.",
            reply_markup=pending_keyboard()
        )
    elif result == "already_registered":
        await update.message.reply_text(
            "Siz allaqachon imtihonga yozilgansiz. Admin tasdig'ini kuting.",
            reply_markup=pending_keyboard()
        )
    else:
        await update.message.reply_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


# ─── VAZIFALAR ────────────────────────────────────────────────────────────────

async def homework_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    student = sheets.find_student_by_telegram_id(user_id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    stage = sheets.get_student_stage(user_id)
    if stage == "bito":
        await homework_bito_list(update, context)
        return
    if stage == "bi":
        await homework_bi_list(update, context)
        return
    # fallback (should not happen for active students)
    await update.message.reply_text("Dasturingiz aniqlanmadi.", reply_markup=stage_keyboard(user_id))


async def _send_hw_file(update: Update, file_id: str, file_type: str, caption: str = "") -> None:
    """Send a stored homework file to the student."""
    kwargs = {"caption": caption[:1024], "parse_mode": "Markdown"} if caption else {}
    try:
        if file_type == "photo":
            await update.message.reply_photo(file_id, **kwargs)
        elif file_type == "video":
            await update.message.reply_video(file_id, **kwargs)
        elif file_type == "audio":
            await update.message.reply_audio(file_id, **kwargs)
        else:
            await update.message.reply_document(file_id, **kwargs)
    except Exception as e:
        logging.warning("_send_hw_file failed (file_id=%s): %s", file_id, e)
        if caption:
            await update.message.reply_text(caption, parse_mode="Markdown")


async def homework_bito_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    hws = sheets.get_homeworks_by_program("bito")
    rows = []
    for hw in sorted(hws, key=lambda h: str(h.get("HWID", ""))):
        key = str(hw.get("HWID", ""))
        title = str(hw.get("Title", key))
        subs = sheets.get_submissions(assignment_id=key, student_id=student["PersonalID"])
        status = "✅" if subs else "📤"
        rows.append([KeyboardButton(f"📚 {key}: {title} — {status}")])
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.HW_BITO_LIST
    await update.message.reply_text(
        "📊 BITO vazifalari:", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def homework_bi_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    hws = sheets.get_homeworks_by_program("bi")
    rows = []
    for hw in sorted(hws, key=lambda h: str(h.get("HWID", ""))):
        key = str(hw.get("HWID", ""))
        title = str(hw.get("Title", key))
        subs = sheets.get_submissions(assignment_id=key, student_id=student["PersonalID"])
        status = "✅" if subs else "📤"
        rows.append([KeyboardButton(f"📈 {key}: {title} — {status}")])
    rows.append([KeyboardButton("🔙 Orqaga")])
    context.user_data["state"] = S.HW_BI_LIST
    await update.message.reply_text(
        "📈 BI vazifalari:", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True)
    )


async def homework_bito_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, hw_key: str):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    hw_sheet = sheets.get_homework(hw_key)
    if not hw_sheet:
        await update.message.reply_text("Vazifa topilmadi.")
        return

    title = str(hw_sheet.get("Title", hw_key)).strip()
    description = str(hw_sheet.get("Description", "")).strip()

    subs = sheets.get_submissions(assignment_id=hw_key, student_id=student["PersonalID"])
    msg = f"📊 *{title}*\n\n{description}"

    file_ids = [f for f in str(hw_sheet.get("FileID", "")).strip().split("|") if f]
    file_types = str(hw_sheet.get("FileType", "")).strip().lower().split("|")
    while len(file_types) < len(file_ids):
        file_types.append("document")

    confirm_markup = ReplyKeyboardMarkup([
        [KeyboardButton("✅ Tasdiqlayman")],
        [KeyboardButton("🔙 Orqaga")],
    ], resize_keyboard=True)
    back_markup = ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)

    if subs:
        grade_info = subs[0].get("Grade") or "Hali baholanmagan"
        msg += f"\n\n✅ Topshirilgan\nBall: {grade_info}"
        context.user_data["state"] = S.HW_SUBMITTED
        if file_ids:
            await _send_hw_file(update, file_ids[0], file_types[0], caption=msg)
            for fid, ftype in zip(file_ids[1:], file_types[1:]):
                await _send_hw_file(update, fid, ftype)
            await update.message.reply_text("🔙", reply_markup=back_markup)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=back_markup)
    else:
        context.user_data["submitting_hw"] = hw_key
        context.user_data["hw_program"] = "bito"
        context.user_data["pending_files"] = []
        context.user_data["state"] = S.HW_UPLOADING
        if file_ids:
            await _send_hw_file(update, file_ids[0], file_types[0], caption=msg)
            for fid, ftype in zip(file_ids[1:], file_types[1:]):
                await _send_hw_file(update, fid, ftype)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        await update.message.reply_text(
            "📎 Fayl, rasm yoki matn yuboring (bir nechta bo'lsa birin-ketin).\nTayyor bo'lgach «✅ Tasdiqlayman» bosing.",
            reply_markup=confirm_markup,
        )


async def homework_bi_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, hw_key: str):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    hw_sheet = sheets.get_homework(hw_key)
    if not hw_sheet:
        await update.message.reply_text("Vazifa topilmadi.")
        return

    title = str(hw_sheet.get("Title", hw_key)).strip()
    description = str(hw_sheet.get("Description", "")).strip()

    subs = sheets.get_submissions(assignment_id=hw_key, student_id=student["PersonalID"])
    msg = f"📈 *{title}*\n\n{description}"
    back_markup = ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)

    file_ids = [f for f in str(hw_sheet.get("FileID", "")).strip().split("|") if f]
    file_types = str(hw_sheet.get("FileType", "")).strip().lower().split("|")
    while len(file_types) < len(file_ids):
        file_types.append("document")

    if subs:
        grade_info = subs[0].get("Grade") or "Hali baholanmagan"
        msg += f"\n\n✅ Topshirilgan\nBall: {grade_info}"
        context.user_data["state"] = S.HW_SUBMITTED
        if file_ids:
            await _send_hw_file(update, file_ids[0], file_types[0], caption=msg)
            for fid, ftype in zip(file_ids[1:], file_types[1:]):
                await _send_hw_file(update, fid, ftype)
            await update.message.reply_text("🔙", reply_markup=back_markup)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=back_markup)
    else:
        context.user_data["pbi_hw_key"] = hw_key
        context.user_data["hw_program"] = "bi"
        context.user_data["state"] = S.HW_BI_AWAITING_FILE
        if file_ids:
            await _send_hw_file(update, file_ids[0], file_types[0], caption=msg)
            for fid, ftype in zip(file_ids[1:], file_types[1:]):
                await _send_hw_file(update, fid, ftype)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        await update.message.reply_text(
            "📁 `.pbit` faylingizni yuboring.",
            parse_mode="Markdown",
            reply_markup=back_markup,
        )


# ─── BITO UPLOAD ──────────────────────────────────────────────────────────────

async def _collect_bito_text(update: Update, context: ContextTypes.DEFAULT_TYPE, files: list) -> str:
    """Download document submissions and pull their text out for AI grading."""
    parts = []
    for f in files:
        if f["file_type"] == "text" and f.get("content"):
            parts.append(f["content"])
        elif f["file_type"] == "document":
            fname = f.get("file_name") or "submission"
            local = f"temp_{update.effective_user.id}_{fname}"
            try:
                tg_file = await context.bot.get_file(f["file_id"])
                await tg_file.download_to_drive(local)
                txt = await asyncio.to_thread(extract_text, local)
                if txt.strip():
                    parts.append(f"=== {fname} ===\n{txt}")
            except Exception as e:
                logging.warning("BITO extract failed: %s", e)
            finally:
                if os.path.exists(local):
                    os.remove(local)
    return "\n\n".join(parts)

async def _extract_answer_file(context: ContextTypes.DEFAULT_TYPE, file_id: str, file_type: str, hw_id: str) -> str:
    """Download the admin-uploaded answer file and extract its text for AI grading."""
    if file_type not in ("document",):
        logging.warning("_extract_answer_file: unsupported type=%r hw=%s", file_type, hw_id)
        return ""
    local = None
    local_raw = None
    try:
        tg_file = await context.bot.get_file(file_id)
        ext = os.path.splitext(tg_file.file_path)[1].lower()
        if not ext:
            # Download without extension, detect format from magic bytes
            local_raw = f"temp_answer_{hw_id}.bin"
            await tg_file.download_to_drive(local_raw)
            ext = detect_ext(local_raw)
            if not ext:
                logging.warning("_extract_answer_file: could not detect format hw=%s path=%s", hw_id, tg_file.file_path)
                return ""
            local = f"temp_answer_{hw_id}{ext}"
            os.rename(local_raw, local)
            local_raw = None
        else:
            local = f"temp_answer_{hw_id}{ext}"
            await tg_file.download_to_drive(local)
        logging.info("_extract_answer_file: hw=%s ext=%s", hw_id, ext)
        txt = await asyncio.to_thread(extract_text, local)
        logging.info("_extract_answer_file: extracted %d chars from hw=%s", len(txt), hw_id)
        return txt.strip()
    except Exception as e:
        logging.warning("_extract_answer_file failed hw=%s: %s", hw_id, e)
        return ""
    finally:
        for f in (local, local_raw):
            if f and os.path.exists(f):
                os.remove(f)


async def handle_hw_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called from button_handler when state == hw_uploading"""
    text = update.message.text or ""
    if text == "✅ Tasdiqlayman":
        files = context.user_data.get("pending_files", [])
        if not files:
            await update.message.reply_text("⚠️ Hech narsa yuborilmagan.")
            return
        student = sheets.find_student_by_telegram_id(update.effective_user.id)
        if not student:
            await update.message.reply_text("⚠️ Xatolik: talaba topilmadi.")
            return
        hw_id = context.user_data.get("submitting_hw")
        sub_ids = []
        for f in files:
            sub_id = sheets.add_submission(student["PersonalID"], hw_id, f["content"], f["file_id"], f["file_type"])
            sub_ids.append(sub_id)
        context.user_data.pop("pending_files", None)
        context.user_data["state"] = S.MAIN
        logging.info("SUBMITTED: student_id=%s hw_id=%s files=%d", student["PersonalID"], hw_id, len(sub_ids))
        await update.message.reply_text(f"✅ Topshirildi! ({len(sub_ids)} ta fayl)",
            reply_markup=stage_keyboard(update.effective_user.id))

        hw_sheet = sheets.get_homework(hw_id)
        hw_title = str(hw_sheet.get("Title", hw_id) if hw_sheet else hw_id)
        answer_file_id = str(hw_sheet.get("AnswerFileID", "") if hw_sheet else "").strip()
        answer_file_type = str(hw_sheet.get("AnswerFileType", "") if hw_sheet else "").strip().lower()
        expected = await _extract_answer_file(context, answer_file_id, answer_file_type, hw_id) if answer_file_id else ""
        if answer_file_id and not expected:
            logging.warning("BITO answer extract returned empty: hw=%s file_id=%s type=%s", hw_id, answer_file_id, answer_file_type)
            await update.message.reply_text("⚠️ Javob faylini o'qib bo'lmadi. Ustoz qo'lda tekshiradi.")

        if expected:
            # #3: photos can't be read by AI — skip grading if no gradeable files
            has_gradeable = any(f["file_type"] in ("text", "document") for f in files)
            if not has_gradeable:
                await update.message.reply_text(
                    "📸 Fotosuratlar AI tomonidan tekshirilmaydi.\n"
                    "Iltimos, .docx, .pdf yoki .xlsx fayl yuboring. Ustoz qo'lda tekshiradi."
                )
            else:
                # #6: extract text before the try so it can be stored for retry without re-downloading
                student_text = await _collect_bito_text(update, context, files)
                status_msg = await update.message.reply_text("🤖 Teacher AI tekshirmoqda... iltimos kuting.")
                try:
                    if student_text.strip():
                        result = await asyncio.to_thread(
                            mentor.evaluate_text, student_text, expected, ""
                        )
                        score = result["score"]
                        feedback = result["feedback"]
                        sheets.upsert_grade(student["PersonalID"], hw_id, str(score), feedback[:300])  # #8
                        report = (
                            f"📊 {hw_title}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"💯 Ball: {score}/100\n\n"
                            f"📝 Fikr:\n\n{feedback}"
                        )
                        if len(report) > 4000:
                            await status_msg.edit_text(f"💯 Ball: {score}/100")
                            await update.message.reply_text(feedback[:4000])
                        else:
                            await status_msg.edit_text(report)
                    else:
                        await status_msg.edit_text("⚠️ Fayldan matn o'qib bo'lmadi. Ustoz qo'lda tekshiradi.")
                except Exception as e:
                    logging.error("BITO AI grading failed: hw=%s student=%s: %s", hw_id, student["PersonalID"], e)
                    context.user_data["ai_retry_text"] = student_text  # #6: store text, not file ids
                    context.user_data["ai_retry_hw"] = hw_id
                    context.user_data["state"] = S.HW_SUBMITTED
                    await status_msg.edit_text(_ai_err_msg(e))
                    await update.message.reply_text(
                        "🔄 Qayta urinish uchun tugmani bosing.",
                        reply_markup=ReplyKeyboardMarkup([
                            [KeyboardButton("🔄 Qayta baholash")],
                            [KeyboardButton("🔙 Orqaga")],
                        ], resize_keyboard=True)
                    )
                    # #7: tell admin so they know manual review may be needed
                    for admin_id in ADMIN_IDS:
                        try:
                            await context.bot.send_message(
                                admin_id,
                                f"⚠️ *{student['FullName']}* uchun AI baholash xato berdi!\n"
                                f"Vazifa: {hw_id}\nXato: {str(e)[:150]}\nQo'lda tekshirish kerak bo'lishi mumkin.",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id,
                    text=f"📥 *{student['FullName']}* vazifa topshirdi!\nVazifa: {hw_id}",
                    parse_mode="Markdown")
            except Exception:
                pass
    elif text == "🔙 Orqaga":
        context.user_data.pop("pending_files", None)
        context.user_data["state"] = S.MAIN
        await homework_menu(update, context)
    else:
        entry = {}
        if update.message.photo:
            entry = {"file_id": update.message.photo[-1].file_id, "file_type": "photo", "content": update.message.caption or ""}
        elif update.message.document:
            entry = {"file_id": update.message.document.file_id, "file_type": "document",
                     "file_name": update.message.document.file_name or "",
                     "content": update.message.caption or ""}
        elif update.message.text:
            entry = {"file_id": "", "file_type": "text", "content": update.message.text}
        if entry:
            context.user_data.setdefault("pending_files", []).append(entry)
            count = len(context.user_data["pending_files"])
            await update.message.reply_text(
                f"✅ {count} ta qabul qilindi. Yana yuborishingiz yoki «✅ Tasdiqlayman» bosing.",
                reply_markup=ReplyKeyboardMarkup([
                    [KeyboardButton("✅ Tasdiqlayman")],
                    [KeyboardButton("🔙 Orqaga")],
                ], resize_keyboard=True)
            )


# ─── BI FILE (PBI) ────────────────────────────────────────────────────────────

async def handle_bi_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called from button_handler when state == hw_bi_awaiting_file"""
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await homework_menu(update, context)
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ .pbit fayl yuboring.")
        return
    if not (document.file_name or "").endswith(".pbit"):
        await update.message.reply_text("❌ Faqat `.pbit` fayl qabul qilinadi.")
        return

    hw_key = context.user_data.get("pbi_hw_key")
    if not hw_key:
        await update.message.reply_text("Xato. Qaytadan vazifani tanlang.")
        context.user_data["state"] = S.MAIN
        return

    status_msg = await update.message.reply_text("📥 Fayl yuklanmoqda...")
    local_path = f"temp_{update.effective_user.id}_{document.file_name}"
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await status_msg.edit_text("⚠️ Xatolik: talaba topilmadi.")
        context.user_data["state"] = S.MAIN
        return

    ai_failed = False
    try:
        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(local_path)
        # Record submission before AI so deadline is preserved even if AI fails.
        # Only add on first attempt — retries reuse the existing row.
        existing_subs = sheets.get_submissions(assignment_id=hw_key, student_id=student["PersonalID"])
        if not existing_subs:
            sheets.add_submission(student["PersonalID"], hw_key, "", document.file_id, "pbit")
        await status_msg.edit_text("🤖 Teacher AI tahlil qilmoqda... iltimos kuting.")
        hw_sheet = sheets.get_homework(hw_key)
        hw_title = str(hw_sheet.get("Title", hw_key) if hw_sheet else hw_key)
        hw_description = str(hw_sheet.get("Description", "") if hw_sheet else "").strip()  # #1
        result = await asyncio.to_thread(
            mentor.evaluate_all,
            local_path,
            description=hw_description,  # #1: pass task description so AI knows what to grade
        )
        score = result["score"]
        feedback = result["feedback"]
        report = (
            f"📊 {hw_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💯 Ball: {score}/100\n\n"
            f"📝 Fikr:\n\n{feedback}"
        )
        if len(report) > 4000:
            await status_msg.edit_text(f"💯 *Ball: {score}/100*", parse_mode="Markdown")
            await update.message.reply_text(feedback[:4000])
        else:
            await status_msg.edit_text(report)

        sheets.upsert_grade(student["PersonalID"], hw_key, str(score), feedback[:300])  # #8

    except Exception as e:
        ai_failed = True
        logging.error("BI checker xato: %s", e)
        await status_msg.edit_text(_ai_err_msg(e))
        await update.message.reply_text(
            "Faylni qayta yuboring yoki keyinroq urinib ko'ring.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
        )
        # #7: notify admins so they know a manual review may be needed
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"⚠️ *{student['FullName']}* uchun BI AI baholash xato berdi!\n"
                    f"Vazifa: {hw_key}\nXato: {str(e)[:150]}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

    if not ai_failed:
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(update.effective_user.id))
    # else: state stays HW_BI_AWAITING_FILE so the student can send the file again


# ─── GRADES ───────────────────────────────────────────────────────────────────

async def grades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    grade_list = sheets.get_grades(student["PersonalID"])
    if not grade_list:
        await update.message.reply_text("Hozircha baholar yo'q.")
        return
    chunk = "🎓 Baholaringiz\n\n"
    for g in grade_list:
        entry = f"Vazifa: {g['AssignmentID']}\nBall: {g['Score']}\nIzoh: {g['Feedback']}\nSana: {g['Date']}\n\n"
        if len(chunk) + len(entry) > 4000:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += entry
    if chunk:
        await update.message.reply_text(chunk)


# ─── ASK QUESTION ─────────────────────────────────────────────────────────────


async def _send_qa_file(bot, chat_id: int, file_id: str, file_type: str, caption: str = "") -> None:
    """Forward a Q&A attachment (photo/document/video) to a chat."""
    try:
        if file_type == "photo":
            await bot.send_photo(chat_id, file_id, caption=caption or None)
        elif file_type == "video":
            await bot.send_video(chat_id, file_id, caption=caption or None)
        else:
            await bot.send_document(chat_id, file_id, caption=caption or None)
    except Exception as e:
        logging.warning("_send_qa_file failed (file_id=%s): %s", file_id, e)


async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Savolingizni yozing:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
    )
    return ASK_QUESTION


async def ask_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or update.message.caption or ""

    if text == "🔙 Orqaga":
        await update.message.reply_text("Bekor qilindi.", reply_markup=stage_keyboard(user_id))
        return ConversationHandler.END

    # Detect attached file
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

    # Require at least some text when there's no attachment, and cap length
    if not text.strip() and not file_id:
        await update.message.reply_text("❌ Savol matni yozing yoki fayl yuboring.")
        return ASK_QUESTION
    if len(text) > 1000:
        await update.message.reply_text("❌ Savol 1000 belgidan oshmasin. Qisqartiring.")
        return ASK_QUESTION

    now = time.time()
    if now - _ask_cooldown.get(user_id, 0) < _ASK_COOLDOWN_SECS:
        remaining = int(_ASK_COOLDOWN_SECS - (now - _ask_cooldown[user_id]))
        await update.message.reply_text(f"⏳ Iltimos {remaining} soniya kutib, keyin savol yuboring.")
        return ASK_QUESTION

    student = sheets.find_student_by_telegram_id(user_id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.", reply_markup=stage_keyboard(user_id))
        return ConversationHandler.END

    _ask_cooldown[user_id] = now
    q_id = sheets.ask_question(student["PersonalID"], text, file_id, file_type)
    logging.info("QUESTION_ASKED: student_id=%s q_id=%s", student["PersonalID"], q_id)
    await update.message.reply_text(
        f"Savol yuborildi! ID: `{q_id}`\nUstoz tez orada javob beradi.",
        parse_mode="Markdown", reply_markup=stage_keyboard(user_id)
    )
    unanswered_count = len(sheets.get_unanswered_questions())
    for admin_id in ADMIN_IDS:
        try:
            notify = (
                f"❓ *{student['FullName']}* dan yangi savol!\n\n"
                f"{text[:200]}\n\nJavobsiz: *{unanswered_count}*"
            )
            await context.bot.send_message(chat_id=admin_id, text=notify, parse_mode="Markdown")
            if file_id:
                await _send_qa_file(context.bot, admin_id, file_id, file_type)
        except Exception:
            pass
    return ConversationHandler.END


# ─── DEADLINES ────────────────────────────────────────────────────────────────

async def deadlines_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return

    student_id = student["PersonalID"]
    deadlines = sheets.get_deadline_tracking(student_id=student_id)

    if not deadlines:
        await update.message.reply_text(
            "📅 Siz uchun deadline belgilanmagan.\n"
            "(Deadline tizimi yangi qabul qilingan talabalar uchun ishlaydi)",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True)
        )
        context.user_data["state"] = S.DEADLINES_VIEW
        return

    now = sheets.now_uz()
    lines = ["📅 *Deadlinelar*\n"]
    excuse_needed = []

    for row in deadlines:
        hw_id = str(row.get("HWID", ""))
        deadline = sheets.parse_deadline_dt(row.get("Deadline"))
        if not deadline:
            continue

        subs = sheets.get_submissions(assignment_id=hw_id, student_id=student_id)
        excuse_status = str(row.get("ExcuseStatus", "")).strip().lower()
        miss_notified = str(row.get("MissNotified", "")).strip().upper() == "TRUE"

        if subs:
            status = "✅ Topshirilgan"
        elif deadline < now:
            if excuse_status == "uzrli":
                status = "📝 Uzrli"
            elif excuse_status == "sababsiz":
                status = "❌ Sababsiz"
            elif excuse_status == "pending":
                status = "⏳ Uzr ko'rib chiqilmoqda"
            elif miss_notified:
                status = "⚠️ O'tib ketdi — uzr yozing"
                excuse_needed.append(row)
            else:
                status = "⚠️ O'tib ketdi"
        else:
            time_left = deadline - now
            hours = int(time_left.total_seconds() / 3600)
            if hours >= 24:
                days = hours // 24
                hrs = hours % 24
                status = f"🕐 {days}k {hrs}s qoldi"
            else:
                status = f"🕐 {hours} soat qoldi"

        lines.append(
            f"• *{hw_id}*\n"
            f"  {deadline.strftime('%d.%m.%Y %H:%M')} | {status}"
        )

    msg = "\n\n".join(lines)
    context.user_data["state"] = S.DEADLINES_VIEW

    if excuse_needed:
        rows = [[KeyboardButton(f"📝 Uzr: {r['HWID']}")] for r in excuse_needed]
        rows.append([KeyboardButton("🔙 Orqaga")])
        await update.message.reply_text(
            msg + "\n\n_Uzr yozish uchun tanlang:_",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True),
        )
    else:
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )


# ─── MY HISTORY ───────────────────────────────────────────────────────────────

async def my_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not student:
        await update.message.reply_text("Avval ro'yxatdan o'ting.")
        return
    history = sheets.get_student_qa_history(student["PersonalID"])
    if not history:
        await update.message.reply_text("Hozircha savol berilmagan.")
        return
    chunk = "💬 Savol-javob tarixingiz\n\n"
    for q in history:
        entry = (
            f"ID: {q['QuestionID']} — {q['DateAsked']}\n"
            f"Savol: {q['Question']}\n"
            f"Javob: {q['Answer'] if q['Answer'] else 'Hali javob berilmagan'}\n\n"
        )
        if len(chunk) + len(entry) > 4000:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += entry
    if chunk:
        await update.message.reply_text(chunk)


# ─── STATE HANDLERS ───────────────────────────────────────────────────────────

async def _sh_hw_bito_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.effective_user.id
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(uid))
        return
    if text.startswith("📚 "):
        hw_key = text.split(" ")[1].rstrip(":")
        await homework_bito_detail(update, context, hw_key)


async def _sh_hw_bi_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.effective_user.id
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(uid))
        return
    if text.startswith("📈 "):
        hw_key = text.split(" ")[1].rstrip(":")
        await homework_bi_detail(update, context, hw_key)


async def _retry_bito_ai_grading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hw_id = context.user_data.get("ai_retry_hw")
    student_text = context.user_data.get("ai_retry_text", "")  # #6: use stored text, no re-download
    student = sheets.find_student_by_telegram_id(update.effective_user.id)
    if not hw_id or not student_text.strip() or not student:
        await update.message.reply_text("⚠️ Ma'lumot topilmadi. Vazifani qaytadan tanlang.", reply_markup=stage_keyboard(update.effective_user.id))
        context.user_data["state"] = S.MAIN
        return

    hw_sheet = sheets.get_homework(hw_id)
    hw_title = str(hw_sheet.get("Title", hw_id) if hw_sheet else hw_id)
    answer_file_id = str(hw_sheet.get("AnswerFileID", "") if hw_sheet else "").strip()
    answer_file_type = str(hw_sheet.get("AnswerFileType", "") if hw_sheet else "").strip().lower()
    expected = await _extract_answer_file(context, answer_file_id, answer_file_type, hw_id) if answer_file_id else ""
    if not expected:
        await update.message.reply_text("⚠️ Javob fayli o'qilmadi. Ustoz qo'lda tekshiradi.", reply_markup=stage_keyboard(update.effective_user.id))
        context.user_data.pop("ai_retry_text", None)
        context.user_data.pop("ai_retry_hw", None)
        context.user_data["state"] = S.MAIN
        return

    status_msg = await update.message.reply_text("🤖 Teacher AI tekshirmoqda... iltimos kuting.")
    try:
        result = await asyncio.to_thread(mentor.evaluate_text, student_text, expected, "")
        score = result["score"]
        feedback = result["feedback"]
        sheets.upsert_grade(student["PersonalID"], hw_id, str(score), feedback[:300])  # #8
        report = (
            f"📊 {hw_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💯 Ball: {score}/100\n\n"
            f"📝 Fikr:\n\n{feedback}"
        )
        if len(report) > 4000:
            await status_msg.edit_text(f"💯 Ball: {score}/100")
            await update.message.reply_text(feedback[:4000])
        else:
            await status_msg.edit_text(report)
        context.user_data.pop("ai_retry_text", None)
        context.user_data.pop("ai_retry_hw", None)
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(update.effective_user.id))
    except Exception as e:
        logging.error("BITO AI retry failed: hw=%s student=%s: %s", hw_id, student["PersonalID"], e)
        await status_msg.edit_text(_ai_err_msg(e))
        await update.message.reply_text(
            "Keyinroq qayta urinib ko'ring.",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("🔄 Qayta baholash")],
                [KeyboardButton("🔙 Orqaga")],
            ], resize_keyboard=True)
        )


async def _sh_hw_submitted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text == "🔙 Orqaga":
        context.user_data.pop("ai_retry_text", None)
        context.user_data.pop("ai_retry_hw", None)
        await homework_menu(update, context)
    elif text == "🔄 Qayta baholash":
        await _retry_bito_ai_grading(update, context)
    else:
        # Keyboard out of sync (e.g. state survived a bot restart but the retry
        # keyboard was never delivered). Re-send the correct keyboard so the
        # student isn't silently stuck.
        if context.user_data.get("ai_retry_text"):
            await update.message.reply_text(
                "🔄 Qayta urinish uchun tugmani bosing.",
                reply_markup=ReplyKeyboardMarkup([
                    [KeyboardButton("🔄 Qayta baholash")],
                    [KeyboardButton("🔙 Orqaga")],
                ], resize_keyboard=True),
            )
        else:
            await update.message.reply_text(
                "🔙 Orqaga qaytish uchun tugmani bosing.",
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True
                ),
            )


async def _sh_deadlines_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.effective_user.id
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(uid))
        return
    if text.startswith("📝 Uzr: "):
        hw_id = text[len("📝 Uzr: "):]
        hw = sheets.get_homework(hw_id)
        program = str(hw.get("Program", "")).strip().lower() if hw else ("bito" if hw_id.lower().startswith("bito") else "bi")
        context.user_data["excuse_hw_id"] = hw_id
        context.user_data["excuse_program"] = program
        context.user_data["state"] = S.EXCUSE_TYPING
        await update.message.reply_text(
            f"📝 *{hw_id}* uchun uzr sababingizni yozing:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Orqaga")]], resize_keyboard=True),
        )


async def _sh_excuse_typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.effective_user.id
    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Bekor qilindi.", reply_markup=stage_keyboard(uid))
        return
    hw_id = context.user_data.get("excuse_hw_id")
    program = context.user_data.get("excuse_program")
    student = sheets.find_student_by_telegram_id(uid)
    if hw_id and program and student:
        sheets.update_deadline_field(student["PersonalID"], program, hw_id, "ExcuseText", text[:500])
        sheets.update_deadline_field(student["PersonalID"], program, hw_id, "ExcuseStatus", "pending")
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"📝 Yangi uzr!\nTalaba: {student['FullName']} ({student['PersonalID']})\n"
                    f"Vazifa: {hw_id}\nMatn: {text[:300]}",
                )
            except Exception:
                pass
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("✅ Uzringiz yuborildi. Admin ko'rib chiqadi.", reply_markup=stage_keyboard(uid))
    else:
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("⚠️ Sessiya tugadi. Deadlinelar sahifasidan qaytadan urinib ko'ring.", reply_markup=stage_keyboard(uid))