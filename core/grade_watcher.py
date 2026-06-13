import logging
from telegram.ext import ContextTypes
from core import sheets

logger = logging.getLogger(__name__)

_SNAPSHOT_KEY = "grade_watcher_snapshot"


async def watch_grade_changes(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 2 minutes. Detects when a grade score changes in the Grades sheet
    (e.g. admin edited directly) and notifies the student.

    Bot-initiated changes (via upsert_grade) are suppressed via consume_bot_graded()
    so students don't get a double notification from both the grading handler and here.
    """
    sheets.bust("grades")
    current_grades = sheets.get_grades()

    # Build snapshot: {student_id: {hw_id: score}}
    current: dict[str, dict[str, str]] = {}
    for g in current_grades:
        sid = str(g.get("StudentID", ""))
        hwid = str(g.get("AssignmentID", ""))
        score = str(g.get("Score", ""))
        if sid and hwid:
            current.setdefault(sid, {})[hwid] = score

    bot_graded = sheets.consume_bot_graded()
    snapshot: dict[str, dict[str, str]] = context.bot_data.get(_SNAPSHOT_KEY, {})

    if snapshot:
        for sid, hw_scores in current.items():
            prev_hw = snapshot.get(sid, {})
            for hwid, score in hw_scores.items():
                prev_score = prev_hw.get(hwid)
                if prev_score is None:
                    continue  # new grade — the grading handler already notified
                if prev_score == score:
                    continue  # unchanged
                if (sid, hwid, score) in bot_graded:
                    continue  # bot just wrote this — handler already sent notification

                # Admin changed the grade directly in the sheet → notify student
                student = sheets.find_student_by_id(sid)
                if not student:
                    continue
                hw = sheets.get_homework(hwid)
                hw_title = str(hw.get("Title", hwid)) if hw else hwid
                try:
                    await context.bot.send_message(
                        int(student["TelegramID"]),
                        f"📊 *Balingiz yangilandi!*\n\n"
                        f"Vazifa: {hwid} — {hw_title}\n"
                        f"Avvalgi ball: {prev_score}\n"
                        f"Yangi ball: *{score}*\n\n"
                        "Ustoz ishingizni ko'rib chiqib, ballingizni o'zgartirdi.",
                        parse_mode="Markdown",
                    )
                    logger.info(
                        "Grade change notified: student=%s hw=%s %s→%s",
                        sid, hwid, prev_score, score,
                    )
                except Exception as e:
                    logger.warning(
                        "Grade change notify failed: student=%s hw=%s: %s", sid, hwid, e
                    )

    context.bot_data[_SNAPSHOT_KEY] = current
