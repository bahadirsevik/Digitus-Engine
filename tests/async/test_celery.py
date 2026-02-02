import pytest
from unittest.mock import MagicMock, patch
from app.tasks.intent_tasks import run_channel_assignment_task

@patch("app.tasks.intent_tasks.SessionLocal")
@patch("app.tasks.intent_tasks.get_ai_service")
@patch("app.tasks.intent_tasks.ChannelEngine")
def test_run_channel_assignment_task_success(mock_engine_cls, mock_get_ai, mock_session_cls):
    """Test channel assignment task success path."""
    # Mock dependencies
    mock_db = MagicMock()
    mock_session_cls.return_value = mock_db

    mock_engine = mock_engine_cls.return_value
    mock_engine.run_channel_assignment.return_value = {"assigned": 10, "errors": 0}

    # Use apply() to run the task synchronously with the real task object logic
    # We need to patch update_state on the task object to verify it's called
    with patch.object(run_channel_assignment_task, 'update_state') as mock_update_state:
        # Execute
        result = run_channel_assignment_task.apply(args=[123]).result

        # Assertions
        assert result["status"] == "completed"
        assert result["scoring_run_id"] == 123
        assert result["result"]["assigned"] == 10

        # Verify calls
        mock_update_state.assert_called_with(state='PROGRESS', meta={'step': 'channel_assignment'})
        mock_engine_cls.assert_called_with(mock_db, mock_get_ai.return_value)
        mock_engine.run_channel_assignment.assert_called_with(123)
        mock_db.close.assert_called_once()

@patch("app.tasks.intent_tasks.SessionLocal")
@patch("app.tasks.intent_tasks.get_ai_service")
@patch("app.tasks.intent_tasks.ChannelEngine")
def test_run_channel_assignment_task_failure(mock_engine_cls, mock_get_ai, mock_session_cls):
    """Test channel assignment task failure path."""
    # Mock dependencies
    mock_db = MagicMock()
    mock_session_cls.return_value = mock_db

    # Simulate exception
    mock_engine = mock_engine_cls.return_value
    mock_engine.run_channel_assignment.side_effect = Exception("Analysis failed")

    # We also need to patch update_state to prevent Redis connection attempt
    with patch.object(run_channel_assignment_task, 'update_state') as mock_update_state:
        # Execute
        result = run_channel_assignment_task.apply(args=[123]).result

        # Assertions
        assert result["status"] == "failed"
        assert "Analysis failed" in result["error"]

        # Verify db closed
        mock_db.close.assert_called_once()
