# -*- coding: utf-8 -*-
"""Profil kıyası sürücüsü — plan_claude_gemini_karsilastirma.md §7.1, Faz 0 6b, G3.

10 onaylı workspace x {Gemini, Sonnet, Haiku}; dondurulmuş crawl girdisi, aynı literal prompt.
NOT (adalet): üretim `preliminary_info` GEÇMEZ (brand_profile.py); benchmark dondurulmuş
`preliminary_info`'yu TÜM sağlayıcılara aynen verir. Halüsinasyon sayısı İNSAN yargısıdır
(LLM hakem yok): kör kartlar -> patron işaretler -> `to_gate_input` -> `evaluate_profile`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.benchmark import profile_inputs
from app.core.benchmark.claude_bakeoff_plan import (
    assert_workspace_allowed, faz1_blockers)
from app.core.benchmark.faz1_ledger import LEDGER_PATH, Faz1Ledger
from app.core.benchmark.hybrid_run import (
    HYBRID_MAX_REQUESTS, Faz1Blocked, StageRoutePinnedAI, resolve_caps,
)
from app.core.benchmark.reference_characterization import (
    BudgetGuardedAI, CharacterizationBudget, CharacterizationError, seal,
)

STAGE = "profile_extract"
# Üretimde AI'nin yazmadığı / kodun türettiği alanlar kıyas dışı.
_SKIP_FIELDS = {"suggested_keywords", "anchor_texts"}


@contextmanager
def _inputs_dir(out_dir: Optional[str]):
    # shortcut: profile_inputs modül global'ini yamar (thread-safe değil), tek iş parçacıklı CLI için yeter.
    old = profile_inputs.PROFILE_INPUTS_DIR
    if out_dir is not None:
        profile_inputs.PROFILE_INPUTS_DIR = Path(out_dir)
    try:
        yield
    finally:
        profile_inputs.PROFILE_INPUTS_DIR = old


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 1. dondurma ─────────────────────────────────────────────────────────

def freeze_inputs(db, workspace_ids: Sequence[int], *, out_dir: Optional[str] = None,
                  crawl: Optional[Callable[[str], Dict[str, Any]]] = None,
                  overwrite: bool = False) -> Dict[int, Path]:
    """BrandProfile satırından company_url + preliminary_info; crawl BİR KEZ (ağ eylemi).
    Var olan dosya overwrite=False iken atlanır (crawl çağrılmaz)."""
    from app.database.models import BrandProfile

    out: Dict[int, Path] = {}
    kwargs = {} if crawl is None else {"crawl": crawl}
    with _inputs_dir(out_dir):
        for ws in workspace_ids:
            path = profile_inputs._path(ws)
            if path.exists() and not overwrite:
                out[ws] = path
                continue
            row = db.query(BrandProfile).filter(BrandProfile.id == ws).first()
            if row is None or not (row.company_url or "").strip():
                raise ValueError(f"ws {ws}: BrandProfile yok veya company_url boş")
            out[ws] = profile_inputs.freeze_profile_input(
                ws, company_url=row.company_url,
                preliminary_info=row.preliminary_info, overwrite=overwrite, **kwargs)
    return out


# ── 2. koşu ─────────────────────────────────────────────────────────────

def default_providers() -> Dict[str, Tuple[str, str]]:
    from app.config import settings

    return {"gemini": ("gemini", settings.GEMINI_MODEL),
            "sonnet": ("anthropic", "claude-sonnet-5-5"),
            "haiku": ("anthropic", "claude-haiku-5-5")}


def _default_ai_factory(plan: Mapping[str, Any], collector: Any,
                        ledger: Optional[Faz1Ledger] = None,
                        caps_override: Optional[Mapping[str, float]] = None):
    """label bazlı guarded+pinned ai; kök ve provider bütçeleri etiketler arası PAYLAŞILIR;
    tavan deney defterinden KALAN olarak alınır (Codex Faz 1 #1)."""
    from app.generators.ai_service import GeminiService, RoutedAIService

    root = RoutedAIService(GeminiService(retry_attempts=1))
    root.collector = collector
    caps = dict(caps_override) if caps_override else resolve_caps(plan)
    if ledger is not None and not caps_override:
        caps = ledger.remaining(caps)
    budgets = {p: CharacterizationBudget(max_requests=HYBRID_MAX_REQUESTS, max_cost_usd=cap,
                                         model=None, provider=p)
               for p, cap in caps.items()}

    def factory(label: str, provider: str, model: str):
        pinned = StageRoutePinnedAI(root, {STAGE: {"provider": provider, "model": model}})
        return BudgetGuardedAI(pinned, budgets, collector=collector)

    return factory


def _seed(plan: Mapping[str, Any]) -> int:
    sealed = plan.get("sealed_at")
    if not sealed:
        raise ValueError("planda sealed_at yok")
    return int(_sha(str(sealed))[:8], 16)


def block_random_order(workspace_ids: Sequence[int], labels: Sequence[str],
                       seed: int) -> List[Tuple[int, str]]:
    """Blok-rastgele: ws sırası karışık; her ws bloğunda etiket sırası karışık."""
    rng = random.Random(seed)
    wss = list(workspace_ids)
    rng.shuffle(wss)
    order: List[Tuple[int, str]] = []
    for ws in wss:
        labs = list(labels)
        rng.shuffle(labs)
        order += [(ws, lab) for lab in labs]
    return order


def _event_summary(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    def s(k):
        v = [e.get(k) for e in events if e.get(k) is not None]
        return sum(v) if v else None

    return {"attempts": len(events), "prompt_tokens": s("prompt_tokens"),
            "candidates_tokens": s("candidates_tokens"),
            "thoughts_tokens": s("thoughts_tokens"), "latency_ms": s("latency_ms"),
            "finish_reasons": [e.get("finish_reason") for e in events]}


def _call_one(ai, collector, frozen) -> Tuple[Optional[dict], Dict[str, Any], Optional[dict]]:
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    has_lr = collector is not None and hasattr(collector, "logical_request")
    profile, err, rid = None, None, None
    with (collector.logical_request() if has_lr else nullcontext()):
        if has_lr and hasattr(collector, "_local"):
            rid = (getattr(collector._local, "ctx", None) or {}).get("request_id")
        try:
            profile = profile_inputs.extract_profile_frozen(ProfileExtractor(ai), frozen)
        except CharacterizationError:
            raise  # bütçe/sözleşme ihlali: koşuyu durdur
        except Exception as exc:  # noqa: BLE001
            err = {"type": type(exc).__name__, "message": str(exc)[:300]}
    events = ([e for e in list(getattr(collector, "_all_events", [])) if e.get("request_id") == rid]
              if rid else [])
    return profile, _event_summary(events), err


def run_profile_compare(*, workspace_ids: Sequence[int],
                        providers: Mapping[str, Tuple[str, str]], plan: Mapping[str, Any],
                        collector: Any, ai_factory: Optional[Callable] = None,
                        out_path: Optional[str] = None, dry_run: bool = False,
                        inputs_dir: Optional[str] = None,
                        ledger: Optional[Faz1Ledger] = None) -> Dict[str, Any]:
    blockers = faz1_blockers(dict(plan))
    if blockers:
        raise Faz1Blocked(blockers)
    for ws in workspace_ids:              # Codex Faz 1 #3: kapsam dışı ws -> dur
        assert_workspace_allowed(dict(plan), ws, "profile")
    labels = list(providers)
    with _inputs_dir(inputs_dir):
        frozen = {ws: profile_inputs.load_profile_input(ws) for ws in workspace_ids}
    prompt_shas = {str(ws): _sha(profile_inputs.build_profile_prompt(f)) for ws, f in frozen.items()}
    seed = _seed(plan)
    order = block_random_order(workspace_ids, labels, seed)
    doc: Dict[str, Any] = {
        "kind": "profile_compare", "workspace_ids": list(workspace_ids),
        "providers": {k: list(v) for k, v in providers.items()}, "prompt_shas": prompt_shas,
        "input_sha256": {str(ws): f["input_sha256"] for ws, f in frozen.items()},
        "order": [[ws, lab] for ws, lab in order], "seed": seed, "dry_run": dry_run,
        "results": [], "usage_totals": {}, "budget_summary": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        return seal(doc)
    if ai_factory is None and ledger is None:
        ledger = Faz1Ledger.open(LEDGER_PATH)
    ledger_ref = "profile:" + ",".join(map(str, workspace_ids))
    remaining = None
    if ledger is not None:            # Codex Faz 1 2. tur: KALICI rezervasyon çağrıdan ÖNCE
        remaining = ledger.reserve(kind="profile_compare", ref=ledger_ref,
                                   caps=resolve_caps(plan))
    factory = ai_factory or _default_ai_factory(plan, collector, caps_override=remaining)
    totals: Dict[str, Dict[str, Any]] = {}
    ai = None
    try:
        for ws, label in order:
            provider, model = providers[label]
            ai = factory(label, provider, model)
            profile, usage, err = _call_one(ai, collector, frozen[ws])
            doc["results"].append({"ws": ws, "label": label, "provider": provider,
                                   "model": model, "profile": profile,
                                   "prompt_sha": prompt_shas[str(ws)],
                                   "usage": usage, "error": err})
            t = totals.setdefault(label, {"calls": 0, "errors": 0, "prompt_tokens": 0,
                                          "candidates_tokens": 0, "latency_ms": 0})
            t["calls"] += 1
            t["errors"] += 1 if err else 0
            for k in ("prompt_tokens", "candidates_tokens", "latency_ms"):
                t[k] += usage.get(k) or 0
    finally:                          # hata/kesintide de mahsup (yapılmış çağrılar kaybolmaz)
        summ = getattr(ai, "summary", None)
        doc["budget_summary"] = summ() if callable(summ) else None
        if ledger is not None:
            ledger.settle(ref=ledger_ref, budget_summary=doc["budget_summary"])
    doc["usage_totals"] = totals
    sealed = seal(doc)
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((json.dumps(sealed, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return sealed


# ── 3. deterministik metrikler ──────────────────────────────────────────

def _schema_fields() -> List[str]:
    from app.core.site_analyzer.profile_extractor import PROFILE_RESPONSE_SCHEMA

    return [k for k in PROFILE_RESPONSE_SCHEMA["properties"] if k not in _SKIP_FIELDS]


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, tuple)):
        return all(_empty(x) for x in v)
    if isinstance(v, dict):
        return all(_empty(x) for x in v.values())
    return False


def field_fill(profile: Mapping[str, Any], schema: Optional[Sequence[str]] = None) -> Dict[str, int]:
    fields = list(schema) if schema is not None else _schema_fields()
    return {"filled": sum(1 for k in fields if not _empty(profile.get(k))), "total": len(fields)}


def _tokens(v: Any) -> set:
    if isinstance(v, (list, tuple)):
        return {t for x in v for t in _tokens(x)}
    return set(re.findall(r"\w+", str(v or "").lower()))


def fidelity_to_confirmed(profile: Mapping[str, Any],
                          confirmed: Mapping[str, Any]) -> Dict[str, Any]:
    """Onaylı profilde dolu her alan için token-Jaccard; overlap_ratio = ortalama.
    missing_fields: onaylıda dolu, adayda boş. changed_fields: Jaccard < 1."""
    scores, missing, changed = [], [], []
    for k in _schema_fields():
        c = confirmed.get(k)
        if _empty(c):
            continue
        p = profile.get(k)
        if _empty(p):
            missing.append(k)
            scores.append(0.0)
            continue
        a, b = _tokens(p), _tokens(c)
        j = len(a & b) / len(a | b) if (a | b) else 1.0
        scores.append(j)
        if j < 1.0:
            changed.append(k)
    return {"overlap_ratio": (sum(scores) / len(scores)) if scores else None,
            "missing_fields": missing, "changed_fields": changed}


def claims_for_review(profile: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Cümle-benzeri her string: liste öğesi = bir iddia; düz string cümle sonunda bölünür."""
    out: List[Dict[str, str]] = []
    for k, v in profile.items():
        if k in _SKIP_FIELDS or _empty(v):
            continue
        for it in (v if isinstance(v, list) else [v]):
            for s in re.split(r"(?<=[.!?])\s+", str(it).strip()):
                if s.strip():
                    out.append({"field": k, "claim_text": s.strip()})
    return out


# ── 4. kör kartlar ──────────────────────────────────────────────────────

_LETTERS = "ABCDEFGH"


def render_profile_cards(results: Sequence[Mapping[str, Any]], out_md: str, *,
                         mapping_path: str, seed: int = 0) -> Dict[str, Dict[str, str]]:
    """Kör kart md + AYRI sır eşlemesi ({ws: {harf: label}}). Hatalı üretim karta girmez."""
    by_ws: Dict[int, List[Mapping[str, Any]]] = {}
    for r in results:
        by_ws.setdefault(r["ws"], []).append(r)
    rng = random.Random(seed)
    mapping: Dict[str, Dict[str, str]] = {}
    lines = ["# Profil kör kartları", "",
             "Her iddia için dayanaksızsa `[ ]` -> `[x]` yapın; her profile 1-5 puan yazın.", ""]
    for ws in sorted(by_ws):
        rs = [r for r in by_ws[ws] if r.get("profile")]
        rng.shuffle(rs)
        mapping[str(ws)] = {}
        lines += [f"## WS {ws}", ""]
        for letter, r in zip(_LETTERS, rs):
            mapping[str(ws)][letter] = r["label"]
            lines += [f"### Profil {letter}", ""]
            lines += [f"- [ ] ({c['field']}) {c['claim_text']}"
                      for c in claims_for_review(r["profile"])]
            lines += ["", "Puan (1-5): ", ""]
    Path(out_md).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    Path(mapping_path).write_bytes((json.dumps(mapping, indent=2) + "\n").encode("utf-8"))
    return mapping


def parse_profile_cards(md: str) -> Dict[int, Dict[str, Dict[str, int]]]:
    """{ws: {harf: {unsupported_count, score}}}; eksik/geçersiz puan veya işaret -> ValueError."""
    out: Dict[int, Dict[str, Dict[str, Any]]] = {}
    ws = letter = None
    for raw in md.splitlines():
        line = raw.rstrip()
        if m := re.match(r"^## WS (\d+)$", line):
            ws, letter = int(m.group(1)), None
            out.setdefault(ws, {})
        elif m := re.match(r"^### Profil ([A-H])$", line):
            if ws is None:
                raise ValueError("profil başlığı WS başlığından önce")
            letter = m.group(1)
            if letter in out[ws]:
                raise ValueError(f"WS {ws} profil {letter} tekrarlı")
            out[ws][letter] = {"unsupported_count": 0, "score": None}
        elif m := re.match(r"^- \[(.)\] ", line):
            if letter is None or m.group(1) not in (" ", "x", "X"):
                raise ValueError(f"geçersiz iddia işareti: {line[:60]!r}")
            if m.group(1) in "xX":
                out[ws][letter]["unsupported_count"] += 1
        elif m := re.match(r"^Puan \(1-5\):\s*(.*)$", line):
            if letter is None or not re.fullmatch(r"[1-5]", m.group(1).strip()):
                raise ValueError(f"WS {ws} profil {letter}: puan 1-5 tam sayı olmalı")
            out[ws][letter]["score"] = int(m.group(1))
    for w, d in out.items():
        for lt, v in d.items():
            if v["score"] is None:
                raise ValueError(f"WS {w} profil {lt}: puan eksik")
    if not out:
        raise ValueError("kart bulunamadı")
    return out


def to_gate_input(parsed: Mapping[int, Mapping[str, Mapping[str, int]]],
                  mapping: Mapping[str, Mapping[str, str]], *, ref_label: str,
                  cand_label: str) -> List[Dict[str, Any]]:
    """evaluate_profile satırları: {ws, halluc_cand, halluc_ref, patron_cand, patron_ref}."""
    rows = []
    for ws, letters in parsed.items():
        inv = {mapping[str(ws)][lt]: v for lt, v in letters.items()}
        if ref_label not in inv or cand_label not in inv:
            raise ValueError(f"WS {ws}: {ref_label}/{cand_label} kartı yok (fail-closed)")
        rows.append({"ws": ws, "halluc_cand": inv[cand_label]["unsupported_count"],
                     "halluc_ref": inv[ref_label]["unsupported_count"],
                     "patron_cand": inv[cand_label]["score"],
                     "patron_ref": inv[ref_label]["score"]})
    return rows


# ── 5. CLI ──────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspaces", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--freeze", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--cards")
    ap.add_argument("--out", default="benchmark/profile_compare.json")
    ap.add_argument("--plan", default="benchmark/claude_bakeoff_plan.json")
    ap.add_argument("--mapping", default="benchmark/profile_cards_mapping.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    wss = [int(x) for x in a.workspaces.split(",") if x.strip()]
    if a.freeze:
        from app.database.connection import SessionLocal

        with SessionLocal() as db:
            for ws, p in freeze_inputs(db, wss, overwrite=a.overwrite).items():
                print(f"ws {ws}: {p}")
    elif a.run:
        from app.core.benchmark.hybrid_run import load_plan
        from app.core.telemetry.usage import UsageCollector

        doc = run_profile_compare(workspace_ids=wss, providers=default_providers(),
                                  plan=load_plan(a.plan), collector=UsageCollector(),
                                  out_path=None if a.dry_run else a.out, dry_run=a.dry_run)
        print(f"results={len(doc['results'])} sha={doc['payload_sha256'][:12]}")
    else:
        doc = json.loads(Path(a.out).read_text(encoding="utf-8"))
        render_profile_cards(doc["results"], a.cards, mapping_path=a.mapping, seed=doc["seed"])
        print(f"cards -> {a.cards}; secret mapping -> {a.mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
