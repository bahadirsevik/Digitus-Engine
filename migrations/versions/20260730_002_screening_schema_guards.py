"""Corpus screening şema garantileri (Codex 6. tur, 5 bulgu).

20260730_001 tabloları oluşturur; bu migration YÜKSELTME YOLUNDA eksik
kalan/gevşek olan kısıtları TAMAMLAR. Fresh install'da baseline squash
canlı `create_all` kullandığı için kısıtlar zaten vardır — hepsi
varlık-kontrollüdür (repo idempotency kuralı).

Kapatılanlar:
1. `channel_assignment_attempts.screening_job_id` → `corpus_screening_jobs`
   FK'si (001'de kolon vardı, FK YOKTU: job tablosu attempts'ten SONRA
   yaratılıyor).
2. Ledger terminal durum sertleştirmesi: `actual_usd <= ceiling_usd` ve
   settled/ceiling_charged için `settled_at IS NOT NULL`.
3. Completed checkpoint GERÇEK payload ister (object/array + SHA +
   completed_at) — aksi halde resume batch'i ücretsiz atlar ama kararları
   yeniden kuramaz.
4. Selection satırının run'ı, attempt'in run'ı ile AYNI olmalı:
   `channel_assignment_attempts(id, scoring_run_id)` UNIQUE + selections
   üzerinde composite FK (tekil attempt FK'si düşer).
5. Checkpoint `(screening_job_id, view, batch_ordinal)` tekilliği.

Revision ID: 20260730_002
Revises: 20260730_001
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_002"
down_revision: Union[str, None] = "20260730_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ATTEMPTS = "channel_assignment_attempts"
SELECTIONS = "corpus_candidate_selections"
CHECKPOINTS = "corpus_screening_batch_checkpoints"
RESERVATIONS = "ai_cost_reservations"


def _fk_names(inspector, table: str) -> dict:
    return {fk["name"]: fk for fk in inspector.get_foreign_keys(table)}


def _unique_names(inspector, table: str) -> set:
    return {u["name"] for u in inspector.get_unique_constraints(table)}


def _check_names(inspector, table: str) -> set:
    return {c["name"] for c in inspector.get_check_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if ATTEMPTS not in tables:          # 001 uygulanmamış → yapacak iş yok
        return

    # ── 1) attempts → screening job FK ───────────────────────────────
    has_job_fk = any(
        fk["constrained_columns"] == ["screening_job_id"]
        and fk["referred_table"] == "corpus_screening_jobs"
        for fk in inspector.get_foreign_keys(ATTEMPTS))
    if not has_job_fk and "corpus_screening_jobs" in tables:
        op.create_foreign_key(
            "fk_assignment_attempt_screening_job", ATTEMPTS,
            "corpus_screening_jobs", ["screening_job_id"], ["id"],
            ondelete="SET NULL")

    # ── 4) attempts (id, scoring_run_id) UNIQUE + composite FK ───────
    if "uq_assignment_attempt_id_run" not in _unique_names(inspector,
                                                           ATTEMPTS):
        op.create_unique_constraint("uq_assignment_attempt_id_run", ATTEMPTS,
                                    ["id", "scoring_run_id"])
    inspector = sa.inspect(bind)
    if SELECTIONS in tables:
        sel_fks = _fk_names(inspector, SELECTIONS)
        has_composite = any(
            set(fk["constrained_columns"])
            == {"assignment_attempt_id", "scoring_run_id"}
            and fk["referred_table"] == ATTEMPTS
            for fk in sel_fks.values())
        if not has_composite:
            # tekil attempt FK'sini düşür (adı otomatik üretilmiş olabilir)
            for name, fk in sel_fks.items():
                if (fk["constrained_columns"] == ["assignment_attempt_id"]
                        and fk["referred_table"] == ATTEMPTS and name):
                    op.drop_constraint(name, SELECTIONS, type_="foreignkey")
            op.create_foreign_key(
                "fk_candidate_selection_attempt_run", SELECTIONS, ATTEMPTS,
                ["assignment_attempt_id", "scoring_run_id"],
                ["id", "scoring_run_id"], ondelete="CASCADE")

    # ── 2) ledger terminal durum sertleştirmesi ──────────────────────
    inspector = sa.inspect(bind)
    if RESERVATIONS in tables:
        res_checks = _check_names(inspector, RESERVATIONS)
        if "ck_ai_cost_reservation_state_transition" in res_checks:
            op.drop_constraint("ck_ai_cost_reservation_state_transition",
                               RESERVATIONS, type_="check")
        op.create_check_constraint(
            "ck_ai_cost_reservation_state_transition", RESERVATIONS,
            "(state = 'reserved' AND actual_usd IS NULL AND "
            " settled_at IS NULL) OR "
            "(state IN ('settled', 'ceiling_charged') AND "
            " actual_usd IS NOT NULL AND settled_at IS NOT NULL)")
        if "ck_ai_cost_reservation_actual_le_ceiling" not in res_checks:
            op.create_check_constraint(
                "ck_ai_cost_reservation_actual_le_ceiling", RESERVATIONS,
                "actual_usd IS NULL OR actual_usd <= ceiling_usd")

    # ── 3) completed checkpoint gerçek payload ister ─────────────────
    inspector = sa.inspect(bind)
    if CHECKPOINTS in tables:
        cp_checks = _check_names(inspector, CHECKPOINTS)
        if "ck_screening_checkpoint_completed_payload" in cp_checks:
            op.drop_constraint("ck_screening_checkpoint_completed_payload",
                               CHECKPOINTS, type_="check")
        op.create_check_constraint(
            "ck_screening_checkpoint_completed_payload", CHECKPOINTS,
            "state <> 'completed' OR (payload IS NOT NULL "
            "AND payload_sha256 IS NOT NULL AND completed_at IS NOT NULL "
            "AND json_typeof(payload) IN ('object', 'array'))")

        # ── 5) ordinal tekilliği ─────────────────────────────────────
        if "uq_screening_checkpoint_ordinal" not in _unique_names(
                inspector, CHECKPOINTS):
            op.create_unique_constraint(
                "uq_screening_checkpoint_ordinal", CHECKPOINTS,
                ["screening_job_id", "view", "batch_ordinal"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if CHECKPOINTS in tables:
        if "uq_screening_checkpoint_ordinal" in _unique_names(inspector,
                                                              CHECKPOINTS):
            op.drop_constraint("uq_screening_checkpoint_ordinal",
                               CHECKPOINTS, type_="unique")
        if "ck_screening_checkpoint_completed_payload" in _check_names(
                inspector, CHECKPOINTS):
            op.drop_constraint("ck_screening_checkpoint_completed_payload",
                               CHECKPOINTS, type_="check")
            op.create_check_constraint(
                "ck_screening_checkpoint_completed_payload", CHECKPOINTS,
                "(state = 'completed' AND payload_sha256 IS NOT NULL) OR "
                "state <> 'completed'")
    inspector = sa.inspect(bind)
    if RESERVATIONS in tables:
        if "ck_ai_cost_reservation_actual_le_ceiling" in _check_names(
                inspector, RESERVATIONS):
            op.drop_constraint("ck_ai_cost_reservation_actual_le_ceiling",
                               RESERVATIONS, type_="check")
        # Codex 6. tur notu: downgrade simetrisi — state_transition CHECK'i
        # 001'deki BİÇİMİNE geri döner (settled_at zorunluluğu olmadan)
        if "ck_ai_cost_reservation_state_transition" in _check_names(
                sa.inspect(bind), RESERVATIONS):
            op.drop_constraint("ck_ai_cost_reservation_state_transition",
                               RESERVATIONS, type_="check")
        op.create_check_constraint(
            "ck_ai_cost_reservation_state_transition", RESERVATIONS,
            "(state = 'reserved' AND actual_usd IS NULL AND "
            " settled_at IS NULL) OR "
            "(state IN ('settled', 'ceiling_charged') AND "
            " actual_usd IS NOT NULL)")
    inspector = sa.inspect(bind)
    if SELECTIONS in tables:
        names = _fk_names(inspector, SELECTIONS)
        if "fk_candidate_selection_attempt_run" in names:
            op.drop_constraint("fk_candidate_selection_attempt_run",
                               SELECTIONS, type_="foreignkey")
            op.create_foreign_key(
                None, SELECTIONS, ATTEMPTS, ["assignment_attempt_id"],
                ["id"], ondelete="CASCADE")
    inspector = sa.inspect(bind)
    if ATTEMPTS in tables:
        if "uq_assignment_attempt_id_run" in _unique_names(inspector,
                                                           ATTEMPTS):
            op.drop_constraint("uq_assignment_attempt_id_run", ATTEMPTS,
                               type_="unique")
        if "fk_assignment_attempt_screening_job" in _fk_names(inspector,
                                                              ATTEMPTS):
            op.drop_constraint("fk_assignment_attempt_screening_job",
                               ATTEMPTS, type_="foreignkey")
