# -*- coding: utf-8 -*-
"""Sosyal Brief Fikir Tekrar Deneme (ideas_retry) Worker Input Hazırlığı (F1-F.7.5).

Bu modül Celery worker'ın AI çağrısından önce ihtiyaç duyacağı güvenilir,
kanonik ve doğrulanmış snapshot'ları hazırlar; ideas_retry attempt'ini claim eder
ve yalnızca eksik hedefler ile boş kategoriler (K4) için kategori bazında
gruplanmış deterministik SocialIdeaPromptInput DTO'larını oluşturur (kategori başına
tek prompt).

Kurallar:
- Celery task içermez.
- AI çağrısı yapmaz.
- API endpoint içermez.
- SocialIdea persist etmez / oluşturmaz / silmez / değiştirmez.
- db.begin(), commit() veya rollback() ÇAĞIRMAZ.
- Attempt success/failure finalize etmez.
- coverage veya requested_target_ids alanlarını değiştirmez.
- Global kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
- Completed replay yolunda prompt_inputs=() döner ve SocialIdea tablosunu okumaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.social.idea_contract import IdeaTargetSpec
from app.core.social.idea_persistence import extract_social_idea_plan_snapshot
from app.core.social.idea_planner import (
    IdeaCategoryPlan,
    IdeaTargetQuota,
    SocialIdeaGenerationPlan,
)
from app.core.social.idea_retry_snapshot import (
    SocialIdeaRetryPlanSnapshot,
    SocialIdeaRetrySnapshotError,
    extract_social_idea_retry_plan_snapshot,
)
from app.database.models import (
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
)
from app.generators.social.attempt_state import (
    AttemptNotClaimableError,
    AttemptNotFoundError,
    AttemptNotWritableError,
    claim_ideas_retry_attempt_for_worker,
)
from app.generators.social.brief_idea_prompt import (
    IdeaKeywordSnapshot,
    SocialIdeaPromptInput,
)
from app.generators.social.format_matrix import get_platform_format


class SocialIdeaRetryWorkerInputError(ValueError):
    """Worker retry input hazırlık domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        field: str | None = None,
        category_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.category_id = category_id

    def __repr__(self) -> str:
        return (
            f"SocialIdeaRetryWorkerInputError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class SocialIdeaRetryWorkerPreparation:
    """Retry worker hazırlık aşaması nihai çıktısı (immutable)."""

    attempt_id: int
    task_id: str
    brief_id: int
    scoring_run_id: int
    source_attempt_id: int
    already_completed: bool
    canonical_target_ids: tuple[int, ...]
    persisted_target_ids_at_start: tuple[int, ...]
    missing_target_ids: tuple[int, ...]
    prompt_inputs: tuple[SocialIdeaPromptInput, ...]


def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def prepare_social_idea_retry_worker_inputs(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> SocialIdeaRetryWorkerPreparation:
    """ideas_retry attempt'ini claim eder ve eksik hedefler için doğrulanmış girdileri hazırlar.

    İşlem sırası:
    1. Girdi doğrulama (task_id, attempt_id, now).
    2. Canonical kilit altında attempt'in claim edilmesi:
       claim_ideas_retry_attempt_for_worker(...)
    3. Brief tazelik ve kanal atama sürüm kontrolleri.
    4. Kaynak attempt keşfi, doğrulaması ve kaynak planın çıkarılması.
    5. Kanonik brief hedeflerinin DB'den yüklenmesi ve format matrisi denetimi.
    6. Retry snapshot'ının extract_social_idea_retry_plan_snapshot ile doğrulanması.
    7. Completed replay ise prompt_inputs=() dönülmesi.
    8. Running yolunda DB varlıklarının (kategori, keyword, target) doğrulanması.
    9. Sadece missing target'lar için kategori bazında gruplanmış SocialIdeaPromptInput DTO'larının oluşturulması.

    Transaction sözleşmesi:
    - db.begin(), commit() veya rollback() ÇAĞIRMAZ.
    - Sadece claim durumunda flush yapılır (attempt_state tarafından).
    - Transaction yönetimi tamamen çağıran katmana aittir.
    """
    current_time = _validate_now(now)

    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialIdeaRetryWorkerInputError(
            "task_id boş olmayan bir string olmalıdır.",
            error_code="IDEA_RETRY_WORKER_INPUT_INVALID",
            field="task_id",
        )

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialIdeaRetryWorkerInputError(
            "attempt_id pozitif bir tamsayı olmalıdır.",
            error_code="IDEA_RETRY_WORKER_INPUT_INVALID",
            field="attempt_id",
        )

    # 1. Canonical kilit ve attempt claim
    try:
        attempt, brief, scoring_run, already_completed = claim_ideas_retry_attempt_for_worker(
            db,
            attempt_id=attempt_id,
            task_id=task_id,
            now=current_time,
        )
    except AttemptNotFoundError:
        raise SocialIdeaRetryWorkerInputError(
            "Attempt bulunamadı.",
            error_code="IDEA_RETRY_WORKER_INPUT_INVALID",
            field="attempt_id",
        ) from None
    except AttemptNotClaimableError:
        raise SocialIdeaRetryWorkerInputError(
            "Attempt claim edilemez durumda.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        ) from None
    except AttemptNotWritableError:
        raise SocialIdeaRetryWorkerInputError(
            "Attempt veya ilişkili kayıtlar üzerinde işlem yapılamaz.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        ) from None
    except ValueError:
        raise SocialIdeaRetryWorkerInputError(
            "Geçersiz attempt claim parametresi.",
            error_code="IDEA_RETRY_WORKER_INPUT_INVALID",
        ) from None

    # 2. Fail-closed brief tazelik ve sürüm kontrolleri
    if brief.is_stale is not False:
        raise SocialIdeaRetryWorkerInputError(
            "Brief stale durumda; worker input hazırlanamaz.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise SocialIdeaRetryWorkerInputError(
            "Brief kanal atama sürümü güncel değil; worker input hazırlanamaz.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    # 3. Kanonik Brief Hedeflerinin Yüklenmesi (1-6 hedef, format matrisi kontrolü)
    target_rows = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .order_by(SocialBriefTarget.id.asc())
        .all()
    )
    if not (1 <= len(target_rows) <= 6):
        raise SocialIdeaRetryWorkerInputError(
            "Brief target sayısı 1 ile 6 arasında olmalıdır.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    seen_target_ids: set[int] = set()
    target_map: dict[int, SocialBriefTarget] = {}
    for t in target_rows:
        if isinstance(t.id, bool) or not isinstance(t.id, int) or t.id <= 0:
            raise SocialIdeaRetryWorkerInputError(
                "Brief target ID pozitif tamsayı olmalıdır.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        if t.id in seen_target_ids:
            raise SocialIdeaRetryWorkerInputError(
                "Brief hedefleri içinde mükerrer ID tespit edildi.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        seen_target_ids.add(t.id)

        if not t.platform or not t.content_format or get_platform_format(t.platform, t.content_format) is None:
            raise SocialIdeaRetryWorkerInputError(
                "Hedef platform veya format kanonik matriste geçersiz.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        target_map[t.id] = t

    canonical_target_ids = tuple(t.id for t in target_rows)

    # 4. Kaynak Attempt Keşfi ve Doğrulaması
    cov = attempt.coverage
    if not isinstance(cov, dict):
        raise SocialIdeaRetryWorkerInputError(
            "Attempt coverage snapshot bir sözlük olmalıdır.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            field="coverage",
        )

    req = cov.get("request")
    if not isinstance(req, dict):
        raise SocialIdeaRetryWorkerInputError(
            "Attempt coverage request bloğu geçersiz.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            field="coverage.request",
        )

    source_attempt_id = req.get("source_attempt_id")
    if isinstance(source_attempt_id, bool) or not isinstance(source_attempt_id, int) or source_attempt_id <= 0:
        raise SocialIdeaRetryWorkerInputError(
            "Attempt coverage source_attempt_id geçersiz.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            field="coverage.request.source_attempt_id",
        )

    source_attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == source_attempt_id)
        .populate_existing()
        .first()
    )
    if source_attempt is None or source_attempt.brief_id != brief.id:
        raise SocialIdeaRetryWorkerInputError(
            "Kaynak attempt bulunamadı veya bu brief'e ait değil.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    if source_attempt.stage != "ideas":
        raise SocialIdeaRetryWorkerInputError(
            "Kaynak attempt aşaması geçersiz.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    if source_attempt.status in ("pending", "running"):
        raise SocialIdeaRetryWorkerInputError(
            "Kaynak attempt henüz tamamlanmamış.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    if source_attempt.status not in ("completed", "partial", "failed"):
        raise SocialIdeaRetryWorkerInputError(
            "Kaynak attempt durumu retry için uygun değil.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    # Kaynak plan snapshot'ının otoriter parser ile çıkarılması
    try:
        source_plan_snapshot = extract_social_idea_plan_snapshot(source_attempt)
    except Exception:
        raise SocialIdeaRetryWorkerInputError(
            "Kaynak attempt snapshot doğrulaması başarısız.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        ) from None

    source_plan = SocialIdeaGenerationPlan(
        total_requested=source_plan_snapshot.total_requested,
        category_plans=tuple(
            IdeaCategoryPlan(
                category_id=cp.category_id,
                requested_count=cp.requested_count,
                target_quotas=tuple(
                    IdeaTargetQuota(target_id=t_id, requested_count=q)
                    for t_id, q in cp.target_quotas
                ),
            )
            for cp in source_plan_snapshot.categories
        ),
        covered_category_ids=tuple(cp.category_id for cp in source_plan_snapshot.categories),
        covered_target_ids=source_plan_snapshot.covered_target_ids,
    )

    # 5. Retry Snapshot Doğrulaması (Kanonik parser)
    try:
        retry_snapshot = extract_social_idea_retry_plan_snapshot(
            attempt,
            source_plan=source_plan,
            expected_canonical_target_ids=canonical_target_ids,
            expected_source_attempt_id=source_attempt.id,
        )
    except SocialIdeaRetrySnapshotError:
        raise SocialIdeaRetryWorkerInputError(
            "Retry snapshot doğrulaması başarısız.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        ) from None

    # 6. Zaten tamamlanmış attempt yolu (Completed Replay)
    if already_completed:
        return SocialIdeaRetryWorkerPreparation(
            attempt_id=attempt.id,
            task_id=task_id,
            brief_id=brief.id,
            scoring_run_id=scoring_run.id,
            source_attempt_id=source_attempt.id,
            already_completed=True,
            canonical_target_ids=retry_snapshot.canonical_target_ids,
            persisted_target_ids_at_start=retry_snapshot.persisted_target_ids_at_start,
            missing_target_ids=retry_snapshot.missing_target_ids,
            prompt_inputs=(),
        )

    # 7. Running Yolunda DB Varlıklarının Doğrulanması
    # 7a. Brief Keywords
    kw_rows = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
    )
    if not (1 <= len(kw_rows) <= 5):
        raise SocialIdeaRetryWorkerInputError(
            "Brief keyword sayısı 1 ile 5 arasında olmalıdır.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    sorted_kws = sorted(kw_rows, key=lambda k: k.position if k.position is not None else -1)
    expected_positions = list(range(len(sorted_kws)))
    actual_positions = [k.position for k in sorted_kws]
    if actual_positions != expected_positions:
        raise SocialIdeaRetryWorkerInputError(
            "Brief keyword pozisyonları 0..n-1 aralığında kesintisiz olmalıdır.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    seen_kw_ids: set[int] = set()
    kw_snapshots: list[IdeaKeywordSnapshot] = []
    for k in sorted_kws:
        kid = k.keyword_id
        if isinstance(kid, bool) or not isinstance(kid, int) or kid <= 0:
            raise SocialIdeaRetryWorkerInputError(
                "Brief keyword_id pozitif tamsayı olmalıdır.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        if kid in seen_kw_ids:
            raise SocialIdeaRetryWorkerInputError(
                "Brief içinde mükerrer keyword_id tespit edildi.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        seen_kw_ids.add(kid)

        snap = k.keyword_snapshot
        if not isinstance(snap, str) or not snap.strip():
            raise SocialIdeaRetryWorkerInputError(
                "Brief keyword_snapshot boş veya whitespace-only olamaz.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )

        kw_snapshots.append(
            IdeaKeywordSnapshot(
                keyword_id=kid,
                keyword_snapshot=snap,
                position=k.position,
            )
        )
    keyword_snapshots_tuple = tuple(kw_snapshots)

    # 7b. Kategoriler
    cat_rows = (
        db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .all()
    )
    cat_map: dict[int, SocialCategory] = {c.id: c for c in cat_rows}

    # Kaynak plandaki tüm kategorileri brief otoritesinden doğrula
    for cp in source_plan.category_plans:
        cid = cp.category_id
        if cid not in cat_map:
            raise SocialIdeaRetryWorkerInputError(
                "Kategori brief altında bulunamadı.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
                category_id=cid,
            )
        cat = cat_map[cid]
        if cat.scoring_run_id != scoring_run.id:
            raise SocialIdeaRetryWorkerInputError(
                "Kategori farklı scoring run'a ait.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
                category_id=cid,
            )
        if cat.is_stale is not False:
            raise SocialIdeaRetryWorkerInputError(
                "Kategori stale durumda.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
                category_id=cid,
            )

        name = cat.category_name
        if not isinstance(name, str) or len(name) == 0 or name != name.strip() or len(name) > 100:
            raise SocialIdeaRetryWorkerInputError(
                "Kategori adı geçersiz veya sınır dışı.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
                category_id=cid,
            )

        desc = cat.description
        if not isinstance(desc, str) or len(desc) == 0 or desc != desc.strip() or len(desc) > 2000:
            raise SocialIdeaRetryWorkerInputError(
                "Kategori açıklaması geçersiz veya sınır dışı.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
                category_id=cid,
            )

    # 8. Plan Atamalarının Gruplanması ve Deterministik Prompt Girdileri
    cat_assignments: dict[int, list[int]] = {cp.category_id: [] for cp in source_plan.category_plans}
    assigned_target_ids: list[int] = []

    for assign in retry_snapshot.plan.assignments:
        cid = assign.category_id
        tid = assign.target_id
        if cid not in cat_assignments:
            raise SocialIdeaRetryWorkerInputError(
                "Assignment kategori ID kaynak planda bulunamadı.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
                category_id=cid,
            )
        if tid not in target_map:
            raise SocialIdeaRetryWorkerInputError(
                "Assignment hedef ID brief hedefleri arasında bulunamadı.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        if assign.requested_count != 1:
            raise SocialIdeaRetryWorkerInputError(
                "Assignment requested_count 1 olmalıdır.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
        cat_assignments[cid].append(tid)
        assigned_target_ids.append(tid)

    if retry_snapshot.is_legacy:
        if tuple(assigned_target_ids) != retry_snapshot.missing_target_ids:
            raise SocialIdeaRetryWorkerInputError(
                "Atanan hedefler eksik hedeflerle birebir eşleşmiyor.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )
    elif not set(retry_snapshot.missing_target_ids).issubset(assigned_target_ids):
        raise SocialIdeaRetryWorkerInputError(
            "Atamalar eksik hedeflerin tamamını kapsamıyor.",
            error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
        )

    # Başlangıçta kaydedilmiş (dolu) bir hedef YALNIZ boş kategori onarımı için
    # istenebilir (K4: her kategori >= 1 fikir); aksi halde prompta eklenemez.
    persisted_target_set = set(retry_snapshot.persisted_target_ids_at_start)
    empty_category_set = set(retry_snapshot.empty_category_ids)
    for assign in retry_snapshot.plan.assignments:
        if (
            assign.target_id in persisted_target_set
            and assign.category_id not in empty_category_set
        ):
            raise SocialIdeaRetryWorkerInputError(
                "Başlangıçta zaten kaydedilmiş bir hedef retry promptuna eklenemez.",
                error_code="IDEA_RETRY_WORKER_INPUT_INCONSISTENT",
            )

    prompt_inputs_list: list[SocialIdeaPromptInput] = []
    for cp in source_plan.category_plans:
        cid = cp.category_id
        assigned_tids = cat_assignments[cid]
        if not assigned_tids:
            # Boş target_specs içeren prompt oluşturulmaz
            continue

        # Hedefleri canonical_target_ids sırasına göre sırala
        sorted_tids = sorted(assigned_tids, key=lambda tid: canonical_target_ids.index(tid))
        target_specs = []
        for tid in sorted_tids:
            t = target_map[tid]
            target_specs.append(
                IdeaTargetSpec(
                    target_id=t.id,
                    platform=t.platform,
                    content_format=t.content_format,
                    requested_count=1,
                )
            )

        cat = cat_map[cid]
        prompt_inputs_list.append(
            SocialIdeaPromptInput(
                attempt_id=attempt.id,
                category_id=cid,
                category_name=cat.category_name,
                category_description=cat.description,
                brand_name_snapshot=brief.brand_name_snapshot,
                brand_context_snapshot=brief.brand_context_snapshot,
                keywords=keyword_snapshots_tuple,
                target_specs=tuple(target_specs),
            )
        )

    return SocialIdeaRetryWorkerPreparation(
        attempt_id=attempt.id,
        task_id=task_id,
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        source_attempt_id=source_attempt.id,
        already_completed=False,
        canonical_target_ids=retry_snapshot.canonical_target_ids,
        persisted_target_ids_at_start=retry_snapshot.persisted_target_ids_at_start,
        missing_target_ids=retry_snapshot.missing_target_ids,
        prompt_inputs=tuple(prompt_inputs_list),
    )
