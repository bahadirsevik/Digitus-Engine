"""
Build channel candidate pools.
"""
import logging
from typing import List, Dict
from decimal import Decimal
from sqlalchemy.orm import Session

# P1.4: BrandProfile / KeywordRelevance BILEREK import edilmiyor — relevance
# okumasi yalniz `app.core.relevance.load_effective_relevance_map` uzerinden
# yapilir. Dogrudan sorgu yazmak kapiyi atlatir.
from app.database.models import (
    KeywordScore, ChannelCandidate, ScoringRun
)
from app.core.constants import ADS_POOL_SIZE, SEO_POOL_SIZE, SOCIAL_POOL_SIZE
from app.core.screening.assistive import (
    ACTION_EXPANSION,
    ACTION_INITIAL,
    AssistiveAuditConflict,
    audit_later_action,
)

logger = logging.getLogger(__name__)


class PoolBuilder:
    """
    Selects top candidates per channel.
    If confirmed profile + relevance exists, uses relevance-adjusted ranking.
    """

    def __init__(self, db: Session):
        self.db = db

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

    def build_candidate_pools(
        self,
        scoring_run_id: int,
        relevance_coefficient: float = 1.0,
        applied_plan=None,
        commit: bool = True,
    ) -> Dict[str, int]:
        """
        Build candidate pools for each channel.

        Relevance path formula:
            adjusted_score = raw_score * relevance_score * relevance_coefficient

        `commit=False`: yazim CAGIRANIN transaction'inda kalir. Eski
        havuz temizligi + yeni adaylar + audit tek commit'te birlesir;
        yazim patlarsa ROLLBACK eski canli havuzu GERI GETIRIR
        (Codex 24. tur #2).

        `applied_plan` (assistive): kapsam ici kanallar (ADS+SEO) icin
        aday kumesi UNION planindan gelir; plani OLMAYAN kanal (SOCIAL)
        bugunku kod yolunu AYNEN kullanir -> kimlikler ve sira BIREBIR
        ayni kalir (invaryant 4). Adaylar ve `is_applied=true` audit
        satirlari AYNI transaction'da yazilir (invaryant 3 ve 5).
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

        active_channels = self._get_active_channels(scoring_run)

        # Cap candidate pool to capacity*3 so small-capacity runs don't send 120 candidates
        # to AI pre-filter when only 5 will be selected.
        capacity_map = {
            "ADS": int(scoring_run.ads_capacity or ADS_POOL_SIZE),
            "SEO": int(scoring_run.seo_capacity or SEO_POOL_SIZE),
            "SOCIAL": int(scoring_run.social_capacity or SOCIAL_POOL_SIZE),
        }
        # Deney anahtari (Optimice pencere deneyi): default 1 = mevcut
        # davranis. >1 ise AI'ya giden aday penceresi carpilir; deger
        # manifest'e yazilir (dispatcher), rapor deneyi acikca gosterir.
        from app.config import settings
        mult = max(1, int(settings.CANDIDATE_POOL_MULTIPLIER or 1))
        pool_sizes = {
            "ADS": min(ADS_POOL_SIZE * mult, capacity_map["ADS"] * 3 * mult),
            "SEO": min(SEO_POOL_SIZE * mult, capacity_map["SEO"] * 3 * mult),
            "SOCIAL": min(SOCIAL_POOL_SIZE * mult,
                          capacity_map["SOCIAL"] * 3 * mult),
        }

        # Filter pool sizes to active channels only
        pool_sizes = {ch: sz for ch, sz in pool_sizes.items() if ch in active_channels}

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

            planned = (applied_plan.channels.get(channel)
                       if applied_plan is not None else None)
            if planned:
                # ASSISTIVE: aday kumesi UNION planindan (baseline + tarama).
                # Kaynak alanlari da yazilir (Codex 24. tur #5): final
                # havuzda hangi adayin taramadan geldigi denetlenebilir.
                for (kid, adjusted, rank, origin, _baseline_rank,
                     screening_rank, fit, _relevance) in planned:
                    self.db.add(ChannelCandidate(
                        scoring_run_id=scoring_run_id,
                        keyword_id=kid,
                        channel=channel,
                        raw_score=Decimal(str(round(float(adjusted), 4))),
                        rank_in_channel=rank,
                        screening_job_id=applied_plan.screening_job_id,
                        candidate_origin_source=origin,
                        candidate_materialization_action=ACTION_INITIAL,
                        screening_fit=(None if fit is None
                                       else Decimal(str(round(float(fit),
                                                              4)))),
                        screening_rank=screening_rank,
                    ))
                result_counts[channel] = len(planned)
                continue

            if has_relevance:
                scored_candidates = []
                for ks in all_scores:
                    raw = float(getattr(ks, score_field) or 0)
                    relevance = float(relevance_map.get(ks.keyword_id, 0.5))
                    # max(raw, 0): v2 skorları negatif olabilir (N_ADS min -15);
                    # negatif x [0,1] relevance sıralamayı ters çevirir.
                    adjusted = max(raw, 0.0) * relevance * relevance_coefficient
                    scored_candidates.append((ks, adjusted))

                # Deterministik tie-break: clamp sonrası 0'a yığılan eşit adjusted
                # skorlarda kanal rank'i (skor->hacim->alfabetik zinciriyle üretildi)
                # ve keyword_id sırayı sabitler — aynı veriyle hep aynı adaylar AI'a gider.
                scored_candidates.sort(
                    key=lambda x: (
                        -x[1],
                        getattr(x[0], rank_field) or 999999,
                        x[0].keyword_id or 0,
                    )
                )
                selected = scored_candidates[:pool_size]

                for new_rank, (ks, adjusted_score) in enumerate(selected, 1):
                    # NOT: relevance yolunda raw_score kolonuna ADJUSTED değer
                    # yazılır (tarihsel isim). Saf v2 skoru her zaman
                    # KeywordScore'dadır — debug/analizde oraya bakın.
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

        if applied_plan is not None:
            self._write_applied_audit(applied_plan, scoring_run_id)
            self.db.flush()
            self._verify_audit_matches_pool(applied_plan, scoring_run_id)

        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return result_counts

    def _write_applied_audit(self, applied_plan, scoring_run_id: int) -> int:
        """`is_applied=true` audit satirlari — AYNI transaction, IDEMPOTENT.

        Aday commit'inden sonra olen bir worker'in lease takeover'i ayni
        (run, attempt, keyword, kanal, action) anahtarini yeniden yazmaya
        calisirdi; `ON CONFLICT DO NOTHING` ile satir COGALMAZ ve
        `uq_candidate_selection` ihlali atamayi dusurmez (24. tur #4).
        Materyalizasyon deterministik oldugu icin ilk satir dogrudur.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.core.screening.assistive import audit_rows_for
        from app.database.models import (
            ChannelAssignmentAttempt,
            CorpusCandidateSelection,
        )

        rows = audit_rows_for(applied_plan, scoring_run_id)
        # ON CONFLICT DO NOTHING tek basina YETMEZ: ayni anahtarda
        # `is_applied=false` (karsi-olgu) satiri varsa applied audit
        # SESSIZCE yutulur ve canli adaylarla audit AYRISIRDI. Assistive'de
        # bu durum sozlesme ihlalidir -> fail-closed (cagiran rollback
        # eder, kosu baseline'a duser).
        existing = (self.db.query(
            CorpusCandidateSelection.keyword_id,
            CorpusCandidateSelection.channel,
            CorpusCandidateSelection.is_applied,
            CorpusCandidateSelection.materialization_identity_sha256)
            .filter(CorpusCandidateSelection.assignment_attempt_id
                    == applied_plan.attempt_id,
                    CorpusCandidateSelection.scoring_run_id
                    == scoring_run_id,
                    CorpusCandidateSelection.materialization_action
                    == ACTION_INITIAL).all())
        planned_keys = {(r["keyword_id"], r["channel"]) for r in rows}
        for keyword_id, channel, is_applied, identity in existing:
            if is_applied and (keyword_id, channel) not in planned_keys:
                # Codex 25. tur #2: PLAN DISI applied anahtar — onceki
                # uygulama farkli bir aday kumesi yazmis demektir; sessizce
                # gecilirse audit canli havuzla ayrisir (fail-closed)
                raise AssistiveAuditConflict(
                    f"attempt {applied_plan.attempt_id} icin plan disi "
                    f"applied audit anahtari var ({channel}/{keyword_id})")
            if (keyword_id, channel) not in planned_keys:
                continue
            if not is_applied:
                raise AssistiveAuditConflict(
                    f"attempt {applied_plan.attempt_id} icin ayni anahtarda "
                    f"karsi-olgu (is_applied=false) satiri var — applied "
                    f"audit yazilamaz ({channel}/{keyword_id})")
            if identity and identity != applied_plan.identity_sha256:
                raise AssistiveAuditConflict(
                    f"attempt {applied_plan.attempt_id} icin FARKLI "
                    f"materyalizasyon kimligi kayitli ({str(identity)[:12]}"
                    f"... != {applied_plan.identity_sha256[:12]}...)")
        if rows:
            self.db.execute(
                pg_insert(CorpusCandidateSelection)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_candidate_selection"))
        # Materyalizasyon kimligi attempt'e de yazilir: hangi aday
        # kumesinin uygulandigi sonradan denetlenebilir (24. tur #5)
        self.db.query(ChannelAssignmentAttempt).filter(
            ChannelAssignmentAttempt.id == applied_plan.attempt_id
        ).update(
            {"materialization_identity_sha256": applied_plan.identity_sha256,
             "applied_screening_channels": list(
                 applied_plan.applied_channels)},
            synchronize_session=False)
        return len(rows)

    def _verify_audit_matches_pool(self, applied_plan,
                                   scoring_run_id: int) -> None:
        """Commit ONCESI birebirlik: applied audit == canli union adaylari.

        Codex 25. tur #2: "audit yazildi" ile "canli havuz union" ayni sey
        degildir; ikisi ayrismadan commit edilmemelidir (fail-closed).
        Codex 26. tur #2: kapi TUM asamalari kapsar — initial, transfer ve
        expansion adaylarinin hepsi audit'te olmali.
        """
        from app.database.models import CorpusCandidateSelection

        audited = {
            (row.keyword_id, row.channel)
            for row in self.db.query(
                CorpusCandidateSelection.keyword_id,
                CorpusCandidateSelection.channel)
            .filter(CorpusCandidateSelection.assignment_attempt_id
                    == applied_plan.attempt_id,
                    CorpusCandidateSelection.scoring_run_id == scoring_run_id,
                    CorpusCandidateSelection.is_applied.is_(True)).all()}
        live = {
            (row.keyword_id, row.channel)
            for row in self.db.query(ChannelCandidate.keyword_id,
                                     ChannelCandidate.channel)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id,
                    ChannelCandidate.channel.in_(
                        list(applied_plan.applied_channels))).all()}
        if audited != live:
            raise AssistiveAuditConflict(
                f"audit ({len(audited)}) canli union adaylariyla "
                f"({len(live)}) ayristi — yalniz audit'te "
                f"{len(audited - live)}, yalniz canlida {len(live - audited)}")

    def add_expansion_candidates(
        self,
        scoring_run_id: int,
        channel: str,
        *,
        limit: int,
        max_total_candidates: int,
        relevance_coefficient: float = 1.0,
        applied_context=None,
    ) -> Dict[str, int]:
        """Add the next ranked candidate window for one channel.

        Uses the same ordering as the initial pool builder: relevance-adjusted
        score when relevance exists, otherwise the channel rank field.
        """
        if limit <= 0:
            return {"added": 0, "window_start": 0, "window_end": 0}

        scoring_run = self.db.query(ScoringRun).filter(
            ScoringRun.id == scoring_run_id
        ).first()
        if not scoring_run:
            raise ValueError(f"Scoring run {scoring_run_id} not found")

        if channel not in self._get_active_channels(scoring_run):
            return {"added": 0, "window_start": 0, "window_end": 0}

        ordered_scores = self._ranked_scores_for_channel(
            scoring_run_id,
            channel,
            relevance_coefficient=relevance_coefficient,
        )
        ordered_scores = ordered_scores[:max_total_candidates]

        existing_rows = (
            self.db.query(ChannelCandidate.keyword_id, ChannelCandidate.rank_in_channel)
            .filter(ChannelCandidate.scoring_run_id == scoring_run_id)
            .filter(ChannelCandidate.channel == channel)
            .all()
        )
        existing_ids = {keyword_id for keyword_id, _ in existing_rows}
        next_rank = max((rank for _, rank in existing_rows), default=0) + 1

        selected = []
        window_start = next_rank
        for ks, score in ordered_scores:
            if ks.keyword_id in existing_ids:
                continue
            selected.append((ks, score))
            if len(selected) >= limit:
                break

        covered = (applied_context is not None
                   and applied_context.covers(channel))
        for offset, (ks, score) in enumerate(selected):
            # NOT: relevance aktifken bu kolon adjusted değer taşır (tarihsel
            # isim); saf v2 skoru KeywordScore'dadır.
            # Expansion penceresi BASELINE siralamasindan gelir; tarama
            # sinyali kullanilmaz -> origin 'baseline' (Codex 26. tur #2).
            self.db.add(ChannelCandidate(
                scoring_run_id=scoring_run_id,
                keyword_id=ks.keyword_id,
                channel=channel,
                raw_score=Decimal(str(round(score, 4))),
                rank_in_channel=next_rank + offset,
                screening_job_id=(applied_context.screening_job_id
                                  if covered else None),
                candidate_origin_source="baseline" if covered else None,
                candidate_materialization_action=(ACTION_EXPANSION
                                                  if covered else None),
            ))
            if covered:
                audit_later_action(
                    self.db, applied_context,
                    scoring_run_id=scoring_run_id, channel=channel,
                    keyword_id=ks.keyword_id, origin="baseline",
                    action=ACTION_EXPANSION, rank=next_rank + offset,
                    adjusted=score)
        if covered and selected:
            self.db.flush()
            self._verify_audit_matches_pool(applied_context, scoring_run_id)

        self.db.commit()
        return {
            "added": len(selected),
            "window_start": window_start if selected else next_rank,
            "window_end": next_rank + len(selected) - 1 if selected else next_rank - 1,
        }

    def _ranked_scores_for_channel(
        self,
        scoring_run_id: int,
        channel: str,
        *,
        relevance_coefficient: float = 1.0,
    ) -> List[tuple[KeywordScore, float]]:
        relevance_map = self._load_relevance_map(scoring_run_id)
        has_relevance = len(relevance_map) > 0
        score_fields = {
            "ADS": ("ads_score", "ads_rank"),
            "SEO": ("seo_score", "seo_rank"),
            "SOCIAL": ("social_score", "social_rank"),
        }
        score_field, rank_field = score_fields[channel]
        all_scores = (
            self.db.query(KeywordScore)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )

        if has_relevance:
            ranked = []
            for ks in all_scores:
                raw = float(getattr(ks, score_field) or 0)
                relevance = float(relevance_map.get(ks.keyword_id, Decimal("0.5")))
                # max(raw, 0): negatif v2 skoru x relevance ters çevirme koruması
                ranked.append((ks, max(raw, 0.0) * relevance * relevance_coefficient))
            # Tie-break build_candidate_pools ile aynı: rank alanı + keyword_id
            ranked.sort(
                key=lambda x: (
                    -x[1],
                    getattr(x[0], rank_field) or 999999,
                    x[0].keyword_id or 0,
                )
            )
            return ranked

        ranked = [(ks, float(getattr(ks, score_field) or 0)) for ks in all_scores]
        ranked.sort(key=lambda x: (getattr(x[0], rank_field) or 999999, x[0].id or 0))
        return ranked

    def _load_relevance_map(self, scoring_run_id: int) -> Dict[int, float]:
        """
        Load relevance scores for all keywords in a run.
        Returns empty dict if the relevance gate is closed.

        Sadakat plani P1.4: kapi mantigi `app/core/relevance/read_model.py`'ye
        tasindi. Buradaki eski surum bayrak + confirmed + deleted_at kontrol
        ediyordu ama `skip_relevance` ve anchor SURUMUNU kontrol etmiyordu;
        `channel_engine` tarafi ise hicbirini. Iki okuyucunun tek kapiyi
        paylasmasi asimetriyi yapisal olarak imkansiz kilar.
        """
        from app.core.relevance import load_effective_relevance_map

        return load_effective_relevance_map(self.db, scoring_run_id)

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
