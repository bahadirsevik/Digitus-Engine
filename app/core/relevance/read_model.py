"""Relevance OKUMA kapisi — tek dogruluk noktasi (plan_marka_profili_sadakati.md P1.4).

Sorun: `KeywordRelevance` satirlari iki ayri yerden, IKI FARKLI sozlesmeyle
okunuyordu.

- `pool_builder._load_relevance_map` : bayrak + confirmed + deleted_at
- `channel_engine._load_relevance_map_for_pooling` : HICBIR KAPI YOK

Ikincisi FINAL SECIMI besliyor (`adjusted = max(base,0) * relevance * coef`),
yani daha zayif kapiya sahip olan yol daha agir sonuca sahipti. Boylece
bayrak kapaliyken, `skip_relevance=true` kosuda, profil draft/arsivliyken veya
anchor surumu bayatken eski relevance degerleri final siralamaya girebiliyordu
— sessiz bir kalite hatasi.

SOZLESME: kapi, `app/core/policy/freshness.py` icindeki `relevance_stale`
kuralinin DUALIDIR. Relevance yalnizca su bes kosulun HEPSI saglanirsa
uygulanir:

  1. ENABLE_RELEVANCE_RERANK acik
  2. run mevcut ve bir workspace'e bagli
  3. run.skip_relevance false
  4. workspace confirmed ve silinmemis
  5. run.relevance_anchor_version == workspace.anchor_version

Dikkat: kapi literal olarak `not relevance_stale` DEGILDIR. `relevance_required`
false iken (bayrak kapali / skip_relevance) freshness "bayat degil" der ama
relevance yine de UYGULANMAMALIDIR — bu iki soru farklidir:
  - freshness: "bu havuz yeniden kurulmali mi?"
  - read model: "relevance carpani uygulanmali mi?"

Kapi kapaliyken bos map doner; cagiranlar zaten eksik anahtar icin notr 0.5
varsayilanini uyguladigi icin siralama notr relevance ile kurulur.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import BrandProfile, KeywordRelevance, ScoringRun

# Relevance satiri bulunmayan keyword icin cagiranlarin uyguladigi notr carpan.
# Burada KULLANILMAZ; kapinin "bos map = notr siralama" davranisini
# belgelemek icin tutulur.
NEUTRAL_RELEVANCE = 0.5


@dataclass(frozen=True)
class RelevanceGateDecision:
    """applied=False ise `reason` neden uygulanmadigini soyler (telemetri/log)."""

    applied: bool
    reason: str


def evaluate_relevance_gate(
    db: Session, scoring_run: Optional[ScoringRun]
) -> RelevanceGateDecision:
    """Relevance carpani bu run icin uygulanabilir mi?"""
    if not getattr(settings, "ENABLE_RELEVANCE_RERANK", False):
        return RelevanceGateDecision(False, "flag_disabled")
    if scoring_run is None:
        return RelevanceGateDecision(False, "run_not_found")
    if getattr(scoring_run, "skip_relevance", False):
        return RelevanceGateDecision(False, "skip_relevance")
    if not scoring_run.brand_profile_id:
        return RelevanceGateDecision(False, "no_workspace")

    profile = (
        db.query(BrandProfile)
        .filter(
            BrandProfile.id == scoring_run.brand_profile_id,
            BrandProfile.status == "confirmed",
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if profile is None:
        return RelevanceGateDecision(False, "profile_not_confirmed")

    run_version = scoring_run.relevance_anchor_version
    if run_version is None:
        return RelevanceGateDecision(False, "anchor_version_missing")
    if int(run_version) != int(profile.anchor_version or 1):
        return RelevanceGateDecision(False, "anchor_version_stale")

    return RelevanceGateDecision(True, "ok")


def load_effective_relevance_map(
    db: Session, scoring_run_id: int
) -> Dict[int, float]:
    """Kapidan gecerse {keyword_id: relevance}, gecmezse BOS dict.

    TUM relevance okuyuculari bunu kullanmalidir — dogrudan `KeywordRelevance`
    sorgusu yazmak kapiyi atlatir ve P1.4'te kapatilan asimetriyi geri getirir.
    """
    scoring_run = (
        db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    )
    decision = evaluate_relevance_gate(db, scoring_run)
    if not decision.applied:
        logger.debug(
            f"Relevance kapisi kapali (run {scoring_run_id}): {decision.reason} "
            f"— notr siralama uygulanacak"
        )
        return {}

    rows = (
        db.query(KeywordRelevance.keyword_id, KeywordRelevance.relevance_score)
        .filter(KeywordRelevance.scoring_run_id == scoring_run_id)
        .all()
    )
    return {
        keyword_id: float(relevance_score)
        for keyword_id, relevance_score in rows
        if relevance_score is not None
    }
