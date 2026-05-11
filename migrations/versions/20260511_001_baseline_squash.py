"""baseline_squash

Revision ID: 20260511_001
Revises:
Create Date: 2026-05-11

Plan2 P0/C1 — Migration chain squash.

Previous reality: the migration chain (17 files in `_archived/`) only added
incremental features (seo_geo, google_ads, social, task_results, workspace
phases A/B/C, …) and assumed that core tables (keywords, scoring_runs,
keyword_scores, channel_candidates, intent_analysis, channel_pools,
content_outputs, compliance_checks) already existed. They were created by
`init_db()` (Base.metadata.create_all) in `docker-compose.prod.yml`'s
startup command, which then stamped HEAD without ever running real migrations.

As a result `alembic upgrade head` against an empty Postgres failed at
the first migration that FK'd a "baseline" table (task_results -> scoring_runs).
Plan2 §P0 madde 2 caught this and §P3 sanctioned drop+recreate during dev.

This squash:
- Replaces the entire archived chain with a single baseline migration.
- Uses `Base.metadata.create_all()` on the live bind, guaranteeing the
  schema exactly matches the current `app/database/models.py`.
- The 17 archived migrations remain in `migrations/versions/_archived/` for
  historical reference but are not loaded by Alembic.

Going forward: new schema changes should use normal `alembic revision
--autogenerate` against this baseline.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260511_001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create every table currently declared in `app.database.models.Base`."""
    from app.database.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop every table currently declared in `app.database.models.Base`."""
    from app.database.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
