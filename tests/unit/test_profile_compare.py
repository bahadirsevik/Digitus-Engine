# -*- coding: utf-8 -*-
"""profile_compare: DB'siz, ağsız, ücretsiz."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.benchmark import claude_gates as cg
from app.core.benchmark import hybrid_run as hr
from app.core.benchmark import profile_compare as pc
from app.core.benchmark import profile_inputs
from app.core.benchmark.reference_characterization import seal

PLAN = Path(__file__).resolve().parents[2] / "benchmark" / "claude_bakeoff_plan.json"
PROFILE = {"company_name": "Acme", "sector": "yazılım",
           "brand_summary": "Acme bulut çözümleri sunar. 10 yıllık deneyimi var.",
           "products": ["CRM", "ERP"], "services": [], "target_audience": "KOBİ",
           "use_cases": ["satış takibi"], "problems_solved": ["dağınık veri"],
           "brand_terms": ["acme"], "exclude_themes": [], "suggested_keywords": ["x"],
           "anchor_texts": ["a"]}
GOOD_TEXT = "Acme bulut yazılım çözümleri. " * 40
PROVIDERS = {"gemini": ("gemini", "gemini-3.8-flash"),
             "sonnet": ("anthropic", "claude-sonnet-5-5"),
             "haiku": ("anthropic", "claude-haiku-5-5")}


@pytest.fixture
def open_plan():
    p = copy.deepcopy(hr.load_plan(str(PLAN)))
    p["decisions"]["K12_document_reference"] = "belge-ref"
    p["spike"]["console_reconciled"] = True
    p["credit"] = {**p.get("credit", {}), "net_available_usd": 80.0}
    # profil kapsami: planin id eslemesindeki her ws onaylidir (99 = donmus girdisi yok)
    p["workspaces"]["ids"] = {"GR-7": 30, "Optimice": 21, "Noksel": 99}
    return seal(p)


class FakeBP:
    def __init__(self, id, url="https://a.example", info="ön bilgi"):
        self.id, self.company_url, self.preliminary_info = id, url, info


class FakeDB:
    """query(...).filter(...).first() sıradaki satırı döndürür."""

    def __init__(self, rows):
        self.q = list(rows)

    def query(self, _m):
        return SimpleNamespace(filter=lambda c: SimpleNamespace(first=lambda: self.q.pop(0)))


def fake_crawl(url):
    return {"site_content": GOOD_TEXT, "source_pages": [url], "content_quality": {}}


def test_freeze_writes_and_skips_existing(tmp_path):
    calls = []

    def crawl(u):
        calls.append(u)
        return fake_crawl(u)

    out = pc.freeze_inputs(FakeDB([FakeBP(1), FakeBP(2)]), [1, 2], out_dir=str(tmp_path), crawl=crawl)
    assert set(out) == {1, 2} and all(p.exists() for p in out.values()) and len(calls) == 2
    before = out[1].read_bytes()
    out2 = pc.freeze_inputs(FakeDB([]), [1], out_dir=str(tmp_path), crawl=crawl)
    assert out2[1] == out[1] and len(calls) == 2 and out[1].read_bytes() == before
    with pytest.raises(FileExistsError):  # alt katman da üzerine yazmayı reddeder
        with pc._inputs_dir(str(tmp_path)):
            profile_inputs.freeze_profile_input(1, company_url="u", preliminary_info=None, crawl=fake_crawl)
    assert profile_inputs.PROFILE_INPUTS_DIR == Path("benchmark/profile_inputs")  # yama geri alındı


def test_freeze_missing_url_fails(tmp_path):
    with pytest.raises(ValueError):
        pc.freeze_inputs(FakeDB([FakeBP(3, url="")]), [3], out_dir=str(tmp_path), crawl=fake_crawl)


class FakeCollector:
    def __init__(self):
        self._all_events, self._local, self.n = [], SimpleNamespace(ctx=None), 0

    @contextmanager
    def logical_request(self):
        self.n += 1
        self._local.ctx = {"request_id": f"r{self.n}", "attempt": 0}
        try:
            yield
        finally:
            self._local.ctx = None


class FakeAI:
    def __init__(self, label, provider, model, collector, log):
        self.label, self.provider, self.model, self.c, self.log = label, provider, model, collector, log

    def for_stage(self, stage, **kw):
        assert stage == "profile_extract"
        return self

    def complete_json(self, prompt, **kw):
        self.log.append((self.label, prompt))
        self.c._all_events.append({"request_id": self.c._local.ctx["request_id"], "latency_ms": 7,
                                   "prompt_tokens": 3, "candidates_tokens": 2})
        return json.dumps(PROFILE)

    def summary(self):
        return {"ok": 1}


def _frozen(tmp_path, wss):
    pc.freeze_inputs(FakeDB([FakeBP(w) for w in wss]), wss, out_dir=str(tmp_path), crawl=fake_crawl)


def test_run_records_per_pair_and_fair_prompt(tmp_path, open_plan):
    wss = [21, 30]
    _frozen(tmp_path, wss)
    col, log = FakeCollector(), []
    doc = pc.run_profile_compare(
        workspace_ids=wss, providers=PROVIDERS, plan=open_plan, collector=col,
        ai_factory=lambda l, p, m: FakeAI(l, p, m, col, log), inputs_dir=str(tmp_path),
        out_path=str(tmp_path / "o.json"))
    assert len(doc["results"]) == 6 and (tmp_path / "o.json").exists()
    for r in doc["results"]:
        assert (r["provider"], r["model"]) == tuple(PROVIDERS[r["label"]]) and r["error"] is None
        assert r["usage"]["latency_ms"] == 7 and r["profile"]["company_name"] == "Acme"
    for ws in wss:
        assert len({r["prompt_sha"] for r in doc["results"] if r["ws"] == ws}) == 1
    # modele giden literal prompt: her ws için tüm etiketlerde aynı; ws'ler ayrı
    assert len({p for _, p in log}) == 1 or len({p for _, p in log}) == 2
    by_label = {}
    for lab, p in log:
        by_label.setdefault(lab, []).append(p)
    assert sorted(by_label["gemini"]) == sorted(by_label["sonnet"]) == sorted(by_label["haiku"])
    expect = [list(x) for x in pc.block_random_order(wss, list(PROVIDERS), doc["seed"])]
    assert doc["order"] == expect
    assert pc.run_profile_compare(workspace_ids=wss, providers=PROVIDERS, plan=open_plan, collector=col,
                                  dry_run=True, inputs_dir=str(tmp_path))["order"] == expect
    assert doc["usage_totals"]["gemini"]["calls"] == 2 and doc["payload_sha256"]


def test_blocked_plan_raises_before_any_call(tmp_path):
    plan = hr.load_plan(str(PLAN))
    called = []
    with pytest.raises(hr.Faz1Blocked):
        pc.run_profile_compare(workspace_ids=[1], providers=PROVIDERS, plan=plan, collector=None,
                               ai_factory=lambda *a: called.append(a), inputs_dir=str(tmp_path))
    assert not called


def test_unapproved_workspace_rejected_before_inputs(tmp_path, open_plan):
    from app.core.benchmark.claude_bakeoff_plan import WorkspaceNotApproved
    with pytest.raises(WorkspaceNotApproved):
        pc.run_profile_compare(workspace_ids=[7], providers=PROVIDERS, plan=open_plan,
                               collector=None, inputs_dir=str(tmp_path), dry_run=True)


def test_missing_frozen_input_fails(tmp_path, open_plan):
    with pytest.raises(FileNotFoundError):
        pc.run_profile_compare(workspace_ids=[99], providers=PROVIDERS, plan=open_plan,
                               collector=None, inputs_dir=str(tmp_path), dry_run=True)


def test_metrics():
    assert pc.field_fill(PROFILE) == {"filled": 8, "total": 10}
    conf = dict(PROFILE, products=["CRM"], target_audience="")
    f = pc.fidelity_to_confirmed(PROFILE, conf)
    assert "products" in f["changed_fields"] and 0 < f["overlap_ratio"] < 1
    assert pc.fidelity_to_confirmed({}, conf)["missing_fields"]
    cl = pc.claims_for_review(PROFILE)
    texts = [c["claim_text"] for c in cl]
    assert "Acme bulut çözümleri sunar." in texts and "10 yıllık deneyimi var." in texts
    assert not any(c["field"] in ("anchor_texts", "suggested_keywords") for c in cl)


def test_cards_roundtrip_and_gate_input(tmp_path):
    results = [{"ws": w, "label": l, "profile": PROFILE} for w in (1, 2) for l in PROVIDERS]
    md, mp = tmp_path / "c.md", tmp_path / "m.json"
    mapping = pc.render_profile_cards(results, str(md), mapping_path=str(mp), seed=5)
    text = md.read_text(encoding="utf-8")
    assert not any(w in text for w in ("gemini", "sonnet", "haiku")) and "\r" not in text
    with pytest.raises(ValueError):
        pc.parse_profile_cards(text)  # puansız -> fail-closed
    filled, first = [], True
    for line in text.splitlines():
        if line.startswith("Puan"):
            line = "Puan (1-5): 4"
        elif line.startswith("- [ ]") and first:
            line, first = line.replace("[ ]", "[x]", 1), False
        filled.append(line)
    parsed = pc.parse_profile_cards("\n".join(filled))
    assert sum(v["unsupported_count"] for d in parsed.values() for v in d.values()) == 1
    with pytest.raises(ValueError):
        pc.parse_profile_cards("\n".join(filled).replace("- [ ]", "- [?]", 1))
    rows = pc.to_gate_input(parsed, mapping, ref_label="gemini", cand_label="sonnet")
    assert len(rows) == 2 and set(rows[0]) == {"ws", "halluc_cand", "halluc_ref", "patron_cand", "patron_ref"}
    th = cg.thresholds(cg.load_plan(PLAN))
    many = [dict(rows[0], ws=i) for i in range(10)]
    assert cg.evaluate_profile(many, th).gate == "G3"
