from telegram import ReplyKeyboardMarkup, KeyboardButton


def student_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("📚 Vazifalar"), KeyboardButton("🎓 Baholar")],
        [KeyboardButton("❓ Savol berish"), KeyboardButton("💬 Savollarim tarixi")],
        [KeyboardButton("📅 Deadlinelar"), KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True)


def unknown_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("📝 Ro'yxatdan o'tish")],
        [KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True)


def pending_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 Imtihonga yozilish")],
        [KeyboardButton("❓ Savol berish"), KeyboardButton("💬 Savollarim tarixi")],
        [KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True)


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("👥 Barcha talabalar"), KeyboardButton("🎓 Baholar")],
        [KeyboardButton("⭐ Baho qo'shish"), KeyboardButton("📢 Xabar yuborish")],
        [KeyboardButton("🔍 Savollar"), KeyboardButton("📋 Uzrlar")],
        [KeyboardButton("✅ Tasdiqlash"), KeyboardButton("📋 Imtihon ro'yxati")],
        [KeyboardButton("⬆️ Promote"), KeyboardButton("⬇️ Demote")],
        [KeyboardButton("🚫 Expel"), KeyboardButton("✅ Unexpel")],
        [KeyboardButton("📎 Vazifa fayli"), KeyboardButton("🔄 Refresh")],
    ], resize_keyboard=True)