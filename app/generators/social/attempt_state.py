# -*- coding: utf-8 -*-
"""SocialGenerationAttempt yaşam döngüsü, lease, heartbeat ve reconciliation yardımcıları.

Bu modül Celery ve HTTP framework'lerinden bağımsızdır.
Tüm public helper'lar commit/rollback ÇAĞIRMAZ; transaction yönetimi çağıran katmana aittir.

GLOBAL KİLİT SIRASI INVARIANT (CANONICAL GLOBAL LOCK ORDER):
ScoringRun, SocialBrief ve SocialGenerationAttempt satırlarının birlikte kilitlendiği
bütün sosyal üretim ve kanal atama yollarında deterministik olarak şu sıra izlenmelidir:

    ScoringRun (FOR UPDATE)
      -> SocialBrief (FOR UPDATE)
        -> SocialGenerationAttempt (FOR UPDATE)

Bu sıralama, `begin_channel_assignment` ve `_mark_run_outputs_stale` (ScoringRun -> SocialBrief)
ile sosyal worker / attempt akışları arasındaki tüm deadlock döngülerini matematiksel olarak
engeller. Hiçbir helper içinde Brief/Attempt kilidi alındıktan sonra ScoringRun kilidi talep
edilemez.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.database.models import (
    ScoringRun,
    SocialBrief,
    SocialGenerationAttempt,
)

ACTIVE_ATTEMPT_STATUSES = ("pending", "running")
TERMINAL_ATTEMPT_STATUSES = ("completed", "partial", "failed")
ALLOWED_STAGES = ("categories", "ideas", "ideas_retry", "contents")

# Sosyal içerik Celery hard limit'i 1200 saniyedir; lease güvenli marjla 1500 saniye seçilmiştir.
SOCIAL_ATTEMPT_LEASE_SECONDS = 1500
_WORKER_OWNERSHIP_ERROR_MESSAGE: str = "Worker sahipliği doğrulanamadı."


# ==================== DOMAIN EXCEPTIONS ====================


class SocialAttemptError(Exception):
    """Sosyal generation attempt domain hataları için temel sınıf."""

    error_code = "SOCIAL_ATTEMPT_ERROR"

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code


class AttemptConflictError(SocialAttemptError):
    """Aynı brief/stage için zaten aktif bir attempt bulunuyor."""

    error_code = "ATTEMPT_CONFLICT"


class AttemptNotClaimableError(SocialAttemptError):
    """Terminal, süresi dolmuş veya başka worker tarafından sahiplenilmiş attempt claim edilemez."""

    error_code = "ATTEMPT_NOT_CLAIMABLE"


class AttemptNotWritableError(SocialAttemptError):
    """Geç worker, terminal attempt, stale brief veya assignment-version uyuşmazlığı nedeniyle çıktı yazılamaz."""

    error_code = "ATTEMPT_NOT_WRITABLE"


class AttemptNotFoundError(SocialAttemptError):
    """Attempt bulunamadı."""

    error_code = "ATTEMPT_NOT_FOUND"


class BriefNotFoundError(SocialAttemptError):
    """SocialBrief bulunamadı."""

    error_code = "BRIEF_NOT_FOUND"


class InvalidStageError(SocialAttemptError):
    """Bilinmeyen veya geçersiz attempt aşaması."""

    error_code = "INVALID_STAGE"


# ==================== INTERNAL HELPERS ====================


def _utcnow(now: datetime | None = None) -> datetime:
    """Timezone-aware UTC datetime döndürür.

    Enjekte edilen `now` naive ise sessizce UTC varsayılmaz; ValueError fırlatılır.
    """
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    """DB sürücüsünden gelen datetime nesnesini timezone-aware UTC yapar."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_id_list(ids: Any) -> list[int] | None:
    """ID listelerini normalize eder:
    - None ise None döner.
    - Yalnızca pozitif int kabul edilir; bool kesinlikle reddedilir.
    - Duplicate'ler temizlenir.
    - Deterministik artan sırada döndürülür.
    - Boş sequence [] için [] döner.
    """
    if ids is None:
        return None
    if not isinstance(ids, (list, tuple, set)):
        raise ValueError("ID listesi bir sequence (list/tuple/set) olmalıdır.")

    clean_ids: set[int] = set()
    for item in ids:
        # Python'da isinstance(True, int) True döndüğü için bool açıkça filtrelenir
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"Geçersiz ID: {item!r} pozitif integer olmalıdır.")
        if item <= 0:
            raise ValueError(f"ID pozitif integer olmalıdır: {item}")
        clean_ids.add(item)
    return sorted(clean_ids)


def _pre_read_context_for_attempt(db: Session, attempt_id: int) -> tuple[int, int]:
    """Kilitsiz ön okuma: kimlik keşfi amacıyla attempt.brief_id ve brief.scoring_run_id okur."""
    attempt_row = (
        db.query(SocialGenerationAttempt.id, SocialGenerationAttempt.brief_id)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .first()
    )
    if attempt_row is None:
        raise AttemptNotFoundError(
            f"Attempt bulunamadı: {attempt_id}", error_code="ATTEMPT_NOT_FOUND"
        )
    brief_id = attempt_row.brief_id

    brief_row = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == brief_id)
        .first()
    )
    if brief_row is None:
        raise BriefNotFoundError(
            f"SocialBrief bulunamadı: {brief_id}", error_code="BRIEF_NOT_FOUND"
        )
    scoring_run_id = brief_row.scoring_run_id

    return brief_id, scoring_run_id


def _lock_context_for_attempt(
    db: Session,
    attempt_id: int,
    *,
    lock_scoring_run: bool = True,
) -> tuple[SocialGenerationAttempt, SocialBrief, ScoringRun | None]:
    """Global kilit sırasını (ScoringRun -> SocialBrief -> SocialGenerationAttempt) garanti eden yardımcı.

    1. Kilitsiz ön okuma ile brief_id ve scoring_run_id bulunur.
    2. Global kilit sırasında FOR UPDATE alınır:
       - ScoringRun FOR UPDATE (varsa)
       - SocialBrief FOR UPDATE
       - SocialGenerationAttempt FOR UPDATE
    3. Kilitler altında ilişkiler yeniden doğrulanır.
    4. populate_existing() ile güncel DB değerleri identity map'e yüklenir.
    """
    pre_brief_id, pre_run_id = _pre_read_context_for_attempt(db, attempt_id)

    # 1. ScoringRun FOR UPDATE kilidi
    scoring_run = None
    if lock_scoring_run:
        scoring_run = (
            db.query(ScoringRun)
            .filter(ScoringRun.id == pre_run_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if scoring_run is None:
            raise AttemptNotWritableError(
                "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
            )

    # 2. SocialBrief FOR UPDATE kilidi
    brief = (
        db.query(SocialBrief)
        .filter(SocialBrief.id == pre_brief_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if brief is None:
        raise BriefNotFoundError(
            f"SocialBrief bulunamadı: {pre_brief_id}", error_code="BRIEF_NOT_FOUND"
        )

    # 3. SocialGenerationAttempt FOR UPDATE kilidi
    attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if attempt is None:
        raise AttemptNotFoundError(
            f"Attempt bulunamadı: {attempt_id}", error_code="ATTEMPT_NOT_FOUND"
        )

    # Kilitler altında ilişki doğrulama
    if brief.scoring_run_id != pre_run_id:
        raise AttemptNotWritableError(
            "Kilit alma sırasında brief-run ilişkisi değişti.",
            error_code="RELATION_CHANGED",
        )
    if attempt.brief_id != brief.id:
        raise AttemptNotWritableError(
            "Kilit alma sırasında attempt-brief ilişkisi değişti.",
            error_code="RELATION_CHANGED",
        )

    return attempt, brief, scoring_run


# ==================== PUBLIC LIFECYCLE HELPERS ====================


def reconcile_expired_attempts(
    db: Session,
    *,
    brief_id: int,
    now: datetime | None = None,
) -> int:
    """Süresi dolmuş pending ve running attempt'leri failed/worker_lost durumuna çeker.

    - Yalnız verilen brief'in aktif (pending, running) attempt'lerini inceler.
    - Satırları güvenli FOR UPDATE ile kilitler.
    - lease_expires_at <= now olan attempt'leri worker_lost yapar.
    - Commit yapmaz, flush edebilir.
    """
    current_time = _utcnow(now)

    active_attempts = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
        )
        .populate_existing()
        .with_for_update()
        .all()
    )

    reconciled_count = 0
    for attempt in active_attempts:
        exp = _to_utc(attempt.lease_expires_at)
        if exp is not None and exp <= current_time:
            attempt.status = "failed"
            attempt.reason_code = "worker_lost"
            attempt.error_message = (
                f"Attempt lease expired at {exp.isoformat()} (worker lost)."
            )
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            reconciled_count += 1

    if reconciled_count > 0:
        db.flush()

    return reconciled_count


def create_or_get_attempt(
    db: Session,
    *,
    brief_id: int,
    stage: str,
    idempotency_key: str,
    requested_target_ids: Sequence[int] | None = None,
    requested_idea_ids: Sequence[int] | None = None,
    now: datetime | None = None,
) -> tuple[SocialGenerationAttempt, bool]:
    """Idempotent attempt oluşturur veya mevcut olanı döndürür.

    İşlem sırası:
    1. stage allowlist kontrol edilir.
    2. Kilitsiz ön okuma ile brief.scoring_run_id bulunur.
    3. Global kilit sırasında: ScoringRun FOR UPDATE -> SocialBrief FOR UPDATE.
    4. Kilit altında: brief-run ilişki tutarlılığı doğrulanır.
    5. Bu brief'in süresi dolmuş aktif attempt'leri RECONCILE edilir.
    6. Aynı (brief_id, stage, idempotency_key) attempt'i aranır; varsa durumundan bağımsız
       aynı satır döner (eğer süresi dolmuşsa az önce failed/worker_lost yapılmıştır).
    7. Yeni attempt oluşturulacaksa: brief.is_stale == False ve assignment_version doğrulanır.
    8. Aynı stage için başka aktif attempt kontrolü yapılır (varsa AttemptConflictError).
    9. Yeni pending attempt oluşturulur ve lease atanır.
    Commit yapmaz.
    """
    if stage not in ALLOWED_STAGES:
        raise InvalidStageError(
            f"Geçersiz stage: {stage!r}. İzin verilenler: {ALLOWED_STAGES}",
            error_code="INVALID_STAGE",
        )

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("idempotency_key boş olamaz.")

    norm_target_ids = normalize_id_list(requested_target_ids)
    norm_idea_ids = normalize_id_list(requested_idea_ids)
    current_time = _utcnow(now)

    # 1. Kilitsiz ön okuma ile scoring_run_id keşfi
    brief_meta = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == brief_id)
        .first()
    )
    if brief_meta is None:
        raise BriefNotFoundError(
            f"SocialBrief bulunamadı: {brief_id}", error_code="BRIEF_NOT_FOUND"
        )
    pre_run_id = brief_meta.scoring_run_id

    # 2. Global kilit sırası: ScoringRun FOR UPDATE -> SocialBrief FOR UPDATE
    scoring_run = (
        db.query(ScoringRun)
        .filter(ScoringRun.id == pre_run_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if scoring_run is None:
        raise AttemptNotWritableError(
            "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
        )

    brief = (
        db.query(SocialBrief)
        .filter(SocialBrief.id == brief_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if brief is None:
        raise BriefNotFoundError(
            f"SocialBrief bulunamadı: {brief_id}", error_code="BRIEF_NOT_FOUND"
        )

    if brief.scoring_run_id != pre_run_id:
        raise AttemptNotWritableError(
            "Kilit alma sırasında brief-run ilişkisi değişti.",
            error_code="RELATION_CHANGED",
        )

    # 3. Süresi dolmuş aktif attempt'leri reconcile et (SocialBrief kilitliyken)
    reconcile_expired_attempts(db, brief_id=brief_id, now=current_time)

    # 4. Aynı (brief_id, stage, idempotency_key) ara (same-key idempotency)
    existing = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == stage,
            SocialGenerationAttempt.idempotency_key == idempotency_key,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    # 5. Yeni attempt oluşturmadan önce kilit altında stale ve version kontrolü
    if brief.is_stale:
        raise AttemptNotWritableError(
            f"Brief {brief_id} stale durumda; yeni attempt oluşturulamaz.",
            error_code="BRIEF_STALE",
        )
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise AttemptNotWritableError(
            f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
            f"run_version={scoring_run.channel_assignment_version}; yeni attempt oluşturulamaz.",
            error_code="ASSIGNMENT_CHANGED",
        )

    # 6. Hâlâ aktif (pending/running) attempt var mı kontrolü
    active_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.brief_id == brief_id,
            SocialGenerationAttempt.stage == stage,
            SocialGenerationAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if active_attempt is not None:
        raise AttemptConflictError(
            f"Brief {brief_id} ve stage '{stage}' için zaten aktif bir attempt bulunuyor "
            f"(attempt_id={active_attempt.id}, status={active_attempt.status}).",
            error_code="ATTEMPT_CONFLICT",
        )

    # 7. Yeni pending attempt oluştur
    attempt = SocialGenerationAttempt(
        brief_id=brief_id,
        stage=stage,
        idempotency_key=idempotency_key,
        status="pending",
        requested_target_ids=norm_target_ids,
        requested_idea_ids=norm_idea_ids,
        heartbeat_at=current_time,
        lease_expires_at=current_time + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS),
        created_at=current_time,
    )
    db.add(attempt)
    db.flush()
    return attempt, True


def claim_attempt(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> SocialGenerationAttempt:
    """Pending attempt'i belirli bir task_id için 'running' durumuna çeker.

    - Boş task_id reddedilir.
    - Attempt bulunamazsa AttemptNotFoundError.
    - Terminal attempt yeniden açılamaz (AttemptNotClaimableError).
    - Süresi dolmuşsa failed/worker_lost yapılarak reddedilir.
    - Zaten running ve aynı task_id ise idempotent başarı.
    - Zaten running ve farklı task_id ise AttemptNotClaimableError.
    - Commit yapmaz.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    current_time = _utcnow(now)

    attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if attempt is None:
        raise AttemptNotFoundError(
            f"Attempt bulunamadı: {attempt_id}", error_code="ATTEMPT_NOT_FOUND"
        )

    if attempt.status in TERMINAL_ATTEMPT_STATUSES:
        raise AttemptNotClaimableError(
            f"Terminal durumdaki attempt ({attempt.status}) claim edilemez.",
            error_code="ATTEMPT_NOT_CLAIMABLE",
        )

    # Lease kontrolü
    exp = _to_utc(attempt.lease_expires_at)
    if exp is not None and exp <= current_time:
        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.error_message = (
            f"Attempt lease expired at {exp.isoformat()} before claim."
        )
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        db.flush()
        raise AttemptNotClaimableError(
            f"Attempt {attempt_id} lease süresi dolmuş ve worker_lost olarak kapatıldı.",
            error_code="LEASE_EXPIRED",
        )

    if attempt.status == "running":
        if attempt.task_id == task_id:
            return attempt
        raise AttemptNotClaimableError(
            _WORKER_OWNERSHIP_ERROR_MESSAGE,
            error_code="ATTEMPT_NOT_CLAIMABLE",
        )

    # pending -> running
    attempt.status = "running"
    attempt.task_id = task_id
    attempt.started_at = current_time
    attempt.heartbeat_at = current_time
    attempt.lease_expires_at = current_time + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS)
    db.flush()
    return attempt


def heartbeat_attempt(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> SocialGenerationAttempt:
    """Running attempt için lease süresini ve heartbeat_at zamanını ileri taşır.

    - Yalnızca running ve aynı task_id sahibi attempt heartbeat alabilir.
    - Lease zaten dolmuşsa failed/worker_lost yapılır ve heartbeat reddedilir.
    - Commit yapmaz.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    current_time = _utcnow(now)

    attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if attempt is None:
        raise AttemptNotFoundError(
            f"Attempt bulunamadı: {attempt_id}", error_code="ATTEMPT_NOT_FOUND"
        )

    if attempt.status != "running":
        raise AttemptNotClaimableError(
            f"Attempt {attempt_id} 'running' durumunda değil (durum: {attempt.status}).",
            error_code="ATTEMPT_NOT_CLAIMABLE",
        )

    if attempt.task_id != task_id:
        raise AttemptNotClaimableError(
            _WORKER_OWNERSHIP_ERROR_MESSAGE,
            error_code="ATTEMPT_NOT_CLAIMABLE",
        )

    exp = _to_utc(attempt.lease_expires_at)
    if exp is not None and exp <= current_time:
        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.error_message = (
            f"Attempt lease expired at {exp.isoformat()} before heartbeat."
        )
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        db.flush()
        raise AttemptNotClaimableError(
            f"Attempt {attempt_id} lease süresi dolmuş; heartbeat kabul edilemez.",
            error_code="LEASE_EXPIRED",
        )

    attempt.heartbeat_at = current_time
    attempt.lease_expires_at = current_time + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS)
    db.flush()
    return attempt


def lock_attempt_for_write(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> SocialGenerationAttempt:
    """Geç worker'ın çıktı kaydetmesini engelleyen merkezi fail-closed guard.

    Global kilit sırasıyla (ScoringRun -> SocialBrief -> SocialGenerationAttempt):
    - Önce kilitsiz attempt.brief_id ve brief.scoring_run_id okunur.
    - ScoringRun FOR UPDATE ile kilitlenir.
    - SocialBrief FOR UPDATE ile kilitlenir.
    - SocialGenerationAttempt FOR UPDATE ile kilitlenir.
    - Attempt 'running' olmalı ve task_id eşleşmeli.
    - Lease henüz dolmamış olmalı (dolmuşsa failed/worker_lost).
    - Brief stale olmamalı (stale ise failed/brief_stale).
    - brief.channel_assignment_version == scoring_run.channel_assignment_version olmalı
      (farklıysa failed/assignment_changed).
    - Başarısızlıkta AttemptNotWritableError fırlatır.
    - Commit yapmaz.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    current_time = _utcnow(now)

    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )

    if attempt.status != "running":
        raise AttemptNotWritableError(
            f"Attempt 'running' durumunda değil (mevcut: {attempt.status}).",
            error_code="ATTEMPT_NOT_RUNNING",
        )

    if attempt.task_id != task_id:
        raise AttemptNotWritableError(
            _WORKER_OWNERSHIP_ERROR_MESSAGE,
            error_code="TASK_MISMATCH",
        )

    # 1. Lease süresi dolmuş mu?
    exp = _to_utc(attempt.lease_expires_at)
    if exp is not None and exp <= current_time:
        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Attempt lease expired at {exp.isoformat()} before write lock."
        )
        db.flush()
        raise AttemptNotWritableError(
            "Attempt lease süresi dolmuş; çıktı yazılamaz.",
            error_code="WORKER_LOST",
        )

    # 2. Brief stale mi?
    if brief.is_stale:
        attempt.status = "failed"
        attempt.reason_code = "brief_stale"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Brief {brief.id} stale işaretlendiği için yazım reddedildi."
        )
        db.flush()
        raise AttemptNotWritableError(
            f"Brief {brief.id} stale durumda; çıktı yazılamaz.",
            error_code="BRIEF_STALE",
        )

    # 3. Kanal atama sürümü değişmiş mi?
    if scoring_run is not None and brief.channel_assignment_version != scoring_run.channel_assignment_version:
        attempt.status = "failed"
        attempt.reason_code = "assignment_changed"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
            f"run_version={scoring_run.channel_assignment_version}."
        )
        db.flush()
        raise AttemptNotWritableError(
            "Kanal atama sürümü değişmiş; çıktı yazılamaz.",
            error_code="ASSIGNMENT_CHANGED",
        )

    return attempt


def finish_attempt(
    db: Session,
    *,
    attempt_id: int,
    status: str,
    task_id: str | None = None,
    coverage: dict | list | None = None,
    warnings: list | None = None,
    reason_code: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> SocialGenerationAttempt:
    """Attempt'i terminal duruma (completed, partial, failed) geçirir.

    - Global kilit sırasıyla (ScoringRun -> SocialBrief -> SocialGenerationAttempt) kilitlenir.
    - Yalnızca terminal durum kabul eder.
    - Zaten aynı terminal durumdaysa ve task_id sahipliği eşleşiyorsa idempotent döner.
    - Farklı terminal duruma çevrilemez.
    - Pending -> terminal geçişine YALNIZ stage == 'categories' ve task_id is None olduğunda izin verilir.
    - Async aşamalar (ideas, ideas_retry, contents) claim edilmeden bitirilemez.
    - Running completed/partial geçişinde task_id, lease, brief staleness ve assignment version doğrulanır.
    - Running failed geçişinde en azından task ownership ve lease kontrol edilir (dolmuş lease worker_lost olur).
    - Commit yapmaz.
    """
    if status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError(
            f"Geçersiz terminal status: {status!r}. Beklenen: {TERMINAL_ATTEMPT_STATUSES}"
        )

    current_time = _utcnow(now)

    # Global kilit sırasıyla: ScoringRun -> SocialBrief -> SocialGenerationAttempt
    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )

    # 1. Zaten terminal mi?
    if attempt.status in TERMINAL_ATTEMPT_STATUSES:
        if attempt.status == status:
            # Idempotent başarı için task sahipliği kontrolü
            if attempt.task_id is not None:
                if not task_id or attempt.task_id != task_id:
                    raise AttemptNotWritableError(
                        _WORKER_OWNERSHIP_ERROR_MESSAGE,
                        error_code="TASK_MISMATCH",
                    )
            else:
                if task_id is not None:
                    raise AttemptNotWritableError(
                        "Senkron categories attempt için task_id verilmemelidir.",
                        error_code="TASK_MISMATCH",
                    )
            return attempt
        raise AttemptNotWritableError(
            f"Attempt zaten terminal durumda ({attempt.status}), {status} olarak değiştirilemez.",
            error_code="ALREADY_TERMINAL",
        )

    # 2. Pending attempt finalizasyon kuralı
    if attempt.status == "pending":
        if attempt.stage != "categories":
            raise AttemptNotWritableError(
                f"Pending {attempt.stage} attempt claim edilmeden bitirilemez; önce claim_attempt çağrılmalıdır.",
                error_code="PENDING_ATTEMPT_NOT_CLAIMABLE",
            )
        if task_id is not None:
            raise AttemptNotWritableError(
                "Pending categories attempt senkron çalışır; task_id verilmemelidir.",
                error_code="UNEXPECTED_TASK_ID_FOR_SYNC",
            )

    # 3. Running attempt kontrolleri
    elif attempt.status == "running":
        # Task ownership kontrolü
        if not task_id or attempt.task_id != task_id:
            raise AttemptNotWritableError(
                _WORKER_OWNERSHIP_ERROR_MESSAGE,
                error_code="TASK_MISMATCH",
            )

        # Lease kontrolü (hem completed/partial hem failed için süresi dolmuş worker worker_lost olur)
        exp = _to_utc(attempt.lease_expires_at)
        if exp is not None and exp <= current_time:
            attempt.status = "failed"
            attempt.reason_code = "worker_lost"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Attempt lease expired at {exp.isoformat()} before finish."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Attempt lease süresi dolmuş; worker_lost olarak kapatıldı.",
                error_code="WORKER_LOST",
            )

        # completed veya partial için ek fail-closed kontroller
        if status in ("completed", "partial"):
            # Brief stale mi?
            if brief.is_stale:
                attempt.status = "failed"
                attempt.reason_code = "brief_stale"
                attempt.completed_at = current_time
                attempt.lease_expires_at = None
                attempt.error_message = (
                    f"Brief {brief.id} stale işaretlendiği için finish reddedildi."
                )
                db.flush()
                raise AttemptNotWritableError(
                    f"Brief {brief.id} stale durumda; tamamlanamaz.",
                    error_code="BRIEF_STALE",
                )

            # Assignment version eşleşiyor mu?
            if (
                scoring_run is not None
                and brief.channel_assignment_version != scoring_run.channel_assignment_version
            ):
                attempt.status = "failed"
                attempt.reason_code = "assignment_changed"
                attempt.completed_at = current_time
                attempt.lease_expires_at = None
                attempt.error_message = (
                    f"Assignment version uyuşmazlığı: brief={brief.channel_assignment_version}, "
                    f"run={scoring_run.channel_assignment_version}."
                )
                db.flush()
                raise AttemptNotWritableError(
                    "Kanal atama sürümü değişmiş; tamamlanamaz.",
                    error_code="ASSIGNMENT_CHANGED",
                )

    attempt.status = status
    if coverage is not None:
        attempt.coverage = coverage
    if warnings is not None:
        attempt.warnings = warnings
    if reason_code is not None:
        attempt.reason_code = reason_code
    if error_message is not None:
        attempt.error_message = error_message
    attempt.completed_at = current_time
    attempt.lease_expires_at = None
    db.flush()
    return attempt


def lock_categories_attempt_for_finalize(
    db: Session,
    *,
    attempt_id: int,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Categories attempt'ini finalize etmek üzere global kilit sırasıyla kilitler.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: pending attempt başarıyla yazılabilir.
            - True: attempt zaten completed; idempotent okuma yolu.

    Kurallar:
    - attempt bulunamazsa ATTEMPT_NOT_FOUND (AttemptNotFoundError).
    - brief bulunamazsa BRIEF_NOT_FOUND (BriefNotFoundError).
    - stage tam olarak 'categories' olmalıdır; aksi halde AttemptNotWritableError (INVALID_STAGE).
    - task_id None olmalıdır (categories bu aşamada senkron attempt'tir); aksi halde UNEXPECTED_TASK_ID_FOR_SYNC.
    - status 'pending' ise:
      - lease_expires_at mevcut ve now değerinden ileride olmalı.
      - lease dolmuşsa WORKER_LOST ile reddet (failed/worker_lost yapıp AttemptNotWritableError fırlatır).
      - brief.is_stale False olmalı (stale ise failed/brief_stale yapıp AttemptNotWritableError fırlatır).
      - brief.channel_assignment_version ile scoring_run.channel_assignment_version eşleşmeli
        (farklıysa failed/assignment_changed yapıp AttemptNotWritableError fırlatır).
      - (attempt, brief, scoring_run, False) döner.
    - status 'completed' ise:
      - (attempt, brief, scoring_run, True) döner. Yeni yazmaya izin vermez.
    - running, partial veya failed attempt yazılabilir kabul edilmez; fail-closed AttemptNotWritableError fırlatır.
    - commit veya rollback çağırmaz.
    - now parametresi timezone-aware olmalıdır.
    """
    current_time = _utcnow(now)

    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )
    if scoring_run is None:
        raise AttemptNotWritableError(
            "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
        )

    if attempt.stage != "categories":
        raise AttemptNotWritableError(
            f"Attempt stage 'categories' olmalıdır (mevcut: {attempt.stage}).",
            error_code="INVALID_STAGE",
        )

    if attempt.task_id is not None:
        raise AttemptNotWritableError(
            "Categories attempt senkron çalışır; task_id None olmalıdır.",
            error_code="UNEXPECTED_TASK_ID_FOR_SYNC",
        )

    if attempt.status == "completed":
        return attempt, brief, scoring_run, True

    if attempt.status == "pending":
        # 1. Lease süresi dolmuş mu?
        exp = _to_utc(attempt.lease_expires_at)
        if exp is None or exp <= current_time:
            attempt.status = "failed"
            attempt.reason_code = "worker_lost"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Attempt lease expired at {exp.isoformat() if exp else 'None'} before finalize lock."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Attempt lease süresi dolmuş; çıktı yazılamaz.",
                error_code="WORKER_LOST",
            )

        # 2. Brief stale mi?
        if brief.is_stale:
            attempt.status = "failed"
            attempt.reason_code = "brief_stale"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Brief {brief.id} stale işaretlendiği için yazım reddedildi."
            )
            db.flush()
            raise AttemptNotWritableError(
                f"Brief {brief.id} stale durumda; çıktı yazılamaz.",
                error_code="BRIEF_STALE",
            )

        # 3. Kanal atama sürümü değişmiş mi?
        if brief.channel_assignment_version != scoring_run.channel_assignment_version:
            attempt.status = "failed"
            attempt.reason_code = "assignment_changed"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
                f"run_version={scoring_run.channel_assignment_version}."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Kanal atama sürümü değişmiş; çıktı yazılamaz.",
                error_code="ASSIGNMENT_CHANGED",
            )

        return attempt, brief, scoring_run, False

    # running, partial, failed veya diğer durumlar
    raise AttemptNotWritableError(
        f"Attempt '{attempt.status}' durumunda; finalize edilemez.",
        error_code="ATTEMPT_NOT_WRITABLE",
    )


def _lock_social_idea_attempt_for_finalize(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    expected_stage: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Ideas veya ideas_retry attempt'ini finalize etmek üzere global kilit sırasıyla kilitler.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: running attempt başarıyla yazılabilir.
            - True: attempt zaten completed; idempotent okuma yolu.
    """
    if expected_stage not in ("ideas", "ideas_retry"):
        raise ValueError(
            f"Geçersiz expected_stage: {expected_stage!r}. İzin verilenler: ('ideas', 'ideas_retry')"
        )

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id bool olmayan pozitif bir tamsayı olmalıdır.")

    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    current_time = _utcnow(now)

    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )
    if scoring_run is None:
        raise AttemptNotWritableError(
            "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
        )

    if attempt.stage != expected_stage:
        raise AttemptNotWritableError(
            f"Attempt stage '{expected_stage}' olmalıdır (mevcut: {attempt.stage}).",
            error_code="INVALID_STAGE",
        )

    if attempt.status == "completed":
        if attempt.task_id != task_id:
            raise AttemptNotWritableError(
                _WORKER_OWNERSHIP_ERROR_MESSAGE,
                error_code="TASK_MISMATCH",
            )
        return attempt, brief, scoring_run, True

    if attempt.status == "pending":
        raise AttemptNotWritableError(
            f"Pending {expected_stage} attempt claim edilmeden bitirilemez; önce running durumuna geçmelidir.",
            error_code="PENDING_ATTEMPT_NOT_CLAIMABLE",
        )

    if attempt.status == "running":
        if attempt.task_id != task_id:
            raise AttemptNotWritableError(
                _WORKER_OWNERSHIP_ERROR_MESSAGE,
                error_code="TASK_MISMATCH",
            )

        # 1. Lease süresi dolmuş mu?
        exp = _to_utc(attempt.lease_expires_at)
        if exp is None or exp <= current_time:
            attempt.status = "failed"
            attempt.reason_code = "worker_lost"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Attempt lease expired at {exp.isoformat() if exp else 'None'} before finalize lock."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Attempt lease süresi dolmuş; çıktı yazılamaz.",
                error_code="WORKER_LOST",
            )

        # 2. Brief stale mi?
        if brief.is_stale:
            attempt.status = "failed"
            attempt.reason_code = "brief_stale"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Brief {brief.id} stale işaretlendiği için yazım reddedildi."
            )
            db.flush()
            raise AttemptNotWritableError(
                f"Brief {brief.id} stale durumda; çıktı yazılamaz.",
                error_code="BRIEF_STALE",
            )

        # 3. Kanal atama sürümü değişmiş mi?
        if brief.channel_assignment_version != scoring_run.channel_assignment_version:
            attempt.status = "failed"
            attempt.reason_code = "assignment_changed"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
                f"run_version={scoring_run.channel_assignment_version}."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Kanal atama sürümü değişmiş; çıktı yazılamaz.",
                error_code="ASSIGNMENT_CHANGED",
            )

        return attempt, brief, scoring_run, False

    # partial, failed veya diğer durumlar
    raise AttemptNotWritableError(
        f"Attempt '{attempt.status}' durumunda; finalize edilemez.",
        error_code="ATTEMPT_NOT_WRITABLE",
    )


def lock_ideas_attempt_for_finalize(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Ideas attempt'ini finalize etmek üzere global kilit sırasıyla kilitler.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: running attempt başarıyla yazılabilir.
            - True: attempt zaten completed; idempotent okuma yolu.
    """
    return _lock_social_idea_attempt_for_finalize(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        expected_stage="ideas",
        now=now,
    )


def lock_ideas_retry_attempt_for_finalize(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Ideas retry attempt'ini finalize etmek üzere global kilit sırasıyla kilitler.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: running attempt başarıyla yazılabilir.
            - True: attempt zaten completed; idempotent okuma yolu.
    """
    return _lock_social_idea_attempt_for_finalize(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        expected_stage="ideas_retry",
        now=now,
    )


def lock_contents_attempt_for_content_write(
    db: Session,
    *,
    attempt_id: int,
    brief_id: int,
    idea_id: int,
    task_id: str,
    now: datetime | None = None,
) -> tuple[SocialGenerationAttempt, SocialBrief, ScoringRun]:
    """Contents stage attempt'ini tekil içerik yazımı için global kilit sırasıyla kilitler ve doğrular.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Doğrulamalar:
    - attempt_id, brief_id, idea_id pozitif int (bool reddedilir)
    - brief.id == brief_id
    - attempt.brief_id == brief_id
    - attempt.stage == "contents"
    - attempt.status == "running"
    - task_id zorunlu ve attempt.task_id == task_id (worker sahipliği)
    - lease süresi dolmamış olmalı (dolmuşsa failed/worker_lost)
    - brief stale olmamalı (stale ise failed/brief_stale)
    - brief.channel_assignment_version == scoring_run.channel_assignment_version (değişmişse failed/assignment_changed)
    - attempt.requested_idea_ids sequence olmalı ve idea_id bu listede yer almalı
    """
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id bool olmayan pozitif bir tamsayı olmalıdır.")
    if isinstance(brief_id, bool) or not isinstance(brief_id, int) or brief_id <= 0:
        raise ValueError("brief_id bool olmayan pozitif bir tamsayı olmalıdır.")
    if isinstance(idea_id, bool) or not isinstance(idea_id, int) or idea_id <= 0:
        raise ValueError("idea_id bool olmayan pozitif bir tamsayı olmalıdır.")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id zorunludur ve boş olamaz.")

    current_time = _utcnow(now)

    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )
    if scoring_run is None:
        raise AttemptNotWritableError(
            "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
        )

    if brief.id != brief_id:
        raise AttemptNotWritableError(
            "Brief ID uyuşmazlığı.", error_code="BRIEF_MISMATCH"
        )

    if attempt.brief_id != brief_id:
        raise AttemptNotWritableError(
            "Attempt brief ID uyuşmazlığı.", error_code="BRIEF_MISMATCH"
        )

    if attempt.stage != "contents":
        raise AttemptNotWritableError(
            f"Attempt stage 'contents' olmalıdır (mevcut: {attempt.stage}).",
            error_code="INVALID_STAGE",
        )

    if attempt.status != "running":
        raise AttemptNotWritableError(
            f"Attempt 'running' durumunda değil (mevcut: {attempt.status}).",
            error_code="ATTEMPT_NOT_RUNNING",
        )

    if attempt.task_id != task_id:
        raise AttemptNotWritableError(
            _WORKER_OWNERSHIP_ERROR_MESSAGE,
            error_code="TASK_MISMATCH",
        )

    # 1. Lease süresi dolmuş mu?
    exp = _to_utc(attempt.lease_expires_at)
    if exp is None or exp <= current_time:
        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Attempt lease expired at {exp.isoformat() if exp else 'None'} before content write."
        )
        db.flush()
        raise AttemptNotWritableError(
            "Attempt lease süresi dolmuş; çıktı yazılamaz.",
            error_code="WORKER_LOST",
        )

    # 2. Brief stale mi?
    if brief.is_stale:
        attempt.status = "failed"
        attempt.reason_code = "brief_stale"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Brief {brief.id} stale işaretlendiği için yazım reddedildi."
        )
        db.flush()
        raise AttemptNotWritableError(
            f"Brief {brief.id} stale durumda; çıktı yazılamaz.",
            error_code="BRIEF_STALE",
        )

    # 3. Kanal atama sürümü değişmiş mi?
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        attempt.status = "failed"
        attempt.reason_code = "assignment_changed"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
            f"run_version={scoring_run.channel_assignment_version}."
        )
        db.flush()
        raise AttemptNotWritableError(
            "Kanal atama sürümü değişmiş; çıktı yazılamaz.",
            error_code="ASSIGNMENT_CHANGED",
        )

    # 4. attempt.requested_idea_ids kontrolü
    req_ids = attempt.requested_idea_ids
    if not isinstance(req_ids, (list, tuple)) or isinstance(req_ids, bool):
        raise AttemptNotWritableError(
            "Attempt requested_idea_ids list veya tuple olmalıdır.",
            error_code="INVALID_REQUESTED_IDEAS",
        )
    if idea_id not in req_ids:
        raise AttemptNotWritableError(
            f"Fikir ID {idea_id} attempt.requested_idea_ids içinde yer almıyor.",
            error_code="IDEA_NOT_REQUESTED",
        )

    return attempt, brief, scoring_run



def _claim_social_idea_attempt_for_worker(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    expected_stage: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Ideas veya ideas_retry attempt'ini worker için canonical kilit sırasıyla claim eden ortak çekirdek fonksiyon."""
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id bool olmayan pozitif bir tamsayı olmalıdır.")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olamaz.")

    current_time = _utcnow(now)

    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )
    if scoring_run is None:
        raise AttemptNotWritableError(
            "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
        )

    if attempt.stage != expected_stage:
        raise AttemptNotWritableError(
            f"Attempt stage '{expected_stage}' olmalıdır (mevcut: {attempt.stage}).",
            error_code="INVALID_STAGE",
        )

    if attempt.status == "completed":
        if attempt.task_id != task_id:
            raise AttemptNotWritableError(
                _WORKER_OWNERSHIP_ERROR_MESSAGE,
                error_code="TASK_MISMATCH",
            )
        return attempt, brief, scoring_run, True

    if attempt.status in ("failed", "partial"):
        raise AttemptNotWritableError(
            f"Attempt terminal durumda ({attempt.status}); claim edilemez.",
            error_code="ATTEMPT_NOT_CLAIMABLE",
        )

    # Fail-closed brief tazelik kontrolleri
    if brief.is_stale:
        attempt.status = "failed"
        attempt.reason_code = "brief_stale"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Brief {brief.id} stale işaretlendiği için claim reddedildi."
        )
        db.flush()
        raise AttemptNotWritableError(
            f"Brief {brief.id} stale durumda; claim edilemez.",
            error_code="BRIEF_STALE",
        )

    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        attempt.status = "failed"
        attempt.reason_code = "assignment_changed"
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.error_message = (
            f"Assignment version uyuşmazlığı: brief_version={brief.channel_assignment_version}, "
            f"run_version={scoring_run.channel_assignment_version}."
        )
        db.flush()
        raise AttemptNotWritableError(
            "Kanal atama sürümü değişmiş; claim reddedildi.",
            error_code="ASSIGNMENT_CHANGED",
        )

    if attempt.status == "pending":
        if attempt.task_id is not None or attempt.started_at is not None:
            raise AttemptNotWritableError(
                "Pending attempt state tutarsız: task_id ve started_at None olmalıdır.",
                error_code="ATTEMPT_STATE_INCONSISTENT",
            )

        exp = _to_utc(attempt.lease_expires_at)
        if exp is None or exp <= current_time:
            attempt.status = "failed"
            attempt.reason_code = "worker_lost"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Attempt lease expired at {exp.isoformat() if exp else 'None'} before worker claim."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Attempt lease süresi dolmuş; worker claim reddedildi.",
                error_code="WORKER_LOST",
            )

        attempt.status = "running"
        attempt.task_id = task_id
        attempt.started_at = current_time
        attempt.heartbeat_at = current_time
        attempt.lease_expires_at = current_time + timedelta(seconds=SOCIAL_ATTEMPT_LEASE_SECONDS)
        db.flush()
        return attempt, brief, scoring_run, False

    if attempt.status == "running":
        if attempt.task_id != task_id:
            raise AttemptNotWritableError(
                _WORKER_OWNERSHIP_ERROR_MESSAGE,
                error_code="TASK_MISMATCH",
            )

        exp = _to_utc(attempt.lease_expires_at)
        if exp is None or exp <= current_time:
            attempt.status = "failed"
            attempt.reason_code = "worker_lost"
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            attempt.error_message = (
                f"Attempt lease expired at {exp.isoformat() if exp else 'None'} before worker claim."
            )
            db.flush()
            raise AttemptNotWritableError(
                "Attempt lease süresi dolmuş; worker claim reddedildi.",
                error_code="WORKER_LOST",
            )

        return attempt, brief, scoring_run, False

    raise AttemptNotWritableError(
        f"Geçersiz attempt statüsü: '{attempt.status}'.",
        error_code="ATTEMPT_NOT_CLAIMABLE",
    )


def claim_ideas_attempt_for_worker(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Ideas attempt'ini worker için global kilit sırasıyla claim eder.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: pending veya running attempt başarıyla claim edildi / devam ediyor.
            - True: attempt zaten completed; idempotent okuma yolu (AI çalıştırılmaz).
    """
    return _claim_social_idea_attempt_for_worker(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        expected_stage="ideas",
        now=now,
    )


def claim_ideas_retry_attempt_for_worker(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Ideas retry attempt'ini worker için global kilit sırasıyla claim eder.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: pending veya running attempt başarıyla claim edildi / devam ediyor.
            - True: attempt zaten completed; idempotent okuma yolu (AI çalıştırılmaz).
    """
    return _claim_social_idea_attempt_for_worker(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        expected_stage="ideas_retry",
        now=now,
    )


def claim_contents_attempt_for_worker(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> tuple[
    SocialGenerationAttempt,
    SocialBrief,
    ScoringRun,
    bool,
]:
    """Contents attempt'ini worker için global kilit sırasıyla claim eder.

    Global kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        tuple[SocialGenerationAttempt, SocialBrief, ScoringRun, bool]:
            - False: pending veya running attempt başarıyla claim edildi / devam ediyor.
            - True: attempt zaten completed; idempotent okuma yolu (AI çalıştırılmaz).
    """
    return _claim_social_idea_attempt_for_worker(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        expected_stage="contents",
        now=now,
    )


ALLOWED_IDEAS_FAILURE_REASONS = frozenset({
    "idea_input_invalid",
    "idea_provider_error",
    "idea_output_invalid",
    "idea_heartbeat_failed",
    "idea_persistence_failed",
    "worker_lost",
    "brief_stale",
    "assignment_changed",
    "worker_bootstrap_failed",
    "dispatch_failed",
    # Eski motor (v2/v2_1) run'ı salt-okunur: worker AI çağırmadan kapatır
    # (app/core/engine_version_gate.py)
    "legacy_run_read_only",
})

ALLOWED_CONTENTS_FAILURE_REASONS = frozenset({
    "content_input_invalid",
    "content_provider_error",
    "content_output_invalid",
    "content_quality_invalid",
    "content_repair_input_invalid",
    "content_repair_provider_error",
    "content_repair_output_invalid",
    "content_execution_inconsistent",
    "content_heartbeat_failed",
    "content_persistence_failed",
    "content_generation_failed",
    "worker_lost",
    "brief_stale",
    "assignment_changed",
    "worker_bootstrap_failed",
    "dispatch_failed",
    # Eski motor (v2/v2_1) run'ı salt-okunur: worker AI çağırmadan kapatır
    # (app/core/engine_version_gate.py)
    "legacy_run_read_only",
})

PENDING_ALLOWED_FAILURE_REASONS = frozenset({
    "worker_lost",
    "brief_stale",
    "assignment_changed",
    "worker_bootstrap_failed",
    "dispatch_failed",
    # Eski motor (v2/v2_1) run'ı salt-okunur: worker AI çağırmadan kapatır
    # (app/core/engine_version_gate.py)
    "legacy_run_read_only",
})


def _finalize_social_idea_attempt_failure(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    reason_code: str,
    error_message: str,
    expected_stage: str,
    now: datetime | None = None,
    allowed_reasons: frozenset[str] | None = None,
) -> tuple[SocialGenerationAttempt, bool]:
    """Ideas, ideas_retry veya contents attempt'ini failure durumunda canonical kilit sırasıyla sonlandıran ortak çekirdek."""
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise ValueError("attempt_id bool olmayan pozitif bir tamsayı olmalıdır.")
    valid_reasons = allowed_reasons if allowed_reasons is not None else ALLOWED_IDEAS_FAILURE_REASONS
    if reason_code not in valid_reasons:
        raise ValueError(
            f"Geçersiz {expected_stage} failure reason_code: {reason_code!r}. "
            f"İzin verilenler: {sorted(valid_reasons)}"
        )
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id boş olmayan bir string olmalıdır.")
    if not isinstance(error_message, str) or not error_message.strip():
        raise ValueError("error_message boş olmayan bir string olmalıdır.")

    current_time = _utcnow(now)

    attempt, brief, scoring_run = _lock_context_for_attempt(
        db, attempt_id, lock_scoring_run=True
    )
    if scoring_run is None:
        raise AttemptNotWritableError(
            "ScoringRun bulunamadı.", error_code="RUN_NOT_FOUND"
        )

    if attempt.stage != expected_stage:
        raise AttemptNotWritableError(
            f"Attempt stage '{expected_stage}' olmalıdır (mevcut: {attempt.stage}).",
            error_code="INVALID_STAGE",
        )

    # 1. Zaten completed ise değiştirme (failed yapılmaz)
    if attempt.status == "completed":
        return attempt, False

    # 2. Zaten failed ise mevcut reason_code ve error_message ezilmez
    if attempt.status == "failed":
        return attempt, False

    # 3. Partial ise yeniden failed yapılmaz
    if attempt.status == "partial":
        return attempt, False

    # 4. Running attempt kontrolleri
    if attempt.status == "running":
        # Task ownership kontrolü: farklı task_id tarafından sahiplenilmişse dokunma
        if attempt.task_id != task_id:
            return attempt, False

        attempt.status = "failed"
        attempt.reason_code = reason_code
        attempt.error_message = error_message
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        db.flush()
        return attempt, True

    # 5. Pending attempt kuralları
    if attempt.status == "pending":
        if reason_code in PENDING_ALLOWED_FAILURE_REASONS:
            attempt.status = "failed"
            attempt.reason_code = reason_code
            attempt.error_message = error_message
            attempt.completed_at = current_time
            attempt.lease_expires_at = None
            db.flush()
            return attempt, True
        # Normal AI/input/persistence hata kodları pending attempt'i sahiplenmeden failed yapamamalı
        return attempt, False

    return attempt, False


def finalize_ideas_attempt_failure(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    reason_code: str,
    error_message: str,
    now: datetime | None = None,
) -> tuple[SocialGenerationAttempt, bool]:
    """Ideas attempt'ini failure durumunda canonical kilit sırasıyla sonlandırır."""
    return _finalize_social_idea_attempt_failure(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        reason_code=reason_code,
        error_message=error_message,
        expected_stage="ideas",
        now=now,
    )


def finalize_ideas_retry_attempt_failure(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    reason_code: str,
    error_message: str,
    now: datetime | None = None,
) -> tuple[SocialGenerationAttempt, bool]:
    """Ideas retry attempt'ini failure durumunda canonical kilit sırasıyla sonlandırır."""
    return _finalize_social_idea_attempt_failure(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        reason_code=reason_code,
        error_message=error_message,
        expected_stage="ideas_retry",
        now=now,
    )


def finalize_contents_attempt_failure(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    reason_code: str,
    error_message: str,
    now: datetime | None = None,
) -> tuple[SocialGenerationAttempt, bool]:
    """Contents attempt'ini failure durumunda canonical kilit sırasıyla sonlandırır."""
    return _finalize_social_idea_attempt_failure(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        reason_code=reason_code,
        error_message=error_message,
        expected_stage="contents",
        now=now,
        allowed_reasons=ALLOWED_CONTENTS_FAILURE_REASONS,
    )


def reconcile_expired_ideas_attempt_for_read(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
    now: datetime | None = None,
) -> bool:
    """Okuma öncesi süresi dolmuş ideas attempt'ini güvenli canonical kilit sırasıyla failed/worker_lost yapar.

    Canonical kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        bool: Attempt bu çağrıda worker_lost yapıldıysa True; değişiklik yapılmadıysa False.

    Kurallar:
        - commit veya rollback çağırmaz.
        - Değişiklik yapılırsa flush eder.
        - brief_id, attempt_id ve brand_profile_id pozitif integer olmalıdır.
        - BrandProfile mevcut ve deleted_at IS NULL olmalıdır.
        - Attempt stage tam olarak "ideas" olmalıdır.
        - Kilitlenen brief.id ve ScoringRun.brand_profile_id uyuşmalıdır.
        - Bulunamama veya uyuşmazlıklarda AttemptNotFoundError fırlatılır.
        - pending/running:
            - lease_expires_at > now ise dokunma (False dön).
            - lease_expires_at <= now veya lease_expires_at is None ise:
                status="failed", reason_code="worker_lost", error_message="Fikir üretim worker lease süresi doldu.",
                completed_at=current_time, lease_expires_at=None, db.flush(), return True.
        - completed/failed/partial: dokunma (False dön).
        - Bilinmeyen status: AttemptNotWritableError fırlatılır.
    """
    for val, name in (
        (brief_id, "brief_id"),
        (attempt_id, "attempt_id"),
        (brand_profile_id, "brand_profile_id"),
    ):
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(f"{name} pozitif integer olmalıdır.")

    current_time = _utcnow(now)

    from app.database.models import BrandProfile

    workspace = (
        db.query(BrandProfile.id)
        .filter(
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if workspace is None:
        raise AttemptNotFoundError(
            f"BrandProfile bulunamadı veya silinmiş: {brand_profile_id}",
            error_code="IDEA_ATTEMPT_NOT_FOUND",
        )

    try:
        attempt, brief, scoring_run = _lock_context_for_attempt(
            db, attempt_id, lock_scoring_run=True
        )
    except (AttemptNotFoundError, BriefNotFoundError):
        raise AttemptNotFoundError(
            "Idea attempt bulunamadı.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    if scoring_run is None or scoring_run.brand_profile_id != brand_profile_id:
        raise AttemptNotFoundError(
            "Workspace uyuşmazlığı.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    if brief.id != brief_id:
        raise AttemptNotFoundError(
            "Brief uyuşmazlığı.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    if attempt.stage != "ideas":
        raise AttemptNotFoundError(
            "Attempt aşaması 'ideas' değil.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    allowed_statuses = {"pending", "running", "completed", "failed", "partial"}
    if attempt.status not in allowed_statuses:
        raise AttemptNotWritableError(
            f"Bilinmeyen attempt statüsü: {attempt.status}",
            error_code="ATTEMPT_NOT_WRITABLE",
        )

    if attempt.status in ("completed", "failed", "partial"):
        return False

    if attempt.status in ("pending", "running"):
        exp = _to_utc(attempt.lease_expires_at)
        if exp is not None and exp > current_time:
            return False

        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.error_message = "Fikir üretim worker lease süresi doldu."
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        db.flush()
        return True

    return False


def reconcile_expired_ideas_retry_attempt_for_read(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
    now: datetime | None = None,
) -> bool:
    """Okuma öncesi süresi dolmuş ideas_retry attempt'ini güvenli canonical kilit sırasıyla failed/worker_lost yapar.

    Canonical kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        bool: Attempt bu çağrıda worker_lost yapıldıysa True; değişiklik yapılmadıysa False.
    """
    for val, name in (
        (brief_id, "brief_id"),
        (attempt_id, "attempt_id"),
        (brand_profile_id, "brand_profile_id"),
    ):
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(f"{name} pozitif integer olmalıdır.")

    current_time = _utcnow(now)

    from app.database.models import BrandProfile

    workspace = (
        db.query(BrandProfile.id)
        .filter(
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if workspace is None:
        raise AttemptNotFoundError(
            f"BrandProfile bulunamadı veya silinmiş: {brand_profile_id}",
            error_code="IDEA_ATTEMPT_NOT_FOUND",
        )

    try:
        attempt, brief, scoring_run = _lock_context_for_attempt(
            db, attempt_id, lock_scoring_run=True
        )
    except (AttemptNotFoundError, BriefNotFoundError):
        raise AttemptNotFoundError(
            "Idea attempt bulunamadı.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    if scoring_run is None or scoring_run.brand_profile_id != brand_profile_id:
        raise AttemptNotFoundError(
            "Workspace uyuşmazlığı.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    if brief.id != brief_id:
        raise AttemptNotFoundError(
            "Brief uyuşmazlığı.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    if attempt.stage != "ideas_retry":
        raise AttemptNotFoundError(
            "Attempt aşaması 'ideas_retry' değil.", error_code="IDEA_ATTEMPT_NOT_FOUND"
        )

    allowed_statuses = {"pending", "running", "completed", "failed", "partial"}
    if attempt.status not in allowed_statuses:
        raise AttemptNotWritableError(
            f"Bilinmeyen attempt statüsü: {attempt.status}",
            error_code="ATTEMPT_NOT_WRITABLE",
        )

    if attempt.status in ("completed", "failed", "partial"):
        return False

    if attempt.status in ("pending", "running"):
        exp = _to_utc(attempt.lease_expires_at)
        if exp is not None and exp > current_time:
            return False

        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.error_message = "Fikir tekrar deneme worker lease süresi doldu."
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        db.flush()
        return True

    return False


def reconcile_expired_contents_attempt_for_read(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
    now: datetime | None = None,
) -> bool:
    """Okuma öncesi süresi dolmuş contents attempt'ini güvenli canonical kilit sırasıyla failed/worker_lost yapar.

    Canonical kilit sırası (CANONICAL GLOBAL LOCK ORDER):
        ScoringRun (FOR UPDATE)
          -> SocialBrief (FOR UPDATE)
            -> SocialGenerationAttempt (FOR UPDATE)

    Dönüş:
        bool: Attempt bu çağrıda worker_lost yapıldıysa True; değişiklik yapılmadıysa False.

    Kurallar:
        - commit veya rollback çağırmaz.
        - Değişiklik yapılırsa flush eder.
        - brief_id, attempt_id ve brand_profile_id pozitif integer olmalıdır.
        - BrandProfile mevcut ve deleted_at IS NULL olmalıdır.
        - Attempt stage tam olarak "contents" olmalıdır.
        - Kilitlenen brief.id ve ScoringRun.brand_profile_id uyuşmalıdır.
        - Bulunamama veya uyuşmazlıklarda AttemptNotFoundError fırlatılır.
        - pending/running:
            - lease_expires_at > now ise dokunma (False dön).
            - lease_expires_at <= now veya lease_expires_at is None ise:
                status="failed", reason_code="worker_lost", error_message="Sosyal içerik üretim worker lease süresi doldu.",
                completed_at=current_time, lease_expires_at=None, task_id=None, db.flush(), return True.
        - completed/failed/partial: dokunma (False dön).
        - Bilinmeyen status: AttemptNotWritableError fırlatılır.
    """
    for val, name in (
        (brief_id, "brief_id"),
        (attempt_id, "attempt_id"),
        (brand_profile_id, "brand_profile_id"),
    ):
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(f"{name} pozitif integer olmalıdır.")

    current_time = _utcnow(now)

    from app.database.models import BrandProfile

    workspace = (
        db.query(BrandProfile.id)
        .filter(
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if workspace is None:
        raise AttemptNotFoundError(
            f"BrandProfile bulunamadı veya silinmiş: {brand_profile_id}",
            error_code="CONTENT_ATTEMPT_NOT_FOUND",
        )

    try:
        attempt, brief, scoring_run = _lock_context_for_attempt(
            db, attempt_id, lock_scoring_run=True
        )
    except (AttemptNotFoundError, BriefNotFoundError):
        raise AttemptNotFoundError(
            "Content attempt bulunamadı.", error_code="CONTENT_ATTEMPT_NOT_FOUND"
        )

    if scoring_run is None or scoring_run.brand_profile_id != brand_profile_id:
        raise AttemptNotFoundError(
            "Workspace uyuşmazlığı.", error_code="CONTENT_ATTEMPT_NOT_FOUND"
        )

    if brief.id != brief_id:
        raise AttemptNotFoundError(
            "Brief uyuşmazlığı.", error_code="CONTENT_ATTEMPT_NOT_FOUND"
        )

    if attempt.stage != "contents":
        raise AttemptNotFoundError(
            "Attempt aşaması 'contents' değil.", error_code="CONTENT_ATTEMPT_NOT_FOUND"
        )

    allowed_statuses = {"pending", "running", "completed", "failed", "partial"}
    if attempt.status not in allowed_statuses:
        raise AttemptNotWritableError(
            f"Bilinmeyen attempt statüsü: {attempt.status}",
            error_code="ATTEMPT_NOT_WRITABLE",
        )

    if attempt.status in ("completed", "failed", "partial"):
        return False

    if attempt.status in ("pending", "running"):
        exp = _to_utc(attempt.lease_expires_at)
        if exp is not None and exp > current_time:
            return False

        attempt.status = "failed"
        attempt.reason_code = "worker_lost"
        attempt.error_message = "Sosyal içerik üretim worker lease süresi doldu."
        attempt.completed_at = current_time
        attempt.lease_expires_at = None
        attempt.task_id = None
        db.flush()
        return True

    return False

