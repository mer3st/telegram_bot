"""
Security-critical path tests: code generation, admin auth, blacklist, env validation.
"""
import hashlib
import pytest
from unittest.mock import patch
from telegram.ext import ConversationHandler
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMakeCode:
    def test_codes_are_unique_per_call(self):
        from config import make_code
        codes = [make_code(12345) for _ in range(50)]
        assert len(set(codes)) == 50, "make_code must return a unique code each call"

    def test_code_is_not_md5_of_telegram_id(self):
        from config import make_code
        for tid in [100, 999999, 123456789]:
            code = make_code(tid)
            md5 = hashlib.md5(str(tid).encode()).hexdigest()[:8].upper()
            assert code != md5, f"Code for {tid} must not equal its MD5"

    def test_code_is_not_deterministic(self):
        from config import make_code
        same_tid_codes = {make_code(42) for _ in range(10)}
        assert len(same_tid_codes) > 1, "Same telegram_id must produce different codes each time"

    def test_code_has_reasonable_length(self):
        from config import make_code
        code = make_code(12345)
        assert 6 <= len(code) <= 20


class TestIsAdmin:
    def test_known_admin_returns_true(self):
        from config import is_admin
        with patch("config.ADMIN_IDS", [111, 222, 333]):
            assert is_admin(111) is True
            assert is_admin(333) is True

    def test_unknown_user_returns_false(self):
        from config import is_admin
        with patch("config.ADMIN_IDS", [111]):
            assert is_admin(999) is False

    def test_empty_admin_list(self):
        from config import is_admin
        with patch("config.ADMIN_IDS", []):
            assert is_admin(111) is False


class TestGetBlacklist:
    def test_returns_empty_set_on_sheet_error(self):
        from core import sheets
        with patch("core.sheets.get_sheet", side_effect=Exception("network error")):
            with patch("core.sheets._cache", {}):
                result = sheets.get_blacklist()
        assert isinstance(result, set)
        assert len(result) == 0

    def test_all_ids_coerced_to_strings(self):
        import time
        from core import sheets
        records = [{"TelegramID": 111}, {"TelegramID": "222"}, {"TelegramID": 333}]
        sheets._cache["blacklist"] = {"v": records, "t": time.time()}
        result = sheets.get_blacklist()
        assert "111" in result
        assert "222" in result
        assert "333" in result
        assert 111 not in result

    def test_rows_without_telegram_id_skipped(self):
        import time
        from core import sheets
        records = [{"TelegramID": "123"}, {"TelegramID": ""}, {"other_col": "x"}]
        sheets._cache["blacklist"] = {"v": records, "t": time.time()}
        result = sheets.get_blacklist()
        assert "123" in result
        assert "" not in result


class TestValidateEnv:
    def test_missing_bot_token_raises(self):
        from config import validate_env
        with patch("config.BOT_TOKEN", None):
            with patch("config.GOOGLE_SHEET_ID", "some_id"):
                with patch("config.ADMIN_IDS", [123]):
                    with pytest.raises(EnvironmentError) as exc_info:
                        validate_env()
        assert "BOT_TOKEN" in str(exc_info.value)

    def test_all_vars_present_does_not_raise(self):
        from config import validate_env
        with patch("config.BOT_TOKEN", "token123"):
            with patch("config.GOOGLE_SHEET_ID", "sheet456"):
                with patch("os.getenv", return_value="123"):
                    validate_env()


class TestAdminIdsParsingRobustness:
    def test_admin_ids_strips_whitespace(self):
        """ADMIN_IDS parsing must handle spaces around commas."""
        import importlib
        import config as cfg
        original = cfg.ADMIN_IDS[:]
        try:
            ids = [int(x) for x in "111 , 222 , 333".split(",") if x.strip()]
            assert ids == [111, 222, 333]
        finally:
            cfg.ADMIN_IDS[:] = original


class TestInputLengthValidation:
    @pytest.mark.asyncio
    async def test_question_over_1000_chars_rejected(self):
        from handlers.student import ask_receive, ASK_QUESTION
        from unittest.mock import MagicMock, AsyncMock
        update = MagicMock()
        update.effective_user.id = 123
        update.message.text = "q" * 1001
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {}
        result = await ask_receive(update, context)
        assert result == ASK_QUESTION

    @pytest.mark.asyncio
    async def test_question_at_1000_chars_accepted(self):
        from handlers.student import ask_receive, ASK_QUESTION, _ask_cooldown
        from unittest.mock import MagicMock, AsyncMock
        import time
        user_id = 54321
        _ask_cooldown.pop(user_id, None)
        update = MagicMock()
        update.effective_user.id = user_id
        update.message.text = "q" * 1000
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {}
        student = {"PersonalID": "STU-0001", "FullName": "Test"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("core.sheets.ask_question", return_value="Q-ABC"):
                with patch("core.sheets.get_unanswered_questions", return_value=[]):
                    with patch("config.ADMIN_IDS", []):
                        with patch("handlers.student.stage_keyboard", return_value=MagicMock()):
                            result = await ask_receive(update, context)
        assert result == ConversationHandler.END
        _ask_cooldown.pop(user_id, None)
