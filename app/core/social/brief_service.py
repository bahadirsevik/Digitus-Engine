# -*- coding: utf-8 -*-
"""Social Brief Atomik Persistence ve Response Serializer Servisi (F1-D.4).

Bu modül F1-D.2 saf doğrulamasını ve F1-D.3 DB eligibility kontrolünü
aynı transaction sınırında çalıştırarak SocialBrief kayıt ağacını atomik olarak oluşturur.

Transaction Sözleşmesi:
- Fonksiyon transaction açmaz (db.begin() çağırmaz).
- commit() veya rollback() çağırmaz.
- Başarı sonunda tek bir db.flush() yapar.
- Transaction yönetimini çağıran katmana bırakır.
- Hataları yakalayıp sessiz fallback üretmez (domain hatalarını doğrudan yukarı taşır).
- Eligibility sırasında alınan kilitleri (BrandProfile, ScoringRun, ChannelPool)
  aynı transaction içinde korur.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.social.brief_eligibility import validate_social_brief_eligibility
from app.core.social.brief_validation import validate_social_brief_request
from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
)
from app.schemas.social_brief import (
    SocialBriefCreateRequest,
    SocialBriefKeywordResponse,
    SocialBriefResponse,
    SocialBriefTargetResponse,
)


class SocialBriefNotFoundError(ValueError):
    """Brief bulunamadığında, başka workspace'e ait olduğunda veya workspace silinmiş olduğunda fırlatılır."""

    error_code: str = "BRIEF_NOT_FOUND"

    def __init__(self, message: str = "Social brief not found."):
        super().__init__(message)
        self.message = message


class SocialBriefRunNotFoundError(ValueError):
    """Scoring run bulunamadığında, başka workspace'e ait olduğunda veya workspace silinmiş olduğunda fırlatılır."""

    error_code: str = "RUN_NOT_FOUND"

    def __init__(self, message: str = "Scoring run not found."):
        super().__init__(message)
        self.message = message



def create_social_brief(
    db: Session,
    *,
    request: SocialBriefCreateRequest,
    brand_profile_id: int,
) -> SocialBrief:
    """Saf doğrulamayı ve DB eligibility kontrolünü tek transaction'da çalıştırarak
    SocialBrief, SocialBriefKeyword ve SocialBriefTarget kayıtlarını atomik oluşturur.

    Args:
        db: Aktif SQLAlchemy oturumu (commit/rollback çağrılmaz).
        request: Pydantic brief oluşturma isteği.
        brand_profile_id: Workspace (BrandProfile) kimliği.

    Returns:
        SocialBrief: Flush edilmiş ve ID'leri atanmış parent ORM nesnesi.

    Raises:
        SocialBriefValidationError: Girdi biçimi veya domain sınırları ihlal edildiğinde.
        SocialBriefEligibilityError: Workspace/run/freshness/havuz üyeliği ihlal edildiğinde.
        IntegrityError: DB seviyesi constraint ihlalinde flush tarafından fırlatılır.
    """
    # 1. Saf doğrulama (F1-D.2)
    validated = validate_social_brief_request(request)

    # 2. DB Eligibility ve kilitler (F1-D.3)
    eligible = validate_social_brief_eligibility(
        db,
        validated=validated,
        brand_profile_id=brand_profile_id,
    )

    # 3. SocialBrief parent nesnesini bellekte oluştur
    brief = SocialBrief(
        scoring_run_id=eligible.scoring_run_id,
        brand_name_snapshot=eligible.brand_name,
        brand_context_snapshot=eligible.brand_context,
        channel_assignment_version=eligible.channel_assignment_version,
        format_matrix_version=eligible.format_matrix_version,
        locked_at=None,
        is_stale=False,
    )

    # 4. SocialBriefKeyword child nesnelerini oluştur (0-tabanlı pozisyonlar ve snapshot)
    for kw in eligible.keywords:
        brief.brief_keywords.append(
            SocialBriefKeyword(
                keyword_id=kw.keyword_id,
                keyword_snapshot=kw.keyword_snapshot,
                position=kw.position,
            )
        )

    # 5. SocialBriefTarget child nesnelerini oluştur
    for tgt in eligible.targets:
        brief.targets.append(
            SocialBriefTarget(
                platform=tgt.platform,
                content_format=tgt.content_format,
                duration_preset_id=tgt.duration_preset_id,
                duration_min_sec=tgt.duration_min_sec,
                duration_max_sec=tgt.duration_max_sec,
            )
        )

    # 6. Tek add ve tek flush ile atomik persistence
    db.add(brief)
    db.flush()

    return brief


def social_brief_to_response(brief: SocialBrief) -> SocialBriefResponse:
    """SocialBrief ORM nesnesini doğrulanmış SocialBriefResponse şemasına dönüştürür.

    Kurallar:
    - ORM nesnesini mutate etmez.
    - DB sorgusu veya commit yapmaz.
    - keyword'leri (position, id) deterministik sırasına göre dizer.
    - target'ları id'ye göre deterministik sıralar.
    - keyword_id null olan bozuk kayıtlarda sessiz coercion yapmaz, fail-closed ValueError fırlatır.

    Args:
        brief: SocialBrief ORM nesnesi.

    Returns:
        SocialBriefResponse: Pydantic v2 yanıt nesnesi.
    """
    if brief is None:
        raise ValueError("SocialBrief nesnesi None olamaz.")

    # brief_keywords deterministik sıralama (position, id)
    keywords_list: list[SocialBriefKeywordResponse] = []
    sorted_keywords = sorted(
        brief.brief_keywords,
        key=lambda k: (k.position if k.position is not None else 0, k.id if k.id is not None else 0),
    )
    for kw in sorted_keywords:
        if kw.keyword_id is None:
            raise ValueError(f"SocialBriefKeyword id={kw.id} keyword_id alanı null olamaz.")
        keywords_list.append(
            SocialBriefKeywordResponse(
                id=kw.id,
                keyword_id=kw.keyword_id,
                keyword_snapshot=kw.keyword_snapshot,
                position=kw.position,
            )
        )

    # targets deterministik sıralama (id)
    targets_list: list[SocialBriefTargetResponse] = []
    sorted_targets = sorted(
        brief.targets,
        key=lambda t: t.id if t.id is not None else 0,
    )
    for tgt in sorted_targets:
        targets_list.append(
            SocialBriefTargetResponse(
                id=tgt.id,
                platform=tgt.platform,
                content_format=tgt.content_format,
                duration_preset_id=tgt.duration_preset_id,
                duration_min_sec=tgt.duration_min_sec,
                duration_max_sec=tgt.duration_max_sec,
            )
        )

    return SocialBriefResponse(
        id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        brand_name_snapshot=brief.brand_name_snapshot,
        brand_context_snapshot=brief.brand_context_snapshot,
        channel_assignment_version=brief.channel_assignment_version,
        format_matrix_version=brief.format_matrix_version,
        locked_at=brief.locked_at,
        is_stale=brief.is_stale,
        created_at=brief.created_at,
        keywords=keywords_list,
        targets=targets_list,
    )


def get_social_brief(
    db: Session,
    *,
    brief_id: int,
    brand_profile_id: int,
) -> SocialBrief:
    """Tekil SocialBrief kaydını workspace izolasyonu ve eager loading ile getirir.

    Sorgu scope'u:
    - SocialBrief.id == brief_id
    - SocialBrief.scoring_run_id üzerinden ScoringRun join
    - ScoringRun.brand_profile_id == brand_profile_id
    - BrandProfile.id == brand_profile_id
    - BrandProfile.deleted_at IS NULL

    Eager loading:
    - SocialBrief.brief_keywords
    - SocialBrief.targets

    Kurallar:
    - commit/rollback yapmaz.
    - Satır değiştirmez.
    - Attempt reconciliation yapmaz.
    - include_stale filtresi uygulamaz (stale brief de ID ile okunabilir).
    - Bulunamazsa SocialBriefNotFoundError fırlatır.
    """
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
        .options(
            selectinload(SocialBrief.brief_keywords),
            selectinload(SocialBrief.targets),
        )
    )
    brief = db.scalars(stmt).first()
    if brief is None:
        raise SocialBriefNotFoundError()
    return brief


def list_social_briefs(
    db: Session,
    *,
    scoring_run_id: int,
    brand_profile_id: int,
) -> list[SocialBrief]:
    """Run'a ait tüm SocialBrief kayıtlarını workspace izolasyonu ve eager loading ile listeler.

    Sorgu scope'u:
    1. Run doğrulama:
       - ScoringRun.id == scoring_run_id
       - ScoringRun.brand_profile_id == brand_profile_id
       - BrandProfile.id == brand_profile_id
       - BrandProfile.deleted_at IS NULL
       Geçersizse SocialBriefRunNotFoundError fırlatır.
    2. Brief listeleme:
       - SocialBrief.scoring_run_id == scoring_run_id
       - Stale ve non-stale kayıtlar birlikte döner
       - Eager loading: brief_keywords, targets
       - Deterministik sıralama: created_at DESC, id DESC

    Kurallar:
    - commit/rollback yapmaz.
    - Satır değiştirmez.
    - Run geçerli fakat brief yoksa boş liste ([]) döner.
    """
    # 1. Run scope doğrula
    run_exists = db.scalar(
        select(ScoringRun.id)
        .join(BrandProfile, ScoringRun.brand_profile_id == BrandProfile.id)
        .where(
            ScoringRun.id == scoring_run_id,
            ScoringRun.brand_profile_id == brand_profile_id,
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
    )
    if run_exists is None:
        raise SocialBriefRunNotFoundError()

    # 2. Brief'leri eager loading ile getir
    stmt = (
        select(SocialBrief)
        .where(SocialBrief.scoring_run_id == scoring_run_id)
        .order_by(SocialBrief.created_at.desc(), SocialBrief.id.desc())
        .options(
            selectinload(SocialBrief.brief_keywords),
            selectinload(SocialBrief.targets),
        )
    )
    return list(db.scalars(stmt).all())

