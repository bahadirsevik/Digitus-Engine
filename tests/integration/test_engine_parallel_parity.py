"""V3 motor asama-ici paralellik — parite, drain ve fail-safe testleri.

plan_engine_paralellik.md §3. Saglayici SAHTE (`DeterministicAI`); gercek
Gemini cagrisi YOK. Paralel yol Redis slotu ister; testte slot alici
`nullcontext` ile degistirilir (Redis'e baglanilmaz).

Kanit yuku:
  1. Parite: seri (concurrency=1) ve paralel kosu AYNI stage payload'larini
     ve AYNI motor ciktisini uretir.
  2. Paralellik gercekten olusuyor (birden cok worker thread'i).
  3. Drain: bir batch patlayinca tamamlanmis batch'ler checkpoint'te KALIR.
  4. Fail-safe: Redis yoksa paralel istense bile SERI kosulur.
  5. Worker thread'i DB'ye DOKUNMAZ (yazim yalniz ana thread'de).
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import nullcontext
from typing import Any, Dict, List

import pytest

from app.core.engine import context as CTX
from app.core.engine import ai_runner as AI
from app.core.engine.ads.runner import (
    SCOPE_KEYWORD, STAGE_FUNNEL, STAGE_INTENT, ads_models, ads_prompt_shas,
    run_ads_stage,
)
from app.core.engine.family.rules import UNMATCHED  # noqa: F401  (kontrat)
from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import seal_manifest
from app.database.models import EngineStageResult

PROFILE = {"sector": "yatirim", "brand_name": "Digitus",
           "brand_summary": "Yatirim araclari", "products": ["hisse"]}
# FUNNEL_BATCH = INTENT_BATCH = 10 -> 25 kelime = 3 batch/asama
KEYWORD_COUNT = 25


class DeterministicAI:
    """Cevabi PROMPT'taki id'lerden turetir (kuyruk sirasi YOK).

    Kuyruk tabanli sahte istemciler paralel kosuda cevabi yanlis batch'e
    verebilirdi; bu istemci id -> cevap eslemesini deterministik kurar,
    boylece seri ve paralel kosu AYNI sonucu uretmek ZORUNDA kalir.
    """

    def __init__(self, fail_on_id: int = None) -> None:
        self.fail_on_id = fail_on_id
        self.threads: set = set()
        self.calls = 0
        self._lock = threading.Lock()

    def for_stage(self, stage: str, *, model: str = None,
                  thinking_level: str = None) -> "DeterministicAI._Scoped":
        return DeterministicAI._Scoped(self, stage)

    class _Scoped:
        def __init__(self, parent: "DeterministicAI", stage: str) -> None:
            self._parent, self._stage = parent, stage

        def complete_json(self, prompt: str, max_tokens: int = None,
                          response_schema: Dict[str, Any] = None) -> str:
            return self._parent._respond(self._stage, prompt)

    def _respond(self, stage: str, prompt: str) -> str:
        ids = [int(x) for x in re.findall(r"^(\d+):", prompt, re.MULTILINE)]
        with self._lock:
            self.calls += 1
            self.threads.add(threading.current_thread().name)
        if self.fail_on_id is not None and self.fail_on_id in ids:
            raise RuntimeError(f"sahte saglayici hatasi (id {self.fail_on_id})")
        results = []
        for kid in ids:
            if stage == STAGE_FUNNEL:
                results.append({"id": kid, "funnel": "commercial",
                                "brand_type": "yok", "relevance": 0.60})
            else:
                results.append({"id": kid, "funnel": "commercial",
                                "intent": 0.60})
        return json.dumps({"results": results}, ensure_ascii=False)


@pytest.fixture
def parallel_slots(monkeypatch):
    """Redis'e gitmeden paralel yolu acar (slot alici -> nullcontext)."""
    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: nullcontext)


def _universe(db_session, make_workspace, make_scoring_run, make_keyword,
              workspace=None):
    """AYNI kelimeler uzerinde yeni bir run: parite icin id'ler degismemeli."""
    if workspace is None:
        workspace = make_workspace()
        for i in range(KEYWORD_COUNT):
            make_keyword(f"kelime {i}", brand_profile_id=workspace.id,
                         monthly_volume=100 + i, competition_score=0.30,
                         trend_3m=10.0)
        db_session.commit()
    run = make_scoring_run(brand_profile_id=workspace.id,
                           algorithm_version="v3")
    db_session.commit()
    rows = CTX.freeze_universe_snapshot(db_session, run).rows
    db_session.commit()
    firm_sha = CTX.firm_block_sha256(CTX.firm_block(PROFILE))
    seal_manifest(run, firm_block_sha256=firm_sha,
                  algorithm_versions={"ads": "nihai_niche_v1"},
                  models=ads_models(), prompt_shas=ads_prompt_shas(), location_policy=_loc_snap({}))
    db_session.commit()
    family_by_id = {int(r.keyword_id): "F1" for r in rows}
    return run, rows, family_by_id, firm_sha, workspace


def _stage_payloads(db_session, run) -> Dict[str, Dict[str, Any]]:
    rows = (db_session.query(EngineStageResult)
            .filter(EngineStageResult.scoring_run_id == run.id,
                    EngineStageResult.scope_type == SCOPE_KEYWORD).all())
    return {f"{r.stage}:{r.scope_key}": r.payload for r in rows}


def _run_ads(db_session, make_workspace, make_scoring_run, make_keyword, *,
             concurrency: int, monkeypatch, ai: DeterministicAI,
             workspace=None):
    monkeypatch.setattr(AI, "engine_concurrency", lambda: concurrency)
    run, rows, family_by_id, firm_sha, workspace = _universe(
        db_session, make_workspace, make_scoring_run, make_keyword, workspace)
    result = run_ads_stage(db_session, run=run, profile=PROFILE, rows=rows,
                           family_by_id=family_by_id, ai=ai,
                           firm_block_sha256=firm_sha)
    db_session.commit()
    return run, result, workspace


def test_seri_ve_paralel_ayni_sonucu_uretir(
        db_session, make_workspace, make_scoring_run, make_keyword,
        monkeypatch, parallel_slots):
    seri_ai = DeterministicAI()
    run_seri, res_seri, ws = _run_ads(db_session, make_workspace,
                                      make_scoring_run, make_keyword,
                                      concurrency=1, monkeypatch=monkeypatch,
                                      ai=seri_ai)
    payload_seri = _stage_payloads(db_session, run_seri)

    par_ai = DeterministicAI()
    run_par, res_par, _ = _run_ads(db_session, make_workspace,
                                   make_scoring_run, make_keyword,
                                   concurrency=4, monkeypatch=monkeypatch,
                                   ai=par_ai, workspace=ws)
    payload_par = _stage_payloads(db_session, run_par)

    assert seri_ai.calls == par_ai.calls        # cagri sayisi degismez
    assert len(payload_seri) == len(payload_par) == KEYWORD_COUNT * 2
    # scope_key'ler farkli run'larda ayni keyword'lere karsilik gelir
    assert sorted(payload_seri.values(), key=json.dumps) == \
           sorted(payload_par.values(), key=json.dumps)
    assert json.dumps(res_seri, sort_keys=True, default=str) == \
           json.dumps(res_par, sort_keys=True, default=str)
    assert len(par_ai.threads) > 1, "paralel kosu tek thread'de kalmis"
    assert len(seri_ai.threads) == 1


def test_drain_tamamlanan_batchleri_korur(
        db_session, make_workspace, make_scoring_run, make_keyword,
        monkeypatch, parallel_slots):
    run, rows, family_by_id, firm_sha, _ws = _universe(
        db_session, make_workspace, make_scoring_run, make_keyword)
    monkeypatch.setattr(AI, "engine_concurrency", lambda: 4)
    # son batch'teki bir kelime patlar; onceki batch'ler basarili
    patlayan = int(rows[-1].keyword_id)
    with pytest.raises(RuntimeError):
        run_ads_stage(db_session, run=run, profile=PROFILE, rows=rows,
                      family_by_id=family_by_id,
                      ai=DeterministicAI(fail_on_id=patlayan),
                      firm_block_sha256=firm_sha)
    db_session.commit()
    kalan = _stage_payloads(db_session, run)
    # ucusta tamamlanan batch'ler YAZILMIS olmali (drain), hepsi degil
    assert 0 < len(kalan) < KEYWORD_COUNT * 2
    assert all(k.startswith(f"{STAGE_FUNNEL}:") for k in kalan)


def test_redis_yoksa_seri_kosulur(monkeypatch):
    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: None)
    threads: List[str] = []

    def job():
        threads.append(threading.current_thread().name)
        return 1

    AI.run_jobs([job for _ in range(4)], lambda _v: None, concurrency=8)
    assert len(set(threads)) == 1, "Redis yokken paralel kosulmus"


def test_worker_thread_db_kullanmaz(
        db_session, make_workspace, make_scoring_run, make_keyword,
        monkeypatch, parallel_slots):
    """Yazim ANA thread'de olmali: persist cagrilari ana thread'de gorunur."""
    ana = threading.current_thread().name
    gorulen: List[str] = []
    gercek = AI.run_jobs

    def izleyen(jobs, persist, **kwargs):
        def sarmal(value):
            gorulen.append(threading.current_thread().name)
            return persist(value)
        return gercek(jobs, sarmal, **kwargs)

    monkeypatch.setattr(AI, "engine_concurrency", lambda: 4)
    monkeypatch.setattr(AI, "run_jobs", izleyen)
    _run_ads(db_session, make_workspace, make_scoring_run, make_keyword,
             concurrency=4, monkeypatch=monkeypatch, ai=DeterministicAI())
    assert gorulen and set(gorulen) == {ana}


def _tracking_slots(limit: int):
    """Global slot taklidi: ayni anda en fazla `limit` is calisabilir."""
    import threading
    from contextlib import contextmanager

    state = {"now": 0, "max": 0}
    sem = threading.Semaphore(limit)
    lock = threading.Lock()

    @contextmanager
    def slot():
        sem.acquire()
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            yield
        finally:
            with lock:
                state["now"] -= 1
            sem.release()

    return slot, state


def test_per_run_limiti_thread_sayisini_sinirlar(monkeypatch):
    """concurrency=3 -> ayni anda en fazla 3 is (global slot genis olsa bile)."""
    import time

    slot, state = _tracking_slots(99)
    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: slot)

    def job():
        time.sleep(0.05)
        return 1

    AI.run_jobs([job for _ in range(12)], lambda _v: None, concurrency=3)
    assert state["max"] == 3


def test_global_limit_iki_kosuyu_birlikte_sinirlar(monkeypatch):
    """Iki es zamanli kosu global slotu PAYLASIR; toplam limit asilmaz."""
    import threading
    import time

    slot, state = _tracking_slots(2)          # global limit = 2
    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: slot)

    def job():
        time.sleep(0.05)
        return 1

    def kosu():
        AI.run_jobs([job for _ in range(8)], lambda _v: None, concurrency=4)

    threads = [threading.Thread(target=kosu) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] <= 2, "global slot limiti asildi"


def test_erken_hatada_yeni_is_baslatilmaz(monkeypatch):
    """Ilk dalgadaki bir is HEMEN patlarsa, dalga disindan is BASLAMAZ.

    Kalip: concurrency=2, 6 is. 0. is uzun surer, 1. is aninda patlar.
    Rolling gonderim olmasaydi executor kuyrugu 2-5'i baslatirdi (ucretli
    cagri israfi).
    """
    import threading
    import time

    slot, _state = _tracking_slots(99)
    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: slot)
    basladi: List[int] = []
    kilit = threading.Lock()

    def make(index: int):
        def job():
            with kilit:
                basladi.append(index)
            if index == 1:
                raise RuntimeError("erken hata")
            time.sleep(0.2)
            return index
        return job

    yazilan: List[Any] = []
    with pytest.raises(RuntimeError, match="erken hata"):
        AI.run_jobs([make(i) for i in range(6)], yazilan.append, concurrency=2)

    assert sorted(basladi) == [0, 1], f"dalga disi is basladi: {basladi}"
    assert yazilan == [0], "ucusta tamamlanan is yazilmadi (drain)"


def test_redis_erisilemezse_seri_kosulur(monkeypatch):
    """GERCEK kesinti: adres var ama servis yok -> baslangic ping'i duser."""
    import threading

    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:65530/0")
    assert AI._global_slot_acquirer() is None

    threads: List[str] = []

    def job():
        threads.append(threading.current_thread().name)
        return 1

    AI.run_jobs([job for _ in range(4)], lambda _v: None, concurrency=8)
    assert len(set(threads)) == 1, "Redis erisilemezken paralel kosulmus"
