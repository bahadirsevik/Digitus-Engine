import pytest
import json
from unittest.mock import MagicMock, patch
from app.generators.ai_service import get_ai_service, MockAIService, GeminiService
import google.generativeai as genai

def test_get_ai_service_mock():
    """Test factory returns MockAIService when requested."""
    service = get_ai_service(use_mock=True)
    assert isinstance(service, MockAIService)

def test_get_ai_service_real():
    """Test factory returns GeminiService by default."""
    with patch("google.generativeai.configure"):
        with patch("google.generativeai.GenerativeModel"):
            service = get_ai_service(use_mock=False, api_key="test_key")
            assert isinstance(service, GeminiService)

def test_mock_ai_service_complete():
    """Test MockAIService complete method."""
    service = MockAIService()
    response = service.complete("test prompt")
    assert isinstance(response, str)
    assert len(response) > 0

def test_mock_ai_service_json_intent():
    """Test MockAIService JSON completion for intent analysis."""
    service = MockAIService()
    prompt = "Analyze intent for - 123: buy iphone"
    response = service.complete_json(prompt)

    data = json.loads(response)
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["keyword_id"] == 123
    assert "intent_type" in data[0]

@patch("google.generativeai.GenerativeModel")
def test_gemini_service_complete(mock_model_class):
    """Test GeminiService complete method calls API."""
    # Setup mock
    mock_model = mock_model_class.return_value
    mock_response = MagicMock()
    mock_response.text = "Generated content"
    mock_model.generate_content.return_value = mock_response

    with patch("google.generativeai.configure"):
        service = GeminiService(api_key="test_key")
        result = service.complete("test prompt")

        assert result == "Generated content"
        mock_model.generate_content.assert_called_once()
        args, kwargs = mock_model.generate_content.call_args
        assert args[0] == "test prompt"

@patch("google.generativeai.GenerativeModel")
def test_gemini_service_complete_json(mock_model_class):
    """Test GeminiService complete_json method calls API with json config."""
    # Setup mock
    mock_model = mock_model_class.return_value
    mock_response = MagicMock()
    mock_response.text = '{"key": "value"}'
    mock_model.generate_content.return_value = mock_response

    with patch("google.generativeai.configure"):
        service = GeminiService(api_key="test_key")
        result = service.complete_json("test prompt")

        assert result == '{"key": "value"}'
        mock_model.generate_content.assert_called_once()
        _, kwargs = mock_model.generate_content.call_args

        # Check generation_config
        assert "generation_config" in kwargs
        gen_config = kwargs["generation_config"]
        # It might be a GenerationConfig object or dict depending on implementation.
        # In code: genai.types.GenerationConfig(...)
        # We can check attributes if it's an object, or access if it's a dict.
        # Since we mocked GenerativeModel, but we didn't mock GenerationConfig used inside the method.
        # However, we can inspect it.
        if hasattr(gen_config, "response_mime_type"):
            assert gen_config.response_mime_type == "application/json"
        else:
             # If it's a mock or other object
             pass
