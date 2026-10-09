"""V3 motor: TAM orkestrasyon paritesi + resume + butce sozlesmesi.

plan_engine_paralellik.md §3 kabul testleri (ADS-only pariteden FARKLI:
burada family + ADS + SEO + SOCIAL zinciri UCTAN UCA kosar ve teslimat
katmani -- `EngineSelection` + `ChannelPool` -- karsilastirilir).

Saglayici SAHTE: cevap PROMPT'taki id'lerden deterministik turetilir, kuyruk
sirasina BAGLI DEGILDIR (paralelde sira degisir, sonuc degismemelidir).
Gercek Gemini cagrisi YOK.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import nullcontext
from typing import Any, Dict, List

import pytest

from app.core.engine import ai_runner as AI
from app.core.engine.context import freeze_universe_snapshot
from app.core.engine.orchestrator import run_v3_orchestration
from app.core.engine.persistence import (
    load_channel_pools, load_engine_selections,
)
from app.core.telemetry.ai_cost_budget import BudgetExceeded

FAMILY_ID = "aile_genel"


def _setup_workspace(make_workspace):
    ws = make_workspace()
    ws.status = "confirmed"
    ws.policy_version = 10
    ws.strategy_version = 5
    ws.anchor_version = 7
    ws.channel_strategy = {"status": "approved",
                           "product_definition": "Otonom AI Platformu",
                           "content_strategy": "Teknik rehber",
                           "social_mode": "hype"}
    ws.profile_data = {"company_name": "Antigravity Tech",
                       "brand_terms": ["antigravity"], "sector": "Yapay Zeka"}
    return ws


class OrchestrationAI:
    """Butun v3 asamalarina deterministik, sema-uyumlu cevap uretir."""

    def __init__(self) -> None:
        self.threads: set = set()
        self.calls: Dict[str, int] = {}
        self._lock = threading.Lock()

    def for_stage(self, stage: str, *, model: str = None,
                  thinking_level: str = None) -> "OrchestrationAI._Scoped":
        return OrchestrationAI._Scoped(self, stage)

    class _Scoped:
        def __init__(self, parent: "OrchestrationAI", stage: str) -> None:
            self._parent, self._stage = parent, stage

        def complete_json(self, prompt: str, max_tokens: int = None,
                          response_schema: Dict[str, Any] = None) -> str:
            return self._parent._respond(self._stage, prompt)

    def _respond(self, stage: str, prompt: str) -> str:
        ids = [int(x) for x in re.findall(r"^(\d+):", prompt, re.MULTILINE)]
        with self._lock:
            self.calls[stage] = self.calls.get(stage, 0) + 1
            self.threads.add(threading.current_thread().name)
        return json.dumps(self._payload(stage, ids), ensure_ascii=False)

    def _payload(self, stage: str, ids: List[int]) -> Dict[str, Any]:
        if stage == "family_a1":
            return {"families": [{
                "family_id": FAMILY_ID, "family_name": "Genel Aile",
                "core_need": "yapay zeka rehberi", "solution_type": "icerik",
                "entity": "ai", "examples": ["ai rehberi"],
                "do_not_confuse": []}]}
        if stage == "family_a2b":
            return {"new_families": []}
        if stage in ("family_a2", "family_a2c"):
            return {"results": [{"id": i, "family_id": FAMILY_ID,
                                 "confidence": "high"} for i in ids]}
        if stage == "family_a3":
            return {"results": [{"id": i, "family_id": FAMILY_ID,
                                 "reason": "onaylandi"} for i in ids]}
        if stage == "ads_funnel":
            return {"results": [{"id": i, "funnel": "commercial",
                                 "brand_type": "yok", "relevance": 0.85}
                                for i in ids]}
        if stage == "ads_intent":
            return {"results": [{"id": i, "funnel": "commercial",
                                 "intent": 0.75} for i in ids]}
        if stage == "seo_rel":
            return {"results": [{"id": i, "band": "strong", "relevance": 0.90}
                                for i in ids]}
        if stage == "seo_bp":
            return {"results": [{"id": i, "band": "very_close",
                                 "business_proximity": 0.85} for i in ids]}
        if stage == "seo_subintent":
            return {"results": [{"id": i, "broad_intent": "informational",
                                 "subintent_id": f"s{i}",
                                 "subintent_label": f"alt niyet {i}"}
                                for i in ids]}
        if stage == "seo_authority":
            return {"results": [{"id": i, "authority": "high"} for i in ids]}
        if stage == "seo_urlgroup":
            return {"groups": [{"group_id": f"g{i}", "ids": [i]} for i in ids]}
        if stage == "social_rel":
            return {"results": [{"id": i, "band": "strong", "relevance": 0.90}
                                for i in ids]}
        if stage == "social_v4":
            return [{"keyword_id": i, "brand_contentability": 80,
                     "attention": 70, "scenario": 65, "relative_fit": 75}
                    for i in ids]
        if stage == "social_intent":
            return [{"keyword_id": i, "social_intent_type": "CONTENT_NATIVE",
                     "intent_confidence": 80, "intent_reason": "icerik"}
                    for i in ids]
        raise AssertionError(f"beklenmeyen asama: {stage}")


def _orchestrate(db_session, make_workspace, make_scoring_run, make_keyword,
                 *, concurrency: int, monkeypatch, ai=None, keywords=None,
                 workspace=None):
    monkeypatch.setattr(AI, "engine_concurrency", lambda: concurrency)
    ws = workspace or _setup_workspace(make_workspace)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads = run.enable_seo = run.enable_social = True
    run.ads_capacity = run.seo_capacity = run.social_capacity = 10
    if keywords is None:
        keywords = [make_keyword(f"antigravity kelime {i}",
                                 brand_profile_id=ws.id,
                                 monthly_volume=500 + i * 10,
                                 competition_score=0.30, trend_3m=15.0)
                    for i in range(12)]
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()
    ai = ai or OrchestrationAI()
    result = run_v3_orchestration(db_session, run=run, ai=ai)
    db_session.commit()
    return run, result, ai, ws, keywords


def _delivery(db_session, run) -> Dict[str, Any]:
    sel = [(s.channel, s.keyword_id, s.algorithm_rank, s.pool_class)
           for s in load_engine_selections(db_session, scoring_run_id=run.id)]
    pools = [(p.channel, p.keyword_id, p.final_rank)
             for p in load_channel_pools(db_session, scoring_run_id=run.id)]
    return {"selections": sorted(sel, key=str), "pools": sorted(pools, key=str)}


@pytest.fixture
def parallel_slots(monkeypatch):
    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: nullcontext)


def test_tam_orkestrasyon_seri_ve_paralel_ayni_teslimati_uretir(
        db_session, make_workspace, make_scoring_run, make_keyword,
        monkeypatch, parallel_slots):
    run_s, res_s, ai_s, ws, kws = _orchestrate(
        db_session, make_workspace, make_scoring_run, make_keyword,
        concurrency=1, monkeypatch=monkeypatch)
    teslim_s = _delivery(db_session, run_s)

    run_p, res_p, ai_p, _, _ = _orchestrate(
        db_session, make_workspace, make_scoring_run, make_keyword,
        concurrency=4, monkeypatch=monkeypatch, workspace=ws, keywords=kws)
    teslim_p = _delivery(db_session, run_p)

    assert res_s["status"] == res_p["status"] == "channel_assigned"
    assert res_s["channels"] == res_p["channels"]
    assert ai_s.calls == ai_p.calls            # asama basina cagri sayisi ayni
    assert teslim_s["selections"] == teslim_p["selections"]
    assert teslim_s["pools"] == teslim_p["pools"]
    assert teslim_s["pools"], "havuz bos — test anlamsiz olurdu"
    assert len(ai_p.threads) > 1 and len(ai_s.threads) == 1


def test_paralel_hata_sonrasi_resume_odenmis_batchi_tekrar_cagirmaz(
        db_session, make_workspace, make_scoring_run, make_keyword,
        monkeypatch, parallel_slots):
    """Hatadan sonra checkpoint'e yazilan asamalar ikinci kosuda TEKRAR
    saglayiciya gitmez (cache/resume korunur)."""
    class YarimAI(OrchestrationAI):
        def _payload(self, stage, ids):
            if stage == "ads_funnel":
                raise RuntimeError("sahte saglayici hatasi")
            return super()._payload(stage, ids)

    monkeypatch.setattr(AI, "engine_concurrency", lambda: 4)
    ws = _setup_workspace(make_workspace)
    run = make_scoring_run(brand_profile_id=ws.id)
    run.algorithm_version = "v3"
    run.status = "scored"
    run.enable_ads = run.enable_seo = run.enable_social = True
    run.ads_capacity = run.seo_capacity = run.social_capacity = 10
    for i in range(12):
        make_keyword(f"antigravity kelime {i}", brand_profile_id=ws.id,
                     monthly_volume=500 + i, competition_score=0.30,
                     trend_3m=15.0)
    db_session.commit()
    freeze_universe_snapshot(db_session, run)
    db_session.commit()

    yarim = YarimAI()
    with pytest.raises(Exception):
        run_v3_orchestration(db_session, run=run, ai=yarim)
    db_session.rollback()
    aile_cagrisi_1 = sum(v for k, v in yarim.calls.items()
                         if k.startswith("family_"))
    assert aile_cagrisi_1 > 0, "aile asamasi hic kosmamis"

    # Ikinci kosu: aile asamalari CACHE'den gelmeli, yeniden cagrilmamali
    run.status = "scored"
    db_session.commit()
    tam = OrchestrationAI()
    run_v3_orchestration(db_session, run=run, ai=tam)
    db_session.commit()
    aile_cagrisi_2 = sum(v for k, v in tam.calls.items()
                         if k.startswith("family_"))
    assert aile_cagrisi_2 == 0, (
        f"odenmis aile batch'leri tekrar cagrildi: {tam.calls}")


def test_butce_tavaninda_ucustaki_basarili_is_yazilir_yeni_is_baslamaz(
        monkeypatch):
    """`BudgetExceeded`: yeni is gonderilmez, tamamlanan is YAZILIR."""
    import time

    monkeypatch.setattr(AI, "_global_slot_acquirer", lambda: nullcontext)
    basladi: List[int] = []
    kilit = threading.Lock()

    def make(index: int):
        def job():
            with kilit:
                basladi.append(index)
            if index == 1:
                raise BudgetExceeded("onayli tavan asilacakti")
            time.sleep(0.2)
            return index
        return job

    yazilan: List[Any] = []
    with pytest.raises(BudgetExceeded):
        AI.run_jobs([make(i) for i in range(8)], yazilan.append, concurrency=2)

    assert sorted(basladi) == [0, 1], f"tavandan sonra is basladi: {basladi}"
    assert yazilan == [0], "ucusta tamamlanan is yazilmadi"


def test_limiter_engine_v3_anahtarini_kullanir(monkeypatch):
    """Motor, screening'in anahtarini PAYLASMAZ (ayri slot havuzu)."""
    yakalanan: Dict[str, Any] = {}

    class SahteLimiter:
        def __init__(self, client, *, limit, key):
            yakalanan["limit"], yakalanan["key"] = limit, key

        def slot(self):
            return nullcontext()

    class SahteRedis:
        def ping(self):
            return True

    import redis as redis_lib

    import app.core.screening.inflight as INF
    monkeypatch.setattr(INF, "RedisInflightLimiter", SahteLimiter)
    monkeypatch.setattr(redis_lib.Redis, "from_url",
                        classmethod(lambda cls, *a, **k: SahteRedis()))
    acquire = AI._global_slot_acquirer()
    assert acquire is not None
    assert yakalanan["key"] == "engine_v3:inflight:leases"
    assert yakalanan["limit"] >= 1
