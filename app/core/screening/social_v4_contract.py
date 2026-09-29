# -*- coding: utf-8 -*-
"""SOCIAL v4 screening prompt sozlesmesi — SURUMLU ve PINLI.

ADS/SEO YOLUNA DOKUNULMAZ: bu sozlesme YALNIZ SOCIAL kanali icindir.
`candidate_union.PRODUCTION_SCREENING_CONTRACT` (tri-channel, batch=10)
oldugu gibi kalir ve ADS/SEO onunla kosmaya devam eder.

KAYNAK: muhurlu SOCIAL DeepSeek retrieval deneyi. `social_prompt.py`
DEGISTIRILMEDI ve bugun hala AYNI SHA'lari uretiyor (test ile kilitli).

KANONIKLESTIRME TUZAGI (yasanmis hata): response schema SHA'si
`json.dumps(schema, sort_keys=True)` ile uretilir — `separators` VERILMEZ.
`separators=(",", ":")` eklemek 052d85ae... uretir ve muhurlu degerle
UYUSMAZ. Bu kural asagida ACIKCA pinlenmistir.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from app.core.screening import social_prompt

SOCIAL_PROMPT_CONTRACT_V4 = "SOCIAL-SCR-2026-08-18-v4"

# Muhurlu deneyden BIREBIR alinan degerler.
SOCIAL_SCREENING_CONTRACT_V4: Dict[str, Any] = {
    "contract_version": SOCIAL_PROMPT_CONTRACT_V4,
    "channel": "SOCIAL",
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "temperature": 0.0,
    "batch_size": 30,
    "views": 2,
    "view_salts": ("scr-view-a", "scr-view-b"),
    "rank_rule": "mean",
    "prompt_template_sha256":
        "df7a5edfae995f47d99a899459401cfe6397ab1789348048c56d7920f0d78895",
    "response_schema_sha256":
        "4dffa950ce8a8718e72d9d07916c3e49a52dc955d6ec03c3542e81b1a30524ab",
    "schema_canonicalization": "json.dumps(schema, sort_keys=True)",
    "deepseek_authority": "retrieval_only_no_final_keep_reject",
    "forbidden_prompt_inputs": (
        "patron_labels", "baseline_rank", "monthly_volume", "social_score",
        "current_selection", "capacity_K",
    ),
}


def response_schema_sha256() -> str:
    """Muhurlu kanoniklestirme — separators VERILMEZ."""
    return hashlib.sha256(
        json.dumps(social_prompt.RESPONSE_SCHEMA, sort_keys=True)
        .encode("utf-8")).hexdigest()


def prompt_template_sha256() -> str:
    return social_prompt.template_sha256()


def verify_sealed_contract() -> Dict[str, Any]:
    """Canli dosyalar muhurlu SHA'lari HALA uretiyor mu? (fail-closed kullanim)"""
    tpl, sch = prompt_template_sha256(), response_schema_sha256()
    return {
        "prompt_template_matches":
            tpl == SOCIAL_SCREENING_CONTRACT_V4["prompt_template_sha256"],
        "response_schema_matches":
            sch == SOCIAL_SCREENING_CONTRACT_V4["response_schema_sha256"],
        "computed": {"prompt_template_sha256": tpl,
                     "response_schema_sha256": sch},
    }


def identity_fields(*, workspace_id, strategy_fingerprint,
                    universe_sha256, union_contract_version) -> Dict[str, Any]:
    """Screening identity'ye giren SOCIAL v4 alanlari (kor reuse'u kapatir)."""
    c = SOCIAL_SCREENING_CONTRACT_V4
    return {
        "workspace_id": workspace_id,
        "strategy_fingerprint": strategy_fingerprint,
        "keyword_universe_sha256": universe_sha256,
        "provider": c["provider"], "model": c["model"],
        "temperature": c["temperature"], "batch_size": c["batch_size"],
        "prompt_template_sha256": c["prompt_template_sha256"],
        "response_schema_sha256": c["response_schema_sha256"],
        "screening_contract_version": c["contract_version"],
        "union_contract_version": union_contract_version,
    }


# Kanal kapsami: bu deney YALNIZ SOCIAL kosar (ADS/SEO screening YOK).
SOCIAL_V4_CHANNEL_SCOPE = ("SOCIAL",)


def reason_codes_sha256() -> str:
    """Muhurlu SOCIAL reason-code kumesinin SHA'si (kimlik bileseni)."""
    return hashlib.sha256(
        "|".join(social_prompt.REASON_CODES).encode("utf-8")).hexdigest()


def identity_contract() -> Dict[str, Any]:
    """Muhurlu sozlesmenin `screening_input_identity` PROJEKSIYONU.

    Yeni sozlesme DEGILDIR: mevcut kimlik fonksiyonunun bekledigi alan
    adlarina muhurlu degerleri tasir. Tri-channel'a ozgu alanlar
    (virtual_buckets, ensemble) SOCIAL v4'te ardisik dilim kullanildigi
    icin acikca 0 / kendi surumuyle isaretlenir.
    """
    c = SOCIAL_SCREENING_CONTRACT_V4
    return {
        "provider": c["provider"],
        "model": c["model"],
        "prompt_version": c["contract_version"],
        "prompt_template_sha256": c["prompt_template_sha256"],
        "response_schema_sha256": c["response_schema_sha256"],
        "reason_code_version": "SOCIAL-RC-2026-08-17-v1",
        "reason_codes_sha256": reason_codes_sha256(),
        "temperature": float(c["temperature"]),
        "batch_size": int(c["batch_size"]),
        # sticky bucket YOK: batch'leme keyword_id artan ardisik dilim
        "virtual_buckets": 0,
        "view_salts": list(c["view_salts"]),
        "rank_rule": c["rank_rule"],
        "tiebreak": "keyword_id",
        "ensemble_contract_version": "SOCIAL-V4-SEQ-2026-08-18",
    }


def is_social_only_v4(contract_version, applied_channels) -> bool:
    """SOCIAL-only + v4 kapsami mi? TEK PREDIKAT.

    Kapsam bu kosulu saglarsa kosan tek gecis muhurlu SOCIAL v4'tur:
    kimlik, sozlesme pinleri, karar tamligi ve union modu bu sozlesmeden
    okunur. Kapsamda ADS/SEO varsa veya sozlesme v3 ise DAVRANIS
    DEGISMEZ (tri-channel).
    """
    from app.core.screening.candidate_union import SCREENING_CONTRACT_V4

    return (contract_version == SCREENING_CONTRACT_V4
            and tuple(applied_channels) == SOCIAL_V4_CHANNEL_SCOPE)
