"""
Keyword management endpoints.

Plan2 §P0/C3 — legacy global yollar kapatıldı. brand_profile_id artık
list/get/create/update/delete/import/cleanup-duplicates için zorunlu;
verilmezse 400 ("brand_profile_id is required"). Global Keyword
silme yolları (crud.delete_all_keywords / crud.delete_keyword) artık
endpoint'lerden çağrılmıyor.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.workspace import verify_workspace
from app.database import crud
from app.database.models import Keyword, WorkspaceKeyword
from app.dependencies import get_db
from app.schemas.keyword import (
    KeywordCreate,
    KeywordImportRequest,
    KeywordImportResponse,
    KeywordListResponse,
    KeywordResponse,
    KeywordUpdate,
    WorkspaceKeywordResponse,
)


router = APIRouter()




def _verify_keyword_in_workspace(
    db: Session, keyword_id: int, brand_profile_id: int
) -> tuple[Keyword, WorkspaceKeyword]:
    """Return (Keyword, WorkspaceKeyword) when the keyword is linked to the
    workspace; otherwise raise 404 (existence leak prevention)."""
    row = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(Keyword.id == keyword_id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="keyword not found in workspace")
    keyword, wk = row
    return keyword, wk


@router.get("/", response_model=KeywordListResponse)
def list_keywords(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2000),
    active_only: bool = Query(True),
    sector: Optional[str] = Query(None),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """List keywords in a workspace.

    `brand_profile_id` zorunludur. Yalnızca bu workspace'in WorkspaceKeyword
    bağlantılı keyword'leri döner.
    """
    verify_workspace(db, brand_profile_id)

    base_query = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
    )
    if active_only:
        base_query = base_query.filter(Keyword.is_active == True)  # noqa: E712
    if sector:
        base_query = base_query.filter(WorkspaceKeyword.sector == sector)

    total = base_query.count()
    rows = base_query.order_by(Keyword.id).offset(skip).limit(limit).all()

    items = []
    for kw, wk in rows:
        resp = WorkspaceKeywordResponse.model_validate(kw)
        resp.monthly_volume = wk.monthly_volume or 0
        resp.trend_3m = wk.trend_3m or 0
        resp.trend_12m = wk.trend_12m or 0
        resp.competition_score = wk.competition_score or 0.5
        resp.sector = wk.sector
        resp.target_market = wk.target_market
        resp.data_source = wk.data_source or "csv"
        resp.wk_monthly_volume = resp.monthly_volume
        resp.wk_trend_3m = float(resp.trend_3m)
        resp.wk_trend_12m = float(resp.trend_12m)
        resp.wk_competition_score = float(resp.competition_score)
        resp.wk_data_source = resp.data_source
        items.append(resp)

    return KeywordListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{keyword_id}", response_model=KeywordResponse)
def get_keyword(
    keyword_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get a specific keyword by ID. The keyword must be linked to the workspace."""
    verify_workspace(db, brand_profile_id)
    keyword, _ = _verify_keyword_in_workspace(db, keyword_id, brand_profile_id)
    return KeywordResponse.model_validate(keyword)


@router.post("/", response_model=KeywordResponse, status_code=201)
def create_keyword(
    keyword_data: KeywordCreate,
    db: Session = Depends(get_db),
):
    """Create a keyword and link it to a workspace.

    `brand_profile_id` zorunludur (request body içinden gelir).
    """
    if keyword_data.brand_profile_id is None:
        raise HTTPException(status_code=400, detail="brand_profile_id is required")
    verify_workspace(db, keyword_data.brand_profile_id)

    result = crud.create_keywords_bulk(
        db,
        [keyword_data.model_dump(exclude={"brand_profile_id"})],
        brand_profile_id=keyword_data.brand_profile_id,
        return_details=True,
    )
    if isinstance(result, dict) and (result["created"] + result["linked"]) > 0:
        keyword = crud.get_keyword_by_text(db, keyword_data.keyword)
        if keyword:
            return KeywordResponse.model_validate(keyword)
    raise HTTPException(status_code=400, detail="Keyword could not be created")


@router.put("/{keyword_id}", response_model=KeywordResponse)
def update_keyword(
    keyword_id: int,
    keyword_data: KeywordUpdate,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Update a keyword linked to a workspace.

    Keyword rows are global and can be linked to multiple workspaces. Updating a
    shared row would leak the change into other workspaces, so shared keywords
    are rejected until a clone/relink flow is implemented.
    """
    verify_workspace(db, brand_profile_id)
    _verify_keyword_in_workspace(db, keyword_id, brand_profile_id)

    link_count = (
        db.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.keyword_id == keyword_id)
        .count()
    )
    if link_count > 1:
        raise HTTPException(
            status_code=409,
            detail="keyword is linked to multiple workspaces; update would affect other workspaces",
        )

    keyword = crud.update_keyword(
        db,
        keyword_id,
        keyword_data.model_dump(exclude_unset=True),
    )
    if not keyword:
        raise HTTPException(status_code=404, detail="keyword not found")
    return KeywordResponse.model_validate(keyword)


@router.delete("/all", status_code=204)
def delete_all_keywords(
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Unlink all keywords from a workspace.

    `brand_profile_id` zorunlu. Global Keyword tablosu KORUNUR — yalnızca
    WorkspaceKeyword bağlantıları silinir. Global delete legacy yolu
    plan2 §P0/C3 ile kaldırıldı (veri kaybı riski).
    """
    verify_workspace(db, brand_profile_id)
    (
        db.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return None


@router.delete("/{keyword_id}", status_code=204)
def delete_keyword(
    keyword_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Unlink a keyword from a workspace.

    `brand_profile_id` zorunlu. Global Keyword satırı KORUNUR — yalnızca
    WorkspaceKeyword bağlantısı silinir. (plan2 §P0/C3)
    """
    verify_workspace(db, brand_profile_id)

    success = crud.remove_keyword_from_workspace(db, keyword_id, brand_profile_id)
    if not success:
        raise HTTPException(status_code=404, detail="keyword not found in workspace")
    return None


@router.post("/import", response_model=KeywordImportResponse)
def import_keywords(
    import_data: KeywordImportRequest,
    db: Session = Depends(get_db),
):
    """Bulk import keywords into a workspace. `brand_profile_id` zorunlu."""
    if import_data.brand_profile_id is None:
        raise HTTPException(status_code=400, detail="brand_profile_id is required")
    verify_workspace(db, import_data.brand_profile_id)

    result = crud.create_keywords_bulk(
        db,
        [kw.model_dump(exclude={"brand_profile_id"}) for kw in import_data.keywords],
        brand_profile_id=import_data.brand_profile_id,
        return_details=True,
    )

    if isinstance(result, dict):
        created = result["created"] + result["linked"]
        skipped = result["skipped_exact"] + result["skipped_fuzzy"]
    else:
        created = result
        skipped = len(import_data.keywords) - created

    return KeywordImportResponse(
        created=created,
        skipped=skipped,
        message=f"{created} keywords created/linked, {skipped} skipped (already exist)",
    )


@router.post("/cleanup-duplicates")
def cleanup_duplicate_keywords(
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Workspace içindeki fuzzy duplicate keyword'leri tespit edip deactivate eder.

    `brand_profile_id` zorunlu. Sadece bu workspace'e bağlı WorkspaceKeyword'lerin
    altındaki Keyword'lerde dedup yapar (plan2 §P0/C3 ile global cleanup kaldırıldı).
    """
    from loguru import logger

    from app.core.keyword_dedup import deduplicate_keywords

    verify_workspace(db, brand_profile_id)

    # Workspace'e bağlı, aktif keyword'leri çek.
    rows = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .filter(Keyword.is_active == True)  # noqa: E712
        .all()
    )

    if len(rows) <= 1:
        return {
            "total_active": len(rows),
            "duplicates_deactivated": 0,
            "deactivated_keywords": [],
            "message": "No duplicates found",
        }

    keyword_dicts = [
        {
            "keyword": kw.keyword,
            "monthly_volume": wk.monthly_volume or 0,
            "competition_score": float(wk.competition_score or 0),
            "_db_id": kw.id,
        }
        for kw, wk in rows
    ]

    deduped = deduplicate_keywords(keyword_dicts)
    survived_ids = {d["_db_id"] for d in deduped}

    deactivated = []
    for kw, _wk in rows:
        if kw.id not in survived_ids:
            kw.is_active = False
            deactivated.append({"id": kw.id, "keyword": kw.keyword})

    if deactivated:
        db.commit()
        logger.info(
            "Workspace {} cleanup: {} duplicate keyword(s) deactivated",
            brand_profile_id,
            len(deactivated),
        )

    return {
        "total_active_before": len(rows),
        "total_active_after": len(deduped),
        "duplicates_deactivated": len(deactivated),
        "deactivated_keywords": deactivated[:50],
        "message": f"{len(deactivated)} duplicate keyword(s) deactivated",
    }
