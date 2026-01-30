"""
Ana skorlama motoru.
Tüm skorlamaları orkestra eder ve veritabanına kaydeder.
"""
from typing import List, Dict, Any, Tuple
from sqlalchemy.orm import Session
from datetime import datetime

from app.database.models import Keyword, ScoringRun, KeywordScore
from app.core.scoring.ads_scorer import calculate_bulk_ads_scores
from app.core.scoring.seo_scorer import calculate_bulk_seo_scores
from app.core.scoring.social_scorer import calculate_bulk_social_scores


class ScoreEngine:
    """
    Ana skorlama motoru.
    Tüm kanallara göre kelimeleri skorlar ve veritabanına kaydeder.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_scoring_run(
        self,
        ads_capacity: int,
        seo_capacity: int,
        social_capacity: int,
        run_name: str = None
    ) -> ScoringRun:
        """
        Yeni bir skorlama çalıştırması oluşturur.
        
        Args:
            ads_capacity: ADS kanalı için hedef kelime sayısı
            seo_capacity: SEO kanalı için hedef kelime sayısı
            social_capacity: SOCIAL kanalı için hedef kelime sayısı
            run_name: Opsiyonel çalıştırma adı
        
        Returns:
            Oluşturulan ScoringRun nesnesi
        """
        # Aktif kelime sayısını al
        total_keywords = self.db.query(Keyword).filter(Keyword.is_active == True).count()
        
        scoring_run = ScoringRun(
            run_name=run_name or f"Run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            total_keywords=total_keywords,
            ads_capacity=ads_capacity,
            seo_capacity=seo_capacity,
            social_capacity=social_capacity,
            status="pending"
        )
        
        self.db.add(scoring_run)
        self.db.commit()
        self.db.refresh(scoring_run)
        
        return scoring_run
    
    def run_scoring(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        Tüm skorlamaları çalıştırır.
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID'si
        
        Returns:
            Skorlama özeti
        """
        # Scoring run'ı al
        scoring_run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        
        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} bulunamadı")
        
        # Durumu güncelle
        scoring_run.status = "scoring"
        scoring_run.started_at = datetime.utcnow()
        self.db.commit()
        
        try:
            # Aktif kelimeleri al
            keywords = self.db.query(Keyword).filter(Keyword.is_active == True).all()
            
            # Dict listesine çevir
            keyword_dicts = [
                {
                    'id': kw.id,
                    'monthly_volume': kw.monthly_volume,
                    'trend_3m': kw.trend_3m,
                    'trend_12m': kw.trend_12m,
                    'competition_score': kw.competition_score
                }
                for kw in keywords
            ]
            
            # Her kanal için skorla
            ads_results = calculate_bulk_ads_scores(keyword_dicts)
            seo_results = calculate_bulk_seo_scores(keyword_dicts)
            social_results = calculate_bulk_social_scores(keyword_dicts)
            
            # Sonuçları birleştir (keyword_id bazında)
            combined = {}
            
            for item in ads_results:
                kid = item['keyword_id']
                combined[kid] = {
                    'keyword_id': kid,
                    'ads_score': item['ads_score'],
                    'ads_rank': item['ads_rank']
                }
            
            for item in seo_results:
                kid = item['keyword_id']
                combined[kid]['seo_score'] = item['seo_score']
                combined[kid]['seo_rank'] = item['seo_rank']
            
            for item in social_results:
                kid = item['keyword_id']
                combined[kid]['social_score'] = item['social_score']
                combined[kid]['social_rank'] = item['social_rank']
            
            # Veritabanına kaydet
            for kid, scores in combined.items():
                keyword_score = KeywordScore(
                    scoring_run_id=scoring_run_id,
                    keyword_id=kid,
                    ads_score=scores['ads_score'],
                    seo_score=scores['seo_score'],
                    social_score=scores['social_score'],
                    ads_rank=scores['ads_rank'],
                    seo_rank=scores['seo_rank'],
                    social_rank=scores['social_rank']
                )
                self.db.add(keyword_score)
            
            self.db.commit()
            
            # Durumu güncelle
            scoring_run.status = "completed"
            self.db.commit()
            
            return {
                'scoring_run_id': scoring_run_id,
                'total_scored': len(combined),
                'status': 'completed'
            }
        
        except Exception as e:
            scoring_run.status = "failed"
            self.db.commit()
            raise e
    
    def get_top_keywords_by_channel(
        self,
        scoring_run_id: int,
        channel: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Belirli bir kanal için en yüksek skorlu kelimeleri döner.
        
        Args:
            scoring_run_id: Skorlama çalıştırması ID
            channel: Kanal adı ('ADS', 'SEO', 'SOCIAL')
            limit: Döndürülecek kelime sayısı
        
        Returns:
            Kelime listesi
        """
        rank_column = getattr(KeywordScore, f"{channel.lower()}_rank")
        score_column = getattr(KeywordScore, f"{channel.lower()}_score")
        
        results = (
            self.db.query(KeywordScore, Keyword)
            .join(Keyword, KeywordScore.keyword_id == Keyword.id)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .order_by(rank_column)
            .limit(limit)
            .all()
        )
        
        return [
            {
                'keyword_id': ks.keyword_id,
                'keyword': kw.keyword,
                'score': float(getattr(ks, f"{channel.lower()}_score") or 0),
                'rank': getattr(ks, f"{channel.lower()}_rank")
            }
            for ks, kw in results
        ]
