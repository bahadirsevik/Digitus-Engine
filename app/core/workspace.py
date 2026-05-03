"""
Workspace doğrulama (verify_workspace).

Tüm yazma endpoint'leri brand_profile_id aldığında bu helper'ı çağırır.
İlk faz: auth yok, sadece existence + soft-delete kontrolü.
Auth eklendiğinde user ownership kontrolü buraya eklenir.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.models import BrandProfile


def verify_workspace(db: Session, brand_profile_id: int) -> BrandProfile:
    """
    Workspace var mı + arşivlenmiş değil mi?

    Args:
        db: Database session
        brand_profile_id: Workspace ID

    Returns:
        BrandProfile instance

    Raises:
        HTTPException 404: Workspace bulunamadı veya arşivlenmiş
    """
    workspace = db.query(BrandProfile).filter(
        BrandProfile.id == brand_profile_id,
        BrandProfile.deleted_at.is_(None),
    ).first()

    if not workspace:
        raise HTTPException(
            status_code=404,
            detail=f"Marka çalışması (id={brand_profile_id}) bulunamadı veya arşivlenmiş"
        )

    return workspace