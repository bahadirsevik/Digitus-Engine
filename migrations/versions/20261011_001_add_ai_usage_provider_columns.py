"""add_ai_usage_provider_columns

Claude karsilastirmasi telemetrisi:
- ai_usage_events.provider / cache_read_tokens / cache_creation_tokens /
  queue_wait_ms / http_attempts (hepsi nullable)
- ck_screening_job_provider 'anthropic' kabul eder.

IDEMPOTENT: baseline squash canli `Base.metadata.create_all` kullandigi icin
yeni kurulumda kolonlar/kisit zaten guncel olabilir; varlik/metin kontrolu
yapilir, yeniden calistirmak no-op'tur.

Revision ID: 20261011_001
Revises: 20261010_001
Create Date: 2026-10-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261011_001"
down_revision: Union[str, None] = "20261010_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ai_usage_events"
_COLUMNS = (
    ("provider", sa.String(length=20)),
    ("cache_read_tokens", sa.Integer()),
    ("cache_creation_tokens", sa.Integer()),
    ("queue_wait_ms", sa.Integer()),
    ("http_attempts", sa.Integer()),
)
_JOB_TABLE = "corpus_screening_jobs"
_CK = "ck_screening_job_provider"
_NEW = "provider IN ('gemini', 'deepseek', 'anthropic')"
_OLD = "provider IN ('gemini', 'deepseek')"


def _existing_columns() -> set:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _ck_has_anthropic() -> bool:
    inspector = sa.inspect(op.get_bind())
    for ck in inspector.get_check_constraints(_JOB_TABLE):
        if ck.get("name") == _CK:
            return "anthropic" in (ck.get("sqltext") or "")
    return False


def _set_ck(expr: str) -> None:
    # IF EXISTS: kisit yoksa da yeniden calistirilabilir
    op.execute(f"ALTER TABLE {_JOB_TABLE} DROP CONSTRAINT IF EXISTS {_CK}")
    op.create_check_constraint(_CK, _JOB_TABLE, expr)


def upgrade() -> None:
    existing = _existing_columns()
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))
    if not _ck_has_anthropic():
        _set_ck(_NEW)


def downgrade() -> None:
    existing = _existing_columns()
    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
    if _ck_has_anthropic():
        _set_ck(_OLD)
