"""
Regression tests for sheets.py.
All gspread I/O is mocked at the get_sheet() boundary.
"""
import time
import pytest
from unittest.mock import MagicMock, patch
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sheets


# â”€â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def ws(records=None, headers=None):
    m = MagicMock()
    m.get_all_records.return_value = records or []
    m.row_values.return_value = headers or []
    return m


def sp(worksheets=None):
    m = MagicMock()
    if worksheets:
        m.worksheet.side_effect = lambda name: worksheets.get(name, MagicMock())
    return m


STUDENT_HEADERS = [
    "PersonalID", "FullName", "Yosh", "Universitet",
    "Telefon nomer", "TelegramID", "Telegram_username",
    "Date", "Stage", "ExamRegistered", "BitoJoinDate", "BiJoinDate",
]


@pytest.fixture(autouse=True)
def clear_cache():
    sheets.bust_all()
    yield
    sheets.bust_all()


# â”€â”€â”€ register_student â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRegisterStudent:
    def test_new_student_stage_is_pending(self):
        students_ws = ws(records=[], headers=STUDENT_HEADERS)
        mock_sp = sp({"Students": students_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            pid, status = sheets.register_student(111, "u", "Name", "phone", "uni", "22")
        assert status == "success"
        row = students_ws.append_row.call_args[0][0]
        assert row[STUDENT_HEADERS.index("Stage")] == "pending"

    def test_duplicate_telegram_id_rejected(self):
        students_ws = ws(records=[{"TelegramID": "111", "PersonalID": "STU-ABC"}])
        mock_sp = sp({"Students": students_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            with patch("core.sheets._cache", {}):
                _, status = sheets.register_student(111, "u", "Name", "phone", "uni")
        assert status == "already_registered"

    def test_personal_id_is_uuid_not_sequential(self):
        ids = set()
        for i in range(10):
            students_ws = ws(records=[], headers=STUDENT_HEADERS)
            mock_sp = sp({"Students": students_ws})
            with patch("core.sheets.get_sheet", return_value=mock_sp):
                with patch("core.sheets._cache", {}):
                    pid, _ = sheets.register_student(1000 + i, "u", "N", "p", "uni")
            ids.add(pid)
        assert len(ids) == 10, "PersonalIDs must be unique (uuid-based)"

    def test_exam_registered_field_not_overwritten(self):
        students_ws = ws(records=[], headers=STUDENT_HEADERS)
        mock_sp = sp({"Students": students_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            pid, status = sheets.register_student(222, "u", "Name", "phone", "uni")
        assert status == "success"
        row = students_ws.append_row.call_args[0][0]
        exam_idx = STUDENT_HEADERS.index("ExamRegistered")
        assert row[exam_idx] == ""


# â”€â”€â”€ get_student_stage â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestGetStudentStage:
    def _s(self, stage, tid=123):
        return {"TelegramID": str(tid), "PersonalID": "STU-0001", "Stage": stage}

    def test_unknown_when_not_found(self):
        with patch("core.sheets.get_all_students", return_value=[]):
            assert sheets.get_student_stage(999) == "unknown"

    def test_pending(self):
        with patch("core.sheets.get_all_students", return_value=[self._s("pending")]):
            assert sheets.get_student_stage(123) == "pending"

    def test_bito(self):
        with patch("core.sheets.get_all_students", return_value=[self._s("bito")]):
            assert sheets.get_student_stage(123) == "bito"

    def test_bi(self):
        with patch("core.sheets.get_all_students", return_value=[self._s("bi")]):
            assert sheets.get_student_stage(123) == "bi"

    def test_empty_stage_defaults_to_bito(self):
        with patch("core.sheets.get_all_students", return_value=[self._s("")]):
            assert sheets.get_student_stage(123) == "bito"

    def test_case_insensitive(self):
        with patch("core.sheets.get_all_students", return_value=[self._s("  BI  ")]):
            assert sheets.get_student_stage(123) == "bi"


# â”€â”€â”€ approve_student â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestApproveStudent:
    def test_returns_telegram_id_on_success(self):
        headers = ["PersonalID", "TelegramID", "Stage", "FullName", "BitoJoinDate"]
        records = [{"PersonalID": "STU-0001", "TelegramID": "777", "Stage": "pending",
                    "FullName": "Test", "BitoJoinDate": ""}]
        students_ws = ws(records=records, headers=headers)
        mock_sp = sp({"Students": students_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            ok, result = sheets.approve_student("STU-0001")
        assert ok is True
        assert result == "777"

    def test_wrong_stage_fails(self):
        headers = ["PersonalID", "TelegramID", "Stage"]
        records = [{"PersonalID": "STU-0001", "TelegramID": "777", "Stage": "bito"}]
        students_ws = ws(records=records, headers=headers)
        mock_sp = sp({"Students": students_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            ok, reason = sheets.approve_student("STU-0001")
        assert ok is False
        assert reason == "wrong_stage"

    def test_not_found_fails(self):
        students_ws = ws(records=[], headers=["PersonalID", "TelegramID", "Stage"])
        mock_sp = sp({"Students": students_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            ok, reason = sheets.approve_student("STU-XXXX")
        assert ok is False
        assert reason == "not_found"


# â”€â”€â”€ grade_submission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestGradeSubmission:
    def test_uses_batch_update_not_two_calls(self):
        records = [{"SubmissionID": "SUB-ABC", "StudentID": "STU-0001", "AssignmentID": "HW1"}]
        sheets._cache["submissions"] = {"v": records, "t": time.time()}
        submissions_ws = MagicMock()
        mock_sp = sp({"Submissions": submissions_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            sid, hwid = sheets.grade_submission("SUB-ABC", "90", "Good")
        assert sid == "STU-0001"
        assert hwid == "HW1"
        submissions_ws.batch_update.assert_called_once()
        submissions_ws.update.assert_not_called()

    def test_missing_sub_returns_none_none(self):
        sheets._cache["submissions"] = {"v": [], "t": time.time()}
        mock_sp = sp({"Submissions": MagicMock()})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            result = sheets.grade_submission("MISSING", "90", "x")
        assert result == (None, None)


# â”€â”€â”€ ask_question IDs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestAskQuestion:
    def test_unique_ids(self):
        ids = []
        for i in range(5):
            records = []
            sheets._cache["qa"] = {"v": records, "t": time.time()}
            qa_ws = MagicMock()
            mock_sp = sp({"QA_History": qa_ws})
            with patch("core.sheets.get_sheet", return_value=mock_sp):
                q_id = sheets.ask_question("STU-0001", f"question {i}")
            ids.append(q_id)
        assert len(set(ids)) == 5, "QA IDs must be unique"

    def test_id_starts_with_q(self):
        sheets._cache["qa"] = {"v": [], "t": time.time()}
        qa_ws = MagicMock()
        mock_sp = sp({"QA_History": qa_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            q_id = sheets.ask_question("STU-0001", "test")
        assert q_id.startswith("Q-")


# â”€â”€â”€ get_blacklist â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestGetBlacklist:
    def test_returns_empty_set_on_error(self):
        with patch("core.sheets.get_sheet", side_effect=Exception("down")):
            with patch("core.sheets._cache", {}):
                result = sheets.get_blacklist()
        assert result == set()

    def test_returns_string_ids(self):
        records = [{"TelegramID": 123}, {"TelegramID": "456"}]
        sheets._cache["blacklist"] = {"v": records, "t": time.time()}
        result = sheets.get_blacklist()
        assert "123" in result
        assert "456" in result


# â”€â”€â”€ delete_code â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestDeleteCode:
    def test_deletes_correct_row(self):
        records = [{"Code": "ABC123", "TelegramID": "111"},
                   {"Code": "XYZ999", "TelegramID": "222"}]
        codes_ws = ws(records=records)
        mock_sp = sp({"Codes": codes_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            result = sheets.delete_code("ABC123")
        assert result is True
        codes_ws.delete_rows.assert_called_once_with(2)

    def test_returns_false_if_not_found(self):
        codes_ws = ws(records=[])
        mock_sp = sp({"Codes": codes_ws})
        with patch("core.sheets.get_sheet", return_value=mock_sp):
            result = sheets.delete_code("NOTHERE")
        assert result is False
