"""
Kanal atama sürecini orkestra eden ana modül.
"""
from typing import Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime

from app.database.models import (
    ScoringRun, ChannelCandidate, IntentAnalysis, ChannelPool
)
from app.core.channel.pool_builder import PoolBuilder
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.constants import ADS_FINAL_CAPACITY, SEO_FINAL_CAPACITY, SOCIAL_FINAL_CAPACITY
from app.generators.ai_service import AIService


class ChannelEngine:
    """
    Kanal atama motoru.
    Skorlanan kelimeler üzerinde kanal atama sürecini orkestra eder.
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = ai_service
        self.pool_builder = PoolBuilder(db)
        self.intent_analyzer = IntentAnalyzer(db, ai_service)
    
    def run_channel_assignment(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        Tam kanal atama sürecini çalıştırır.
        
        Adımlar:
        1. Aday havuzları oluştur (sabit havuz boyutları)
        2. Her kanal için niyet analizi yap
        3. Filtreyi geçenleri final havuza al
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID
        
        Returns:
            Süreç özeti
        """
        scoring_run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        
        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} bulunamadı")
        
        # Durumu güncelle
        scoring_run.status = "intent_analysis"
        self.db.commit()
        
        results = {
            'scoring_run_id': scoring_run_id,
            'steps': {}
        }
        
        try:
            # Adım 0: Temizlik (Önceki denemelerden kalan verileri sil)
            self.db.query(ChannelPool).filter(ChannelPool.scoring_run_id == scoring_run_id).delete()
            self.db.query(IntentAnalysis).filter(IntentAnalysis.scoring_run_id == scoring_run_id).delete()
            self.db.query(ChannelCandidate).filter(ChannelCandidate.scoring_run_id == scoring_run_id).delete()
            self.db.commit()

            # Adım 1: Aday havuzları oluştur
            pool_counts = self.pool_builder.build_candidate_pools(scoring_run_id)
            results['steps']['pool_building'] = pool_counts
            
            # Adım 2: Her kanal için niyet analizi
            intent_results = {}
            for channel in ['ADS', 'SEO', 'SOCIAL']:
                intent_results[channel] = self.intent_analyzer.analyze_candidates(
                    scoring_run_id, channel
                )
            results['steps']['intent_analysis'] = intent_results
            
            # Adım 3: Final havuzları oluştur
            final_counts = self._build_final_pools(scoring_run_id, scoring_run)
            results['steps']['final_pools'] = final_counts
            

            
            # Durumu güncelle
            scoring_run.status = "completed"
            scoring_run.completed_at = datetime.utcnow()
            self.db.commit()
            
            results['status'] = 'completed'
        
        except Exception as e:
            scoring_run.status = "failed"
            self.db.commit()
            results['status'] = 'failed'
            results['error'] = str(e)
            raise e
        
        return results
    
    def _build_final_pools(
        self,
        scoring_run_id: int,
        scoring_run: ScoringRun
    ) -> Dict[str, int]:
        """
        Niyet analizini geçen kelimelerden final havuzları oluşturur.
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID
            scoring_run: ScoringRun nesnesi
        
        Returns:
            Her kanal için seçilen kelime sayısı
        """
        final_counts = {}
        
        for channel, capacity in [
            ('ADS', ADS_FINAL_CAPACITY),
            ('SEO', SEO_FINAL_CAPACITY),
            ('SOCIAL', SOCIAL_FINAL_CAPACITY)
        ]:
            # Niyet analizini geçen adayları al
            passed_candidates = (
                self.db.query(IntentAnalysis, ChannelCandidate)
                .join(
                    ChannelCandidate,
                    (IntentAnalysis.keyword_id == ChannelCandidate.keyword_id) &
                    (IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id) &
                    (IntentAnalysis.channel == ChannelCandidate.channel)
                )
                .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
                .filter(IntentAnalysis.channel == channel)
                .filter(IntentAnalysis.is_passed == True)
                .order_by(ChannelCandidate.rank_in_channel)
                .limit(capacity)
                .all()
            )
            
            # Final havuza ekle
            for rank, (intent, candidate) in enumerate(passed_candidates, 1):
                pool_entry = ChannelPool(
                    scoring_run_id=scoring_run_id,
                    keyword_id=candidate.keyword_id,
                    channel=channel,
                    final_rank=rank,
                    is_strategic=False  # Sonra güncellenecek
                )
                self.db.add(pool_entry)
            
            final_counts[channel] = len(passed_candidates)
        
        self.db.commit()
        
        return final_counts
    
    def get_channel_pools(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        Tüm kanal havuzlarını döner.
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID
        
        Returns:
            Kanal havuzları
        """
        from app.database.models import Keyword
        
        result = {
            'scoring_run_id': scoring_run_id,
            'channels': {}
        }
        
        for channel in ['ADS', 'SEO', 'SOCIAL']:
            pools = (
                self.db.query(ChannelPool, Keyword)
                .join(Keyword, ChannelPool.keyword_id == Keyword.id)
                .filter(ChannelPool.scoring_run_id == scoring_run_id)
                .filter(ChannelPool.channel == channel)
                .order_by(ChannelPool.final_rank)
                .all()
            )
            
            result['channels'][channel] = [
                {
                    'rank': pool.final_rank,
                    'keyword_id': pool.keyword_id,
                    'keyword': keyword.keyword,
                    'is_strategic': pool.is_strategic
                }
                for pool, keyword in pools
            ]
        
        return result
