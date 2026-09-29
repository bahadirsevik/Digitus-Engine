# -*- coding: utf-8 -*-
"""Atomik Tekil Sosyal İçerik Persistence Modülü (Mikro Faz F1-G.5.1).

Bu modül seçilmiş bir SocialIdea için üretilmiş ve nihai kalite kararı 'accept' olan
tek bir ValidatedSocialContent nesnesini mevcut SocialContent tablosuna atomik,
brief-aware, idempotent, eşzamanlı yarışa dayanıklı ve fail-closed biçimde kaydeder.

Kurallar (plan_social_brief_akisi.md rev.4 §4, §6, §9 & F1-G.5.1 şartnamesi):
- Transaction'ı persistence fonksiyonu açmaz veya kapatmaz (db.begin() çağırmaz).
- db.commit() veya db.rollback() KESİNLİKLE çağırmaz.
- Başarılı yeni kayıt veya idempotent mevcut kayıt sonunda yalnız db.flush() çağrılır.
- Eşzamanlı yarışta partial unique index (uq_social_content_idea_brief) ihlali
  oluşursa izole savepoint (db.begin_nested()) kullanılarak dış transaction'ın
  aborted duruma düşmesi engellenir; kazanan satır doğrulanıp already_present=True dönülür.
- AI veya harici ağ çağrısı YAPILMAZ.
- HTTPException KESİNLİKLE kullanılmaz.
- Hata mesajlarında hiçbir raw kullanıcı metni, caption, hook, iddia, SQL veya DB constraint detayı sızdırılmaz.
- Legacy brief_id IS NULL satırlarına dokunulmaz.
- SocialContent modeline veya DB şemasına YENİ KOLON / MIGRATION EKLENMEZ.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.social.content_contract import (
    ALLOWED_HOOK_STYLES,
    ContentTargetSpec,
    PLATFORM_CAPTION_LIMITS,
    SocialContentOutputValidationError,
    ValidatedCarouselPayload,
    ValidatedHook,
    ValidatedSocialContent,
    ValidatedThreadPayload,
    ValidatedVideoPayload,
    serialize_content_format_payload,
    validate_social_content_output,
)
from app.core.social.content_quality import SocialContentQualityDecision
from app.database.models import (
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    AttemptNotFoundError,
    AttemptNotWritableError,
    BriefNotFoundError,
    lock_contents_attempt_for_content_write,
)
from app.generators.social.format_matrix import (
    get_duration_preset,
    get_platform_format,
)


# ==================== DOMAIN EXCEPTIONS ====================


class SocialContentPersistenceError(ValueError):
    """Sosyal içerik persistence domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        idea_id: int | None = None,
        brief_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.idea_id = idea_id
        self.brief_id = brief_id

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        if self.idea_id is not None:
            parts.append(f"(idea_id={self.idea_id})")
        if self.brief_id is not None:
            parts.append(f"(brief_id={self.brief_id})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialContentPersistenceError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r}, "
            f"idea_id={self.idea_id!r}, brief_id={self.brief_id!r})"
        )


# Sabit Hata Kodları
CONTENT_PERSISTENCE_INVALID_INPUT = "CONTENT_PERSISTENCE_INVALID_INPUT"
CONTENT_PERSISTENCE_INCONSISTENT = "CONTENT_PERSISTENCE_INCONSISTENT"
CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE = "CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE"
CONTENT_PERSISTENCE_REJECTED = "CONTENT_PERSISTENCE_REJECTED"
CONTENT_PERSISTENCE_CONFLICT = "CONTENT_PERSISTENCE_CONFLICT"


# ==================== DTO'LAR ====================


@dataclass(frozen=True)
class PersistedSocialContent:
    """Veritabanına kaydedilmiş veya doğrulanmış içerik kaydı (derinlemesine dondurulmuş / immutable)."""

    id: int
    idea_id: int
    brief_id: int
    target_id: int
    platform: str
    content_format: str
    hooks: tuple[ValidatedHook, ...]
    caption: str
    scenario: str | None
    format_payload: (
        ValidatedVideoPayload
        | ValidatedCarouselPayload
        | ValidatedThreadPayload
        | None
    )
    visual_suggestion: str | None
    video_concept: str | None
    cta_text: str | None
    hashtags: tuple[str, ...]
    industry_posting_suggestion: str | None
    platform_notes: str | None
    duration_status: str
    actual_duration_sec: int | None
    validation_warnings: tuple[str, ...]
    is_stale: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "hooks", tuple(self.hooks))
        object.__setattr__(self, "hashtags", tuple(self.hashtags))
        object.__setattr__(
            self,
            "validation_warnings",
            tuple(self.validation_warnings),
        )


def _is_idea_brief_unique_violation(exc: IntegrityError) -> bool:
    """PostgreSQL / SQLAlchemy IntegrityError'ın 'uq_social_content_idea_brief' kısıt ihlali olup olmadığını belirler."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    if diag is not None:
        c_name = getattr(diag, "constraint_name", None)
        if c_name is not None:
            return c_name == "uq_social_content_idea_brief"
    pgcode = getattr(orig, "pgcode", None)
    orig_str = str(orig or exc)
    if pgcode == "23505" and "uq_social_content_idea_brief" in orig_str:
        return True
    if "uq_social_content_idea_brief" in orig_str:
        return True
    return False


@dataclass(frozen=True)
class PersistedSocialContentResult:
    """İçerik persistence nihai sonucu (immutable)."""

    brief_id: int
    attempt_id: int
    idea_id: int
    content: PersistedSocialContent
    already_present: bool


# ==================== SAF PRIVATE VALIDATORLAR ====================


def _validate_content_quality_consistency(
    content: ValidatedSocialContent,
    quality_decision: SocialContentQualityDecision,
    *,
    idea_id: int,
    brief_id: int,
) -> None:
    """ValidatedSocialContent ve SocialContentQualityDecision arasındaki tutarlılığı fail-closed doğrular."""
    # 1. Action ve grounding kontrolü
    if quality_decision.action in ("reject", "repair"):
        raise SocialContentPersistenceError(
            "Reject veya repair kararı persistence katmanına kaydedilemez.",
            error_code=CONTENT_PERSISTENCE_REJECTED,
            field="quality_decision",
            idea_id=idea_id,
            brief_id=brief_id,
        )
    if quality_decision.action != "accept":
        raise SocialContentPersistenceError(
            "Yalnızca action='accept' olan içerik kaydedilebilir.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="quality_decision",
            idea_id=idea_id,
            brief_id=brief_id,
        )

    if quality_decision.grounding_clean is not True:
        raise SocialContentPersistenceError(
            "Grounding temiz olmayan içerik kaydedilemez.",
            error_code=CONTENT_PERSISTENCE_REJECTED,
            field="quality_decision",
            idea_id=idea_id,
            brief_id=brief_id,
        )

    if quality_decision.claims != ():
        raise SocialContentPersistenceError(
            "İddia (claim) içeren içerik kaydedilemez.",
            error_code=CONTENT_PERSISTENCE_REJECTED,
            field="quality_decision",
            idea_id=idea_id,
            brief_id=brief_id,
        )

    # 2. Süre tutarlılığı
    if content.duration_status == "mismatch":
        if not quality_decision.repair_attempted:
            raise SocialContentPersistenceError(
                "İlk kalite kararında duration mismatch bulunan içerik repair edilmeden kabul edilemez.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
        if quality_decision.duration_acceptable is not False:
            raise SocialContentPersistenceError(
                "Duration mismatch durumunda duration_acceptable False olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
        if quality_decision.reason_codes != ("duration_mismatch",):
            raise SocialContentPersistenceError(
                "Duration mismatch durumunda reason_codes ('duration_mismatch',) olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
        if "duration_mismatch" not in quality_decision.warnings:
            raise SocialContentPersistenceError(
                "Duration mismatch warning içinde yer almalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
    elif content.duration_status in ("valid", "not_applicable"):
        if quality_decision.duration_acceptable is not True:
            raise SocialContentPersistenceError(
                "Valid veya not_applicable durumunda duration_acceptable True olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
        if quality_decision.reason_codes != ():
            raise SocialContentPersistenceError(
                "Valid veya not_applicable durumunda reason_codes boş olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
        if "duration_mismatch" in quality_decision.warnings:
            raise SocialContentPersistenceError(
                "Valid veya not_applicable durumunda duration_mismatch uyarısı bulunamaz.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="quality_decision",
                idea_id=idea_id,
                brief_id=brief_id,
            )
    else:
        raise SocialContentPersistenceError(
            "Bilinmeyen veya geçersiz duration_status değeri.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="duration_status",
            idea_id=idea_id,
            brief_id=brief_id,
        )

    # 3. Validation soft warning'lerinin quality_decision.warnings içinde korunduğunu doğrula
    for w in content.validation_warnings:
        if w not in quality_decision.warnings:
            raise SocialContentPersistenceError(
                "İçerik doğrulama uyarısı kalite kararında korunmamış.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="warnings",
                idea_id=idea_id,
                brief_id=brief_id,
            )


def validate_and_build_persisted_social_content(
    row: SocialContent,
    *,
    target: SocialBriefTarget,
    idea: SocialIdea,
    brief: SocialBrief,
) -> PersistedSocialContent:
    """Veritabanındaki SocialContent satırını katı format ve sözleşme kurallarıyla doğrular ve PersistedSocialContent döner."""
    if row.brief_id != brief.id:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırı brief_id uyuşmazlığı içeriyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            idea_id=idea.id,
            brief_id=brief.id,
        )
    if row.idea_id != idea.id:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırı idea_id uyuşmazlığı içeriyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            idea_id=idea.id,
            brief_id=brief.id,
        )
    if row.is_stale is not False:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırı stale olarak işaretlenmiş.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            idea_id=idea.id,
            brief_id=brief.id,
        )

    # Hedef format ve süre preset'i kanonik matris kontrolü
    fmt_def = get_platform_format(target.platform, target.content_format)
    if fmt_def is None:
        raise SocialContentPersistenceError(
            "Hedef format kanonik matriste bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="content_format",
            idea_id=idea.id,
            brief_id=brief.id,
        )

    if fmt_def.requires_duration:
        if (
            not target.duration_preset_id
            or target.duration_min_sec is None
            or target.duration_max_sec is None
        ):
            raise SocialContentPersistenceError(
                "Video hedefi için süre preset ve sınırları zorunludur.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_preset_id",
                idea_id=idea.id,
                brief_id=brief.id,
            )
        preset_def = get_duration_preset(target.duration_preset_id)
        if preset_def is None or preset_def not in fmt_def.duration_presets:
            raise SocialContentPersistenceError(
                "Hedef süre preset'i kanonik matrisle uyuşmuyor.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_preset_id",
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if (
            target.duration_min_sec != preset_def.min_sec
            or target.duration_max_sec != preset_def.max_sec
        ):
            raise SocialContentPersistenceError(
                "Hedef süre sınırları kanonik preset değerleriyle uyuşmuyor.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_min_sec",
                idea_id=idea.id,
                brief_id=brief.id,
            )
    else:
        if (
            target.duration_preset_id is not None
            or target.duration_min_sec is not None
            or target.duration_max_sec is not None
        ):
            raise SocialContentPersistenceError(
                "Video dışı format için süre alanları NULL olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_preset_id",
                idea_id=idea.id,
                brief_id=brief.id,
            )

    target_spec = ContentTargetSpec(
        target_id=target.id,
        platform=target.platform,
        content_format=target.content_format,
        duration_preset_id=target.duration_preset_id,
        duration_min_sec=target.duration_min_sec,
        duration_max_sec=target.duration_max_sec,
    )

    row_dict: dict[str, Any] = {
        "hooks": row.hooks,
        "caption": row.caption,
        "cta_text": row.cta_text,
        "hashtags": row.hashtags,
        "format_payload": row.format_payload,
        "visual_suggestion": row.visual_suggestion,
        "video_concept": row.video_concept,
        "industry_posting_suggestion": row.industry_posting_suggestion,
        "platform_notes": row.platform_notes,
    }

    try:
        revalidated = validate_social_content_output(row_dict, target_spec)
    except SocialContentOutputValidationError as exc:
        raise SocialContentPersistenceError(
            "Mevcut veritabanı satırı içerik sözleşmesini ihlal ediyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field=exc.field,
            idea_id=idea.id,
            brief_id=brief.id,
        ) from None
    except Exception:
        raise SocialContentPersistenceError(
            "Mevcut veritabanı satırı içerik sözleşmesini ihlal ediyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            idea_id=idea.id,
            brief_id=brief.id,
        ) from None

    # Kanonik scenario paritesi
    if row.scenario != revalidated.scenario:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırının senaryosu kanonik sözleşmeyle uyuşmuyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="scenario",
            idea_id=idea.id,
            brief_id=brief.id,
        )

    # Kanonik format_payload paritesi
    expected_serialized_payload = serialize_content_format_payload(revalidated.format_payload)
    if row.format_payload != expected_serialized_payload:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırının format_payload alanı kanonik sözleşmeyle uyuşmuyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="format_payload",
            idea_id=idea.id,
            brief_id=brief.id,
        )

    # Kanonik duration_status ve actual_duration_sec paritesi
    if row.duration_status != revalidated.duration_status:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırının duration_status alanı kanonik sözleşmeyle uyuşmuyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="duration_status",
            idea_id=idea.id,
            brief_id=brief.id,
        )

    if row.actual_duration_sec != revalidated.actual_duration_sec:
        raise SocialContentPersistenceError(
            "Mevcut içerik satırının actual_duration_sec alanı kanonik sözleşmeyle uyuşmuyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="actual_duration_sec",
            idea_id=idea.id,
            brief_id=brief.id,
        )

    # validation_warnings doğrulaması
    if not isinstance(row.validation_warnings, (list, tuple)):
        raise SocialContentPersistenceError(
            "Mevcut validation_warnings alanı liste veya tuple olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="validation_warnings",
            idea_id=idea.id,
            brief_id=brief.id,
        )
    for w in row.validation_warnings:
        if not isinstance(w, str):
            raise SocialContentPersistenceError(
                "Mevcut validation warning metni string olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="validation_warnings",
                idea_id=idea.id,
                brief_id=brief.id,
            )

    if row.duration_status == "mismatch":
        if "duration_mismatch" not in row.validation_warnings:
            raise SocialContentPersistenceError(
                "duration_status mismatch iken duration_mismatch uyarısı bulunmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="validation_warnings",
                idea_id=idea.id,
                brief_id=brief.id,
            )
    elif row.duration_status in ("valid", "not_applicable"):
        if "duration_mismatch" in row.validation_warnings:
            raise SocialContentPersistenceError(
                "duration_status valid/not_applicable iken duration_mismatch uyarısı bulunamaz.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="validation_warnings",
                idea_id=idea.id,
                brief_id=brief.id,
            )

    return PersistedSocialContent(
        id=row.id,
        idea_id=row.idea_id,
        brief_id=row.brief_id,
        target_id=target.id,
        platform=target.platform,
        content_format=target.content_format,
        hooks=revalidated.hooks,
        caption=revalidated.caption,
        scenario=revalidated.scenario,
        format_payload=revalidated.format_payload,
        visual_suggestion=revalidated.visual_suggestion,
        video_concept=revalidated.video_concept,
        cta_text=revalidated.cta_text,
        hashtags=revalidated.hashtags,
        industry_posting_suggestion=revalidated.industry_posting_suggestion,
        platform_notes=revalidated.platform_notes,
        duration_status=row.duration_status,
        actual_duration_sec=row.actual_duration_sec,
        validation_warnings=tuple(row.validation_warnings),
        is_stale=row.is_stale,
    )


_validate_and_build_persisted_content = validate_and_build_persisted_social_content


# ==================== ANA PERSISTENCE FONKSİYONU ====================


def persist_social_content(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    idea_id: int,
    content: ValidatedSocialContent,
    quality_decision: SocialContentQualityDecision,
    task_id: str,
    now: datetime | None = None,
) -> PersistedSocialContentResult:
    """Tekil sosyal içeriği otoriter zincir ve kalite kararı altında atomik kaydeder.

    Kurallar:
    - db.begin(), db.commit(), db.rollback() çağırmaz.
    - Global kilit sırası: ScoringRun -> SocialBrief -> SocialGenerationAttempt.
    - Kalite kararı 'accept' ve 'grounding_clean' True olmalıdır.
    - Idempotent: aynı brief/idea için zaten kayıt varsa overwrite etmez, already_present=True döner.
    - Eşzamanlı yarış: uq_social_content_idea_brief partial unique index savepoint ile izole edilir.
    """
    # 1. Girdi doğrulama
    if not isinstance(db, Session):
        raise SocialContentPersistenceError(
            "db bir SQLAlchemy Session örneği olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="db",
        )
    if isinstance(brief_id, bool) or not isinstance(brief_id, int) or brief_id <= 0:
        raise SocialContentPersistenceError(
            "brief_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="brief_id",
        )
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialContentPersistenceError(
            "attempt_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="attempt_id",
        )
    if isinstance(idea_id, bool) or not isinstance(idea_id, int) or idea_id <= 0:
        raise SocialContentPersistenceError(
            "idea_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="idea_id",
        )
    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialContentPersistenceError(
            "task_id zorunludur ve boş olamaz.",
            error_code=CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE,
            field="task_id",
            idea_id=idea_id,
            brief_id=brief_id,
        )
    if not isinstance(content, ValidatedSocialContent):
        raise SocialContentPersistenceError(
            "content bir ValidatedSocialContent örneği olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="content",
            idea_id=idea_id,
            brief_id=brief_id,
        )
    if not isinstance(quality_decision, SocialContentQualityDecision):
        raise SocialContentPersistenceError(
            "quality_decision bir SocialContentQualityDecision örneği olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="quality_decision",
            idea_id=idea_id,
            brief_id=brief_id,
        )
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )

    # 2. Kalite kararı ve içerik yapısal tutarlılığı
    _validate_content_quality_consistency(
        content,
        quality_decision,
        idea_id=idea_id,
        brief_id=brief_id,
    )

    # 3. Canonical global lock order ve attempt doğrulaması
    try:
        attempt, brief, scoring_run = lock_contents_attempt_for_content_write(
            db,
            attempt_id=attempt_id,
            brief_id=brief_id,
            idea_id=idea_id,
            task_id=task_id,
            now=now,
        )
    except AttemptNotWritableError as exc:
        raise SocialContentPersistenceError(
            exc.message,
            error_code=CONTENT_PERSISTENCE_ATTEMPT_NOT_WRITABLE,
            field="attempt_id",
            idea_id=idea_id,
            brief_id=brief_id,
        ) from exc
    except (AttemptNotFoundError, BriefNotFoundError) as exc:
        raise SocialContentPersistenceError(
            exc.message,
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="attempt_id",
            idea_id=idea_id,
            brief_id=brief_id,
        ) from exc

    # 4. Otoriter DB Zinciri Doğrulamaları
    idea = (
        db.query(SocialIdea)
        .filter(SocialIdea.id == idea_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if idea is None:
        raise SocialContentPersistenceError(
            "Fikir bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="idea_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if idea.brief_id != brief.id:
        raise SocialContentPersistenceError(
            "Fikir belirtilen brief'e ait değil.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="idea_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if idea.is_stale is not False:
        raise SocialContentPersistenceError(
            "Fikir stale durumda.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="idea_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )

    # Kategori kontrolü
    category = (
        db.query(SocialCategory)
        .filter(SocialCategory.id == idea.category_id)
        .populate_existing()
        .one_or_none()
    )
    if category is None:
        raise SocialContentPersistenceError(
            "Fikrin kategorisi bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="category_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if category.brief_id != brief.id:
        raise SocialContentPersistenceError(
            "Kategori farklı brief'e ait.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="category_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if category.scoring_run_id != scoring_run.id:
        raise SocialContentPersistenceError(
            "Kategori farklı scoring_run'a ait.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="category_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if category.is_stale is not False:
        raise SocialContentPersistenceError(
            "Kategori stale durumda.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="category_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )

    # Hedef (target) kontrolü
    target = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.id == idea.brief_target_id)
        .populate_existing()
        .one_or_none()
    )
    if target is None:
        raise SocialContentPersistenceError(
            "Fikrin brief hedefi bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="brief_target_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if target.brief_id != brief.id:
        raise SocialContentPersistenceError(
            "Brief hedefi farklı brief'e ait.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="brief_target_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if idea.target_platform != target.platform:
        raise SocialContentPersistenceError(
            "Fikir hedef platformu brief hedefiyle uyuşmuyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="target_platform",
            idea_id=idea_id,
            brief_id=brief.id,
        )
    if idea.content_format != target.content_format:
        raise SocialContentPersistenceError(
            "Fikir içerik formatı brief hedefiyle uyuşmuyor.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="content_format",
            idea_id=idea_id,
            brief_id=brief.id,
        )

    # Anahtar kelime kontrolü
    brief_kw = (
        db.query(SocialBriefKeyword)
        .filter(
            SocialBriefKeyword.brief_id == brief.id,
            SocialBriefKeyword.keyword_id == idea.keyword_id,
        )
        .first()
    )
    if brief_kw is None:
        raise SocialContentPersistenceError(
            "Fikir anahtar kelimesi brief kelimeleri arasında bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="keyword_id",
            idea_id=idea_id,
            brief_id=brief.id,
        )

    # Format matrisi ve kanonik preset kontrolü
    fmt_def = get_platform_format(target.platform, target.content_format)
    if fmt_def is None:
        raise SocialContentPersistenceError(
            "Platform ve format kombinasyonu kanonik matriste bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="content_format",
            idea_id=idea_id,
            brief_id=brief.id,
        )

    if fmt_def.requires_duration:
        if (
            not target.duration_preset_id
            or target.duration_min_sec is None
            or target.duration_max_sec is None
        ):
            raise SocialContentPersistenceError(
                "Video hedefi için süre preset ve sınırları zorunludur.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_preset_id",
                idea_id=idea_id,
                brief_id=brief.id,
            )
        preset_def = get_duration_preset(target.duration_preset_id)
        if preset_def is None or preset_def not in fmt_def.duration_presets:
            raise SocialContentPersistenceError(
                "Hedef süre preset'i kanonik matrisle uyuşmuyor.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_preset_id",
                idea_id=idea_id,
                brief_id=brief.id,
            )
        if (
            target.duration_min_sec != preset_def.min_sec
            or target.duration_max_sec != preset_def.max_sec
        ):
            raise SocialContentPersistenceError(
                "Hedef süre sınırları kanonik preset değerleriyle uyuşmuyor.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_min_sec",
                idea_id=idea_id,
                brief_id=brief.id,
            )
    else:
        if (
            target.duration_preset_id is not None
            or target.duration_min_sec is not None
            or target.duration_max_sec is not None
        ):
            raise SocialContentPersistenceError(
                "Video dışı format için süre alanları NULL olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                field="duration_preset_id",
                idea_id=idea_id,
                brief_id=brief.id,
            )

    # 4b. Incoming content'in canonical sözleşme ile yeniden doğrulanması (Micro-Phase F1-G.5.1a)
    target_spec = ContentTargetSpec(
        target_id=target.id,
        platform=target.platform,
        content_format=target.content_format,
        duration_preset_id=target.duration_preset_id,
        duration_min_sec=target.duration_min_sec,
        duration_max_sec=target.duration_max_sec,
    )
    raw_incoming_dict: dict[str, Any] = {
        "hooks": [
            {
                "text": h.text,
                "style": h.style,
                **({"ab_score": h.ab_score} if h.ab_score is not None else {}),
            }
            for h in content.hooks
        ],
        "caption": content.caption,
        "cta_text": content.cta_text,
        "hashtags": list(content.hashtags),
        "format_payload": serialize_content_format_payload(content.format_payload),
        "visual_suggestion": content.visual_suggestion,
        "video_concept": content.video_concept,
        "industry_posting_suggestion": content.industry_posting_suggestion,
        "platform_notes": content.platform_notes,
    }
    try:
        revalidated_incoming = validate_social_content_output(raw_incoming_dict, target_spec)
    except SocialContentOutputValidationError as exc:
        raise SocialContentPersistenceError(
            "Gelen içerik sözleşme kurallarını ihlal ediyor.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field=exc.field,
            idea_id=idea_id,
            brief_id=brief_id,
        ) from None
    except Exception:
        raise SocialContentPersistenceError(
            "Gelen içerik sözleşme kurallarını ihlal ediyor.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            idea_id=idea_id,
            brief_id=brief_id,
        ) from None

    if (
        content.hooks != revalidated_incoming.hooks
        or content.caption != revalidated_incoming.caption
        or content.format_payload != revalidated_incoming.format_payload
        or content.visual_suggestion != revalidated_incoming.visual_suggestion
        or content.video_concept != revalidated_incoming.video_concept
        or content.cta_text != revalidated_incoming.cta_text
        or content.hashtags != revalidated_incoming.hashtags
        or content.industry_posting_suggestion != revalidated_incoming.industry_posting_suggestion
        or content.platform_notes != revalidated_incoming.platform_notes
        or content.duration_status != revalidated_incoming.duration_status
        or content.actual_duration_sec != revalidated_incoming.actual_duration_sec
        or content.validation_warnings != revalidated_incoming.validation_warnings
        or content.scenario != revalidated_incoming.scenario
    ):
        raise SocialContentPersistenceError(
            "Gelen içerik nesnesi ile kanonik sözleşme çıktısı arasında alan uyuşmazlığı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            idea_id=idea_id,
            brief_id=brief_id,
        )

    # 5. Idempotent Mevcut Satır Kontrolü
    existing_content = (
        db.query(SocialContent)
        .filter(
            SocialContent.idea_id == idea.id,
            SocialContent.brief_id == brief.id,
        )
        .one_or_none()
    )
    if existing_content is not None:
        persisted = _validate_and_build_persisted_content(
            existing_content,
            target=target,
            idea=idea,
            brief=brief,
        )
        db.flush()
        return PersistedSocialContentResult(
            brief_id=brief.id,
            attempt_id=attempt.id,
            idea_id=idea.id,
            content=persisted,
            already_present=True,
        )

    # 6. Yeni Satır Hazırlığı ve Savepoint ile Atomik Insert
    serialized_hooks = [
        {
            "text": h.text,
            "style": h.style,
            **({"ab_score": h.ab_score} if h.ab_score is not None else {}),
        }
        for h in revalidated_incoming.hooks
    ]
    serialized_hashtags = list(revalidated_incoming.hashtags)
    serialized_format_payload = serialize_content_format_payload(revalidated_incoming.format_payload)
    serialized_warnings = list(quality_decision.warnings)

    new_content_row = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        is_stale=False,
        hooks=serialized_hooks,
        caption=revalidated_incoming.caption,
        scenario=revalidated_incoming.scenario,
        format_payload=serialized_format_payload,
        visual_suggestion=revalidated_incoming.visual_suggestion,
        video_concept=revalidated_incoming.video_concept,
        cta_text=revalidated_incoming.cta_text,
        hashtags=serialized_hashtags,
        industry_posting_suggestion=revalidated_incoming.industry_posting_suggestion,
        platform_notes=revalidated_incoming.platform_notes,
        duration_status=revalidated_incoming.duration_status,
        actual_duration_sec=revalidated_incoming.actual_duration_sec,
        validation_warnings=serialized_warnings,
        regeneration_count=0,
    )

    try:
        with db.begin_nested():
            db.add(new_content_row)
            db.flush()
    except IntegrityError as exc:
        if not _is_idea_brief_unique_violation(exc):
            raise SocialContentPersistenceError(
                "Veritabanı bütünlük kısıtı ihlali.",
                error_code=CONTENT_PERSISTENCE_CONFLICT,
                idea_id=idea.id,
                brief_id=brief.id,
            ) from None

        # Eşzamanlı yarış: rakip session satırı az önce ekledi
        winner = (
            db.query(SocialContent)
            .filter(
                SocialContent.idea_id == idea.id,
                SocialContent.brief_id == brief.id,
            )
            .one_or_none()
        )
        if winner is None:
            raise SocialContentPersistenceError(
                "Eşzamanlı yarış sonrası içerik satırı bulunamadı.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        persisted = _validate_and_build_persisted_content(
            winner,
            target=target,
            idea=idea,
            brief=brief,
        )
        db.flush()
        return PersistedSocialContentResult(
            brief_id=brief.id,
            attempt_id=attempt.id,
            idea_id=idea.id,
            content=persisted,
            already_present=True,
        )

    persisted = _validate_and_build_persisted_content(
        new_content_row,
        target=target,
        idea=idea,
        brief=brief,
    )
    db.flush()
    return PersistedSocialContentResult(
        brief_id=brief.id,
        attempt_id=attempt.id,
        idea_id=idea.id,
        content=persisted,
        already_present=False,
    )


# ==================== FINAL DB COVERAGE DOĞRULAYICISI ====================


def validate_social_content_attempt_coverage(
    db: Session,
    *,
    brief_id: int,
    requested_idea_ids: tuple[int, ...],
    expected_successful_idea_ids: tuple[int, ...],
    expected_unresolved_idea_ids: tuple[int, ...],
) -> tuple[int, ...]:
    """Otoriter final DB coverage doğrulaması yapar (Mikro Faz F1-G.5.5a).

    Kurallar:
    - db.commit() veya db.rollback() çağırmaz.
    - AI veya harici çağrı yapmaz.
    - Ham içerik veya SQL sızdırmaz.
    - expected_successful_idea_ids ve expected_unresolved_idea_ids, requested_idea_ids'in snapshot sırasını koruyan bir ayrık bölümü olmalıdır.
    - expected_successful_idea_ids içindeki her fikir için brief_id altında tam 1 adet non-stale SocialContent olmalıdır.
    - İçerik doğru brief/idea/target zincirine ait olmalı ve validate_and_build_persisted_social_content ile doğrulanmalıdır.
    - expected_unresolved_idea_ids içindeki hiçbir fikir için brief_id altında non-stale SocialContent bulunamaz.
    - Başka brief'e ait veya stale içerikler başarı sayılamaz.
    - Başarılı ID'leri snapshot sırasıyla döner.
    """
    if not isinstance(db, Session):
        raise SocialContentPersistenceError(
            "db bir SQLAlchemy Session örneği olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="db",
        )
    if isinstance(brief_id, bool) or not isinstance(brief_id, int) or brief_id <= 0:
        raise SocialContentPersistenceError(
            "brief_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
            field="brief_id",
        )
    for field_name, val in [
        ("requested_idea_ids", requested_idea_ids),
        ("expected_successful_idea_ids", expected_successful_idea_ids),
        ("expected_unresolved_idea_ids", expected_unresolved_idea_ids),
    ]:
        if not isinstance(val, tuple):
            raise SocialContentPersistenceError(
                f"{field_name} tuple olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
                field=field_name,
            )
        if len(set(val)) != len(val):
            raise SocialContentPersistenceError(
                f"{field_name} benzersiz olmalıdır.",
                error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
                field=field_name,
            )
        for i in val:
            if isinstance(i, bool) or not isinstance(i, int) or i <= 0:
                raise SocialContentPersistenceError(
                    f"{field_name} pozitif tamsayılar içermelidir.",
                    error_code=CONTENT_PERSISTENCE_INVALID_INPUT,
                    field=field_name,
                )

    req_set = set(requested_idea_ids)
    succ_set = set(expected_successful_idea_ids)
    unres_set = set(expected_unresolved_idea_ids)

    # Ayrıklık ve tam kapsama
    if succ_set & unres_set:
        raise SocialContentPersistenceError(
            "expected_successful_idea_ids ve expected_unresolved_idea_ids kesişemez.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
        )
    if (succ_set | unres_set) != req_set:
        raise SocialContentPersistenceError(
            "expected_successful_idea_ids ve expected_unresolved_idea_ids requested_idea_ids kümesini eksiksiz kapsamalıdır.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
        )

    # Sıralama kontrolü (snapshot sırasını korumalı)
    expected_succ_order = tuple(i for i in requested_idea_ids if i in succ_set)
    if expected_successful_idea_ids != expected_succ_order:
        raise SocialContentPersistenceError(
            "expected_successful_idea_ids snapshot sırasını korumalıdır.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="expected_successful_idea_ids",
        )
    expected_unres_order = tuple(i for i in requested_idea_ids if i in unres_set)
    if expected_unresolved_idea_ids != expected_unres_order:
        raise SocialContentPersistenceError(
            "expected_unresolved_idea_ids snapshot sırasını korumalıdır.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="expected_unresolved_idea_ids",
        )

    # 1. Brief kontrolü
    brief = db.query(SocialBrief).filter(SocialBrief.id == brief_id).one_or_none()
    if brief is None:
        raise SocialContentPersistenceError(
            "Brief bulunamadı.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            field="brief_id",
            brief_id=brief_id,
        )

    # 2. Idea'lar ve Target'lar
    ideas = (
        db.query(SocialIdea)
        .filter(SocialIdea.id.in_(requested_idea_ids), SocialIdea.brief_id == brief_id)
        .all()
    )
    if len(ideas) != len(requested_idea_ids):
        raise SocialContentPersistenceError(
            "Talep edilen fikirlerden bazıları bulunamadı veya brief'e ait değil.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            brief_id=brief_id,
        )
    ideas_by_id = {i.id: i for i in ideas}
    for i in ideas:
        if i.is_stale is not False:
            raise SocialContentPersistenceError(
                "Talep edilen fikirlerden biri stale durumda.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                idea_id=i.id,
                brief_id=brief_id,
            )

    target_ids = {i.brief_target_id for i in ideas}
    targets = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.id.in_(target_ids), SocialBriefTarget.brief_id == brief_id)
        .all()
    )
    if len(targets) != len(target_ids):
        raise SocialContentPersistenceError(
            "Fikirlere ait bazı brief hedefleri bulunamadı veya brief'e ait değil.",
            error_code=CONTENT_PERSISTENCE_INCONSISTENT,
            brief_id=brief_id,
        )
    targets_by_id = {t.id: t for t in targets}

    # 3. SocialContent satırları
    all_contents = (
        db.query(SocialContent)
        .filter(SocialContent.idea_id.in_(requested_idea_ids))
        .all()
    )
    for c in all_contents:
        if c.brief_id != brief_id:
            raise SocialContentPersistenceError(
                "Fikir için farklı brief'e ait içerik bulundu.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                idea_id=c.idea_id,
                brief_id=brief_id,
            )

    contents_by_idea: dict[int, list[SocialContent]] = {}
    for c in all_contents:
        contents_by_idea.setdefault(c.idea_id, []).append(c)

    # Unresolved kontrolü: hiçbir non-stale içerik olmamalı
    for u_id in expected_unresolved_idea_ids:
        non_stale = [c for c in contents_by_idea.get(u_id, []) if c.is_stale is False]
        if len(non_stale) > 0:
            raise SocialContentPersistenceError(
                "Unresolved fikir için veritabanında aktif içerik bulundu.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                idea_id=u_id,
                brief_id=brief_id,
            )

    # Successful kontrolü: tam 1 adet non-stale içerik olmalı ve geçerli olmalı
    for s_id in expected_successful_idea_ids:
        non_stale = [c for c in contents_by_idea.get(s_id, []) if c.is_stale is False]
        if len(non_stale) == 0:
            raise SocialContentPersistenceError(
                "Başarılı beklenen fikir için aktif içerik bulunamadı.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                idea_id=s_id,
                brief_id=brief_id,
            )
        if len(non_stale) > 1:
            raise SocialContentPersistenceError(
                "Başarılı beklenen fikir için birden fazla aktif içerik bulundu.",
                error_code=CONTENT_PERSISTENCE_INCONSISTENT,
                idea_id=s_id,
                brief_id=brief_id,
            )

        content_row = non_stale[0]
        idea = ideas_by_id[s_id]
        target = targets_by_id[idea.brief_target_id]

        validate_and_build_persisted_social_content(
            content_row,
            target=target,
            idea=idea,
            brief=brief,
        )

    return expected_successful_idea_ids

