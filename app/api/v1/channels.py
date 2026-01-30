"""
Channel assignment endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.core.channel.channel_engine import ChannelEngine
from app.schemas.channel import (
    ChannelPoolsResponse,
    ChannelAssignmentSummary
)


router = APIRouter()


@router.post("/runs/{run_id}/assign")
def run_channel_assignment(
    run_id: int,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """
    Run full channel assignment process.
    
    Steps:
    1. Build candidate pools (2x capacity)
    2. Analyze intent for each candidate
    3. Filter and build final pools
    4. Identify strategic keywords
    """
    engine = ChannelEngine(db, ai)
    
    try:
        result = engine.run_channel_assignment(run_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs/{run_id}/pools")
def get_channel_pools(
    run_id: int,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Get all channel pools for a scoring run."""
    engine = ChannelEngine(db, ai)
    return engine.get_channel_pools(run_id)


@router.get("/runs/{run_id}/pools/{channel}")
def get_single_channel_pool(
    run_id: int,
    channel: str,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai)
):
    """Get pool for a specific channel."""
    if channel.upper() not in ['ADS', 'SEO', 'SOCIAL']:
        raise HTTPException(status_code=400, detail="Invalid channel")
    
    engine = ChannelEngine(db, ai)
    pools = engine.get_channel_pools(run_id)
    
    return {
        "channel": channel.upper(),
        "keywords": pools.get('channels', {}).get(channel.upper(), [])
    }


