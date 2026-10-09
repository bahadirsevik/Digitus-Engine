"""Relevance okuma kapisi — tek kapi ve simetri (plan_marka_profili_sadakati.md P1.3/P1.4).

P1.4'ten once `KeywordRelevance` satirlari IKI FARKLI sozlesmeyle okunuyordu:

- `pool_builder._load_relevance_map`            : bayrak + confirmed + deleted_at
- `channel_engine._load_relevance_map_for_pooling` : HICBIR KAPI YOK

Ikincisi FINAL SECIMI besliyordu (adjusted = max(base,0) * relevance * coef),
yani ZAYIF kapiya sahip yol AGIR sonuca sahipti. Bu dosyanin cekirdek iddiasi
SIMETRI'dir: iki okuyucu her kosulda AYNI map'i dondurmeli.

Kapi sozlesmesi (freshness.relevance_stale kuralinin duali):
  bayrak + run var + skip_relevance false + profil confirmed/silinmemis +
  run.relevance_anchor_version == workspace.anchor_version
"""
import pytest

from app.core.channel.channel_engine import ChannelEngine
from app.core.channel.pool_builder import PoolBuilder
from app.core.relevance import evaluate_relevance_gate, load_effective_relevance_map
from app.database.models import KeywordRelevance, ScoringRun

CONFIRMED_PROFILE = {
    "company_name": "Digitus",
    "products": ["SEO"],
    "anchor_texts": ["dijital pazarlama ajansi"],
}


@pytest.fixture
def gated_run(db_session, make_workspace, make_keyword, make_scoring_run):
    """Kapinin ACIK oldugu temel kurulum; testler tek tek bozar.

    workspace.anchor_version = 3 ve run.relevance_anchor_version = 3 →
    surum ekseni acikca hizali.
    """
    workspace = make_workspace(
        name="Gate WS",
        status="confirmed",
        profile_data=dict(CONFIRMED_PROFILE),
        anchor_version=3,
    )
    run = make_scoring_run(
        brand_profile_id=workspace.id, relevance_anchor_version=3,
    )
    keyword = make_keyword("seo ajansi", brand_profile_id=workspace.id)
    db_session.add(KeywordRelevance(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        relevance_score=0.842,
        matched_anchor="dijital pazarlama ajansi",
        method="embedding",
    ))
    db_session.commit()
    return workspace, run, keyword


def _both_readers(db_session, run_id):
    """(pool_builder map, channel_engine map) — P1.4 simetri olcumu."""
    return (
        PoolBuilder(db_session)._load_relevance_map(run_id),
        ChannelEngine(db_session, None)._load_relevance_map_for_pooling(run_id),
    )


def _assert_symmetric_and_empty(db_session, run_id):
    pool_map, engine_map = _both_readers(db_session, run_id)
    assert pool_map == engine_map == {}


# ── Kapi ACIK ───────────────────────────────────────────────────────────────


def test_gate_open_applies_relevance_in_both_readers(db_session, gated_run):
    _, run, keyword = gated_run

    assert evaluate_relevance_gate(db_session, run).applied is True
    pool_map, engine_map = _both_readers(db_session, run.id)

    assert pool_map == engine_map
    assert engine_map[keyword.id] == pytest.approx(0.842)


# ── Kapi KAPALI: her kosul ayri ayri ────────────────────────────────────────


def test_skip_relevance_run_ignores_existing_rows(db_session, gated_run):
    """skip_relevance=true kosuda relevance satiri VARSA bile uygulanmaz.

    Bu kol P1.4 oncesi yalnizca channel_engine tarafinda aciklikti: kullanici
    ilgi skorunu acikca atladigi halde final siralama eski satirlarla kuruluyordu.
    """
    _, run, _ = gated_run
    run.skip_relevance = True
    db_session.commit()

    assert evaluate_relevance_gate(db_session, run).reason == "skip_relevance"
    _assert_symmetric_and_empty(db_session, run.id)


def test_draft_profile_blocks_relevance(db_session, gated_run):
    workspace, run, _ = gated_run
    workspace.status = "draft"
    db_session.commit()

    assert (
        evaluate_relevance_gate(db_session, run).reason == "profile_not_confirmed"
    )
    _assert_symmetric_and_empty(db_session, run.id)


def test_archived_workspace_blocks_relevance(db_session, gated_run):
    from datetime import datetime, timezone

    workspace, run, _ = gated_run
    workspace.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    assert (
        evaluate_relevance_gate(db_session, run).reason == "profile_not_confirmed"
    )
    _assert_symmetric_and_empty(db_session, run.id)


def test_stale_anchor_version_blocks_relevance(db_session, gated_run):
    """Profil degisti, relevance yeniden hesaplanmadi → eski skorlar UYGULANMAZ.

    P1.4 oncesi bu satirlar final siralamaya giriyordu; pool_builder da surumu
    kontrol etmiyordu.
    """
    workspace, run, _ = gated_run
    workspace.anchor_version = 7  # profil degisti
    db_session.commit()

    assert (
        evaluate_relevance_gate(db_session, run).reason == "anchor_version_stale"
    )
    _assert_symmetric_and_empty(db_session, run.id)


def test_missing_anchor_version_blocks_relevance(db_session, gated_run):
    _, run, _ = gated_run
    run.relevance_anchor_version = None
    db_session.commit()

    assert (
        evaluate_relevance_gate(db_session, run).reason == "anchor_version_missing"
    )
    _assert_symmetric_and_empty(db_session, run.id)


def test_disabled_flag_blocks_relevance(db_session, gated_run, monkeypatch):
    from app.config import settings

    _, run, _ = gated_run
    monkeypatch.setattr(settings, "ENABLE_RELEVANCE_RERANK", False)

    assert evaluate_relevance_gate(db_session, run).reason == "flag_disabled"
    _assert_symmetric_and_empty(db_session, run.id)


def test_unknown_run_returns_empty_map(db_session):
    assert load_effective_relevance_map(db_session, 999_999) == {}


# ── P1.3: tazeleme yolunun onay kapisi ──────────────────────────────────────


def test_channel_assignment_refuses_recompute_for_unconfirmed_profile(
    db_session, make_workspace, make_scoring_run,
):
    """recompute istendi ama profil onayli degil → sessiz devam YOK.

    Eskiden bu sorgu yalniz id ile cekiyordu; onaysiz profille ucretli
    embedding kosup havuzda kullanilmayacak satirlar uretiyordu.
    """
    from app.core.relevance import RelevanceRefreshError

    workspace = make_workspace(
        name="Onaysiz WS",
        status="draft",
        profile_data=dict(CONFIRMED_PROFILE),
        anchor_version=3,
    )
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="relevance_computed",
        relevance_anchor_version=1,
    )

    with pytest.raises(RelevanceRefreshError) as exc_info:
        ChannelEngine(db_session, None).run_channel_assignment(
            run.id,
            recompute_relevance=True,
            requested_anchor_version=3,
        )

    assert "onaylanmis degil" in str(exc_info.value)
    # Run basarisiz isaretlenir (channel_engine disaridaki except blogu)
    db_session.expire_all()
    assert db_session.get(ScoringRun, run.id).status == "failed"
    # Ucretli embedding hic kosmadi → relevance satiri yazilmadi
    assert (
        db_session.query(KeywordRelevance)
        .filter(KeywordRelevance.scoring_run_id == run.id)
        .count() == 0
    )
