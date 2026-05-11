"""
Workspace doğrulama helper'ları (plan2 §P0 — ortak guard isimlendirme).

Standart kontrat (hepsi için aynı):
- brand_profile_id None ise HTTPException 400 ("required") — mutating=True'da.
- brand_profile_id var ama workspace yoksa/arşivse 404 ("not found").
- İlişkili obje (run/task/keyword/export) workspace ile eşleşmiyorsa 404
  (mevcudiyet sızıntısını önlemek için 403 değil).

Endpoint kodu sadece tek satır `obj = verify_X(...)` çağırarak işini bitirir.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.orm import Session

from app.database.models import BrandProfile, ScoringRun


def verify_workspace(db: Session, brand_profile_id: int) -> BrandProfile:
    """Workspace var mı + arşivlenmiş değil mi?

    Raises HTTPException 404 if not found or soft-deleted.
    """
    workspace = db.query(BrandProfile).filter(
        BrandProfile.id == brand_profile_id,
        BrandProfile.deleted_at.is_(None),
    ).first()

    if not workspace:
        raise HTTPException(
            status_code=404,
            detail=f"Marka çalışması (id={brand_profile_id}) bulunamadı veya arşivlenmiş",
        )

    return workspace


def verify_scoring_run(
    db: Session,
    run_id: int,
    brand_profile_id: Optional[int] = None,
    *,
    mutating: bool = False,
) -> ScoringRun:
    """Scoring run var mı + verilen workspace'e ait mi?

    Args:
        db: Database session.
        run_id: ScoringRun ID.
        brand_profile_id: Optional workspace scope.
            - `mutating=True` iken zorunlu (None ise 400).
            - `mutating=False` iken opsiyonel; None geçilirse geçiş dönemi
              warning'i loglanır ve sadece existence kontrolü yapılır.
        mutating: Endpoint state değiştiriyor mu (POST/DELETE/EXECUTE).
            Read-only endpoint'ler False bırakır.

    Returns:
        ScoringRun instance.

    Raises:
        HTTPException 400: mutating ama brand_profile_id None.
        HTTPException 404: run bulunamadı veya workspace ile eşleşmiyor.
    """
    if mutating and brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="brand_profile_id is required",
        )

    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail="scoring run not found",
        )

    if brand_profile_id is not None:
        # Workspace must exist + match.
        verify_workspace(db, brand_profile_id)
        if run.brand_profile_id != brand_profile_id:
            raise HTTPException(
                status_code=404,
                detail="scoring run not found in workspace",
            )
    else:
        logger.warning(
            "legacy scoring run access run_id={} — brand_profile_id omitted; "
            "this code path will be removed in P4",
            run_id,
        )

    return run