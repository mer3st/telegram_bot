import logging
from core import sheets
from core.sheets import bust
from config import REG_CODES
from telegram.ext import ContextTypes
from core.keyboards import pending_keyboard


async def process_form_responses(context: ContextTypes.DEFAULT_TYPE):
    bust("form_responses")  # always read fresh — no stale cache between job runs
    responses = sheets.get_form_responses()
    students = sheets.get_all_students()
    existing_telegram_ids = {str(s["TelegramID"]) for s in students}
    sheet_codes = sheets.load_all_codes()

    for i, r in enumerate(responses):
        code = str(r.get("Code", "")).strip()
        if str(r.get("PersonalID", "")).strip():
            continue
        telegram_id = REG_CODES.get(code) or sheet_codes.get(code)
        if not telegram_id or str(telegram_id) in existing_telegram_ids:
            continue

        full_name  = str(r.get("To'liq ism familiya (Passportdagidek)", "") or "").strip()
        phone      = str(r.get("Telefon nomer", "") or "").strip()
        university = str(r.get("Qaysi universitetni bitirgansiz?", "") or "").strip()
        yosh       = str(r.get("Yosh", "") or "").strip()
        username = ""
        try:
            chat = await context.bot.get_chat(telegram_id)
            username = chat.username or ""
        except Exception:
            pass

        personal_id, status = sheets.register_student(telegram_id, username, full_name, phone, university, yosh)
        if status == "success":
            logging.info("REGISTERED: telegram_id=%s personal_id=%s name=%s", telegram_id, personal_id, full_name)
            sheets.claim_form_response(i + 2, personal_id, telegram_id)
            REG_CODES.pop(code, None)
            sheets.delete_code(code)
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        f"✅ Ro'yxatdan o'tish muvaffaqiyatli!\n\n"
                        f"Shaxsiy ID: `{personal_id}`\n"
                        f"Ism: {full_name}\n\n"
                        "Shaxsiy IDingizni saqlang!\n\n"
                        "Endi imtihonga yozilish uchun «📋 Imtihonga yozilish» "
                        "tugmasini bosing."
                    ),
                    parse_mode="Markdown",
                    reply_markup=pending_keyboard()
                )
            except Exception:
                pass