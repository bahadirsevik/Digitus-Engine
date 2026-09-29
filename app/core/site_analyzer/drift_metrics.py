"""Embedding geçişi drift metrikleri (plan E kabulü — Codex v10-3/v10-4).

Salt-okunur simülasyonlar: run'ın hiçbir kaydına dokunmaz.

- simulate_final_pools: channel_engine._build_final_pools_v2 sıralamasının
  kopyası (eligibility joins, SEO gt/ga boost, sınıf önceliği, hacim/kelime
  tie-break). Aday kümesi + AI kararları SABİT; yalnız relevance değişir.
- simulate_candidate_pools: PoolBuilder.build_candidate_pools sıralamasının
  kopyası — relevance değişiminin AI'ya GİRECEK aday kümesini nasıl
  değiştireceğini ölçer (risk yüzeyi: yeni girenlerin AI sonucu bilinemez).
- rank_avg / spearman_tie_aware: tie-aware sıralama korelasyonu.

Bu modül app/core altında yaşar ki simülatörler GERÇEK engine'e karşı
testle kilitlenebilsin (script'te kalsalar test edilemezlerdi).
"""
from typing import Dict, List

import numpy as np
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.constants import (
    ADS_FINAL_CAPACITY,
    ADS_POOL_SIZE,
    DEFAULT_AI_CLASS_WHEN_MISSING,
    SEO_FINAL_CAPACITY,
    SEO_POOL_SIZE,
    SEO_W_GA,
    SEO_W_GT,
    SOCIAL_FINAL_CAPACITY,
    SOCIAL_POOL_SIZE,
)
from app.database.models import (
    ChannelCandidate,
    IntentAnalysis,
    Keyword,
    KeywordScore,
    PreFilterResult,
    ScoringRun,
)

_SCORE_FIELDS = {"ADS": "ads_score", "SEO": "seo_score", "SOCIAL": "social_score"}
_RANK_FIELDS = {"ADS": "ads_rank", "SEO": "seo_rank", "SOCIAL": "social_rank"}


def rank_avg(values) -> np.ndarray:
    """Tie-aware ortalama rank (Codex v9-4: argsort(argsort) tie'da yanlış)."""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_tie_aware(old_map: Dict[int, float], new_map: Dict[int, float]) -> float:
    ids = sorted(set(old_map) & set(new_map))
    if len(ids) < 3:
        return float("nan")
    ra = rank_avg([old_map[i] for i in ids])
    rb = rank_avg([new_map[i] for i in ids])
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def _active_channels(run: ScoringRun) -> List[str]:
    return [c for c, flag in (
        ("ADS", run.enable_ads), ("SEO", run.enable_seo), ("SOCIAL", run.enable_social),
    ) if flag]


def simulate_final_pools(db: Session, run: ScoringRun,
                         relevance_map: Dict[int, float]) -> Dict[str, List[dict]]:
    """channel_engine._build_final_pools_v2 sıralamasının salt-okunur kopyası.

    NOT: relevance_coefficient bilinçli olarak 1.0 — tüm adjusted skorları
    aynı çarpanla ölçekler, sıralamayı ve kesmeyi DEĞİŞTİRMEZ.
    """
    capacity_map = {
        "ADS": run.ads_capacity or ADS_FINAL_CAPACITY,
        "SEO": run.seo_capacity or SEO_FINAL_CAPACITY,
        "SOCIAL": run.social_capacity or SOCIAL_FINAL_CAPACITY,
    }
    scores = {
        ks.keyword_id: ks
        for ks in db.query(KeywordScore).filter(
            KeywordScore.scoring_run_id == run.id
        ).all()
    }

    pools: Dict[str, List[dict]] = {}
    for channel in _active_channels(run):
        rows = (
            db.query(IntentAnalysis, ChannelCandidate, PreFilterResult, Keyword.keyword)
            .join(ChannelCandidate, and_(
                IntentAnalysis.keyword_id == ChannelCandidate.keyword_id,
                IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id,
                IntentAnalysis.channel == ChannelCandidate.channel,
            ))
            .join(PreFilterResult, and_(
                PreFilterResult.keyword_id == IntentAnalysis.keyword_id,
                PreFilterResult.scoring_run_id == IntentAnalysis.scoring_run_id,
                PreFilterResult.channel == IntentAnalysis.channel,
            ))
            .join(Keyword, Keyword.id == IntentAnalysis.keyword_id)
            .filter(IntentAnalysis.scoring_run_id == run.id)
            .filter(IntentAnalysis.channel == channel)
            .filter(IntentAnalysis.is_passed == True)  # noqa: E712
            .filter(PreFilterResult.is_kept == True)  # noqa: E712
            .all()
        )
        scored = []
        for intent, cand, pf, kw_text in rows:
            kid = cand.keyword_id
            ks = scores.get(kid)
            base = float(getattr(ks, _SCORE_FIELDS[channel]) or 0) if ks else 0.0
            if channel == "SEO":
                # v2_1 (plan Faz E): motorla aynı dal — S_G(strategy_fit)/ga
                if (getattr(run, "algorithm_version", "v2") or "v2") == "v2_1":
                    base += (SEO_W_GT * int(bool(intent.strategy_fit))
                             + SEO_W_GA * int(bool(intent.ga)))
                else:
                    base += (SEO_W_GT * int(bool(intent.gt))
                             + SEO_W_GA * int(bool(intent.ga)))
            relevance = float(relevance_map.get(kid, 0.5))
            adjusted = max(base, 0.0) * relevance
            if channel == "SEO":
                cls = 0
            elif pf.ai_class is not None:
                cls = pf.ai_class
            else:
                cls = DEFAULT_AI_CLASS_WHEN_MISSING
            snapshot = (ks.metrics_snapshot or {}) if ks else {}
            try:
                volume = int(snapshot.get("monthly_volume") or 0)
            except (TypeError, ValueError):
                volume = 0
            scored.append({
                "kid": kid, "text": kw_text or "", "cls": cls,
                "adjusted": adjusted, "volume": volume,
            })
        scored.sort(key=lambda r: (
            -r["cls"], -r["adjusted"], -r["volume"], r["text"], r["kid"]
        ))
        pools[channel] = scored[: capacity_map[channel]]
    return pools


def simulate_candidate_pools(db: Session, run: ScoringRun,
                             relevance_map: Dict[int, float]) -> Dict[str, List[int]]:
    """PoolBuilder.build_candidate_pools (relevance yolu) sıralamasının kopyası.

    Codex v10-3: relevance değişimi AI aşamasına GİRECEK adayları da değiştirir
    (kapasite×3 kesmesi) — bu offline ölçülebilir; yalnız yeni girenlerin AI
    SONUCU bilinemez (risk yüzeyi olarak raporlanır).
    """
    capacity_map = {
        "ADS": int(run.ads_capacity or ADS_POOL_SIZE),
        "SEO": int(run.seo_capacity or SEO_POOL_SIZE),
        "SOCIAL": int(run.social_capacity or SOCIAL_POOL_SIZE),
    }
    pool_sizes = {
        "ADS": min(ADS_POOL_SIZE, capacity_map["ADS"] * 3),
        "SEO": min(SEO_POOL_SIZE, capacity_map["SEO"] * 3),
        "SOCIAL": min(SOCIAL_POOL_SIZE, capacity_map["SOCIAL"] * 3),
    }
    all_scores = (
        db.query(KeywordScore)
        .filter(KeywordScore.scoring_run_id == run.id)
        .all()
    )
    out: Dict[str, List[int]] = {}
    for channel in _active_channels(run):
        ranked = []
        for ks in all_scores:
            raw = float(getattr(ks, _SCORE_FIELDS[channel]) or 0)
            relevance = float(relevance_map.get(ks.keyword_id, 0.5))
            adjusted = max(raw, 0.0) * relevance
            ranked.append((ks, adjusted))
        ranked.sort(key=lambda x: (
            -x[1],
            getattr(x[0], _RANK_FIELDS[channel]) or 999999,
            x[0].keyword_id or 0,
        ))
        out[channel] = [ks.keyword_id for ks, _ in ranked[: pool_sizes[channel]]]
    return out


def pool_diff(old_pool: List[dict], new_pool: List[dict]):
    old_ids = {r["kid"] for r in old_pool}
    new_ids = {r["kid"] for r in new_pool}
    union = old_ids | new_ids
    jaccard = (len(old_ids & new_ids) / len(union)) if union else 1.0
    entered = [r for r in new_pool if r["kid"] not in old_ids]
    exited = [r for r in old_pool if r["kid"] not in new_ids]
    return jaccard, entered, exited


def id_set_jaccard(a, b) -> float:
    a, b = set(a), set(b)
    union = a | b
    return (len(a & b) / len(union)) if union else 1.0


def class_dist(pool: List[dict]) -> Dict[int, int]:
    dist: Dict[int, int] = {}
    for r in pool:
        dist[r["cls"]] = dist.get(r["cls"], 0) + 1
    return dict(sorted(dist.items(), reverse=True))
