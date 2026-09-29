"""Motor v3: scoring_runs.algorithm_version CHECK kısıtını genişlet.

plan_algoritma_entegrasyonu.md K2 — v3 bir run sürümü olarak KABUL
EDİLEBİLİR olmalı (bayrak kontrolü uygulama katmanındadır, bkz.
ENABLE_ENGINE_V3 / app/api/v1/scoring.py). 20260723_001'de eklenen
`ck_scoring_runs_algorithm_version` yalnız ('v2', 'v2_1') izin veriyordu;
bu kısıt DEĞİŞTİRİLMEDEN v3 run'lar INSERT aşamasında IntegrityError ile
reddedilirdi (uygulama katmanındaki 409 kapılarından tamamen bağımsız bir
engel). MEVCUT migration dosyasına DOKUNULMAZ — kısıt burada, yeni bir
migration'da, idempotent biçimde drop+recreate edilir.

Revision ID: 20260918_002
Revises: 20260918_001
Create Date: 2026-09-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260918_002"
down_revision: Union[str, None] = "20260918_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALGO_CK = "ck_scoring_runs_algorithm_version"
_OLD_CONDITION = "algorithm_version IN ('v2', 'v2_1')"
_NEW_CONDITION = "algorithm_version IN ('v2', 'v2_1', 'v3')"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_cks = {
        ck["name"]: ck for ck in inspector.get_check_constraints("scoring_runs")
    }
    ck = existing_cks.get(_ALGO_CK)
    # Idempotent: kısıt zaten 'v3'e izin veriyorsa (ör. baseline'dan taze
    # kurulum) tekrar drop+create yapılmaz.
    already_allows_v3 = ck is not None and "'v3'" in (ck.get("sqltext") or "")
    if already_allows_v3:
        return

    if ck is not None:
        op.drop_constraint(_ALGO_CK, "scoring_runs", type_="check")
    op.create_check_constraint(_ALGO_CK, "scoring_runs", _NEW_CONDITION)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_cks = {
        ck["name"]: ck for ck in inspector.get_check_constraints("scoring_runs")
    }
    ck = existing_cks.get(_ALGO_CK)
    if ck is None:
        op.create_check_constraint(_ALGO_CK, "scoring_runs", _OLD_CONDITION)
        return
    if "'v3'" in (ck.get("sqltext") or ""):
        op.drop_constraint(_ALGO_CK, "scoring_runs", type_="check")
        op.create_check_constraint(_ALGO_CK, "scoring_runs", _OLD_CONDITION)
