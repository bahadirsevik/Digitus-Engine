"""Faz 1 / 1B hibrit koşucu — GERÇEK DB üzerinde uçtan uca (ücretsiz, ağsız).

`hybrid_run.run_hybrid` bugüne dek yalnız DB'siz birim testlerle sınandı
(tests/unit/test_hybrid_run.py): ScoreEngine.create_scoring_run + geçişler +
freeze_universe_snapshot + run_v3_orchestration + AdGenerationSet +
SEOGeoGenerator + sosyal brief zinciri + collector.flush hiç birlikte
koşmamıştı. Bu dosya o zinciri SAHTE kök AI ile (gerçek sağlayıcı/ağ YOK)
koşturur.

Sahte kök AI (`HybridFakeAI`):
  * motor aşamalarını `OrchestrationAI`'a (test_engine_parallel_orchestration)
    devreder; içerik aşamaları (ads_grouping, ads_rsa, claim_rewrite,
    micro_shorten, seo_generation, geo_compliance, social_brief_*) için
    sözleşme-uyumlu JSON üretir,
  * her `for_stage` çağrısının provider/model'ini ve her tamamlamayı kaydeder,
  * gerçek backend'ler gibi `collector.record(...)` yapar (budget settle çalışır),
  * `RoutedAIService` çözümlemesini taklit eder (provider verilmezse global
    AI_STAGE_ROUTES okunur).
Gerçek `build_benchmark_ai` yolu da koşar: yalnız `hr.RoutedAIService` /
`hr.GeminiService` isimleri sahteyle değiştirilir (StageRoutePinnedAI +
BudgetGuardedAI + CharacterizationBudget GERÇEK).

Plan: `benchmark/claude_bakeoff_plan.json` yalnız OKUNUR; blokajlar test için
bellekte doldurulup yeniden mühürlenir (dosyaya yazılmaz).
"""
from __future__ import annotations

import copy
import json
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import app.core.benchmark.hybrid_run as hr
from app.config import settings
from app.core.benchmark.claude_bakeoff_plan import (
    CONTENT_STAGES, O_STAGES, Y_STAGES, faz1_blockers,
)
from app.core.benchmark.reference_characterization import seal
from app.core.engine import ai_runner as AI
from app.core.telemetry.usage import UsageCollector
from app.database.models import (
    AdGenerationSet, AdGroup, AdHeadline, AiUsageEvent, ChannelPool,
    ContentOutput, ScoringRun, SocialBrief, SocialCategory, SocialContent,
    SocialIdea,
)
from tests.integration.test_engine_parallel_orchestration import (
    OrchestrationAI, _setup_workspace,
)

PLAN_FILE = (Path(__file__).resolve().parents[2]
             / "benchmark" / "claude_bakeoff_plan.json")
CAPS = {"ADS": 10, "SEO": 10, "SOCIAL": 10}
# Rakam İÇERMEYEN, ayırt edilebilir kelimeler: grounding doğrulayıcısı sayısal
# iddia sayar; rakamlı kelime sahte içeriği "iddialı" yapıp onarım turlarını
# tetiklerdi (deterministik çağrı sayısı bozulurdu).
SUFFIXES = ["alfa", "beta", "gama", "delta", "epsilon", "zeta", "eta", "teta",
            "iota", "kapa", "lamda", "omega", "sigma", "tau"]
RSA_HEADLINES = ["Güvenli Alışveriş Fırsatı", "Hemen Keşfedin", "Kaliteli Hizmet Burada",
                 "Size Özel Çözümler", "Detaylı Bilgi Alın"]
RSA_DESCRIPTIONS = [
    "Ürünlerimizi inceleyin ve ihtiyacınıza uygun seçeneği hemen bulun.",
    "Uzman ekibimizle tanışın, size uygun çözümü birlikte belirleyelim.",
    "Detaylı bilgi için bize ulaşın ve hemen keşfetmeye başlayın.",
]
RSA_NEGATIVES = ["nedir", "nasıl", "ne demek", "ücretsiz", "bedava", "free",
                 "ucuz", "ikinci el", "şikayet", "tamir", "indir", "pdf"]
LONG_HEADLINE = "Antigravity Teknoloji Çözümleri Burada Sizi Bekliyor"  # 52 karakter, kural kısaltamaz


# ═══ Sahte içerik yanıtları (saf fonksiyonlar; DB'siz doğrulanabilir) ═══════

def input_json(prompt: str) -> Dict[str, Any]:
    m = re.search(r"<INPUT_JSON>\n(.*?)\n</INPUT_JSON>", prompt, re.S)
    assert m, "prompt'ta <INPUT_JSON> bloğu yok"
    return json.loads(m.group(1))


def resp_grouping(prompt: str) -> str:
    pairs = [(int(i), k) for i, k in
             re.findall(r'"id":\s*(\d+),\s*"keyword":\s*"([^"]*)"', prompt)]
    assert pairs, "gruplama prompt'unda keyword bulunamadı"
    chunks = [pairs[i:i + 5] for i in range(0, len(pairs), 5)]
    if len(chunks) > 1 and len(chunks[-1]) < 3:      # 3-7 kuralı: kuyruğu öncekine kat
        chunks[-2].extend(chunks.pop())
    return json.dumps({"ad_groups": [
        {"name": "Fikstür Grubu %s" % chr(ord("A") + n), "theme": "ortak niyet",
         "keyword_ids": [i for i, _ in c], "keywords": [k for _, k in c]}
        for n, c in enumerate(chunks)]}, ensure_ascii=False)


def resp_rsa(long_headline: bool = False) -> str:
    heads = list(RSA_HEADLINES)
    if long_headline:
        heads[0] = LONG_HEADLINE
    return json.dumps({
        "headlines": [{"text": t, "type": "keyword", "position": "any"} for t in heads],
        "descriptions": [{"text": t, "type": "value_prop", "position": "any"}
                         for t in RSA_DESCRIPTIONS],
        "negative_keywords": [{"keyword": k, "match_type": "phrase",
                               "category": "genel", "reason": "niyet dışı"}
                              for k in RSA_NEGATIVES],
    }, ensure_ascii=False)


def resp_seo(kw: str) -> str:
    sentence = ("Bu bölümde %s konusu için pratik adımlar ve doğrulanabilir "
                "bilgiler sade biçimde anlatılır." % kw)
    paragraphs = "\n\n".join([" ".join([sentence] * 2)] * 3 + [sentence])   # 7 cümle (~550 kelime)
    return json.dumps({
        "title": "%s Rehberi" % kw.title(),
        "url_suggestion": kw.lower().replace(" ", "-"),
        "intro_paragraph": ("%s nedir sorusunun kısa yanıtı bu yazının başında verilir. "
                            "%s konusunda %d güncel bilgiler sade bir dille açıklanır."
                            % (kw, kw, datetime.now().year)),
        "subheadings": ["%s Nedir?" % kw, "%s Nasıl Çalışır?" % kw,
                        "%s Neden Önemlidir?" % kw, "%s Nasıl Seçilir?" % kw,
                        "%s Nerede Kullanılır?" % kw],
        "body_sections": [paragraphs] * 5,
        "bullet_points": [{"text": "Madde %s" % i, "order": i} for i in (1, 2, 3)],
        "internal_link_anchor": "ilgili içerik",
        "internal_link_suggestion": "/ilgili-sayfa",
        "external_link_anchor": None,
        "external_link_url": None,
        "meta_description": "%s hakkında kısa ve açık bir rehber." % kw,
        "faq_items": [{"question": "%s nedir?" % kw, "answer": "Kısa ve net bir yanıt."}] * 3,
        "image_alt_texts": ["%s görseli bir" % kw, "%s görseli iki" % kw],
    }, ensure_ascii=False)


def resp_geo() -> str:
    crit = ["intro_answers_question", "snippet_extractable", "info_hierarchy_strong",
            "tone_is_informative", "no_fluff_content", "direct_answer_present",
            "has_verifiable_info"]
    out: Dict[str, Any] = {c: True for c in crit}
    out.update({"detailed_analysis": [], "improvement_notes": "",
                "ai_snippet_preview": "Kısa özet."})
    return json.dumps(out, ensure_ascii=False)


def resp_categories(prompt: str) -> str:
    d = input_json(prompt)
    ids = [k["id"] for k in d["keywords"]]
    return json.dumps({"categories": [
        {"category_name": "Eğitici Seri", "category_type": "educational",
         "description": "Konuyu sade anlatan eğitici içerik teması.",
         "relevance_score": 0.9, "suggested_keyword_ids": ids},
        {"category_name": "Ürün Faydaları", "category_type": "product_benefit",
         "description": "Ürünün kullanım değerini anlatan içerik teması.",
         "relevance_score": 0.8, "suggested_keyword_ids": ids[:1]},
    ]}, ensure_ascii=False)


def resp_ideas(prompt: str) -> str:
    d = input_json(prompt)
    kws = [k["id"] for k in d["keywords"]]
    cat = d["category"]["name"]
    ideas, n = [], 0
    for spec in d["target_specs"]:
        for j in range(spec["requested_count"]):
            ideas.append({
                "target_id": spec["target_id"],
                "primary_keyword_id": kws[n % len(kws)],
                "idea_title": "%s %s fikri %d" % (cat, spec["content_format"], j + 1),
                "idea_description": "Kategori teması için %s formatında yaratıcı bir anlatım."
                                    % spec["content_format"],
                "target_platform": spec["platform"],
                "content_format": spec["content_format"],
                "trend_alignment": 0.7})
            n += 1
    return json.dumps({"ideas": ideas}, ensure_ascii=False)


def resp_content(prompt: str) -> str:
    d = input_json(prompt)
    kw = d["primary_keyword"]["keyword"]
    fmt = d["target"]["content_format"]
    payload: Optional[Dict[str, Any]] = None
    if fmt == "carousel":
        payload = {"kind": "carousel", "slides": [
            {"position": i, "headline": "Adım %s" % w,
             "body": "%s için sade bir anlatım." % kw,
             "visual_direction": "Minimal ve temiz kompozisyon"}
            for i, w in ((1, "bir"), (2, "iki"), (3, "üç"))]}
    elif fmt != "post":
        raise AssertionError("sahte içerik yalnız post/carousel biliyor: %r" % fmt)
    return json.dumps({
        "hooks": [{"text": "%s hakkında merak edilenler" % kw, "style": "question"}],
        "caption": "%s konusunda kısa ve faydalı bir anlatım." % kw,
        "cta_text": "Daha fazlasını keşfedin.",
        "hashtags": ["rehber", "ipucu", "bilgi", "keşfet", "öneri"],
        "format_payload": payload,
        "visual_suggestion": None, "video_concept": None,
        "industry_posting_suggestion": None, "platform_notes": None,
    }, ensure_ascii=False)


# ═══ Sahte kök AI ═══════════════════════════════════════════════════════════

class FakeScoped:
    """StageScopedAIService yerine: provider/model/thinking_level taşır."""

    def __init__(self, root: "HybridFakeAI", stage: str, provider: str,
                 model: str, thinking_level: Optional[str]):
        self._root = root          # logical_request(...) `_root.collector`'ı okur
        self.stage, self.provider = stage, provider
        self.model, self.thinking_level = model, thinking_level

    def for_stage(self, stage: str, **overrides):
        # GERÇEK StageScopedAIService.for_stage gibi: KÖKE gider (pinned/guard atlanır)
        return self._root.for_stage(stage, **overrides)

    def complete_json(self, prompt: str, max_tokens: int = 6000,
                      response_schema: Optional[Dict[str, Any]] = None, **kw) -> str:
        return self._root.answer(self, prompt, max_tokens, json_mode=True)

    def complete(self, prompt: str, max_tokens: int = 2000, **kw) -> str:
        return self._root.answer(self, prompt, max_tokens, json_mode=False)


class HybridFakeAI:
    def __init__(self, *, fail_always=(), fail_nth: Optional[Dict[str, set]] = None,
                 long_headline: bool = False):
        self.collector = None
        self.cost_binding = None
        self.closed = False
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._engine = OrchestrationAI()
        self._counts: Counter = Counter()
        self._fail_always = set(fail_always)
        self._fail_nth = {k: set(v) for k, v in (fail_nth or {}).items()}
        self._long_headline = long_headline
        self._last_seo_kw = "anahtar"

    def close(self) -> None:
        self.closed = True

    def for_stage(self, stage: str, *, model=None, provider=None,
                  thinking_level=None, thinking_budget=None):
        if provider is None:       # RoutedAIService._resolve taklidi
            route = (settings.AI_STAGE_ROUTES or {}).get(stage)
            if route:
                provider, model = route["provider"], route["model"]
            else:
                provider = "gemini"
        return FakeScoped(self, stage, provider, model or settings.GEMINI_MODEL,
                          thinking_level)

    # -- tamamlama --------------------------------------------------------
    def answer(self, sc: FakeScoped, prompt: str, max_tokens: int,
               json_mode: bool) -> str:
        with self._lock:
            self._counts[sc.stage] += 1
            nth = self._counts[sc.stage]
            self.calls.append({"stage": sc.stage, "provider": sc.provider,
                               "model": sc.model,
                               "thinking_level": sc.thinking_level, "nth": nth})
        if sc.stage in self._fail_always or nth in self._fail_nth.get(sc.stage, ()):
            raise RuntimeError("injected failure: %s #%d" % (sc.stage, nth))
        out = self._payload(sc.stage, prompt)
        if self.collector is not None:
            self.collector.record(
                stage=sc.stage, model=sc.model, provider=sc.provider,
                prompt_tokens=max(1, len(prompt.encode("utf-8")) // 4),
                candidates_tokens=max(1, min(int(max_tokens or 1),
                                             len(out.encode("utf-8")) // 4)),
                thoughts_tokens=0, finish_reason="STOP", latency_ms=1)
        return out

    def _payload(self, stage: str, prompt: str) -> str:
        if stage == "ads_grouping":
            return resp_grouping(prompt)
        if stage == "ads_rsa":
            return resp_rsa(self._long_headline)
        if stage == "claim_rewrite":
            return "Hemen Keşfedin"
        if stage == "micro_shorten":
            return "Teknoloji Çözümleri Burada"
        if stage == "seo_generation":
            m = (re.search(r"- Anahtar Kelime: (.+)", prompt)
                 or re.search(r"# ANAHTAR KELİME\n(.+)", prompt))
            if m:
                self._last_seo_kw = m.group(1).strip()
            return resp_seo(self._last_seo_kw)
        if stage == "geo_compliance":
            return resp_geo()
        if stage == "social_brief_categories":
            return resp_categories(prompt)
        if stage == "social_brief_ideas":
            return resp_ideas(prompt)
        if stage == "social_brief_contents":
            return resp_content(prompt)
        return self._engine._respond(stage, prompt)      # motor aşamaları

    # -- gözlem yardımcıları ---------------------------------------------
    def stage_routes(self) -> Dict[str, set]:
        out: Dict[str, set] = defaultdict(set)
        for c in self.calls:
            out[c["stage"]].add((c["provider"], c["model"]))
        return out

    def count(self, stage: str) -> int:
        return sum(1 for c in self.calls if c["stage"] == stage)

    def by_provider(self, provider: str) -> int:
        return sum(1 for c in self.calls if c["provider"] == provider)


# ═══ Fixture'lar ════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def sealed_plan():
    return hr.load_plan(str(PLAN_FILE))       # yalnız okunur, verify_seal'li


def _unblock(plan, ws_id=None):
    p = copy.deepcopy(plan)
    p["decisions"]["K12_document_reference"] = "belge-ref"
    p["spike"]["console_reconciled"] = True
    p["credit"] = {**p.get("credit", {}), "net_available_usd": 80.0}
    # Codex Faz 1 #3: hedef ws planin 1B kapsaminda ONAYLI olmali -> fixture ws'i
    p["workspaces"]["ids"] = {"GR-7": ws_id if ws_id is not None else 21, "Optimice": 19}
    p = seal(p)
    assert faz1_blockers(p) == []
    return p


@pytest.fixture
def open_plan(sealed_plan, world):
    return _unblock(sealed_plan, world.id)


@pytest.fixture(autouse=True)
def _ledger_in_tmp(tmp_path, monkeypatch):
    # Deney defteri testte repoya YAZILMAZ (Codex Faz 1 #1 defteri gecici dosyada)
    monkeypatch.setattr(hr, "LEDGER_PATH", tmp_path / "faz1_budget_ledger.json")


@pytest.fixture
def world(db_session, make_workspace, make_keyword, monkeypatch):
    """Onaylı workspace + 14 keyword; seri motor; sosyal brief bayrağı açık."""
    monkeypatch.setattr(AI, "engine_concurrency", lambda: 1)
    monkeypatch.setattr(settings, "ENABLE_SOCIAL_BRIEF_FLOW", True)
    ws = _setup_workspace(make_workspace)
    for i, suffix in enumerate(SUFFIXES):
        make_keyword("antigravity kelime %s" % suffix, brand_profile_id=ws.id,
                     monthly_volume=500 + i * 10, competition_score=0.30,
                     trend_3m=15.0)
    db_session.commit()
    return ws


def run_cfg(db, ws, plan, config, fake, monkeypatch, *, content=hr.CONTENT_FAMILIES,
            **kw):
    """`run_hybrid`'i GERÇEK build_benchmark_ai yoluyla koşar (yalnız kök sahte)."""
    monkeypatch.setattr(hr, "RoutedAIService", lambda _default: fake)
    monkeypatch.setattr(hr, "GeminiService", lambda **_k: object())
    collector = UsageCollector(brand_profile_id=ws.id)
    res = hr.run_hybrid(db, workspace_id=ws.id, config_name=config, plan=plan,
                        collector=collector, capacities=dict(CAPS),
                        content=content, **kw)
    return res, collector


def _only_run(db) -> ScoringRun:
    db.expire_all()
    runs = db.query(ScoringRun).all()
    assert len(runs) == 1, "tam olarak bir ScoringRun beklenirdi: %d" % len(runs)
    return runs[0]


def _pool_counts(db, run_id) -> Dict[str, int]:
    rows = db.query(ChannelPool).filter(ChannelPool.scoring_run_id == run_id).all()
    return dict(Counter(r.channel for r in rows))


def _assert_full_success(res, fake, db, run):
    """Üç içerik ailesi de gerçekten üretti ve AI'ya GİTTİ (false-green savunması)."""
    assert res["content_errors"] == {}, res["content_errors"]
    assert set(res["content"]) == {"ads", "seo", "social"}
    assert fake.count("ads_rsa") >= 1, "ads 'başarılı' ama RSA AI çağrısı yok"
    assert fake.count("seo_generation") >= 1, "seo 'başarılı' ama AI çağrısı yok"
    assert fake.count("geo_compliance") >= 1
    for st in ("social_brief_categories", "social_brief_ideas",
               "social_brief_contents"):
        assert fake.count(st) >= 1, "sosyal '%s' AI çağrısı yok" % st


# ═══ (a) Aday: uçtan uca ════════════════════════════════════════════════════

def test_aday_end_to_end(db_session, world, open_plan, monkeypatch):
    sentinel = {"intent": {"provider": "gemini", "model": "sentinel-model"}}
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", sentinel)
    fake = HybridFakeAI()

    res, collector = run_cfg(db_session, world, open_plan, "Aday", fake,
                             monkeypatch)
    run = _only_run(db_session)
    k3 = open_plan["decisions"]["K3_models"]

    # -- koşu + mühür ------------------------------------------------------
    assert run.status == "channel_assigned"
    assert run.algorithm_version == "v3" and res["scoring_run_id"] == run.id
    sec = res["manifest_section"]
    assert sec["manifest_schema_version"] == 3
    assert set(sec["providers"]) == set(Y_STAGES) | set(O_STAGES), (
        "mühürlenen rota kümesi Y∪O ile aynı olmalı (sealed olmayan bir rota "
        "sessizce Gemini'de kalır): %s" % sorted(sec["providers"]))
    assert set(sec["providers"].values()) == {"anthropic"}
    assert all(sec["models"][s] == k3["Y"] for s in Y_STAGES)
    assert all(sec["models"][s] == k3["O"] for s in O_STAGES)
    for stage, model in sec["models"].items():       # rotasız aşama Gemini kimliğinde
        if stage not in sec["providers"] and model != "deterministic":
            assert model.startswith("gemini"), (stage, model)
    for key in ("adapter_contract_version", "system_sha",
                "schema_transformer_version", "thinking_policy_sha"):
        assert sec.get(key), key
    assert run.execution_manifest["engine_v3"] == sec      # DB'deki mühür == rapor

    # -- sahte AI'nın gördüğü rotalar -------------------------------------
    routes = fake.stage_routes()
    expect = {**{s: ("anthropic", k3["Y"]) for s in Y_STAGES},
              **{s: ("anthropic", k3["O"]) for s in O_STAGES},
              **{s: ("anthropic", k3["content"]) for s in CONTENT_STAGES}}
    for stage, pms in routes.items():
        if stage in expect:
            assert pms == {expect[stage]}, (stage, pms)
        else:
            assert {p for p, _ in pms} == {"gemini"}, (stage, pms)
    seen = set(routes)
    assert seen & set(Y_STAGES), "hiçbir Y aşaması çağrılmadı: %s" % sorted(seen)
    assert seen & set(O_STAGES), "hiçbir O aşaması çağrılmadı: %s" % sorted(seen)
    for stage in ("seo_generation", "ads_rsa", "social_brief_ideas",
                  "social_brief_contents"):
        assert stage in seen and routes[stage] == {("anthropic", k3["content"])}
    # Gemini'de kalan içerik aşamaları da çalıştı (≤3 kelimede gruplama AI'sız tek grup)
    assert {"geo_compliance", "social_brief_categories"} <= seen
    # bu çalıştırmada iç içe (guard'sız) mikro aşama beklenmez (bkz. ayrı test)
    assert fake.count("claim_rewrite") == fake.count("micro_shorten") == 0

    # -- havuzlar ----------------------------------------------------------
    pools = _pool_counts(db_session, run.id)
    assert {"ADS", "SEO", "SOCIAL"} <= set(pools) and all(pools.values()), pools
    if pools["ADS"] > 3:
        assert "ads_grouping" in seen

    # -- içerik ------------------------------------------------------------
    _assert_full_success(res, fake, db_session, run)

    sets = db_session.query(AdGenerationSet).filter_by(scoring_run_id=run.id).all()
    assert len(sets) == 1 and sets[0].status == "active"   # ilk başarılı set otomatik aktif
    assert sets[0].groups_count == res["content"]["ads"]["n_groups"] > 0
    heads = [h.headline_text for h in db_session.query(AdHeadline).join(
        AdGroup, AdHeadline.ad_group_id == AdGroup.id).filter(
        AdGroup.generation_set_id == sets[0].id)]
    assert set(RSA_HEADLINES) <= set(heads), (
        "AI başlıkları yok: deterministik fallback devreye girmiş olabilir: %s" % heads[:6])

    seo = res["content"]["seo"]
    assert seo["failed"] == {} and len(seo["content_ids"]) == seo["n_requested"] > 0
    seo_rows = db_session.query(ContentOutput).filter_by(
        scoring_run_id=run.id, channel="SEO").count()
    assert seo_rows == seo["n_requested"]
    assert fake.count("seo_generation") >= seo["n_requested"]
    assert fake.count("geo_compliance") >= seo["n_requested"]

    soc = res["content"]["social"]
    assert soc["idea_status"] == "completed" and soc["content_status"] == "completed"
    assert soc["unresolved_idea_ids"] == [] and soc["persisted_idea_ids"]
    brief = db_session.query(SocialBrief).filter_by(scoring_run_id=run.id).one()
    assert brief.id == soc["brief_id"]
    assert db_session.query(SocialCategory).filter_by(brief_id=brief.id).count() >= 2
    assert db_session.query(SocialIdea).filter_by(brief_id=brief.id).count() >= soc["n_ideas"] > 0
    assert db_session.query(SocialContent).filter_by(brief_id=brief.id).count() == len(
        soc["persisted_idea_ids"])

    # -- bütçe: sağlayıcı bazlı atıf, tavan yakılmadı ----------------------
    b = res["budget_summary"]
    assert b["anthropic"]["requests_used"] == fake.by_provider("anthropic") > 0
    assert b["gemini"]["requests_used"] == fake.by_provider("gemini") > 0
    assert b["deepseek"]["requests_used"] == 0
    for prov in ("anthropic", "gemini"):
        assert b[prov]["cost_settled_usd"] > 0, prov
        assert b[prov]["ceiling_charges"] == 0, (
            "%s: gerçek usage ile settle EDİLEMEDİ (tavan yakıldı)" % prov)
        assert b[prov]["cost_settled_usd"] <= b[prov]["max_cost_usd"]
    assert b["anthropic"]["max_cost_usd"] == 50.0 and b["gemini"]["max_cost_usd"] == 20.0

    # -- usage + flush -----------------------------------------------------
    assert res["usage"]["requests"] == len(fake.calls)
    assert res["usage"]["prompt_tokens"] > 0 and res["usage"]["candidates_tokens"] > 0
    assert collector._pending == [], "collector.flush() çağrılmadı/boşaltmadı"
    db_session.expire_all()
    rows = db_session.query(AiUsageEvent).filter_by(scoring_run_id=run.id).all()
    assert len(rows) == len(fake.calls), (len(rows), len(fake.calls))
    assert all(r.provider for r in rows)
    assert {r.provider for r in rows} == {"anthropic", "gemini"}
    assert {r.model for r in rows if r.provider == "anthropic"} <= {k3["Y"], k3["O"], k3["content"]}
    assert Counter(r.stage for r in rows) == Counter(c["stage"] for c in fake.calls)

    # -- (c) global ayar geri yüklendi, kapanış ----------------------------
    assert settings.AI_STAGE_ROUTES is sentinel
    assert fake.closed


# ═══ (b) G/G/G ve G/G/C: mühür şekli ════════════════════════════════════════

def test_ggg_manifest_is_byte_identical_schema2_all_gemini(
        db_session, world, open_plan, monkeypatch):
    fake = HybridFakeAI()
    res, _ = run_cfg(db_session, world, open_plan, "G/G/G", fake, monkeypatch)
    run = _only_run(db_session)

    assert run.status == "channel_assigned"
    sec = res["manifest_section"]
    assert sec["manifest_schema_version"] == 2
    assert set(sec) == {"manifest_schema_version", "firm_block_sha256",
                        "algorithm_versions", "models", "prompt_shas",
                        "location_policy"}, "G/G/G mühürü provider alanları taşımamalı"
    assert all(m.startswith("gemini") or m == "deterministic"
               for m in sec["models"].values())
    assert fake.calls and {c["provider"] for c in fake.calls} == {"gemini"}
    _assert_full_success(res, fake, db_session, run)

    b = res["budget_summary"]
    assert b["anthropic"]["requests_used"] == 0
    assert b["gemini"]["requests_used"] == len(fake.calls)
    assert b["gemini"]["ceiling_charges"] == 0
    db_session.expire_all()
    rows = db_session.query(AiUsageEvent).filter_by(scoring_run_id=run.id).all()
    assert len(rows) == len(fake.calls) and {r.provider for r in rows} == {"gemini"}


def test_ggc_content_only_routes_keep_schema2_manifest(
        db_session, world, open_plan, monkeypatch):
    """Yalnız içerik aşamaları Claude'da: motor mühürlerinde rota YOK (sema 2)."""
    fake = HybridFakeAI()
    res, _ = run_cfg(db_session, world, open_plan, "G/G/C", fake, monkeypatch)
    run = _only_run(db_session)
    k3 = open_plan["decisions"]["K3_models"]

    assert run.status == "channel_assigned"
    assert res["manifest_section"]["manifest_schema_version"] == 2
    assert "providers" not in res["manifest_section"]
    routes = fake.stage_routes()
    for s in CONTENT_STAGES:
        if s in routes:
            assert routes[s] == {("anthropic", k3["content"])}, (s, routes[s])
    for s in set(Y_STAGES) | set(O_STAGES):
        if s in routes:
            assert {p for p, _ in routes[s]} == {"gemini"}, (s, routes[s])
    assert fake.by_provider("anthropic") > 0
    _assert_full_success(res, fake, db_session, run)


# ═══ (d) bloklu plan ════════════════════════════════════════════════════════

def test_blocked_plan_creates_nothing(db_session, world, open_plan, monkeypatch):
    blocked = copy.deepcopy(open_plan)
    blocked["decisions"]["K12_document_reference"] = None
    blocked = seal(blocked)
    assert faz1_blockers(blocked) == ["K12_document_reference"]

    def _boom(*_a, **_k):
        raise AssertionError("bloklu planda AI nesnesi kurulmamalıydı")

    monkeypatch.setattr(hr, "RoutedAIService", _boom)
    monkeypatch.setattr(hr, "GeminiService", _boom)
    sentinel = {"intent": {"provider": "gemini", "model": "sentinel-model"}}
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", sentinel)
    before = db_session.query(ScoringRun).count()

    with pytest.raises(hr.Faz1Blocked) as ei:
        hr.run_hybrid(db_session, workspace_id=world.id, config_name="Aday",
                      plan=blocked, collector=UsageCollector(brand_profile_id=world.id),
                      capacities=dict(CAPS))
    assert ei.value.blockers == ["K12_document_reference"]

    db_session.expire_all()
    assert db_session.query(ScoringRun).count() == before == 0
    assert db_session.query(AiUsageEvent).count() == 0
    assert settings.AI_STAGE_ROUTES is sentinel


# ═══ (e) false-green savunması: enjekte edilen hatalar ══════════════════════

def test_seo_total_failure_is_reported_while_other_families_complete(
        db_session, world, open_plan, monkeypatch):
    fake = HybridFakeAI(fail_always={"seo_generation"})
    res, _ = run_cfg(db_session, world, open_plan, "Aday", fake, monkeypatch)
    run = _only_run(db_session)

    assert run.status == "channel_assigned"            # motor etkilenmedi
    assert "seo" in res["content_errors"], res["content_errors"]
    assert "injected failure: seo_generation" in res["content_errors"]["seo"]
    assert "seo" not in res["content"]
    assert set(res["content_errors"]) == {"seo"}
    assert fake.count("seo_generation") >= 1           # gerçekten denendi
    assert db_session.query(ContentOutput).filter_by(
        scoring_run_id=run.id, channel="SEO").count() == 0

    # diğer aileler tamamlandı ve AI'ya gitti
    assert res["content"]["ads"]["set_status"] == "active"
    assert fake.count("ads_rsa") >= 1
    assert res["content"]["social"]["content_status"] == "completed"
    assert fake.count("social_brief_contents") >= 1


def test_seo_partial_failure_is_recorded_not_hidden(
        db_session, world, open_plan, monkeypatch):
    fake = HybridFakeAI(fail_nth={"seo_generation": {1}})
    res, _ = run_cfg(db_session, world, open_plan, "Aday", fake, monkeypatch,
                     content=("seo",))
    seo = res["content"]["seo"]
    assert res["content_errors"] == {}
    assert len(seo["failed"]) == 1 and "injected failure" in next(iter(seo["failed"].values()))
    assert len(seo["content_ids"]) == seo["n_requested"] - 1 >= 1


def test_ads_failure_marks_set_failed_and_is_reported(
        db_session, world, open_plan, monkeypatch):
    fake = HybridFakeAI(fail_always={"ads_rsa"})
    res, _ = run_cfg(db_session, world, open_plan, "Aday", fake, monkeypatch)
    run = _only_run(db_session)

    assert "ads" in res["content_errors"] and "ads" not in res["content"]
    assert set(res["content_errors"]) == {"ads"}, res["content_errors"]
    sets = db_session.query(AdGenerationSet).filter_by(scoring_run_id=run.id).all()
    assert len(sets) == 1 and sets[0].status == "failed"
    assert db_session.query(AdGroup).filter_by(scoring_run_id=run.id).count() == 0
    assert fake.count("ads_rsa") >= 1
    assert res["content"]["seo"]["failed"] == {}
    assert res["content"]["social"]["content_status"] == "completed"


# ═══ (f) bütçe tavanı: ağ çağrısından ÖNCE durur ════════════════════════════

def test_claude_cap_breach_fails_run_before_any_anthropic_call(
        db_session, world, open_plan, monkeypatch):
    sentinel = {"intent": {"provider": "gemini", "model": "sentinel-model"}}
    monkeypatch.setattr(settings, "AI_STAGE_ROUTES", sentinel)
    fake = HybridFakeAI()
    with pytest.raises(Exception):
        run_cfg(db_session, world, open_plan, "Aday", fake, monkeypatch,
                caps_usd={"claude": 1e-9})
    run = _only_run(db_session)
    assert run.status == "failed"
    assert fake.by_provider("anthropic") == 0, "tavan aşıldı ama Claude çağrıldı"
    assert db_session.query(ChannelPool).filter_by(scoring_run_id=run.id).count() == 0
    assert settings.AI_STAGE_ROUTES is sentinel      # istisnada da geri yüklendi
    assert fake.closed


# ═══ (g) bilinen açık: iç içe scoped() guard'ı atlıyor ══════════════════════

def test_nested_scoped_calls_stay_under_budget_guard(
        db_session, world, open_plan, monkeypatch):
    """claim_rewrite/micro_shorten `scoped(self.ai_service, ...)` ile KÖKE iner.

    `_GuardedScoped.__getattr__` iç servisin `for_stage`'ini açar; sonuç
    pinned/guard'sız kök servistir -> bu çağrılar HARD-CAP'e sayılmaz ve
    rota sabitleyiciyi atlar. Düzelene kadar XFAIL (açığı kanıtlar, kosuyu
    kirletmez).
    """
    fake = HybridFakeAI(long_headline=True)
    res, _ = run_cfg(db_session, world, open_plan, "Aday", fake, monkeypatch,
                     content=("ads",))
    nested = fake.count("micro_shorten")
    assert nested >= 1, "tetik kurulamadı: micro_shorten hiç çağrılmadı"
    assert "ads" in res["content"], res["content_errors"]
    gem_calls, gem_guarded = (fake.by_provider("gemini"),
                              res["budget_summary"]["gemini"]["requests_used"])
    assert gem_guarded == gem_calls, (
        "HARD-CAP DELİĞİ: %d Gemini çağrısından yalnız %d'si budget guard'a girdi "
        "(iç içe scoped() guard'sız köke indi)" % (gem_calls, gem_guarded))
