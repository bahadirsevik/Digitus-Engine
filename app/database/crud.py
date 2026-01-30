"""
CRUD operations for database models.
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.database.models import (
    Keyword, ScoringRun, KeywordScore, 
    ChannelCandidate, IntentAnalysis, ChannelPool,
    ContentOutput, ComplianceCheck
)


# ==================== KEYWORD CRUD ====================

def get_keyword(db: Session, keyword_id: int) -> Optional[Keyword]:
    """Get a keyword by ID."""
    return db.query(Keyword).filter(Keyword.id == keyword_id).first()


def get_keyword_by_text(db: Session, keyword: str) -> Optional[Keyword]:
    """Get a keyword by text."""
    return db.query(Keyword).filter(Keyword.keyword == keyword).first()


def get_keywords(
    db: Session, 
    skip: int = 0, 
    limit: int = 100,
    active_only: bool = True,
    sector: Optional[str] = None
) -> List[Keyword]:
    """Get list of keywords with optional filters."""
    query = db.query(Keyword)
    
    if active_only:
        query = query.filter(Keyword.is_active == True)
    
    if sector:
        query = query.filter(Keyword.sector == sector)
    
    return query.offset(skip).limit(limit).all()


def get_all_active_keywords(db: Session) -> List[Keyword]:
    """Get all active keywords."""
    return db.query(Keyword).filter(Keyword.is_active == True).all()


def create_keyword(db: Session, keyword_data: Dict[str, Any]) -> Keyword:
    """Create a new keyword."""
    db_keyword = Keyword(**keyword_data)
    db.add(db_keyword)
    db.commit()
    db.refresh(db_keyword)
    return db_keyword


def create_keywords_bulk(db: Session, keywords_data: List[Dict[str, Any]]) -> int:
    """Create multiple keywords at once. Returns count of created."""
    created = 0
    skipped = 0
    
    # Sanitize and filter data before inserting
    sanitized_keywords = []
    for kw_data in keywords_data:
        # Skip if keyword already exists
        existing = get_keyword_by_text(db, kw_data.get('keyword', ''))
        if existing:
            skipped += 1
            continue
        
        # Sanitize trend values to prevent overflow (Numeric(7,2) max is 99999.99)
        if 'trend_12m' in kw_data:
            try:
                val = float(kw_data['trend_12m'])
                if not (-9999 <= val <= 9999):
                    kw_data['trend_12m'] = 0
            except (TypeError, ValueError):
                kw_data['trend_12m'] = 0
        
        if 'trend_3m' in kw_data:
            try:
                val = float(kw_data['trend_3m'])
                if not (-9999 <= val <= 9999):
                    kw_data['trend_3m'] = 0
            except (TypeError, ValueError):
                kw_data['trend_3m'] = 0
        
        sanitized_keywords.append(kw_data)
    
    # Insert in batches
    batch_size = 50
    for i in range(0, len(sanitized_keywords), batch_size):
        batch = sanitized_keywords[i:i + batch_size]
        try:
            for kw_data in batch:
                db_keyword = Keyword(**kw_data)
                db.add(db_keyword)
            db.commit()
            created += len(batch)
        except Exception as e:
            db.rollback()
            # Try one by one if batch failed
            for kw_data in batch:
                try:
                    db_keyword = Keyword(**kw_data)
                    db.add(db_keyword)
                    db.commit()
                    created += 1
                except Exception as inner_e:
                    db.rollback()
                    skipped += 1
                    print(f"Keyword import error: {kw_data.get('keyword', 'unknown')}: {str(inner_e)[:100]}")
    
    return created


def update_keyword(db: Session, keyword_id: int, update_data: Dict[str, Any]) -> Optional[Keyword]:
    """Update a keyword."""
    db_keyword = get_keyword(db, keyword_id)
    if db_keyword:
        for key, value in update_data.items():
            setattr(db_keyword, key, value)
        db.commit()
        db.refresh(db_keyword)
    return db_keyword


def delete_keyword(db: Session, keyword_id: int) -> bool:
    """Delete a keyword."""
    db_keyword = get_keyword(db, keyword_id)
    if db_keyword:
        db.delete(db_keyword)
        db.commit()
        return True
    return False


def delete_all_keywords(db: Session) -> int:
    """Delete all keywords. Returns number of deleted items."""
    count = db.query(Keyword).delete()
    db.commit()
    return count


# ==================== SCORING RUN CRUD ====================

def get_scoring_run(db: Session, run_id: int) -> Optional[ScoringRun]:
    """Get a scoring run by ID."""
    return db.query(ScoringRun).filter(ScoringRun.id == run_id).first()


def get_scoring_runs(db: Session, skip: int = 0, limit: int = 20) -> List[ScoringRun]:
    """Get list of scoring runs."""
    return db.query(ScoringRun).order_by(ScoringRun.created_at.desc()).offset(skip).limit(limit).all()


def create_scoring_run(db: Session, run_data: Dict[str, Any]) -> ScoringRun:
    """Create a new scoring run."""
    db_run = ScoringRun(**run_data)
    db.add(db_run)
    db.commit()
    db.refresh(db_run)
    return db_run


def update_scoring_run_status(db: Session, run_id: int, status: str) -> Optional[ScoringRun]:
    """Update scoring run status."""
    db_run = get_scoring_run(db, run_id)
    if db_run:
        db_run.status = status
        db.commit()
        db.refresh(db_run)
    return db_run


def delete_scoring_run(db: Session, run_id: int) -> bool:
    """Delete a scoring run."""
    db_run = get_scoring_run(db, run_id)
    if db_run:
        db.delete(db_run)
        db.commit()
        return True
    return False


# ==================== KEYWORD SCORE CRUD ====================

def get_keyword_scores_by_run(
    db: Session, 
    scoring_run_id: int,
    limit: int = 1000
) -> List[KeywordScore]:
    """Get all keyword scores for a scoring run."""
    return db.query(KeywordScore).filter(
        KeywordScore.scoring_run_id == scoring_run_id
    ).limit(limit).all()


def create_keyword_score(db: Session, score_data: Dict[str, Any]) -> KeywordScore:
    """Create a keyword score."""
    db_score = KeywordScore(**score_data)
    db.add(db_score)
    db.commit()
    db.refresh(db_score)
    return db_score


def create_keyword_scores_bulk(db: Session, scores_data: List[Dict[str, Any]]) -> int:
    """Create multiple keyword scores at once."""
    for score_data in scores_data:
        db.add(KeywordScore(**score_data))
    db.commit()
    return len(scores_data)


# ==================== CHANNEL POOL CRUD ====================

def get_channel_pool(
    db: Session, 
    scoring_run_id: int, 
    channel: str
) -> List[ChannelPool]:
    """Get channel pool for a specific channel."""
    return db.query(ChannelPool).filter(
        and_(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == channel
        )
    ).order_by(ChannelPool.final_rank).all()


def get_strategic_keywords(db: Session, scoring_run_id: int) -> List[ChannelPool]:
    """Get strategic keywords (appear in multiple channels)."""
    return db.query(ChannelPool).filter(
        and_(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.is_strategic == True
        )
    ).all()


# ==================== CONTENT OUTPUT CRUD ====================

def get_content_outputs_by_keyword(
    db: Session, 
    keyword_id: int
) -> List[ContentOutput]:
    """Get all content outputs for a keyword."""
    return db.query(ContentOutput).filter(
        ContentOutput.keyword_id == keyword_id
    ).all()


def create_content_output(db: Session, content_data: Dict[str, Any]) -> ContentOutput:
    """Create a content output."""
    db_content = ContentOutput(**content_data)
    db.add(db_content)
    db.commit()
    db.refresh(db_content)
    return db_content
