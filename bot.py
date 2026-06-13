import logging
import os
import stat
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes, TypeHandler,
    PicklePersistence, ChatMemberHandler,
)

from core import sheets
import states as S
from config import BOT_TOKEN, ADMIN_IDS, is_admin, validate_env, REG_CODES
from core.keyboards import student_keyboard, admin_keyboard, pending_keyboard, unknown_keyboard
from handlers.common import (
    start, help_command, cancel, refresh_cache, error_handler,
    stage_keyboard, myid_command, reset_command,
)
from handlers.student import (
    register_start,
    homework_menu,
    homework_bito_detail, homework_bi_detail,
    handle_hw_upload, handle_bi_file,
    grades, ask_start, ask_receive, my_history,
    exam_registration_handler, exam_confirm_handler,
    deadlines_view,
    ASK_QUESTION,
    _sh_hw_bito_list, _sh_hw_bi_list, _sh_hw_submitted,
    _sh_deadlines_view, _sh_excuse_typing,
)
from handlers.admin import (
    admin_students, admin_grades_menu, admin_grades_by_student, admin_grades_by_homework,
    admin_grades_show_student, admin_grades_show_hw,
    add_grade_start, add_grade_score, add_grade_feedback,
    broadcast_start, broadcast_target, broadcast_pick_student, broadcast_send,
    admin_questions, admin_excuses,
    promote_to_bi, demote_to_bito,
    approve_student_cmd, exam_list,
    approve_pick_student,
    promote_pick_student, demote_pick_student,
    expel_student_cmd, unexpel_student_cmd, expel_pick_student, unexpel_pick_student,
    checkgrade, setgrade,
    ADMIN_GRADE_SCORE, ADMIN_GRADE_FEEDBACK, BROADCAST_MSG, BROADCAST_TARGET, BROADCAST_PICK_STUDENT,
    _sh_admin_grades_menu, _sh_admin_grades_pick_student, _sh_admin_grades_pick_hw,
    _sh_questions_list, _sh_question_detail_list,
    _sh_admin_approve_pick, _sh_admin_promote_pick, _sh_admin_demote_pick,
    _sh_admin_expel_pick, _sh_admin_unexpel_pick,
    _sh_admin_excuse_list, _sh_admin_excuse_review,
    _handle_waiting_answer,
    set_hw_file_start,
    _sh_admin_hw_program_pick, _sh_admin_hw_list, _sh_admin_hw_detail,
    _sh_admin_hw_new_title, _sh_admin_hw_new_desc, _sh_admin_hw_new_days,
    _sh_admin_hw_delete_pick,
    _sh_admin_hw_file_pick, _sh_admin_hw_file_upload, _sh_admin_hw_answer_upload,
)
from core.deadline_checker import check_deadlines
from core.grade_watcher import watch_grade_changes
from core.sheets import backup_to_csv


async def _run_backup(context):
    backup_to_csv()
from handlers.form_processor import process_form_responses
from handlers.channels import channel_gate, handle_channel_member_update

logging.basicConfig(level=logging.INFO)

# Maps state → handler function. Adding a new state requires one entry here.
_STATE_DISPATCH: dict = {
    S.HW_BITO_LIST:              _sh_hw_bito_list,
    S.HW_BI_LIST:                _sh_hw_bi_list,
    S.HW_UPLOADING:              handle_hw_upload,
    S.HW_SUBMITTED:              _sh_hw_submitted,
    S.HW_BI_AWAITING_FILE:       handle_bi_file,
    S.ADMIN_GRADES_MENU:         _sh_admin_grades_menu,
    S.ADMIN_GRADES_PICK_STUDENT: _sh_admin_grades_pick_student,
    S.ADMIN_GRADES_PICK_HW:      _sh_admin_grades_pick_hw,
    S.QUESTIONS_LIST:            _sh_questions_list,
    S.QUESTION_DETAIL_LIST:      _sh_question_detail_list,
    S.EXAM_CONFIRM:              exam_confirm_handler,
    S.ADMIN_APPROVE_PICK:        _sh_admin_approve_pick,
    S.ADMIN_PROMOTE_PICK:        _sh_admin_promote_pick,
    S.ADMIN_DEMOTE_PICK:         _sh_admin_demote_pick,
    S.ADMIN_EXPEL_PICK:          _sh_admin_expel_pick,
    S.ADMIN_UNEXPEL_PICK:        _sh_admin_unexpel_pick,
    S.DEADLINES_VIEW:            _sh_deadlines_view,
    S.EXCUSE_TYPING:             _sh_excuse_typing,
    S.ADMIN_EXCUSE_LIST:         _sh_admin_excuse_list,
    S.ADMIN_EXCUSE_REVIEW:       _sh_admin_excuse_review,
    S.ADMIN_HW_PROGRAM_PICK:     _sh_admin_hw_program_pick,
    S.ADMIN_HW_LIST:             _sh_admin_hw_list,
    S.ADMIN_HW_DETAIL:           _sh_admin_hw_detail,
    S.ADMIN_HW_NEW_TITLE:        _sh_admin_hw_new_title,
    S.ADMIN_HW_NEW_DESC:         _sh_admin_hw_new_desc,
    S.ADMIN_HW_NEW_DAYS:         _sh_admin_hw_new_days,
    S.ADMIN_HW_DELETE_PICK:      _sh_admin_hw_delete_pick,
    S.ADMIN_HW_FILE_PICK:        _sh_admin_hw_file_pick,
    S.ADMIN_HW_FILE_UPLOAD:      _sh_admin_hw_file_upload,
    S.ADMIN_HW_ANSWER_UPLOAD:    _sh_admin_hw_answer_upload,
}

# Maps main-menu button text → handler. Role-dependent buttons handled separately.
_MENU_DISPATCH: dict = {
    "📋 Imtihonga yozilish": exam_registration_handler,
    "📝 Ro'yxatdan o'tish":  register_start,
    "📚 Vazifalar":          homework_menu,
    "❓ Savol berish":        ask_start,
    "💬 Savollarim tarixi":  my_history,
    "ℹ️ Yordam":             help_command,
    "👥 Barcha talabalar":   admin_students,
    "⭐ Baho qo'shish":      add_grade_start,
    "📢 Xabar yuborish":     broadcast_start,
    "🔍 Savollar":           admin_questions,
    "⬆️ Promote":            promote_pick_student,
    "⬇️ Demote":             demote_pick_student,
    "📅 Deadlinelar":        deadlines_view,
    "📋 Uzrlar":             admin_excuses,
    "📎 Vazifa fayli":       set_hw_file_start,
    "✅ Tasdiqlash":         approve_pick_student,
    "📋 Imtihon ro'yxati":   exam_list,
    "🚫 Expel":              expel_pick_student,
    "✅ Unexpel":            unexpel_pick_student,
}


# ─── BUTTON HANDLER ───────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    state = context.user_data.get("state", S.MAIN)
    user_id = update.effective_user.id

    state_fn = _STATE_DISPATCH.get(state)
    if state_fn:
        await state_fn(update, context)
        return

    if text == "🔙 Orqaga":
        context.user_data["state"] = S.MAIN
        await update.message.reply_text("Asosiy menyu:", reply_markup=stage_keyboard(user_id))
        return

    if context.user_data.get("waiting_for_answer"):
        await _handle_waiting_answer(update, context)
        return

    if text == "🎓 Baholar":
        if is_admin(user_id):
            await admin_grades_menu(update, context)
        else:
            await grades(update, context)
        return

    if text == "🔄 Refresh":
        sheets.bust_all()
        await update.message.reply_text("✅ Cache tozalandi!", reply_markup=admin_keyboard())
        return

    menu_fn = _MENU_DISPATCH.get(text)
    if menu_fn:
        await menu_fn(update, context)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    validate_env()

    loaded = sheets.load_all_codes()
    REG_CODES.update(loaded)

    persistence = PicklePersistence(filepath="bot_data")
    try:
        os.chmod("bot_data", stat.S_IRUSR | stat.S_IWUSR)  # 600: owner read/write only
    except (FileNotFoundError, NotImplementedError):
        pass  # first run (file created on first save) or Windows

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    application.job_queue.run_repeating(process_form_responses, interval=30, first=10)
    application.job_queue.run_repeating(check_deadlines, interval=900, first=30)
    application.job_queue.run_repeating(watch_grade_changes, interval=120, first=60)
    application.job_queue.run_repeating(_run_backup, interval=86400, first=300)  # daily

    application.add_handler(TypeHandler(Update, channel_gate), group=-1)
    application.add_handler(ChatMemberHandler(handle_channel_member_update, ChatMemberHandler.CHAT_MEMBER))

    reg_conv = ConversationHandler(
        entry_points=[
            CommandHandler("register", register_start),
            MessageHandler(filters.Regex("^📝 Ro'yxatdan o'tish$"), register_start),
        ],
        states={},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    ask_conv = ConversationHandler(
        entry_points=[
            CommandHandler("ask", ask_start),
            MessageHandler(filters.Regex("^❓ Savol berish$"), ask_start),
        ],
        states={
            ASK_QUESTION: [MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND,
                ask_receive,
            )],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    grade_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add_grade", add_grade_start),
            MessageHandler(filters.Regex("^⭐ Baho qo'shish$"), add_grade_start),
        ],
        states={
            ADMIN_GRADE_SCORE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_grade_score)],
            ADMIN_GRADE_FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_grade_feedback)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    broadcast_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", broadcast_start),
            MessageHandler(filters.Regex("^📢 Xabar yuborish$"), broadcast_start),
        ],
        states={
            BROADCAST_TARGET:       [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_target)],
            BROADCAST_PICK_STUDENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_pick_student)],
            BROADCAST_MSG:          [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("refresh", refresh_cache))
    application.add_handler(CommandHandler("grades", grades))
    application.add_handler(CommandHandler("students", admin_students))
    application.add_handler(CommandHandler("questions", admin_questions))
    application.add_handler(CommandHandler("promote", promote_to_bi))
    application.add_handler(CommandHandler("demote", demote_to_bito))
    application.add_handler(CommandHandler("approve", approve_student_cmd))
    application.add_handler(CommandHandler("exam_list", exam_list))
    application.add_handler(CommandHandler("expel", expel_student_cmd))
    application.add_handler(CommandHandler("unexpel", unexpel_student_cmd))
    application.add_handler(CommandHandler("checkgrade", checkgrade))
    application.add_handler(CommandHandler("setgrade", setgrade))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("reset", reset_command))

    application.add_handler(reg_conv)
    application.add_handler(ask_conv)
    application.add_handler(grade_conv)
    application.add_handler(broadcast_conv)

    application.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND,
        button_handler
    ))
    application.add_error_handler(error_handler)
    print("Bot ishga tushdi...")
    application.run_polling(allowed_updates=["message", "chat_member"])


if __name__ == "__main__":
    main()
