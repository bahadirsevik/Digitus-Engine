# -*- coding: utf-8 -*-
"""`ChannelAssignmentAttempt` yaşam döngüsü (Codex 10. tur #1, #2).

Neden ayrı modül: attempt satırı İKİ bağımsız işin (parent assignment ve
child screening) ortak sahibidir. Yanlış kapatma iki yönde de bozar:

- Erken kapatma: ledger harcamayı YALNIZ `running` sahipte kabul eder
  (`SPENDABLE_OWNER_STATES`); attempt `pending` kalırsa gerçek shadow
  koşusu ilk rezervasyonda `OwnerNotSpendable` ile düşer, parent bitince
  hemen `completed` yapılırsa paralel tarama ortasında kesilir.
- Hiç kapatmama: `uq_assignment_attempt_active_run` partial unique'i run
  başına TEK aktif attempt'e izin verir; kapanmayan satır bir sonraki
  atamayı KALICI olarak bloke eder (off yolunda bile).

Bu yüzden kural: attempt İLK başlayan işle `running` olur, terminal
duruma YALNIZCA her iki taraf da bittiğinde geçer, ve dispatch öncesi
sahipsiz kalmış aktif attempt'ler reconcile edilir.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update

from app.database.models import (
    ChannelAssignmentAttempt,
    CorpusScreeningJob,
    TaskResult,
)

ACTIVE_STATES = ("pending", "running")
# Parent assignment task'ının Celery hard limiti 3600s; lease ondan
# GÜVENLİ MARJLA uzun tutulur (süre dolduysa worker kesin ölmüştür)
ASSIGNMENT_LEASE_SECONDS = 4200
# Ertelenmis parent'in broker yayim penceresi: bu sureden uzun `publishing`
# durumu, yayim yapamadan olmus bir process demektir
PARENT_PUBLISH_LEASE_SECONDS = 300
DEFERRED_PARENT_CONTRACT_VERSION = "DPR-2026-07-31-v1"
TERMINAL_STATES = ("completed", "failed")
JOB_TERMINAL_STATES = ("completed", "failed", "fallback", "not_needed")
TASK_TERMINAL_STATES = ("completed", "failed", "cancelled")
PHASE_SCREENING = "screening"
PHASE_ASSIGNMENT = "assignment"
PHASE_COMPLETED = "completed"


def start_attempt(db, *, attempt_id: Optional[int] = None,
                  parent_task_id: Optional[str] = None,
                  phase: Optional[str] = None) -> bool:
    """`pending -> running` (idempotent).

    Zaten `running` ise no-op'tur ve True döner; terminal attempt YENİDEN
    AÇILMAZ (gecikmiş worker harcama yapamaz).
    """
    query = update(ChannelAssignmentAttempt).where(
        ChannelAssignmentAttempt.status.in_(ACTIVE_STATES))
    if attempt_id is not None:
        query = query.where(ChannelAssignmentAttempt.id == attempt_id)
    elif parent_task_id is not None:
        query = query.where(
            ChannelAssignmentAttempt.parent_task_id == parent_task_id)
    else:
        raise ValueError("attempt_id veya parent_task_id gerekli")
    values = {"status": "running"}
    if phase is not None:
        values["phase"] = phase
    result = db.execute(query.values(**values))
    db.commit()
    return bool(result.rowcount)


class AssignmentNotClaimable(RuntimeError):
    """Parent assignment başka bir worker tarafından çalıştırılıyor."""

    error_code = "ASSIGNMENT_NOT_CLAIMABLE"


def _deferred_parent_sha256(payload: dict) -> str:
    body = {key: value for key, value in payload.items()
            if key != "payload_sha256"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_deferred_parent_payload(
    attempt: ChannelAssignmentAttempt,
    *,
    previous_status: str,
    recompute_relevance: bool,
) -> dict:
    """Build the only persisted representation of a deferred parent call."""
    if not isinstance(recompute_relevance, bool):
        raise TypeError("recompute_relevance bool olmali")
    payload = {
        "contract_version": DEFERRED_PARENT_CONTRACT_VERSION,
        "parent_task_id": attempt.parent_task_id,
        "attempt_id": int(attempt.id),
        "scoring_run_id": int(attempt.scoring_run_id),
        "screening_job_id": int(attempt.screening_job_id),
        "previous_status": str(previous_status),
        "recompute_relevance": recompute_relevance,
    }
    payload["payload_sha256"] = _deferred_parent_sha256(payload)
    return payload


def _validated_parent_dispatch(attempt: ChannelAssignmentAttempt,
                               payload: dict) -> Optional[dict]:
    """Validate the persisted payload and rebuild executable args from DB.

    The manifest never supplies arbitrary Celery ``args``/``kwargs``. Those
    values are reconstructed from the locked attempt row, so a corrupted
    manifest cannot dispatch another run or silently alter frozen versions.
    """
    if not isinstance(payload, dict):
        return None
    expected_keys = {
        "contract_version", "parent_task_id", "attempt_id",
        "scoring_run_id", "screening_job_id", "previous_status",
        "recompute_relevance", "payload_sha256",
    }
    if set(payload) != expected_keys:
        return None
    if payload.get("contract_version") != DEFERRED_PARENT_CONTRACT_VERSION:
        return None
    supplied_sha = payload.get("payload_sha256")
    if not isinstance(supplied_sha, str) or re.fullmatch(
            r"[0-9a-f]{64}", supplied_sha) is None:
        return None
    try:
        expected_sha = _deferred_parent_sha256(payload)
    except (TypeError, ValueError):
        return None
    if supplied_sha != expected_sha:
        return None
    if not isinstance(payload.get("recompute_relevance"), bool):
        return None
    for field in ("attempt_id", "scoring_run_id", "screening_job_id"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
    if not isinstance(payload.get("parent_task_id"), str) \
            or not payload["parent_task_id"]:
        return None
    if (
        payload.get("parent_task_id") != attempt.parent_task_id
        or payload.get("attempt_id") != attempt.id
        or payload.get("scoring_run_id") != attempt.scoring_run_id
        or payload.get("screening_job_id") != attempt.screening_job_id
    ):
        return None
    manifest_recompute = (attempt.manifest or {}).get("recompute_relevance")
    if not isinstance(manifest_recompute, bool):
        return None
    if payload["recompute_relevance"] != manifest_recompute:
        return None
    if attempt.relevance_coefficient is None:
        return None
    previous_status = payload.get("previous_status")
    if not isinstance(previous_status, str) or not previous_status:
        return None
    return {
        "args": [
            int(attempt.scoring_run_id),
            float(attempt.relevance_coefficient),
        ],
        "kwargs": {
            "requested_policy_version": attempt.requested_policy_version,
            "requested_anchor_version": attempt.requested_anchor_version,
            "recompute_relevance": manifest_recompute,
            "requested_strategy_version": attempt.requested_strategy_version,
        },
        "previous_status": previous_status,
    }


def claim_parent_release(db, *, payload: dict) -> Optional[dict]:
    """Ertelenmis parent atamasini yayimlama hakkini TEK sahibe verir.

    FAIL-CLOSED (Codex 27. tur #2): attempt YOKSA ya da payload attempt ile
    BIREBIR eslesmiyorsa parent HIC kuyruga verilmez — bozuk/yabanci payload
    muhasebesiz bir Gemini hatti baslatamaz. Dogrulananlar:
      - attempt var ve terminal degil
      - parent_task_id / scoring_run_id / screening_job_id birebir ayni
      - mod assistive (ertelenen yol yalniz assistive'dir)
      - screening job TERMINAL (tarama bitmeden parent salinmaz)

    Kayip-dispatch penceresi (Codex 27. tur #3): durum once `publishing`
    olur; broker yayimi BASARILI olunca `mark_parent_published` ile `sent`
    yazilir. Process arada olurse lease suresi dolunca reconciler AYNI
    task ID ile yeniden yayimlayabilir.
    """
    attempt_id = payload.get("attempt_id") if isinstance(payload, dict) else None
    attempt = (db.query(ChannelAssignmentAttempt)
               .filter(ChannelAssignmentAttempt.id == attempt_id)
               .with_for_update()
               .first())
    if attempt is None:
        return None
    dispatch = _validated_parent_dispatch(attempt, payload)
    if dispatch is None:
        return None
    if attempt.status in TERMINAL_STATES:
        return None
    if attempt.screening_mode != "assistive":
        return None
    job = db.get(CorpusScreeningJob, attempt.screening_job_id)
    if job is None or job.status not in JOB_TERMINAL_STATES:
        return None
    state = attempt.assignment_dispatch_state
    if state == "publishing":
        last = attempt.dispatch_last_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and (datetime.now(timezone.utc)
                                 - last).total_seconds() \
                < PARENT_PUBLISH_LEASE_SECONDS:
            return None           # baska bir yayimci canli
    elif state != "pending":
        return None               # sent/started/finished: is bitmis
    attempt.assignment_dispatch_state = "publishing"
    attempt.dispatch_attempts = int(attempt.dispatch_attempts or 0) + 1
    attempt.dispatch_last_at = datetime.now(timezone.utc)
    db.commit()
    return dispatch


def mark_parent_published(db, *, attempt_id: int) -> None:
    """Broker yayimi BASARILI: `publishing -> sent`."""
    db.execute(
        update(ChannelAssignmentAttempt)
        .where(ChannelAssignmentAttempt.id == attempt_id,
               ChannelAssignmentAttempt.assignment_dispatch_state
               == "publishing")
        .values(assignment_dispatch_state="sent",
                dispatch_last_at=datetime.now(timezone.utc)))
    db.commit()


def stale_parent_payloads(db, scoring_run_id: int) -> list:
    """Lease'i dolmus `publishing` attempt'lerin kalici payload'lari.

    Yayim penceresinde olen process'in parent'i asla kuyruga girmemis
    olur; reconciler bunlari AYNI task ID ile yeniden yayimlayabilir.
    """
    out = []
    rows = (db.query(ChannelAssignmentAttempt)
            .filter(ChannelAssignmentAttempt.scoring_run_id == scoring_run_id,
                    ChannelAssignmentAttempt.status.in_(ACTIVE_STATES),
                    ChannelAssignmentAttempt.assignment_dispatch_state
                    == "publishing")
            .all())
    now = datetime.now(timezone.utc)
    for attempt in rows:
        last = attempt.dispatch_last_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and (now - last).total_seconds() \
                < PARENT_PUBLISH_LEASE_SECONDS:
            continue
        payload = (attempt.manifest or {}).get("deferred_parent")
        if payload:
            # SALT OKUR: durum degistirmez — yayim hakki yine
            # `claim_parent_release` CAS'inden alinir (tek kazanan)
            out.append(payload)
    return out


def claim_assignment(db, *, parent_task_id: str) -> bool:
    """Parent pipeline için TEK SAHİPLİ claim (Codex 11. tur #2).

    Screening child'ı execution lease ile korunuyordu; parent yalnız
    idempotent `start_attempt()` çağırdığı için aynı Celery task'ının iki
    teslimi de kanal pipeline'ını çalıştırabiliyordu. Burada attempt satırı
    kilitlenir ve `assignment_dispatch_state` CAS'i uygulanır:

      pending|sent -> started (tek kazanan)
      started      -> yalnız lease SÜRESİ DOLMUŞSA devralınır

    Attempt satırı YOKSA (bu değişiklikten önce dispatch edilmiş koşular
    ve doğrudan task çağıran testler) True döner — eski davranış korunur.
    """
    attempt = (db.query(ChannelAssignmentAttempt)
               .filter(ChannelAssignmentAttempt.parent_task_id
                       == parent_task_id)
               .with_for_update()
               .first())
    if attempt is None:
        return True
    if attempt.status in TERMINAL_STATES:
        raise AssignmentNotClaimable(
            f"attempt {attempt.id} terminal ({attempt.status}) — gecikmiş "
            f"teslim pipeline'ı YENİDEN çalıştıramaz")
    now = datetime.now(timezone.utc)
    if attempt.assignment_dispatch_state == "started":
        last = attempt.dispatch_last_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and (now - last).total_seconds() \
                < ASSIGNMENT_LEASE_SECONDS:
            raise AssignmentNotClaimable(
                f"attempt {attempt.id} için CANLI assignment lease var "
                f"(son işaret {last}) — mükerrer çalıştırma reddedildi")
    attempt.assignment_dispatch_state = "started"
    attempt.dispatch_last_at = now
    attempt.dispatch_attempts = int(attempt.dispatch_attempts or 0) + 1
    attempt.status = "running"
    attempt.phase = PHASE_ASSIGNMENT
    db.commit()
    return True


def _attempt_for(db, attempt_id, parent_task_id):
    query = db.query(ChannelAssignmentAttempt)
    if attempt_id is not None:
        return query.filter(ChannelAssignmentAttempt.id == attempt_id).first()
    return query.filter(
        ChannelAssignmentAttempt.parent_task_id == parent_task_id).first()


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parent_is_dead(task, now) -> bool:
    """Hard-kill sonrası `running` kalmış parent (Codex 11. tur #3).

    Celery hard limit'i aşan yaştaki aktif task, worker öldüğü için
    ASLA terminal olmayacaktır — yaş bunun tek güvenilir kanıtıdır.
    """
    stamp = _aware(getattr(task, "started_at", None)) or _aware(
        getattr(task, "created_at", None))
    if stamp is None:
        return False
    return (now - stamp).total_seconds() >= ASSIGNMENT_LEASE_SECONDS


def _child_is_dead(job, now) -> bool:
    """Execution lease'i dolmuş `running` screening job'ı."""
    expires = _aware(getattr(job, "execution_lease_expires_at", None))
    if expires is not None:
        return expires <= now
    stamp = _aware(getattr(job, "started_at", None)) or _aware(
        getattr(job, "created_at", None))
    if stamp is None:
        return False
    return (now - stamp).total_seconds() >= ASSIGNMENT_LEASE_SECONDS


def _side_states(db, attempt: ChannelAssignmentAttempt, *,
                 finalize_dead: bool = False):
    """(parent bitti mi, parent başarılı mı, child bitti mi, child sağlam mı).

    `finalize_dead=True` (reconciler): hard-kill sonrası `running` kalmış
    parent/child, yaş ve lease kanıtıyla ÖLÜ sayılır ve tipli olarak
    kapatılır — aksi halde run sonsuza dek "aktif" görünür.
    """
    now = datetime.now(timezone.utc)
    parent = (db.query(TaskResult)
              .filter(TaskResult.task_id == attempt.parent_task_id)
              .first())
    parent_done = parent is None or parent.status in TASK_TERMINAL_STATES
    parent_ok = parent is not None and parent.status == "completed"
    if not parent_done and finalize_dead and _parent_is_dead(parent, now):
        parent.status = "failed"
        parent.error_message = (
            "worker hard-kill sonrası stale — reconciler kapattı")
        parent_done, parent_ok = True, False
    if attempt.screening_job_id is None:
        return parent_done, parent_ok, True, True
    job = db.get(CorpusScreeningJob, attempt.screening_job_id)
    child_done = job is None or job.status in JOB_TERMINAL_STATES
    child_ok = job is not None and job.status in ("completed", "not_needed")
    if job is not None and not child_done and finalize_dead \
            and _child_is_dead(job, now):
        job.status = "fallback"
        job.error_code = "SCREENING_STALE_RECONCILED"
        job.error_message = "execution lease doldu — reconciler kapattı"
        job.completed_at = now
        # Codex 12. tur #5: child TaskResult da terminal yapılır, aksi
        # halde progress kaydı sonsuza dek `running` görünür
        if job.task_id:
            child = (db.query(TaskResult)
                     .filter(TaskResult.task_id == job.task_id).first())
            if child is not None and child.status in ("pending", "running"):
                child.status = "completed"
                child.progress = 100
                child.error_message = (
                    "execution lease doldu — reconciler kapattı")
                child.result_data = {
                    **(child.result_data or {}),
                    "status": "fallback",
                    "reason": "SCREENING_STALE_RECONCILED"}
        child_done, child_ok = True, False
    return parent_done, parent_ok, child_done, child_ok


def try_finish_attempt(db, *, attempt_id: Optional[int] = None,
                       parent_task_id: Optional[str] = None,
                       error_code: Optional[str] = None,
                       error_message: Optional[str] = None) -> Optional[str]:
    """Her İKİ taraf da bittiyse attempt'i kapatır; değilse dokunmaz.

    Dönen değer: yeni terminal durum ya da None (hâlâ aktif iş var).
    Shadow'da parent önce bitse bile attempt `running` kalır — aksi halde
    ledger paralel taramanın ortasında harcamayı reddederdi.
    """
    attempt = _attempt_for(db, attempt_id, parent_task_id)
    if attempt is None:
        return None
    if attempt.status in TERMINAL_STATES:
        return attempt.status
    parent_done, parent_ok, child_done, child_ok = _side_states(db, attempt)
    if not (parent_done and child_done):
        return None
    status = "completed" if (parent_ok and child_ok) else "failed"
    attempt.status = status
    attempt.phase = PHASE_COMPLETED
    attempt.completed_at = datetime.now(timezone.utc)
    if status == "failed":
        attempt.error_code = attempt.error_code or (
            error_code or ("ASSIGNMENT_FAILED" if not parent_ok
                           else "SCREENING_NOT_APPLIED"))
        attempt.error_message = attempt.error_message or (error_message or "")
    if attempt.assignment_dispatch_state != "finished":
        attempt.assignment_dispatch_state = "finished"
    db.commit()
    return status


def reconcile_stale_attempts(db, scoring_run_id: int) -> int:
    """Sahipsiz kalmış aktif attempt'leri kapatır (dispatch ön adımı).

    Aktif attempt = `pending|running`. Parent task'i terminal/yok VE (varsa)
    screening job'ı terminal/yok ise iş bitmiştir; satır açık kalırsa
    partial unique run'ı kalıcı kilitler.
    """
    closed = 0
    touched = False
    rows = (db.query(ChannelAssignmentAttempt)
            .filter(ChannelAssignmentAttempt.scoring_run_id == scoring_run_id,
                    ChannelAssignmentAttempt.status.in_(ACTIVE_STATES))
            .all())
    for attempt in rows:
        parent_done, parent_ok, child_done, child_ok = _side_states(
            db, attempt, finalize_dead=True)
        if not (parent_done and child_done):
            # Yan düzeltmeler (ölü child kapatma) attempt hâlâ aktif olsa
            # da KAYBOLMAZ — commit aşağıda yapılır (Codex 12. tur #5)
            touched = True
            continue
        attempt.status = "completed" if (parent_ok and child_ok) else "failed"
        attempt.phase = PHASE_COMPLETED
        attempt.completed_at = datetime.now(timezone.utc)
        if attempt.status == "failed" and not attempt.error_code:
            attempt.error_code = "STALE_ATTEMPT_RECONCILED"
            attempt.error_message = (
                "dispatch öncesi sahipsiz aktif attempt kapatıldı")
        attempt.assignment_dispatch_state = "finished"
        _restore_run_status(db, attempt, ok=(parent_ok and child_ok))
        closed += 1
    if closed or touched:
        db.commit()
    return closed


def _restore_run_status(db, attempt, *, ok: bool) -> None:
    """Stale finalizasyonda `ScoringRun` da kurtarılır (Codex 12. tur #3).

    Reconciler task/attempt'i kapatıp run'ı `channel_assigning` bırakırsa
    bir sonraki dispatch state machine'e takılır ve kullanıcı çıkmaza
    girerdi. Başarısız kapanışta run, atama ÖNCESİ duruma (`scored`)
    döndürülür — CAS ile, yalnız hâlâ `channel_assigning` ise.
    """
    if ok:
        return
    from sqlalchemy import update as _update

    from app.database.models import ScoringRun

    db.execute(
        _update(ScoringRun)
        .where(ScoringRun.id == attempt.scoring_run_id,
               ScoringRun.status == "channel_assigning")
        .values(status="scored"))


def active_attempt(db, scoring_run_id: int
                   ) -> Optional[ChannelAssignmentAttempt]:
    return (db.query(ChannelAssignmentAttempt)
            .filter(ChannelAssignmentAttempt.scoring_run_id == scoring_run_id,
                    ChannelAssignmentAttempt.status.in_(ACTIVE_STATES))
            .order_by(ChannelAssignmentAttempt.id.desc())
            .first())


__all__ = ["ACTIVE_STATES", "ASSIGNMENT_LEASE_SECONDS",
           "AssignmentNotClaimable", "DEFERRED_PARENT_CONTRACT_VERSION",
           "TERMINAL_STATES", "active_attempt",
           "build_deferred_parent_payload", "claim_assignment",
           "claim_parent_release", "reconcile_stale_attempts",
           "start_attempt", "try_finish_attempt"]
