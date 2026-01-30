"""
Scoring endpoints.
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io
import csv

from app.dependencies import get_db
from app.core.scoring.score_engine import ScoreEngine
from app.schemas.scoring import (
    ScoringRunCreate, ScoringRunResponse, ScoringRunStatus,
    ScoringResultsResponse, KeywordScoreResponse
)
from app.database.models import ScoringRun, KeywordScore, Keyword


router = APIRouter()


@router.post("/runs", response_model=ScoringRunResponse, status_code=201)
def create_scoring_run(
    run_data: ScoringRunCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new scoring run.
    This initializes a scoring session with specified capacities.
    """
    engine = ScoreEngine(db)
    scoring_run = engine.create_scoring_run(
        ads_capacity=run_data.ads_capacity,
        seo_capacity=run_data.seo_capacity,
        social_capacity=run_data.social_capacity,
        run_name=run_data.run_name
    )
    return ScoringRunResponse.model_validate(scoring_run)


@router.post("/runs/{run_id}/execute", response_model=dict)
def execute_scoring(
    run_id: int,
    db: Session = Depends(get_db)
):
    """
    Execute scoring for all keywords in the run.
    Calculates ADS, SEO, and SOCIAL scores for all active keywords.
    """
    engine = ScoreEngine(db)
    
    try:
        result = engine.run_scoring(run_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs", response_model=List[ScoringRunStatus])
def list_scoring_runs(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """List all scoring runs."""
    runs = db.query(ScoringRun).order_by(ScoringRun.created_at.desc()).offset(skip).limit(limit).all()
    return [ScoringRunStatus.model_validate(run) for run in runs]


@router.get("/runs/{run_id}", response_model=ScoringRunStatus)
def get_scoring_run(run_id: int, db: Session = Depends(get_db)):
    """Get details of a specific scoring run."""
    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Scoring run not found")
    return ScoringRunStatus.model_validate(run)


@router.delete("/runs/{run_id}", status_code=204)
def delete_scoring_run(run_id: int, db: Session = Depends(get_db)):
    """Delete a scoring run."""
    from app.database import crud
    success = crud.delete_scoring_run(db, run_id)
    if not success:
        raise HTTPException(status_code=404, detail="Scoring run not found")
    return None


@router.get("/runs/{run_id}/scores", response_model=ScoringResultsResponse)
def get_scoring_results(
    run_id: int,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get scored keywords for a run."""
    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Scoring run not found")
    
    scores = (
        db.query(KeywordScore, Keyword)
        .join(Keyword, KeywordScore.keyword_id == Keyword.id)
        .filter(KeywordScore.scoring_run_id == run_id)
        .limit(limit)
        .all()
    )
    
    return ScoringResultsResponse(
        scoring_run_id=run_id,
        status=run.status,
        total_scored=len(scores),
        scores=[
            KeywordScoreResponse(
                keyword_id=score.keyword_id,
                keyword=keyword.keyword,
                ads_score=score.ads_score,
                seo_score=score.seo_score,
                social_score=score.social_score,
                ads_rank=score.ads_rank,
                seo_rank=score.seo_rank,
                social_rank=score.social_rank
            )
            for score, keyword in scores
        ]
    )


@router.get("/runs/{run_id}/top/{channel}")
def get_top_by_channel(
    run_id: int,
    channel: str,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """Get top scoring keywords for a specific channel."""
    if channel.upper() not in ['ADS', 'SEO', 'SOCIAL']:
        raise HTTPException(status_code=400, detail="Invalid channel. Use ADS, SEO, or SOCIAL")
    
    engine = ScoreEngine(db)
    top_keywords = engine.get_top_keywords_by_channel(run_id, channel.upper(), limit)
    
    return {
        "channel": channel.upper(),
        "top_keywords": top_keywords
    }


@router.get("/runs/{run_id}/export/csv")
def export_scoring_csv(
    run_id: int,
    db: Session = Depends(get_db)
):
    """Export scoring results as CSV file."""
    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Scoring run not found")
    
    scores = (
        db.query(KeywordScore, Keyword)
        .join(Keyword, KeywordScore.keyword_id == Keyword.id)
        .filter(KeywordScore.scoring_run_id == run_id)
        .all()
    )
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        'Keyword ID', 'Keyword', 'Monthly Volume', 'Sector',
        'ADS Score', 'ADS Rank', 'SEO Score', 'SEO Rank', 
        'SOCIAL Score', 'SOCIAL Rank'
    ])
    
    # Data rows
    for score, keyword in scores:
        writer.writerow([
            keyword.id,
            keyword.keyword,
            keyword.monthly_volume,
            keyword.sector or '',
            float(score.ads_score) if score.ads_score else 0,
            score.ads_rank or 0,
            float(score.seo_score) if score.seo_score else 0,
            score.seo_rank or 0,
            float(score.social_score) if score.social_score else 0,
            score.social_rank or 0
        ])
    
    output.seek(0)
    
    filename = f"scoring_run_{run_id}_{run.run_name or 'export'}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
