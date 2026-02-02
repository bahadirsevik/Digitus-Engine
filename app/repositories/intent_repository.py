"""
Repository for Intent Analysis database operations.
"""
from typing import List, Tuple
from sqlalchemy.orm import Session
from app.database.models import ChannelCandidate, Keyword, IntentAnalysis

class IntentRepository:
    """
    Repository for handling IntentAnalysis related database operations.
    """
    def __init__(self, db: Session):
        self.db = db

    def get_candidates(self, scoring_run_id: int, channel: str) -> List[Tuple[ChannelCandidate, Keyword]]:
        """
        Retrieves candidates for analysis for a specific run and channel.
        """
        return (
            self.db.query(ChannelCandidate, Keyword)
            .join(Keyword, ChannelCandidate.keyword_id == Keyword.id)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .order_by(ChannelCandidate.rank_in_channel)
            .all()
        )

    def save_intent_analyses(self, analyses: List[IntentAnalysis]) -> None:
        """
        Bulk saves intent analysis results.
        """
        if analyses:
            self.db.add_all(analyses)
            self.db.commit()
