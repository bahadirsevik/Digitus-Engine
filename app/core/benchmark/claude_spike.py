# -*- coding: utf-8 -*-
"""Claude spike (plan Faz 0 madde 5): SENTETİK prompt'larla adaptör doğrulama.

python -m app.core.benchmark.claude_spike --out benchmark/claude_spike.json [--dry-run]

Müşteri verisi yok; <= 30 çağrı, tavan 0,50 $. Gemini kontrolleri AYRI adım
(gemini bölümü yer tutucudur).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.telemetry.usage import price_for
from app.generators.anthropic_service import (
    ADAPTER_CONTRACT_VERSION, THINKING_ALLOWANCE, THINKING_POLICY_SHA,
    AnthropicService, anthropic_request_envelope,
    anthropic_request_input_ceiling_bytes)

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
RL_HEADERS = ("requests", "tokens", "input-tokens", "output-tokens")

_OPT = {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "note": {"type": "string"}}, "required": ["title", "summary"]}
_NULLABLE = {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "note": {"type": ["string", "null"]}},
    "required": ["title", "summary", "note"]}
_BRAND = ("Örnek Mobilya: İstanbul merkezli, masif ahşap yemek masası ve "
          "kitaplık üreten kurgusal bir markadır.")
_SITE = (_BRAND + " Ürünler: masif meşe masa, ceviz kitaplık, TV ünitesi. "
         "Hizmetler: ücretsiz keşif, ölçüye özel üretim, montaj. Hedef kitle: "
         "ev dekorasyonuyla ilgilenen aileler. ") * 3


def _cases() -> Dict[str, Dict[str, Any]]:
    from app.core.site_analyzer.profile_extractor import (
        PROFILE_EXTRACTION_PROMPT, PROFILE_RESPONSE_SCHEMA)
    from app.core.social.content_contract import (
        ContentTargetSpec, build_social_content_response_schema)

    return {
        "c1_optional": dict(stage="ads_intent", json_mode=True, schema=_OPT, cap=600,
                            prompt=_BRAND + " Bir başlık ve özet yaz. 'note' "
                            "alanını YAZMA, çıktıdan tamamen çıkar."),
        "c2_nullable": dict(stage="seo_rel", json_mode=True, schema=_NULLABLE, cap=600,
                            prompt=_BRAND + " Bir başlık ve özet yaz. 'note' için "
                            "söyleyecek bir şey yok, değeri null yap."),
        "c3_nested": dict(stage="profile_extract", json_mode=True,
                          schema=PROFILE_RESPONSE_SCHEMA, cap=2000,
                          prompt=PROFILE_EXTRACTION_PROMPT.format(
                              site_content=_SITE,
                              preliminary_info="(kullanıcı tarafından sağlanmadı)")),
        "c4_json_mode": dict(stage="ads_funnel", json_mode=True, schema=None, cap=600,
                             prompt=_BRAND + ' JSON döndür: {"slogan": str, '
                             '"anahtar_kelimeler": [5 adet str]}.'),
        "c5_effort": dict(stage="family_a2", json_mode=False, schema=None, cap=600,
                          prompt="'ahşap masa', 'masif meşe masa fiyatları', 'masa "
                          "nasıl temizlenir', 'ofis masası' kelimelerini satın alma, "
                          "bilgi ve gezinme niyetine ayır; kısa gerekçe yaz."),
        "c6_long": dict(stage="social_brief_contents", json_mode=False, schema=None,
                        cap=2500, prompt=_BRAND + " Bu marka için yaklaşık 1500 "
                        "token uzunluğunda, numaralı 40 maddelik Türkçe sosyal "
                        "medya içerik fikri listesi yaz."),
        # Codex Faz 0 #3: gercek uretim semasi (minItems/maxItems donusumde duser)
        "c7_social_schema": dict(
            stage="social_brief_contents", json_mode=True, cap=1500,
            schema=build_social_content_response_schema(
                ContentTargetSpec(1, "instagram", "post")),
            prompt=_BRAND + " için Instagram gönderisi üret: başlık, metin, "
            "CTA ve 5-20 hashtag; şemaya birebir uy."),
    }


def build_spike_plan() -> List[Dict[str, Any]]:
    cases = _cases()
    # (case, effort, tekrar): haiku 20, sonnet 8, opus yok
    spec = {HAIKU: [("c1_optional", "low", 3), ("c2_nullable", "low", 3),
                    ("c3_nested", "low", 3), ("c4_json_mode", "low", 4),
                    ("c5_effort", "low", 2), ("c5_effort", "medium", 2),
                    ("c6_long", "low", 2), ("c7_social_schema", "low", 1)],
            SONNET: [("c1_optional", "low", 1), ("c2_nullable", "low", 1),
                     ("c3_nested", "low", 1), ("c4_json_mode", "low", 1),
                     ("c5_effort", "low", 1), ("c5_effort", "medium", 1),
                     ("c6_long", "low", 1), ("c7_social_schema", "low", 1)]}
    plan = []
    for model, rows in spec.items():
        for case, effort, reps in rows:
            for rep in range(reps):
                c = cases[case]
                plan.append({"id": f"{model}:{case}:{effort}:{rep}", "model": model,
                             "case": case, "effort": effort, "stage": c["stage"],
                             "prompt": c["prompt"], "json_mode": c["json_mode"],
                             "response_schema": c["schema"], "max_tokens": c["cap"]})
    return plan


def call_ceiling_usd(item: Dict[str, Any]) -> float:
    price = price_for(item["model"])
    env = anthropic_request_envelope(
        item["prompt"], model=item["model"], max_tokens=item["max_tokens"],
        json_mode=item["json_mode"], response_schema=item["response_schema"],
        thinking_level=item["effort"])
    in_bytes = anthropic_request_input_ceiling_bytes(
        item["prompt"], json_mode=item["json_mode"],
        response_schema=item["response_schema"])
    return in_bytes / 1e6 * price["input"] + env["max_tokens"] / 1e6 * price["output"]


def _probe_item() -> Dict[str, Any]:
    return {"id": "headers_probe", "model": HAIKU, "case": "headers_probe",
            "effort": "low", "stage": "unknown", "prompt": "ping", "json_mode": False,
            "response_schema": None, "max_tokens": 16}


def _settled_usd(model: str, rec: Dict[str, Any]) -> Optional[float]:
    p, out = rec.get("prompt_tokens"), rec.get("candidates_tokens")
    if p is None or out is None:
        return None
    price = price_for(model)
    read = rec.get("cache_read_tokens") or 0
    # shortcut: cache yazımı taban girdi fiyatından (spike'ta cache kullanılmaz)
    return ((p - read) * price["input"] + read * price["input_cache_hit"]
            + out * price["output"]) / 1e6


class _Collector:
    def __init__(self):
        self.last: Dict[str, Any] = {}

    def record(self, **kw):
        self.last = kw


def _pct(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def _parsed(text: str) -> Optional[dict]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def _headers(raw_headers) -> Dict[str, Any]:
    out = {}
    for dim in RL_HEADERS:
        for suffix in ("limit", "remaining", "reset"):
            name = f"anthropic-ratelimit-{dim}-{suffix}"
            out[name] = raw_headers.get(name)
    return out


def _git_sha() -> str:
    sha = os.environ.get("APP_GIT_SHA")
    if sha:
        return sha
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _seal(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import anthropic
        version = anthropic.__version__
    except Exception:
        version = "unknown"
    from app.core.social.anthropic_schema import SCHEMA_TRANSFORMER_VERSION

    sealed = dict(body)
    sealed.update(
        anthropic_version=version,
        price_valid_from={m: price_for(m)["valid_from"] for m in (HAIKU, SONNET)},
        git_sha=_git_sha(), adapter_contract_version=ADAPTER_CONTRACT_VERSION,
        thinking_policy_sha=THINKING_POLICY_SHA,
        schema_transformer_version=SCHEMA_TRANSFORMER_VERSION,
        created_at=datetime.now(timezone.utc).isoformat())
    sealed["payload_sha256"] = hashlib.sha256(json.dumps(
        sealed, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return sealed


def _one_call(svc, item, collector) -> Dict[str, Any]:
    collector.last = {}
    row: Dict[str, Any] = {"id": item["id"], "model": item["model"],
                           "case": item["case"], "effort": item["effort"],
                           "stage": item["stage"], "max_tokens_caller": item["max_tokens"],
                           "status": "ok", "schema_accepted": None}
    has_schema = item["response_schema"] is not None
    try:
        text = svc._execute(
            item["prompt"], max_tokens=item["max_tokens"], temperature=None,
            json_mode=item["json_mode"], response_schema=item["response_schema"],
            stage=item["stage"], model=item["model"], thinking_level=item["effort"])
    except Exception as exc:
        msg = str(exc)
        row["status"] = "error"
        row["error"] = msg[:500]
        if has_schema and "bad request (invalid argument)" in msg:
            row["schema_accepted"] = False
            row["schema_error"] = msg[:500]
        text = ""
    else:
        if has_schema:
            row["schema_accepted"] = True
    rec = collector.last
    usage_in = rec.get("prompt_tokens")
    prompt_bytes = len(item["prompt"].encode("utf-8"))
    row.update(
        stop_reason=rec.get("finish_reason"), text_len=len(text),
        input_tokens=usage_in, output_tokens=rec.get("candidates_tokens"),
        cache_read_tokens=rec.get("cache_read_tokens"),
        cache_creation_tokens=rec.get("cache_creation_tokens"),
        prompt_utf8_bytes=prompt_bytes,
        tokenizer_ratio=(round(usage_in / prompt_bytes, 4) if usage_in else None),
        latency_ms=rec.get("latency_ms"), queue_wait_ms=rec.get("queue_wait_ms"),
        cost_usd=_settled_usd(item["model"], rec), visible_tokens=None,
        thinking_tokens=None)
    if item["case"] in ("c1_optional", "c2_nullable") and text:
        obj = _parsed(text)
        if obj is not None:
            row["note_absent"] = "note" not in obj
            row["note_null"] = obj.get("note", "x") is None
    if text and row["output_tokens"] is not None:
        try:   # shortcut: user rolüyle sayılır, birkaç token şablon payı içerir
            n = svc.client.messages.count_tokens(
                model=item["model"],
                messages=[{"role": "user", "content": text}]).input_tokens
            row["visible_tokens"] = n
            row["thinking_tokens"] = max(0, row["output_tokens"] - n)
        except Exception as exc:
            row["count_tokens_error"] = str(exc)[:200]
    return row


def _probe_headers(svc, item) -> Dict[str, Any]:
    env = anthropic_request_envelope(
        item["prompt"], model=item["model"], max_tokens=item["max_tokens"],
        json_mode=False, response_schema=None, thinking_level="low")
    try:   # adaptör ham başlıkları açmaz: SDK istemcisine doğrudan, TEK istek
        raw = svc.client.messages.with_raw_response.create(**env)
        return {"headers": _headers(raw.headers), "error": None}
    except Exception as exc:
        return {"headers": None, "error": str(exc)[:300]}


def _summary(rows: List[Dict[str, Any]], total_ceiling: float) -> Dict[str, Any]:
    per_model = {}
    for m in (HAIKU, SONNET):
        costs = [r["cost_usd"] for r in rows
                 if r["model"] == m and r.get("cost_usd") is not None]
        per_model[m] = {"n_settled": len(costs), "p50_usd": _pct(costs, .5),
                        "p90_usd": _pct(costs, .9)}
    schema_rows = [r for r in rows if r.get("schema_accepted") is not None]
    c1 = [r for r in schema_rows if r["case"] == "c1_optional"]
    rec = {}
    for eff in THINKING_ALLOWANCE:
        th = [r["thinking_tokens"] for r in rows
              if r["effort"] == eff and r.get("thinking_tokens") is not None]
        p99 = _pct(th, .99)
        rec[eff] = {"n": len(th), "thinking_p99": p99,
                    "recommended": None if p99 is None else math.ceil(p99 / 100) * 100,
                    "current": THINKING_ALLOWANCE[eff]}
    return {
        "per_model_cost": per_model,
        "total_settled_usd": round(sum(r["cost_usd"] for r in rows if r.get("cost_usd")), 6),
        "total_ceiling_usd": round(total_ceiling, 6),
        "schemas_accepted": sum(1 for r in schema_rows if r["schema_accepted"]),
        "schemas_rejected": sum(1 for r in schema_rows if not r["schema_accepted"]),
        "optional_fields_accepted": (all(r["schema_accepted"] for r in c1) if c1 else None),
        "max_tokens_stops": [r["id"] for r in rows if r.get("stop_reason") == "max_tokens"],
        "thinking_allowance_recommendation": rec,
    }


def run_spike(plan: List[Dict[str, Any]], *, out_path, cap_usd: float = 0.50,
              max_calls: int = 30,
              service_factory: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    svc = (service_factory or AnthropicService)()
    collector = _Collector()
    svc.collector = collector
    rows: List[Dict[str, Any]] = []
    spent, calls = 0.0, 0
    headers: Dict[str, Any] = {"headers": None, "error": "skipped: cap"}

    probe = _probe_item()
    ceil = call_ceiling_usd(probe)
    if calls < max_calls and spent + ceil <= cap_usd:
        spent, calls = spent + ceil, calls + 1
        headers = _probe_headers(svc, probe)
    for item in plan:
        base = {"id": item["id"], "model": item["model"], "case": item["case"],
                "effort": item["effort"], "stage": item["stage"]}
        try:
            ceil = call_ceiling_usd(item)
        except Exception as exc:   # örn. şema dönüştürücü reddi: HTTP öncesi
            rows.append({**base, "status": f"envelope_error: {exc}"[:300],
                         "schema_accepted": False if item["response_schema"] else None})
            continue
        if calls >= max_calls or spent + ceil > cap_usd:
            rows.append({**base, "status": "skipped: cap", "ceiling_usd": ceil})
            continue
        spent, calls = spent + ceil, calls + 1
        row = _one_call(svc, item, collector)
        row["ceiling_usd"] = ceil
        rows.append(row)
    body = {"cap_usd": cap_usd, "max_calls": max_calls, "calls_made": calls,
            "ratelimit_probe": headers, "rows": rows,
            "summary": _summary(rows, spent),
            "gemini": {"thoughts_tokens_check": "not run"}}
    sealed = _seal(body)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(
        (json.dumps(sealed, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return sealed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmark/claude_spike.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cap-usd", type=float, default=0.50)
    ap.add_argument("--max-calls", type=int, default=30)
    a = ap.parse_args(argv)
    plan = build_spike_plan()
    if a.dry_run:
        total = call_ceiling_usd(_probe_item())
        print(f"{'headers_probe':50s} ceiling ${total:.5f}")
        for it in plan:
            c = call_ceiling_usd(it)
            total += c
            print(f"{it['id']:50s} ceiling ${c:.5f}")
        print(f"calls={len(plan) + 1} (plan {len(plan)} + probe 1)  "
              f"total ceiling=${total:.4f}  cap=${a.cap_usd}")
        return 0
    res = run_spike(plan, out_path=a.out, cap_usd=a.cap_usd, max_calls=a.max_calls)
    print(f"wrote {a.out}: calls={res['calls_made']} "
          f"settled=${res['summary']['total_settled_usd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
