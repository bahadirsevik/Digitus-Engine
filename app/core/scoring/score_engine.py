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
from sqlalchemy import update
from datetime import datetime
from loguru import logger

from app.database.models import (
    Keyword, ScoringRun, KeywordScore,
    BrandProfile, WorkspaceKeyword,
)
from app.core.constants import TREND_CLIP_MIN, TREND_CLIP_MAX
from app.core.scoring.ads_scorer import calculate_bulk_ads_scores
from app.core.scoring.seo_scorer import calculate_bulk_seo_scores
from app.core.scoring.social_scorer import calculate_bulk_social_scores
from app.core.scoring.normalizer import clip, derive_stage1_variables
from app.core.scoring.state_machine import transition


def _normalize_scoring_metrics(
    monthly_volume,
    trend_3m,
    trend_12m,
    competition_score,
) -> Optional[Dict[str, Any]]:
    """Ön temizlik (doküman Bölüm 2): V null/0 satırı ATILIR (None döner).

    Ölçek sözleşmesi:
      - trend_3m/trend_12m: skorlamada kullanılan ondalık oran, [-1,+3] kırpılmış
        (DB yüzde saklar: +%56 -> 0.56)
      - _raw_trend_3m/_raw_trend_12m: metrics_snapshot için ham yüzde değerleri
      - competition_score: [0,1]; null -> 0.0
    """
    try:
        volume = int(monthly_volume or 0)
    except (TypeError, ValueError):
        volume = 0
    if volume <= 0:
        return None

    try:
        raw_t3 = float(trend_3m) if trend_3m is not None else 0.0
    except (TypeError, ValueError):
        raw_t3 = 0.0
    try:
        raw_t12 = float(trend_12m) if trend_12m is not None else 0.0
    except (TypeError, ValueError):
        raw_t12 = 0.0
    try:
        competition = float(competition_score) if competition_score is not None else 0.0
    except (TypeError, ValueError):
        competition = 0.0

    return {
        "monthly_volume": volume,
        "trend_3m": clip(raw_t3 / 100.0, TREND_CLIP_MIN, TREND_CLIP_MAX),
        "trend_12m": clip(raw_t12 / 100.0, TREND_CLIP_MIN, TREND_CLIP_MAX),
        "competition_score": min(1.0, max(0.0, competition)),
        "_raw_trend_3m": raw_t3,
        "_raw_trend_12m": raw_t12,
    }


class ScoreEngine:
    """
    Ana skorlama motoru.
    Tüm kanallara göre kelimeleri skorlar ve veritabanına kaydeder.
    """

    def __init__(self, db: Session):
        self.db = db

    def _select_keywords_with_metrics(self, run: ScoringRun) -> Tuple[List[Dict], int]:
        """
        WorkspaceKeyword'den snapshot metrikleri ile keyword listesi.
        Global Keyword.monthly_volume vs KULLANILMAZ.

        Returns:
            (keywords, skipped_invalid_metrics) — V null/0 satırlar atılır.
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

        # Scoring dict'i WK metriklerinden oluştur; geçersiz satırlar atılır
        result = []
        skipped = 0
        for kw, wk in base_query.all():
            metrics = _normalize_scoring_metrics(
                wk.monthly_volume,
                wk.trend_3m,
                wk.trend_12m,
                wk.competition_score,
            )
            if metrics is None:
                skipped += 1
                continue
            result.append({
                'id': kw.id,
                'keyword': kw.keyword,  # deterministik tie-break için
                **metrics,
                '_wk_id': wk.id,  # metrics_snapshot için
            })
        return result, skipped

    def _legacy_select_keywords(self, run: ScoringRun) -> Tuple[List[Dict], int]:
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

        result = []
        skipped = 0
        for kw in keywords:
            metrics = _normalize_scoring_metrics(
                kw.monthly_volume,
                kw.trend_3m,
                kw.trend_12m,
                kw.competition_score,
            )
            if metrics is None:
                skipped += 1
                continue
            result.append({
                'id': kw.id,
                'keyword': kw.keyword,
                **metrics,
                '_wk_id': None,
            })
        return result, skipped

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
        auto_assign_channels: bool = False,
        algorithm_version: str = "v3",
        commit: bool = True,
    ) -> ScoringRun:
        """
        Yeni bir skorlama çalıştırması oluşturur.

        algorithm_version yalnız BURADA yazılır ve immutable'dır — dispatch
        ve worker DB'deki değeri okur, runtime global ayardan türetmez
        (plan Faz E).

        `commit=False`: satır yalnız FLUSH edilir (id atanır), transaction
        AÇIK kalır. Deneme workspace'lerinde run INSERT'i ile slot claim'i
        AYNI transaction'da olmak zorundadır (bkz. core/trial_authorization).
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
            auto_assign_channels=auto_assign_channels,
            algorithm_version=algorithm_version,
            status="pending"
        )

        self.db.add(scoring_run)
        if commit:
            self.db.commit()
        else:
            self.db.flush()
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
            # Keyword seçimi (V null/0 satırlar ön temizlikte atılır)
            if scoring_run.brand_profile_id:
                keywords, skipped_invalid = self._select_keywords_with_metrics(scoring_run)
            else:
                keywords, skipped_invalid = self._legacy_select_keywords(scoring_run)

            if skipped_invalid:
                logger.warning(
                    "Scoring run {}: {} satır geçersiz metrik (V null/0) nedeniyle atlandı",
                    scoring_run_id, skipped_invalid,
                )

            # Türetilmiş liste-göreli değişkenler (H/Ln/TrK/MB) — tek noktadan,
            # üç kanal aynı değerleri paylaşır (doküman Bölüm 3)
            derive_stage1_variables(keywords)

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
                    # Ölçek sözleşmesi: üst düzey trend_3m/trend_12m HAM YÜZDE
                    # snapshot'tır (DB ile aynı); derived.t3/t12 ise skorlamada
                    # kullanılan oran + [-1,+3] kırpılmış değerlerdir.
                    metrics_snapshot={
                        "monthly_volume": kw_metrics.get('monthly_volume', 0),
                        "trend_3m": kw_metrics.get('_raw_trend_3m', 0.0),
                        "trend_12m": kw_metrics.get('_raw_trend_12m', 0.0),
                        "competition_score": kw_metrics.get('competition_score', 0.0),
                        "snapshot_source": "workspace_keyword" if kw_metrics.get('_wk_id') else "legacy_keyword",
                        "wk_id": kw_metrics.get('_wk_id'),
                        "derived": {
                            "h": kw_metrics.get('h'),
                            "ln": kw_metrics.get('ln'),
                            "trk": kw_metrics.get('trk'),
                            "mb": kw_metrics.get('mb'),
                            "t3": kw_metrics.get('trend_3m', 0.0),
                            "t12": kw_metrics.get('trend_12m', 0.0),
                            "spec": "v2",
                        },
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
                # total_keywords (skorlama öncesi sayım) ile scored_count
                # farklılaşması normaldir: V null/0 satırlar skorlanmaz.
                'skipped_invalid_metrics': skipped_invalid,
                'should_compute_relevance': should_compute_relevance,
            }

        except Exception as e:
            try:
                transition(self.db, scoring_run, "failed")
            except ValueError:
                # Zaten failed ise sessizce devam et
                self.db.rollback()
                self.db.execute(
                    update(ScoringRun)
                    .where(ScoringRun.id == scoring_run.id)
                    .values(status="failed")
                )
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
