# -*- coding: utf-8 -*-
"""Workspace geneli salt-okunur sosyal içerik geçmişi (plan K13 / F1-H.4).

Aktif workspace'in farklı scoring run ve brief'lerinde üretilmiş sosyal
içerikleri tek listede, en yeniden eskiye döndürür.

Kurallar:
- Workspace izolasyonu: içerik -> fikir -> kategori -> ScoringRun.brand_profile_id.
  Brief'li ve brief'siz (legacy) içerikler aynı yoldan gelir.
- Sıralama deterministik: ``created_at DESC, id DESC``.
- Sayfa boyutu sınırlı; tek veri sorgusu + tek sayım sorgusu (N+1 yok).
- Eskimiş (stale) kayıtlar gizlenmez; ``is_stale`` ile işaretlenir. Görüntüleme
  fazıdır — dedup, yayın durumu veya aktif/arşiv yaşam döngüsü yoktur.
- Legacy satırlar yeni kurallarla yeniden doğrulanmaz (plan §8); bozuk JSON
  alanları güvenli biçimde boş kabul edilir, istek düşmez.
- Salt okunur: commit / rollback / flush / FOR UPDATE yok.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import (
    BrandProfile,
    Keyword,
    ScoringRun,
    SocialBrief,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialIdea,
)

HISTORY_DEFAULT_LIMIT = 20
HISTORY_MAX_LIMIT = 50
HISTORY_MAX_OFFSET = 10_000

_FORMAT_PAYLOAD_KINDS = frozenset({"video", "carousel", "thread", "caption"})


class SocialContentHistoryNotFoundError(Exception):
    """Workspace yok veya arşivlenmiş (404 semantiği)."""


class SocialContentHistoryInputError(ValueError):
    """Geçersiz sayfalama parametresi (400 semantiği)."""


@dataclass(frozen=True)
class SocialHistoryHook:
    text: str
    style: Optional[str]


@dataclass(frozen=True)
class SocialContentHistoryItem:
    id: int
    idea_id: int
    idea_title: Optional[str]
    brief_id: Optional[int]
    brief_is_stale: Optional[bool]
    scoring_run_id: int
    run_name: Optional[str]
    category_name: Optional[str]
    keyword: Optional[str]
    platform: Optional[str]
    content_format: Optional[str]
    hooks: tuple[SocialHistoryHook, ...]
    caption: str
    scenario: Optional[str]
    format_payload: Optional[dict[str, Any]]
    visual_suggestion: Optional[str]
    video_concept: Optional[str]
    cta_text: Optional[str]
    hashtags: tuple[str, ...]
    industry_posting_suggestion: Optional[str]
    platform_notes: Optional[str]
    duration_status: Optional[str]
    actual_duration_sec: Optional[int]
    duration_min_sec: Optional[int]
    duration_max_sec: Optional[int]
    validation_warnings: tuple[str, ...]
    is_stale: bool
    created_at: Optional[datetime]


@dataclass(frozen=True)
class SocialContentHistoryPage:
    brand_profile_id: int
    total: int
    limit: int
    offset: int
    has_more: bool
    items: tuple[SocialContentHistoryItem, ...]


def _is_pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _safe_hooks(raw: Any) -> tuple[SocialHistoryHook, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[SocialHistoryHook] = []
    for h in raw:
        if isinstance(h, str) and h.strip():
            out.append(SocialHistoryHook(text=h, style=None))
        elif isinstance(h, dict) and isinstance(h.get("text"), str) and h["text"].strip():
            style = h.get("style")
            out.append(SocialHistoryHook(text=h["text"], style=style if isinstance(style, str) else None))
    return tuple(out)


def _safe_str_list(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(s for s in raw if isinstance(s, str) and s.strip())


def _safe_format_payload(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("kind") not in _FORMAT_PAYLOAD_KINDS:
        return None
    return raw


def _safe_duration(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def load_social_content_history(
    db: Session,
    *,
    brand_profile_id: int,
    limit: int = HISTORY_DEFAULT_LIMIT,
    offset: int = 0,
    brief_id: Optional[int] = None,
) -> SocialContentHistoryPage:
    """Workspace içerik geçmişinin bir sayfasını döndürür.

    Raises:
        SocialContentHistoryInputError: limit/offset/brief_id sınır dışı.
        SocialContentHistoryNotFoundError: workspace yok veya arşivlenmiş.
    """
    if not _is_pos_int(brand_profile_id):
        raise SocialContentHistoryInputError("brand_profile_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= HISTORY_MAX_LIMIT):
        raise SocialContentHistoryInputError("limit")
    if isinstance(offset, bool) or not isinstance(offset, int) or not (0 <= offset <= HISTORY_MAX_OFFSET):
        raise SocialContentHistoryInputError("offset")
    if brief_id is not None and not _is_pos_int(brief_id):
        raise SocialContentHistoryInputError("brief_id")

    workspace_exists = (
        db.query(BrandProfile.id)
        .filter(BrandProfile.id == brand_profile_id, BrandProfile.deleted_at.is_(None))
        .first()
        is not None
    )
    if not workspace_exists:
        raise SocialContentHistoryNotFoundError()

    base = (
        db.query(SocialContent.id)
        .join(SocialIdea, SocialContent.idea_id == SocialIdea.id)
        .join(SocialCategory, SocialIdea.category_id == SocialCategory.id)
        .join(ScoringRun, SocialCategory.scoring_run_id == ScoringRun.id)
        .filter(ScoringRun.brand_profile_id == brand_profile_id)
    )
    if brief_id is not None:
        base = base.filter(SocialContent.brief_id == brief_id)
    total = base.with_entities(func.count(SocialContent.id)).scalar() or 0

    query = (
        db.query(
            SocialContent,
            SocialIdea.idea_title,
            SocialIdea.target_platform,
            SocialIdea.content_format,
            SocialCategory.category_name,
            ScoringRun.id,
            ScoringRun.run_name,
            SocialBrief.is_stale,
            SocialBriefTarget.duration_min_sec,
            SocialBriefTarget.duration_max_sec,
            Keyword.keyword,
        )
        .join(SocialIdea, SocialContent.idea_id == SocialIdea.id)
        .join(SocialCategory, SocialIdea.category_id == SocialCategory.id)
        .join(ScoringRun, SocialCategory.scoring_run_id == ScoringRun.id)
        .outerjoin(SocialBrief, SocialContent.brief_id == SocialBrief.id)
        .outerjoin(SocialBriefTarget, SocialIdea.brief_target_id == SocialBriefTarget.id)
        .outerjoin(Keyword, SocialIdea.keyword_id == Keyword.id)
        .filter(ScoringRun.brand_profile_id == brand_profile_id)
    )
    if brief_id is not None:
        query = query.filter(SocialContent.brief_id == brief_id)
    rows = (
        query.order_by(SocialContent.created_at.desc().nullslast(), SocialContent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items: list[SocialContentHistoryItem] = []
    for (
        content,
        idea_title,
        idea_platform,
        idea_format,
        category_name,
        run_id,
        run_name,
        brief_is_stale,
        dur_min,
        dur_max,
        keyword,
    ) in rows:
        items.append(
            SocialContentHistoryItem(
                id=content.id,
                idea_id=content.idea_id,
                idea_title=idea_title,
                brief_id=content.brief_id,
                brief_is_stale=None if content.brief_id is None else bool(brief_is_stale),
                scoring_run_id=run_id,
                run_name=run_name,
                category_name=category_name,
                keyword=keyword,
                platform=idea_platform,
                content_format=idea_format,
                hooks=_safe_hooks(content.hooks),
                caption=content.caption or "",
                scenario=content.scenario,
                format_payload=_safe_format_payload(content.format_payload),
                visual_suggestion=content.visual_suggestion,
                video_concept=content.video_concept,
                cta_text=content.cta_text,
                hashtags=_safe_str_list(content.hashtags),
                industry_posting_suggestion=content.industry_posting_suggestion,
                platform_notes=content.platform_notes,
                duration_status=content.duration_status,
                actual_duration_sec=_safe_duration(content.actual_duration_sec),
                duration_min_sec=_safe_duration(dur_min),
                duration_max_sec=_safe_duration(dur_max),
                validation_warnings=_safe_str_list(content.validation_warnings),
                is_stale=bool(content.is_stale),
                created_at=content.created_at,
            )
        )

    return SocialContentHistoryPage(
        brand_profile_id=brand_profile_id,
        total=int(total),
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < int(total),
        items=tuple(items),
    )
