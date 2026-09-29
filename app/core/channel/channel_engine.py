"""
Kanal atama sÃ¼recini orkestra eden ana modÃ¼l.
Pre-Filter AI katmanÄ± entegre (Faz 2).
"""
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Any, List
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime

# P1.4: KeywordRelevance BILEREK import edilmiyor — relevance okumasi yalniz
# `app.core.relevance.load_effective_relevance_map` kapisindan gecer.
from app.database.models import (
    ScoringRun, ChannelCandidate, IntentAnalysis, ChannelPool,
    PreFilterResult, KeywordScore, Keyword
)
from app.database.connection import SessionLocal
from app.core.channel.pool_builder import PoolBuilder
from app.core.screening.assistive import (
    ACTION_TRANSFER,
    audit_later_action,
)
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON, BrandExclusionFilter
from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter
from app.core.channel.pre_filters.social_prefilter import SocialPreFilter
from app.core.constants import (
    ADS_FINAL_CAPACITY, SEO_FINAL_CAPACITY, SOCIAL_FINAL_CAPACITY,
    ADS_MAX_EXPANSION_POOL_SIZE, SEO_MAX_EXPANSION_POOL_SIZE,
    SOCIAL_MAX_EXPANSION_POOL_SIZE, MAX_EXPANSION_ROUNDS,
    MAX_EXPANSION_AI_BATCHES,
    INTENT_BATCH_SIZE, BRAND_FILTER_BATCH_SIZE, PREFILTER_BATCH_SIZE,
    INTENT_SOURCE_TRANSFER_AI, INTENT_SOURCE_FALLBACK,
    STOP_CAPACITY_REACHED, STOP_BUDGET_REACHED, STOP_NO_MORE_CANDIDATES,
    STOP_MAX_ROUNDS_REACHED, STOP_CHANNEL_DISABLED,
    SEO_W_GT, SEO_W_GA, DEFAULT_AI_CLASS_WHEN_MISSING,
    RISING_OPPORTUNITY_TOP_RATIO, RISING_OPPORTUNITY_MIN_H,
    RISING_OPPORTUNITY_LABEL,
)
from app.core.channel.ai_budget import AiCallBudget
from app.core.scoring.state_machine import transition
from app.generators.ai_service import AIService

logger = logging.getLogger(__name__)

POLICY_VERSION_CHANGED = "POLICY_VERSION_CHANGED"
STRATEGY_CHANGED = "STRATEGY_CHANGED"


class PolicyVersionChangedError(Exception):
    """Atama sürerken workspace politika/anchor sürümü değişti — havuz
    aktive edilmedi (plan v13 finalize sözleşmesi)."""


class StrategyChangedError(Exception):
    """v2_1: atama sürerken onaylı kanal stratejisi değişti — havuz aktive
    edilmedi (plan v2.1 Faz C yarış sözleşmesi)."""


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
    
    def _finalize_pool_versions(
        self,
        scoring_run: ScoringRun,
        *,
        requested_policy_version: int | None,
        requested_anchor_version: int | None,
        parent_task_id: str | None = None,
        screening_apply: dict | None = None,
    ) -> None:
        """Sürüm doğrulaması + run sürüm yazımı — transition ile AYNI
        transaction'da (commit'i transition yapar). Eşleşmezse
        PolicyVersionChangedError: transition çağrılmaz, run sürümü yazılmaz.
        """
        if requested_policy_version is None or not scoring_run.brand_profile_id:
            return  # sürümsüz (workspace'siz/legacy) dispatch — sözleşme dışı

        from app.config import settings as _settings
        from app.database.models import BrandProfile as _BrandProfile

        workspace = (
            self.db.query(_BrandProfile)
            .filter(_BrandProfile.id == scoring_run.brand_profile_id)
            .with_for_update()
            # Identity-map tuzağı (Codex Faz C #1): session profili daha önce
            # yüklediyse FOR UPDATE kilidi alınır ama ESKİ attribute'lar döner;
            # populate_existing DB'deki güncel sürümleri zorlar.
            .populate_existing()
            .first()
        )
        if workspace is None:
            return

        relevance_required = bool(
            _settings.ENABLE_RELEVANCE_RERANK and not scoring_run.skip_relevance
        )
        policy_matches = (
            int(workspace.policy_version or 1) == requested_policy_version
        )
        anchor_matches = (
            not relevance_required
            or (
                requested_anchor_version is not None
                and int(workspace.anchor_version or 1) == requested_anchor_version
            )
        )
        if not (policy_matches and anchor_matches):
            self.db.rollback()  # kilit bırakılır, bekleyen yazım varsa atılır
            raise PolicyVersionChangedError(
                f"{POLICY_VERSION_CHANGED}: atama sürerken workspace "
                f"politika/profil sürümü değişti — havuz aktive edilmedi, "
                f"yeniden atama gerekli."
            )

        scoring_run.channel_pool_policy_version = requested_policy_version
        if relevance_required and requested_anchor_version is not None:
            scoring_run.relevance_anchor_version = requested_anchor_version
        self._finalize_screening_provenance(
            scoring_run, parent_task_id=parent_task_id,
            screening_apply=screening_apply)

    def _finalize_screening_provenance(
        self, scoring_run: ScoringRun, *, parent_task_id: str | None = None,
        screening_apply: dict | None = None
    ) -> None:
        """Havuzun tarama künyesi (plan §7.4, Codex 17/18. tur).

        `parent_task_id` verilirse künye BU KOŞUNUN attempt'inden yazılır
        (Codex 18. tur #2): "run'ın en son attempt'i" varsayımı assistive
        açıldığında yanlış satırı okuyabilirdi.

        Başarılı finalize'da AÇIKÇA yazılır — NULL bırakmak denetimde
        "bilinmiyor" demektir ve freshness ekseni bunu yorumlayamaz:
          - off/shadow: havuz BASELINE'dır -> mode='off', context SHA yok
            (shadow taraması yalnız `is_applied=false` ölçümdür)
          - assistive: mode='assistive' + kullanılan bağlam SHA'sı
        Attempt satırı yoksa (eski/dispatcher'sız yollar) 'off' yazılır.
        """
        from app.database.models import ChannelAssignmentAttempt

        query = (self.db.query(ChannelAssignmentAttempt)
                 .filter(ChannelAssignmentAttempt.scoring_run_id
                         == scoring_run.id))
        if parent_task_id:
            attempt = query.filter(
                ChannelAssignmentAttempt.parent_task_id == parent_task_id
            ).first()
            if attempt is None:
                # Kimlik verildi ama satır yok: SESSİZCE başka bir
                # attempt'in künyesi yazılmaz — fail-safe 'off'
                scoring_run.channel_pool_screening_mode = "off"
                scoring_run.channel_pool_screening_context_sha256 = None
                return
        else:
            attempt = query.order_by(
                ChannelAssignmentAttempt.id.desc()).first()
        mode = getattr(attempt, "screening_mode", None) or "off"
        # Codex 25. tur #1: KUNYE ATTEMPT MODUNDAN DEGIL, GERCEK SONUCTAN
        # yazilir. Kill switch / drift / APPLY_FAILED nedeniyle baseline
        # kurulduysa havuz assistive DEGILDIR; 'assistive' damgasi
        # freshness eksenini ve sonraki denetimleri yaniltirdi.
        applied_ok = bool((screening_apply or {}).get("applied")) and bool(
            (screening_apply or {}).get("materialization_identity_sha256"))
        if mode == "assistive" and not applied_ok:
            # `screening_apply` HIC verilmemisse de fail-open YOK: kanit
            # yoksa havuz baseline sayilir (Codex 26. tur #1)
            scoring_run.channel_pool_screening_mode = "off"
            scoring_run.channel_pool_screening_context_sha256 = None
            return
        if mode == "assistive":
            scoring_run.channel_pool_screening_mode = "assistive"
            scoring_run.channel_pool_screening_context_sha256 = getattr(
                attempt, "context_sha256", None)
        else:
            scoring_run.channel_pool_screening_mode = "off"
            scoring_run.channel_pool_screening_context_sha256 = None

    def _finalize_strategy_version(
        self,
        scoring_run: ScoringRun,
        *,
        requested_strategy_version: int | None,
    ) -> None:
        """v2_1 finalize yarış sözleşmesi (plan v2.1 Faz C).

        _finalize_pool_versions ile AYNI kalıp: workspace FOR UPDATE ile
        tazelenir; onaylı strateji sürümü dispatch snapshot'ından farklıysa
        StrategyChangedError — transition çağrılmaz, havuz aktive edilmez.
        Başarıda channel_pool_strategy_version dispatch snapshot'ına eşit
        yazılır. v2 run'larda tamamen atlanır.
        """
        if getattr(scoring_run, "algorithm_version", "v2") != "v2_1":
            return
        if not scoring_run.brand_profile_id:
            return

        from app.database.models import BrandProfile as _BrandProfile

        workspace = (
            self.db.query(_BrandProfile)
            .filter(_BrandProfile.id == scoring_run.brand_profile_id)
            .with_for_update()
            # Identity-map tuzağı (Codex Faz C #1): populate_existing olmadan
            # önceden yüklenmiş profil ESKİ strategy_version döndürür ve
            # gerçek iki-session yarışı kaçar.
            .populate_existing()
            .first()
        )
        current = (
            int(workspace.strategy_version or 0) if workspace is not None else 0
        )
        if (
            requested_strategy_version is None
            or current != int(requested_strategy_version)
        ):
            self.db.rollback()
            raise StrategyChangedError(
                f"{STRATEGY_CHANGED}: atama sürerken onaylı kanal stratejisi "
                f"değişti (istenen {requested_strategy_version}, güncel "
                f"{current}) — havuz aktive edilmedi, yeniden atama gerekli."
            )
        scoring_run.channel_pool_strategy_version = int(requested_strategy_version)

    def _get_active_channels(self, run: ScoringRun) -> List[str]:
        """ScoringRun enable_* flag'lerine göre aktif kanalları döner."""
        channels = []
        if run.enable_ads:
            channels.append('ADS')
        if run.enable_seo:
            channels.append('SEO')
        if run.enable_social:
            channels.append('SOCIAL')
        return channels

    def run_channel_assignment(
        self,
        scoring_run_id: int,
        relevance_coefficient: float = 1.0,
        task_id: str | None = None,
        requested_policy_version: int | None = None,
        requested_anchor_version: int | None = None,
        recompute_relevance: bool = False,
        requested_strategy_version: int | None = None,
    ) -> Dict[str, Any]:
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

        if relevance_coefficient < 0.1 or relevance_coefficient > 3.0:
            raise ValueError(
                f"Invalid relevance coefficient: {relevance_coefficient}. "
                "Allowed range is 0.1 - 3.0."
            )
        
        self._scoring_run = scoring_run  # Cache for helper methods

        # Durumu güncelle — dispatch helper auto/manual yolda bu geçişi
        # önceden yapmış olabilir. Eski doğrudan engine çağrıları için
        # geriye uyumlu olarak burada da destekliyoruz.
        if scoring_run.status != "channel_assigning":
            transition(self.db, scoring_run, target="channel_assigning")
        
        results = {
            'scoring_run_id': scoring_run_id,
            'steps': {
                'effective_relevance_coefficient': relevance_coefficient
            }
        }
        
        try:
            # Adım -1 (plan v13): relevance bayatsa havuz kurulmadan ÖNCE
            # sürümlü core servisle tazele. Başarısızlık assignment'ı
            # BAŞARISIZ yapar (eski/neutral relevance ile devam YOK — anchor
            # değiştiyse eski sıralama yanlıştır); run policy-stale kalır.
            if recompute_relevance and scoring_run.brand_profile_id:
                self._update_assignment_progress(
                    task_id, 3, "Relevance skorları tazeleniyor."
                )
                from app.core.relevance import (
                    RelevanceRefreshError,
                    refresh_keyword_relevance,
                )
                from app.core.telemetry import UsageCollector
                from app.database.models import BrandProfile as _BrandProfile

                # P1.3: ONAY KAPISI. Bu sorgu eskiden yalniz id ile
                # cekiyordu; ayni islemin diger iki yolu (manuel compute ve
                # onay-sonrasi background) ile pipeline'in diger tuketicileri
                # (pool_builder, brand_filter, brand_defense) confirmed +
                # deleted_at kosuyordu. Kapisiz hali, havuzda KULLANILMAYACAK
                # bir relevance icin ucretli embedding harciyordu.
                _profile = self.db.query(_BrandProfile).filter(
                    _BrandProfile.id == scoring_run.brand_profile_id,
                    _BrandProfile.status == "confirmed",
                    _BrandProfile.deleted_at.is_(None),
                ).first()
                if _profile is None:
                    # Sessiz devam YOK (yukaridaki sozlesme): recompute
                    # istendigi halde onayli profil yoksa havuz eski/notr
                    # relevance ile kurulamaz.
                    raise RelevanceRefreshError(
                        "Relevance tazelenemedi: run'a bagli workspace "
                        "onaylanmis degil veya arsivlenmis. Profili onaylayip "
                        "kanal atamasini yeniden baslatin."
                    )
                if requested_anchor_version is not None:
                    # P1.3: harcama ledger'a baglanir. Onceden collector
                    # gecilmedigi icin RelevanceScorer kendi client'ini kurup
                    # telemetrinin disinda kaliyordu.
                    _collector = UsageCollector(
                        scoring_run_id=scoring_run_id,
                        brand_profile_id=scoring_run.brand_profile_id,
                    )
                    try:
                        refresh_keyword_relevance(
                            self.db,
                            scoring_run,
                            _profile,
                            requested_anchor_version=requested_anchor_version,
                            collector=_collector,
                        )
                    finally:
                        _collector.finalize()
                    results['steps']['relevance_refresh'] = {
                        "anchor_version": requested_anchor_version,
                    }

            # ASSISTIVE (plan §8.2): tarama TAMAMLANMISSA ve TAZE
            # dogrulamalar gecerse aday kumesi union'dan gelir; aksi halde
            # sessizce BASELINE (kullaniciya hata GITMEZ). Plan insasi
            # TEMIZLIKTEN ONCE yapilir: hicbir dogrulama hatasi canli
            # havuzu silmis durumda birakmaz.
            self._update_assignment_progress(task_id, 5, "Kanal ataması hazırlanıyor.")
            from app.core.screening.assistive import build_applied_plan

            applied_plan, screening_skip = build_applied_plan(
                self.db, scoring_run_id=scoring_run_id,
                parent_task_id=task_id,
                relevance_coefficient=relevance_coefficient)

            # Adim 0+1: Temizlik + aday havuzu TEK transaction (24. tur #2)
            self._update_assignment_progress(task_id, 15, "Aday havuzları kuruluyor.")
            pool_counts, screening_apply = self._build_pools_atomically(
                scoring_run_id,
                relevance_coefficient=relevance_coefficient,
                applied_plan=applied_plan, screening_skip=screening_skip,
                parent_task_id=task_id)
            results['steps']['pool_building'] = pool_counts
            results['steps']['screening_apply'] = screening_apply
            # Transfer/expansion adaylari da AYNI kaynak kunyesini alir
            self._applied_context = (applied_plan.context()
                                     if (applied_plan is not None
                                         and screening_apply.get("applied"))
                                     else None)

            # Adım 1.5: Deterministik rakip bloğu — intent'ten ÖNCE (plan A).
            # Rakip terimli aday hiçbir AI katmanına girmez (terminal satır).
            from app.core.channel.competitor_filter import apply_competitor_block
            results['steps']['competitor_filter'] = apply_competitor_block(
                self.db, scoring_run_id
            )
            
            # Adim 2: Her kanal icin niyet analizi — paralel (ayri DB session)
            self._update_assignment_progress(task_id, 35, "Intent analizi başlıyor.")
            intent_results = self._analyze_intent_parallel(scoring_run_id, task_id=task_id)
            results['steps']['intent_analysis'] = intent_results

            # Adim 2.4: Marka profili dislama temalari — kanal bagimsiz paylasimli filtre
            self._update_assignment_progress(task_id, 50, "Marka dışlama filtresi çalışıyor.")
            brand_filter_results = self._run_brand_filter(scoring_run_id, task_id=task_id)
            results['steps']['brand_filtering'] = brand_filter_results
            
            # AdÄ±m 2.5: Pre-Filter AI katmanÄ±
            self._update_assignment_progress(task_id, 60, "Kanal prefilter başlıyor.")
            prefilter_results = self._run_pre_filters(scoring_run_id, task_id=task_id)
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
            self._update_assignment_progress(task_id, 80, "Final kanal havuzları oluşturuluyor.")
            final_counts = self._build_final_pools_v2(scoring_run_id, scoring_run, relevance_coefficient=relevance_coefficient)
            results['steps']['final_pools'] = final_counts

            expansion_results = self._run_expansion_rounds(
                scoring_run_id,
                scoring_run,
                final_counts,
                relevance_coefficient=relevance_coefficient,
                task_id=task_id,
            )
            results['steps']['expansion_rounds'] = expansion_results
            if any(item.get("rounds_run", 0) > 0 for item in expansion_results.values()):
                final_counts = self._build_final_pools_v2(
                    scoring_run_id,
                    scoring_run,
                    relevance_coefficient=relevance_coefficient,
                )
                results['steps']['final_pools'] = final_counts
                for channel, count in final_counts.items():
                    if channel in expansion_results:
                        expansion_results[channel]["final_count_after_expansion"] = count
                        expansion_results[channel]["unfilled_count"] = max(
                            (expansion_results[channel].get("capacity") or 0) - count, 0
                        )

            # Adım 4: SEO metadata — seçim SONRASI, yalnız kesin final havuz.
            # Hata run'ı ASLA fail etmez; başarısızlık yalnız UNAVAILABLE
            # işareti bırakır (is_kept/final_rank/adjusted_score değişmez).
            results['steps']['seo_metadata'] = self._run_seo_metadata_step(
                scoring_run_id, scoring_run, task_id=task_id
            )

            # Faz G: seçim kalitesi gözlemlenebilirliği — transition'dan hemen
            # önce toplanır; hata run'ı ASLA fail etmez
            results['selection_quality'] = self._collect_selection_quality(
                scoring_run_id, results
            )

            # Finalize (plan v13): sürüm doğrulaması + run sürüm yazımı,
            # channel_assigned transition'ı ile AYNI transaction'da.
            # anchor kontrolü yalnız recompute'ta değil, relevance bu run
            # için ETKİNSE her zaman yapılır (uzun atama sırasında profil
            # değişmiş olabilir). Eşleşmezse transition YOK — run eski
            # sürümünde/policy-stale kalır, partial havuz merkezi guard'la
            # tüketilemez, retry Adım 0 temizliğinden geçer.
            self._finalize_pool_versions(
                scoring_run,
                requested_policy_version=requested_policy_version,
                requested_anchor_version=requested_anchor_version,
                parent_task_id=task_id,
                # Kunye GERCEK sonuctan: baseline'a dusuldiyse 'off'
                screening_apply=results['steps'].get('screening_apply'),
            )
            # v2.1: strateji sürümü de AYNI transaction'da doğrulanır/yazılır
            self._finalize_strategy_version(
                scoring_run,
                requested_strategy_version=requested_strategy_version,
            )
            transition(self.db, scoring_run, target="channel_assigned")
            self._update_assignment_progress(task_id, 100, "Kanal ataması tamamlandı.")

            results['status'] = 'channel_assigned'
        
        except Exception as e:
            transition(self.db, scoring_run, target="failed")
            results['status'] = 'failed'
            results['error'] = str(e)
            raise e
        
        return results
    
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # AdÄ±m 2.5: Pre-Filter AI katmanÄ±
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


    # ─────────────────────────────────────────────────────────
    # Adim 2: Paralel intent analizi
    # ─────────────────────────────────────────────────────────

    def _make_batch_progress_callback(
        self,
        task_id: str | None,
        *,
        start: int,
        end: int,
        total_batches: int,
        label: str,
    ) -> Callable[[str, int], None] | None:
        if not task_id or total_batches <= 0:
            return None

        lock = threading.Lock()
        completed = 0
        last_progress = start

        def _callback(channel: str, batches_done: int = 1) -> None:
            nonlocal completed, last_progress
            with lock:
                completed = min(total_batches, completed + max(1, batches_done))
                ratio = completed / max(total_batches, 1)
                progress = start + int((end - start) * ratio)
                progress = max(last_progress, min(end, progress))
                last_progress = progress
                message = f"{label}: {completed}/{total_batches} batch tamamlandı"
                if channel:
                    message = f"{message} ({channel})"
            self._update_assignment_progress(task_id, progress, message)

        return _callback

    def _intent_batch_count(self, scoring_run_id: int) -> int:
        active_channels = self._get_active_channels(self._scoring_run)
        total = 0
        for channel in active_channels:
            count = (
                self.db.query(ChannelCandidate)
                .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
                .filter(ChannelCandidate.channel == channel)
                .count()
            )
            total += math.ceil(count / INTENT_BATCH_SIZE) if count else 0
        return total

    def _prefilter_batch_count(self, scoring_run_id: int) -> int:
        active_channels = self._get_active_channels(self._scoring_run)
        total = 0
        for channel in active_channels:
            if channel == 'SEO':
                # SEO prefilter seçimde deterministik — AI batch'i yok
                continue
            count = (
                self.db.query(IntentAnalysis)
                .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
                .filter(IntentAnalysis.channel == channel)
                .filter(IntentAnalysis.is_passed == True)  # noqa: E712
                .count()
            )
            total += math.ceil(count / PREFILTER_BATCH_SIZE) if count else 0
        return total

    def _build_pools_atomically(self, scoring_run_id: int, *,
                                relevance_coefficient: float,
                                applied_plan=None, screening_skip=None,
                                parent_task_id: str | None = None):
        """Temizlik + aday havuzu TEK transaction; hata ESKIYI KORUR.

        Assistive yazimi patlarsa rollback eski canli havuzu geri getirir
        ve kosu BASELINE ile surer (invaryant 7); sebep denetime yazilir.
        Dönüş: `(pool_counts, screening_apply_info)`.
        """
        try:
            self._clear_previous_assignment(scoring_run_id)
            pool_counts = self.pool_builder.build_candidate_pools(
                scoring_run_id,
                relevance_coefficient=relevance_coefficient,
                applied_plan=applied_plan, commit=False)
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            if applied_plan is None:
                raise
            # Codex 25. tur #2: bu attempt icin ONCEDEN yazilmis
            # `is_applied=true` audit varsa baseline'a DUSULEMEZ — canli
            # havuz baseline, audit "uygulandi" derdi. Eski canli durum
            # korunur, kosu fail-closed duser.
            if self._has_applied_audit(applied_plan.attempt_id,
                                       scoring_run_id):
                logger.error(
                    f"assistive yazimi patladi ve run {scoring_run_id} icin "
                    f"applied audit MEVCUT — baseline'a dusulmuyor "
                    f"(fail-closed)")
                raise
            logger.exception(
                f"assistive union uygulanamadi (run {scoring_run_id}) "
                f"— baseline'a dusuluyor")
            screening_skip = {"reason": "APPLY_FAILED",
                              "detail": str(exc)[:500]}
            applied_plan = None
            self._clear_previous_assignment(scoring_run_id)
            pool_counts = self.pool_builder.build_candidate_pools(
                scoring_run_id,
                relevance_coefficient=relevance_coefficient,
                applied_plan=None, commit=False)
            self.db.commit()
        # Kaynak/fallback denetimi (24. tur #5 — sebep artik gorunur)
        info = ({"applied": True,
                 "attempt_id": applied_plan.attempt_id,
                 "screening_job_id": applied_plan.screening_job_id,
                 "applied_channels": list(applied_plan.applied_channels),
                 "materialization_identity_sha256":
                     applied_plan.identity_sha256,
                 "budgets": applied_plan.budgets}
                if applied_plan is not None
                else {"applied": False,
                      "reason": (screening_skip or {}).get("reason",
                                                           "BASELINE"),
                      "detail": (screening_skip or {}).get("detail")})
        self._record_screening_apply(scoring_run_id,
                                     parent_task_id=parent_task_id,
                                     info=info)
        return pool_counts, info

    def _has_applied_audit(self, attempt_id: int,
                           scoring_run_id: int) -> bool:
        """Bu attempt icin `is_applied=true` audit satiri var mi?"""
        from app.database.models import CorpusCandidateSelection

        return bool(
            self.db.query(CorpusCandidateSelection.id)
            .filter(CorpusCandidateSelection.assignment_attempt_id
                    == attempt_id,
                    CorpusCandidateSelection.scoring_run_id == scoring_run_id,
                    CorpusCandidateSelection.is_applied.is_(True))
            .first())

    def _record_screening_apply(self, scoring_run_id: int, *,
                                parent_task_id: str | None,
                                info: dict) -> None:
        """Fallback sebebi attempt MANIFESTINE de yazilir (25. tur #3).

        Parent TaskResult silinse/dondurulsa bile "neden baseline'a
        dusuldu" sorusu attempt satirindan cevaplanabilir olmali.
        """
        from app.database.models import ChannelAssignmentAttempt

        query = self.db.query(ChannelAssignmentAttempt).filter(
            ChannelAssignmentAttempt.scoring_run_id == scoring_run_id)
        attempt = (query.filter(ChannelAssignmentAttempt.parent_task_id
                                == parent_task_id).first()
                   if parent_task_id
                   else query.order_by(
                       ChannelAssignmentAttempt.id.desc()).first())
        if attempt is None:
            return
        manifest = dict(attempt.manifest or {})
        manifest["screening_apply"] = info
        attempt.manifest = manifest
        self.db.commit()

    def _clear_previous_assignment(self, scoring_run_id: int) -> None:
        """Onceki denemeden kalan atama verisi — COMMIT ETMEZ.

        Commit cagirana aittir: temizlik + yeni adaylar + audit ayni
        transaction'da kalir (Codex 24. tur #2).
        """
        self.db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == scoring_run_id).delete()
        self.db.query(PreFilterResult).filter(
            PreFilterResult.scoring_run_id == scoring_run_id).delete()
        self.db.query(IntentAnalysis).filter(
            IntentAnalysis.scoring_run_id == scoring_run_id).delete()
        self.db.query(ChannelCandidate).filter(
            ChannelCandidate.scoring_run_id == scoring_run_id).delete()

    def _analyze_intent_parallel(
        self,
        scoring_run_id: int,
        task_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        ADS / SEO / SOCIAL intent analizini paralel olarak calistirir.
        Her kanal kendi DB session'ini olusturur — SQLAlchemy thread safety.
        """
        progress_callback = self._make_batch_progress_callback(
            task_id,
            start=35,
            end=50,
            total_batches=self._intent_batch_count(scoring_run_id),
            label="Intent analizi",
        )

        def _worker(channel: str):
            db = SessionLocal()
            try:
                analyzer = IntentAnalyzer(db, self.ai_service)
                result = analyzer.analyze_candidates(
                    scoring_run_id,
                    channel,
                    progress_callback=progress_callback,
                )
                db.commit()
                return channel, result
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        active_channels = self._get_active_channels(self._scoring_run)
        intent_results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=len(active_channels) or 1) as executor:
            futures = {executor.submit(_worker, ch): ch for ch in active_channels}
            for future in as_completed(futures):
                channel, result = future.result()
                intent_results[channel] = result

        return intent_results

    def _brand_filter_batch_count(self, scoring_run_id: int) -> int:
        count = (
            self.db.query(IntentAnalysis.keyword_id)
            .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
            .filter(IntentAnalysis.is_passed == True)  # noqa: E712
            .distinct()
            .count()
        )
        from app.core.constants import BRAND_FILTER_BATCH_SIZE

        return math.ceil(count / BRAND_FILTER_BATCH_SIZE) if count else 0

    def _run_seo_metadata_step(
        self,
        scoring_run_id: int,
        scoring_run: ScoringRun,
        task_id: str | None = None,
    ) -> Dict[str, Any]:
        """Seçim-sonrası SEO metadata adımı (B3).

        Yalnız kesin final SEO havuzundaki kelimeler için koşar. Her türlü
        hata yutulur — motor hatası önceden başarıyla yazılmış metadata'yı
        EZMEZ (generate_metadata yalnız SEO_METADATA almamışları
        UNAVAILABLE işaretler). Metadata çağrıları expansion bütçesi DIŞIDIR.
        """
        if not scoring_run.enable_seo:
            return {"skipped": True, "reason": "seo_disabled"}

        seo_pool_ids = [
            row.keyword_id
            for row in (
                self.db.query(ChannelPool.keyword_id)
                .filter(ChannelPool.scoring_run_id == scoring_run_id)
                .filter(ChannelPool.channel == 'SEO')
                .all()
            )
        ]
        if not seo_pool_ids:
            return {"requested": 0, "updated": 0, "unavailable": 0, "ai_calls_used": 0}

        self._update_assignment_progress(task_id, 96, "SEO metadata üretiliyor.")
        try:
            return SeoPreFilter(self.db, self.ai_service).generate_metadata(
                scoring_run_id, seo_pool_ids
            )
        except Exception as exc:
            # Metadata SECIMDEN SONRA kosar: hata run'i dusurmez. Ancak
            # butce tukenmesi SESSIZ kalmaz — sebep acikca etiketlenir
            # (Codex is sirasi #1: butce hatasi generic hata gibi
            # raporlanamaz).
            from app.core.telemetry.ai_cost_budget import BudgetError as _BE

            is_budget = isinstance(exc, _BE)
            logger.error(
                f"SEO metadata adımı hata (run devam ediyor"
                f"{', BÜTÇE' if is_budget else ''}): {exc}"
            )
            self.db.rollback()
            return {
                "error": str(exc)[:200],
                "reason_code": ("BUDGET_EXCEEDED" if is_budget
                                else "METADATA_ERROR"),
                "requested": len(seo_pool_ids),
                "updated": 0,
                "unavailable": len(seo_pool_ids),
                "ai_calls_used": 0,
            }

    def _run_brand_filter(
        self,
        scoring_run_id: int,
        task_id: str | None = None,
    ) -> Dict[str, Any]:
        """Run confirmed-profile exclude_themes filter once before channel pre-filters."""
        progress_callback = self._make_batch_progress_callback(
            task_id,
            start=50,
            end=60,
            total_batches=self._brand_filter_batch_count(scoring_run_id),
            label="Marka dışlama",
        )
        brand_filter = BrandExclusionFilter(self.db, self.ai_service)
        result = brand_filter.filter_intent_passed(
            scoring_run_id,
            progress_callback=progress_callback,
        )
        # Politika özgüllük katmanı — YALNIZ SHADOW (karşı-olgusal sütun).
        # brand_filter.py mühürlü deney artifact'larında SHA ile pinli olduğu
        # için zenginleştirme filtre DÖNÜŞÜ üzerinde, burada yapılır.
        result = self._apply_specificity_shadow(scoring_run_id, result)
        logger.info("Brand exclusion filter result for run %s: %s", scoring_run_id, result)
        return result

    def _apply_specificity_shadow(
        self,
        scoring_run_id: int,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Karşı-olgusal özgüllük sütunları; uygulanan karara DOKUNMAZ."""
        from app.config import settings
        from app.core.policy.specificity import apply_shadow

        try:
            from app.database.models import BrandProfile

            run = self.db.query(ScoringRun).filter(
                ScoringRun.id == scoring_run_id).first()
            profile = (
                self.db.query(BrandProfile)
                .filter(BrandProfile.id == run.brand_profile_id).first()
                if run and run.brand_profile_id else None
            )
            profile_data = (profile.profile_data
                            if profile and isinstance(profile.profile_data, dict)
                            else {})
            return apply_shadow(
                result, profile_data,
                flag_enabled=bool(getattr(
                    settings, "ENABLE_POLICY_SPECIFICITY_RESOLVER", False)),
            )
        except Exception as exc:                      # telemetri hattı
            # Shadow katmanı ÜRETİMİ DÜŞÜREMEZ: hata yalnız loglanır ve
            # baseline sonuç aynen döner.
            logger.warning(
                "Specificity shadow skipped for run %s: %s", scoring_run_id, exc)
            return result

    def _run_pre_filters(
        self,
        scoring_run_id: int,
        task_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        Tum kanallar icin AI pre-filter calistirir.
        Faz A (ADS/SEO/SOCIAL) paralel; Faz B (cross-channel) sequential.
        """
        FILTER_CLS = {'ADS': AdsPreFilter, 'SEO': SeoPreFilter, 'SOCIAL': SocialPreFilter}
        progress_callback = self._make_batch_progress_callback(
            task_id,
            start=60,
            end=80,
            total_batches=self._prefilter_batch_count(scoring_run_id),
            label="Kanal prefilter",
        )

        def _run_filter(channel: str):
            db = SessionLocal()
            try:
                pf = FILTER_CLS[channel](db, self.ai_service)
                result = pf.filter_candidates(
                    scoring_run_id,
                    progress_callback=progress_callback,
                )
                db.commit()
                return channel, result
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        active_channels = self._get_active_channels(self._scoring_run)

        # Faz A: paralel (sadece aktif kanallar için)
        results = {}
        with ThreadPoolExecutor(max_workers=len(active_channels) or 1) as executor:
            futures = {executor.submit(_run_filter, ch): ch for ch in active_channels}
            for future in as_completed(futures):
                channel, result = future.result()
                results[channel] = result

        # Faz B: Cross-channel transfer (ADS -> SEO) — sadece iki kanal da aktifse
        if self._scoring_run.enable_ads and self._scoring_run.enable_seo:
            seo_pf = SeoPreFilter(self.db, self.ai_service)
            results['cross_channel'] = self._process_cross_channel_transfers(
                scoring_run_id, seo_pf, progress_callback=progress_callback)
        else:
            logger.info("Cross-channel transfer atlandı: ADS ve SEO aynı anda aktif değil")
            results['cross_channel'] = {"transferred": 0, "seo_kept": 0, "seo_eliminated": 0}

        channel_summary = ", ".join(
            f"{ch}={results.get(ch, 'N/A')}"
            for ch in active_channels
        )
        logger.info(
            f"Pre-filter tamamlandi: {channel_summary}, "
            f"Cross-channel={results.get('cross_channel', {})}"
        )

        return results

    def _process_cross_channel_transfers(
        self,
        scoring_run_id: int,
        seo_pf,
        progress_callback: Callable[[str, int], None] | None = None,
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

        transfer_keyword_ids = [
            tc.keyword_id
            for tc in transfer_candidates
            if (tc.extra_data or {}).get("reason_code") != BRAND_EXCLUDED_REASON
        ]
        if not transfer_keyword_ids:
            logger.info("Cross-channel: marka dışlama filtresinden sonra transfer edilecek keyword yok")
            return {"transferred": 0, "seo_kept": 0, "seo_eliminated": 0}

        logger.info(
            f"Cross-channel: {len(transfer_keyword_ids)} keyword "
            f"ADSâ†'SEO transferi baÅŸlatÄ±lÄ±yor"
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
        created_ids: List[int] = []
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
            seo_score = float(kw_score.seo_score) if kw_score and kw_score.seo_score is not None else 0.0
            seo_rank = kw_score.seo_rank if kw_score and kw_score.seo_rank is not None else next_rank

            # Synthetic ChannelCandidate (SEO score ile) — intent SENTETİK
            # DEĞİL: transferler gerçek GT/GA analizi alır (aşağıda).
            # Transfer adayinin kaynagi: kelimeyi ADS havuzuna sokan
            # kaynak (baseline/screening/both). Assistive uygulanmadiysa
            # alanlar NULL kalir (bugunku davranis).
            context = getattr(self, "_applied_context", None)
            origin = (context.origin_for('ADS', kw_id)
                      if context is not None else None)
            self.db.add(ChannelCandidate(
                scoring_run_id=scoring_run_id,
                keyword_id=kw_id,
                channel='SEO',
                raw_score=seo_score,
                rank_in_channel=seo_rank,
                screening_job_id=(context.screening_job_id
                                  if context is not None else None),
                candidate_origin_source=origin,
                candidate_materialization_action=(
                    ACTION_TRANSFER if context is not None else None),
            ))
            if context is not None:
                audit_later_action(
                    self.db, context, scoring_run_id=scoring_run_id,
                    channel='SEO', keyword_id=kw_id, origin=origin,
                    action=ACTION_TRANSFER, rank=seo_rank,
                    adjusted=seo_score)

            next_rank += 1
            created_count += 1
            created_ids.append(kw_id)

        # Codex 26b #1: transfer de expansion gibi COMMIT ONCESI birebirlik
        # kapisindan gecer. `ON CONFLICT DO NOTHING` cakisan/eski audit
        # satirinda yazimi yutabilir; kapi olmadan aday commit edilir ve
        # audit ile canli havuz ayrisirdi. Ihlalde aday VE audit birlikte
        # geri alinir (fail-closed).
        transfer_context = getattr(self, "_applied_context", None)
        if transfer_context is not None and created_ids:
            from app.core.screening.assistive import AssistiveAuditConflict

            try:
                self.db.flush()
                self.pool_builder._verify_audit_matches_pool(
                    transfer_context, scoring_run_id)
            except AssistiveAuditConflict:
                self.db.rollback()
                logger.error(
                    f"transfer audit ayristi (run {scoring_run_id}) — "
                    f"adaylar ve audit birlikte geri alindi")
                raise
        self.db.commit()

        # Gerçek GT/GA analizi + SEO prefilter — YALNIZ created_ids üzerinde:
        # zaten SEO adayı olan kelimelerin mevcut Intent/PreFilterResult
        # kayıtları overwrite EDİLMEZ (run-15 dersi). AI hatası alan
        # transferler source='fallback' + gt/ga NULL ile yine geçer
        # (SEO intent elemez).
        # Rakip bloğu transferlere de uygulanır (plan A): politika SEO'da
        # block ise rakip transfer adayı intent'e girmeden terminal olur
        competitor_blocked_created = 0
        if created_ids:
            from app.core.channel.competitor_filter import apply_competitor_block
            transfer_blocked = apply_competitor_block(
                self.db, scoring_run_id, channels=['SEO'], keyword_ids=created_ids
            )
            competitor_blocked_created = transfer_blocked.get('SEO', 0)

        intent_ai_count = 0
        intent_fallback_count = 0
        if created_ids:
            self.intent_analyzer.analyze_candidates(
                scoring_run_id,
                'SEO',
                keyword_ids=created_ids,
                source=INTENT_SOURCE_TRANSFER_AI,
                progress_callback=progress_callback,
            )
            intent_ai_count = (
                self.db.query(IntentAnalysis)
                .filter(
                    IntentAnalysis.scoring_run_id == scoring_run_id,
                    IntentAnalysis.channel == 'SEO',
                    IntentAnalysis.keyword_id.in_(created_ids),
                    IntentAnalysis.source == INTENT_SOURCE_TRANSFER_AI,
                )
                .count()
            )
            intent_fallback_count = (
                self.db.query(IntentAnalysis)
                .filter(
                    IntentAnalysis.scoring_run_id == scoring_run_id,
                    IntentAnalysis.channel == 'SEO',
                    IntentAnalysis.keyword_id.in_(created_ids),
                    IntentAnalysis.source == INTENT_SOURCE_FALLBACK,
                )
                .count()
            )
            # SEO prefilter artık deterministik (fiyat filtresi + keep) —
            # transferlerin parse hatasıyla karantinası yapısal olarak bitti;
            # fiyat filtresi transferlere de uygulanır.
            seo_result = seo_pf.filter_candidates(
                scoring_run_id,
                keyword_ids=created_ids,
                progress_callback=progress_callback,
            )
        else:
            seo_result = {"kept": 0, "eliminated": 0, "fallback": 0, "total": 0}

        logger.info(
            f"Cross-channel tamamlandi: {created_count} transfer, "
            f"intent_ai={intent_ai_count}, intent_fallback={intent_fallback_count}, "
            f"SEO sonuc={seo_result}"
        )

        return {
            "requested": len(transfer_keyword_ids),
            "transferred": created_count,
            "created": created_count,
            "already_present": len(transfer_keyword_ids) - created_count,
            "competitor_blocked_created": competitor_blocked_created,
            "intent_ai_created": intent_ai_count,
            "intent_fallback_created": intent_fallback_count,
            # SEO prefilter deterministik: eliminated == fiyat filtresi
            "price_blocked_created": int(seo_result.get("eliminated", 0)),
            "seo_kept": seo_result.get("kept", 0),
            "seo_eliminated": seo_result.get("eliminated", 0),
        }

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # AdÄ±m 3: Final HavuzlarÄ± (v2 â€" intent + prefilter + backfill)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    # ═══════════════════════════════════════════════════════════
    # Faz G: selection_quality gözlemlenebilirliği
    # ═══════════════════════════════════════════════════════════

    def _collect_selection_quality(self, scoring_run_id: int, results: Dict) -> Dict:
        """Run-15 karşılaştırmasının ölçüm aleti (plan Faz G).

        Kanal başına seçim kalitesi sayaçları + paydası AYRI transfer bloğu +
        expansion özeti. Hata run'ı fail ETMEZ — {'error': ...} döner.
        """
        try:
            from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON
            from app.core.constants import (
                COMPETITOR_TERM_REASON,
                INTENT_SOURCE_FALLBACK,
                SEO_METADATA_UNAVAILABLE_REASON,
            )

            final_counts = results.get('steps', {}).get('final_pools', {}) or {}
            expansion = results.get('steps', {}).get('expansion_rounds', {}) or {}

            run = self.db.query(ScoringRun).filter(
                ScoringRun.id == scoring_run_id
            ).first()
            algorithm_version = (
                getattr(run, "algorithm_version", "v2") or "v2" if run else "v2"
            )

            def _reason_count(channel: str, reason: str) -> int:
                return (
                    self.db.query(PreFilterResult)
                    .filter(
                        PreFilterResult.scoring_run_id == scoring_run_id,
                        PreFilterResult.channel == channel,
                        PreFilterResult.extra_data['reason_code'].as_string() == reason,
                    )
                    .count()
                )

            channels: Dict[str, Dict] = {}
            for channel, final_count in final_counts.items():
                capacity = (expansion.get(channel) or {}).get('capacity')
                entry: Dict = {
                    'capacity': capacity,
                    'final': final_count,
                    'unfilled': (
                        max(int(capacity) - int(final_count), 0)
                        if capacity is not None else None
                    ),
                    'intent_fallback': (
                        self.db.query(IntentAnalysis)
                        .filter(
                            IntentAnalysis.scoring_run_id == scoring_run_id,
                            IntentAnalysis.channel == channel,
                            IntentAnalysis.source == INTENT_SOURCE_FALLBACK,
                        )
                        .count()
                    ),
                    'prefilter_fallback': (
                        self.db.query(PreFilterResult)
                        .filter(
                            PreFilterResult.scoring_run_id == scoring_run_id,
                            PreFilterResult.channel == channel,
                            PreFilterResult.is_fallback.is_(True),
                        )
                        .count()
                    ),
                    'prefilter_eliminated': (
                        self.db.query(PreFilterResult)
                        .filter(
                            PreFilterResult.scoring_run_id == scoring_run_id,
                            PreFilterResult.channel == channel,
                            PreFilterResult.is_kept.is_(False),
                        )
                        .count()
                    ),
                    'brand_excluded': _reason_count(channel, BRAND_EXCLUDED_REASON),
                    'competitor_blocked': _reason_count(channel, COMPETITOR_TERM_REASON),
                }

                def _pool_intent_null_count(*columns) -> int:
                    from sqlalchemy import or_

                    return (
                        self.db.query(ChannelPool)
                        .join(
                            IntentAnalysis,
                            (IntentAnalysis.keyword_id == ChannelPool.keyword_id)
                            & (IntentAnalysis.scoring_run_id == scoring_run_id)
                            & (IntentAnalysis.channel == 'SEO'),
                        )
                        .filter(
                            ChannelPool.scoring_run_id == scoring_run_id,
                            ChannelPool.channel == 'SEO',
                            or_(*[col.is_(None) for col in columns]),
                        )
                        .count()
                    )

                def _pool_class_count(channel_name: str, ai_class) -> int:
                    query = (
                        self.db.query(ChannelPool)
                        .join(
                            PreFilterResult,
                            (PreFilterResult.keyword_id == ChannelPool.keyword_id)
                            & (PreFilterResult.scoring_run_id == scoring_run_id)
                            & (PreFilterResult.channel == channel_name),
                        )
                        .filter(
                            ChannelPool.scoring_run_id == scoring_run_id,
                            ChannelPool.channel == channel_name,
                        )
                    )
                    if ai_class is None:
                        query = query.filter(PreFilterResult.ai_class.is_(None))
                    else:
                        query = query.filter(
                            PreFilterResult.ai_class == ai_class
                        )
                    return query.count()

                if channel == 'SEO':
                    entry['price_blocked'] = _reason_count('SEO', 'PRICE_TERM')
                    entry['metadata_unavailable'] = _reason_count(
                        'SEO', SEO_METADATA_UNAVAILABLE_REASON
                    )
                    if algorithm_version == 'v2_1':
                        # Plan Faz E: v2_1 seçim skoru strategy_fit/ga okur —
                        # NULL sayıları AYRI raporlanır (fallback görünürlüğü)
                        entry['strategy_fit_null_in_pool'] = (
                            _pool_intent_null_count(IntentAnalysis.strategy_fit)
                        )
                        entry['ga_null_in_pool'] = (
                            _pool_intent_null_count(IntentAnalysis.ga)
                        )
                    else:
                        entry['gt_ga_null_in_pool'] = _pool_intent_null_count(
                            IntentAnalysis.gt, IntentAnalysis.ga
                        )

                if channel == 'ADS':
                    for label, ai_class in (('hot_sale', 2), ('lead', 1)):
                        entry[label] = _pool_class_count('ADS', ai_class)
                    # Plan Faz E: tam sınıf dağılımı (havuzdaki satırlar)
                    entry['class_counts'] = {
                        str(c): _pool_class_count('ADS', c)
                        for c in (2, 1, 0, None)
                    }

                if channel == 'SOCIAL':
                    # Plan Faz E: SOCIAL sınıf dağılımı (0-3)
                    entry['class_counts'] = {
                        str(c): _pool_class_count('SOCIAL', c)
                        for c in (3, 2, 1, 0, None)
                    }
                    # Codex Faz E #1: ÜÇ boyutun ayrı dağılımı — payda AÇIK
                    # (total): 'pool' final havuz satırları, 'evaluated' bu
                    # run'da değerlendirilen TÜM SOCIAL prefilter satırları.
                    # Faz F'de SOCIAL farkının hangi boyuttan geldiği buradan
                    # okunur.
                    dim_keys = (
                        'opinion_discussion', 'curiosity_comparison',
                        'agenda_theme',
                    )

                    def _dim_counts(rows) -> Dict:
                        counts = {d: 0 for d in dim_keys}
                        total = 0
                        for (extra,) in rows:
                            total += 1
                            dims = (extra or {}).get('dims') or {}
                            for d in dim_keys:
                                if dims.get(d) == 1:
                                    counts[d] += 1
                        return {'total': total, **counts}

                    evaluated_rows = (
                        self.db.query(PreFilterResult.extra_data)
                        .filter(
                            PreFilterResult.scoring_run_id == scoring_run_id,
                            PreFilterResult.channel == 'SOCIAL',
                        )
                        .all()
                    )
                    pool_rows = (
                        self.db.query(PreFilterResult.extra_data)
                        .join(
                            ChannelPool,
                            (ChannelPool.keyword_id == PreFilterResult.keyword_id)
                            & (ChannelPool.scoring_run_id == scoring_run_id)
                            & (ChannelPool.channel == 'SOCIAL'),
                        )
                        .filter(
                            PreFilterResult.scoring_run_id == scoring_run_id,
                            PreFilterResult.channel == 'SOCIAL',
                        )
                        .all()
                    )
                    entry['dimension_counts'] = {
                        'pool': _dim_counts(pool_rows),
                        'evaluated': _dim_counts(evaluated_rows),
                    }

                channels[channel] = entry

            # Plan Faz E: relevance'ın final kesmeye etkisi — aynı adaylar
            # nötr relevance (sabit 0.5) ile yeniden sıralanınca havuza
            # giren/çıkan sayısı. (Patron-pozitif erişim raporu runtime'da
            # bilinemez — o ölçüm benchmark aracındadır: v21_numeric.)
            relevance_effect = None
            if run is not None:
                try:
                    from app.core.site_analyzer.drift_metrics import (
                        simulate_final_pools,
                    )

                    actual = simulate_final_pools(
                        self.db, run,
                        self._load_relevance_map_for_pooling(scoring_run_id),
                    )
                    neutral = simulate_final_pools(self.db, run, {})
                    relevance_effect = {}
                    for ch in actual:
                        a_ids = {r['kid'] for r in actual.get(ch, [])}
                        n_ids = {r['kid'] for r in neutral.get(ch, [])}
                        relevance_effect[ch] = {
                            'moved_in': len(a_ids - n_ids),
                            'moved_out': len(n_ids - a_ids),
                        }
                except Exception as exc:
                    logger.warning(f"relevance_effect hesaplanamadı: {exc}")

            return {
                'algorithm_version': algorithm_version,
                'relevance_effect': relevance_effect,
                'channels': channels,
                # Paydalar AYRI (run-15 sayı karmaşasının dersi):
                # requested/created/already_present/intent_ai_created/
                # intent_fallback_created/price_blocked_created
                'transfers': (
                    results.get('steps', {})
                    .get('pre_filtering', {})
                    .get('cross_channel', {})
                ),
                'expansion': expansion,
                # Final havuz kaynak kirilimi (Codex 26. tur #2): hangi
                # aday hangi asamadan ve hangi kaynaktan geldi
                'candidate_sources': self._candidate_source_breakdown(
                    scoring_run_id),
            }
        except Exception as e:  # gözlemlenebilirlik run'ı asla düşürmez
            logger.error(f"selection_quality toplanamadı: {e}")
            return {'error': str(e)}

    def _candidate_source_breakdown(self, scoring_run_id: int) -> Dict:
        """Kanal basina canli adaylarin kaynak/asama dagilimi.

        `origin`: baseline / screening / both (tarama uygulanmadiysa
        'unattributed'), `action`: initial / transfer / expansion.
        FINAL havuz (`ChannelPool` satirlari) icin de ayni kirilim verilir —
        "tarama sonucu final secime ne kattinsa" sorusunun olcum aleti.
        """
        from sqlalchemy import func as sa_func

        out: Dict[str, Dict] = {}
        rows = (
            self.db.query(
                ChannelCandidate.channel,
                ChannelCandidate.candidate_origin_source,
                ChannelCandidate.candidate_materialization_action,
                sa_func.count(ChannelCandidate.id))
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .group_by(ChannelCandidate.channel,
                      ChannelCandidate.candidate_origin_source,
                      ChannelCandidate.candidate_materialization_action)
            .all())
        for channel, origin, action, count in rows:
            entry = out.setdefault(channel, {"candidates": {}, "final": {}})
            # NULL action 'initial' VARSAYILMAZ (Codex 26b #2): baseline
            # kosuda ve kapsam disi kanalda expansion/transfer adaylari da
            # NULL tasir; onlari 'initial' saymak yanlis kirilim uretirdi
            key = f"{origin or 'unattributed'}:{action or 'unknown'}"
            entry["candidates"][key] = entry["candidates"].get(key, 0) + count

        final_rows = (
            self.db.query(
                ChannelPool.channel,
                ChannelCandidate.candidate_origin_source,
                ChannelCandidate.candidate_materialization_action,
                sa_func.count(ChannelPool.id))
            .join(ChannelCandidate,
                  and_(ChannelCandidate.scoring_run_id
                       == ChannelPool.scoring_run_id,
                       ChannelCandidate.keyword_id == ChannelPool.keyword_id,
                       ChannelCandidate.channel == ChannelPool.channel))
            .filter(ChannelPool.scoring_run_id == scoring_run_id)
            .group_by(ChannelPool.channel,
                      ChannelCandidate.candidate_origin_source,
                      ChannelCandidate.candidate_materialization_action)
            .all())
        for channel, origin, action, count in final_rows:
            entry = out.setdefault(channel, {"candidates": {}, "final": {}})
            key = f"{origin or 'unattributed'}:{action or 'unknown'}"
            entry["final"][key] = entry["final"].get(key, 0) + count
        return out

    def _load_relevance_map_for_pooling(self, scoring_run_id: int) -> Dict[int, float]:
        """Run icindeki keyword relevance skorlarini keyword_id bazinda doner.

        Sadakat plani P1.4: bu metod ONCEDEN KAPISIZDI — bayrak kapali,
        skip_relevance=true, profil draft/arsivli veya anchor surumu bayat
        oldugunda bile eski relevance degerlerini FINAL SECIME tasiyordu
        (`adjusted = max(base,0) * relevance * coef`). Artik `pool_builder`
        ile AYNI kapiyi kullanir; kapi kapaliysa bos map doner ve siralama
        notr relevance (0.5) ile kurulur.
        """
        from app.core.relevance import load_effective_relevance_map

        return load_effective_relevance_map(self.db, scoring_run_id)

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

    def _load_snapshot_map(self, scoring_run_id: int) -> Dict[int, Dict[str, Any]]:
        """KeywordScore.metrics_snapshot -> keyword_id haritası.

        Final seçimde hacim (tie-break) ve H (Yükselen Fırsat) buradan okunur.
        Eski run'larda 'derived' anahtarı olmayabilir — tüketici guard'lıdır.
        """
        rows = (
            self.db.query(KeywordScore.keyword_id, KeywordScore.metrics_snapshot)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )
        return {keyword_id: (snapshot or {}) for keyword_id, snapshot in rows}

    def _capacity_map(self, scoring_run: ScoringRun) -> Dict[str, int]:
        return {
            'ADS': int(scoring_run.ads_capacity or ADS_FINAL_CAPACITY),
            'SEO': int(scoring_run.seo_capacity or SEO_FINAL_CAPACITY),
            'SOCIAL': int(scoring_run.social_capacity or SOCIAL_FINAL_CAPACITY),
        }

    def _expansion_cap_map(self) -> Dict[str, int]:
        return {
            'ADS': ADS_MAX_EXPANSION_POOL_SIZE,
            'SEO': SEO_MAX_EXPANSION_POOL_SIZE,
            'SOCIAL': SOCIAL_MAX_EXPANSION_POOL_SIZE,
        }

    def _count_brand_excluded(self, scoring_run_id: int, channel: str) -> int:
        return (
            self.db.query(PreFilterResult)
            .filter(PreFilterResult.scoring_run_id == scoring_run_id)
            .filter(PreFilterResult.channel == channel)
            .filter(PreFilterResult.extra_data["reason_code"].as_string() == BRAND_EXCLUDED_REASON)
            .count()
        )

    def _prefilter_summary_for_channel(self, scoring_run_id: int, channel: str) -> Dict[str, int]:
        rows = (
            self.db.query(PreFilterResult)
            .filter(PreFilterResult.scoring_run_id == scoring_run_id)
            .filter(PreFilterResult.channel == channel)
            .all()
        )
        kept = sum(1 for row in rows if row.is_kept)
        return {
            "kept": kept,
            "eliminated": len(rows) - kept,
            "brand_excluded": sum(
                1
                for row in rows
                if (row.extra_data or {}).get("reason_code") == BRAND_EXCLUDED_REASON
            ),
        }

    def _candidate_count(self, scoring_run_id: int, channel: str) -> int:
        return (
            self.db.query(ChannelCandidate)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .count()
        )

    def _estimate_expansion_batches(self, window_size: int, channel: str) -> int:
        """Pencere BOYUTU seçimi için kaba tahmin (sınırı HARD bütçe korur).

        Kanal-farkında: SEO prefilter deterministik (0 AI çağrısı); brand
        filter /5'lik batch kullanır (eski kod /8 sayıyordu — düzeltildi).
        """
        if window_size <= 0:
            return 0
        estimate = (
            math.ceil(window_size / INTENT_BATCH_SIZE)
            + math.ceil(window_size / BRAND_FILTER_BATCH_SIZE)
        )
        if channel != 'SEO':
            estimate += math.ceil(window_size / PREFILTER_BATCH_SIZE)
        return estimate

    def _window_for_budget(self, desired: int, remaining_calls: int, channel: str) -> int:
        if desired <= 0 or remaining_calls <= 0:
            return 0
        size = desired
        while size > 0 and self._estimate_expansion_batches(size, channel) > remaining_calls:
            size -= 1
        return size

    def _run_expansion_rounds(
        self,
        scoring_run_id: int,
        scoring_run: ScoringRun,
        initial_final_counts: Dict[str, int],
        *,
        relevance_coefficient: float,
        task_id: str | None = None,
    ) -> Dict[str, Dict[str, Any]]:
        active_channels = self._get_active_channels(scoring_run)
        capacity_map = self._capacity_map(scoring_run)
        expansion_cap_map = self._expansion_cap_map()
        summaries: Dict[str, Dict[str, Any]] = {}
        # Kapasite-odaklı: %70 eşiği KALDIRILDI — dolmayan her kanal dener.
        # Durma koşulları: kapasite / aday üst sınırı / HARD bütçe (60) /
        # max 2 tur / aday kalmadı.
        expanding_channels = [
            ch
            for ch in active_channels
            if initial_final_counts.get(ch, 0) < capacity_map[ch]
        ]
        per_channel_budget = (
            max(1, MAX_EXPANSION_AI_BATCHES // len(expanding_channels))
            if expanding_channels
            else 0
        )

        for channel in ['ADS', 'SEO', 'SOCIAL']:
            capacity = capacity_map.get(channel, 0)
            initial_candidates = self._candidate_count(scoring_run_id, channel)
            pf = self._prefilter_summary_for_channel(scoring_run_id, channel)
            before = initial_final_counts.get(channel, 0)
            summaries[channel] = {
                "capacity": capacity,
                "initial_candidates": initial_candidates,
                "final_count_before_expansion": before,
                "final_count_after_expansion": before,
                "unfilled_count": max(capacity - before, 0),
                "brand_excluded": pf["brand_excluded"],
                "prefilter_kept": pf["kept"],
                "prefilter_eliminated": pf["eliminated"],
                "rounds_run": 0,
                "total_candidates_examined": initial_candidates,
                "expansion_ai_batches_used": 0,
                "expansion_ai_batch_budget": per_channel_budget if channel in expanding_channels else 0,
                "candidate_window_start": None,
                "candidate_window_end": None,
                "stop_reason": (
                    STOP_CHANNEL_DISABLED
                    if channel not in active_channels
                    else STOP_CAPACITY_REACHED
                    if before >= capacity
                    else STOP_MAX_ROUNDS_REACHED
                ),
            }

        FILTER_CLS = {'ADS': AdsPreFilter, 'SEO': SeoPreFilter, 'SOCIAL': SocialPreFilter}

        for channel in expanding_channels:
            summary = summaries[channel]
            capacity = capacity_map[channel]
            # HARD bütçe: her complete_json çağrısından ÖNCE try_consume()
            # (katmanların içinde) — tahmin yalnız pencere boyutu seçer,
            # sınırı bu nesne korur. Kanal başına per_channel_budget;
            # toplam <= MAX_EXPANSION_AI_BATCHES garanti.
            budget = AiCallBudget(per_channel_budget)

            for round_no in range(1, MAX_EXPANSION_ROUNDS + 1):
                current_count = self._build_final_pools_v2(
                    scoring_run_id,
                    scoring_run,
                    relevance_coefficient=relevance_coefficient,
                ).get(channel, 0)
                summary["final_count_after_expansion"] = current_count
                summary["unfilled_count"] = max(capacity - current_count, 0)
                if current_count >= capacity:
                    summary["stop_reason"] = STOP_CAPACITY_REACHED
                    break
                if budget.exhausted:
                    summary["stop_reason"] = STOP_BUDGET_REACHED
                    break

                current_candidates = self._candidate_count(scoring_run_id, channel)
                pf = self._prefilter_summary_for_channel(scoring_run_id, channel)
                survival_rate = max(pf["kept"] / max(current_candidates, 1), 0.05)
                needed = capacity - current_count
                desired_window = math.ceil((needed / survival_rate) * 1.3)
                remaining_cap = expansion_cap_map[channel] - current_candidates
                desired_window = max(0, min(desired_window, remaining_cap))
                window_size = self._window_for_budget(desired_window, budget.remaining, channel)

                if window_size <= 0:
                    summary["stop_reason"] = (
                        STOP_BUDGET_REACHED if desired_window > 0 else STOP_NO_MORE_CANDIDATES
                    )
                    break

                self._update_assignment_progress(
                    task_id,
                    80 + min(12, round_no * 4),
                    f"{channel} için ek aday turu {round_no}/{MAX_EXPANSION_ROUNDS} çalışıyor.",
                    expansion_rounds=summaries,
                )

                added_info = self.pool_builder.add_expansion_candidates(
                    scoring_run_id,
                    channel,
                    limit=window_size,
                    max_total_candidates=expansion_cap_map[channel],
                    relevance_coefficient=relevance_coefficient,
                    applied_context=getattr(self, "_applied_context", None),
                )
                added = added_info["added"]
                if added <= 0:
                    summary["stop_reason"] = STOP_NO_MORE_CANDIDATES
                    break

                summary["rounds_run"] += 1
                summary["candidate_window_start"] = added_info["window_start"]
                summary["candidate_window_end"] = added_info["window_end"]
                summary["total_candidates_examined"] += added

                # Rakip bloğu expansion penceresine de uygulanır (plan A —
                # pencere adayları intent'e girmeden terminal işaretlenir)
                from app.core.channel.competitor_filter import apply_competitor_block
                window_blocked = apply_competitor_block(
                    self.db, scoring_run_id, channels=[channel]
                )
                summary["competitor_blocked"] = (
                    summary.get("competitor_blocked", 0)
                    + window_blocked.get(channel, 0)
                )

                keyword_ids = [
                    row.keyword_id
                    for row in (
                        self.db.query(ChannelCandidate.keyword_id)
                        .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
                        .filter(ChannelCandidate.channel == channel)
                        .filter(ChannelCandidate.rank_in_channel >= added_info["window_start"])
                        .filter(ChannelCandidate.rank_in_channel <= added_info["window_end"])
                        .all()
                    )
                ]

                intent_result = self.intent_analyzer.analyze_candidates(
                    scoring_run_id,
                    channel,
                    keyword_ids=keyword_ids,
                    budget=budget,
                )
                brand_result = BrandExclusionFilter(self.db, self.ai_service).filter_intent_passed(
                    scoring_run_id,
                    keyword_ids=keyword_ids,
                    channels=[channel],
                    budget=budget,
                )
                prefilter_result = FILTER_CLS[channel](self.db, self.ai_service).filter_candidates(
                    scoring_run_id,
                    keyword_ids=keyword_ids,
                    budget=budget,
                )
                # Bütçe muhasebesi GERÇEK çağrı sayısına dayanır
                # (ilk çağrılar + parse retry + eksik-subset retry + tekil retry)
                used = (
                    int(intent_result.get("ai_calls_used", 0))
                    + int(brand_result.get("ai_calls_used", 0))
                    + int(prefilter_result.get("ai_calls_used", 0))
                )
                summary["expansion_ai_batches_used"] += used
                pf_after = self._prefilter_summary_for_channel(scoring_run_id, channel)
                summary["brand_excluded"] = pf_after["brand_excluded"]
                summary["prefilter_kept"] = pf_after["kept"]
                summary["prefilter_eliminated"] = pf_after["eliminated"]
                summary["stop_reason"] = STOP_MAX_ROUNDS_REACHED

        self._update_assignment_progress(
            task_id,
            95,
            "Ek aday turları tamamlandı.",
            expansion_rounds=summaries,
        )
        return summaries

    def _update_assignment_progress(
        self,
        task_id: str | None,
        progress: int,
        message: str,
        *,
        expansion_rounds: Dict[str, Any] | None = None,
    ) -> None:
        if not task_id:
            return
        try:
            from app.tasks.task_status import update_task_status

            update_task_status(
                task_id,
                status="running",
                progress=progress,
                result_data={
                    "current_message": message,
                    "steps": {"expansion_rounds": expansion_rounds or {}},
                },
            )
        except Exception as exc:
            logger.warning("Could not update channel assignment progress: %s", exc)

    def _build_final_pools_v2(
        self,
        scoring_run_id: int,
        scoring_run: ScoringRun,
        relevance_coefficient: float = 1.0
    ) -> Dict[str, int]:
        """
        Intent + Pre-filter gecen satirlardan SINIF-ONCELIKLI final havuz kurar
        (Skorlama v2 Bolum 7): sort key (-sinif, -adjusted, -hacim, keyword).

        - SEO'da sinif ekseni yok; onun yerine secim skoru
          N_SEO + SEO_W_GT*gt + SEO_W_GA*ga ile hesaplanir (intent elemez;
          PRICE_TERM satirlari is_kept=False oldugundan join'de otomatik dislanir).
        - BACKFILL YOK: elenen (sinif -1) kelime listeye giremez; kapasite
          doldurma expansion turlarinin isidir.
        - Cift kanal etiketleri: ADS ∩ SEO -> is_strategic; SOCIAL ust dilim
          + yuksek H -> pool_label='rising_opportunity'.
        """
        final_counts = {}
        self.db.query(ChannelPool).filter(ChannelPool.scoring_run_id == scoring_run_id).delete()
        self.db.commit()
        # v2.1 (plan Faz E): SEO seçim skoru N_SEO_core + 15*strategy_fit +
        # 4*ga (S_G, doc §5). Sürüm DB'deki immutable run alanından okunur.
        algorithm_version = (
            getattr(scoring_run, "algorithm_version", "v2") or "v2"
        )
        relevance_map = self._load_relevance_map_for_pooling(scoring_run_id)
        channel_score_maps = self._load_channel_score_maps(scoring_run_id)
        snapshot_map = self._load_snapshot_map(scoring_run_id)

        active_channels = self._get_active_channels(scoring_run)
        capacity_map = {
            'ADS': scoring_run.ads_capacity or ADS_FINAL_CAPACITY,
            'SEO': scoring_run.seo_capacity or SEO_FINAL_CAPACITY,
            'SOCIAL': scoring_run.social_capacity or SOCIAL_FINAL_CAPACITY,
        }
        selected_ids_by_channel: Dict[str, set] = {}

        for channel in active_channels:
            capacity = capacity_map[channel]
            passed_rows = (
                self.db.query(IntentAnalysis, ChannelCandidate, PreFilterResult, Keyword.keyword)
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
                .join(Keyword, Keyword.id == IntentAnalysis.keyword_id)
                .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
                .filter(IntentAnalysis.channel == channel)
                .filter(IntentAnalysis.is_passed == True)
                .filter(PreFilterResult.is_kept == True)
                .all()
            )

            scored_rows = []
            for intent, candidate, pf_result, keyword_text in passed_rows:
                kid = candidate.keyword_id
                base = channel_score_maps.get(channel, {}).get(kid, 0.0)
                if channel == 'SEO':
                    # G_T/G_A (v2) veya S_G/G_A (v2_1) dereceleri SECIM aninda
                    # eklenir (KeywordScore'daki ham N_SEO tabani degismez —
                    # YZ skoru mutasyona ugratmaz). NULL -> 0 sayilir.
                    if algorithm_version == "v2_1":
                        base += (SEO_W_GT * int(bool(intent.strategy_fit))
                                 + SEO_W_GA * int(bool(intent.ga)))
                    else:
                        base += (SEO_W_GT * int(bool(intent.gt))
                                 + SEO_W_GA * int(bool(intent.ga)))
                # Relevance yoksa konservatif 0.5; max(base,0) negatif skor
                # x relevance ters cevirme korumasi
                relevance = relevance_map.get(kid, 0.5)
                adjusted = max(base, 0.0) * relevance * relevance_coefficient

                if channel == 'SEO':
                    cls = 0  # SEO'da sinif ekseni yok
                elif pf_result.ai_class is not None:
                    cls = pf_result.ai_class
                else:
                    cls = DEFAULT_AI_CLASS_WHEN_MISSING

                snapshot = snapshot_map.get(kid) or {}
                derived = snapshot.get("derived") or {}
                try:
                    volume = int(snapshot.get("monthly_volume") or 0)
                except (TypeError, ValueError):
                    volume = 0
                try:
                    h_value = float(derived.get("h") or 0.0)
                except (TypeError, ValueError):
                    h_value = 0.0

                scored_rows.append({
                    "keyword_id": kid,
                    "keyword_text": keyword_text or "",
                    "cls": cls,
                    "adjusted": adjusted,
                    "relevance": relevance,
                    "volume": volume,
                    "h": h_value,
                })

            # Deterministik sinif-oncelikli siralama (dokuman Bolum 7)
            scored_rows.sort(key=lambda r: (
                -r["cls"], -r["adjusted"], -r["volume"], r["keyword_text"], r["keyword_id"]
            ))
            selected = scored_rows[:capacity]

            rising_cutoff = math.ceil(capacity * RISING_OPPORTUNITY_TOP_RATIO)
            for rank, row in enumerate(selected, 1):
                pool_label = None
                if (
                    channel == 'SOCIAL'
                    and rank <= rising_cutoff
                    and row["h"] >= RISING_OPPORTUNITY_MIN_H
                ):
                    pool_label = RISING_OPPORTUNITY_LABEL
                self.db.add(ChannelPool(
                    scoring_run_id=scoring_run_id,
                    keyword_id=row["keyword_id"],
                    channel=channel,
                    final_rank=rank,
                    relevance_score=Decimal(str(round(row["relevance"], 3))),
                    adjusted_score=Decimal(str(round(row["adjusted"], 4))),
                    is_strategic=False,
                    pool_label=pool_label,
                ))

            selected_ids_by_channel[channel] = {row["keyword_id"] for row in selected}
            final_counts[channel] = len(selected)

        self.db.commit()

        # Stratejik Anahtar Kelime: hem ADS hem SEO seciminde yer alanlar
        ads_ids = selected_ids_by_channel.get('ADS', set())
        seo_ids = selected_ids_by_channel.get('SEO', set())
        strategic_ids = ads_ids & seo_ids
        if strategic_ids:
            self.db.query(ChannelPool).filter(
                ChannelPool.scoring_run_id == scoring_run_id,
                ChannelPool.channel.in_(['ADS', 'SEO']),
                ChannelPool.keyword_id.in_(strategic_ids),
            ).update({ChannelPool.is_strategic: True}, synchronize_session=False)
            self.db.commit()
            logger.info(
                f"Stratejik anahtar kelime etiketi: {len(strategic_ids)} keyword (ADS-SEO kesisimi)"
            )

        return final_counts
    
    def get_channel_pools(self, scoring_run_id: int) -> Dict[str, Any]:
        """
        TÃ¼m kanal havuzlarÄ±nÄ± dÃ¶ner.
        
        Args:
            scoring_run_id: Skorlama Ã§alÄ±ÅŸtÄ±rmasÄ± ID
        
        Returns:
            Kanal havuzlarÄ±
        """
        from app.database.models import Keyword, WorkspaceKeyword

        run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        active_channels = self._get_active_channels(run) if run else ['ADS', 'SEO', 'SOCIAL']
        capacity_map = {
            'ADS': int(run.ads_capacity or ADS_FINAL_CAPACITY) if run else ADS_FINAL_CAPACITY,
            'SEO': int(run.seo_capacity or SEO_FINAL_CAPACITY) if run else SEO_FINAL_CAPACITY,
            'SOCIAL': int(run.social_capacity or SOCIAL_FINAL_CAPACITY) if run else SOCIAL_FINAL_CAPACITY,
        }

        # Hacim, workspace snapshot'ından gelir (export ile ayni kaynak; global
        # Keyword kolonu legacy). Ayni kelimenin birden fazla snapshot'i varsa
        # en yuksek hacim deterministik olarak secilir.
        volume_map: Dict[int, int] = {}
        if run and run.brand_profile_id:
            for wk in (
                self.db.query(WorkspaceKeyword.keyword_id, WorkspaceKeyword.monthly_volume)
                .filter(WorkspaceKeyword.brand_profile_id == run.brand_profile_id)
                .all()
            ):
                current = volume_map.get(wk.keyword_id, 0)
                volume_map[wk.keyword_id] = max(current, int(wk.monthly_volume or 0))

        sel_map = {}
        if run and getattr(run, "algorithm_version", None) == "v3":
            from app.database.models import EngineSelection
            selections = (
                self.db.query(EngineSelection)
                .filter(EngineSelection.scoring_run_id == scoring_run_id)
                .all()
            )
            sel_map = {(s.channel.upper(), s.keyword_id): s for s in selections}

        result = {
            'scoring_run_id': scoring_run_id,
            'channels': {},
            'capacities': {ch: capacity_map[ch] for ch in active_channels},
        }
        for channel in active_channels:
            pools = (
                self.db.query(ChannelPool, Keyword)
                .outerjoin(Keyword, ChannelPool.keyword_id == Keyword.id)
                .filter(ChannelPool.scoring_run_id == scoring_run_id)
                .filter(ChannelPool.channel == channel)
                .order_by(ChannelPool.final_rank)
                .all()
            )
            
            result['channels'][channel] = [
                {
                    'id': pool.id,
                    'rank': pool.final_rank,
                    'final_rank': pool.final_rank,
                    'keyword_id': pool.keyword_id,
                    'keyword': (
                        keyword.keyword
                        if keyword is not None
                        else f"[missing keyword #{pool.keyword_id}]"
                    ),
                    'keyword_missing': keyword is None,
                    'volume': volume_map.get(
                        pool.keyword_id,
                        int(keyword.monthly_volume or 0) if keyword is not None else None,
                    ),
                    'is_strategic': pool.is_strategic,
                    'pool_label': pool.pool_label,
                    'relevance_score': float(pool.relevance_score) if pool.relevance_score is not None else None,
                    'adjusted_score': float(pool.adjusted_score) if pool.adjusted_score is not None else None,
                    'algorithm_rank': sel_map.get((channel.upper(), pool.keyword_id)).algorithm_rank if sel_map.get((channel.upper(), pool.keyword_id)) else None,
                    'pool_class': sel_map.get((channel.upper(), pool.keyword_id)).pool_class if sel_map.get((channel.upper(), pool.keyword_id)) else None,
                    'capacity': capacity_map.get(channel),
                }
                for pool, keyword in pools
            ]
        
        result['unfilled_counts'] = {
            ch: max(0, capacity_map[ch] - len(result['channels'].get(ch, [])))
            for ch in active_channels
        }
        return result
