"""
Build channel candidate pools.
"""
import logging
from typing import List, Dict
from decimal import Decimal
from sqlalchemy.orm import Session

from app.database.models import (
    KeywordScore, ChannelCandidate, ScoringRun,
    BrandProfile, KeywordRelevance
)
from app.core.constants import ADS_POOL_SIZE, SEO_POOL_SIZE, SOCIAL_POOL_SIZE

logger = logging.getLogger(__name__)


class PoolBuilder:
    """
    Selects top candidates per channel.
    If confirmed profile + relevance exists, uses relevance-adjusted ranking.
    """

    def __init__(self, db: Session):
        self.db = db

    def build_candidate_pools(
        self,
        scoring_run_id: int,
        relevance_coefficient: float = 1.0
    ) -> Dict[str, int]:
        """
        Build candidate pools for each channel.

        Relevance path formula:
            adjusted_score = raw_score * relevance_score * relevance_coefficient
        """
        scoring_run = self.db.query(ScoringRun).filter(
            ScoringRun.id == scoring_run_id
        ).first()

        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} not found")

        relevance_map = self._load_relevance_map(scoring_run_id)
        has_relevance = len(relevance_map) > 0

        if has_relevance:
            logger.info(
                "Relevance gate active for run %s with %s keywords",
                scoring_run_id,
                len(relevance_map)
            )

        pool_sizes = {
            "ADS": ADS_POOL_SIZE,
            "SEO": SEO_POOL_SIZE,
            "SOCIAL": SOCIAL_POOL_SIZE,
        }

        score_fields = {
            "ADS": ("ads_score", "ads_rank"),
            "SEO": ("seo_score", "seo_rank"),
            "SOCIAL": ("social_score", "social_rank"),
        }

        result_counts: Dict[str, int] = {}

        for channel, pool_size in pool_sizes.items():
            score_field, rank_field = score_fields[channel]

            all_scores = (
                self.db.query(KeywordScore)
                .filter(KeywordScore.scoring_run_id == scoring_run_id)
                .all()
            )

            if has_relevance:
                scored_candidates = []
                for ks in all_scores:
                    raw = float(getattr(ks, score_field) or 0)
                    relevance = float(relevance_map.get(ks.keyword_id, Decimal("1.0")))
                    adjusted = raw * relevance * relevance_coefficient
                    scored_candidates.append((ks, adjusted))

                scored_candidates.sort(key=lambda x: x[1], reverse=True)
                selected = scored_candidates[:pool_size]

                for new_rank, (ks, adjusted_score) in enumerate(selected, 1):
                    self.db.add(ChannelCandidate(
                        scoring_run_id=scoring_run_id,
                        keyword_id=ks.keyword_id,
                        channel=channel,
                        raw_score=Decimal(str(round(adjusted_score, 4))),
                        rank_in_channel=new_rank,
                    ))
            else:
                all_scores_sorted = sorted(
                    all_scores,
                    key=lambda x: getattr(x, rank_field) or 999999
                )
                selected = all_scores_sorted[:pool_size]

                for ks in selected:
                    self.db.add(ChannelCandidate(
                        scoring_run_id=scoring_run_id,
                        keyword_id=ks.keyword_id,
                        channel=channel,
                        raw_score=getattr(ks, score_field),
                        rank_in_channel=getattr(ks, rank_field),
                    ))

            result_counts[channel] = len(selected)

        self.db.commit()
        return result_counts

    def _load_relevance_map(self, scoring_run_id: int) -> Dict[int, Decimal]:
        """
        Load relevance scores for all keywords in a run.
        Returns empty dict if no confirmed profile or no relevance data.
        """
        from app.config import settings

        if not settings.ENABLE_RELEVANCE_RERANK:
            return {}

        profile = self.db.query(BrandProfile).filter(
            BrandProfile.scoring_run_id == scoring_run_id,
            BrandProfile.status == "confirmed",
        ).first()

        if not profile:
            return {}

        relevance_rows = (
            self.db.query(KeywordRelevance)
            .filter(KeywordRelevance.scoring_run_id == scoring_run_id)
            .all()
        )

        return {r.keyword_id: r.relevance_score for r in relevance_rows}

    def get_candidates_by_channel(
        self,
        scoring_run_id: int,
        channel: str
    ) -> List[ChannelCandidate]:
        """Return all candidates for a specific channel."""
        return (
            self.db.query(ChannelCandidate)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .order_by(ChannelCandidate.rank_in_channel)
            .all()
        )
