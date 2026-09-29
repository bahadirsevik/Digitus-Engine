# -*- coding: utf-8 -*-
"""Sosyal Brief İçerik Üretimi Preflight, Idempotent Attempt ve Snapshot (F1-G.5.7.1).

Bu modül sosyal içerik üretimi başlangıç akışını yönetir:
- İstek girdisini strict doğrular (SocialBriefContentsGenerateRequest),
- Workspace ve brief sahipliğini doğrular,
- Seçilen fikirlerin kanonik, güncel ve aynı brief'e ait olduğunu otoriter olarak doğrular,
- Grounding girdilerini (confirmed product_facts ve trusted_brand_usp) güvenli biçimde dondurur,
- stage="contents" SocialGenerationAttempt kaydını canonical kilit sırasıyla oluşturur veya aynı key ile replay eder,
- Attempt.coverage içine worker'ın beklediği exact contents_request_v1 snapshot'ını yazar,
- Aynı idempotency anahtarının farklı bir istekle kullanılmasını fail-closed reddeder.

Kurallar:
- Transaction açmaz (db.begin() çağırmaz).
- db.commit() veya db.rollback() KESİNLİKLE ÇAĞIRMAZ.
- AI çağrısı veya harici ağ çağrısı YAPMAZ.
- Celery task dispatch veya API endpoint içermez.
- Global kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
- Hata mesajlarında dinamik ID, USP metni veya ürün tanımı sızdırılmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.channel.brand_defense import load_product_definition
from app.core.social.content_worker_input import (
    extract_social_content_request_snapshot,
)
from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.social.attempt_state import (
    AttemptConflictError,
    create_or_get_attempt,
)
from app.schemas.social_brief import SocialBriefContentsGenerateRequest


# ==================== SABİT HATA KODLARI ====================

CONTENT_INVALID_INPUT = "CONTENT_INVALID_INPUT"
CONTENT_BRIEF_NOT_FOUND = "CONTENT_BRIEF_NOT_FOUND"
CONTENT_BRIEF_NOT_LOCKED = "CONTENT_BRIEF_NOT_LOCKED"
CONTENT_BRIEF_STALE = "CONTENT_BRIEF_STALE"
CONTENT_ASSIGNMENT_CHANGED = "CONTENT_ASSIGNMENT_CHANGED"
CONTENT_IDEAS_INVALID = "CONTENT_IDEAS_INVALID"
CONTENT_IDEA_NOT_ELIGIBLE = "CONTENT_IDEA_NOT_ELIGIBLE"
CONTENT_GROUNDING_INVALID = "CONTENT_GROUNDING_INVALID"
CONTENT_ATTEMPT_SNAPSHOT_INVALID = "CONTENT_ATTEMPT_SNAPSHOT_INVALID"
CONTENT_ATTEMPT_REQUEST_MISMATCH = "CONTENT_ATTEMPT_REQUEST_MISMATCH"


# ==================== DOMAIN HATA SINIFI ====================

class SocialContentFlowError(ValueError):
    """Sosyal içerik akışı preflight domain hatası.

    Güvenlik: Hata mesajlarında ham idea ID, brief ID, workspace ID,
    idempotency key, kullanıcı USP metni veya ürün tanımları yer almaz.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.details = details

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialContentFlowError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


# ==================== DTO'LAR (IMMUTABLE) ====================

@dataclass(frozen=True)
class SocialContentGenerationStart:
    """İçerik üretimi preflight, attempt ve snapshot başlatma sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    attempt_id: int
    attempt_status: str
    requested_idea_ids: tuple[int, ...]
    attempt_created: bool
    replayed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_idea_ids", tuple(self.requested_idea_ids))


# ==================== INTERNAL TIME VALIDATOR ====================

def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


# ==================== ANA FLOW BAŞLATMA SERVİSİ ====================

def begin_social_content_generation(
    db: Session,
    *,
    brief_id: int,
    brand_profile_id: int,
    request: SocialBriefContentsGenerateRequest,
    now: datetime | None = None,
) -> SocialContentGenerationStart:
    """Sosyal brief içerik üretimi başlangıç akışını çalıştırır (F1-G.5.7.1).

    İşlem Sırası:
    1. Strict skalar ve request doğrulaması.
    2. Workspace ve brief doğrulaması (ScoringRun FOR UPDATE -> SocialBrief FOR UPDATE).
    3. Seçilen fikirlerin otoriter doğrulaması (brief, category, target, format, keyword üyeliği).
    4. Otoriter confirmed product facts ve trusted_brand_usp grounding hazırlığı.
    5. stage="contents" SocialGenerationAttempt elde etme / oluşturma.
    6. Yeni attempt için exact contents_request_v1 snapshot'ının yazılması.
    7. Same-key replay durumunda snapshot ve request uyuşmazlık denetimleri.

    Kurallar:
    - db.commit() veya db.rollback() çağırmaz.
    - Tüm hata mesajları statiktir; dinamik hassas veriler sızdırılmaz.
    """
    # 1. Strict skalar ve tip doğrulamaları
    if not isinstance(db, Session):
        raise SocialContentFlowError(
            "db bir SQLAlchemy Session örneği olmalıdır.",
            error_code=CONTENT_INVALID_INPUT,
            field="db",
        )

    if isinstance(brief_id, bool) or not isinstance(brief_id, int) or brief_id <= 0:
        raise SocialContentFlowError(
            "brief_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_INVALID_INPUT,
            field="brief_id",
        )

    if isinstance(brand_profile_id, bool) or not isinstance(brand_profile_id, int) or brand_profile_id <= 0:
        raise SocialContentFlowError(
            "brand_profile_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_INVALID_INPUT,
            field="brand_profile_id",
        )

    if not isinstance(request, SocialBriefContentsGenerateRequest):
        raise SocialContentFlowError(
            "request exact SocialBriefContentsGenerateRequest nesnesi olmalıdır.",
            error_code=CONTENT_INVALID_INPUT,
            field="request",
        )

    current_time = _validate_now(now)

    # 2. Workspace ve brief doğrulaması
    # Kilitsiz ön okuma: kimlik keşfi için scoring_run_id oku
    brief_pre = (
        db.query(SocialBrief.id, SocialBrief.scoring_run_id)
        .filter(SocialBrief.id == brief_id)
        .first()
    )
    if brief_pre is None:
        raise SocialContentFlowError(
            "Sosyal brief bulunamadı.",
            error_code=CONTENT_BRIEF_NOT_FOUND,
            field="brief_id",
        )
    pre_run_id = brief_pre.scoring_run_id

    # Global kilit sırası: BrandProfile -> ScoringRun -> SocialBrief
    # Arşivlenmiş (deleted_at IS NOT NULL) workspace diğer akışlarla (category_flow,
    # idea_flow, idea_retry_flow) aynı şekilde fail-closed NOT_FOUND üretir.
    brand_profile = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == brand_profile_id, BrandProfile.deleted_at.is_(None))
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if brand_profile is None:
        raise SocialContentFlowError(
            "Sosyal brief bulunamadı.",
            error_code=CONTENT_BRIEF_NOT_FOUND,
            field="brief_id",
        )

    # Workspace izolasyonu: ScoringRun.brand_profile_id == brand_profile_id filtresi
    # eşleşmezse fail-closed NOT_FOUND semantiği üretilir.
    scoring_run = (
        db.query(ScoringRun)
        .filter(
            ScoringRun.id == pre_run_id,
            ScoringRun.brand_profile_id == brand_profile_id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if scoring_run is None:
        raise SocialContentFlowError(
            "Sosyal brief bulunamadı.",
            error_code=CONTENT_BRIEF_NOT_FOUND,
            field="brief_id",
        )

    brief = (
        db.query(SocialBrief)
        .filter(
            SocialBrief.id == brief_id,
            SocialBrief.scoring_run_id == scoring_run.id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if brief is None or brief.scoring_run_id != pre_run_id:
        raise SocialContentFlowError(
            "Sosyal brief bulunamadı.",
            error_code=CONTENT_BRIEF_NOT_FOUND,
            field="brief_id",
        )

    if brief.locked_at is None:
        raise SocialContentFlowError(
            "Brief kilitlenmemiş durumda.",
            error_code=CONTENT_BRIEF_NOT_LOCKED,
            field="brief_id",
        )

    if brief.is_stale:
        raise SocialContentFlowError(
            "Brief güncel değil (stale).",
            error_code=CONTENT_BRIEF_STALE,
            field="brief_id",
        )

    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise SocialContentFlowError(
            "Brief kanal atama sürümü scoring run ile uyuşmuyor.",
            error_code=CONTENT_ASSIGNMENT_CHANGED,
            field="brief_id",
        )

    # 3. Seçilen fikirlerin otoriter doğrulaması
    ideas_rows = (
        db.query(SocialIdea)
        .filter(SocialIdea.id.in_(request.idea_ids))
        .all()
    )
    ideas_by_id = {i.id: i for i in ideas_rows}

    # Hedefler (targets)
    targets = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.brief_id == brief_id)
        .all()
    )
    targets_by_id = {t.id: t for t in targets}

    # Kategoriler (categories)
    categories = (
        db.query(SocialCategory)
        .filter(SocialCategory.brief_id == brief_id)
        .all()
    )
    categories_by_id = {c.id: c for c in categories}

    # Brief anahtar kelimeleri
    brief_keywords = (
        db.query(SocialBriefKeyword.keyword_id)
        .filter(SocialBriefKeyword.brief_id == brief_id)
        .all()
    )
    brief_keyword_ids = {bk[0] for bk in brief_keywords}

    for idea_id in request.idea_ids:
        if idea_id not in ideas_by_id:
            # Kayıp veya başka brief'e ait fikirler dışarıdan ayırt edilemez
            raise SocialContentFlowError(
                "Seçilen fikir bulunamadı veya bu brief için uygun değil.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )
        idea = ideas_by_id[idea_id]

        if idea.brief_id != brief_id:
            raise SocialContentFlowError(
                "Seçilen fikir bulunamadı veya bu brief için uygun değil.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )

        if idea.is_stale:
            raise SocialContentFlowError(
                "Seçilen fikir güncel değil (stale).",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )

        # Hedef kontrolü
        if idea.brief_target_id is None or idea.brief_target_id not in targets_by_id:
            raise SocialContentFlowError(
                "Fikrin bağlı olduğu hedef bu brief için uygun değil.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )
        target = targets_by_id[idea.brief_target_id]
        if idea.target_platform != target.platform or idea.content_format != target.content_format:
            raise SocialContentFlowError(
                "Fikrin platform veya formatı bağlı hedef ile uyuşmuyor.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )

        # Kategori kontrolü
        if idea.category_id is None or idea.category_id not in categories_by_id:
            raise SocialContentFlowError(
                "Fikrin bağlı olduğu kategori bu brief için uygun değil.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )
        cat = categories_by_id[idea.category_id]
        if cat.scoring_run_id != scoring_run.id:
            raise SocialContentFlowError(
                "Fikrin bağlı olduğu kategori bu scoring run'a ait değil.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )
        if cat.is_stale:
            raise SocialContentFlowError(
                "Fikrin bağlı olduğu kategori güncel değil (stale).",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )

        # Anahtar kelime kontrolü
        if idea.keyword_id is None or idea.keyword_id not in brief_keyword_ids:
            raise SocialContentFlowError(
                "Fikrin bağlı olduğu anahtar kelime brief üyeliğinde bulunmuyor.",
                error_code=CONTENT_IDEA_NOT_ELIGIBLE,
                field="idea_ids",
            )

    # 4. Grounding snapshot hazırlığı
    product_facts = load_product_definition(db, scoring_run)
    if product_facts is not None:
        if (
            isinstance(product_facts, bool)
            or not isinstance(product_facts, str)
            or len(product_facts) == 0
            or product_facts != product_facts.strip()
            or len(product_facts) > 5000
        ):
            raise SocialContentFlowError(
                "Otoriter ürün tanımı geçersiz veya sınırları aşıyor.",
                error_code=CONTENT_GROUNDING_INVALID,
                field="product_facts",
            )

    # trusted_brand_usp yalnız request'te açıkça verilen değerdir; generic fallback eklenmez
    trusted_brand_usp = request.trusted_brand_usp

    # 5. Attempt elde etme / oluşturma (canonical kilit sırasıyla)
    attempt, attempt_created = create_or_get_attempt(
        db,
        brief_id=brief_id,
        stage="contents",
        idempotency_key=request.idempotency_key,
        requested_idea_ids=list(request.idea_ids),
        now=current_time,
    )

    # 6. Exact snapshot (yeni attempt için)
    if attempt_created:
        attempt.requested_idea_ids = list(request.idea_ids)
        attempt.coverage = {
            "schema_version": "contents_request_v1",
            "request": {
                "idea_ids": list(request.idea_ids),
                "product_facts": product_facts,
                "trusted_brand_usp": trusted_brand_usp,
            },
        }
        # Worker snapshot parser doğrulaması (parite garantisi)
        try:
            extract_social_content_request_snapshot(attempt)
        except Exception as exc:
            raise SocialContentFlowError(
                "Üretilen attempt coverage snapshot'ı geçersiz.",
                error_code=CONTENT_ATTEMPT_SNAPSHOT_INVALID,
            ) from exc

        db.flush()

    # 7. Same-key replay doğrulaması (mevcut attempt için)
    else:
        try:
            snapshot = extract_social_content_request_snapshot(attempt)
        except Exception as exc:
            raise SocialContentFlowError(
                "Mevcut attempt coverage snapshot'ı geçersiz veya bozuk.",
                error_code=CONTENT_ATTEMPT_SNAPSHOT_INVALID,
            ) from exc

        if tuple(attempt.requested_idea_ids or ()) != tuple(snapshot.idea_ids):
            raise SocialContentFlowError(
                "Mevcut attempt requested_idea_ids ile snapshot idea_ids uyuşmuyor.",
                error_code=CONTENT_ATTEMPT_SNAPSHOT_INVALID,
            )

        if tuple(snapshot.idea_ids) != tuple(request.idea_ids):
            raise SocialContentFlowError(
                "Aynı idempotency anahtarı ile farklı fikir listesi gönderilemez.",
                error_code=CONTENT_ATTEMPT_REQUEST_MISMATCH,
                field="idea_ids",
            )

        if snapshot.product_facts != product_facts:
            raise SocialContentFlowError(
                "Aynı idempotency anahtarı ile ürün tanımı uyuşmazlığı.",
                error_code=CONTENT_ATTEMPT_REQUEST_MISMATCH,
                field="product_facts",
            )

        if snapshot.trusted_brand_usp != trusted_brand_usp:
            raise SocialContentFlowError(
                "Aynı idempotency anahtarı ile farklı marka USP'si gönderilemez.",
                error_code=CONTENT_ATTEMPT_REQUEST_MISMATCH,
                field="trusted_brand_usp",
            )

    return SocialContentGenerationStart(
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        attempt_id=attempt.id,
        attempt_status=attempt.status,
        requested_idea_ids=tuple(request.idea_ids),
        attempt_created=attempt_created,
        replayed=not attempt_created,
    )
