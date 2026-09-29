# -*- coding: utf-8 -*-
"""İki AYRI kimlik: provider çağrısı vs aday materyalizasyonu (plan §5.6).

`screening_input_identity_sha256`
    DeepSeek çağrısının kimliği. Aynı kimlikte model kararı ÜCRETSİZ
    yeniden kullanılır (reuse). Bileşenler: run, sıralı evren (id+metin),
    dondurulmuş bağlam SHA, provider/model/prompt/reason-code/şema,
    temperature/batch/kova/tuz/merge sözleşmesi ve UYGULANAN KANAL KAPSAMI.

`candidate_materialization_identity_sha256`
    Aday kümesinin kimliği. Relevance katsayısı/verisi, kapasite veya
    aktif kanal değişirse DeepSeek TEKRAR ÇAĞRILMAZ; yalnız aday kümesi
    yeniden materyalize edilir.

Kapsam (`applied_screening_channels`) İKİ kimliğe de girer: kapsam
değişirse (ör. SOCIAL sonradan dahil edilirse) eski screening kararı
yeniden kullanılamaz ve aday kümesi yeniden kurulur.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    SCREENING_APPLIED_CHANNELS_V3,
    UNION_CONTRACT_V3,
)
from app.core.screening.context import CONTEXT_CONTRACT_VERSION

# Codex 5. tur #3: prompt/şema pinleri runner DAVRANIŞINI kapsamıyordu.
# `max_output_tokens`, retry limitleri ve unresolved fallback semantiği
# değişirse AYNI reuse kimliğiyle FARKLI sonuç üretilebilirdi. Bu blok
# runner sözleşmesini kimliğe bağlar; değeri değişen her sabit yeni
# `SCREENING_RUNNER_CONTRACT_VERSION` gerektirir.
# v2: tekil-retry bütçesi GÖRÜNÜM seviyesinde paylaşılır
SCREENING_RUNNER_CONTRACT_VERSION = "SCRRUN-2026-07-31-v2"


def screening_runner_contract() -> Dict[str, Any]:
    """Runner davranış sabitlerinin CANLI değerleri (tek kaynak: kod)."""
    from app.core.screening.contract import MAX_OUTPUT_TOKENS
    from app.core.screening.runner import (
        MAX_MISSING_RETRIES,
        MAX_PARSE_RETRIES,
        MAX_TRANSIENT_RETRIES,
        SINGLE_RETRY_LIMIT,
        UNRESOLVED_FALLBACK_FIT,
    )

    return {
        "runner_contract_version": SCREENING_RUNNER_CONTRACT_VERSION,
        # Tekil retry kotasının KAPSAMI sözleşmenin parçasıdır: batch
        # seviyesine kayarsa maliyet üst sınırı sessizce büyür
        "single_retry_scope": "view",
        "max_output_tokens": int(MAX_OUTPUT_TOKENS),
        "max_transient_retries": int(MAX_TRANSIENT_RETRIES),
        "max_parse_retries": int(MAX_PARSE_RETRIES),
        "max_missing_retries": int(MAX_MISSING_RETRIES),
        "single_retry_limit": int(SINGLE_RETRY_LIMIT),
        "unresolved_fallback_fit": int(UNRESOLVED_FALLBACK_FIT),
    }

IDENTITY_CONTRACT_VERSION = "SCRID-2026-07-30-v2"
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ScreeningIdentityError(RuntimeError):
    """Kimlik girdisi eksik/geçersiz — hash üretilmez (fail-closed)."""


def _canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ScreeningIdentityError(msg)


def _require_sha(value: Optional[str], name: str) -> str:
    # Codex 5. tur #5: yalnız uzunluk değil, 64 hane HEX
    _require(isinstance(value, str) and bool(_FULL_SHA_RE.match(value)),
             f"{name} tam 64 hane hex SHA olmalı: {value!r}")
    return value


def _require_channels(channels: Sequence[str], name: str) -> list:
    """Kanonik KANAL LİSTESİ: tekrarsız + SIRALI.

    Codex 5. tur #2: sıralamadan geçmeyen liste ("SEO","ADS") ile
    ("ADS","SEO") farklı hash üretiyordu — aynı kapsam, iki kimlik.
    """
    _require(bool(channels), f"{name} boş olamaz")
    out = [str(c) for c in channels]
    _require(len(set(out)) == len(out),
             f"{name} tekrarlı kanal içeremez: {out}")
    return sorted(out)


def universe_sha256(rows: Iterable[Mapping[str, Any]]) -> str:
    """Sıralı (keyword_id, metin) evren hash'i.

    Sıra keyword_id'ye göre KANONİKLEŞTİRİLİR: DB dönüş sırası hash'i
    oynatamaz. Metin de girer — aynı id'nin metni değişirse (import
    onarımı) bağlam gerçekten değişmiştir.
    """
    items = []
    seen = set()
    for row in rows:
        kid = row.get("keyword_id")
        _require(isinstance(kid, int) and not isinstance(kid, bool),
                 f"evren satırında geçersiz keyword_id: {kid!r}")
        _require(kid not in seen, f"evren içinde tekrarlanan id: {kid}")
        seen.add(kid)
        text = row.get("text")
        _require(isinstance(text, str) and text != "",
                 f"keyword {kid} için metin boş")
        items.append([kid, text])
    _require(bool(items), "evren boş — kimlik üretilemez")
    items.sort(key=lambda pair: pair[0])
    return _canonical_sha(items)


def screening_input_identity(
    *,
    scoring_run_id: int,
    universe_rows: Iterable[Mapping[str, Any]],
    context_sha256: str,
    applied_screening_channels: Sequence[str] = SCREENING_APPLIED_CHANNELS_V3,
    contract: Dict[str, Any] = PRODUCTION_SCREENING_CONTRACT,
    union_contract_version: str = UNION_CONTRACT_V3,
    runner_contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Provider çağrısı kimliği + reuse için tam bileşen dökümü."""
    _require(isinstance(scoring_run_id, int)
             and not isinstance(scoring_run_id, bool) and scoring_run_id > 0,
             f"scoring_run_id pozitif tam sayı olmalı: {scoring_run_id!r}")
    uni_sha = universe_sha256(universe_rows)
    ctx_sha = _require_sha(context_sha256, "context_sha256")
    channels = _require_channels(applied_screening_channels,
                                 "applied_screening_channels")
    components = {
        "identity_contract_version": IDENTITY_CONTRACT_VERSION,
        "context_contract_version": CONTEXT_CONTRACT_VERSION,
        "union_contract_version": union_contract_version,
        "scoring_run_id": scoring_run_id,
        "universe_sha256": uni_sha,
        "context_sha256": ctx_sha,
        "applied_screening_channels": channels,
        "provider": contract["provider"],
        "model": contract["model"],
        "prompt_version": contract["prompt_version"],
        "prompt_template_sha256": _require_sha(
            contract["prompt_template_sha256"], "prompt_template_sha256"),
        "reason_code_version": contract["reason_code_version"],
        "reason_codes_sha256": _require_sha(
            contract["reason_codes_sha256"], "reason_codes_sha256"),
        "response_schema_sha256": _require_sha(
            contract["response_schema_sha256"], "response_schema_sha256"),
        "temperature": float(contract["temperature"]),
        "batch_size": int(contract["batch_size"]),
        "virtual_buckets": int(contract["virtual_buckets"]),
        "view_salts": list(contract["view_salts"]),
        "rank_rule": contract["rank_rule"],
        "tiebreak": contract["tiebreak"],
        "ensemble_contract_version": contract["ensemble_contract_version"],
        # Codex 5. tur #3: runner davranışı da kimliğin parçası
        "runner_contract": dict(runner_contract
                                if runner_contract is not None
                                else screening_runner_contract()),
    }
    return {"sha256": _canonical_sha(components), "components": components}


def candidate_materialization_identity(
    *,
    scoring_run_id: int,
    screening_input_identity_sha256: Optional[str],
    channel_rank_snapshot_sha256: str,
    relevance_rows_sha256: Optional[str],
    relevance_anchor_version: Optional[int],
    relevance_coefficient: Optional[float],
    active_channels: Sequence[str],
    capacities: Mapping[str, int],
    budgets: Mapping[str, Mapping[str, int]],
    algorithm_version: str,
    policy_version: Optional[int],
    strategy_version: Optional[int],
    assignment_version: Optional[int],
    applied_screening_channels: Sequence[str] = SCREENING_APPLIED_CHANNELS_V3,
    union_contract_version: str = UNION_CONTRACT_V3,
) -> Dict[str, Any]:
    """Aday kümesi kimliği.

    `screening_input_identity_sha256=None` → screening'siz (off) yol:
    kimlik yalnız deterministik girdilerden kurulur, böylece flag-off
    materyalizasyonu da denetlenebilir kalır. `scoring_run_id` ZORUNLU
    bileşendir (Codex 5. tur #1): off yolunda run kimliği başka hiçbir
    alandan gelmediği için iki farklı run aynı hash'i üretebilirdi.
    """
    _require(isinstance(scoring_run_id, int)
             and not isinstance(scoring_run_id, bool) and scoring_run_id > 0,
             f"scoring_run_id pozitif tam sayı olmalı: {scoring_run_id!r}")
    if screening_input_identity_sha256 is not None:
        _require_sha(screening_input_identity_sha256,
                     "screening_input_identity_sha256")
    _require_sha(channel_rank_snapshot_sha256,
                 "channel_rank_snapshot_sha256")
    if relevance_rows_sha256 is not None:
        _require_sha(relevance_rows_sha256, "relevance_rows_sha256")
    channels = _require_channels(active_channels, "active_channels")
    _require(set(capacities) >= set(channels),
             f"kapasite eksik: {sorted(set(channels) - set(capacities))}")
    _require(set(budgets) >= set(channels),
             f"bütçe eksik: {sorted(set(channels) - set(budgets))}")
    for ch in channels:
        blk = budgets[ch]
        for key in ("B", "T", "U"):
            _require(key in blk, f"{ch} bütçesinde '{key}' yok")
            _require(isinstance(blk[key], int)
                     and not isinstance(blk[key], bool) and blk[key] >= 0,
                     f"{ch}.{key} negatif olmayan tam sayı olmalı: "
                     f"{blk[key]!r}")
    components = {
        "identity_contract_version": IDENTITY_CONTRACT_VERSION,
        "union_contract_version": union_contract_version,
        "scoring_run_id": scoring_run_id,
        "screening_input_identity_sha256": screening_input_identity_sha256,
        "channel_rank_snapshot_sha256": channel_rank_snapshot_sha256,
        "relevance_rows_sha256": relevance_rows_sha256,
        "relevance_anchor_version": relevance_anchor_version,
        "relevance_coefficient": (None if relevance_coefficient is None
                                  else float(relevance_coefficient)),
        "active_channels": channels,
        "capacities": {ch: int(capacities[ch]) for ch in channels},
        "budgets": {ch: {k: int(budgets[ch][k]) for k in ("B", "T", "U")}
                    for ch in channels},
        "algorithm_version": str(algorithm_version),
        "policy_version": policy_version,
        "strategy_version": strategy_version,
        "assignment_version": assignment_version,
        "applied_screening_channels": _require_channels(
            applied_screening_channels, "applied_screening_channels"),
    }
    return {"sha256": _canonical_sha(components), "components": components}


def channel_rank_snapshot_sha256(rank_by_channel: Mapping[str, Mapping[int, int]]
                                 ) -> str:
    """Kanal başına (keyword_id -> raw rank) anlık görüntüsünün hash'i."""
    _require(bool(rank_by_channel), "rank snapshot boş")
    payload = {
        ch: sorted([[int(kid), int(rank)] for kid, rank in ranks.items()],
                   key=lambda pair: pair[0])
        for ch, ranks in sorted(rank_by_channel.items())
    }
    return _canonical_sha(payload)


def relevance_rows_sha256(rows: Mapping[int, Optional[float]]) -> str:
    """Relevance satırlarının hash'i (id -> skor; None ayrı değerdir)."""
    payload = sorted(
        [[int(kid), (None if score is None else round(float(score), 6))]
         for kid, score in rows.items()], key=lambda pair: pair[0])
    return _canonical_sha(payload)
