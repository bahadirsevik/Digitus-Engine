"""K17 ortak AI cagri katmani testleri (`app/core/engine/ai_runner.py`).

Sozlesme (K17, plan_algoritma_entegrasyonu.md): uretimde her LLM asamasi TEK
GECISTIR; yalniz kilitli teknik tekrar kurallari uygulanir:
  * parse/batch/SAGLAYICI hatasinda TOPLAM en fazla `MAX_ATTEMPTS` (=2)
    deneme; ikinci hatadan sonra enjekte edilen `error_cls`, son hata
    `__cause__` olacak sekilde yukseltilir,
  * `BudgetExceeded` (`app.core.telemetry.ai_cost_budget`) ASLA tekrar
    EDILMEZ — dogrudan yeniden yukseltilir,
  * eksik ID'ler icin YALNIZ eksik kumesine `TARGETED_RETRIES` (=1) hedefli
    tekrar,
  * fazladan/bilinmeyen ID YAPISAL HATADIR,
  * hedefli tekrardan sonra hala eksik ID varsa `error_cls` firlatilir —
    UNMATCHED yazilmaz, checkpoint OLUSTURULMAZ.

Bu dosyanin sonunda BIR entegrasyon testi var: Family V2 runner'inin bir
asamasi (A2) hedefli tekrardan sonra hala eksik ID donerse
`engine_stage_results`'a O ASAMADAN hic satir yazilmadigini DB sorgusuyla
kanitlar (yarim checkpoint yok).

Ucretli saglayici cagrisi YOK: `QueueAI` gercek saglayiciya HICBIR zaman
gitmez, sirayla hazir HAM STRING cevap (veya kuyruga konulmus bir istisna
NESNESI — saglayici/transport hatasini simule etmek icin) dondurur.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List

import pytest

from app.core.engine import ai_runner as AI
from app.core.engine.ai_runner import (
    MAX_ATTEMPTS,
    TARGETED_RETRIES,
    AiStageError,
    run_batch,
    run_single,
)

DUMMY_SCHEMA: Dict[str, Any] = {"type": "object"}

# Gecerli JSON ama beklenen anahtari TASIMIYOR -> `_parse` TEMIZ None doner.
WRONG_KEY_JSON = json.dumps({"unrelated": True})

# Gercekci kirpilma: Gemini'nin bir string degerin ORTASINDA kesildigi
# senaryo — `parse_ai_json_object` hicbir sey kurtaramayinca kendi
# `AIJsonParseError`'ini firlatir; `_parse` artik bunu YAKALAYIP `None`a
# cevirir (duzeltilen kusur, bkz. asagidaki testler).
TRUNCATED_JSON = '{"results": [{"id": 1, "family_id": "af'


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


class _SamplingCaptureRoot:
    def __init__(self) -> None:
        self.temperature = "not-called"

    def _execute(self, prompt, *, max_tokens, temperature, json_mode,
                 response_schema, stage, model=None, thinking_level=None,
                 thinking_budget=None, provider=None):
        self.temperature = temperature
        return "{}"


def test_gemini_38_engine_call_omits_sampling_temperature():
    """V3 Gemini 3.8 cagrisi genel 0.3 varsayilanini wire'a tasimaz."""
    from app.generators.ai_service import StageScopedAIService

    root = _SamplingCaptureRoot()
    scoped = StageScopedAIService(
        root, "seo_rel", model="gemini-3.8-flash", thinking_level="low")

    AI._complete(scoped, "prompt", 100, DUMMY_SCHEMA)

    assert root.temperature is None


def test_legacy_engine_model_keeps_complete_json_default_temperature():
    """Gemini 3.8 disindaki mevcut yollarin 0.3 davranisi degismez."""
    from app.generators.ai_service import StageScopedAIService

    root = _SamplingCaptureRoot()
    scoped = StageScopedAIService(
        root, "legacy", model="gemini-3.5-flash", thinking_level="minimal")

    AI._complete(scoped, "prompt", 100, DUMMY_SCHEMA)

    assert root.temperature == pytest.approx(0.3)


def _json_array(items: List[Dict[str, Any]]) -> str:
    """Ust duzey DIZI cevap (SOCIAL V4/V5 semasi — `{"results":...}` sarmali
    YOK)."""
    return json.dumps(items, ensure_ascii=False)


class _ScopedQueueAI:
    def __init__(self, parent: "QueueAI", stage: str) -> None:
        self._parent = parent
        self._stage = stage

    def complete_json(self, prompt: str, max_tokens: int = None,
                       response_schema: Dict[str, Any] = None) -> str:
        return self._parent._respond(self._stage, prompt)


class QueueAI:
    """`for_stage(...).complete_json(...)` cagrilarini SAYAN + sirayla hazir
    cevap dondüren sahte istemci. Gercek saglayiciya ASLA gitmez.

    Kuyruktaki bir oge `BaseException` ORNEGIYSE (ör. `ConnectionError(...)`,
    `BudgetExceeded(...)`), saglayici/transport hatasini simule etmek icin
    OLDUGU GIBI firlatilir; aksi halde HAM STRING cevap olarak dondurulur.
    """

    def __init__(self, stage_responses: Dict[str, List[Any]]) -> None:
        self._queues: Dict[str, List[Any]] = {
            stage: list(payloads) for stage, payloads in stage_responses.items()
        }
        self.calls: List[tuple] = []
        self.call_counts: Counter = Counter()

    def for_stage(self, stage: str, *, model: str = None,
                  thinking_level: str = None) -> "_ScopedQueueAI":
        return _ScopedQueueAI(self, stage)

    def _respond(self, stage: str, prompt: str) -> str:
        self.calls.append((stage, prompt))
        self.call_counts[stage] += 1
        queue = self._queues.get(stage)
        if not queue:
            raise AssertionError(
                f"QueueAI: '{stage}' icin kuyrukta hazir cevap kalmadi "
                f"(bu {self.call_counts[stage]}. cagri) — GERCEK saglayiciya "
                "gidilmeye CALISILIYOR olabilir, bu test bunu engeller."
            )
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


# ---------------------------------------------------------------------------
# run_batch — parse hatasi / kurtarma
# ---------------------------------------------------------------------------


def test_run_batch_parse_failure_retries_exactly_max_attempts_then_raises():
    ai = QueueAI({"stage_x": [WRONG_KEY_JSON, WRONG_KEY_JSON]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    with pytest.raises(AiStageError, match="kirpik/bozuk"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        )

    assert ai.call_counts["stage_x"] == MAX_ATTEMPTS == 2


def test_run_batch_recovers_when_second_attempt_parses_correctly():
    good = _ok({"results": [{"id": 1, "family_id": "aile", "confidence": "high"}]})
    ai = QueueAI({"stage_x": [WRONG_KEY_JSON, good]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
    )

    assert result == {1: {"id": 1, "family_id": "aile", "confidence": "high"}}
    assert ai.call_counts["stage_x"] == 2


def test_run_batch_missing_id_triggers_single_targeted_retry_with_only_missing_rows():
    rows = [{"keyword_id": 1, "keyword_text": "a"},
            {"keyword_id": 2, "keyword_text": "b"},
            {"keyword_id": 3, "keyword_text": "c"}]
    # Ilk cevap TAM olarak gecerli JSON ama id=3 EKSIK.
    first = _ok({"results": [
        {"id": 1, "family_id": "x", "confidence": "high"},
        {"id": 2, "family_id": "y", "confidence": "high"},
    ]})
    # Hedefli tekrar: yalniz eksik id=3 icin cevap.
    second = _ok({"results": [{"id": 3, "family_id": "z", "confidence": "medium"}]})
    ai = QueueAI({"stage_x": [first, second]})

    captured_subsets: List[List[int]] = []

    def build_prompt(subset):
        captured_subsets.append([r["keyword_id"] for r in subset])
        return "prompt-for-" + ",".join(str(r["keyword_id"]) for r in subset)

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=build_prompt, schema=DUMMY_SCHEMA, max_tokens=100,
    )

    assert set(result) == {1, 2, 3}
    assert result[3]["family_id"] == "z"
    assert ai.call_counts["stage_x"] == 2
    # Retry prompt'una giden satirlarin TAM OLARAK eksikler oldugunu kanitla.
    assert captured_subsets == [[1, 2, 3], [3]]


def test_run_batch_still_missing_after_targeted_retry_raises_and_checkpoint_note_present():
    class CustomStageError(RuntimeError):
        """error_cls enjeksiyonunun gercekten kullanildigini kanitlamak icin."""

    rows = [{"keyword_id": 1, "keyword_text": "a"},
            {"keyword_id": 2, "keyword_text": "b"}]
    first = _ok({"results": [{"id": 1, "family_id": "x", "confidence": "high"}]})
    # Hedefli tekrar da id=2'yi HALA getirmiyor.
    second = _ok({"results": []})
    ai = QueueAI({"stage_x": [first, second]})

    with pytest.raises(CustomStageError, match="checkpoint olusturulmaz"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
            error_cls=CustomStageError,
        )

    # TOPLAM 2 cagri: 1 tam batch + 1 hedefli tekrar (TARGETED_RETRIES=1).
    assert ai.call_counts["stage_x"] == 1 + TARGETED_RETRIES == 2


def test_run_batch_unexpected_id_in_every_attempt_raises_structural_error():
    rows = [{"keyword_id": 1, "keyword_text": "a"}]
    # id=99 istenmedi — fazladan/bilinmeyen ID sessizce YOK SAYILMAZ.
    bad = _ok({"results": [
        {"id": 1, "family_id": "x", "confidence": "high"},
        {"id": 99, "family_id": "y", "confidence": "high"},
    ]})
    ai = QueueAI({"stage_x": [bad, bad]})

    with pytest.raises(AiStageError, match=r"istenmeyen id 99"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        )
    assert ai.call_counts["stage_x"] == MAX_ATTEMPTS


def test_run_batch_recovers_when_retry_fixes_unexpected_id():
    """Yakin-ikiz kelimelerde tek ID kaymasi kosuyu OLDURMEZ: cevap butunuyle
    reddedilir, ayni batch yeniden sorulur, ilk cevaptan hicbir sey alinmaz."""
    rows = [{"keyword_id": 1, "keyword_text": "a"},
            {"keyword_id": 2, "keyword_text": "b"}]
    bad = _ok({"results": [
        {"id": 1, "family_id": "BAD", "confidence": "high"},
        {"id": 99, "family_id": "y", "confidence": "high"},
    ]})
    good = _ok({"results": [
        {"id": 1, "family_id": "x", "confidence": "high"},
        {"id": 2, "family_id": "y", "confidence": "high"},
    ]})
    ai = QueueAI({"stage_x": [bad, good]})

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
    )

    assert result[1]["family_id"] == "x"
    assert set(result) == {1, 2}
    assert ai.call_counts["stage_x"] == 2


def test_run_batch_non_numeric_id_in_every_attempt_raises():
    rows = [{"keyword_id": 1, "keyword_text": "a"}]
    bad = _ok({"results": [{"id": "abc", "family_id": "x", "confidence": "high"}]})
    ai = QueueAI({"stage_x": [bad, bad]})

    with pytest.raises(AiStageError, match="sayisal olmayan id"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        )
    assert ai.call_counts["stage_x"] == MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# run_single — ID'siz asama (A1 / A2B)
# ---------------------------------------------------------------------------


def test_run_single_returns_items_on_first_success():
    good = _ok({"families": [{"family_id": "x"}]})
    ai = QueueAI({"stage_y": [good]})

    items = run_single(
        ai, stage="stage_y", model="m", thinking_level="minimal", prompt="p",
        schema=DUMMY_SCHEMA, max_tokens=100, result_key="families",
    )

    assert items == [{"family_id": "x"}]
    assert ai.call_counts["stage_y"] == 1


def test_run_single_recovers_when_second_attempt_parses_correctly():
    good = _ok({"families": [{"family_id": "x"}]})
    ai = QueueAI({"stage_y": [WRONG_KEY_JSON, good]})

    items = run_single(
        ai, stage="stage_y", model="m", thinking_level="minimal", prompt="p",
        schema=DUMMY_SCHEMA, max_tokens=100, result_key="families",
    )

    assert items == [{"family_id": "x"}]
    assert ai.call_counts["stage_y"] == 2


def test_run_single_parse_failure_retries_then_raises_after_max_attempts():
    ai = QueueAI({"stage_y": [WRONG_KEY_JSON, WRONG_KEY_JSON]})

    with pytest.raises(AiStageError, match="kirpik/bozuk"):
        run_single(
            ai, stage="stage_y", model="m", thinking_level="minimal", prompt="p",
            schema=DUMMY_SCHEMA, max_tokens=100, result_key="families",
        )

    assert ai.call_counts["stage_y"] == MAX_ATTEMPTS == 2


# ---------------------------------------------------------------------------
# DUZELTME dogrulamasi: kirpik/truncated JSON artik NORMAL MAX_ATTEMPTS
# tekrar dongusune duser (once `_parse` `AIJsonParseError`'i yakalamiyordu,
# ham hata 1. denemede disari sizardi — o kusur duzeltildi, burada YENI
# davranis dogrulanir).
# ---------------------------------------------------------------------------


def test_run_batch_truncated_json_falls_into_retry_contract_and_raises_injected_error_cls():
    """DUZELTILDI: `_parse`, `parse_ai_json_object`'in firlattigi
    `AIJsonParseError`'i artik YAKALIYOR ve `None`a ceviriyor. Gercekci bir
    "token akisi ortasinda kesildi" kirpilmasi (bir string degerin ortasinda
    kesilen JSON) da normal `MAX_ATTEMPTS` tekrar dongusune duser; TOPLAM 2
    deneme sonrasi ham `AIJsonParseError` DEGIL, enjekte edilen `error_cls`
    firlatilir."""
    class CustomStageError(RuntimeError):
        pass

    ai = QueueAI({"stage_x": [TRUNCATED_JSON, TRUNCATED_JSON]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    with pytest.raises(CustomStageError, match="kirpik/bozuk"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
            error_cls=CustomStageError,
        )

    assert ai.call_counts["stage_x"] == MAX_ATTEMPTS == 2


def test_run_batch_recovers_when_first_attempt_is_truncated_json():
    good = _ok({"results": [{"id": 1, "family_id": "aile", "confidence": "high"}]})
    ai = QueueAI({"stage_x": [TRUNCATED_JSON, good]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
    )

    assert result == {1: {"id": 1, "family_id": "aile", "confidence": "high"}}
    assert ai.call_counts["stage_x"] == 2


def test_run_single_truncated_json_falls_into_retry_contract_and_raises_injected_error_cls():
    """Ayni duzeltme A1/A2B yolunda (`run_single`) da gecerli."""
    class CustomStageError(RuntimeError):
        pass

    ai = QueueAI({"stage_y": [TRUNCATED_JSON, TRUNCATED_JSON]})

    with pytest.raises(CustomStageError, match="kirpik/bozuk"):
        run_single(
            ai, stage="stage_y", model="m", thinking_level="minimal", prompt="p",
            schema=DUMMY_SCHEMA, max_tokens=100, result_key="families",
            error_cls=CustomStageError,
        )

    assert ai.call_counts["stage_y"] == MAX_ATTEMPTS == 2


def test_run_single_recovers_when_first_attempt_is_truncated_json():
    good = _ok({"families": [{"family_id": "x"}]})
    ai = QueueAI({"stage_y": [TRUNCATED_JSON, good]})

    items = run_single(
        ai, stage="stage_y", model="m", thinking_level="minimal", prompt="p",
        schema=DUMMY_SCHEMA, max_tokens=100, result_key="families",
    )

    assert items == [{"family_id": "x"}]
    assert ai.call_counts["stage_y"] == 2


# ---------------------------------------------------------------------------
# Saglayici/transport hatasi da MAX_ATTEMPTS sozlesmesine dahil; ikinci
# hatadan sonra enjekte edilen error_cls, son hata __cause__ olacak sekilde
# yukseltilir. `BudgetExceeded` ISTISNADIR: ASLA tekrar edilmez.
# ---------------------------------------------------------------------------


def test_run_batch_recovers_after_single_provider_error():
    good = _ok({"results": [{"id": 1, "family_id": "aile", "confidence": "high"}]})
    ai = QueueAI({"stage_x": [ConnectionError("saglayici zaman asimi"), good]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
    )

    assert result == {1: {"id": 1, "family_id": "aile", "confidence": "high"}}
    assert ai.call_counts["stage_x"] == 2


def test_run_batch_two_provider_errors_raises_injected_error_cls_with_chained_cause():
    class CustomStageError(RuntimeError):
        pass

    first_error = ConnectionError("ilk saglayici hatasi")
    second_error = TimeoutError("ikinci saglayici hatasi")
    ai = QueueAI({"stage_x": [first_error, second_error]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    with pytest.raises(CustomStageError, match="saglayici") as excinfo:
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
            error_cls=CustomStageError,
        )

    assert excinfo.value.__cause__ is second_error
    assert ai.call_counts["stage_x"] == 2


def test_run_batch_budget_exceeded_propagates_immediately_without_retry():
    """`BudgetExceeded` ASLA tekrar EDILMEZ: error_cls'e SARILMAZ, oldugu
    gibi disari verilir; sahte istemcinin cagri sayaci TAM OLARAK 1'dir
    (hic tekrar denenmemistir — tekrar denemek tavani asan harcamayi
    surdururdu)."""
    from app.core.telemetry.ai_cost_budget import BudgetExceeded

    ai = QueueAI({"stage_x": [BudgetExceeded("onayli cap asilirdi")]})
    rows = [{"keyword_id": 1, "keyword_text": "a"}]

    with pytest.raises(BudgetExceeded):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        )

    assert ai.call_counts["stage_x"] == 1


# ---------------------------------------------------------------------------
# DUZELTME dogrulamasi (SOCIAL V4/V5 kusuru): `result_key`/`response_id_field`
# parametrizasyonu. `result_key=None` ise cevabin KENDISI ust duzey bir
# DIZIDIR (`{"results":...}` sarmali YOK — SOCIAL V4/V5'in GERCEK semasi);
# `response_id_field` cevap OGESINDEKI id alanini kontrol eder, varsayilani
# "id"dir ve ADS/SEO/Family sozlesmesini BOZMAZ.
# ---------------------------------------------------------------------------


def test_run_batch_result_key_none_parses_bare_top_level_array():
    """SOCIAL V4/V5 semasi: `{"results": [...]}` sarmali YOK, cevabin
    KENDISI dizidir; id alani `keyword_id`dir."""
    rows = [{"keyword_id": 1, "keyword_text": "a"},
            {"keyword_id": 2, "keyword_text": "b"}]
    bare_array = _json_array([
        {"keyword_id": 1, "brand_contentability": 70},
        {"keyword_id": 2, "brand_contentability": 40},
    ])
    ai = QueueAI({"stage_x": [bare_array]})

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        result_key=None, response_id_field="keyword_id",
    )

    assert result == {1: {"keyword_id": 1, "brand_contentability": 70},
                      2: {"keyword_id": 2, "brand_contentability": 40}}
    assert ai.call_counts["stage_x"] == 1


def test_run_batch_result_key_results_still_parses_wrapped_object_by_default():
    """Varsayilan sozlesme (ADS/SEO/Family) DEGISMEDI: `{"results": [{"id":
    ...}]}` sarmali hala calisir — `result_key` parametresi omit edilebilir."""
    rows = [{"keyword_id": 1, "keyword_text": "a"}]
    wrapped = _ok({"results": [{"id": 1, "family_id": "x", "confidence": "high"}]})
    ai = QueueAI({"stage_x": [wrapped]})

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
    )

    assert result == {1: {"id": 1, "family_id": "x", "confidence": "high"}}
    assert ai.call_counts["stage_x"] == 1


def test_run_batch_response_id_field_default_id_behavior_unbroken():
    rows = [{"keyword_id": 1, "keyword_text": "a"}]
    wrapped = _ok({"results": [{"id": 1, "family_id": "x", "confidence": "high"}]})
    ai = QueueAI({"stage_x": [wrapped]})

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        response_id_field="id",  # ACIKCA varsayilanla ayni deger — regresyon ipucu
    )
    assert result == {1: {"id": 1, "family_id": "x", "confidence": "high"}}


def test_run_batch_response_id_field_keyword_id_reads_keyword_id_not_id():
    """`response_id_field='keyword_id'` iken cevap ogesinde literal `id`
    alani YOKTUR; yalniz `keyword_id` okunur (SOCIAL V4/V5'in gercek alani —
    `id_field` parametresi yalniz `rows` tarafini kontrol eder, bu AYRI bir
    parametredir)."""
    rows = [{"keyword_id": 5, "keyword_text": "a"}]
    bare_array = _json_array([{"keyword_id": 5, "relative_fit": 90}])
    ai = QueueAI({"stage_x": [bare_array]})

    result = run_batch(
        ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
        build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        result_key=None, response_id_field="keyword_id",
    )
    assert result == {5: {"keyword_id": 5, "relative_fit": 90}}


# ---------------------------------------------------------------------------
# Ayni cevapta TEKRAR EDEN id — yapisal hata (eskiden sessizce "sonuncu
# kazanir" idi; artik reddediliyor, checkpoint yazilmaz)
# ---------------------------------------------------------------------------


def test_run_batch_duplicate_id_in_bare_array_response_raises_structural_error():
    rows = [{"keyword_id": 1, "keyword_text": "a"}]
    duplicated = _json_array([
        {"keyword_id": 1, "brand_contentability": 70},
        {"keyword_id": 1, "brand_contentability": 99},   # AYNI id IKINCI kez
    ])
    ai = QueueAI({"stage_x": [duplicated, duplicated]})

    with pytest.raises(AiStageError, match=r"tekrar eden id 1"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
            result_key=None, response_id_field="keyword_id",
        )
    # Fonksiyon FIRLATTI — hicbir sozluk cagirana DONMEDI; cagiran taraf
    # (social/runner.py::_stage) bu yuzden write_stage_results'i HIC
    # cagiramaz, yarim checkpoint OLUSMAZ.
    assert ai.call_counts["stage_x"] == MAX_ATTEMPTS


def test_run_batch_duplicate_id_also_rejected_in_default_results_wrapped_contract():
    """Ayni koruma varsayilan (ADS/SEO) sozlesmede de gecerli — SOCIAL'a
    OZGU degil, K17 katmaninin GENEL kurali."""
    rows = [{"keyword_id": 1, "keyword_text": "a"}]
    duplicated = _ok({"results": [
        {"id": 1, "family_id": "x", "confidence": "high"},
        {"id": 1, "family_id": "y", "confidence": "low"},
    ]})
    ai = QueueAI({"stage_x": [duplicated, duplicated]})

    with pytest.raises(AiStageError, match=r"tekrar eden id 1"):
        run_batch(
            ai, stage="stage_x", model="m", thinking_level="minimal", rows=rows,
            build_prompt=lambda subset: "p", schema=DUMMY_SCHEMA, max_tokens=100,
        )


# ---------------------------------------------------------------------------
# Entegrasyon: Family V2 runner'da hedefli tekrar sonrasi HALA eksikse
# o asamadan HICBIR satir yazilmadigini DB'den kanitla (yarim checkpoint yok)
# ---------------------------------------------------------------------------


@dataclass
class Row:
    keyword_id: int
    keyword_text: str


def test_family_a2_writes_no_rows_when_targeted_retry_still_missing_ids(
    db_session, make_workspace, make_scoring_run,
):
    from app.core.engine.family import rules as R
    from app.core.engine.family.runner import (
        STAGE_A1,
        STAGE_A2,
        family_models,
        family_prompt_shas,
        run_family_stage,
    )
    from app.core.engine.persistence import seal_manifest
    from app.core.policy.location_policy import policy_snapshot as _loc_snap
    from app.database.models import EngineStageResult

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    firm_sha = "firm-sha-k17-test"
    seal_manifest(
        run, firm_block_sha256=firm_sha, algorithm_versions={},
        models=family_models(), prompt_shas=family_prompt_shas(),
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    rows = [Row(1, "kelime bir"), Row(2, "kelime iki")]

    a1 = _ok({"families": [{
        "family_id": "aile_x", "family_name": "Aile X", "core_need": "n",
        "solution_type": "s", "entity": "e",
        "examples": ["kelime bir", "kelime iki"], "do_not_confuse": [],
    }]})
    # A2 ilk cevap: yalniz id=1. Hedefli tekrar de id=2'yi getirmiyor.
    a2_first = _ok({"results": [{"id": 1, "family_id": "aile_x", "confidence": "high"}]})
    a2_retry = _ok({"results": []})

    ai = QueueAI({STAGE_A1: [a1], STAGE_A2: [a2_first, a2_retry]})

    with pytest.raises(R.FamilyStageError, match="checkpoint olusturulmaz"):
        run_family_stage(
            db_session, run=run, profile={"sector": "test"}, rows=rows, ai=ai,
            firm_block_sha256=firm_sha,
        )

    a2_rows = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_A2)
        .all()
    )
    assert a2_rows == [], "A2 basarisiz oldugu halde satir yazilmis — yarim checkpoint"

    # A1 kendi icinde basariyla tamamlanmisti — o asamanin satiri KORUNUR.
    a1_rows = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id,
                EngineStageResult.stage == STAGE_A1)
        .all()
    )
    assert len(a1_rows) == 1
