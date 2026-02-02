import pytest
from unittest.mock import MagicMock
from app.core.channel.intent_analyzer import IntentAnalyzer

class TestIntentAnalyzer:
    @pytest.fixture
    def intent_analyzer(self):
        mock_db = MagicMock()
        mock_ai_service = MagicMock()
        return IntentAnalyzer(db=mock_db, ai_service=mock_ai_service)

    def test_process_intent_result_valid_high_confidence(self, intent_analyzer):
        """Test case 1: Valid intent with high confidence (>0.45)."""
        intent_result = {
            "intent_type": "transactional",
            "confidence": 0.8,
            "reasoning": "Clear intent"
        }
        accepted_intents = ["transactional", "commercial"]

        result = intent_analyzer._process_intent_result(intent_result, accepted_intents)

        assert result["intent_type"] == "transactional"
        assert result["confidence"] == 0.8
        assert result["is_passed"] is True
        assert result["reasoning"] == "Clear intent"

    def test_process_intent_result_valid_low_confidence(self, intent_analyzer):
        """Test case 2: Valid intent with low confidence (<0.45)."""
        intent_result = {
            "intent_type": "transactional",
            "confidence": 0.3,
            "reasoning": "Uncertain"
        }
        accepted_intents = ["transactional", "commercial"]

        result = intent_analyzer._process_intent_result(intent_result, accepted_intents)

        assert result["intent_type"] == "transactional"
        assert result["confidence"] == 0.3
        assert result["is_passed"] is False

    def test_process_intent_result_invalid_intent_high_confidence(self, intent_analyzer):
        """Test case 3: Invalid intent (not in allowed list) with high confidence."""
        intent_result = {
            "intent_type": "informational",
            "confidence": 0.9,
            "reasoning": "User wants info"
        }
        accepted_intents = ["transactional", "commercial"]

        result = intent_analyzer._process_intent_result(intent_result, accepted_intents)

        assert result["intent_type"] == "informational"
        assert result["is_passed"] is False

    def test_process_intent_result_missing_keys(self, intent_analyzer):
        """Test case 4: Edge cases: Missing keys in the dictionary."""
        intent_result = {}  # Empty dict
        # Default intent is 'informational'
        # If accepted_intents includes 'informational', and default confidence 0.5 > 0.45, it should pass.
        accepted_intents = ["informational"]

        result = intent_analyzer._process_intent_result(intent_result, accepted_intents)

        assert result["intent_type"] == "informational"
        assert result["confidence"] == 0.5
        assert result["is_passed"] is True

    def test_process_intent_result_confidence_as_string(self, intent_analyzer):
        """Test case 4: Edge cases: confidence as string instead of float."""
        intent_result = {
            "intent_type": "transactional",
            "confidence": "0.95"
        }
        accepted_intents = ["transactional"]

        result = intent_analyzer._process_intent_result(intent_result, accepted_intents)

        assert result["confidence"] == 0.95
        assert result["is_passed"] is True

    def test_process_intent_result_invalid_confidence_string(self, intent_analyzer):
        """Test case 4: Edge cases: invalid string confidence."""
        intent_result = {
            "intent_type": "transactional",
            "confidence": "not_a_number"
        }
        accepted_intents = ["transactional"]

        result = intent_analyzer._process_intent_result(intent_result, accepted_intents)

        # Should default to 0.5
        assert result["confidence"] == 0.5
        assert result["is_passed"] is True
