# -*- coding: utf-8 -*-
"""Cache reuse UYGUNLUGU — kor reuse YASAK.

Onceki bir screening karari YENIDEN KULLANILABILIR ancak su alanlarin
TAMAMI birebir eslesirse:

    workspace_id, strategy_fingerprint,
    keyword_universe_sha256,
    provider, model, temperature, batch_size,
    prompt_template_sha256, response_schema_sha256,
    screening_contract_version, union_contract_version

Bir alan DOGRULANAMIYORSA (bilinmiyor/None) bu bir esitlik SAYILMAZ —
`unverifiable` olarak isaretlenir ve reuse REDDEDILIR. "Cagrilar zaten
yapilmisti" varsayimiyla kor reuse YAPILMAZ.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

REQUIRED_FIELDS = (
    "workspace_id",
    "strategy_fingerprint",
    "keyword_universe_sha256",
    "provider",
    "model",
    "temperature",
    "batch_size",
    "prompt_template_sha256",
    "response_schema_sha256",
    "screening_contract_version",
    "union_contract_version",
)

VERDICT_HIT = "cache_hit"
VERDICT_MISS = "cache_miss"
VERDICT_UNVERIFIABLE = "cache_unverifiable"


def compare(candidate: Dict[str, Any],
            required: Dict[str, Any]) -> Dict[str, Any]:
    """Alan alan karsilastirma. Eksik/None -> unverifiable (reuse YOK)."""
    rows = []
    mismatches, unverifiable = [], []
    for field in REQUIRED_FIELDS:
        got = candidate.get(field, None)
        want = required.get(field, None)
        if got is None or want is None:
            state = "unverifiable"
            unverifiable.append(field)
        elif str(got) == str(want):
            state = "match"
        else:
            state = "mismatch"
            mismatches.append(field)
        rows.append({"field": field, "expected": want, "found": got,
                     "state": state})
    if unverifiable:
        verdict = VERDICT_UNVERIFIABLE
    elif mismatches:
        verdict = VERDICT_MISS
    else:
        verdict = VERDICT_HIT
    return {
        "verdict": verdict,
        "reusable": verdict == VERDICT_HIT,
        "mismatched_fields": mismatches,
        "unverifiable_fields": unverifiable,
        "rows": rows,
        "rule": ("reuse YALNIZ tum alanlar 'match' ise; tek bir "
                 "unverifiable/mismatch reuse'u REDDEDER"),
    }
