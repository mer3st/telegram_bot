import logging
from core import sheets
from config import ADMIN_IDS


async def check_deadlines(context):
    """Job: runs every 15 min. Sends reminders and miss notifications."""
    try:
        tracking = sheets.get_deadline_tracking()
    except Exception as e:
        logging.error(f"check_deadlines: failed to load tracking: {e}")
        try:
            await context.bot.send_message(ADMIN_IDS[0], f"⚠️ check_deadlines ishlamadi: {e}")
        except Exception:
            pass
        return

    now = sheets.now_uz()
    any_update = False

    for row in tracking:
        student_id = str(row.get("StudentID", "")).strip()
        program = str(row.get("Program", "")).strip()
        hw_id = str(row.get("HWID", "")).strip()
        deadline = sheets.parse_deadline_dt(row.get("Deadline"))

        if not deadline or not student_id or not hw_id:
            continue

        student = sheets.find_student_by_id(student_id)
        if not student:
            continue
        try:
            tg_id = int(student["TelegramID"])
        except (ValueError, KeyError):
            continue

        time_left = deadline - now
        hours_left = time_left.total_seconds() / 3600

        rem12 = str(row.get("Rem12Sent", "")).strip().upper() == "TRUE"
        rem6 = str(row.get("Rem6Sent", "")).strip().upper() == "TRUE"
        rem2 = str(row.get("Rem2Sent", "")).strip().upper() == "TRUE"
        miss_notified = str(row.get("MissNotified", "")).strip().upper() == "TRUE"

        already_submitted = bool(sheets.get_submissions(assignment_id=hw_id, student_id=student_id))

        if hours_left > 0 and already_submitted:
            continue

        if hours_left > 0:
            # ── 12-hour reminder ──
            if hours_left <= 12 and not rem12:
                try:
                    await context.bot.send_message(
                        tg_id,
                        f"⏰ Eslatma: *{hw_id}* — {int(hours_left)} soatdan keyin deadline!\n"
                        f"Vaqt: {deadline.strftime('%d.%m %H:%M')} (UTC+5)",
                        parse_mode="Markdown",
                    )
                    sheets.update_deadline_field(student_id, program, hw_id, "Rem12Sent", "TRUE", bust_after=False)
                    any_update = True
                except Exception as e:
                    logging.warning(f"12h reminder failed for {student_id}/{hw_id}: {e}")

            # ── 6-hour reminder ──
            if hours_left <= 6 and not rem6:
                try:
                    await context.bot.send_message(
                        tg_id,
                        f"⏰ Eslatma: *{hw_id}* — {int(hours_left)} soat qoldi!\n"
                        f"Vaqt: {deadline.strftime('%d.%m %H:%M')} (UTC+5)",
                        parse_mode="Markdown",
                    )
                    sheets.update_deadline_field(student_id, program, hw_id, "Rem6Sent", "TRUE", bust_after=False)
                    any_update = True
                except Exception as e:
                    logging.warning(f"6h reminder failed for {student_id}/{hw_id}: {e}")

            # ── 2-hour reminder ──
            if hours_left <= 2 and not rem2:
                try:
                    await context.bot.send_message(
                        tg_id,
                        f"🚨 *{hw_id}* — atigi {int(hours_left * 60)} daqiqa qoldi!\n"
                        f"Vaqt: {deadline.strftime('%d.%m %H:%M')} (UTC+5)",
                        parse_mode="Markdown",
                    )
                    sheets.update_deadline_field(student_id, program, hw_id, "Rem2Sent", "TRUE", bust_after=False)
                    any_update = True
                except Exception as e:
                    logging.warning(f"2h reminder failed for {student_id}/{hw_id}: {e}")

        else:
            # ── Missed deadline ──
            if miss_notified:
                continue
            subs = sheets.get_submissions(assignment_id=hw_id, student_id=student_id)
            if subs:
                continue
            try:
                await context.bot.send_message(
                    tg_id,
                    f"⚠️ *{hw_id}* deadlini o'tib ketdi.\n\n"
                    "Sababsiz hisoblanmasligi uchun «📅 Deadlinelar» bo'limiga kiring va uzr yozing.",
                    parse_mode="Markdown",
                )
                sheets.update_deadline_field(student_id, program, hw_id, "MissNotified", "TRUE", bust_after=False)
                any_update = True
            except Exception as e:
                logging.warning(f"miss notification failed for {student_id}/{hw_id}: {e}")

    if any_update:
        sheets.bust("deadlines")
