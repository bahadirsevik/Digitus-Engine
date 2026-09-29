"""Corpus screening üretim veri modeli (plan_corpus_screening_production §5).

Altı yeni tablo:
  channel_assignment_attempts        — assignment audit + orkestrasyon CAS
  corpus_screening_jobs              — tarama işi (reuse kimliği burada)
  corpus_screening_decisions         — modelin immutable çıktısı
  corpus_screening_batch_checkpoints — batch resume (tekrar ücret YOK)
  corpus_candidate_selections        — materyalizasyon audit'i
  ai_cost_reservations               — kalıcı hard-cap ledger'ı (Numeric)

Kolon ekleri: channel_candidates (kaynak atfı), scoring_runs (screening
tercihi + havuz künyesi), export_jobs (uygulanan havuz künyesi).

Codex kısıtları: para alanları Numeric; materyalizasyon unique anahtarı
run ID içerir; screening reuse ve materyalizasyon kimlikleri AYRI
indekslenir; reservation CAS geçişleri CHECK ile sınırlıdır.

Backfill YOK: mevcut run'lar `screening_preference='off'` ve NULL künye
ile aynen çalışır (flag-off yolu bit-bit korunur).

İdempotent kalıp: baseline squash canlı `create_all` kullandığından her
tablo/kolon/index varlık-kontrollü eklenir (repo kuralı; örnek
20260723_001).

Revision ID: 20260730_001
Revises: 20260723_001
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_001"
down_revision: Union[str, None] = "20260723_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(inspector) -> set:
    return set(inspector.get_table_names())


def _columns(inspector, table: str) -> set:
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    # ── 1) channel_assignment_attempts ───────────────────────────────
    if "channel_assignment_attempts" not in tables:
        op.create_table(
            "channel_assignment_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("parent_task_id", sa.String(64), nullable=False),
            sa.Column("scoring_run_id", sa.Integer(), nullable=False),
            sa.Column("brand_profile_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False,
                      server_default="pending"),
            sa.Column("phase", sa.String(20), nullable=False,
                      server_default="screening"),
            sa.Column("screening_mode", sa.String(20), nullable=False,
                      server_default="off"),
            sa.Column("applied_candidate_multiplier", sa.Integer(),
                      nullable=False, server_default="1"),
            sa.Column("counterfactual_target_multiplier", sa.Integer(),
                      nullable=False, server_default="1"),
            sa.Column("relevance_coefficient", sa.Numeric(4, 2),
                      nullable=True),
            sa.Column("applied_screening_channels", sa.JSON(), nullable=True),
            sa.Column("preflight_sha256", sa.String(64), nullable=True),
            sa.Column("approved_screening_cap_usd", sa.Numeric(12, 6),
                      nullable=True),
            sa.Column("approved_downstream_cap_usd", sa.Numeric(12, 6),
                      nullable=True),
            sa.Column("requested_policy_version", sa.Integer(), nullable=True),
            sa.Column("requested_anchor_version", sa.Integer(), nullable=True),
            sa.Column("requested_strategy_version", sa.Integer(),
                      nullable=True),
            sa.Column("requested_assignment_version", sa.Integer(),
                      nullable=True),
            sa.Column("context_sha256", sa.String(64), nullable=True),
            sa.Column("channel_rank_snapshot_sha256", sa.String(64),
                      nullable=True),
            sa.Column("relevance_rows_sha256", sa.String(64), nullable=True),
            sa.Column("materialization_identity_sha256", sa.String(64),
                      nullable=True),
            sa.Column("screening_job_id", sa.Integer(), nullable=True),
            sa.Column("assignment_task_id", sa.String(64), nullable=True),
            sa.Column("assignment_dispatch_state", sa.String(20),
                      nullable=False, server_default="pending"),
            sa.Column("dispatch_attempts", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("dispatch_last_at", sa.DateTime(timezone=True),
                      nullable=True),
            sa.Column("manifest", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True),
                      nullable=True),
            sa.ForeignKeyConstraint(["scoring_run_id"], ["scoring_runs.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["brand_profile_id"],
                                    ["brand_profiles.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("parent_task_id",
                                name="uq_assignment_attempt_parent_task"),
            sa.CheckConstraint(
                "status IN ('pending', 'running', 'completed', 'failed')",
                name="ck_assignment_attempt_status"),
            sa.CheckConstraint(
                "phase IN ('screening', 'assignment', 'completed')",
                name="ck_assignment_attempt_phase"),
            sa.CheckConstraint(
                "screening_mode IN ('off', 'shadow', 'assistive')",
                name="ck_assignment_attempt_mode"),
            sa.CheckConstraint(
                "assignment_dispatch_state IN "
                "('pending', 'sent', 'started', 'finished')",
                name="ck_assignment_attempt_dispatch_state"),
            sa.CheckConstraint(
                "(screening_mode = 'off' AND applied_candidate_multiplier = 1 "
                " AND counterfactual_target_multiplier = 1) OR "
                "(screening_mode = 'shadow' AND "
                " applied_candidate_multiplier = 1 AND "
                " counterfactual_target_multiplier = 3) OR "
                "(screening_mode = 'assistive' AND "
                " applied_candidate_multiplier = 3 AND "
                " counterfactual_target_multiplier = 3)",
                name="ck_assignment_attempt_multiplier_matrix"),
            sa.CheckConstraint(
                "approved_screening_cap_usd IS NULL OR "
                "approved_screening_cap_usd > 0",
                name="ck_assignment_attempt_screening_cap_positive"),
            sa.CheckConstraint(
                "approved_downstream_cap_usd IS NULL OR "
                "approved_downstream_cap_usd > 0",
                name="ck_assignment_attempt_downstream_cap_positive"),
        )

    # ── 2) corpus_screening_jobs ─────────────────────────────────────
    if "corpus_screening_jobs" not in tables:
        op.create_table(
            "corpus_screening_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_id", sa.String(64), nullable=True),
            sa.Column("scoring_run_id", sa.Integer(), nullable=False),
            sa.Column("brand_profile_id", sa.Integer(), nullable=False),
            sa.Column("created_by_parent_task_id", sa.String(64),
                      nullable=True),
            sa.Column("status", sa.String(20), nullable=False,
                      server_default="pending"),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("model", sa.String(100), nullable=False),
            sa.Column("prompt_version", sa.String(40), nullable=False),
            sa.Column("temperature", sa.Numeric(4, 2), nullable=False),
            sa.Column("batch_size", sa.Integer(), nullable=False),
            sa.Column("view_salts", sa.JSON(), nullable=False),
            sa.Column("applied_screening_channels", sa.JSON(),
                      nullable=False),
            sa.Column("screening_input_identity_sha256", sa.String(64),
                      nullable=False),
            sa.Column("universe_sha256", sa.String(64), nullable=False),
            sa.Column("context_sha256", sa.String(64), nullable=False),
            sa.Column("input_snapshot", sa.JSON(), nullable=False),
            sa.Column("screening_context", sa.JSON(), nullable=False),
            sa.Column("runner_contract", sa.JSON(), nullable=True),
            sa.Column("strategy_fingerprint", sa.String(128), nullable=True),
            sa.Column("dispatch_policy_version", sa.Integer(), nullable=True),
            sa.Column("dispatch_anchor_version", sa.Integer(), nullable=True),
            sa.Column("dispatch_strategy_version", sa.Integer(),
                      nullable=True),
            sa.Column("planned_requests", sa.Integer(), nullable=True),
            sa.Column("actual_requests", sa.Integer(), nullable=True),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("ceiling_charges", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("coverage_resolved", sa.Integer(), nullable=True),
            sa.Column("unresolved_count", sa.Integer(), nullable=True),
            sa.Column("contract_violations", sa.Integer(), nullable=True),
            sa.Column("decisions_available", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True),
                      nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True),
                      nullable=True),
            sa.ForeignKeyConstraint(["scoring_run_id"], ["scoring_runs.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["brand_profile_id"],
                                    ["brand_profiles.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("task_id", name="uq_screening_job_task"),
            sa.CheckConstraint(
                "status IN ('pending', 'running', 'completed', 'failed', "
                "'fallback', 'not_needed')",
                name="ck_screening_job_status"),
            sa.CheckConstraint("provider IN ('gemini', 'deepseek')",
                               name="ck_screening_job_provider"),
            sa.CheckConstraint("batch_size >= 1",
                               name="ck_screening_job_batch_size"),
            sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                               name="ck_screening_job_cost_nonneg"),
        )

    # ── 3) corpus_screening_decisions ────────────────────────────────
    if "corpus_screening_decisions" not in tables:
        op.create_table(
            "corpus_screening_decisions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("screening_job_id", sa.Integer(), nullable=False),
            sa.Column("keyword_score_id", sa.Integer(), nullable=False),
            sa.Column("keyword_id", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(20), nullable=False),
            sa.Column("view_a_fit", sa.SmallInteger(), nullable=True),
            sa.Column("view_a_reason", sa.String(60), nullable=True),
            sa.Column("view_a_unresolved", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("view_b_fit", sa.SmallInteger(), nullable=True),
            sa.Column("view_b_reason", sa.String(60), nullable=True),
            sa.Column("view_b_unresolved", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("merged_fit", sa.Numeric(6, 4), nullable=True),
            sa.Column("passing", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
            sa.Column("disagreement", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("uncertain", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("contract_violations", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["screening_job_id"],
                                    ["corpus_screening_jobs.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["keyword_score_id"],
                                    ["keyword_scores.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("screening_job_id", "keyword_score_id",
                                "channel", name="uq_screening_decision"),
            sa.CheckConstraint("channel IN ('ADS', 'SEO', 'SOCIAL')",
                               name="ck_screening_decision_channel"),
        )

    # ── 4) corpus_screening_batch_checkpoints ────────────────────────
    if "corpus_screening_batch_checkpoints" not in tables:
        op.create_table(
            "corpus_screening_batch_checkpoints",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("screening_job_id", sa.Integer(), nullable=False),
            sa.Column("view", sa.String(40), nullable=False),
            sa.Column("batch_ordinal", sa.Integer(), nullable=False),
            sa.Column("batch_hash", sa.String(64), nullable=False),
            sa.Column("request_contract_sha256", sa.String(64),
                      nullable=False),
            sa.Column("state", sa.String(20), nullable=False,
                      server_default="pending"),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("payload_sha256", sa.String(64), nullable=True),
            sa.Column("logical_request_id", sa.String(64), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("usage", sa.JSON(), nullable=True),
            sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True),
                      nullable=True),
            sa.ForeignKeyConstraint(["screening_job_id"],
                                    ["corpus_screening_jobs.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("screening_job_id", "view", "batch_hash",
                                name="uq_screening_checkpoint"),
            sa.CheckConstraint(
                "state IN ('pending', 'completed', 'failed')",
                name="ck_screening_checkpoint_state"),
            sa.CheckConstraint(
                "(state = 'completed' AND payload_sha256 IS NOT NULL) OR "
                "state <> 'completed'",
                name="ck_screening_checkpoint_completed_payload"),
            sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0",
                               name="ck_screening_checkpoint_cost_nonneg"),
        )

    # ── 5) corpus_candidate_selections ───────────────────────────────
    if "corpus_candidate_selections" not in tables:
        op.create_table(
            "corpus_candidate_selections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("assignment_attempt_id", sa.Integer(), nullable=False),
            sa.Column("scoring_run_id", sa.Integer(), nullable=False),
            sa.Column("screening_job_id", sa.Integer(), nullable=True),
            sa.Column("keyword_id", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(20), nullable=False),
            sa.Column("origin_source", sa.String(20), nullable=False),
            sa.Column("materialization_action", sa.String(20),
                      nullable=False),
            sa.Column("materialization_identity_sha256", sa.String(64),
                      nullable=True),
            sa.Column("baseline_rank", sa.Integer(), nullable=True),
            sa.Column("screening_rank", sa.Integer(), nullable=True),
            sa.Column("initial_materialized_rank", sa.Integer(),
                      nullable=True),
            sa.Column("screening_fit", sa.Numeric(6, 4), nullable=True),
            sa.Column("relevance_score", sa.Numeric(6, 4), nullable=True),
            sa.Column("adjusted_score", sa.Numeric(15, 4), nullable=True),
            sa.Column("active_channel", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
            sa.Column("capacity", sa.Integer(), nullable=True),
            sa.Column("b_initial", sa.Integer(), nullable=True),
            sa.Column("t_target", sa.Integer(), nullable=True),
            sa.Column("u_union_size", sa.Integer(), nullable=True),
            sa.Column("is_initial_set", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
            sa.Column("is_applied", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["assignment_attempt_id"],
                                    ["channel_assignment_attempts.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["scoring_run_id"], ["scoring_runs.id"],
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["screening_job_id"],
                                    ["corpus_screening_jobs.id"],
                                    ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["keyword_id"], ["keywords.id"],
                                    ondelete="CASCADE"),
            # Codex: unique anahtar run ID İÇERİR
            sa.UniqueConstraint("scoring_run_id", "assignment_attempt_id",
                                "keyword_id", "channel",
                                "materialization_action",
                                name="uq_candidate_selection"),
            sa.CheckConstraint("channel IN ('ADS', 'SEO', 'SOCIAL')",
                               name="ck_candidate_selection_channel"),
            sa.CheckConstraint(
                "origin_source IN ('baseline', 'screening', 'both', 'none')",
                name="ck_candidate_selection_origin"),
            sa.CheckConstraint(
                "materialization_action IN "
                "('initial', 'transfer', 'expansion')",
                name="ck_candidate_selection_action"),
        )

    # ── 6) ai_cost_reservations ──────────────────────────────────────
    if "ai_cost_reservations" not in tables:
        op.create_table(
            "ai_cost_reservations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("budget_owner_attempt_id", sa.Integer(),
                      nullable=False),
            sa.Column("budget_kind", sa.String(20), nullable=False),
            sa.Column("request_id", sa.String(64), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("stage", sa.String(60), nullable=True),
            sa.Column("provider", sa.String(20), nullable=True),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("ceiling_usd", sa.Numeric(12, 6), nullable=False),
            sa.Column("actual_usd", sa.Numeric(12, 6), nullable=True),
            sa.Column("state", sa.String(20), nullable=False,
                      server_default="reserved"),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
            sa.Column("settled_at", sa.DateTime(timezone=True),
                      nullable=True),
            sa.ForeignKeyConstraint(["budget_owner_attempt_id"],
                                    ["channel_assignment_attempts.id"],
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("budget_owner_attempt_id", "budget_kind",
                                "request_id", "attempt",
                                name="uq_ai_cost_reservation"),
            sa.CheckConstraint("budget_kind IN ('screening', 'downstream')",
                               name="ck_ai_cost_reservation_kind"),
            sa.CheckConstraint(
                "state IN ('reserved', 'settled', 'ceiling_charged')",
                name="ck_ai_cost_reservation_state"),
            sa.CheckConstraint("ceiling_usd >= 0",
                               name="ck_ai_cost_reservation_ceiling_nonneg"),
            sa.CheckConstraint("actual_usd IS NULL OR actual_usd >= 0",
                               name="ck_ai_cost_reservation_actual_nonneg"),
            sa.CheckConstraint(
                "(state = 'reserved' AND actual_usd IS NULL AND "
                " settled_at IS NULL) OR "
                "(state IN ('settled', 'ceiling_charged') AND "
                " actual_usd IS NOT NULL)",
                name="ck_ai_cost_reservation_state_transition"),
            sa.CheckConstraint(
                "state <> 'ceiling_charged' OR actual_usd = ceiling_usd",
                name="ck_ai_cost_reservation_ceiling_charge_amount"),
        )

    # ── İndeksler (partial unique'ler dahil) ─────────────────────────
    inspector = sa.inspect(bind)
    index_specs = [
        ("channel_assignment_attempts", "idx_assignment_attempt_run",
         ["scoring_run_id", "status"], False, None),
        ("channel_assignment_attempts", "idx_assignment_attempt_workspace",
         ["brand_profile_id", "created_at"], False, None),
        ("channel_assignment_attempts",
         "idx_assignment_attempt_materialization",
         ["materialization_identity_sha256"], False, None),
        ("channel_assignment_attempts", "uq_assignment_attempt_active_run",
         ["scoring_run_id"], True, "status IN ('pending', 'running')"),
        ("corpus_screening_jobs", "uq_screening_job_active_identity",
         ["screening_input_identity_sha256"], True,
         "status IN ('pending', 'running')"),
        ("corpus_screening_jobs", "uq_screening_job_completed_identity",
         ["screening_input_identity_sha256"], True, "status = 'completed'"),
        ("corpus_screening_jobs", "idx_screening_job_run_status",
         ["scoring_run_id", "status"], False, None),
        ("corpus_screening_jobs", "idx_screening_job_workspace",
         ["brand_profile_id", "created_at"], False, None),
        ("corpus_screening_decisions", "idx_screening_decision_job_channel",
         ["screening_job_id", "channel"], False, None),
        ("corpus_screening_batch_checkpoints",
         "idx_screening_checkpoint_job_state",
         ["screening_job_id", "state"], False, None),
        ("corpus_candidate_selections", "idx_candidate_selection_run_channel",
         ["scoring_run_id", "channel", "is_applied"], False, None),
        ("corpus_candidate_selections", "idx_candidate_selection_attempt",
         ["assignment_attempt_id"], False, None),
        ("corpus_candidate_selections",
         "idx_candidate_selection_materialization",
         ["materialization_identity_sha256"], False, None),
        ("ai_cost_reservations", "idx_ai_cost_reservation_owner",
         ["budget_owner_attempt_id", "budget_kind", "state"], False, None),
    ]
    for table, name, cols, unique, where in index_specs:
        if name in _indexes(inspector, table):
            continue
        kwargs = {"unique": unique}
        if where:
            kwargs["postgresql_where"] = sa.text(where)
        op.create_index(name, table, cols, **kwargs)

    # ── Kolon ekleri (varlık kontrollü) ──────────────────────────────
    cc_cols = _columns(inspector, "channel_candidates")
    if "screening_job_id" not in cc_cols:
        op.add_column("channel_candidates",
                      sa.Column("screening_job_id", sa.Integer(),
                                nullable=True))
        op.create_foreign_key(
            "fk_channel_candidates_screening_job", "channel_candidates",
            "corpus_screening_jobs", ["screening_job_id"], ["id"],
            ondelete="SET NULL")
    if "candidate_origin_source" not in cc_cols:
        op.add_column("channel_candidates",
                      sa.Column("candidate_origin_source", sa.String(20),
                                nullable=True))
    if "candidate_materialization_action" not in cc_cols:
        op.add_column("channel_candidates",
                      sa.Column("candidate_materialization_action",
                                sa.String(20), nullable=True))
    if "screening_fit" not in cc_cols:
        op.add_column("channel_candidates",
                      sa.Column("screening_fit", sa.Numeric(6, 4),
                                nullable=True))
    if "screening_rank" not in cc_cols:
        op.add_column("channel_candidates",
                      sa.Column("screening_rank", sa.Integer(),
                                nullable=True))
    if "idx_channel_candidates_screening_job" not in _indexes(
            sa.inspect(bind), "channel_candidates"):
        op.create_index("idx_channel_candidates_screening_job",
                        "channel_candidates", ["screening_job_id"])
    cc_checks = {c["name"] for c
                 in sa.inspect(bind).get_check_constraints(
                     "channel_candidates")}
    if "ck_channel_candidates_origin_source" not in cc_checks:
        op.create_check_constraint(
            "ck_channel_candidates_origin_source", "channel_candidates",
            "candidate_origin_source IS NULL OR candidate_origin_source IN "
            "('baseline', 'screening', 'both', 'none')")
    if "ck_channel_candidates_materialization_action" not in cc_checks:
        op.create_check_constraint(
            "ck_channel_candidates_materialization_action",
            "channel_candidates",
            "candidate_materialization_action IS NULL OR "
            "candidate_materialization_action IN "
            "('initial', 'transfer', 'expansion')")

    run_cols = _columns(sa.inspect(bind), "scoring_runs")
    if "screening_preference" not in run_cols:
        op.add_column("scoring_runs",
                      sa.Column("screening_preference", sa.String(20),
                                nullable=False, server_default="off"))
    if "channel_pool_screening_mode" not in run_cols:
        op.add_column("scoring_runs",
                      sa.Column("channel_pool_screening_mode", sa.String(20),
                                nullable=True))
    if "channel_pool_screening_context_sha256" not in run_cols:
        op.add_column("scoring_runs",
                      sa.Column("channel_pool_screening_context_sha256",
                                sa.String(64), nullable=True))

    export_cols = _columns(sa.inspect(bind), "export_jobs")
    if "pool_screening_mode" not in export_cols:
        op.add_column("export_jobs",
                      sa.Column("pool_screening_mode", sa.String(20),
                                nullable=True))
    if "pool_screening_context_sha256" not in export_cols:
        op.add_column("export_jobs",
                      sa.Column("pool_screening_context_sha256",
                                sa.String(64), nullable=True))


def downgrade() -> None:
    """Geri alma: yeni tablolar/kolonlar düşer.

    Audit kayıtları silinmiş olur — üretimde rollback yolu FLAG kapatmaktır
    (plan §14), downgrade yalnız geliştirme/test içindir.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, col in (("export_jobs", "pool_screening_context_sha256"),
                       ("export_jobs", "pool_screening_mode"),
                       ("scoring_runs",
                        "channel_pool_screening_context_sha256"),
                       ("scoring_runs", "channel_pool_screening_mode"),
                       ("scoring_runs", "screening_preference"),
                       ("channel_candidates", "screening_rank"),
                       ("channel_candidates", "screening_fit"),
                       ("channel_candidates",
                        "candidate_materialization_action"),
                       ("channel_candidates", "candidate_origin_source"),
                       ("channel_candidates", "screening_job_id")):
        if col in _columns(sa.inspect(bind), table):
            op.drop_column(table, col)

    # FK bağımlılık sırası: attempts -> jobs referansı taşıdığı için
    # attempts, jobs'tan ÖNCE düşer (aksi halde DependentObjectsStillExist)
    for table in ("ai_cost_reservations", "corpus_candidate_selections",
                  "corpus_screening_batch_checkpoints",
                  "corpus_screening_decisions",
                  "channel_assignment_attempts",
                  "corpus_screening_jobs"):
        if table in _tables(sa.inspect(bind)):
            op.drop_table(table)
