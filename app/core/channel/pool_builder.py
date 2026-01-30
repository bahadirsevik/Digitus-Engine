"""
Kanal havuzlarını oluşturur.
Her kanal için 2x kapasite kadar aday seçer.
"""
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database.models import KeywordScore, ChannelCandidate, ScoringRun
from app.core.constants import ADS_POOL_SIZE, SEO_POOL_SIZE, SOCIAL_POOL_SIZE


class PoolBuilder:
    """
    Kanal havuz oluşturucu.
    Her kanal için en yüksek skorlu kelimeleri aday olarak seçer.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def build_candidate_pools(self, scoring_run_id: int) -> Dict[str, int]:
        """
        Her kanal için aday havuzlarını oluşturur.
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID
        
        Returns:
            Her kanal için seçilen aday sayısı
        """
        # Scoring run'ı al
        scoring_run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        
        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} bulunamadı")
        
        # Sabit havuz boyutları
        ads_pool_size = ADS_POOL_SIZE
        seo_pool_size = SEO_POOL_SIZE
        social_pool_size = SOCIAL_POOL_SIZE
        
        # ADS adaylarını seç
        ads_candidates = (
            self.db.query(KeywordScore)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .order_by(KeywordScore.ads_rank)
            .limit(ads_pool_size)
            .all()
        )
        
        for candidate in ads_candidates:
            self.db.add(ChannelCandidate(
                scoring_run_id=scoring_run_id,
                keyword_id=candidate.keyword_id,
                channel="ADS",
                raw_score=candidate.ads_score,
                rank_in_channel=candidate.ads_rank
            ))
        
        # SEO adaylarını seç
        seo_candidates = (
            self.db.query(KeywordScore)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .order_by(KeywordScore.seo_rank)
            .limit(seo_pool_size)
            .all()
        )
        
        for candidate in seo_candidates:
            self.db.add(ChannelCandidate(
                scoring_run_id=scoring_run_id,
                keyword_id=candidate.keyword_id,
                channel="SEO",
                raw_score=candidate.seo_score,
                rank_in_channel=candidate.seo_rank
            ))
        
        # SOCIAL adaylarını seç
        social_candidates = (
            self.db.query(KeywordScore)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .order_by(KeywordScore.social_rank)
            .limit(social_pool_size)
            .all()
        )
        
        for candidate in social_candidates:
            self.db.add(ChannelCandidate(
                scoring_run_id=scoring_run_id,
                keyword_id=candidate.keyword_id,
                channel="SOCIAL",
                raw_score=candidate.social_score,
                rank_in_channel=candidate.social_rank
            ))
        
        self.db.commit()
        
        return {
            'ADS': len(ads_candidates),
            'SEO': len(seo_candidates),
            'SOCIAL': len(social_candidates)
        }
    
    def get_candidates_by_channel(
        self,
        scoring_run_id: int,
        channel: str
    ) -> List[ChannelCandidate]:
        """
        Belirli bir kanal için tüm adayları döner.
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID
            channel: Kanal adı
        
        Returns:
            Aday listesi
        """
        return (
            self.db.query(ChannelCandidate)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .order_by(ChannelCandidate.rank_in_channel)
            .all()
        )
