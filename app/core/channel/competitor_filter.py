"""Deterministik rakip terim bloğu (plan A — pre-intent hard block).

AI'dan BAĞIMSIZ çalışır ve intent'ten ÖNCE uygulanır: rakip terimi içeren
aday, intent sonucu ne olursa olsun kanala giremez ve o aday için HİÇBİR
AI çağrısı yapılmaz (terminal PreFilterResult satırı tüm katman
sorgularında atlanır).

Üç uygulama yolu: initial pool, expansion penceresi, transfer created_ids.
Kayıt sözleşmesi: PreFilterResult(is_kept=False, label='COMPETITOR',
extra_data={reason_code: COMPETITOR_TERM, matched_term}).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.core.constants import COMPETITOR_TERM_REASON, TERMINAL_PREFILTER_REASONS
from app.core.policy.competitor_policy import (
    POLICY_BLOCK,
    approved_competitor_terms,
    competitor_policy_for,
    match_term,
)
from app.database.models import (
    BrandProfile,
    ChannelCandidate,
    Keyword,
    PreFilterResult,
    ScoringRun,
)


def _blocked_channels(profile) -> List[str]:
    policy = competitor_policy_for(profile)
    return [ch.upper() for ch, mode in policy.items() if mode == POLICY_BLOCK]


def apply_competitor_block(
    db: Session,
    scoring_run_id: int,
    channels: Optional[List[str]] = None,
    keyword_ids: Optional[List[int]] = None,
) -> Dict[str, int]:
    """Rakip terimli adayları terminal PreFilterResult ile bloklar.

    Args:
        channels: sınırlandırılacak kanallar (None = politikadaki block'lular)
        keyword_ids: yalnız bu keyword'ler (expansion penceresi / transfer)

    Returns: {channel: blocked_count} — erken dönüşler de sözleşmeye uyar.
    """
    run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    if run is None or not run.brand_profile_id:
        return {}
    profile = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == run.brand_profile_id)
        .first()
    )
    if profile is None:
        return {}

    terms = approved_competitor_terms(profile)
    if not terms:
        return {}

    target_channels = [
        ch for ch in _blocked_channels(profile)
        if channels is None or ch in channels
    ]
    if not target_channels:
        return {}

    query = (
        db.query(ChannelCandidate, Keyword)
        .join(Keyword, ChannelCandidate.keyword_id == Keyword.id)
        .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
        .filter(ChannelCandidate.channel.in_(target_channels))
    )
    if keyword_ids is not None:
        if not keyword_ids:
            return {ch: 0 for ch in target_channels}
        query = query.filter(ChannelCandidate.keyword_id.in_(keyword_ids))

    blocked: Dict[str, int] = {ch: 0 for ch in target_channels}
    for candidate, keyword in query.all():
        matched = match_term(keyword.keyword, terms)
        if matched is None:
            continue

        existing = (
            db.query(PreFilterResult)
            .filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.keyword_id == candidate.keyword_id,
                PreFilterResult.channel == candidate.channel,
            )
            .first()
        )
        if existing is not None:
            reason = (existing.extra_data or {}).get("reason_code")
            if reason in TERMINAL_PREFILTER_REASONS:
                continue  # terminal satır zaten var — dokunma
            # Terminal blok, AI kaynaklı satırı EZER (deterministik kural üstün)
            existing.is_kept = False
            existing.label = "COMPETITOR"
            existing.ai_class = None
            existing.is_fallback = False
            existing.ai_reasoning = f"Rakip terim: {matched}"
            existing.extra_data = {
                "reason_code": COMPETITOR_TERM_REASON,
                "matched_term": matched,
            }
            existing.transfer_channel = None
        else:
            db.add(PreFilterResult(
                scoring_run_id=scoring_run_id,
                keyword_id=candidate.keyword_id,
                channel=candidate.channel,
                is_kept=False,
                label="COMPETITOR",
                ai_class=None,
                is_fallback=False,
                ai_reasoning=f"Rakip terim: {matched}",
                extra_data={
                    "reason_code": COMPETITOR_TERM_REASON,
                    "matched_term": matched,
                },
            ))
        blocked[candidate.channel] = blocked.get(candidate.channel, 0) + 1

    db.commit()
    total = sum(blocked.values())
    if total:
        logger.info(
            f"Competitor block: {total} aday elendi "
            f"(run={scoring_run_id}, {blocked})"
        )
    return blocked
