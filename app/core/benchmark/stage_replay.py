"""Faz 1A: yakalanmis istekleri baska saglayiciya AYNI prompt ile replay et.

Her yakalanmis `initial` / `attempt==1` cagri ayni motor fonksiyonundan
(`ai_runner.run_batch` / `run_single`) gecer: parse + tekrar butcesi uretimle
ayni. `ai` cagiran tarafindan BudgetGuardedAI ile sarilmis gelir; burasi
FIYATLAMA YAPMAZ, ag kullanmaz.

Hedefli tekrar replay'de KAPALIDIR (Codex Faz 1 #2): eksik-ID alt kumesinin
prompt'u uretimle birebir kurulamaz; kiyas iki tarafta da ILK DENEME ile
simetriktir, teslim-sonrasi kiyas 1B'nin isidir.
upgrade if 1A retry rates matter.
shortcut: `response_parser` kullanan asamalar (SOCIAL) icin parser, cagiranin
`response_parsers={stage: fn}` argumaniyla verilmeli; verilmezse varsayilan
parse kullanilir.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.core.benchmark.reference_characterization import (
    CharacterizationError, _sha, seal,
)
from app.core.engine import ai_runner
from app.core.telemetry.ai_cost_budget import BudgetExceeded

LONG_TEXT_CHARS = 80


def _usage(events: List[dict]) -> Dict[str, Any]:
    def tot(key):
        return sum(e.get(key) or 0 for e in events)
    return {"prompt": tot("prompt_tokens"), "candidates": tot("candidates_tokens"),
            "thoughts": tot("thoughts_tokens"), "n_events": len(events)}


def replay_capture(calls: List[Dict[str, Any]], *, ai: Any, provider: str,
                   model_for_stage: Mapping[str, str],
                   stages: Optional[set] = None, collector: Any,
                   out_path: Path, source_run_id: Optional[str] = None,
                   source_calls_sha256: Optional[str] = None,
                   response_parsers: Optional[Mapping[str, Callable]] = None
                   ) -> Dict[str, Any]:
    todo = [c for c in calls
            if (stages is None or c["stage"] in stages)
            and c.get("attempt_kind") == "initial" and c.get("attempt", 1) == 1]
    missing = sorted({c["stage"] for c in todo} - set(model_for_stage))
    if missing:                          # fail-closed: hicbir cagri yapilmadan
        raise CharacterizationError(f"model_for_stage eksik: {missing}")
    parsers = response_parsers or {}
    need_parser = sorted({c["stage"] for c in todo
                          if c.get("has_response_parser")} - set(parsers))
    if need_parser:                      # Codex Faz 1 #2: sessiz varsayilan parse YOK
        raise CharacterizationError(
            f"asama(lar) uretimde response_parser kullaniyor, replay'e verilmedi: "
            f"{need_parser}")
    out_calls: List[Dict[str, Any]] = []
    by_provider: Dict[str, List[dict]] = {}
    aborted: Optional[BaseException] = None

    for idx, c in enumerate(todo):
        stage, model = c["stage"], model_for_stage[c["stage"]]
        ids = list(c.get("expected_ids") or [])
        common = dict(stage=stage, model=model, provider=provider,
                      thinking_level=c["thinking_level"],
                      schema=c["response_schema"], max_tokens=c["max_tokens"],
                      result_key=c.get("result_key"))
        rec: Dict[str, Any] = {"request_index": idx, "source_seq": c.get("seq"),
                               "stage": stage, "provider": provider,
                               "model": model, "ok": False, "error": None,
                               "parsed": None}
        with collector.logical_request():
            rid = collector._local.ctx["request_id"]
            try:
                if ids:
                    id_field = c.get("id_field") or "keyword_id"
                    # Codex Faz 1 #2: alt kume prompt'u uretimle birebir
                    # kurulamaz -> hedefli tekrar KAPALI; ilk-deneme kismi
                    # sonuc + eksik ID listesi doner (karsilastirma iki
                    # tarafta da ILK DENEME ile simetriktir).
                    got = ai_runner.run_batch(
                        ai, rows=[{id_field: i} for i in ids],
                        build_prompt=lambda _subset, p=c["prompt"]: p,
                        id_field=id_field,
                        response_id_field=c.get("response_id_field") or "id",
                        response_parser=parsers.get(stage),
                        targeted_retries=0, allow_missing=True, **common)
                    rec["parsed"] = {str(k): v for k, v in got.items()}
                    rec["missing_ids"] = [i for i in ids if i not in got]
                    rec["targeted_retry_replayed"] = False
                else:
                    rec["parsed"] = ai_runner.run_single(
                        ai, prompt=c["prompt"], **common)
                rec["ok"] = True
            except BudgetExceeded as exc:
                rec["error"] = f"BudgetExceeded: {exc}"[:300]
                aborted = exc
            except Exception as exc:     # tek cagri hatasi batch'i durdurmaz
                rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
                if exc.__cause__ is not None:
                    rec["error"] += f" <- {type(exc.__cause__).__name__}"
        events = [e for e in collector._all_events if e.get("request_id") == rid]
        rec.update(latency_ms=sum(e.get("latency_ms") or 0 for e in events),
                   queue_wait_ms=sum(e.get("queue_wait_ms") or 0 for e in events),
                   http_attempts=sum(e.get("http_attempts") or 0 for e in events),
                   retry_reasons=[e["retry_reason"] for e in events
                                  if e.get("retry_reason")],
                   finish_reasons=[e.get("finish_reason") for e in events],
                   usage=_usage(events), usage_missing=not events)
        for e in events:
            by_provider.setdefault(e.get("provider") or provider, []).append(e)
        out_calls.append(rec)
        if aborted is not None:
            break

    artifact = seal({
        "kind": "stage_replay", "source_run_id": source_run_id,
        "source_calls_sha256": source_calls_sha256 or _sha(calls),
        "provider": provider,
        "models": {s: model_for_stage[s] for s in sorted({c["stage"] for c in todo})},
        "stages": sorted({c["stage"] for c in todo}),
        "n_calls": len(out_calls), "n_planned": len(todo),
        "n_ok": sum(r["ok"] for r in out_calls),
        "n_error": sum(not r["ok"] for r in out_calls),
        "aborted": None if aborted is None else str(aborted)[:300],
        "calls": out_calls,
        "usage_totals": {p: _usage(ev) for p, ev in sorted(by_provider.items())},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")
    if aborted is not None:
        raise aborted
    return artifact


# -- asama karari uyumu (plan 7.2) — betimleyici, asama bilgisi YOK ----------

def _decision(item: Any) -> Any:
    """Ogeden >80 karakterlik serbest metin alanlari cikarilmis hali."""
    if isinstance(item, Mapping):
        return {k: _decision(v) for k, v in item.items()
                if not (isinstance(v, str) and len(v) > LONG_TEXT_CHARS)}
    if isinstance(item, list):
        return [_decision(v) for v in item]
    return item


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def compare_stage_outputs(ref_calls: List[Dict[str, Any]],
                          cand_artifact: Mapping[str, Any]) -> Dict[str, Any]:
    """Yakalanmis (referans) ham cevaplar vs aday replay ciktisi, asama bazinda."""
    ref_by_seq = {c["seq"]: c for c in ref_calls}
    stages: Dict[str, Dict[str, Any]] = {}
    for cand in cand_artifact["calls"]:
        ref = ref_by_seq[cand["source_seq"]]
        st = stages.setdefault(cand["stage"], {
            "n_calls": 0, "n_cand_error": 0, "n_ref_unparseable": 0,
            "n_ids_ref": 0, "n_ids_cand": 0, "n_both": 0, "n_agree": 0,
            "only_ref_ids": [], "only_cand_ids": [], "fields": {},
            "n_single": 0, "n_single_agree": 0})
        st["n_calls"] += 1
        st["n_cand_error"] += not cand["ok"]
        items = ai_runner._parse(ref.get("raw_text"), ref.get("result_key"))
        if items is None:
            st["n_ref_unparseable"] += 1
            items = []
        if not ref.get("expected_ids"):          # ID'siz (single) asama
            st["n_single"] += 1
            st["n_single_agree"] += bool(cand["ok"] and _canon(_decision(items))
                                         == _canon(_decision(cand["parsed"])))
            continue
        rid = ref.get("response_id_field") or "id"
        r = {str(int(i[rid])): i for i in items
             if isinstance(i, Mapping) and rid in i}
        k = cand["parsed"] or {}
        st["n_ids_ref"] += len(r)
        st["n_ids_cand"] += len(k)
        st["only_ref_ids"] += sorted(set(r) - set(k))
        st["only_cand_ids"] += sorted(set(k) - set(r))
        for i in sorted(set(r) & set(k)):
            a, b = _decision(r[i]), _decision(k[i])
            st["n_both"] += 1
            st["n_agree"] += _canon(a) == _canon(b)
            for f in set(a) & set(b):
                s = st["fields"].setdefault(f, {"n": 0, "agree": 0})
                s["n"] += 1
                s["agree"] += _canon(a[f]) == _canon(b[f])
    for st in stages.values():
        st["decision_agreement"] = (st["n_agree"] / st["n_both"]
                                    if st["n_both"] else None)
        for s in st["fields"].values():
            s["rate"] = s["agree"] / s["n"]
    return {"source_calls_sha256": cand_artifact.get("source_calls_sha256"),
            "provider": cand_artifact.get("provider"), "stages": stages}
