"""
Kanal atama sÃ¼recini orkestra eden ana modÃ¼l.
Pre-Filter AI katmanÄ± entegre (Faz 2).
"""
import logging
from typing import Dict, Any, List
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime

from app.database.models import (
    ScoringRun, ChannelCandidate, IntentAnalysis, ChannelPool,
    PreFilterResult, KeywordScore, KeywordRelevance
)
from app.core.channel.pool_builder import PoolBuilder
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.constants import (
    ADS_FINAL_CAPACITY, SEO_FINAL_CAPACITY, SOCIAL_FINAL_CAPACITY,
    BACKFILL_MAX_RATIO, BACKFILL_EXCLUDED_REASONS,
)
from app.generators.ai_service import AIService

logger = logging.getLogger(__name__)


class ChannelEngine:
    """
    Kanal atama motoru.
    Skorlanan kelimeler Ã¼zerinde kanal atama sÃ¼recini orkestra eder.
    """
    
    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = ai_service
        self.pool_builder = PoolBuilder(db)
        self.intent_analyzer = IntentAnalyzer(db, ai_service)
    
    def run_channel_assignment(self, scoring_run_id: int, relevance_coefficient: float = 1.0) -> Dict[str, Any]:
        """
        Tam kanal atama sÃ¼recini Ã§alÄ±ÅŸtÄ±rÄ±r.
        
        AdÄ±mlar:
        1. Aday havuzlarÄ± oluÅŸtur (sabit havuz boyutlarÄ±)
        2. Her kanal iÃ§in niyet analizi yap
        3. Filtreyi geÃ§enleri final havuza al
        
        Args:
            scoring_run_id: Skorlama Ã§alÄ±ÅŸtÄ±rmasÄ± ID
        
        Returns:
            SÃ¼reÃ§ Ã¶zeti
        """
        scoring_run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        
        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} bulunamadÄ±")

        if relevance_coefficient < 0.1 or relevance_coefficient > 3.0:
            raise ValueError(
                f"Invalid relevance coefficient: {relevance_coefficient}. "
                "Allowed range is 0.1 - 3.0."
            )
        
        # Durumu gÃ¼ncelle
        scoring_run.status = "intent_analysis"
        self.db.commit()
        
        results = {
            'scoring_run_id': scoring_run_id,
            'steps': {
                'effective_relevance_coefficient': relevance_coefficient
            }
        }
        
        try:
            # AdÄ±m 0: Temizlik (Ã–nceki denemelerden kalan verileri sil)
            self.db.query(ChannelPool).filter(ChannelPool.scoring_run_id == scoring_run_id).delete()
            self.db.query(PreFilterResult).filter(PreFilterResult.scoring_run_id == scoring_run_id).delete()
            self.db.query(IntentAnalysis).filter(IntentAnalysis.scoring_run_id == scoring_run_id).delete()
            self.db.query(ChannelCandidate).filter(ChannelCandidate.scoring_run_id == scoring_run_id).delete()
            self.db.commit()

            # AdÄ±m 1: Aday havuzlarÄ± oluÅŸtur
            pool_counts = self.pool_builder.build_candidate_pools(scoring_run_id, relevance_coefficient=relevance_coefficient)
            results['steps']['pool_building'] = pool_counts
            
            # AdÄ±m 2: Her kanal iÃ§in niyet analizi
            intent_results = {}
            for channel in ['ADS', 'SEO', 'SOCIAL']:
                intent_results[channel] = self.intent_analyzer.analyze_candidates(
                    scoring_run_id, channel
                )
            results['steps']['intent_analysis'] = intent_results
            
            # AdÄ±m 2.5: Pre-Filter AI katmanÄ±
            prefilter_results = self._run_pre_filters(scoring_run_id)
            results['steps']['pre_filtering'] = prefilter_results
            
            # Post-condition: pre_filter_results tablosunda kayÄ±t var mÄ±?
            pf_count = self.db.query(PreFilterResult).filter(
                PreFilterResult.scoring_run_id == scoring_run_id
            ).count()
            if pf_count == 0:
                logger.error(
                    f"PRE-FILTER POST-CONDITION FAILED: "
                    f"scoring_run_id={scoring_run_id} iÃ§in 0 kayÄ±t Ã¼retildi. "
                    f"SonuÃ§lar: {prefilter_results}"
                )
            else:
                logger.info(f"Pre-filter post-condition OK: {pf_count} kayÄ±t Ã¼retildi")
            
            # AdÄ±m 3: Final havuzlarÄ± oluÅŸtur (intent + prefilter + backfill)
            final_counts = self._build_final_pools_v2(scoring_run_id, scoring_run, relevance_coefficient=relevance_coefficient)
            results['steps']['final_pools'] = final_counts
            

            
            # Durumu gÃ¼ncelle
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
    
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # AdÄ±m 2.5: Pre-Filter AI katmanÄ±
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    def _run_pre_filters(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        TÃ¼m kanallar iÃ§in AI pre-filter Ã§alÄ±ÅŸtÄ±rÄ±r.
        Cross-channel transfer AKTÄ°F (Faz 4).
        """
        from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
        from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
        from app.core.channel.pre_filters.social_prefilter import SocialPreFilter

        results = {}

        # Her kanal iÃ§in pre-filter Ã§alÄ±ÅŸtÄ±r
        ads_pf = AdsPreFilter(self.db, self.ai_service)
        seo_pf = SeoPreFilter(self.db, self.ai_service)
        social_pf = SocialPreFilter(self.db, self.ai_service)

        # Faz A: TÃ¼m kanallarÄ± filtrele
        results['ADS'] = ads_pf.filter_candidates(scoring_run_id)
        results['SEO'] = seo_pf.filter_candidates(scoring_run_id)
        results['SOCIAL'] = social_pf.filter_candidates(scoring_run_id)

        # Faz B: Cross-channel transfer (ADS â†’ SEO)
        results['cross_channel'] = self._process_cross_channel_transfers(
            scoring_run_id, seo_pf)

        logger.info(
            f"Pre-filter tamamlandÄ±: "
            f"ADS={results['ADS']}, SEO={results['SEO']}, "
            f"SOCIAL={results['SOCIAL']}, "
            f"Cross-channel={results['cross_channel']}"
        )

        return results

    def _process_cross_channel_transfers(
        self,
        scoring_run_id: int,
        seo_pf
    ) -> Dict[str, Any]:
        """
        ADS'den elenen keyword'leri SEO'ya aktarÄ±r (2-faz).

        Faz A: ADS pre-filter sonuÃ§larÄ±ndan transfer_channel='SEO' olanlarÄ± al.
        Faz B: Bu keyword'ler iÃ§in:
            1. Synthetic IntentAnalysis kaydÄ± oluÅŸtur (source='transfer')
            2. Synthetic ChannelCandidate kaydÄ± oluÅŸtur
            3. SEO pre-filter Ã§alÄ±ÅŸtÄ±r
        """
        # Faz A: Transfer adaylarÄ±nÄ± bul
        transfer_candidates = (
            self.db.query(PreFilterResult)
            .filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.channel == 'ADS',
                PreFilterResult.is_kept == False,
                PreFilterResult.transfer_channel == 'SEO'
            )
            .all()
        )

        if not transfer_candidates:
            logger.info("Cross-channel: transfer edilecek keyword yok")
            return {"transferred": 0, "seo_kept": 0, "seo_eliminated": 0}

        transfer_keyword_ids = [tc.keyword_id for tc in transfer_candidates]
        logger.info(
            f"Cross-channel: {len(transfer_keyword_ids)} keyword "
            f"ADSâ†’SEO transferi baÅŸlatÄ±lÄ±yor"
        )

        # Faz B: Synthetic kayÄ±tlar oluÅŸtur
        # Mevcut SEO ChannelCandidate'larÄ±n max rank'ini bul
        from sqlalchemy import func as sa_func
        max_rank_result = (
            self.db.query(sa_func.max(ChannelCandidate.rank_in_channel))
            .filter(
                ChannelCandidate.scoring_run_id == scoring_run_id,
                ChannelCandidate.channel == 'SEO'
            )
            .scalar()
        )
        next_rank = (max_rank_result or 0) + 1

        created_count = 0
        for kw_id in transfer_keyword_ids:
            # Upsert-safe: aynÄ± keyword zaten SEO'da varsa atla
            existing_candidate = (
                self.db.query(ChannelCandidate)
                .filter(
                    ChannelCandidate.scoring_run_id == scoring_run_id,
                    ChannelCandidate.keyword_id == kw_id,
                    ChannelCandidate.channel == 'SEO'
                )
                .first()
            )
            if existing_candidate:
                logger.debug(
                    f"Transfer skip: keyword_id={kw_id} zaten SEO candidate"
                )
                continue

            # Transfer keyword iÃ§in SEO skorunu al
            kw_score = self.db.query(KeywordScore).filter(
                KeywordScore.scoring_run_id == scoring_run_id,
                KeywordScore.keyword_id == kw_id
            ).first()
            seo_score = float(kw_score.seo_score) if kw_score and kw_score.seo_score else 0.5
            seo_rank = kw_score.seo_rank if kw_score and kw_score.seo_rank else next_rank

            # Synthetic ChannelCandidate (SEO score ile)
            self.db.add(ChannelCandidate(
                scoring_run_id=scoring_run_id,
                keyword_id=kw_id,
                channel='SEO',
                raw_score=seo_score,
                rank_in_channel=seo_rank
            ))

            # Synthetic IntentAnalysis (source='transfer')
            existing_intent = (
                self.db.query(IntentAnalysis)
                .filter(
                    IntentAnalysis.scoring_run_id == scoring_run_id,
                    IntentAnalysis.keyword_id == kw_id,
                    IntentAnalysis.channel == 'SEO'
                )
                .first()
            )
            if not existing_intent:
                self.db.add(IntentAnalysis(
                    scoring_run_id=scoring_run_id,
                    keyword_id=kw_id,
                    channel='SEO',
                    intent_type='informational',
                    confidence_score=0.70,
                    ai_reasoning='ADSâ†’SEO cross-channel transfer',
                    is_passed=True,
                    source='transfer'
                ))

            next_rank += 1
            created_count += 1

        self.db.commit()

        # SEO pre-filter'Ä± transfer edilen keyword'ler Ã¼zerinde Ã§alÄ±ÅŸtÄ±r
        if created_count > 0:
            seo_result = seo_pf.filter_candidates(
                scoring_run_id,
                keyword_ids=transfer_keyword_ids
            )
        else:
            seo_result = {"kept": 0, "eliminated": 0, "fallback": 0, "total": 0}

        logger.info(
            f"Cross-channel tamamlandÄ±: {created_count} transfer, "
            f"SEO sonuÃ§={seo_result}"
        )

        return {
            "transferred": created_count,
            "seo_kept": seo_result.get("kept", 0),
            "seo_eliminated": seo_result.get("eliminated", 0),
        }

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # AdÄ±m 3: Final HavuzlarÄ± (v2 â€” intent + prefilter + backfill)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    def _load_relevance_map_for_pooling(self, scoring_run_id: int) -> Dict[int, float]:
        """Run icindeki keyword relevance skorlarini keyword_id bazinda doner."""
        rows = (
            self.db.query(KeywordRelevance.keyword_id, KeywordRelevance.relevance_score)
            .filter(KeywordRelevance.scoring_run_id == scoring_run_id)
            .all()
        )
        return {
            keyword_id: float(relevance_score)
            for keyword_id, relevance_score in rows
            if relevance_score is not None
        }

    def _load_channel_score_maps(self, scoring_run_id: int) -> Dict[str, Dict[int, float]]:
        """KeywordScore tablosunu kanal bazli hizli erisim map'ine cevirir."""
        rows = (
            self.db.query(
                KeywordScore.keyword_id,
                KeywordScore.ads_score,
                KeywordScore.seo_score,
                KeywordScore.social_score,
            )
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )
        score_maps = {"ADS": {}, "SEO": {}, "SOCIAL": {}}
        for keyword_id, ads_score, seo_score, social_score in rows:
            score_maps["ADS"][keyword_id] = float(ads_score) if ads_score is not None else 0.0
            score_maps["SEO"][keyword_id] = float(seo_score) if seo_score is not None else 0.0
            score_maps["SOCIAL"][keyword_id] = float(social_score) if social_score is not None else 0.0
        return score_maps

    def _calculate_pool_score_details(
        self,
        keyword_id: int,
        channel: str,
        channel_score_maps: Dict[str, Dict[int, float]],
        relevance_map: Dict[int, float],
        relevance_coefficient: float,
    ) -> Dict[str, Decimal]:
        """
        Pool tablosuna yazilacak vector similarity ve adjusted skoru hesaplar.
        Formula: adjusted_score = raw_score * relevance_score * relevance_coefficient
        Relevance yoksa 1.0 kabul edilir.
        """
        channel_scores = channel_score_maps.get(channel, {})
        raw_score = channel_scores.get(keyword_id, 0.0)
        relevance_score = relevance_map.get(keyword_id, 1.0)
        adjusted_score = raw_score * relevance_score * relevance_coefficient
        return {
            "relevance_score": Decimal(str(round(relevance_score, 3))),
            "adjusted_score": Decimal(str(round(adjusted_score, 4))),
        }

    def _build_final_pools_v2(
        self,
        scoring_run_id: int,
        scoring_run: ScoringRun,
        relevance_coefficient: float = 1.0
    ) -> Dict[str, int]:
        """
        Intent âœ“ + Pre-filter âœ“ keyword'lerden final havuz oluÅŸturur.
        Kapasite altÄ±na dÃ¼ÅŸerse backfill uygular (max %30).
        """
        final_counts = {}
        relevance_map = self._load_relevance_map_for_pooling(scoring_run_id)
        channel_score_maps = self._load_channel_score_maps(scoring_run_id)

        for channel, capacity in [
            ('ADS', ADS_FINAL_CAPACITY),
            ('SEO', SEO_FINAL_CAPACITY),
            ('SOCIAL', SOCIAL_FINAL_CAPACITY)
        ]:
            # Birincil: intent geÃ§en + prefilter geÃ§en
            passed_candidates = (
                self.db.query(IntentAnalysis, ChannelCandidate)
                .join(
                    ChannelCandidate,
                    and_(
                        IntentAnalysis.keyword_id == ChannelCandidate.keyword_id,
                        IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id,
                        IntentAnalysis.channel == ChannelCandidate.channel
                    )
                )
                .join(
                    PreFilterResult,
                    and_(
                        PreFilterResult.keyword_id == IntentAnalysis.keyword_id,
                        PreFilterResult.scoring_run_id == IntentAnalysis.scoring_run_id,
                        PreFilterResult.channel == IntentAnalysis.channel
                    )
                )
                .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
                .filter(IntentAnalysis.channel == channel)
                .filter(IntentAnalysis.is_passed == True)
                .filter(PreFilterResult.is_kept == True)
                .order_by(ChannelCandidate.rank_in_channel, ChannelCandidate.keyword_id)
                .limit(capacity)
                .all()
            )

            # Final havuza ekle
            for rank, (intent, candidate) in enumerate(passed_candidates, 1):
                score_details = self._calculate_pool_score_details(
                    candidate.keyword_id,
                    channel,
                    channel_score_maps,
                    relevance_map,
                    relevance_coefficient,
                )
                self.db.add(ChannelPool(
                    scoring_run_id=scoring_run_id,
                    keyword_id=candidate.keyword_id,
                    channel=channel,
                    final_rank=rank,
                    relevance_score=score_details["relevance_score"],
                    adjusted_score=score_details["adjusted_score"],
                    is_strategic=False
                ))

            current_count = len(passed_candidates)

            # â•â•â• BACKFILL: Kapasite altÄ±ysa borderline'dan doldur (max %30) â•â•â•
            if current_count < capacity:
                max_backfill = int(capacity * BACKFILL_MAX_RATIO)
                shortfall = min(capacity - current_count, max_backfill)
                already_in = {c.keyword_id for _, c in passed_candidates}

                # Backfill: is_fallback=True olanlarÄ± SQL'de dÄ±ÅŸla,
                # reason_code kontrolÃ¼nÃ¼ Python'da yap (JSON field gÃ¼venliÄŸi)
                borderline_raw = (
                    self.db.query(IntentAnalysis, ChannelCandidate, PreFilterResult)
                    .join(
                        ChannelCandidate,
                        and_(
                            IntentAnalysis.keyword_id == ChannelCandidate.keyword_id,
                            IntentAnalysis.scoring_run_id == ChannelCandidate.scoring_run_id,
                            IntentAnalysis.channel == ChannelCandidate.channel
                        )
                    )
                    .join(
                        PreFilterResult,
                        and_(
                            PreFilterResult.keyword_id == IntentAnalysis.keyword_id,
                            PreFilterResult.scoring_run_id == IntentAnalysis.scoring_run_id,
                            PreFilterResult.channel == IntentAnalysis.channel
                        )
                    )
                    .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
                    .filter(IntentAnalysis.channel == channel)
                    .filter(IntentAnalysis.is_passed == True)
                    .filter(PreFilterResult.is_kept == False)
                    .filter(PreFilterResult.is_fallback == False)
                    .filter(~ChannelCandidate.keyword_id.in_(already_in))
                    .order_by(ChannelCandidate.rank_in_channel, ChannelCandidate.keyword_id)
                    .limit(shortfall * 2)  # reason_code filtresi sonrasÄ± yeterli olsun
                    .all()
                )

                # Python tarafÄ±nda reason_code kontrolÃ¼ (JSON field gÃ¼venliÄŸi)
                borderline = []
                for intent, candidate, pf_result in borderline_raw:
                    reason_code = (pf_result.extra_data or {}).get("reason_code")
                    if reason_code in BACKFILL_EXCLUDED_REASONS:
                        continue
                    borderline.append((intent, candidate))
                    if len(borderline) >= shortfall:
                        break

                for offset, (intent, candidate) in enumerate(borderline, 1):
                    score_details = self._calculate_pool_score_details(
                        candidate.keyword_id,
                        channel,
                        channel_score_maps,
                        relevance_map,
                        relevance_coefficient,
                    )
                    self.db.add(ChannelPool(
                        scoring_run_id=scoring_run_id,
                        keyword_id=candidate.keyword_id,
                        channel=channel,
                        final_rank=current_count + offset,
                        relevance_score=score_details["relevance_score"],
                        adjusted_score=score_details["adjusted_score"],
                        is_strategic=False
                    ))

                if borderline:
                    logger.info(
                        f"{channel} backfill: {len(borderline)} keyword "
                        f"(max {shortfall}, kapasite {capacity})"
                    )
                current_count += len(borderline)

            final_counts[channel] = current_count

        self.db.commit()

        return final_counts
    
    def get_channel_pools(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        TÃ¼m kanal havuzlarÄ±nÄ± dÃ¶ner.
        
        Args:
            scoring_run_id: Skorlama Ã§alÄ±ÅŸtÄ±rmasÄ± ID
        
        Returns:
            Kanal havuzlarÄ±
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
                    'is_strategic': pool.is_strategic,
                    'relevance_score': float(pool.relevance_score) if pool.relevance_score is not None else None,
                    'adjusted_score': float(pool.adjusted_score) if pool.adjusted_score is not None else None,
                }
                for pool, keyword in pools
            ]
        
        return result

