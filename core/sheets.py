import os, json, time, logging, uuid, csv
import gspread
import gspread.exceptions
import gspread.utils
import requests.exceptions
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from tenacity import Retrying, stop_after_attempt, wait_exponential, retry_if_exception

load_dotenv()

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

TZ_UZ = timezone(timedelta(hours=5))


def now_uz():
    return datetime.now(TZ_UZ)


def parse_deadline_dt(s):
    if not s:
        return None
    try:
        dt = datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=TZ_UZ)
    except ValueError:
        return None


def _stage(s):
    return str(s.get("Stage", "")).strip().lower()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# ─── RETRY ────────────────────────────────────────────────────────────────────

def _is_retryable(exc):
    if isinstance(exc, gspread.exceptions.APIError):
        try:
            return exc.response.status_code in (429, 500, 502, 503, 504)
        except AttributeError:
            return True
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


_retrying = Retrying(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)


def _call(fn):
    """Execute a gspread API call with exponential backoff on transient errors."""
    return _retrying(fn)


# ─── CACHE ────────────────────────────────────────────────────────────────────

_cache = {}
CACHE_TTL = 60


def cached(key, fn):
    now = time.time()
    if key in _cache and now - _cache[key]["t"] < CACHE_TTL:
        return _cache[key]["v"]
    result = _call(fn)
    _cache[key] = {"v": result, "t": now}
    return result


def bust(key):
    _cache.pop(key, None)


def bust_all():
    _cache.clear()


# ─── SHEET CONNECTION ─────────────────────────────────────────────────────────

_spreadsheet = None

def get_sheet():
    global _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet

    def _connect():
        raw = os.getenv("GOOGLE_CREDENTIALS")
        if raw:
            creds = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
        client = gspread.authorize(creds)
        return client.open_by_key(SHEET_ID)

    _spreadsheet = _call(_connect)
    return _spreadsheet


# ─── STUDENTS ─────────────────────────────────────────────────────────────────
# Columns: PersonalID | FullName | Yosh | Universitet | Telefon nomer |
#          TelegramID | Telegram_username | Date | Stage | ExamRegistered | BitoJoinDate | BiJoinDate

def get_all_students():
    return cached("students", lambda: get_sheet().worksheet("Students").get_all_records())


def find_student_by_telegram_id(telegram_id):
    for s in get_all_students():
        if str(s["TelegramID"]) == str(telegram_id):
            return s
    return None


def find_student_by_id(student_id):
    for s in get_all_students():
        if str(s["PersonalID"]) == str(student_id):
            return s
    return None


def register_student(telegram_id, username, full_name, phone, university, yosh=""):
    bust("students")
    students = get_all_students()
    for s in students:
        if str(s["TelegramID"]) == str(telegram_id):
            return None, "already_registered"
    personal_id = f"STU-{uuid.uuid4().hex[:6].upper()}"
    date = now_uz().strftime("%Y-%m-%d %H:%M")
    sheet = get_sheet().worksheet("Students")
    headers = _call(lambda: sheet.row_values(1))
    row_data = [""] * len(headers)
    field_map = {
        "PersonalID": personal_id,
        "FullName": full_name,
        "Yosh": str(yosh),
        "Universitet": university,
        "Telefon nomer": phone,
        "TelegramID": str(telegram_id),
        "Telegram_username": username,
        "Date": date,
        "Stage": "pending",
    }
    for field, value in field_map.items():
        if field in headers:
            row_data[headers.index(field)] = value
    _call(lambda: sheet.append_row(row_data))
    bust("students")
    return personal_id, "success"


# ─── FORM RESPONSES ───────────────────────────────────────────────────────────
# Columns: Otmetka | To'liq ism | Telefon | Email | Code |
#          Nega... | Ma'lumot... | Yosh | Qaysi tillar | Universitet |
#          Stolbets 6 | PersonalID | TelegramID

def get_form_responses():
    return cached("form_responses", lambda: get_sheet().worksheet("Form_Responses").get_all_records())


def claim_form_response(row_index, personal_id, telegram_id):
    sheet = get_sheet().worksheet("Form_Responses")
    _call(lambda: sheet.update(range_name=f"L{row_index}", values=[[personal_id]]))
    _call(lambda: sheet.update(range_name=f"M{row_index}", values=[[str(telegram_id)]]))
    bust("form_responses")
    bust("students")


# ─── GRADES ───────────────────────────────────────────────────────────────────

def get_grades(student_id=None):
    records = cached("grades", lambda: get_sheet().worksheet("Grades").get_all_records())
    if student_id:
        return [g for g in records if str(g["StudentID"]) == str(student_id)]
    return records


# Track grades written by the bot so the grade watcher doesn't double-notify.
_bot_graded: set = set()


def _mark_bot_graded(student_id: str, hw_id: str, score: str) -> None:
    _bot_graded.add((str(student_id), str(hw_id), str(score)))


def consume_bot_graded() -> frozenset:
    """Return the set of (student_id, hw_id, score) written by the bot since last call, then clear it."""
    result = frozenset(_bot_graded)
    _bot_graded.clear()
    return result


def add_grade(student_id, assignment_id, score, feedback):
    date = now_uz().strftime("%Y-%m-%d %H:%M")
    _call(lambda: get_sheet().worksheet("Grades").append_row([student_id, assignment_id, score, feedback, date]))
    bust("grades")


def upsert_grade(student_id, assignment_id, score, feedback):
    """Update the existing grade row for this student+homework, or append if none exists.
    Prevents duplicate grade rows when AI retries or re-grades."""
    sheet = get_sheet().worksheet("Grades")
    records = _call(lambda: sheet.get_all_records())
    date = now_uz().strftime("%Y-%m-%d %H:%M")
    _mark_bot_graded(student_id, assignment_id, score)  # suppress watcher notification
    for i, g in enumerate(records):
        if str(g.get("StudentID", "")) == str(student_id) and str(g.get("AssignmentID", "")) == str(assignment_id):
            row = i + 2
            _call(lambda: sheet.batch_update([
                {"range": f"C{row}", "values": [[score]]},
                {"range": f"D{row}", "values": [[feedback]]},
                {"range": f"E{row}", "values": [[date]]},
            ]))
            bust("grades")
            return
    _call(lambda: sheet.append_row([student_id, assignment_id, score, feedback, date]))
    bust("grades")


# ─── Q&A HISTORY ──────────────────────────────────────────────────────────────

def ask_question(student_id, question, file_id="", file_type=""):
    q_id = f"Q-{uuid.uuid4().hex[:6].upper()}"
    date = now_uz().strftime("%Y-%m-%d %H:%M")
    # Columns: QuestionID | StudentID | Question | Answer | AnsweredBy | DateAsked | DateAnswered | StudentFileID | StudentFileType | AdminFileID | AdminFileType
    _call(lambda: get_sheet().worksheet("QA_History").append_row(
        [q_id, student_id, question, "", "", date, "", file_id, file_type, "", ""]
    ))
    bust("qa")
    return q_id


def get_unanswered_questions():
    records = cached("qa", lambda: get_sheet().worksheet("QA_History").get_all_records())
    return [q for q in records if q["Answer"] == ""]


def get_student_qa_history(student_id):
    records = cached("qa", lambda: get_sheet().worksheet("QA_History").get_all_records())
    return [q for q in records if str(q["StudentID"]) == str(student_id)]


def answer_question(question_id, answer, answered_by, file_id="", file_type=""):
    records = cached("qa", lambda: get_sheet().worksheet("QA_History").get_all_records())
    for i, q in enumerate(records):
        if str(q["QuestionID"]) == str(question_id):
            row = i + 2
            date = now_uz().strftime("%Y-%m-%d %H:%M")
            sheet = get_sheet().worksheet("QA_History")
            updates = [
                {"range": f"D{row}", "values": [[answer]]},
                {"range": f"E{row}", "values": [[answered_by]]},
                {"range": f"G{row}", "values": [[date]]},
            ]
            if file_id:
                updates += [
                    {"range": f"J{row}", "values": [[file_id]]},
                    {"range": f"K{row}", "values": [[file_type]]},
                ]
            _call(lambda: sheet.batch_update(updates))
            bust("qa")
            return q["StudentID"], q
    return None, None


# ─── SUBMISSIONS ──────────────────────────────────────────────────────────────

def get_submissions(assignment_id=None, student_id=None):
    records = cached("submissions", lambda: get_sheet().worksheet("Submissions").get_all_records())
    if assignment_id:
        records = [r for r in records if str(r["AssignmentID"]) == str(assignment_id)]
    if student_id:
        records = [r for r in records if str(r["StudentID"]) == str(student_id)]
    return records


def add_submission(student_id, assignment_id, content, file_id, file_type):
    sub_id = f"SUB-{uuid.uuid4().hex[:6].upper()}"
    date = now_uz().strftime("%Y-%m-%d %H:%M")
    _call(lambda: get_sheet().worksheet("Submissions").append_row([
        sub_id, student_id, assignment_id, content, file_id, file_type, date, "", "",
    ]))
    bust("submissions")
    return sub_id


def grade_submission(sub_id, grade, feedback):
    records = cached("submissions", lambda: get_sheet().worksheet("Submissions").get_all_records())
    for i, r in enumerate(records):
        if str(r["SubmissionID"]) == str(sub_id):
            row = i + 2
            _call(lambda: get_sheet().worksheet("Submissions").batch_update([
                {"range": f"H{row}", "values": [[grade]]},
                {"range": f"I{row}", "values": [[feedback]]},
            ]))
            bust("submissions")
            return r["StudentID"], r["AssignmentID"]
    return None, None


# ─── CODES ────────────────────────────────────────────────────────────────────

def save_code(code, telegram_id):
    sheet = get_sheet().worksheet("Codes")
    records = _call(lambda: sheet.get_all_records())
    for r in records:
        if str(r["TelegramID"]) == str(telegram_id):
            return
    _call(lambda: sheet.append_row([code, str(telegram_id)]))
    bust("codes")


def load_all_codes():
    records = cached("codes", lambda: get_sheet().worksheet("Codes").get_all_records())
    result = {}
    for r in records:
        code = str(r.get("Code", "")).strip()
        telegram_id = r.get("TelegramID", "")
        if not code or not str(telegram_id).strip():
            continue
        try:
            result[code] = int(telegram_id)
        except (ValueError, TypeError):
            logging.warning(f"Skipping invalid TelegramID row: {r}")
    return result


def delete_code(code):
    """Delete a used registration code from the Codes sheet."""
    sheet = get_sheet().worksheet("Codes")
    records = _call(lambda: sheet.get_all_records())
    for i, r in enumerate(records):
        if str(r.get("Code", "")).strip() == str(code).strip():
            row = i + 2
            _call(lambda: sheet.delete_rows(row))
            bust("codes")
            return True
    return False

# ─── PROGRAM STAGE (bito → bi after midterm) ──────────────────────────────────
# Add a "Stage" column to the Students sheet. Empty or "bito" = stage 1.
# "bi" = promoted past the midterm (must also be in the BI channel).

def get_student_stage(telegram_id):
    """Return 'unknown', 'pending', 'bito', or 'bi'."""
    s = find_student_by_telegram_id(telegram_id)
    if not s:
        return "unknown"
    stage = str(s.get("Stage", "")).strip().lower()
    if stage == "bi":
        return "bi"
    if stage == "pending":
        return "pending"
    return "bito"  # empty, "bito", or any legacy value


def set_student_stage(personal_id, stage):
    """Set a student's stage. Returns (ok, reason)."""
    sheet = get_sheet().worksheet("Students")
    headers = cached("students_headers", lambda: sheet.row_values(1))
    if "Stage" not in headers:
        return False, "no_column"
    col_idx = headers.index("Stage") + 1
    col_letter = gspread.utils.rowcol_to_a1(1, col_idx)[:-1]
    students = _call(lambda: sheet.get_all_records())
    for i, s in enumerate(students):
        if str(s["PersonalID"]) == str(personal_id):
            row = i + 2
            _call(lambda: sheet.update(range_name=f"{col_letter}{row}", values=[[stage]]))
            bust("students")
            return True, "ok"
    return False, "not_found"


def register_for_exam(telegram_id):
    """Register a pending student for the active exam.
    Returns (True, personal_id) on success, (False, reason) on failure.
    """
    # ── Find student ──────────────────────────────────────────────────────────
    stu_sheet = get_sheet().worksheet("Students")
    stu_headers = cached("students_headers", lambda: stu_sheet.row_values(1))
    if "ExamRegistered" not in stu_headers:
        return False, "no_column"
    reg_col_idx = stu_headers.index("ExamRegistered") + 1
    reg_col_letter = gspread.utils.rowcol_to_a1(1, reg_col_idx)[:-1]

    students = _call(lambda: stu_sheet.get_all_records())
    pid = None
    stu_row = None
    for i, s in enumerate(students):
        if str(s["TelegramID"]) == str(telegram_id):
            if str(s.get("Stage", "")).strip().lower() != "pending":
                return False, "wrong_stage"
            if str(s.get("ExamRegistered", "")).strip().upper() == "TRUE":
                return False, "already_registered"
            pid = str(s["PersonalID"])
            stu_row = i + 2
            break
    if pid is None:
        return False, "not_found"

    # ── Find active exam & check slots ────────────────────────────────────────
    bust("exam")
    exam = get_active_exam()
    if not exam:
        return False, "no_exam"

    max_slots = int(exam.get("MaxSlots", 0) or 0)
    avail_raw = exam.get("AvailableSlots", "")
    try:
        available = int(avail_raw) if str(avail_raw).strip() else max_slots
    except (ValueError, TypeError):
        available = max_slots
    if available <= 0:
        return False, "no_slots"

    exam_id = str(exam.get("ExamDate", "")).strip()

    # ── Write ExamRegistered=TRUE to Students sheet ───────────────────────────
    _call(lambda: stu_sheet.update(range_name=f"{reg_col_letter}{stu_row}", values=[["TRUE"]]))
    bust("students")

    # ── Append row to ExamRegistrations sheet ─────────────────────────────────
    try:
        reg_sheet = get_sheet().worksheet("Exam_Registrations")
        _call(lambda: reg_sheet.append_row(
            [pid, exam_id, "registered", now_uz().strftime("%Y-%m-%d %H:%M")],
            value_input_option="USER_ENTERED",
        ))
    except Exception as e:
        logging.warning("register_for_exam: ExamRegistrations write failed: %s", e)

    # ── Decrement AvailableSlots in Exams sheet ───────────────────────────────
    try:
        exams_sheet = get_sheet().worksheet("Exams")
        ex_headers = _call(lambda: exams_sheet.row_values(1))
        if "AvailableSlots" in ex_headers:
            avail_col_idx = ex_headers.index("AvailableSlots") + 1
            avail_col_letter = gspread.utils.rowcol_to_a1(1, avail_col_idx)[:-1]
            exam_records = _call(lambda: exams_sheet.get_all_records())
            for j, er in enumerate(exam_records):
                active_val = er.get("Active", "")
                if str(active_val).strip().upper() == "TRUE" or active_val is True:
                    ex_row = j + 2
                    new_avail = max(0, available - 1)
                    _call(lambda: exams_sheet.update(
                        range_name=f"{avail_col_letter}{ex_row}",
                        values=[[new_avail]],
                    ))
                    bust("exam")
                    break
    except Exception as e:
        logging.warning("register_for_exam: AvailableSlots decrement failed: %s", e)

    return True, pid


def get_exam_signups():
    """Return students with stage='pending' and ExamRegistered='TRUE'."""
    return [
        s for s in get_all_students()
        if str(s.get("Stage", "")).strip().lower() == "pending"
        and str(s.get("ExamRegistered", "")).strip().upper() == "TRUE"
    ]


def approve_student(personal_id):
    """Promote a pending student to bito. Returns (True, telegram_id) or (False, reason)."""
    sheet = get_sheet().worksheet("Students")
    headers = cached("students_headers", lambda: sheet.row_values(1))
    if "Stage" not in headers:
        return False, "no_column"
    stage_idx = headers.index("Stage") + 1
    stage_letter = gspread.utils.rowcol_to_a1(1, stage_idx)[:-1]
    students = _call(lambda: sheet.get_all_records())
    for i, s in enumerate(students):
        if str(s["PersonalID"]) == str(personal_id):
            if str(s.get("Stage", "")).strip().lower() != "pending":
                return False, "wrong_stage"
            row = i + 2
            _call(lambda: sheet.update(range_name=f"{stage_letter}{row}", values=[["bito"]]))
            bust("students")
            return True, str(s["TelegramID"])
    return False, "not_found"


def get_blacklist():
    """Return set of TelegramIDs (strings) that are expelled."""
    try:
        records = cached("blacklist", lambda: get_sheet().worksheet("Blacklist").get_all_records())
        return {str(r["TelegramID"]) for r in records if r.get("TelegramID")}
    except Exception:
        return set()


def add_to_blacklist(personal_id, reason=""):
    """Expel a student by PersonalID. Returns (True, telegram_id) or (False, reason_str)."""
    student = find_student_by_id(personal_id)
    if not student:
        return False, "not_found"
    telegram_id = str(student["TelegramID"])
    if telegram_id in get_blacklist():
        return False, "already_blacklisted"
    date = now_uz().strftime("%Y-%m-%d %H:%M")
    _call(lambda: get_sheet().worksheet("Blacklist").append_row([
        telegram_id, personal_id, student["FullName"], reason, date
    ]))
    bust("blacklist")
    return True, telegram_id


def remove_from_blacklist(personal_id):
    """Un-expel a student. Returns (True, telegram_id) if removed, (False, None) otherwise."""
    sheet = get_sheet().worksheet("Blacklist")
    records = _call(lambda: sheet.get_all_records())
    for i, r in enumerate(records):
        if str(r.get("PersonalID", "")) == str(personal_id):
            telegram_id = str(r.get("TelegramID", ""))
            row = i + 2
            _call(lambda: sheet.delete_rows(row))
            bust("blacklist")
            return True, telegram_id
    return False, None


def get_active_exam():
    """Return the active exam row from the Exams sheet, or None if unavailable."""
    try:
        records = cached("exam", lambda: get_sheet().worksheet("Exams").get_all_records())
    except Exception:
        return None
    for r in records:
        if str(r.get("Active", "")).strip().upper() == "TRUE":
            return r
    return None


def get_exam_registered_count():
    """Count pending students who have signed up for the exam. Always reads fresh."""
    bust("students")
    return sum(
        1 for s in get_all_students()
        if str(s.get("Stage", "")).strip().lower() == "pending"
        and str(s.get("ExamRegistered", "")).strip().upper() == "TRUE"
    )


# ─── BI JOIN DATE ─────────────────────────────────────────────────────────────

def _set_date_column(personal_id, column_name):
    """Write now_uz() into a named date column for a student. Returns True on success."""
    sheet = get_sheet().worksheet("Students")
    headers = cached("students_headers", lambda: sheet.row_values(1))
    if column_name not in headers:
        return False
    col_idx = headers.index(column_name) + 1
    col_letter = gspread.utils.rowcol_to_a1(1, col_idx)[:-1]
    students = _call(lambda: sheet.get_all_records())
    for i, s in enumerate(students):
        if str(s["PersonalID"]) == str(personal_id):
            row = i + 2
            _call(lambda: sheet.update(range_name=f"{col_letter}{row}", values=[[now_uz().strftime("%Y-%m-%d %H:%M")]]))
            bust("students")
            return True
    return False


def set_bito_join_date(personal_id):
    """Record when a student first joins the BITO channel. Returns True on success."""
    return _set_date_column(personal_id, "BitoJoinDate")


def set_bi_join_date(personal_id):
    """Record when a student first joins the BI channel. Returns True on success."""
    return _set_date_column(personal_id, "BiJoinDate")


# ─── HOMEWORKS SHEET ──────────────────────────────────────────────────────────
# Columns: HWID | Program | Title | Description | DeadlineDays | FileID | FileType | AnswerFileID | AnswerFileType

def _normalize_hw(record: dict) -> dict:
    if "Programm" in record and "Program" not in record:
        record = dict(record)
        record["Program"] = record.pop("Programm")
    return record


def get_all_homeworks():
    try:
        raw = cached("homeworks", lambda: get_sheet().worksheet("Homeworks").get_all_records())
        return [_normalize_hw(h) for h in raw]
    except Exception:
        return []


def get_homework(hw_id):
    for h in get_all_homeworks():
        if str(h.get("HWID", "")).strip() == str(hw_id):
            return h
    return None


def get_homeworks_by_program(program):
    return [
        h for h in get_all_homeworks()
        if str(h.get("Program", "")).strip().lower() == program.lower()
        and str(h.get("HWID", "")).strip()
    ]


def set_homework_file(hw_id, file_id, file_type):
    """Write FileID and FileType for a homework row. Returns True on success."""
    sheet = get_sheet().worksheet("Homeworks")
    headers = _call(lambda: sheet.row_values(1))
    if "FileID" not in headers or "FileType" not in headers:
        return False
    fid_col = gspread.utils.rowcol_to_a1(1, headers.index("FileID") + 1)[:-1]
    ftype_col = gspread.utils.rowcol_to_a1(1, headers.index("FileType") + 1)[:-1]
    records = _call(lambda: sheet.get_all_records())
    for i, h in enumerate(records):
        if str(h.get("HWID", "")).strip() == str(hw_id):
            row = i + 2
            _call(lambda: sheet.update(range_name=f"{fid_col}{row}", values=[[file_id]]))
            _call(lambda: sheet.update(range_name=f"{ftype_col}{row}", values=[[file_type]]))
            bust("homeworks")
            return True
    return False


def add_homework(hwid, program, title, description, deadline_days):
    sheet = get_sheet().worksheet("Homeworks")
    _call(lambda: sheet.append_row([hwid, program, title, description, deadline_days, "", "", "", ""]))
    bust("homeworks")


def delete_homework(hwid):
    sheet = get_sheet().worksheet("Homeworks")
    records = _call(lambda: sheet.get_all_records())
    for i, h in enumerate(records):
        if str(h.get("HWID", "")).strip() == str(hwid):
            _call(lambda: sheet.delete_rows(i + 2))
            bust("homeworks")
            return True
    return False


def set_homework_answer_file(hw_id, file_id, file_type):
    """Write AnswerFileID and AnswerFileType for a BITO homework row."""
    sheet = get_sheet().worksheet("Homeworks")
    headers = _call(lambda: sheet.row_values(1))
    if "AnswerFileID" not in headers or "AnswerFileType" not in headers:
        return False
    fid_col = gspread.utils.rowcol_to_a1(1, headers.index("AnswerFileID") + 1)[:-1]
    ftype_col = gspread.utils.rowcol_to_a1(1, headers.index("AnswerFileType") + 1)[:-1]
    records = _call(lambda: sheet.get_all_records())
    for i, h in enumerate(records):
        if str(h.get("HWID", "")).strip() == str(hw_id):
            row = i + 2
            _call(lambda: sheet.update(range_name=f"{fid_col}{row}", values=[[file_id]]))
            _call(lambda: sheet.update(range_name=f"{ftype_col}{row}", values=[[file_type]]))
            bust("homeworks")
            return True
    return False


# ─── DEADLINE TRACKING ────────────────────────────────────────────────────────
# Columns: StudentID | Program | HWID | Deadline |
#          Rem12Sent | Rem6Sent | Rem2Sent | MissNotified |
#          ExcuseText | ExcuseStatus | ReviewedBy

def get_deadline_tracking(student_id=None, program=None):
    try:
        records = cached("deadlines", lambda: get_sheet().worksheet("DeadlineTracking").get_all_records())
    except Exception:
        return []
    if student_id:
        records = [r for r in records if str(r.get("StudentID", "")) == str(student_id)]
    if program:
        records = [r for r in records if str(r.get("Program", "")) == program]
    return records


def create_deadline_rows(student_id, program, hw_keys, join_date):
    """Create DeadlineTracking rows for a student. Skip rows that already exist."""
    try:
        sheet = get_sheet().worksheet("DeadlineTracking")
    except Exception:
        return
    existing = _call(lambda: sheet.get_all_records())
    existing_keys = {
        (str(r.get("StudentID", "")), str(r.get("Program", "")), str(r.get("HWID", "")))
        for r in existing
    }
    rows_to_add = []
    cumulative_days = 0
    for hw_id in hw_keys:
        hw = get_homework(hw_id)
        days = int(hw.get("DeadlineDays") or 1) if hw else 1
        cumulative_days += days
        if (str(student_id), program, str(hw_id)) in existing_keys:
            continue
        deadline = join_date + timedelta(days=cumulative_days)
        rows_to_add.append([
            str(student_id), program, str(hw_id),
            deadline.strftime("%Y-%m-%d %H:%M"),
            "", "", "", "", "", "", "",
        ])
    if rows_to_add:
        _call(lambda: sheet.append_rows(rows_to_add))
    bust("deadlines")


def create_bito_deadlines(personal_id):
    hws = get_homeworks_by_program("bito")
    hw_keys = [h["HWID"] for h in sorted(hws, key=lambda h: str(h.get("HWID", "")))]
    create_deadline_rows(personal_id, "bito", hw_keys, now_uz())


def create_bi_deadlines(personal_id):
    hws = get_homeworks_by_program("bi")
    hw_keys = [h["HWID"] for h in sorted(hws, key=lambda h: str(h.get("HWID", "")))]
    create_deadline_rows(personal_id, "bi", hw_keys, now_uz())


def update_deadline_field(student_id, program, hw_id, field, value, bust_after=True):
    """Update a single field in a DeadlineTracking row.

    Pass bust_after=False when calling in a tight loop and bust("deadlines")
    manually once after the loop finishes.
    """
    try:
        sheet = get_sheet().worksheet("DeadlineTracking")
        headers = cached("deadline_headers", lambda: sheet.row_values(1))
        if field not in headers:
            return False
        col_idx = headers.index(field) + 1
        col_letter = gspread.utils.rowcol_to_a1(1, col_idx)[:-1]
        records = cached("deadlines", lambda: sheet.get_all_records())
        for i, r in enumerate(records):
            if (str(r.get("StudentID", "")) == str(student_id) and
                    str(r.get("Program", "")) == program and
                    str(r.get("HWID", "")) == str(hw_id)):
                row = i + 2
                _call(lambda: sheet.update(range_name=f"{col_letter}{row}", values=[[value]]))
                if bust_after:
                    bust("deadlines")
                return True
    except Exception as e:
        logging.warning(f"update_deadline_field failed: {e}")
    return False


def get_pending_excuses():
    """Return rows where student submitted an excuse and admin hasn't reviewed yet."""
    try:
        records = cached("deadlines", lambda: get_sheet().worksheet("DeadlineTracking").get_all_records())
    except Exception:
        return []
    return [
        r for r in records
        if str(r.get("ExcuseStatus", "")).strip().lower() == "pending"
    ]


def set_excuse_status(student_id, program, hw_id, status, reviewed_by):
    """Mark an excuse as 'uzrli' or 'sababsiz'."""
    try:
        sheet = get_sheet().worksheet("DeadlineTracking")
        headers = cached("deadline_headers", lambda: sheet.row_values(1))
        records = cached("deadlines", lambda: sheet.get_all_records())
        for i, r in enumerate(records):
            if (str(r.get("StudentID", "")) == str(student_id) and
                    str(r.get("Program", "")) == program and
                    str(r.get("HWID", "")) == str(hw_id)):
                row = i + 2
                updates = []
                if "ExcuseStatus" in headers:
                    col = gspread.utils.rowcol_to_a1(row, headers.index("ExcuseStatus") + 1)
                    updates.append({"range": col, "values": [[status]]})
                if "ReviewedBy" in headers:
                    col = gspread.utils.rowcol_to_a1(row, headers.index("ReviewedBy") + 1)
                    updates.append({"range": col, "values": [[reviewed_by]]})
                if updates:
                    _call(lambda: sheet.batch_update(updates))
                bust("deadlines")
                return True
    except Exception as e:
        logging.warning(f"set_excuse_status failed: {e}")
    return False


def count_unexcused(student_id, program):
    """Count sababsiz misses for a student in a given program."""
    try:
        records = cached("deadlines", lambda: get_sheet().worksheet("DeadlineTracking").get_all_records())
    except Exception:
        return 0
    return sum(
        1 for r in records
        if str(r.get("StudentID", "")) == str(student_id)
        and str(r.get("Program", "")) == program
        and str(r.get("ExcuseStatus", "")).strip().lower() == "sababsiz"
    )


_BACKUP_SHEETS = [
    ("Students",         "students"),
    ("Homeworks",        "homeworks"),
    ("Submissions",      "submissions"),
    ("Grades",           "grades"),
    ("DeadlineTracking", "deadlines"),
    ("QA_History",       "qa_history"),
]


def backup_to_csv(backup_dir: str = "backups") -> None:
    """Export every key sheet to timestamped CSV files in backup_dir."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(TZ_UZ).strftime("%Y%m%d_%H%M%S")
    sp = get_sheet()
    for worksheet_name, file_stem in _BACKUP_SHEETS:
        try:
            rows = sp.worksheet(worksheet_name).get_all_records()
        except Exception as e:
            logging.warning("backup: skipping %s — %s", worksheet_name, e)
            continue
        if not rows:
            continue
        path = os.path.join(backup_dir, f"{file_stem}_{stamp}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        logging.info("backup: wrote %s (%d rows)", path, len(rows))
