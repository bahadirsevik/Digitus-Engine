"""
Ana skorlama motoru.
Tüm skorlamaları orkestra eder ve veritabanına kaydeder.

v2: WorkspaceKeyword snapshot metrikleriyle çalışır.
    _deactivate_duplicate_keywords kaldırıldı (scoring read-only).
    enable_* flag'lerine göre koşullu kanal skorlama.
    KeywordScore.metrics_snapshot zorunlu doldurulur.
"""
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from datetime import datetime
from loguru import logger

from app.database.models import (
    Keyword, ScoringRun, KeywordScore,
    BrandProfile, WorkspaceKeyword,
)
from app.core.scoring.ads_scorer import calculate_bulk_ads_scores
from app.core.scoring.seo_scorer import calculate_bulk_seo_scores
from app.core.scoring.social_scorer import calculate_bulk_social_scores
from app.core.scoring.state_machine import transition


class ScoreEngine:
    """
    Ana skorlama motoru.
    Tüm kanallara göre kelimeleri skorlar ve veritabanına kaydeder.
    """

    def __init__(self, db: Session):
        self.db = db

    def _select_keywords_with_metrics(self, run: ScoringRun) -> List[Dict]:
        """
        WorkspaceKeyword'den snapshot metrikleri ile keyword listesi.
        Global Keyword.monthly_volume vs KULLANILMAZ.
        """
        base_query = (
            self.db.query(Keyword, WorkspaceKeyword)
                .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
                .filter(WorkspaceKeyword.brand_profile_id == run.brand_profile_id)
                .filter(Keyword.is_active == True)
        )

        # keyword_source_filter artık WK.data_source üzerinden
        if run.keyword_source_filter:
            base_query = base_query.filter(
                WorkspaceKeyword.data_source == run.keyword_source_filter
            )

        if run.keyword_selection_mode == "top_n":
            base_query = base_query.order_by(
                WorkspaceKeyword.monthly_volume.desc()
            ).limit(run.keyword_limit)

        elif run.keyword_selection_mode == "specific":
            if not run.selected_keyword_ids:
                raise ValueError("specific mode requires selected_keyword_ids")
            # Backend validation: id'ler bu workspace'in WK'larına ait mi?
            valid_ids = set(
                r[0] for r in
                base_query.filter(Keyword.id.in_(run.selected_keyword_ids))
                          .with_entities(Keyword.id).all()
            )
            invalid = set(run.selected_keyword_ids) - valid_ids
            if invalid:
                from fastapi import HTTPException
                raise HTTPException(
                    400,
                    f"{len(invalid)} keyword bu workspace'e ait değil veya pasif"
                )
            base_query = base_query.filter(Keyword.id.in_(valid_ids))

        # Scoring dict'i WK metriklerinden oluştur
        return [
            {
                'id': kw.id,
                'monthly_volume': wk.monthly_volume,
                'trend_3m': float(wk.trend_3m),
                'trend_12m': float(wk.trend_12m),
                'competition_score': float(wk.competition_score),
                '_wk_id': wk.id,  # metrics_snapshot için
            }
            for kw, wk in base_query.all()
        ]

    def _legacy_select_keywords(self, run: ScoringRun) -> List[Dict]:
        """
        Legacy: Global Keyword üzerinden keyword listesi (geriye uyumluluk).
        brand_profile_id yoksa kullanılır.
        """
        kw_query = self.db.query(Keyword).filter(Keyword.is_active == True)
        if run.keyword_source_filter:
            kw_query = kw_query.filter(
                Keyword.data_source == run.keyword_source_filter
            )
        keywords = kw_query.all()

        return [
            {
                'id': kw.id,
                'monthly_volume': kw.monthly_volume,
                'trend_3m': float(kw.trend_3m) if kw.trend_3m is not None else 0,
                'trend_12m': float(kw.trend_12m) if kw.trend_12m is not None else 0,
                'competition_score': float(kw.competition_score) if kw.competition_score is not None else 0.5,
                '_wk_id': None,
            }
            for kw in keywords
        ]

    def create_scoring_run(
        self,
        ads_capacity: int,
        seo_capacity: int,
        social_capacity: int,
        default_relevance_coefficient: float = 1.0,
        run_name: str = None,
        company_url: str = None,
        competitor_urls: list = None,
        keyword_source_filter: Optional[str] = None,
        brand_profile_id: Optional[int] = None,
        enable_ads: bool = True,
        enable_seo: bool = True,
        enable_social: bool = True,
        keyword_selection_mode: str = "all",
        keyword_limit: Optional[int] = None,
        selected_keyword_ids: Optional[List[int]] = None,
        skip_relevance: bool = False,
    ) -> ScoringRun:
        """
        Yeni bir skorlama çalıştırması oluşturur.
        """
        # Aktif kelime sayısını al
        if brand_profile_id:
            kw_count = (
                self.db.query(Keyword)
                    .join(WorkspaceKeyword)
                    .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
                    .filter(Keyword.is_active == True)
                    .count()
            )
        else:
            kw_query = self.db.query(Keyword).filter(Keyword.is_active == True)
            if keyword_source_filter:
                kw_query = kw_query.filter(Keyword.data_source == keyword_source_filter)
            kw_count = kw_query.count()

        scoring_run = ScoringRun(
            run_name=run_name or f"Run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            total_keywords=kw_count,
            ads_capacity=ads_capacity,
            seo_capacity=seo_capacity,
            social_capacity=social_capacity,
            default_relevance_coefficient=default_relevance_coefficient,
            company_url=company_url,
            competitor_urls=competitor_urls,
            keyword_source_filter=keyword_source_filter,
            brand_profile_id=brand_profile_id,
            enable_ads=enable_ads,
            enable_seo=enable_seo,
            enable_social=enable_social,
            keyword_selection_mode=keyword_selection_mode,
            keyword_limit=keyword_limit,
            selected_keyword_ids=selected_keyword_ids,
            skip_relevance=skip_relevance,
            status="pending"
        )

        self.db.add(scoring_run)
        self.db.commit()
        self.db.refresh(scoring_run)

        return scoring_run

    def run_scoring(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        Tüm skorlamaları çalıştırır.

        Returns:
            scoring_run_id, scored_count, should_compute_relevance
        """
        scoring_run = self.db.query(ScoringRun).filter(
            ScoringRun.id == scoring_run_id
        ).first()

        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} bulunamadı")

        # Durumu güncelle
        transition(self.db, scoring_run, "scoring")

        try:
            # Keyword seçimi
            if scoring_run.brand_profile_id:
                keywords = self._select_keywords_with_metrics(scoring_run)
            else:
                keywords = self._legacy_select_keywords(scoring_run)

            # Koşullu kanal skorlama
            combined = {}

            if scoring_run.enable_ads:
                ads_results = calculate_bulk_ads_scores(keywords)
                for item in ads_results:
                    kid = item['keyword_id']
                    combined.setdefault(kid, {'keyword_id': kid})
                    combined[kid]['ads_score'] = item['ads_score']
                    combined[kid]['ads_rank'] = item['ads_rank']

            if scoring_run.enable_seo:
                seo_results = calculate_bulk_seo_scores(keywords)
                for item in seo_results:
                    kid = item['keyword_id']
                    combined.setdefault(kid, {'keyword_id': kid})
                    combined[kid]['seo_score'] = item['seo_score']
                    combined[kid]['seo_rank'] = item['seo_rank']

            if scoring_run.enable_social:
                social_results = calculate_bulk_social_scores(keywords)
                for item in social_results:
                    kid = item['keyword_id']
                    combined.setdefault(kid, {'keyword_id': kid})
                    combined[kid]['social_score'] = item['social_score']
                    combined[kid]['social_rank'] = item['social_rank']

            # Metrics map for snapshot
            metrics_map = {kw['id']: kw for kw in keywords}

            # Veritabanına kaydet
            for kid, scores in combined.items():
                kw_metrics = metrics_map.get(kid, {})
                keyword_score = KeywordScore(
                    scoring_run_id=scoring_run_id,
                    keyword_id=kid,
                    ads_score=scores.get('ads_score'),
                    seo_score=scores.get('seo_score'),
                    social_score=scores.get('social_score'),
                    ads_rank=scores.get('ads_rank'),
                    seo_rank=scores.get('seo_rank'),
                    social_rank=scores.get('social_rank'),
                    metrics_snapshot={
                        "monthly_volume": kw_metrics.get('monthly_volume', 0),
                        "trend_3m": kw_metrics.get('trend_3m', 0),
                        "trend_12m": kw_metrics.get('trend_12m', 0),
                        "competition_score": kw_metrics.get('competition_score', 0.5),
                        "snapshot_source": "workspace_keyword" if kw_metrics.get('_wk_id') else "legacy_keyword",
                        "wk_id": kw_metrics.get('_wk_id'),
                    }
                )
                self.db.add(keyword_score)

            self.db.commit()

            # Durumu güncelle
            transition(self.db, scoring_run, "scored")

            # Path B: relevance gerekiyor mu?
            should_compute_relevance = False
            if not scoring_run.skip_relevance:
                profile = self.db.query(BrandProfile).filter(
                    BrandProfile.id == scoring_run.brand_profile_id,
                    BrandProfile.status == "confirmed",
                    BrandProfile.deleted_at.is_(None),
                ).first()
                if profile:
                    should_compute_relevance = True

            return {
                'scoring_run_id': scoring_run_id,
                'scored_count': len(combined),
                'should_compute_relevance': should_compute_relevance,
            }

        except Exception as e:
            try:
                transition(self.db, scoring_run, "failed")
            except ValueError:
                # Zaten failed ise sessizce devam et
                self.db.rollback()
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