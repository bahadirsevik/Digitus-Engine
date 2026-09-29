# -*- coding: utf-8 -*-
"""Sosyal Brief Fikir Üretim Worker Input Hazırlığı (F1-F.6a).

Bu modül Celery worker'ın AI çağrısından önce ihtiyaç duyacağı güvenilir,
kanonik ve doğrulanmış DB snapshot'ını hazırlar ve attempt'i claim eder.

Kurallar:
- Celery task içermez.
- AI çağrısı yapmaz.
- API endpoint içermez.
- SocialIdea persist etmez / oluşturmaz.
- db.begin(), commit() veya rollback() ÇAĞIRMAZ.
- Attempt success/failure finalize etmez.
- coverage alanını değiştirmez.
- DB'yi (ve attempt coverage plan snapshot'ını) tek otorite kabul eder.
- Global kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
- Completed replay yolunda prompt_inputs=() döner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.social.idea_contract import IdeaTargetSpec
from app.core.social.idea_persistence import (
    SocialIdeaCategoryPlanSnapshot,
    SocialIdeaPlanSnapshot,
    extract_social_idea_plan_snapshot,
)
from app.database.models import (
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
)
from app.generators.social.attempt_state import claim_ideas_attempt_for_worker
from app.generators.social.brief_idea_prompt import (
    IdeaKeywordSnapshot,
    SocialIdeaPromptInput,
)
from app.generators.social.format_matrix import get_platform_format


class SocialIdeaWorkerInputError(ValueError):
    """Worker input hazırlık domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "WORKER_INPUT_INCONSISTENT",
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
            f"SocialIdeaWorkerInputError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class SocialIdeaWorkerPreparation:
    """Worker hazırlık aşaması nihai çıktısı (immutable)."""

    attempt_id: int
    task_id: str
    brief_id: int
    scoring_run_id: int
    already_completed: bool
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


def prepare_social_idea_worker_inputs(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> SocialIdeaWorkerPreparation:
    """Ideas attempt'ini worker için claim eder ve AI çağrısı için doğrulanmış girdileri hazırlar.

    İşlem sırası:
    1. Girdi doğrulama (task_id, attempt_id).
    2. Canonical kilit altında attempt'in claim edilmesi:
       ScoringRun -> SocialBrief -> SocialGenerationAttempt.
    3. Attempt.coverage üzerinden kanonik plan snapshot'ının fail-closed doğrulanması.
    4. Completed attempt ise: already_completed=True, prompt_inputs=() dönülmesi.
    5. Running durumda DB otoritesinden brief, kategori, keyword ve target'ların doğrulanması.
    6. Her plan kategorisi için immutable SocialIdeaPromptInput DTO'larının oluşturulması.

    Transaction sözleşmesi:
    - db.begin(), commit() veya rollback() ÇAĞIRMAZ.
    - Sadece claim durumunda db.flush() yapılır.
    - Transaction yönetimi tamamen çağıran katmana aittir.
    """
    current_time = _validate_now(now)

    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialIdeaWorkerInputError(
            "task_id boş olmayan bir string olmalıdır.",
            error_code="WORKER_INPUT_INVALID",
            field="task_id",
        )

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialIdeaWorkerInputError(
            "attempt_id pozitif bir tamsayı olmalıdır.",
            error_code="WORKER_INPUT_INVALID",
            field="attempt_id",
        )

    # 1. Canonical kilit ve attempt claim
    attempt, brief, scoring_run, already_completed = claim_ideas_attempt_for_worker(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        now=current_time,
    )

    # 2. Plan snapshot'ının doğrulanması (ortak canonical parser)
    plan = extract_social_idea_plan_snapshot(attempt)

    # 3. Zaten tamamlanmış attempt yolu (Completed Replay)
    if already_completed:
        return SocialIdeaWorkerPreparation(
            attempt_id=attempt.id,
            task_id=task_id,
            brief_id=brief.id,
            scoring_run_id=scoring_run.id,
            already_completed=True,
            prompt_inputs=(),
        )

    # 4. DB Varlıklarının Yüklenmesi ve Bütünlük Doğrulaması
    # 4a. Brief tazelik kontrolü
    if brief.is_stale is not False:
        raise SocialIdeaWorkerInputError(
            "Brief stale durumda; worker input hazırlanamaz.",
            error_code="WORKER_INPUT_INCONSISTENT",
        )
    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise SocialIdeaWorkerInputError(
            "Brief kanal atama sürümü güncel değil; worker input hazırlanamaz.",
            error_code="WORKER_INPUT_INCONSISTENT",
        )

    # 4b. Kategoriler
    cat_rows = (
        db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .all()
    )
    cat_map: dict[int, SocialCategory] = {c.id: c for c in cat_rows}

    # Plan kategorilerini plan sırasıyla denetle
    ordered_categories: list[SocialCategory] = []
    for cp in plan.categories:
        if cp.category_id not in cat_map:
            raise SocialIdeaWorkerInputError(
                f"Kategori {cp.category_id} brief altında bulunamadı.",
                error_code="WORKER_INPUT_INCONSISTENT",
                category_id=cp.category_id,
            )
        cat = cat_map[cp.category_id]
        if cat.scoring_run_id != scoring_run.id:
            raise SocialIdeaWorkerInputError(
                f"Kategori {cp.category_id} farklı scoring run'a ait.",
                error_code="WORKER_INPUT_INCONSISTENT",
                category_id=cp.category_id,
            )
        if cat.is_stale is not False:
            raise SocialIdeaWorkerInputError(
                f"Kategori {cp.category_id} stale durumda.",
                error_code="WORKER_INPUT_INCONSISTENT",
                category_id=cp.category_id,
            )

        name = cat.category_name
        if not isinstance(name, str) or len(name) == 0 or name != name.strip() or len(name) > 100:
            raise SocialIdeaWorkerInputError(
                f"Kategori {cp.category_id} adı geçersiz veya sınır dışı (1-100 karakter, trimlenmiş).",
                error_code="WORKER_INPUT_INCONSISTENT",
                category_id=cp.category_id,
            )

        desc = cat.description
        if not isinstance(desc, str) or len(desc) == 0 or desc != desc.strip() or len(desc) > 2000:
            raise SocialIdeaWorkerInputError(
                f"Kategori {cp.category_id} açıklaması geçersiz veya sınır dışı (1-2000 karakter, trimlenmiş).",
                error_code="WORKER_INPUT_INCONSISTENT",
                category_id=cp.category_id,
            )

        ordered_categories.append(cat)

    # 4c. Keywords
    kw_rows = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
    )
    if not (1 <= len(kw_rows) <= 5):
        raise SocialIdeaWorkerInputError(
            f"Brief keyword sayısı 1-5 arasında olmalıdır (mevcut: {len(kw_rows)}).",
            error_code="WORKER_INPUT_INCONSISTENT",
        )

    # Pozisyon sıralaması ve kesintisiz 0..n-1 kontrolü
    sorted_kws = sorted(kw_rows, key=lambda k: k.position if k.position is not None else -1)
    expected_positions = list(range(len(sorted_kws)))
    actual_positions = [k.position for k in sorted_kws]
    if actual_positions != expected_positions:
        raise SocialIdeaWorkerInputError(
            "Brief keyword pozisyonları 0..n-1 aralığında kesintisiz olmalıdır.",
            error_code="WORKER_INPUT_INCONSISTENT",
        )

    seen_kw_ids: set[int] = set()
    kw_snapshots: list[IdeaKeywordSnapshot] = []
    for k in sorted_kws:
        kid = k.keyword_id
        if isinstance(kid, bool) or not isinstance(kid, int) or kid <= 0:
            raise SocialIdeaWorkerInputError(
                "Brief keyword_id pozitif tamsayı olmalıdır.",
                error_code="WORKER_INPUT_INCONSISTENT",
            )
        if kid in seen_kw_ids:
            raise SocialIdeaWorkerInputError(
                "Brief içinde mükerrer keyword_id tespit edildi.",
                error_code="WORKER_INPUT_INCONSISTENT",
            )
        seen_kw_ids.add(kid)

        snap = k.keyword_snapshot
        if not isinstance(snap, str) or not snap.strip():
            raise SocialIdeaWorkerInputError(
                "Brief keyword_snapshot boş veya whitespace-only olamaz.",
                error_code="WORKER_INPUT_INCONSISTENT",
            )

        kw_snapshots.append(
            IdeaKeywordSnapshot(
                keyword_id=kid,
                keyword_snapshot=snap,
                position=k.position,
            )
        )
    keyword_snapshots_tuple = tuple(kw_snapshots)

    # 4d. Targets
    target_rows = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief.id)
        .all()
    )
    if not (1 <= len(target_rows) <= 6):
        raise SocialIdeaWorkerInputError(
            f"Brief target sayısı 1-6 arasında olmalıdır (mevcut: {len(target_rows)}).",
            error_code="WORKER_INPUT_INCONSISTENT",
        )

    target_map: dict[int, SocialBriefTarget] = {t.id: t for t in target_rows}
    if len(target_map) != len(target_rows):
        raise SocialIdeaWorkerInputError(
            "Brief hedefleri içinde mükerrer ID tespit edildi.",
            error_code="WORKER_INPUT_INCONSISTENT",
        )

    for t in target_rows:
        if get_platform_format(t.platform, t.content_format) is None:
            raise SocialIdeaWorkerInputError(
                f"Hedef platform/format kombinasyonu ({t.platform}, {t.content_format}) kanonik matriste geçersiz.",
                error_code="WORKER_INPUT_INCONSISTENT",
            )

    # Planda kapsanan tüm target ID'ler brief altında mevcut olmalı
    for tid in plan.covered_target_ids:
        if tid not in target_map:
            raise SocialIdeaWorkerInputError(
                f"Planda kapsanan hedef {tid} brief hedefleri arasında bulunamadı.",
                error_code="WORKER_INPUT_INCONSISTENT",
            )

    # 5. Her Kategori İçin SocialIdeaPromptInput Üretimi
    prompt_inputs_list: list[SocialIdeaPromptInput] = []
    for cp, cat in zip(plan.categories, ordered_categories):
        # Yalnızca o kategoriye ait ve plan kotası > 0 olan hedefler
        cat_target_specs: list[IdeaTargetSpec] = []
        for tid, count in cp.target_quotas:
            if count > 0:
                if tid not in target_map:
                    raise SocialIdeaWorkerInputError(
                        f"Kategori {cp.category_id} hedefi ({tid}) brief hedefleri arasında bulunamadı.",
                        error_code="WORKER_INPUT_INCONSISTENT",
                        category_id=cp.category_id,
                    )
                t = target_map[tid]
                cat_target_specs.append(
                    IdeaTargetSpec(
                        target_id=t.id,
                        platform=t.platform,
                        content_format=t.content_format,
                        requested_count=count,
                    )
                )

        prompt_input = SocialIdeaPromptInput(
            attempt_id=attempt.id,
            category_id=cp.category_id,
            category_name=cat.category_name,
            category_description=cat.description,
            brand_name_snapshot=brief.brand_name_snapshot,
            brand_context_snapshot=brief.brand_context_snapshot,
            keywords=keyword_snapshots_tuple,
            target_specs=tuple(cat_target_specs),
        )
        prompt_inputs_list.append(prompt_input)

    return SocialIdeaWorkerPreparation(
        attempt_id=attempt.id,
        task_id=task_id,
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        already_completed=False,
        prompt_inputs=tuple(prompt_inputs_list),
    )
