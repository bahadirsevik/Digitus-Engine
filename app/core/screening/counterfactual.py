# -*- coding: utf-8 -*-
"""Shadow counterfactual materyalizasyonu (plan §7.2).

Shadow'un ÖLÇÜM ALETİ budur: canlı `ChannelCandidate` çarpanı 1 kalırken,
"aynı koşu 3B union sözleşmesiyle hangi adayları seçerdi" sorusu
`CorpusCandidateSelection(is_applied=False)` satırlarına yazılır.

Sözleşme:
- Canlı havuza HİÇBİR yazma yapılmaz; yalnız `is_applied=False` satırlar.
- Kapsam kararı v3'tür: ADS/SEO additive union, SOCIAL `baseline_only`
  (kapsam dışı kanalın aday kimlikleri VE SIRASI flag-off ile birebir).
- Baseline sırası ÜRETİM sırasıdır (`candidate_replay.adjusted_ordering`
  / `raw_rank_ordering`) — burada ikinci bir sıralama kuralı YAZILMAZ.
- Satırlar IMMUTABLE ve idempotenttir: aynı attempt için ikinci çağrı
  yeniden yazmaz, sayıyı doğrular.
- Fail-closed: job completed değilse, kararlar eksikse veya evren
  kararlarla örtüşmüyorsa satır üretilmez.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.benchmark.candidate_replay import (
    DEFAULT_RELEVANCE,
    adjusted_ordering,
    budget_initial,
    raw_rank_ordering,
)
from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    SCOPED_SCREENING_CONTRACT_V3,
    SCREENING_APPLIED_CHANNELS_V3,
    build_union_candidate_plan,
)
from app.core.screening.ensemble import EnsembleResult, ensemble_ordering
from app.core.screening.identity import (
    candidate_materialization_identity,
    channel_rank_snapshot_sha256,
    relevance_rows_sha256,
)
from app.database.models import (
    ChannelAssignmentAttempt,
    CorpusCandidateSelection,
    CorpusScreeningDecision,
    CorpusScreeningJob,
    ScoringRun,
)

CHANNELS = ("ADS", "SEO", "SOCIAL")
ACTION_INITIAL = "initial"


class CounterfactualError(RuntimeError):
    """Ölçüm üretilemedi — satır YAZILMAZ (fail-closed)."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def channel_union_mode(channel: str) -> str:
    """v3 kapsam kararı: kapsam içi additive, kapsam dışı baseline_only."""
    return (SCOPED_SCREENING_CONTRACT_V3["in_scope_mode"]
            if channel in SCREENING_APPLIED_CHANNELS_V3
            else SCOPED_SCREENING_CONTRACT_V3["out_of_scope_mode"])


def _rows_from_job_snapshot(job: CorpusScreeningJob) -> List[Dict[str, Any]]:
    """Ölçüm YALNIZ dispatch anındaki mühürlü satırlardan üretilir.

    Codex 10. tur #5: canlı `KeywordScore`/`KeywordRelevance` okumak,
    parent aynı anda relevance'ı tazelerken sonucu zamanlamaya bağımlı
    kılıyordu. Snapshot'ta skor/rank/relevance yoksa (eski job) ölçüm
    ÜRETİLMEZ — canlı tablodan sessizce tamamlanmaz.
    """
    raw = (job.input_snapshot or {}).get("rows")
    if not isinstance(raw, list) or not raw:
        raise CounterfactualError("SNAPSHOT_MISSING",
                                  f"job {job.id} input_snapshot.rows boş")
    rows = []
    for item in raw:
        scores = item.get("scores")
        ranks = item.get("ranks")
        if not isinstance(scores, dict) or not isinstance(ranks, dict):
            raise CounterfactualError(
                "SNAPSHOT_INCOMPLETE",
                f"job {job.id} snapshot'ında skor/rank yok — ölçüm canlı "
                f"tablodan TAMAMLANMAZ (dispatch anı dondurulmalıydı)")
        missing = [ch for ch in CHANNELS if ch not in scores or ch not in ranks]
        if missing:
            raise CounterfactualError(
                "SNAPSHOT_INCOMPLETE",
                f"job {job.id} snapshot'ında eksik kanal: {missing}")
        rows.append({
            "keyword_id": int(item["keyword_id"]),
            "text": item.get("keyword", ""),
            "scores": {ch: float(scores[ch] or 0.0) for ch in CHANNELS},
            "ranks": {ch: ranks[ch] for ch in CHANNELS},
            "relevance": (None if item.get("relevance") is None
                          else float(item["relevance"])),
        })
    rows.sort(key=lambda r: r["keyword_id"])
    return rows


def _merged_from_decisions(db, job_id: int, salts: List[str]
                           ) -> List[EnsembleResult]:
    """Kararlardan ensemble görünümünü YENİDEN kurar (yeni kural YOK)."""
    by_keyword: Dict[int, Dict[str, Any]] = {}
    rows = (db.query(CorpusScreeningDecision)
            .filter(CorpusScreeningDecision.screening_job_id == job_id)
            .all())
    if not rows:
        raise CounterfactualError("DECISIONS_MISSING",
                                  f"job {job_id} için karar satırı yok")
    for row in rows:
        entry = by_keyword.setdefault(row.keyword_id, {
            "mean_fit": {}, "passing": {}, "disagreement": {},
            "raw_views": {salts[0]: {}, salts[-1]: {}},
            "unresolved": {salts[0]: True, salts[-1]: True},
            "uncertain": True})
        key = row.channel.lower()
        entry["mean_fit"][key] = (None if row.merged_fit is None
                                  else float(row.merged_fit))
        entry["passing"][key] = bool(row.passing)
        entry["disagreement"][key] = bool(row.disagreement)
        entry["raw_views"][salts[0]][key] = (
            None if row.view_a_fit is None else int(row.view_a_fit))
        entry["raw_views"][salts[-1]][key] = (
            None if row.view_b_fit is None else int(row.view_b_fit))
        # Görünüm çözülmüşse (unresolved=False) kelime genelinde çözülmüştür
        entry["unresolved"][salts[0]] &= bool(row.view_a_unresolved)
        entry["unresolved"][salts[-1]] &= bool(row.view_b_unresolved)
        entry["uncertain"] &= bool(row.uncertain)
    merged = []
    for keyword_id, entry in by_keyword.items():
        unresolved_views = [salt for salt, flag in entry["unresolved"].items()
                            if flag]
        merged.append(EnsembleResult(
            keyword_id=keyword_id, keyword="",
            mean_fit=entry["mean_fit"], passing=entry["passing"],
            raw_views=entry["raw_views"],
            context_disagreement=entry["disagreement"],
            uncertain=bool(entry["uncertain"]),
            unresolved_views=unresolved_views))
    merged.sort(key=lambda r: r.keyword_id)
    return merged


def materialize_counterfactual(db, *, job_id: int,
                               attempt_id: Optional[int] = None
                               ) -> Dict[str, Any]:
    """Tamamlanmış shadow taramasının 3B karşı-olgusunu yazar.

    `attempt_id` AÇIKÇA verilmelidir (reuse yolu): job'a bağlı ilk
    attempt'i seçmek, aynı job'ı yeniden kullanan YENİ attempt'in ölçümünü
    eski attempt'e yazılmış sayıp sessizce atlıyordu (canlı koşuda
    yakalandı: attempt 6'nın ölçüm satırı 0, kimliği NULL).
    """
    job = db.get(CorpusScreeningJob, job_id)
    if job is None:
        raise CounterfactualError("JOB_MISSING", f"job {job_id} yok")
    if job.status != "completed":
        raise CounterfactualError(
            "JOB_NOT_COMPLETED",
            f"job {job_id} durumu {job.status!r} — kısmi sonuç "
            f"materyalize EDİLMEZ")
    if attempt_id is not None:
        attempt = db.get(ChannelAssignmentAttempt, attempt_id)
        if attempt is not None and attempt.screening_job_id != job_id:
            raise CounterfactualError(
                "OWNERSHIP_MISMATCH",
                f"attempt {attempt_id} job {attempt.screening_job_id}'e "
                f"bağlı — verilen {job_id}")
    else:
        attempt = (db.query(ChannelAssignmentAttempt)
                   .filter(ChannelAssignmentAttempt.screening_job_id
                           == job_id)
                   .order_by(ChannelAssignmentAttempt.id.desc())
                   .first())
    if attempt is None:
        raise CounterfactualError(
            "ATTEMPT_MISSING", f"job {job_id} bir attempt'e bağlı değil")
    if attempt.scoring_run_id != job.scoring_run_id:
        raise CounterfactualError(
            "OWNERSHIP_MISMATCH",
            f"attempt {attempt.id} run {attempt.scoring_run_id} != job run "
            f"{job.scoring_run_id}")
    run = db.get(ScoringRun, job.scoring_run_id)

    existing = (db.query(CorpusCandidateSelection)
                .filter(CorpusCandidateSelection.assignment_attempt_id
                        == attempt.id,
                        CorpusCandidateSelection.scoring_run_id
                        == run.id).count())

    rows = _rows_from_job_snapshot(job)
    salts = list(job.view_salts or PRODUCTION_SCREENING_CONTRACT["view_salts"])
    merged = _merged_from_decisions(db, job_id, salts)
    universe_ids = {r["keyword_id"] for r in rows}
    decided_ids = {r.keyword_id for r in merged}
    if decided_ids != universe_ids:
        raise CounterfactualError(
            "UNIVERSE_DECISION_MISMATCH",
            f"karar kümesi evrenle örtüşmüyor (yalnız evrende "
            f"{len(universe_ids - decided_ids)}, yalnız kararda "
            f"{len(decided_ids - universe_ids)})")

    rank_sha = channel_rank_snapshot_sha256(
        {ch: {r["keyword_id"]: (r["ranks"][ch] or 10 ** 9) for r in rows}
         for ch in CHANNELS})
    if attempt.channel_rank_snapshot_sha256 \
            and attempt.channel_rank_snapshot_sha256 != rank_sha:
        raise CounterfactualError(
            "RANK_SNAPSHOT_MISMATCH",
            f"snapshot rank mührü attempt'tekiyle uyuşmuyor "
            f"({rank_sha[:12]}... != "
            f"{attempt.channel_rank_snapshot_sha256[:12]}...)")

    coefficient = float(attempt.relevance_coefficient
                        if attempt.relevance_coefficient is not None
                        else (run.default_relevance_coefficient or 1.0))
    relevance_present = all(r["relevance"] is not None for r in rows)
    capacities = {ch: int(getattr(run, f"{ch.lower()}_capacity") or 0)
                  for ch in CHANNELS}
    multiplier = int(attempt.counterfactual_target_multiplier or 3)

    plans: Dict[str, Dict[str, Any]] = {}
    budgets: Dict[str, Dict[str, int]] = {}
    for channel in CHANNELS:
        baseline = (adjusted_ordering(rows, channel, coefficient)
                    if relevance_present
                    else raw_rank_ordering(rows, channel))
        raw_ranks = {r["keyword_id"]: (r["ranks"][channel] or 10 ** 9)
                     for r in rows}
        ens = ensemble_ordering(
            merged, channel.lower(), raw_rank=raw_ranks,
            rank_rule=PRODUCTION_SCREENING_CONTRACT["rank_rule"])
        b_initial = budget_initial(channel, capacities[channel])
        plan = build_union_candidate_plan(
            baseline_ordering=baseline, ensemble_ordering=ens,
            b_initial=b_initial, universe_size=len(rows),
            multiplier=multiplier, mode=channel_union_mode(channel))
        plans[channel] = plan
        budgets[channel] = {"B": b_initial, "T": int(plan["target"]),
                            "U": int(plan["union_size"])}

    identity = candidate_materialization_identity(
        scoring_run_id=int(run.id),
        screening_input_identity_sha256=job.screening_input_identity_sha256,
        channel_rank_snapshot_sha256=rank_sha,
        relevance_rows_sha256=(relevance_rows_sha256(
            {r["keyword_id"]: r["relevance"] for r in rows})
            if relevance_present else None),
        # Codex 11. tur #4: canlı run alanı DEĞİL, dispatch anında
        # dondurulan sürüm (parent sonradan tazelerse kimlik oynamaz)
        relevance_anchor_version=(attempt.requested_anchor_version
                                  if attempt.requested_anchor_version
                                  is not None
                                  else run.relevance_anchor_version),
        relevance_coefficient=coefficient,
        active_channels=CHANNELS, capacities=capacities, budgets=budgets,
        algorithm_version=getattr(run, "algorithm_version", "v2") or "v2",
        policy_version=attempt.requested_policy_version,
        strategy_version=attempt.requested_strategy_version,
        assignment_version=attempt.requested_assignment_version,
        applied_screening_channels=list(
            job.applied_screening_channels or SCREENING_APPLIED_CHANNELS_V3))

    expected = sum(len(plans[ch]["selected"]) for ch in CHANNELS)
    if not existing:
        # Aynı ölçüm BAŞKA bir attempt'te zaten üretilmişse (reuse yolu)
        # 443 satır ÇOĞALTILMAZ: kimlik AYNI olduğu için ölçüm de aynıdır;
        # yeni attempt kimliği damgalanır ve kaynağı raporlanır.
        twin = (db.query(CorpusCandidateSelection)
                .filter(CorpusCandidateSelection.screening_job_id == job_id,
                        CorpusCandidateSelection.materialization_identity_sha256
                        == identity["sha256"])
                .first())
        if twin is not None:
            attempt.materialization_identity_sha256 = identity["sha256"]
            db.commit()
            return {"status": "reused_measurement",
                    "rows": 0,
                    "source_attempt_id": twin.assignment_attempt_id,
                    "materialization_identity_sha256": identity["sha256"],
                    "budgets": budgets}
    if existing:
        # IMMUTABLE: ikinci çağrı yeniden yazmaz — ama tutarlılık YALNIZ
        # satır sayısıyla doğrulanmaz (Codex 10. tur #10): kimlik, sıra ve
        # materyalizasyon SHA'sı da birebir eşleşmeli.
        if existing != expected:
            raise CounterfactualError(
                "SELECTION_COUNT_MISMATCH",
                f"mevcut {existing} satır != beklenen {expected} — "
                f"immutable ölçüm yeniden yazılmaz")
        stored = (db.query(CorpusCandidateSelection)
                  .filter(CorpusCandidateSelection.assignment_attempt_id
                          == attempt.id,
                          CorpusCandidateSelection.scoring_run_id == run.id)
                  .all())
        bad_sha = sorted({r.materialization_identity_sha256 for r in stored}
                         - {identity["sha256"]})
        if bad_sha:
            raise CounterfactualError(
                "SELECTION_IDENTITY_MISMATCH",
                f"mevcut satırların materyalizasyon kimliği farklı "
                f"({[str(x)[:12] for x in bad_sha]} != "
                f"{identity['sha256'][:12]}...)")
        # Codex 11. tur #7: yalnız kimlik+sıra değil, KAYNAK metadata'sı da
        # doğrulanır (origin/baseline/screening rank ve is_applied bozulursa
        # ölçüm sessizce yanlış kalırdı)
        stored_keys = {(r.channel, r.keyword_id,
                        r.initial_materialized_rank, r.origin_source,
                        r.baseline_rank, r.screening_rank,
                        bool(r.is_applied)) for r in stored}
        expected_keys = {(ch, int(item["keyword_id"]), item["initial_rank"],
                          item["origin_source"], item["baseline_rank"],
                          item["screening_rank"], False)
                         for ch in CHANNELS
                         for item in plans[ch]["selected"]}
        if stored_keys != expected_keys:
            raise CounterfactualError(
                "SELECTION_SET_MISMATCH",
                f"mevcut ölçüm satırları beklenen kümeyle örtüşmüyor "
                f"(yalnız DB'de {len(stored_keys - expected_keys)}, yalnız "
                f"planda {len(expected_keys - stored_keys)})")
        return {"status": "already_materialized", "rows": existing,
                "materialization_identity_sha256": identity["sha256"],
                "budgets": budgets}

    fit_by_keyword = {r.keyword_id: r.mean_fit for r in merged}
    relevance_by_keyword = {r["keyword_id"]: r["relevance"] for r in rows}
    score_by_keyword = {r["keyword_id"]: r["scores"] for r in rows}
    for channel in CHANNELS:
        plan = plans[channel]
        for item in plan["selected"]:
            kid = int(item["keyword_id"])
            rel = relevance_by_keyword.get(kid)
            adjusted = (max(score_by_keyword[kid][channel], 0.0)
                        * (rel if rel is not None else DEFAULT_RELEVANCE)
                        * coefficient)
            fit = (fit_by_keyword.get(kid) or {}).get(channel.lower())
            db.add(CorpusCandidateSelection(
                assignment_attempt_id=attempt.id,
                scoring_run_id=run.id,
                screening_job_id=job.id,
                keyword_id=kid,
                channel=channel,
                origin_source=item["origin_source"],
                materialization_action=ACTION_INITIAL,
                materialization_identity_sha256=identity["sha256"],
                baseline_rank=item["baseline_rank"],
                screening_rank=item["screening_rank"],
                initial_materialized_rank=item["initial_rank"],
                screening_fit=fit,
                relevance_score=rel,
                adjusted_score=adjusted,
                active_channel=True,
                capacity=capacities[channel],
                b_initial=budgets[channel]["B"],
                t_target=budgets[channel]["T"],
                u_union_size=budgets[channel]["U"],
                is_initial_set=True,
                # SHADOW: canlı havuza UYGULANMAZ — ölçüm satırı
                is_applied=False))
    attempt.materialization_identity_sha256 = identity["sha256"]
    db.commit()
    return {"status": "materialized", "rows": expected,
            "materialization_identity_sha256": identity["sha256"],
            "budgets": budgets,
            "union_modes": {ch: channel_union_mode(ch) for ch in CHANNELS}}


def counterfactual_summary(db, *, attempt_id: Optional[int] = None,
                           job_id: Optional[int] = None
                           ) -> Optional[Dict[str, Any]]:
    """UI/rapor özeti: kanal başına union boyutu ve kaynak dağılımı.

    `job_id` ile sorgulamak REUSE yolunda da çalışır: ölçüm satırları
    taramayı ÜRETEN attempt'e yazılıdır, yeniden kullanan attempt'e değil.
    """
    query = db.query(CorpusCandidateSelection)
    if attempt_id is not None:
        query = query.filter(
            CorpusCandidateSelection.assignment_attempt_id == attempt_id)
    elif job_id is not None:
        query = query.filter(
            CorpusCandidateSelection.screening_job_id == job_id)
    else:
        raise ValueError("attempt_id veya job_id gerekli")
    rows = query.all()
    if not rows:
        return None
    out: Dict[str, Any] = {}
    for row in rows:
        block = out.setdefault(row.channel, {
            "u_union_size": row.u_union_size, "b_initial": row.b_initial,
            "t_target": row.t_target, "rows": 0,
            "origin": {"baseline": 0, "screening": 0, "both": 0, "none": 0}})
        block["rows"] += 1
        block["origin"][row.origin_source] = \
            block["origin"].get(row.origin_source, 0) + 1
    return out


__all__ = ["CounterfactualError", "channel_union_mode",
           "counterfactual_summary", "materialize_counterfactual"]
