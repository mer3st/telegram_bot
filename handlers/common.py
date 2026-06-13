import logging
import traceback
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from core import sheets
from config import make_code, is_admin, REG_CODES, ADMIN_IDS
from core.keyboards import student_keyboard, admin_keyboard, pending_keyboard, unknown_keyboard


def student_contact_link(student: dict) -> str:
    """Return '@username' if set, else a clickable tg:// profile link using first name."""
    username = str(student.get("Telegram_username", "") or "").strip()
    if username:
        return f"@{username}"
    tg_id = str(student.get("TelegramID", "") or "").strip()
    name = str(student.get("FullName", "talaba") or "talaba").strip()
    if tg_id:
        return f"[{name}](tg://user?id={tg_id})"
    return name


def stage_keyboard(user_id: int):
    if is_admin(user_id):
        return admin_keyboard()
    stage = sheets.get_student_stage(user_id)
    if stage == "pending":
        return pending_keyboard()
    if stage in ("bito", "bi"):
        return student_keyboard()
    return unknown_keyboard()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.pop("state", None)
    student = sheets.find_student_by_telegram_id(user_id)
    kb = stage_keyboard(user_id)
    if student:
        await update.message.reply_text(
            f"👋 Xush kelibsiz, *{student['FullName']}*!\n"
            f"Shaxsiy ID: `{student['PersonalID']}`",
            parse_mode="Markdown", reply_markup=kb
        )
    else:
        code = make_code(user_id)
        REG_CODES[code] = user_id
        sheets.save_code(code, user_id)
        await update.message.reply_text(
            "👋 Assalomu alaykum! *BITO&BI* dastur botiga xush kelibsiz!\n\n"
            "Ro'yxatdan o'tish uchun «📝 Ro'yxatdan o'tish» tugmasini bosing.",
            parse_mode="Markdown", reply_markup=kb
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kb = stage_keyboard(user_id)
    stage = sheets.get_student_stage(user_id)
    if is_admin(user_id):
        text = (
            "Admin buyruqlari\n\n"
            "👥 Barcha talabalar — ro'yxat\n"
            "🎓 Baholar — ko'rish (talaba/vazifa bo'yicha)\n"
            "⭐ Baho qo'shish — talaba tanlash → ball\n"
            "📢 Xabar yuborish — hammaga broadcast\n"
            "🔍 Savollar — javobsiz savollar\n"
            "/refresh — cache tozalash"
        )
    elif stage == "pending":
        text = (
            "Siz hozir imtihon kutish bosqichasiz.\n\n"
            "📋 Imtihonga yozilish — imtihon uchun ro'yxatdan o'ting\n"
            "❓ Savol berish — ustozga savol yuboring\n"
            "💬 Savollarim tarixi — savol-javoblaringiz"
        )
    else:
        text = (
            "Talaba buyruqlari\n\n"
            "📚 Vazifalar — BITO yoki BI vazifalar\n"
            "🎓 Baholar — baholaringiz\n"
            "📅 Deadlinelar — deadline va uzrlar\n"
            "❓ Savol berish — ustozga savol\n"
            "💬 Savollarim tarixi — savol-javoblar"
        )
    await update.message.reply_text(text, reply_markup=kb)


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    student = sheets.find_student_by_telegram_id(user_id)
    if not student:
        await update.message.reply_text(
            f"Telegram ID: `{user_id}`\nBotda ro'yxatdan o'tilmagan.",
            parse_mode="Markdown"
        )
        return
    stage = sheets.get_student_stage(user_id)
    bot_state = context.user_data.get("state", "main")
    await update.message.reply_text(
        f"👤 *Sizning ma'lumotlaringiz*\n\n"
        f"Shaxsiy ID: `{student['PersonalID']}`\n"
        f"Ism: {student['FullName']}\n"
        f"Bosqich: `{stage}`\n"
        f"Bot holati: `{bot_state}`\n"
        f"Telegram ID: `{user_id}`",
        parse_mode="Markdown",
        reply_markup=stage_keyboard(user_id)
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear any stuck conversation state and return to main menu."""
    context.user_data.clear()
    await update.message.reply_text(
        "🔄 Bot holati tozalandi. Asosiy menyu:",
        reply_markup=stage_keyboard(update.effective_user.id)
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = stage_keyboard(update.effective_user.id)
    await update.message.reply_text("Bekor qilindi.", reply_markup=kb)
    return ConversationHandler.END


async def refresh_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Faqat admin uchun.")
        return
    sheets.bust_all()
    await update.message.reply_text("✅ Cache tozalandi!")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Update handling error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Xatolik yuz berdi. Birozdan keyin qaytadan urinib ko'ring."
            )
    except Exception:
        pass
    try:
        error_type = type(context.error).__name__
        error_msg = str(context.error)[:500]
        for admin_id in ADMIN_IDS:
            await context.bot.send_message(admin_id, f"⚠️ Bot xatosi: {error_type}: {error_msg}")
    except Exception:
        pass
