# -*- coding: utf-8 -*-
"""Social Brief DB Eligibility ve Keyword Snapshot Servisi (F1-D.3).

Bu modül saf doğrulamadan geçmiş brief girdisinin workspace, ScoringRun,
güncel SOCIAL havuzu ve keyword üyeliği açısından veritabanı üzerinden
doğrulanmasını sağlar.

Transaction Sözleşmesi:
- Fonksiyon transaction açmaz, commit veya rollback çağırmaz.
- DB'ye insert, update veya delete yapmaz.
- Aldığı satır kilitleri çağıranın transaction'ına aittir.
- Gelecek persistence fazı (F1-D.4) bu fonksiyonu çağırıp aynı transaction
  içinde brief kayıtlarını oluşturacaktır.
- Çağıran hata halinde rollback, başarı halinde persistence sonrası commit
  edecektir.
- Kilit sırası: BrandProfile -> ScoringRun -> ChannelPool.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.policy.freshness import compute_pool_freshness
from app.core.social.brief_validation import (
    ValidatedSocialBriefInput,
    ValidatedSocialBriefTarget,
)
from app.database.models import BrandProfile, ChannelPool, Keyword, ScoringRun


class SocialBriefEligibilityError(ValueError):
    """Sosyal brief DB eligibility ve havuz doğrulama hatası (HTTP bağımsız)."""

    def __init__(
        self,
        error_code: str,
        message: str,
        field: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.field = field
        self.details = details

    def __repr__(self) -> str:
        return (
            f"SocialBriefEligibilityError(error_code={self.error_code!r}, "
            f"field={self.field!r}, message={self.message!r}, details={self.details!r})"
        )


@dataclass(frozen=True)
class EligibleSocialBriefKeyword:
    """Uygun bulunmuş ve snapshot'ı alınmış brief anahtar kelimesi (immutable)."""
    keyword_id: int
    keyword_snapshot: str
    position: int


@dataclass(frozen=True)
class EligibleSocialBriefInput:
    """DB doğrulaması tamamlanmış ve snapshot'ları hazır sosyal brief girdisi (immutable)."""
    scoring_run_id: int
    brand_profile_id: int
    channel_assignment_version: int
    format_matrix_version: str
    brand_name: Optional[str]
    brand_context: Optional[str]
    keywords: Tuple[EligibleSocialBriefKeyword, ...]
    targets: Tuple[ValidatedSocialBriefTarget, ...]


def validate_social_brief_eligibility(
    db: Session,
    *,
    validated: ValidatedSocialBriefInput,
    brand_profile_id: int,
) -> EligibleSocialBriefInput:
    """Saf brief girdisinin workspace, ScoringRun, freshness ve SOCIAL pool üyeliğini doğrular.

    Kilit sırası:
        BrandProfile -> ScoringRun -> ChannelPool (seçili satırlar)

    Args:
        db: Aktif SQLAlchemy oturumu (commit/rollback çağrılmaz).
        validated: F1-D.2 saf doğrulama çıktısı.
        brand_profile_id: Doğrulanacak workspace/marka kimliği.

    Returns:
        EligibleSocialBriefInput: Snapshot'ları ve sıra pozisyonları belirlenmiş immutable girdi.

    Raises:
        SocialBriefEligibilityError: Workspace/run bulunamadığında (RUN_NOT_FOUND),
            havuz bayat olduğunda (POOL_STALE) veya keyword'ler SOCIAL havuzuna ait
            olmadığında / geçersiz olduğunda (SOCIAL_KEYWORD_NOT_ELIGIBLE).
    """
    # 1. Workspace doğrulaması (BrandProfile FOR UPDATE)
    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == brand_profile_id, BrandProfile.deleted_at.is_(None))
        .with_for_update()
        .populate_existing()
        .first()
    )
    if workspace is None:
        raise SocialBriefEligibilityError(
            error_code="RUN_NOT_FOUND",
            message=f"Workspace bulunamadı veya silinmiş: {brand_profile_id}",
            field="brand_profile_id",
        )

    # 2. ScoringRun doğrulaması (ScoringRun FOR UPDATE)
    run = (
        db.query(ScoringRun)
        .filter(
            ScoringRun.id == validated.scoring_run_id,
            ScoringRun.brand_profile_id == brand_profile_id,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if run is None:
        raise SocialBriefEligibilityError(
            error_code="RUN_NOT_FOUND",
            message=f"ScoringRun bulunamadı veya workspace'e ait değil: {validated.scoring_run_id}",
            field="scoring_run_id",
        )

    # channel_assignment_version kontrolü (fail-closed, null olamaz)
    assignment_version = getattr(run, "channel_assignment_version", None)
    if assignment_version is None:
        raise SocialBriefEligibilityError(
            error_code="RUN_NOT_FOUND",
            message=f"ScoringRun {run.id} channel_assignment_version değeri tanımsız.",
            field="scoring_run_id",
        )

    # 3. Merkezi Freshness doğrulaması (compute_pool_freshness)
    freshness = compute_pool_freshness(run, workspace)
    if freshness.channel_pool_stale:
        raise SocialBriefEligibilityError(
            error_code="POOL_STALE",
            message="Kanal havuzları güncel değil (stale). Yeni brief oluşturulamaz.",
            field="scoring_run_id",
            details=freshness.as_dict(),
        )

    # 4. SOCIAL Pool üyeliği doğrulaması ve satır kilitleme (ChannelPool FOR UPDATE)
    channel_pools = (
        db.query(ChannelPool)
        .filter(
            ChannelPool.scoring_run_id == validated.scoring_run_id,
            ChannelPool.channel == "SOCIAL",
            ChannelPool.keyword_id.in_(validated.keyword_ids),
        )
        .with_for_update()
        .populate_existing()
        .all()
    )

    found_pool_kw_ids = {p.keyword_id for p in channel_pools}
    missing_keyword_ids = [
        kid for kid in validated.keyword_ids if kid not in found_pool_kw_ids
    ]
    if missing_keyword_ids:
        raise SocialBriefEligibilityError(
            error_code="SOCIAL_KEYWORD_NOT_ELIGIBLE",
            message=(
                f"Seçilen keyword'ler SOCIAL havuzunda bulunamadı veya bu run'a ait değil: "
                f"{missing_keyword_ids}"
            ),
            field="keyword_ids",
            details={
                "requested_keyword_ids": list(validated.keyword_ids),
                "missing_keyword_ids": missing_keyword_ids,
            },
        )

    # 5. Keyword Snapshot üretimi (canlı Keyword.keyword alanı)
    keywords = (
        db.query(Keyword)
        .filter(Keyword.id.in_(validated.keyword_ids))
        .all()
    )
    kw_map = {k.id: k for k in keywords}

    # Snapshot boş/null kontrolü (fail-closed)
    invalid_snapshot_ids = [
        kid
        for kid in validated.keyword_ids
        if kid not in kw_map
        or kw_map[kid].keyword is None
        or not str(kw_map[kid].keyword).strip()
    ]
    if invalid_snapshot_ids:
        raise SocialBriefEligibilityError(
            error_code="SOCIAL_KEYWORD_NOT_ELIGIBLE",
            message=f"Seçilen keyword metin snapshot'ı boş veya geçersiz: {invalid_snapshot_ids}",
            field="keyword_ids",
            details={
                "requested_keyword_ids": list(validated.keyword_ids),
                "missing_keyword_ids": invalid_snapshot_ids,
            },
        )

    # Kullanıcının keyword sırasını ve deterministik 0-tabanlı pozisyonları koru
    eligible_keywords = tuple(
        EligibleSocialBriefKeyword(
            keyword_id=kid,
            keyword_snapshot=kw_map[kid].keyword,
            position=pos,
        )
        for pos, kid in enumerate(validated.keyword_ids)
    )

    return EligibleSocialBriefInput(
        scoring_run_id=validated.scoring_run_id,
        brand_profile_id=brand_profile_id,
        channel_assignment_version=assignment_version,
        format_matrix_version=validated.format_matrix_version,
        brand_name=validated.brand_name,
        brand_context=validated.brand_context,
        keywords=eligible_keywords,
        targets=validated.targets,
    )
