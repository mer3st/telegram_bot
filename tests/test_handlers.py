"""
Tests for handler-level logic.
Patches sheets.* to avoid any real network calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.ext import ConversationHandler
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_update(text="", user_id=123):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def make_context(state=None):
    ctx = MagicMock()
    ctx.user_data = {}
    if state:
        ctx.user_data["state"] = state
    return ctx


# â”€â”€â”€ homework_menu routing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestHomeworkMenu:
    @pytest.mark.asyncio
    async def test_bito_student_routed_to_bito_list(self):
        from handlers.student import homework_menu
        update = make_update()
        context = make_context()
        student = {"PersonalID": "STU-0001", "Stage": "bito", "TelegramID": "123"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("core.sheets.get_student_stage", return_value="bito"):
                with patch("handlers.student.homework_bito_list", new_callable=AsyncMock) as mock_bito:
                    with patch("handlers.student.homework_bi_list", new_callable=AsyncMock) as mock_bi:
                        await homework_menu(update, context)
        mock_bito.assert_called_once()
        mock_bi.assert_not_called()

    @pytest.mark.asyncio
    async def test_bi_student_routed_to_bi_list(self):
        from handlers.student import homework_menu
        update = make_update()
        context = make_context()
        student = {"PersonalID": "STU-0001", "Stage": "bi", "TelegramID": "123"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("core.sheets.get_student_stage", return_value="bi"):
                with patch("handlers.student.homework_bito_list", new_callable=AsyncMock) as mock_bito:
                    with patch("handlers.student.homework_bi_list", new_callable=AsyncMock) as mock_bi:
                        await homework_menu(update, context)
        mock_bi.assert_called_once()
        mock_bito.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_student_replies_and_returns(self):
        from handlers.student import homework_menu
        update = make_update()
        context = make_context()
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            await homework_menu(update, context)
        update.message.reply_text.assert_called_once()


# â”€â”€â”€ ask_receive null guard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestAskReceive:
    @pytest.mark.asyncio
    async def test_null_student_ends_conversation(self):
        from handlers.student import ask_receive
        update = make_update("Mening savolim", user_id=999)
        context = make_context()
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            result = await ask_receive(update, context)
        assert result == ConversationHandler.END
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_question_over_1000_chars_stays_in_state(self):
        from handlers.student import ask_receive, ASK_QUESTION
        update = make_update("x" * 1001)
        context = make_context()
        result = await ask_receive(update, context)
        assert result == ASK_QUESTION

    @pytest.mark.asyncio
    async def test_back_button_ends_conversation(self):
        from handlers.student import ask_receive
        update = make_update("ðŸ”™ Orqaga")
        context = make_context()
        with patch("handlers.student.stage_keyboard", return_value=MagicMock()):
            result = await ask_receive(update, context)
        assert result == ConversationHandler.END

    @pytest.mark.asyncio
    async def test_cooldown_prevents_rapid_questions(self):
        import time as t
        from handlers.student import ask_receive, ASK_QUESTION, _ask_cooldown
        user_id = 7777
        _ask_cooldown[user_id] = t.time()
        update = make_update("Savol matni", user_id=user_id)
        context = make_context()
        result = await ask_receive(update, context)
        assert result == ASK_QUESTION
        update.message.reply_text.assert_called_once()
        _ask_cooldown.pop(user_id, None)


# â”€â”€â”€ homework detail null guards â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestHomeworkDetailNullGuard:
    @pytest.mark.asyncio
    async def test_bito_detail_null_student_does_not_crash(self):
        from handlers.student import homework_bito_detail
        update = make_update(user_id=9999)
        context = make_context()
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            await homework_bito_detail(update, context, "bito-01")
        update.message.reply_text.assert_called()

    @pytest.mark.asyncio
    async def test_bi_detail_null_student_does_not_crash(self):
        from handlers.student import homework_bi_detail
        update = make_update(user_id=9999)
        context = make_context()
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            await homework_bi_detail(update, context, "bi-01")
        update.message.reply_text.assert_called()


# â”€â”€â”€ register_start â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRegisterStart:
    @pytest.mark.asyncio
    async def test_existing_student_shows_id(self):
        from handlers.student import register_start
        update = make_update()
        context = make_context()
        context.args = []
        student = {"PersonalID": "STU-ABC", "Stage": "bito", "TelegramID": "123"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("handlers.student.stage_keyboard", return_value=MagicMock()):
                result = await register_start(update, context)
        assert result == ConversationHandler.END
        call_text = update.message.reply_text.call_args[0][0]
        assert "STU-ABC" in call_text


# â”€â”€â”€ grades handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestGrades:
    @pytest.mark.asyncio
    async def test_no_grades_shows_message(self):
        from handlers.student import grades
        update = make_update()
        context = make_context()
        student = {"PersonalID": "STU-0001"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("core.sheets.get_grades", return_value=[]):
                await grades(update, context)
        update.message.reply_text.assert_called_once()
        assert "yo'q" in update.message.reply_text.call_args[0][0]


# ─── _sh_excuse_typing missing context ────────────────────────────────────────

class TestExcuseTyping:
    @pytest.mark.asyncio
    async def test_missing_hw_id_resets_state_and_replies(self):
        from handlers.student import _sh_excuse_typing
        import states as S
        update = make_update("Uzr matni")
        context = make_context()
        # hw_id missing from user_data
        context.user_data["excuse_program"] = "bito"
        student = {"PersonalID": "STU-0001", "FullName": "Test"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("handlers.student.stage_keyboard", return_value=MagicMock()):
                await _sh_excuse_typing(update, context)
        assert context.user_data.get("state") == S.MAIN
        update.message.reply_text.assert_called_once()
        assert "Sessiya" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_missing_program_resets_state_and_replies(self):
        from handlers.student import _sh_excuse_typing
        import states as S
        update = make_update("Uzr matni")
        context = make_context()
        context.user_data["excuse_hw_id"] = "bito-01"
        # excuse_program missing
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            with patch("handlers.student.stage_keyboard", return_value=MagicMock()):
                await _sh_excuse_typing(update, context)
        assert context.user_data.get("state") == S.MAIN
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_back_button_cancels(self):
        from handlers.student import _sh_excuse_typing
        import states as S
        update = make_update("🔙 Orqaga")
        context = make_context()
        with patch("handlers.student.stage_keyboard", return_value=MagicMock()):
            await _sh_excuse_typing(update, context)
        assert context.user_data.get("state") == S.MAIN
        update.message.reply_text.assert_called_once()


# ─── handle_hw_upload null student guard ──────────────────────────────────────

class TestHwUploadNullStudent:
    @pytest.mark.asyncio
    async def test_null_student_on_confirm_replies_and_returns(self):
        from handlers.student import handle_hw_upload
        update = make_update("✅ Tasdiqlayman")
        context = make_context()
        context.user_data["pending_files"] = [{"file_id": "x", "file_type": "text", "content": "text"}]
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            await handle_hw_upload(update, context)
        update.message.reply_text.assert_called_once()
        assert "Xatolik" in update.message.reply_text.call_args[0][0]


# ─── add_grade_feedback session expired guard ─────────────────────────────────

class TestAddGradeFeedbackSessionExpired:
    @pytest.mark.asyncio
    async def test_missing_context_ends_conversation(self):
        from handlers.admin import add_grade_feedback
        update = make_update("Yaxshi ish!")
        context = make_context()
        # grade_student / grade_hw / grade_score all missing
        result = await add_grade_feedback(update, context)
        assert result == ConversationHandler.END
        update.message.reply_text.assert_called_once()
        assert "Sessiya" in update.message.reply_text.call_args[0][0]


# ─── _handle_waiting_answer null q_id guard ───────────────────────────────────

class TestHandleWaitingAnswerNullQId:
    @pytest.mark.asyncio
    async def test_missing_q_id_clears_flag_and_replies(self):
        from handlers.admin import _handle_waiting_answer
        update = make_update("Bu javob")
        context = make_context()
        context.user_data["waiting_for_answer"] = True
        # answer_q_id not set
        with patch("handlers.admin.stage_keyboard", return_value=MagicMock()):
            await _handle_waiting_answer(update, context)
        assert context.user_data.get("waiting_for_answer") is False
        update.message.reply_text.assert_called_once()
        assert "Sessiya" in update.message.reply_text.call_args[0][0]


# ─── broadcast_target routing ─────────────────────────────────────────────────

class TestBroadcastTarget:
    @pytest.mark.asyncio
    async def test_all_target_advances_to_msg_state(self):
        from handlers.admin import broadcast_target, BROADCAST_MSG
        update = make_update("👥 Barcha talabalarga")
        context = make_context()
        result = await broadcast_target(update, context)
        assert result == BROADCAST_MSG
        assert context.user_data.get("broadcast_target") == "all"

    @pytest.mark.asyncio
    async def test_one_target_advances_to_pick_student(self):
        from handlers.admin import broadcast_target, BROADCAST_PICK_STUDENT
        update = make_update("👤 Bir talabaga")
        context = make_context()
        students = [{"FullName": "Ali", "PersonalID": "S-01", "TelegramID": "42"}]
        with patch("core.sheets.get_all_students", return_value=students):
            result = await broadcast_target(update, context)
        assert result == BROADCAST_PICK_STUDENT

    @pytest.mark.asyncio
    async def test_back_button_ends_conversation(self):
        from handlers.admin import broadcast_target
        update = make_update("🔙 Orqaga")
        context = make_context()
        result = await broadcast_target(update, context)
        assert result == ConversationHandler.END
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_text_stays_in_state(self):
        from handlers.admin import broadcast_target, BROADCAST_TARGET
        update = make_update("noma'lum")
        context = make_context()
        result = await broadcast_target(update, context)
        assert result == BROADCAST_TARGET


# ─── broadcast_send ───────────────────────────────────────────────────────────

class TestBroadcastSend:
    @pytest.mark.asyncio
    async def test_back_button_ends_conversation(self):
        from handlers.admin import broadcast_send
        update = make_update("🔙 Orqaga")
        context = make_context()
        result = await broadcast_send(update, context)
        assert result == ConversationHandler.END

    @pytest.mark.asyncio
    async def test_one_target_missing_tg_id_replies_error(self):
        from handlers.admin import broadcast_send
        update = make_update("Salom!")
        context = make_context()
        context.user_data["broadcast_target"] = "one"
        # broadcast_student_id intentionally not set
        result = await broadcast_send(update, context)
        assert result == ConversationHandler.END
        assert "Xatolik" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_all_target_sends_to_each_student(self):
        from handlers.admin import broadcast_send
        update = make_update("E'lon matni")
        context = make_context()
        context.user_data["broadcast_target"] = "all"
        students = [
            {"TelegramID": "11", "PersonalID": "S-01", "FullName": "Ali"},
            {"TelegramID": "22", "PersonalID": "S-02", "FullName": "Vali"},
        ]
        context.bot = MagicMock()
        context.bot.send_message = AsyncMock()
        with patch("core.sheets.get_all_students", return_value=students):
            with patch("core.sheets.get_blacklist", return_value=[]):
                result = await broadcast_send(update, context)
        assert result == ConversationHandler.END
        assert context.bot.send_message.call_count == 2


# ─── deadlines_view null student ──────────────────────────────────────────────

class TestDeadlinesView:
    @pytest.mark.asyncio
    async def test_null_student_replies_and_returns(self):
        from handlers.student import deadlines_view
        update = make_update()
        context = make_context()
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            await deadlines_view(update, context)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_deadlines_shows_message(self):
        from handlers.student import deadlines_view
        import states as S
        update = make_update()
        context = make_context()
        student = {"PersonalID": "S-01"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("core.sheets.get_deadline_tracking", return_value=[]):
                await deadlines_view(update, context)
        assert context.user_data.get("state") == S.DEADLINES_VIEW
        update.message.reply_text.assert_called_once()


# ─── my_history null student ──────────────────────────────────────────────────

class TestMyHistory:
    @pytest.mark.asyncio
    async def test_null_student_replies_and_returns(self):
        from handlers.student import my_history
        update = make_update()
        context = make_context()
        with patch("core.sheets.find_student_by_telegram_id", return_value=None):
            await my_history(update, context)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_history_shows_message(self):
        from handlers.student import my_history
        update = make_update()
        context = make_context()
        student = {"PersonalID": "S-01"}
        with patch("core.sheets.find_student_by_telegram_id", return_value=student):
            with patch("core.sheets.get_student_qa_history", return_value=[]):
                await my_history(update, context)
        update.message.reply_text.assert_called_once()
        assert "savol" in update.message.reply_text.call_args[0][0]
