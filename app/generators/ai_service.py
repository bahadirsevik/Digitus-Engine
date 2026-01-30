"""
Google Gemini, OpenAI ve Anthropic API'leri için birleşik wrapper.
"""
import os
import time
import random
import logging
from typing import Optional, Callable, Any
from abc import ABC, abstractmethod

import google.generativeai as genai
import google.api_core.exceptions

logger = logging.getLogger(__name__)

class AIService(ABC):
    """Abstract base class for AI services."""
    
    @abstractmethod
    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7
    ) -> str:
        """Generate a completion for the given prompt."""
        pass
    
    @abstractmethod
    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.3
    ) -> str:
        """Generate a JSON completion for the given prompt."""
        pass


class GeminiService(AIService):
    """Google Gemini API implementation."""
    
    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")
    
    def _retry_on_rate_limit(self, func: Callable, *args, **kwargs) -> Any:
        """
        Executes a function with exponential backoff for Rate Limit errors.
        """
        max_retries = 3
        base_delay = 2

        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except google.api_core.exceptions.ResourceExhausted as e:
                if attempt == max_retries:
                    logger.error(f"Gemini API rate limit exceeded after {max_retries} retries.")
                    raise e

                delay = (base_delay * (2 ** attempt)) + random.uniform(0, 1)
                logger.warning(f"Rate limit hit. Retrying in {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            except Exception as e:
                # Other errors are raised immediately
                raise e

    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7
    ) -> str:
        def _call_api():
            return self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature
                )
            )

        response = self._retry_on_rate_limit(_call_api)
        return response.text
    
    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.3
    ) -> str:
        json_prompt = f"{prompt}\n\nSADECE geçerli JSON formatında yanıt ver, başka hiçbir şey yazma."

        def _call_api():
            return self.model.generate_content(
                json_prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                    response_mime_type="application/json"
                )
            )

        response = self._retry_on_rate_limit(_call_api)
        text = response.text

        # Centralized Markdown cleanup
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].strip()

        return text


class MockAIService(AIService):
    """
    Mock AI service for testing without API calls.
    Returns deterministic responses.
    """
    
    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.7
    ) -> str:
        return "This is a mock response for testing purposes."
    
    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.3
    ) -> str:
        # Return mock intent analysis
        import json
        import re
        import random
        
        if "intent" in prompt.lower() or "niyet" in prompt.lower():
            # Extract keyword IDs from prompt
            # Pattern matches "- {id}: {keyword}" format
            ids = re.findall(r'- (\d+):', prompt)
            
            results = []
            intents = ['transactional', 'informational', 'commercial', 'navigational', 'trend_worthy']
            
            for kid in ids:
                results.append({
                    "keyword_id": int(kid),
                    "intent_type": random.choice(intents),
                    "confidence": 0.85,
                    "reasoning": "Mock AI analysis based on keyword pattern."
                })
                
            return json.dumps(results)
            
        return json.dumps({"result": "mock"})


def get_ai_service(use_mock: bool = False, api_key: Optional[str] = None) -> AIService:
    """
    Konfigürasyona göre doğru AI servisini döner.
    
    Args:
        use_mock: Test için mock servis kullan
        api_key: Opsiyonel API anahtarı (verilmezse env'den okunur)
    
    Returns:
        AI service instance
    """
    if use_mock:
        return MockAIService()
    
    # Varsayılan olarak Gemini kullan
    return GeminiService(api_key=api_key)
