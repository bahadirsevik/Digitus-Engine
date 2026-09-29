# -*- coding: utf-8 -*-
"""Atomik Kategori Persistence ve Başarı Finalizasyonu (F1-E.4).

Bu modül F1-E.3 tarafından üretilmiş ve doğrulanmış kategorileri SocialCategory
tablosuna atomik, idempotent ve geç-worker güvenli biçimde kaydeder; aynı transaction
içinde categories attempt'ini completed durumuna getirir.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- commit veya rollback çağırmaz.
- AI çağrısı yapmaz.
- HTTPException kullanmaz.
- Başarı sonunda db.flush() edebilir.
- Transaction sahipliği çağıran katmandadır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.social.brief_service import SocialBriefNotFoundError
from app.core.social.category_contract import (
    CANONICAL_CATEGORY_TYPES,
    ValidatedSocialCategory,
)
from app.core.social.category_flow import SocialCategoryGenerationStart
from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialCategory,
    SocialGenerationAttempt,
)
from app.generators.social.attempt_state import (
    finish_attempt,
    lock_categories_attempt_for_finalize,
)
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.generators.social.brief_category_generator import SocialCategoryAIResult


class SocialCategoryPersistenceError(ValueError):
    """Sosyal kategori persistence domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        category_index: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.category_index = category_index

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        if self.category_index is not None:
            parts.append(f"(category_index={self.category_index})")
        return " ".join(parts)


@dataclass(frozen=True)
class PersistedSocialCategory:
    """Veritabanına kaydedilmiş veya mevcut doğrulanmış kategori kaydı (immutable)."""

    id: int
    category_name: str
    category_type: str
    description: str
    relevance_score: float
    suggested_keyword_ids: tuple[int, ...]


@dataclass(frozen=True)
class PersistedSocialCategoriesResult:
    """Kategori persistence nihai sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    attempt_id: int
    category_ids: tuple[int, ...]
    categories: tuple[PersistedSocialCategory, ...]
    already_completed: bool


def _validate_persistence_relevance_score(
    value: Any,
    *,
    error_code: str,
    category_index: int,
) -> float:
    """relevance_score değerini güvenli biçimde doğrular ve float'a çevirir.

    Taşma ve tip hatalarını (OverflowError, ValueError, TypeError) yakalar,
    asla ham exception veya dinamik kullanıcı girdisi sızdırmaz.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SocialCategoryPersistenceError(
            "relevance_score 0 ile 1 arasında sonlu sayı olmalıdır.",
            error_code=error_code,
            field="relevance_score",
            category_index=category_index,
        )

    try:
        float_val = float(value)
    except (OverflowError, ValueError, TypeError):
        raise SocialCategoryPersistenceError(
            "relevance_score geçerli bir sonlu sayı olmalıdır.",
            error_code=error_code,
            field="relevance_score",
            category_index=category_index,
        )

    if not math.isfinite(float_val) or not (0.0 <= float_val <= 1.0):
        raise SocialCategoryPersistenceError(
            "relevance_score 0 ile 1 arasında sonlu sayı olmalıdır.",
            error_code=error_code,
            field="relevance_score",
            category_index=category_index,
        )

    return float_val


def _validate_and_build_persisted_categories(
    existing_cats: list[SocialCategory],
    *,
    brief_id: int,
    scoring_run_id: int,
    db_keyword_ids: set[int],
    max_categories: int = 6,
) -> list[PersistedSocialCategory]:
    """Mevcut kategori satırlarını F1-E.4 kurallarıyla doğrular ve PersistedSocialCategory listesi döner.

    Fail-closed: her türlü kural dışı durumda CATEGORY_PERSISTENCE_INCONSISTENT fırlatır.
    """
    if not (2 <= len(existing_cats) <= max_categories):
        raise SocialCategoryPersistenceError(
            "Completed attempt için kayıtlı kategori sayısı sınırların dışındadır.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    existing_seen_names: set[str] = set()
    persisted_existing: list[PersistedSocialCategory] = []
    for idx, cat in enumerate(existing_cats):
        if cat.brief_id != brief_id or cat.scoring_run_id != scoring_run_id:
            raise SocialCategoryPersistenceError(
                "Mevcut kategori brief veya run uyuşmazlığı.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )
        if cat.is_stale is not False:
            raise SocialCategoryPersistenceError(
                "Mevcut kategori stale durumda.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )
        cat_name = cat.category_name
        if (
            isinstance(cat_name, bool)
            or not isinstance(cat_name, str)
            or len(cat_name) == 0
            or cat_name != cat_name.strip()
            or len(cat_name) > 100
        ):
            raise SocialCategoryPersistenceError(
                "Mevcut kategori adı başta/sonda boşluk olmayan 1-100 karakter arası bir metin olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )
        folded = cat_name.casefold()
        if folded in existing_seen_names:
            raise SocialCategoryPersistenceError(
                "Mevcut kategoriler arasında mükerrer isim var.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )
        existing_seen_names.add(folded)

        if cat.category_type not in CANONICAL_CATEGORY_TYPES:
            raise SocialCategoryPersistenceError(
                "Mevcut kategori tipi kanonik değil.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )
        cat_desc = cat.description
        if (
            isinstance(cat_desc, bool)
            or not isinstance(cat_desc, str)
            or len(cat_desc) == 0
            or cat_desc != cat_desc.strip()
            or len(cat_desc) > 2000
        ):
            raise SocialCategoryPersistenceError(
                "Mevcut kategori açıklaması başta/sonda boşluk olmayan 1-2000 karakter arası bir metin olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )

        valid_rel_score = _validate_persistence_relevance_score(
            cat.relevance_score,
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
            category_index=idx,
        )

        kw_ids = cat.suggested_keyword_ids
        if not isinstance(kw_ids, list) or len(kw_ids) == 0:
            raise SocialCategoryPersistenceError(
                "Mevcut kategori suggested_keyword_ids geçersiz.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                category_index=idx,
            )
        seen_kws = set()
        for kid in kw_ids:
            if isinstance(kid, bool) or not isinstance(kid, int) or kid <= 0:
                raise SocialCategoryPersistenceError(
                    "Mevcut kategori keyword ID geçersiz.",
                    error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                    category_index=idx,
                )
            if kid in seen_kws:
                raise SocialCategoryPersistenceError(
                    "Mevcut kategori içinde mükerrer keyword ID var.",
                    error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                    category_index=idx,
                )
            seen_kws.add(kid)
            if kid not in db_keyword_ids:
                raise SocialCategoryPersistenceError(
                    "Mevcut kategori brief dışı keyword referansı içeriyor.",
                    error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                    category_index=idx,
                )
        persisted_existing.append(
            PersistedSocialCategory(
                id=cat.id,
                category_name=cat.category_name,
                category_type=cat.category_type,
                description=cat.description,
                relevance_score=valid_rel_score,
                suggested_keyword_ids=tuple(kw_ids),
            )
        )
    return persisted_existing


def persist_social_categories(
    db: Session,
    *,
    start: SocialCategoryGenerationStart,
    ai_result: SocialCategoryAIResult,
    now: datetime | None = None,
) -> PersistedSocialCategoriesResult:
    """Doğrulanmış kategorileri atomik olarak kaydeder ve attempt'i completed yapar.

    Transaction sözleşmesi:
    - db.begin(), commit() veya rollback() ÇAĞIRMAZ.
    - Başarı sonunda db.flush() çağrılır.
    - Transaction yönetimi tamamen çağıran katmana aittir.

    İdempotency:
    - Attempt zaten completed ise mevcut geçerli kategorileri yükler ve
      already_completed=True döner; yeni satır eklemez.
    """
    # 1. Girdi tutarlılığı kontrolleri (start)
    if not isinstance(start, SocialCategoryGenerationStart):
        raise SocialCategoryPersistenceError(
            "start parametresi SocialCategoryGenerationStart nesnesi olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
        )

    if start.attempt_created is not True:
        raise SocialCategoryPersistenceError(
            "start.attempt_created True olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="attempt_created",
        )

    if (
        isinstance(start.max_categories, bool)
        or not isinstance(start.max_categories, int)
        or not (2 <= start.max_categories <= 6)
    ):
        raise SocialCategoryPersistenceError(
            "start.max_categories 2 ile 6 arasında tamsayı olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="max_categories",
        )

    if start.attempt_status != "pending":
        raise SocialCategoryPersistenceError(
            "start.attempt_status 'pending' olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="attempt_status",
        )

    if (
        isinstance(start.attempt_id, bool)
        or not isinstance(start.attempt_id, int)
        or start.attempt_id <= 0
    ):
        raise SocialCategoryPersistenceError(
            "start.attempt_id pozitif tamsayı olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="attempt_id",
        )

    if (
        isinstance(start.brief_id, bool)
        or not isinstance(start.brief_id, int)
        or start.brief_id <= 0
    ):
        raise SocialCategoryPersistenceError(
            "start.brief_id pozitif tamsayı olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="brief_id",
        )

    if (
        isinstance(start.scoring_run_id, bool)
        or not isinstance(start.scoring_run_id, int)
        or start.scoring_run_id <= 0
    ):
        raise SocialCategoryPersistenceError(
            "start.scoring_run_id pozitif tamsayı olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="scoring_run_id",
        )

    if (
        isinstance(start.brand_profile_id, bool)
        or not isinstance(start.brand_profile_id, int)
        or start.brand_profile_id <= 0
    ):
        raise SocialCategoryPersistenceError(
            "start.brand_profile_id pozitif tamsayı olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="brand_profile_id",
        )

    if not isinstance(start.keywords, tuple) or len(start.keywords) == 0:
        raise SocialCategoryPersistenceError(
            "start.keywords boş olamaz.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="keywords",
        )

    # 2. Girdi tutarlılığı kontrolleri (ai_result)
    from app.generators.social.brief_category_generator import SocialCategoryAIResult

    if not isinstance(ai_result, SocialCategoryAIResult):
        raise SocialCategoryPersistenceError(
            "ai_result parametresi SocialCategoryAIResult nesnesi olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
        )

    if ai_result.attempt_id != start.attempt_id:
        raise SocialCategoryPersistenceError(
            "ai_result.attempt_id ile start.attempt_id uyuşmuyor.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="attempt_id",
        )

    if (
        isinstance(ai_result.ai_calls_used, bool)
        or not isinstance(ai_result.ai_calls_used, int)
        or not (1 <= ai_result.ai_calls_used <= 2)
    ):
        raise SocialCategoryPersistenceError(
            "ai_result.ai_calls_used 1 veya 2 olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="ai_calls_used",
        )

    if not isinstance(ai_result.categories, tuple):
        raise SocialCategoryPersistenceError(
            "ai_result.categories bir tuple olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="categories",
        )

    if not (2 <= len(ai_result.categories) <= start.max_categories):
        raise SocialCategoryPersistenceError(
            "Kategori sayısı sınırların dışındadır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            field="categories",
        )

    # 3. Kategori öğeleri doğrulama
    seen_category_names: set[str] = set()
    for cat_idx, cat in enumerate(ai_result.categories):
        if not isinstance(cat, ValidatedSocialCategory):
            raise SocialCategoryPersistenceError(
                "Kategori öğesi ValidatedSocialCategory olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                category_index=cat_idx,
            )

        cat_name = cat.category_name
        if (
            isinstance(cat_name, bool)
            or not isinstance(cat_name, str)
            or len(cat_name) == 0
            or cat_name != cat_name.strip()
            or len(cat_name) > 100
        ):
            raise SocialCategoryPersistenceError(
                "Kategori adı başta/sonda boşluk olmayan 1-100 karakter arası bir metin olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                field="category_name",
                category_index=cat_idx,
            )
        folded_name = cat_name.casefold()
        if folded_name in seen_category_names:
            raise SocialCategoryPersistenceError(
                "Mükerrer kategori adı tespit edildi.",
                error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                field="category_name",
                category_index=cat_idx,
            )
        seen_category_names.add(folded_name)

        if cat.category_type not in CANONICAL_CATEGORY_TYPES:
            raise SocialCategoryPersistenceError(
                "Kategori tipi kanonik allowlist içinde olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                field="category_type",
                category_index=cat_idx,
            )

        desc = cat.description
        if (
            isinstance(desc, bool)
            or not isinstance(desc, str)
            or len(desc) == 0
            or desc != desc.strip()
            or len(desc) > 2000
        ):
            raise SocialCategoryPersistenceError(
                "Kategori açıklaması başta/sonda boşluk olmayan 1-2000 karakter arası bir metin olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                field="description",
                category_index=cat_idx,
            )

        _validate_persistence_relevance_score(
            cat.relevance_score,
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
            category_index=cat_idx,
        )

        kw_ids = cat.suggested_keyword_ids
        if not isinstance(kw_ids, tuple) or len(kw_ids) == 0:
            raise SocialCategoryPersistenceError(
                "suggested_keyword_ids boş olmayan bir tuple olmalıdır.",
                error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                field="suggested_keyword_ids",
                category_index=cat_idx,
            )
        seen_kw_ids: set[int] = set()
        for kw_id in kw_ids:
            if isinstance(kw_id, bool) or not isinstance(kw_id, int) or kw_id <= 0:
                raise SocialCategoryPersistenceError(
                    "suggested_keyword_ids pozitif tamsayılar içermelidir.",
                    error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                    field="suggested_keyword_ids",
                    category_index=cat_idx,
                )
            if kw_id in seen_kw_ids:
                raise SocialCategoryPersistenceError(
                    "Kategori içinde mükerrer keyword_id tespit edildi.",
                    error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
                    field="suggested_keyword_ids",
                    category_index=cat_idx,
                )
            seen_kw_ids.add(kw_id)

    # 4. Attempt kilitleme (Global kilit sırası: ScoringRun -> SocialBrief -> SocialGenerationAttempt)
    locked_attempt, locked_brief, locked_run, already_completed = (
        lock_categories_attempt_for_finalize(
            db, attempt_id=start.attempt_id, now=now
        )
    )

    # 5. DB kimlik ve üyelik doğrulaması
    if (
        locked_attempt.id != start.attempt_id
        or locked_attempt.brief_id != start.brief_id
        or locked_brief.id != start.brief_id
        or locked_brief.scoring_run_id != start.scoring_run_id
        or locked_run.id != start.scoring_run_id
        or locked_run.brand_profile_id != start.brand_profile_id
        or locked_attempt.stage != "categories"
    ):
        raise SocialCategoryPersistenceError(
            "Attempt, brief veya workspace kimlik bilgileri eşleşmiyor.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    # Brief'e bağlı SocialBriefKeyword satırlarını position ASC, id ASC yükle
    db_keywords = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == locked_brief.id)
        .order_by(SocialBriefKeyword.position.asc(), SocialBriefKeyword.id.asc())
        .all()
    )

    if not db_keywords:
        raise SocialCategoryPersistenceError(
            "Brief için anahtar kelime kaydı bulunamadı.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    # Snapshot position dizisi 0'dan başlayan kesintisiz sıra olmalı
    expected_positions = list(range(len(db_keywords)))
    actual_positions = [k.position for k in db_keywords]
    if actual_positions != expected_positions:
        raise SocialCategoryPersistenceError(
            "Brief anahtar kelime pozisyonları kesintisiz değil.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    # DB'deki keyword ID kümesi ile start.keywords ID kümesi tam eşleşmeli
    db_keyword_ids = {k.keyword_id for k in db_keywords}
    start_keyword_ids = {k.keyword_id for k in start.keywords}
    if None in db_keyword_ids or db_keyword_ids != start_keyword_ids:
        raise SocialCategoryPersistenceError(
            "Brief anahtar kelime kümesi start snapshot ile uyuşmuyor.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    if len(db_keywords) != len(start.keywords):
        raise SocialCategoryPersistenceError(
            "Brief anahtar kelime sayısı start snapshot ile uyuşmuyor.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    for db_kw, start_kw in zip(db_keywords, start.keywords):
        if (
            db_kw.keyword_id != start_kw.keyword_id
            or db_kw.keyword_snapshot != start_kw.keyword_snapshot
            or db_kw.position != start_kw.position
        ):
            raise SocialCategoryPersistenceError(
                "Brief anahtar kelime snapshot değerleri start snapshot ile birebir eşleşmiyor.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
            )

    # Her category.suggested_keyword_ids yalnız bu DB keyword kümesinden gelmeli
    for cat_idx, cat in enumerate(ai_result.categories):
        out_of_brief = set(cat.suggested_keyword_ids) - db_keyword_ids
        if out_of_brief:
            raise SocialCategoryPersistenceError(
                "Kategori brief dışı anahtar kelime referansı içeriyor.",
                error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
                field="suggested_keyword_ids",
                category_index=cat_idx,
            )

    # 6. Completed idempotent yol
    if already_completed:
        existing_cats = (
            db.query(SocialCategory)
            .filter(SocialCategory.brief_id == locked_brief.id)
            .order_by(SocialCategory.id.asc())
            .all()
        )
        persisted_existing = _validate_and_build_persisted_categories(
            existing_cats,
            brief_id=locked_brief.id,
            scoring_run_id=locked_brief.scoring_run_id,
            db_keyword_ids=db_keyword_ids,
            max_categories=start.max_categories,
        )

        return PersistedSocialCategoriesResult(
            brief_id=locked_brief.id,
            scoring_run_id=locked_brief.scoring_run_id,
            attempt_id=locked_attempt.id,
            category_ids=tuple(c.id for c in persisted_existing),
            categories=tuple(persisted_existing),
            already_completed=True,
        )

    # 7. Pending attempt yeni kayıt yolu
    existing_count = (
        db.query(SocialCategory)
        .filter(SocialCategory.brief_id == locked_brief.id)
        .count()
    )
    if existing_count > 0:
        raise SocialCategoryPersistenceError(
            "Brief için önceden oluşturulmuş kategori kayıtları mevcut.",
            error_code="CATEGORY_PERSISTENCE_CONFLICT",
        )

    added_records: list[SocialCategory] = []
    for cat in ai_result.categories:
        record = SocialCategory(
            scoring_run_id=locked_brief.scoring_run_id,
            brief_id=locked_brief.id,
            category_name=cat.category_name,
            category_type=cat.category_type,
            description=cat.description,
            relevance_score=float(cat.relevance_score),
            suggested_keyword_ids=list(cat.suggested_keyword_ids),
            is_stale=False,
        )
        db.add(record)
        added_records.append(record)

    db.flush()

    persisted_cats = tuple(
        PersistedSocialCategory(
            id=r.id,
            category_name=r.category_name,
            category_type=r.category_type,
            description=r.description,
            relevance_score=float(r.relevance_score),
            suggested_keyword_ids=tuple(r.suggested_keyword_ids),
        )
        for r in added_records
    )

    used_keyword_ids = sorted(
        {kid for cat in ai_result.categories for kid in cat.suggested_keyword_ids}
    )
    coverage_payload = {
        "category_count": len(persisted_cats),
        "keyword_ids_used": used_keyword_ids,
        "ai_calls_used": ai_result.ai_calls_used,
    }

    finish_attempt(
        db,
        attempt_id=locked_attempt.id,
        status="completed",
        task_id=None,
        coverage=coverage_payload,
        warnings=[],
        reason_code=None,
        now=now,
    )
    locked_attempt.reason_code = None

    return PersistedSocialCategoriesResult(
        brief_id=locked_brief.id,
        scoring_run_id=locked_brief.scoring_run_id,
        attempt_id=locked_attempt.id,
        category_ids=tuple(c.id for c in persisted_cats),
        categories=persisted_cats,
        already_completed=False,
    )


def load_completed_social_categories(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
) -> PersistedSocialCategoriesResult:
    """Completed categories attempt'ine ait kategorileri salt-okunur ve doğrulanmış olarak yükler.

    Workspace izolasyonu:
    - Brief, ScoringRun ve BrandProfile join edilerek brand_profile_id ve deleted_at kontrol edilir.
    - Bulunamazsa veya başka workspace'e aitse SocialBriefNotFoundError fırlatır (404 semantiği).

    Attempt doğrulaması:
    - stage == 'categories'
    - status == 'completed'
    - brief_id ve scoring_run_id brief ile eşleşmeli.
    - Geçersizse CATEGORY_PERSISTENCE_INCONSISTENT fırlatır.

    Kategori doğrulaması:
    - id ASC sırada çekilir.
    - _validate_and_build_persisted_categories ile F1-E.4 kuralları doğrulanır (2-6 kategori).

    Sözleşme:
    - commit/rollback yapmaz.
    - Satır mutate etmez.
    """
    if (
        isinstance(brief_id, bool)
        or not isinstance(brief_id, int)
        or brief_id <= 0
        or isinstance(attempt_id, bool)
        or not isinstance(attempt_id, int)
        or attempt_id <= 0
        or isinstance(brand_profile_id, bool)
        or not isinstance(brand_profile_id, int)
        or brand_profile_id <= 0
    ):
        raise SocialCategoryPersistenceError(
            "brief_id, attempt_id ve brand_profile_id pozitif integer olmalıdır.",
            error_code="CATEGORY_PERSISTENCE_INVALID_INPUT",
        )

    # 1. Workspace izolasyonlu brief sorgusu
    stmt = (
        select(SocialBrief)
        .join(ScoringRun, SocialBrief.scoring_run_id == ScoringRun.id)
        .join(BrandProfile, ScoringRun.brand_profile_id == BrandProfile.id)
        .where(
            SocialBrief.id == brief_id,
            ScoringRun.brand_profile_id == brand_profile_id,
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
    )
    brief = db.scalars(stmt).first()
    if brief is None:
        raise SocialBriefNotFoundError()

    # 2. Attempt sorgusu ve doğrulaması
    attempt = (
        db.query(SocialGenerationAttempt)
        .filter(
            SocialGenerationAttempt.id == attempt_id,
            SocialGenerationAttempt.brief_id == brief.id,
            SocialGenerationAttempt.stage == "categories",
        )
        .first()
    )
    if attempt is None or attempt.status != "completed":
        raise SocialCategoryPersistenceError(
            "Completed categories attempt bulunamadı veya geçersiz durumda.",
            error_code="CATEGORY_PERSISTENCE_INCONSISTENT",
        )

    # 3. Brief keyword ID'lerini topla
    db_keywords = (
        db.query(SocialBriefKeyword.keyword_id)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
    )
    db_keyword_ids = {k.keyword_id for k in db_keywords}

    # 4. Kategorileri yükle ve doğrula
    existing_cats = (
        db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief.id)
        .order_by(SocialCategory.id.asc())
        .all()
    )

    persisted = _validate_and_build_persisted_categories(
        existing_cats,
        brief_id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        db_keyword_ids=db_keyword_ids,
        max_categories=6,
    )

    return PersistedSocialCategoriesResult(
        brief_id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        attempt_id=attempt.id,
        category_ids=tuple(c.id for c in persisted),
        categories=tuple(persisted),
        already_completed=True,
    )
