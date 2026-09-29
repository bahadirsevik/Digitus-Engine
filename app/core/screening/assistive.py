# -*- coding: utf-8 -*-
"""Assistive: union adaylarının CANLI havuza uygulanması (plan §7.3, §8.2).

BAĞLAYICI INVARYANTLAR (Codex 24. tur):
1. Tarama TAMAMLANMADAN canlı `ChannelCandidate` DEĞİŞMEZ.
2. Drift ve kill-switch kontrolleri canlı yazımdan HEMEN ÖNCE, TAZE DB
   verisiyle yapılır (önbellekten/attempt snapshot'ından değil).
3. Eski havuz temizliği + ADS/SEO union + audit TEK transaction'dadır
   (çağıran `channel_engine` commit'i tek noktada yapar).
4. SOCIAL kimlikleri VE SIRASI baseline ile BİREBİR kalır (plan'a hiç
   girmez → bugünkü kod yolu aynen çalışır).
5. `is_applied=true` audit satırları canlı adaylarla AYNI transaction'da
   yazılır ve İDEMPOTENTTİR (lease takeover satırı çoğaltmaz).
6. Hata/fallback durumunda YARIM union veya KISMİ audit kalmaz.
7. Otomatik akışta uygunsuzluk kullanıcıya HATA DEĞİLDİR: sebep audit'e
   yazılır ve baseline'a düşülür.

Bu modül karar üretir (`build_applied_plan`); yazma işini `PoolBuilder`
tek transaction'da yapar — böylece adaylar ve audit satırları asla
ayrışmaz.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.core.benchmark.candidate_replay import (
    DEFAULT_RELEVANCE,
    adjusted_ordering,
    budget_initial,
    raw_rank_ordering,
)
from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    build_union_candidate_plan,
)
from app.core.screening.counterfactual import (
    CHANNELS,
    CounterfactualError,
    _merged_from_decisions,
    _rows_from_job_snapshot,
    channel_union_mode,
)
from app.core.screening.ensemble import ensemble_ordering
from app.database.models import (
    ChannelAssignmentAttempt,
    CorpusScreeningJob,
    ScoringRun,
)

# Union YALNIZ kapsam içi kanallara uygulanır; SOCIAL bugünkü yolda kalır
APPLIED_CHANNELS = ("ADS", "SEO")
ACTION_INITIAL = "initial"
ACTION_TRANSFER = "transfer"
ACTION_EXPANSION = "expansion"


@dataclass
class AppliedContext:
    """Uygulanan planin KUNYESI — transfer/expansion da bunu tasir.

    Ilk union yazildiktan sonra plani tasimaya gerek yoktur; sonraki
    asamalarin (transfer, expansion) kaynak alanlari ve audit satirlari
    icin attempt/job/kimlik + kanal-kelime kaynak haritasi yeterlidir.
    """

    attempt_id: int
    screening_job_id: int
    identity_sha256: str
    applied_channels: Tuple[str, ...]
    capacities: Dict[str, int] = field(default_factory=dict)
    origin_map: Dict[Tuple[str, int], str] = field(default_factory=dict)

    def covers(self, channel: str) -> bool:
        """Kapsam disi kanal (SOCIAL) audit/kaynak ALMAZ."""
        return channel in self.applied_channels

    def origin_for(self, channel: str, keyword_id: int) -> str:
        """Kelimenin o kanaldaki kaynagi; bilinmiyorsa 'baseline'."""
        return self.origin_map.get((channel, int(keyword_id)), "baseline")


@dataclass
class AppliedPlan:
    """Canlı havuza yazılacak aday planı + audit verisi."""

    attempt_id: int
    screening_job_id: int
    identity_sha256: str
    # kanal -> [(keyword_id, adjusted_score, rank, origin, baseline_rank,
    #           screening_rank, fit, relevance)]
    channels: Dict[str, List[Tuple]] = field(default_factory=dict)
    budgets: Dict[str, Dict[str, int]] = field(default_factory=dict)
    capacities: Dict[str, int] = field(default_factory=dict)
    applied_channels: Tuple[str, ...] = APPLIED_CHANNELS

    def keyword_ids(self, channel: str) -> List[int]:
        return [row[0] for row in self.channels.get(channel, [])]

    def context(self) -> "AppliedContext":
        """Sonraki asamalara tasinacak kunye."""
        return AppliedContext(
            attempt_id=self.attempt_id,
            screening_job_id=self.screening_job_id,
            identity_sha256=self.identity_sha256,
            applied_channels=tuple(self.applied_channels),
            capacities=dict(self.capacities),
            origin_map={(channel, int(row[0])): row[3]
                        for channel, rows in self.channels.items()
                        for row in rows})


class AssistiveAuditConflict(RuntimeError):
    """Applied audit yazilamaz: ayni anahtarda cakisan satir var.

    Cagiran ROLLBACK eder; kosu baseline ile surer (sebep denetime
    yazilir). Sessiz yutma YASAK — audit canli adaylarla ayrisamaz.
    """


class AssistiveNotApplicable(Exception):
    """Uygun değil — HATA DEĞİL, baseline'a düşülür (sebep audit'e)."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def active_channels_of(run: ScoringRun) -> Tuple[str, ...]:
    """Run'ın GERÇEK aktif kanalları (`enable_*`)."""
    return tuple(ch for ch in CHANNELS
                 if bool(getattr(run, f"enable_{ch.lower()}", True)))


def expected_applied_channels(run: ScoringRun) -> Tuple[str, ...]:
    """Union'ın uygulanabileceği kanallar = kapsam içi ∩ aktif."""
    active = set(active_channels_of(run))
    return tuple(ch for ch in APPLIED_CHANNELS if ch in active)


def _verify_live_preconditions(db, run: ScoringRun,
                               attempt: ChannelAssignmentAttempt,
                               job: CorpusScreeningJob, settings) -> None:
    """Canlı yazımdan HEMEN ÖNCE, TAZE veriyle kapılar (invaryant 2).

    Attempt snapshot'ına GÜVENİLMEZ: workspace satırı kilit altında
    yeniden okunur ve bağlam/politika sürümleri o an hesaplanır.
    """
    from app.core.policy.channel_strategy import approved_strategy
    from app.core.screening.context import (
        ScreeningContextMissing,
        canonical_screening_context,
    )
    from app.core.telemetry.downstream_ledger import (
        DownstreamRouteError,
        require_ledgered_routes,
    )
    from app.database.models import BrandProfile

    if not getattr(settings, "ENABLE_CORPUS_SCREENING", False):
        raise AssistiveNotApplicable("KILL_SWITCH_OFF",
                                     "tarama bayrağı kapatılmış")
    if attempt.screening_mode != "assistive":
        raise AssistiveNotApplicable("MODE_NOT_ASSISTIVE",
                                     str(attempt.screening_mode))
    if job.status != "completed":
        raise AssistiveNotApplicable("SCREENING_NOT_COMPLETED", job.status)
    try:
        require_ledgered_routes(settings)
    except DownstreamRouteError as exc:
        raise AssistiveNotApplicable("DOWNSTREAM_ROUTE_NOT_LEDGERED",
                                     str(exc)) from exc

    workspace = (db.query(BrandProfile)
                 .filter(BrandProfile.id == run.brand_profile_id)
                 .with_for_update()
                 .populate_existing()
                 .first())
    if workspace is None:
        raise AssistiveNotApplicable("WORKSPACE_MISSING", "")
    try:
        context = canonical_screening_context(workspace)
    except ScreeningContextMissing as exc:
        raise AssistiveNotApplicable("CONTEXT_MISSING", str(exc)) from exc
    if context["context_sha256"] != job.context_sha256:
        raise AssistiveNotApplicable(
            "CONTEXT_DRIFT",
            f"canlı {context['context_sha256'][:12]}... != mühür "
            f"{str(job.context_sha256)[:12]}...")
    live_policy = int(getattr(workspace, "policy_version", 1) or 1)
    if attempt.requested_policy_version is not None \
            and live_policy != int(attempt.requested_policy_version):
        raise AssistiveNotApplicable(
            "POLICY_DRIFT", f"canlı {live_policy} != dispatch "
                            f"{attempt.requested_policy_version}")
    live_anchor = int(getattr(workspace, "anchor_version", 1) or 1)
    if attempt.requested_anchor_version is not None \
            and live_anchor != int(attempt.requested_anchor_version):
        raise AssistiveNotApplicable(
            "ANCHOR_DRIFT", f"canlı {live_anchor} != dispatch "
                            f"{attempt.requested_anchor_version}")
    strategy = approved_strategy(workspace)
    if not strategy:
        raise AssistiveNotApplicable("STRATEGY_REMOVED", "")


def _verify_job_contract(db, *, run, attempt, job, rows, merged,
                         applied: Tuple[str, ...],
                         relevance_present: bool) -> str:
    """Job sözleşmesi + sahiplik + mühür doğrulaması (Codex 24. tur #6).

    Keyword ID kümesi TEK BAŞINA yetmez: bir kelimenin SEO kararı eksik
    olsa bile ADS kararı bulunduğu için küme kontrolü geçerdi. Burada
    kapsam, sağlayıcı/model/prompt sözleşmesi, runner sözleşmesi, karar
    tamlığı ve evren/rank/relevance mühürleri fail-closed doğrulanır.
    Dönüş: doğrulanmış `channel_rank_snapshot_sha256`.
    """
    from app.core.screening.identity import (
        channel_rank_snapshot_sha256,
        relevance_rows_sha256,
        screening_runner_contract,
        universe_sha256,
    )

    # Sahiplik: job, attempt ve run AYNI run/workspace'e ait olmalı
    if int(job.scoring_run_id) != int(run.id) \
            or int(attempt.scoring_run_id) != int(run.id):
        raise AssistiveNotApplicable(
            "OWNERSHIP_MISMATCH",
            f"job run {job.scoring_run_id}, attempt run "
            f"{attempt.scoring_run_id}, canlı run {run.id}")
    if job.brand_profile_id != run.brand_profile_id:
        raise AssistiveNotApplicable(
            "WORKSPACE_MISMATCH",
            f"job ws {job.brand_profile_id} != run ws "
            f"{run.brand_profile_id}")

    # Kapsam: mühürlü kapsam canlı aktif kapsamla BİREBİR olmalı
    sealed = set(job.applied_screening_channels or ())
    if sealed != set(applied):
        raise AssistiveNotApplicable(
            "APPLIED_CHANNEL_SET_DRIFT",
            f"mühür {sorted(sealed)} != canlı aktif kapsam "
            f"{sorted(applied)}")
    attempt_scope = set(attempt.applied_screening_channels or ()) or sealed
    if attempt_scope != sealed:
        raise AssistiveNotApplicable(
            "APPLIED_CHANNEL_SET_DRIFT",
            f"attempt kapsamı {sorted(attempt_scope)} != job kapsamı "
            f"{sorted(sealed)}")

    # Sağlayıcı/model/prompt/örnekleme sözleşmesi
    contract = PRODUCTION_SCREENING_CONTRACT
    drift = []
    if job.provider != contract["provider"]:
        drift.append(f"provider={job.provider!r}")
    if job.model != contract["model"]:
        drift.append(f"model={job.model!r}")
    if job.prompt_version != contract["prompt_version"]:
        drift.append(f"prompt_version={job.prompt_version!r}")
    if int(job.batch_size or 0) != int(contract["batch_size"]):
        drift.append(f"batch_size={job.batch_size!r}")
    if float(job.temperature or 0) != float(contract["temperature"]):
        drift.append(f"temperature={job.temperature!r}")
    if list(job.view_salts or []) != list(contract["view_salts"]):
        drift.append(f"view_salts={job.view_salts!r}")
    if drift:
        raise AssistiveNotApplicable("SCREENING_CONTRACT_DRIFT",
                                     ", ".join(drift))
    live_runner = screening_runner_contract()
    if dict(job.runner_contract or {}) != live_runner:
        raise AssistiveNotApplicable(
            "RUNNER_CONTRACT_DRIFT",
            "mühürlü runner sözleşmesi canlı kodla uyuşmuyor")

    # Karar tamlığı: HER kelime için ÜÇ kanalın da kararı olmalı
    required = {ch.lower() for ch in CHANNELS}
    incomplete = [m.keyword_id for m in merged
                  if not required <= set(m.passing)]
    if incomplete:
        raise AssistiveNotApplicable(
            "DECISIONS_INCOMPLETE",
            f"{len(incomplete)} kelimede kanal kararı eksik "
            f"(ilk: {incomplete[:5]})")

    # Mühürler: evren, rank ve relevance
    try:
        uni_sha = universe_sha256([{"keyword_id": r["keyword_id"],
                                    "text": r["text"]} for r in rows])
    except Exception as exc:  # noqa: BLE001 - fail-closed
        raise AssistiveNotApplicable("UNIVERSE_SEAL_UNVERIFIABLE",
                                     str(exc)) from exc
    if job.universe_sha256 and uni_sha != job.universe_sha256:
        raise AssistiveNotApplicable(
            "UNIVERSE_SEAL_MISMATCH",
            f"canlı {uni_sha[:12]}... != mühür "
            f"{str(job.universe_sha256)[:12]}...")

    rank_sha = channel_rank_snapshot_sha256(
        {ch: {r["keyword_id"]: (r["ranks"][ch] or 10 ** 9) for r in rows}
         for ch in CHANNELS})
    if attempt.channel_rank_snapshot_sha256 \
            and attempt.channel_rank_snapshot_sha256 != rank_sha:
        raise AssistiveNotApplicable(
            "RANK_SNAPSHOT_MISMATCH",
            f"{rank_sha[:12]}... != "
            f"{str(attempt.channel_rank_snapshot_sha256)[:12]}...")
    if attempt.relevance_rows_sha256:
        if not relevance_present:
            raise AssistiveNotApplicable(
                "RELEVANCE_SEAL_MISMATCH",
                "attempt relevance mührü var ama snapshot'ta relevance yok")
        live_rel = relevance_rows_sha256(
            {r["keyword_id"]: r["relevance"] for r in rows})
        if live_rel != attempt.relevance_rows_sha256:
            raise AssistiveNotApplicable(
                "RELEVANCE_SEAL_MISMATCH",
                f"{live_rel[:12]}... != "
                f"{str(attempt.relevance_rows_sha256)[:12]}...")
    return rank_sha


def build_applied_plan(db, *, scoring_run_id: int,
                       parent_task_id: Optional[str],
                       relevance_coefficient: float = 1.0,
                       settings=None) -> Tuple[Optional[AppliedPlan],
                                               Optional[Dict[str, Any]]]:
    """Assistive uygunsa canlıya yazılacak planı üretir.

    Dönüş: `(plan, skip_info)` — plan None ise BASELINE koşulur ve
    `skip_info` sebebi taşır (kullanıcıya hata GİTMEZ, invaryant 7).
    """
    from app.config import settings as default_settings

    settings = settings or default_settings
    attempt = None
    if parent_task_id:
        attempt = (db.query(ChannelAssignmentAttempt)
                   .filter(ChannelAssignmentAttempt.parent_task_id
                           == parent_task_id)
                   .first())
    if attempt is None:
        attempt = (db.query(ChannelAssignmentAttempt)
                   .filter(ChannelAssignmentAttempt.scoring_run_id
                           == scoring_run_id)
                   .order_by(ChannelAssignmentAttempt.id.desc())
                   .first())
    if attempt is None or attempt.screening_mode != "assistive":
        return None, None            # off/shadow: bugünkü yol
    if not attempt.screening_job_id:
        return None, {"reason": "NO_SCREENING_JOB"}

    run = db.get(ScoringRun, scoring_run_id)
    job = db.get(CorpusScreeningJob, attempt.screening_job_id)
    if run is None or job is None:
        return None, {"reason": "JOB_MISSING"}

    try:
        _verify_live_preconditions(db, run, attempt, job, settings)
        applied = expected_applied_channels(run)
        if not applied:
            raise AssistiveNotApplicable(
                "NO_APPLIED_CHANNELS",
                "kapsam içi kanalların (ADS/SEO) hepsi kapalı")
        rows = _rows_from_job_snapshot(job)
        salts = list(job.view_salts or [])
        merged = _merged_from_decisions(db, job.id, salts)
        universe_ids = {r["keyword_id"] for r in rows}
        if {m.keyword_id for m in merged} != universe_ids:
            raise AssistiveNotApplicable(
                "UNIVERSE_DECISION_MISMATCH",
                "karar kümesi mühürlü evrenle örtüşmüyor")
        relevance_present = all(r["relevance"] is not None for r in rows)
        rank_sha = _verify_job_contract(
            db, run=run, attempt=attempt, job=job, rows=rows, merged=merged,
            applied=applied, relevance_present=relevance_present)
    except (AssistiveNotApplicable, CounterfactualError) as exc:
        reason = getattr(exc, "reason", None) or getattr(exc, "code", "ERROR")
        detail = getattr(exc, "detail", "") or str(exc)
        logger.warning(f"assistive UYGULANMADI ({reason}): {detail}")
        return None, {"reason": reason, "detail": detail[:500]}

    active = active_channels_of(run)
    capacities = {ch: int(getattr(run, f"{ch.lower()}_capacity") or 0)
                  for ch in CHANNELS}
    multiplier = int(attempt.applied_candidate_multiplier or 3)
    fit_by_keyword = {m.keyword_id: m.mean_fit for m in merged}
    relevance_by_keyword = {r["keyword_id"]: r["relevance"] for r in rows}
    score_by_keyword = {r["keyword_id"]: r["scores"] for r in rows}

    plan = AppliedPlan(attempt_id=attempt.id, screening_job_id=job.id,
                       identity_sha256="", capacities=capacities,
                       applied_channels=applied)
    for channel in applied:
        baseline = (adjusted_ordering(rows, channel, relevance_coefficient)
                    if relevance_present
                    else raw_rank_ordering(rows, channel))
        raw_ranks = {r["keyword_id"]: (r["ranks"][channel] or 10 ** 9)
                     for r in rows}
        ens = ensemble_ordering(merged, channel.lower(), raw_rank=raw_ranks,
                                rank_rule=PRODUCTION_SCREENING_CONTRACT[
                                    "rank_rule"])
        b_initial = budget_initial(channel, capacities[channel])
        union = build_union_candidate_plan(
            baseline_ordering=baseline, ensemble_ordering=ens,
            b_initial=b_initial, universe_size=len(rows),
            multiplier=multiplier, mode=channel_union_mode(channel))
        entries = []
        for item in union["selected"]:
            kid = int(item["keyword_id"])
            rel = relevance_by_keyword.get(kid)
            adjusted = (max(score_by_keyword[kid][channel], 0.0)
                        * (rel if rel is not None else DEFAULT_RELEVANCE)
                        * relevance_coefficient)
            entries.append((kid, adjusted, item["initial_rank"],
                            item["origin_source"], item["baseline_rank"],
                            item["screening_rank"],
                            (fit_by_keyword.get(kid) or {}).get(
                                channel.lower()),
                            rel))
        plan.channels[channel] = entries
        plan.budgets[channel] = {"B": b_initial, "T": int(union["target"]),
                                 "U": int(union["union_size"])}

    plan.identity_sha256 = _materialization_identity(
        run=run, attempt=attempt, job=job, rank_sha=rank_sha, rows=rows,
        active=active, applied=applied, capacities=capacities,
        budgets=plan.budgets, relevance_coefficient=relevance_coefficient,
        relevance_present=relevance_present)
    logger.info(
        f"assistive plan hazır: run {run.id} attempt {attempt.id} "
        f"kapsam {list(applied)} "
        + " ".join(f"{ch} U={plan.budgets[ch]['U']}" for ch in applied))
    return plan, None


def _materialization_identity(*, run, attempt, job, rank_sha, rows, active,
                              applied, capacities, budgets,
                              relevance_coefficient,
                              relevance_present) -> str:
    from app.core.screening.identity import (
        candidate_materialization_identity,
        relevance_rows_sha256,
    )

    full_budgets = dict(budgets)
    for channel in active:
        if channel not in full_budgets:
            b = budget_initial(channel, capacities[channel])
            full_budgets[channel] = {"B": b, "T": b, "U": b}
    return candidate_materialization_identity(
        scoring_run_id=int(run.id),
        screening_input_identity_sha256=job.screening_input_identity_sha256,
        channel_rank_snapshot_sha256=rank_sha,
        relevance_rows_sha256=(relevance_rows_sha256(
            {r["keyword_id"]: r["relevance"] for r in rows})
            if relevance_present else None),
        relevance_anchor_version=attempt.requested_anchor_version,
        relevance_coefficient=relevance_coefficient,
        active_channels=list(active), capacities=capacities,
        budgets=full_budgets,
        algorithm_version=getattr(run, "algorithm_version", "v2") or "v2",
        policy_version=attempt.requested_policy_version,
        strategy_version=attempt.requested_strategy_version,
        assignment_version=attempt.requested_assignment_version,
        applied_screening_channels=list(applied))["sha256"]


def audit_rows_for(plan: AppliedPlan, scoring_run_id: int) -> List[Dict]:
    """`is_applied=True` audit satırları (adaylarla AYNI transaction'da).

    Yazım İDEMPOTENTTİR: `PoolBuilder` bu satırları
    `ON CONFLICT (uq_candidate_selection) DO NOTHING` ile ekler; aday
    commit'inden sonra ölen bir worker'ın lease takeover'ı aynı audit'i
    çoğaltamaz (Codex 24. tur #4).
    """
    out = []
    for channel, entries in plan.channels.items():
        budgets = plan.budgets[channel]
        for (kid, adjusted, rank, origin, baseline_rank, screening_rank,
             fit, relevance) in entries:
            out.append({
                "assignment_attempt_id": plan.attempt_id,
                "scoring_run_id": scoring_run_id,
                "screening_job_id": plan.screening_job_id,
                "keyword_id": kid,
                "channel": channel,
                "origin_source": origin,
                "materialization_action": ACTION_INITIAL,
                "materialization_identity_sha256": plan.identity_sha256,
                "baseline_rank": baseline_rank,
                "screening_rank": screening_rank,
                "initial_materialized_rank": rank,
                "screening_fit": fit,
                "relevance_score": relevance,
                "adjusted_score": adjusted,
                "active_channel": True,
                "capacity": plan.capacities.get(channel),
                "b_initial": budgets["B"],
                "t_target": budgets["T"],
                "u_union_size": budgets["U"],
                "is_initial_set": True,
                # ASSISTIVE: canlı havuza UYGULANDI
                "is_applied": True,
            })
    return out


def audit_later_action(db, context: "AppliedContext", *, scoring_run_id: int,
                       channel: str, keyword_id: int, origin: str,
                       action: str, rank, adjusted=None) -> bool:
    """Transfer/expansion adayi icin `is_applied=true` audit satiri.

    Kapsam disi kanal (SOCIAL) audit ALMAZ; ayni anahtar ikinci kez
    yazilmaz (ON CONFLICT DO NOTHING) — takeover satiri cogaltmaz.
    """
    from decimal import Decimal

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.database.models import CorpusCandidateSelection

    if context is None or not context.covers(channel):
        return False
    db.execute(
        pg_insert(CorpusCandidateSelection).values([{
            "assignment_attempt_id": context.attempt_id,
            "scoring_run_id": scoring_run_id,
            "screening_job_id": context.screening_job_id,
            "keyword_id": int(keyword_id),
            "channel": channel,
            "origin_source": origin or "baseline",
            "materialization_action": action,
            "materialization_identity_sha256": context.identity_sha256,
            "initial_materialized_rank": rank,
            "adjusted_score": (None if adjusted is None
                               else Decimal(str(round(float(adjusted), 4)))),
            "active_channel": True,
            "capacity": context.capacities.get(channel),
            "is_initial_set": False,
            "is_applied": True,
        }]).on_conflict_do_nothing(constraint="uq_candidate_selection"))
    return True


__all__ = ["ACTION_INITIAL", "APPLIED_CHANNELS", "AppliedPlan",
           "AssistiveAuditConflict", "AssistiveNotApplicable", "active_channels_of", "audit_rows_for",
           "build_applied_plan", "expected_applied_channels"]
