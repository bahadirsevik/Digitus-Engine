"""Read the campaign offer fixed by the first persisted content.

Writers must hold the existing SocialBrief row lock while checking this value.
No extra schema or transaction is needed: saved content is the source of truth.
"""
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import SocialContent


def load_locked_offer_terms(db: Session, brief_id: int) -> dict[str, Any] | None:
    rows = (
        db.query(SocialContent.campaign_payload)
        .filter(SocialContent.brief_id == brief_id)
        .order_by(SocialContent.id.asc())
    )
    for (payload,) in rows:
        if isinstance(payload, dict) and isinstance(payload.get("offer_terms"), dict):
            return deepcopy(payload["offer_terms"])
    return None
