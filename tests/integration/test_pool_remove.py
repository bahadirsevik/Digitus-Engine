def _pool(db_session, run_id, keyword_id, channel, rank, **kwargs):
    from app.database.models import ChannelPool

    row = ChannelPool(
        scoring_run_id=run_id,
        keyword_id=keyword_id,
        channel=channel,
        final_rank=rank,
        adjusted_score=kwargs.pop("adjusted_score", 10 - rank),
        relevance_score=kwargs.pop("relevance_score", 0.8),
        is_strategic=kwargs.pop("is_strategic", False),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_delete_pool_item_compacts_only_channel_and_marks_run_content_stale(
    client, db_session, make_workspace, make_scoring_run, make_keyword
):
    from app.database.models import ChannelPool, ContentOutput

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigned")
    other_run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigned")

    kw1 = make_keyword("ads one", brand_profile_id=workspace.id)
    kw2 = make_keyword("ads two", brand_profile_id=workspace.id)
    kw3 = make_keyword("ads three", brand_profile_id=workspace.id)
    kw4 = make_keyword("seo one", brand_profile_id=workspace.id)

    _pool(db_session, run.id, kw1.id, "ADS", 1)
    removed = _pool(db_session, run.id, kw2.id, "ADS", 2, is_strategic=True)
    removed_id = removed.id
    _pool(db_session, run.id, kw3.id, "ADS", 3)
    seo_pool = _pool(db_session, run.id, kw4.id, "SEO", 7)

    stale_target = ContentOutput(
        scoring_run_id=run.id,
        keyword_id=kw1.id,
        channel="ADS",
        content_type="ad_group",
        content_data={"headline": "x"},
        is_stale=False,
    )
    untouched = ContentOutput(
        scoring_run_id=other_run.id,
        keyword_id=kw4.id,
        channel="SEO",
        content_type="blog_post",
        content_data={"title": "y"},
        is_stale=False,
    )
    db_session.add_all([stale_target, untouched])
    db_session.commit()

    resp = client.delete(
        f"/api/v1/channels/runs/{run.id}/pools/{removed_id}",
        params={"brand_profile_id": workspace.id},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["channel"] == "ADS"
    assert data["pool_counts"]["ADS"] == 2
    assert data["content_marked_stale"] == 1

    db_session.expire_all()
    assert db_session.get(ChannelPool, removed_id) is None
    ads_ranks = [
        row.final_rank
        for row in db_session.query(ChannelPool)
        .filter_by(scoring_run_id=run.id, channel="ADS")
        .order_by(ChannelPool.final_rank)
        .all()
    ]
    assert ads_ranks == [1, 2]
    assert db_session.get(ChannelPool, seo_pool.id).final_rank == 7
    assert db_session.get(ContentOutput, stale_target.id).is_stale is True
    assert db_session.get(ContentOutput, untouched.id).is_stale is False


def test_get_pools_exposes_workspace_snapshot_volume(
    client, db_session, make_workspace, make_scoring_run, make_keyword
):
    """Kanal sayfası 'Hacim' kolonu WK snapshot hacmini gösterir (boş '-' kalmaz)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigned")
    kw = make_keyword("hacimli kelime", brand_profile_id=workspace.id, monthly_volume=1250)
    _pool(db_session, run.id, kw.id, "ADS", 1)

    resp = client.get(
        f"/api/v1/channels/runs/{run.id}/pools",
        params={"brand_profile_id": workspace.id},
    )

    assert resp.status_code == 200
    item = resp.json()["channels"]["ADS"][0]
    assert item["volume"] == 1250


def test_export_channel_volume_uses_workspace_snapshot(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Export kanal hacmi, pools API'siyle AYNI kaynaktan (WK snapshot) gelir.

    Codex bulgusu: get_pool global Keyword.monthly_volume okuyordu; UI (WK) ile
    export ayrışabiliyordu. Global kolon kasıtlı farklı bırakılır ve WK kazanır.
    """
    from app.exporters.data_collector import ExportDataCollector

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigned")
    kw = make_keyword("export hacim", brand_profile_id=workspace.id, monthly_volume=1250)
    # Global (legacy) kolonu bilerek farklılaştır — snapshot kazanmalı
    kw.monthly_volume = 5
    db_session.commit()
    _pool(db_session, run.id, kw.id, "SEO", 1)

    channels = ExportDataCollector(db_session)._collect_channels(run.id)

    assert channels.seo.keywords[0].volume == 1250


def test_delete_pool_item_blocks_while_assignment_running(
    client, db_session, make_workspace, make_scoring_run, make_keyword
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigning")
    kw = make_keyword("running pool", brand_profile_id=workspace.id)
    row = _pool(db_session, run.id, kw.id, "SOCIAL", 1)

    resp = client.delete(
        f"/api/v1/channels/runs/{run.id}/pools/{row.id}",
        params={"brand_profile_id": workspace.id},
    )

    assert resp.status_code == 409


def test_delete_pool_item_atomic_rollback_on_staleness_failure(
    client, db_session, make_workspace, make_scoring_run, make_keyword, monkeypatch
):
    """F1-B.1: Pool item deletion, rank compaction, and staleness marking must be in
    the same transaction. If staleness marking raises an error, the transaction must
    roll back cleanly so the ChannelPool item is NOT deleted and ranks are NOT compacted."""
    import pytest
    from app.database.models import ChannelPool

    workspace = make_workspace("Pool Rollback WS")
    run = make_scoring_run(brand_profile_id=workspace.id, status="channel_assigned")

    kw1 = make_keyword("kw 1", brand_profile_id=workspace.id)
    kw2 = make_keyword("kw 2", brand_profile_id=workspace.id)
    kw3 = make_keyword("kw 3", brand_profile_id=workspace.id)

    item1 = _pool(db_session, run.id, kw1.id, "ADS", 1)
    item2 = _pool(db_session, run.id, kw2.id, "ADS", 2)
    item3 = _pool(db_session, run.id, kw3.id, "ADS", 3)
    removed_id = item2.id

    def _failing_stale(db, run_id):
        raise RuntimeError("Simulated staleness failure")

    monkeypatch.setattr("app.api.v1.channels.mark_run_content_stale", _failing_stale)

    with pytest.raises(RuntimeError, match="Simulated staleness failure"):
        client.delete(
            f"/api/v1/channels/runs/{run.id}/pools/{removed_id}",
            params={"brand_profile_id": workspace.id},
        )

    # Roll back the test session to reset any uncommitted local dirty state
    db_session.rollback()
    db_session.expire_all()

    # Verify ChannelPool row is NOT deleted
    pool_item = db_session.get(ChannelPool, removed_id)
    assert pool_item is not None
    assert pool_item.id == removed_id
    assert pool_item.final_rank == 2

    # Verify ranks of remaining items were NOT permanently compacted
    ranks = {
        row.id: row.final_rank
        for row in db_session.query(ChannelPool)
        .filter_by(scoring_run_id=run.id, channel="ADS")
        .all()
    }
    assert ranks == {item1.id: 1, item2.id: 2, item3.id: 3}

