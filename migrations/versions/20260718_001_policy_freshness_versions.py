"""Policy/anchor freshness sürümleri + kalıcı rakip URL kararları (plan v13).

- brand_profiles: competitor_url_decisions (JSON), policy_version, anchor_version
- scoring_runs: channel_pool_policy_version, relevance_anchor_version
- task_results: brand_profile_id (nullable FK — run'suz preview task sahipliği)
- export_jobs: requested_policy_version, requested_anchor_version
- Veri geçişi 1: approved topic_policy.excluded_terms → excluded_info birleşimi
  (excluded_info bundan sonra konu dışlamalarının TEK kullanıcı kaynağıdır)
- Veri geçişi 2: channel_assigned/completed + ChannelPool'u olan run'lar için
  execution_manifest.policy_snapshot mevcut etkin politikayla EŞLEŞİYORSA
  channel_pool_policy_version=1 backfill'i; eşleşmeyen/snapshot'sız run NULL
  kalır (= policy_stale, güvenli taraf — run-18 önceliği).
- relevance_anchor_version backfill YOK (NULL): mevcut KeywordRelevance
  satırlarının bugünkü anchor'larla üretildiği kanıtlanamaz; run ilk yeniden
  atamada bir kez relevance hesaplar.

Migration bağımsızlığı: uygulama servisleri import EDİLMEZ — normalizasyon ve
etkin-politika karşılaştırmasının minimal sürümü burada sabitlenmiştir.

Revision ID: 20260718_001
Revises: 20260717_003
Create Date: 2026-07-18
"""
from __future__ import annotations

import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_001"
down_revision: Union[str, None] = "20260717_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Migration'a sabitlenmiş minimal normalizasyon (uygulamadan bağımsız) ──

_TURKISH_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def _norm(text: str) -> str:
    text = (text or "").translate(_TURKISH_MAP).lower().strip()
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_excluded_info(value) -> list:
    if not value:
        return []
    parts = re.split(r"[,\n]+", str(value))
    seen, out = set(), []
    for item in parts:
        text = item.strip()
        key = _norm(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _approved_terms(entries) -> list:
    return sorted(
        e.get("term", "")
        for e in (entries or [])
        if isinstance(e, dict) and e.get("status") == "approved" and e.get("term")
    )


def _channel_policy(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        ch: ("allow" if raw.get(ch) == "allow" else "block")
        for ch in ("ads", "seo", "social")
    }


def _current_snapshot(profile_row) -> dict:
    """assignment_dispatcher._policy_snapshot ile AYNI şekil (migration kopyası)."""
    profile_data = profile_row["profile_data"] or {}
    topic = profile_row["topic_policy"] or {}
    topic_terms = list(topic.get("excluded_terms") or []) + list(
        topic.get("excluded_aliases") or []
    )
    return {
        "profile_exclude_themes": sorted(profile_data.get("exclude_themes") or []),
        "competitor_terms_approved": _approved_terms(profile_row["competitor_terms"]),
        "competitor_channel_policy": _channel_policy(profile_row["competitor_policy"]),
        "topic_terms_approved": _approved_terms(topic_terms),
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    bp_cols = {c["name"] for c in inspector.get_columns("brand_profiles")}
    if "competitor_url_decisions" not in bp_cols:
        op.add_column("brand_profiles", sa.Column("competitor_url_decisions", sa.JSON(), nullable=True))
    if "policy_version" not in bp_cols:
        op.add_column(
            "brand_profiles",
            sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
        )
    if "anchor_version" not in bp_cols:
        op.add_column(
            "brand_profiles",
            sa.Column("anchor_version", sa.Integer(), nullable=False, server_default="1"),
        )

    run_cols = {c["name"] for c in inspector.get_columns("scoring_runs")}
    if "channel_pool_policy_version" not in run_cols:
        op.add_column("scoring_runs", sa.Column("channel_pool_policy_version", sa.Integer(), nullable=True))
    if "relevance_anchor_version" not in run_cols:
        op.add_column("scoring_runs", sa.Column("relevance_anchor_version", sa.Integer(), nullable=True))

    tr_cols = {c["name"] for c in inspector.get_columns("task_results")}
    if "brand_profile_id" not in tr_cols:
        op.add_column(
            "task_results",
            sa.Column(
                "brand_profile_id",
                sa.Integer(),
                sa.ForeignKey("brand_profiles.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    ej_cols = {c["name"] for c in inspector.get_columns("export_jobs")}
    if "requested_policy_version" not in ej_cols:
        op.add_column("export_jobs", sa.Column("requested_policy_version", sa.Integer(), nullable=True))
    if "requested_anchor_version" not in ej_cols:
        op.add_column("export_jobs", sa.Column("requested_anchor_version", sa.Integer(), nullable=True))

    # ── Veri geçişi 1: approved topic terimleri → excluded_info (idempotent) ──
    profiles = bind.execute(sa.text(
        "SELECT id, excluded_info, topic_policy FROM brand_profiles"
    )).mappings().all()
    for row in profiles:
        topic = row["topic_policy"]
        if isinstance(topic, str):
            try:
                topic = json.loads(topic)
            except Exception:
                topic = None
        if not isinstance(topic, dict):
            continue
        approved = [
            e.get("term", "").strip()
            for e in (topic.get("excluded_terms") or [])
            if isinstance(e, dict) and e.get("status") == "approved" and e.get("term")
        ]
        if not approved:
            continue
        existing_terms = _parse_excluded_info(row["excluded_info"])
        seen_keys = {_norm(t) for t in existing_terms}
        missing = []
        for term in approved:
            key = _norm(term)
            # seen_keys ile İLERLEYEREK dedup: approved listesi kendi içinde
            # tekrar taşıyorsa (Codex bulgusu) yalnız ilki eklenir
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            missing.append(term)
        if not missing:
            continue  # idempotent: tekrar koşuda değişiklik üretmez
        merged = ", ".join(existing_terms + missing)
        bind.execute(
            sa.text("UPDATE brand_profiles SET excluded_info = :v WHERE id = :id"),
            {"v": merged, "id": row["id"]},
        )

    # ── Veri geçişi 2: snapshot-doğrulamalı channel_pool_policy_version backfill ──
    runs = bind.execute(sa.text(
        "SELECT r.id, r.brand_profile_id, r.execution_manifest "
        "FROM scoring_runs r "
        "WHERE r.status IN ('channel_assigned', 'completed') "
        "AND r.channel_pool_policy_version IS NULL "
        "AND r.brand_profile_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM channel_pools p WHERE p.scoring_run_id = r.id)"
    )).mappings().all()
    profile_cache: dict = {}
    for run in runs:
        manifest = run["execution_manifest"]
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except Exception:
                manifest = None
        snapshot = (manifest or {}).get("policy_snapshot") if isinstance(manifest, dict) else None
        if not isinstance(snapshot, dict) or not snapshot:
            continue  # snapshot yok → NULL kalır (stale, güvenli taraf)
        wp_id = run["brand_profile_id"]
        if wp_id not in profile_cache:
            prow = bind.execute(sa.text(
                "SELECT profile_data, competitor_terms, competitor_policy, topic_policy "
                "FROM brand_profiles WHERE id = :id"
            ), {"id": wp_id}).mappings().first()
            if prow is None:
                profile_cache[wp_id] = None
            else:
                prow = dict(prow)
                for key in ("profile_data", "competitor_terms", "competitor_policy", "topic_policy"):
                    if isinstance(prow.get(key), str):
                        try:
                            prow[key] = json.loads(prow[key])
                        except Exception:
                            prow[key] = None
                profile_cache[wp_id] = _current_snapshot(prow)
        current = profile_cache[wp_id]
        if current is None:
            continue
        if snapshot == current:
            bind.execute(
                sa.text(
                    "UPDATE scoring_runs SET channel_pool_policy_version = 1 WHERE id = :id"
                ),
                {"id": run["id"]},
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    ej_cols = {c["name"] for c in inspector.get_columns("export_jobs")}
    for col in ("requested_anchor_version", "requested_policy_version"):
        if col in ej_cols:
            op.drop_column("export_jobs", col)

    tr_cols = {c["name"] for c in inspector.get_columns("task_results")}
    if "brand_profile_id" in tr_cols:
        op.drop_column("task_results", "brand_profile_id")

    run_cols = {c["name"] for c in inspector.get_columns("scoring_runs")}
    for col in ("relevance_anchor_version", "channel_pool_policy_version"):
        if col in run_cols:
            op.drop_column("scoring_runs", col)

    bp_cols = {c["name"] for c in inspector.get_columns("brand_profiles")}
    for col in ("anchor_version", "policy_version", "competitor_url_decisions"):
        if col in bp_cols:
            op.drop_column("brand_profiles", col)
