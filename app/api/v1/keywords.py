"""
Keyword management endpoints.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.database import crud
from app.database.models import Keyword
from app.schemas.keyword import (
    KeywordCreate, KeywordUpdate, KeywordResponse,
    KeywordListResponse, KeywordImportRequest, KeywordImportResponse
)


router = APIRouter()


@router.get("/", response_model=KeywordListResponse)
def list_keywords(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2000),
    active_only: bool = Query(True),
    sector: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    List keywords with pagination and filtering.
    
    - **skip**: Number of records to skip (pagination)
    - **limit**: Max number of records to return
    - **active_only**: Filter only active keywords
    - **sector**: Filter by sector
    """
    keywords = crud.get_keywords(db, skip=skip, limit=limit, active_only=active_only, sector=sector)
    total = db.query(Keyword).filter(Keyword.is_active == True if active_only else True).count()
    
    return KeywordListResponse(
        items=[KeywordResponse.model_validate(kw) for kw in keywords],
        total=total,
        skip=skip,
        limit=limit
    )


@router.get("/{keyword_id}", response_model=KeywordResponse)
def get_keyword(keyword_id: int, db: Session = Depends(get_db)):
    """Get a specific keyword by ID."""
    keyword = crud.get_keyword(db, keyword_id)
    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found")
    return KeywordResponse.model_validate(keyword)


@router.post("/", response_model=KeywordResponse, status_code=201)
def create_keyword(keyword_data: KeywordCreate, db: Session = Depends(get_db)):
    """Create a new keyword."""
    # Check if already exists
    existing = crud.get_keyword_by_text(db, keyword_data.keyword)
    if existing:
        raise HTTPException(status_code=400, detail="Keyword already exists")
    
    keyword = crud.create_keyword(db, keyword_data.model_dump())
    return KeywordResponse.model_validate(keyword)


@router.put("/{keyword_id}", response_model=KeywordResponse)
def update_keyword(
    keyword_id: int,
    keyword_data: KeywordUpdate,
    db: Session = Depends(get_db)
):
    """Update an existing keyword."""
    keyword = crud.update_keyword(db, keyword_id, keyword_data.model_dump(exclude_unset=True))
    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found")
    return KeywordResponse.model_validate(keyword)


@router.delete("/all", status_code=204)
def delete_all_keywords(db: Session = Depends(get_db)):
    """Delete all keywords."""
    crud.delete_all_keywords(db)
    return None


@router.delete("/{keyword_id}", status_code=204)
def delete_keyword(keyword_id: int, db: Session = Depends(get_db)):
    """Delete a keyword."""
    success = crud.delete_keyword(db, keyword_id)
    if not success:
        raise HTTPException(status_code=404, detail="Keyword not found")
    return None


@router.post("/import", response_model=KeywordImportResponse)
def import_keywords(
    import_data: KeywordImportRequest,
    db: Session = Depends(get_db)
):
    """Bulk import keywords."""
    created = crud.create_keywords_bulk(db, [kw.model_dump() for kw in import_data.keywords])
    skipped = len(import_data.keywords) - created
    
    return KeywordImportResponse(
        created=created,
        skipped=skipped,
        message=f"{created} keywords created, {skipped} skipped (already exist)"
    )
