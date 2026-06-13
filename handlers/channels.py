import time
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ApplicationHandlerStop

from core import sheets
from config import (
    is_admin, ADMIN_IDS,
    BITO_CHANNEL_ID, BI_CHANNEL_ID,
    BITO_CHANNEL_LINK, BI_CHANNEL_LINK,
    admin_contact,
)

_MEMBER_LEFT = {"left", "kicked", "restricted", "banned"}

logger = logging.getLogger(__name__)

# Statuses that count as "in the channel"
_MEMBER_OK = {"member", "administrator", "creator", "owner"}

# Cache positive membership briefly so we don't call get_chat_member on every tap.
# (user_id, channel_id) -> (is_member, timestamp). Negatives are NOT trusted from
# cache, so the moment a user joins, their next message lets them through.
_membership_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_MEMBERSHIP_TTL = 300  # 5 minutes

# Don't spam admins: notify at most once per user per cooldown.
_notified: dict[int, float] = {}
_NOTIFY_COOLDOWN = 3600  # 1 hour


_ALLOWED_FOR_UNKNOWN = {
    "/start", "/help", "/register", "/myid", "/reset",
    "📝 Ro'yxatdan o'tish", "ℹ️ Yordam",
}

_ALLOWED_FOR_PENDING = {
    "/start", "/help", "/myid", "/reset",
    "📋 Imtihonga yozilish", "ℹ️ Yordam",
    "❓ Savol berish", "💬 Savollarim tarixi",
    "✅ Ha, yozilaman", "🔙 Orqaga",
}


async def _is_member(bot, user_id: int, channel_id: int) -> bool:
    now = time.time()
    cached = _membership_cache.get((user_id, channel_id))
    if cached and cached[0] and now - cached[1] < _MEMBERSHIP_TTL:
        return True
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        ok = member.status in _MEMBER_OK
    except Exception as e:
        # Bot not admin in the channel, or user unknown to Telegram, etc.
        logger.warning("get_chat_member failed (user=%s channel=%s): %s",
                       user_id, channel_id, e)
        ok = False
    _membership_cache[(user_id, channel_id)] = (ok, now)
    return ok


async def _send_blocked_message(update: Update, missing):
    lines = ["🔒 Botdan foydalanish uchun avval quyidagi kanal(lar)ga a'zo bo'ling:\n"]
    buttons = []
    for _key, _cid, link, label in missing:
        if link:
            lines.append(f"• {label} kanal: {link}")
            buttons.append([InlineKeyboardButton(f"➡️ {label} kanalga o'tish", url=link)])
        else:
            lines.append(f"• {label} kanal")
    lines.append("\nA'zo bo'lgandan so'ng adminning tasdig'ini kuting. "
                 "Admin xabardor qilindi. ✅")
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    try:
        await update.effective_message.reply_text(
            "\n".join(lines), reply_markup=markup, disable_web_page_preview=True
        )
    except Exception as e:
        logger.warning("Could not send blocked message to %s: %s",
                       update.effective_user.id, e)


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, user, missing):
    now = time.time()
    if now - _notified.get(user.id, 0) < _NOTIFY_COOLDOWN:
        return
    _notified[user.id] = now

    labels = ", ".join(label for *_, label in missing)
    uname = f"@{user.username}" if user.username else "(username yo'q)"
    text = (
        "🔔 A'zolik kutilmoqda\n\n"
        f"Foydalanuvchi: {user.full_name} {uname}\n"
        f"Telegram ID: {user.id}\n"
        f"A'zo emas: {labels}\n\n"
        "Iltimos, uning so'rovini tasdiqlang yoki kanalga qo'shing."
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except Exception:
            pass


async def channel_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Global pre-handler (group=-1). Routes access based on student stage:
      unknown  → only /start and registration allowed
      pending  → only exam sign-up allowed
      bito     → must be in BITO channel
      bi       → must be in BITO + BI channels
    """
    if update.effective_message is None or update.effective_user is None:
        return

    # Evict _notified entries older than 24h to prevent unbounded growth
    now_t = time.time()
    stale = [uid for uid, ts in _notified.items() if now_t - ts > 86400]
    for uid in stale:
        del _notified[uid]

    user = update.effective_user

    if is_admin(user.id):
        return

    if str(user.id) in sheets.get_blacklist():
        try:
            contact = admin_contact()
            msg = "🚫 Siz botdan foydalanish huquqiga ega emassiz."
            if contact:
                msg += f"\nSabab yoki yordam uchun: {contact}"
            await update.effective_message.reply_text(msg)
        except Exception:
            pass
        raise ApplicationHandlerStop

    stage = sheets.get_student_stage(user.id)

    if stage == "unknown":
        msg_text = (update.effective_message.text or "").strip()
        cmd = msg_text.split()[0].lower() if msg_text.startswith("/") else ""
        if msg_text in _ALLOWED_FOR_UNKNOWN or cmd in {"/start", "/help", "/register"}:
            return
        await update.effective_message.reply_text(
            "Botdan foydalanish uchun avval ro'yxatdan o'ting.\n"
            "«📝 Ro'yxatdan o'tish» tugmasini bosing."
        )
        raise ApplicationHandlerStop

    if stage == "pending":
        msg_text = (update.effective_message.text or "").strip()
        cmd = msg_text.split()[0].lower() if msg_text.startswith("/") else ""
        if msg_text in _ALLOWED_FOR_PENDING or cmd in {"/start", "/help"}:
            return
        await update.effective_message.reply_text(
            "Siz imtihon kutish bosqichasiz.\n"
            "Imtihonga yozilish uchun «📋 Imtihonga yozilish» tugmasini bosing."
        )
        raise ApplicationHandlerStop

    # bito or bi — check channel membership
    needed = []
    if BITO_CHANNEL_ID:
        needed.append(("bito", BITO_CHANNEL_ID, BITO_CHANNEL_LINK, "BITO"))
    if stage == "bi" and BI_CHANNEL_ID:
        needed.append(("bi", BI_CHANNEL_ID, BI_CHANNEL_LINK, "BI"))

    if not needed:
        # Channels not configured: no blocking, but still init deadlines on first visit.
        _maybe_init_deadlines(sheets.find_student_by_telegram_id(user.id), stage)
        return

    missing = []
    for entry in needed:
        _key, channel_id, *_ = entry
        if not await _is_member(context.bot, user.id, channel_id):
            missing.append(entry)

    if not missing:
        # All required channels confirmed: init deadlines on first join.
        _maybe_init_deadlines(sheets.find_student_by_telegram_id(user.id), stage)
        return

    await _send_blocked_message(update, missing)
    await _notify_admins(context, user, missing)
    raise ApplicationHandlerStop


def _maybe_init_deadlines(student, stage: str) -> None:
    """Create deadline rows the first time a student is confirmed in their channel."""
    if not student:
        return
    pid = str(student["PersonalID"])
    if stage == "bito" and not str(student.get("BitoJoinDate", "")).strip():
        try:
            sheets.set_bito_join_date(pid)
            sheets.create_bito_deadlines(pid)
        except Exception as e:
            logger.error("Failed to init bito deadlines for %s: %s", pid, e)
    elif stage == "bi" and not str(student.get("BiJoinDate", "")).strip():
        try:
            sheets.set_bi_join_date(pid)
            sheets.create_bi_deadlines(pid)
        except Exception as e:
            logger.error("Failed to init bi deadlines for %s: %s", pid, e)


async def handle_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fires on chat_member updates in BITO/BI channels (bot must be admin there).
    - Join: initialises deadlines immediately when student enters channel.
    - Leave/kick: notifies all admins.
    """
    cmu = update.chat_member
    if not cmu:
        return

    channel_id = cmu.chat.id
    if channel_id not in (BITO_CHANNEL_ID, BI_CHANNEL_ID):
        return

    user = cmu.new_chat_member.user
    old_ok = cmu.old_chat_member.status in _MEMBER_OK
    new_ok = cmu.new_chat_member.status in _MEMBER_OK

    student = sheets.find_student_by_telegram_id(user.id)

    if not old_ok and new_ok:
        # Student just joined the channel.
        if not student:
            return
        stage = str(student.get("Stage", "")).strip().lower()
        if channel_id == BITO_CHANNEL_ID and stage == "bito":
            _maybe_init_deadlines(student, "bito")
        elif channel_id == BI_CHANNEL_ID and stage == "bi":
            _maybe_init_deadlines(student, "bi")

    elif old_ok and not new_ok:
        # Student left or was removed — notify admins.
        if not student:
            return
        stage = str(student.get("Stage", "")).strip().lower()
        if stage not in ("bito", "bi"):
            return
        channel_label = "BITO" if channel_id == BITO_CHANNEL_ID else "BI"
        uname = f"@{user.username}" if user.username else "(username yo'q)"
        text = (
            "⚠️ Talaba kanaldan chiqib ketdi\n\n"
            f"Ism: {user.full_name} {uname}\n"
            f"Telegram ID: {user.id}\n"
            f"Kanal: {channel_label}\n"
            f"Bosqich: {stage.upper()}"
        )
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, text)
            except Exception:
                pass