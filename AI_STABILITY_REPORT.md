# AI Stability & Robustness Report

## Executive Summary

This report analyzes the current implementation of Google Gemini AI integrations within `app/core` and `app/generators`.

**Current Status:** **High Risk**

The system is currently vulnerable to API failures. While `IntentAnalyzer` implements basic error handling and fallbacks, critical generation components (`AdsGenerator`, `SEOGeoGenerator`) lack any resilience mechanisms. Crucially, there is **no retry logic** for Rate Limit (HTTP 429) errors across the entire application, meaning transient API spikes will cause immediate task failures or degraded data quality.

---

## Code Analysis

### 1. `app/generators/ai_service.py` (Core Wrapper)
*   **Current State:** A direct wrapper around `google.generativeai`.
*   **Missing Features:**
    *   **No Retry Logic:** The `complete` and `complete_json` methods make a single API call. If the Google API returns a 429 (Resource Exhausted) or 503 (Service Unavailable), the exception propagates immediately.
    *   **No Validation:** `complete_json` requests JSON MIME type but returns raw text without validating if it is parseable JSON.

### 2. `app/generators/ads/ads_generator.py` & `app/generators/seo_geo/seo_geo_generator.py`
*   **Current State:** These classes consume `ai_service` and assume perfect success.
*   **Vulnerability:**
    *   `response = self.ai.complete_json(prompt)`
    *   `return json.loads(response)`
*   **Risk:** If `complete_json` fails (raises Exception), the process crashes. If it returns malformed JSON (e.g., includes Markdown backticks despite the prompt instructions), `json.loads` raises `JSONDecodeError`, crashing the entire Celery task (`generation_tasks.py`).

### 3. `app/core/channel/intent_analyzer.py`
*   **Current State:** Implements a "Batch Analysis" approach with error handling.
*   **Strengths:**
    *   Uses a `try/except` block to catch `json.JSONDecodeError` and generic `Exception`.
    *   Implements a markdown cleaning routine (`if "```json" in content...`).
    *   Provides a **Fallback Mechanism**: If the API fails, it assigns a default `intent_type` based on the channel configuration.
*   **Weaknesses:**
    *   **No Backoff:** It does not retry on failure. A simple network blip or rate limit triggers the fallback immediately, potentially degrading the quality of the "Keyword Analysis" unnecessarily.

---

## Identified Risks

### 1. Rate Limiting (HTTP 429 / ResourceExhausted)
*   **Scenario:** Google API limits are reached (e.g., 60 requests/minute for free tier).
*   **Impact:**
    *   **Generators:** Immediate crash of `generate_ads_content_task` or `generate_seo_content_task`. No content is generated.
    *   **Intent Analysis:** Immediate fallback to default values. While the process continues, the data quality is compromised (all keywords marked as 'informational' or 'transactional' blindly).

### 2. Malformed Responses
*   **Scenario:** AI returns text like "Here is the JSON: ```json {...}```" or an error message "I cannot generate that".
*   **Impact:**
    *   **Generators:** `json.loads` fails. Task crashes.
    *   **Intent Analysis:** Handled by cleaning logic and fallback.

---

## Recommendations

### 1. Implement Retry with Exponential Backoff
Modify `app/generators/ai_service.py` or use a decorator library like `tenacity` to handle transient errors automatically.

**Proposed Implementation:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import google.api_core.exceptions

class GeminiService(AIService):
    # ...

    @retry(
        retry=retry_if_exception_type(google.api_core.exceptions.ResourceExhausted),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    def complete(self, ...):
        # ...
```

### 2. Centralize Response Parsing & Validation
Move the JSON cleaning logic (currently only in `IntentAnalyzer`) into `GeminiService.complete_json`. This ensures all consumers benefit from markdown stripping.

**Proposed Implementation:**
```python
    def complete_json(self, ...):
        # ... api call ...
        text = response.text
        # Clean markdown
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].strip()
        return text
```

### 3. Add Graceful Degradation in Generators
Update `AdsGenerator` and `SEOGeoGenerator` to handle failures for specific items without crashing the whole batch.

**Proposed Implementation:**
```python
    def generate_responsive_search_ad(self, ...):
        try:
            # ... generation logic ...
            return json.loads(response)
        except Exception as e:
            # Log error
            return {
                "error": str(e),
                "headlines": ["Fallback Headline"],
                "descriptions": ["Fallback Description"]
            }
```

### 4. Circuit Breaker (Advanced)
If errors persist (e.g., quota drained for the day), stop calling the API for a set duration to prevent wasting resources and filling logs with errors.
