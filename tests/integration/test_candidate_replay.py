# -*- coding: utf-8 -*-
"""Aday-üretim replay çekirdeği testleri (Codex plan denetimi 2. tur #4).

Kapsam: pool-cap/B_initial hesabı, initial/actual bütçe ayrımı, üretim
tie-break paritesi, adjusted top-B ↔ DB ChannelCandidate sıralı eşleşme
kapısı, RRF 1-based rank, aynı-metin/farklı-ID ezilmemesi, eksik etiket
evreni reddi, bozuk relevance kimlik kümesi reddi, girdi hash değişimi,
artifact payload doğrulaması.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.benchmark.candidate_replay import (
    ReplayInputError,
    adjusted_ordering,
    budget_initial,
    build_channel_replay,
    compute_payload_sha,
    load_run_inputs,
    resolve_positive_ids,
    rrf_ordering,
    validate_artifact,
    verify_universe_lock,
)
from app.database.models import (
    ChannelCandidate,
    KeywordRelevance,
    KeywordScore,
    TaskResult,
)

MANIFEST_OK = {"candidate_pool_multiplier": 1, "relevance_enabled": True}


def _mk_universe(db, make_workspace, make_scoring_run, make_keyword,
                 specs, *, manifest=None, algo="v2", status="channel_assigned",
                 with_task=True, ads_capacity=1):
    """specs: [(text, ads_score, ads_rank, relevance), ...]"""
    ws = make_workspace("replay evreni")
    run = make_scoring_run(brand_profile_id=ws.id, status=status,
                           ads_capacity=ads_capacity)
    run.algorithm_version = algo
    run.execution_manifest = manifest if manifest is not None else MANIFEST_OK
    if with_task:
        import uuid

        db.add(TaskResult(
            task_id=str(uuid.uuid4()), task_type="channel_assignment",
            scoring_run_id=run.id, status="completed", progress=100,
        ))
    kws = []
    for text, score, rank, rel in specs:
        kw = make_keyword(text, brand_profile_id=ws.id)
        db.add(KeywordScore(
            scoring_run_id=run.id, keyword_id=kw.id,
            ads_score=score, ads_rank=rank,
        ))
        if rel is not None:
            db.add(KeywordRelevance(
                scoring_run_id=run.id, keyword_id=kw.id,
                relevance_score=rel,
            ))
        kws.append(kw)
    db.commit()
    return ws, run, kws


def _seed_candidates(db, run, ordering, n):
    for rank, kid in enumerate(ordering[:n], start=1):
        db.add(ChannelCandidate(
            scoring_run_id=run.id, keyword_id=kid, channel="ADS",
            raw_score=1.0, rank_in_channel=rank,
        ))
    db.commit()


def test_budget_initial_pool_cap():
    assert budget_initial("ADS", 10) == 30       # 3K < cap(120)
    assert budget_initial("ADS", 63) == 120      # cap kesiyor
    assert budget_initial("SEO", 86) == 60       # cap(60) kesiyor
    assert budget_initial("SOCIAL", 10) == 30


def test_rrf_is_one_based():
    # Bağımsız 1-based hesapla ve modülle karşılaştır
    primary = [101, 102, 103]
    secondary = [103, 101, 102]
    k = 7
    r1 = {kid: i for i, kid in enumerate(primary, start=1)}
    r2 = {kid: i for i, kid in enumerate(secondary, start=1)}
    expected = sorted(
        primary,
        key=lambda kid: (-(1 / (k + r1[kid]) + 1 / (k + r2[kid])), kid),
    )
    assert rrf_ordering(primary, secondary, k) == expected
    # Simetrik eşitlikte kid kırar
    assert rrf_ordering([1, 2], [2, 1], 60) == [1, 2]


def test_production_tie_break_rank_then_id(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Üç satır aynı adjusted (skor 10, rel 0.5) — sıra kanal rank'i,
    # rank eşitse keyword_id belirler
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("aaa", 10, 3, 0.5), ("bbb", 10, 1, 0.5), ("ccc", 10, None, 0.5)],
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    order = adjusted_ordering(loaded["rows"], "ADS", 1.0)
    # rank 1 önce, rank 3 sonra, rank'siz (999999) en sona
    assert order == [kws[1].id, kws[0].id, kws[2].id]


def test_parity_gate_matches_and_rejects(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("k1", 30, 1, 0.9), ("k2", 20, 2, 0.9), ("k3", 10, 3, 0.9),
         ("k4", 5, 4, 0.9)],
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    order = adjusted_ordering(loaded["rows"], "ADS", 1.0)
    _seed_candidates(db_session, run, order, 4)

    # Parite geçer + bütçe ayrımı: B_initial=3 (K=1 → 3), B_actual=4
    result = build_channel_replay(
        db_session, run.id, "ADS", 1, loaded["rows"], {kws[3].id}, 1.0
    )
    assert result["B_initial"] == 3
    assert result["B_actual_examined"] == 4
    # Üretim baseline'ı DB kümesinden: k4 incelendi → reach 1
    assert result["production_baseline"]["reached"] == 1
    # B_initial'da k4 (4. sıra) YOK, B_actual'da var
    ra = result["orderings"]["adjusted_production"]["recall_at"]
    assert ra["B_initial"]["reached"] == 0
    assert ra["B_actual_examined"]["reached"] == 1

    # DB sırasını boz (ilk ikiyi takas) → fail-closed, rapor yok
    c1 = db_session.query(ChannelCandidate).filter_by(
        scoring_run_id=run.id, rank_in_channel=1).first()
    c2 = db_session.query(ChannelCandidate).filter_by(
        scoring_run_id=run.id, rank_in_channel=2).first()
    c1.rank_in_channel, c2.rank_in_channel = 2, 1
    db_session.commit()
    with pytest.raises(ReplayInputError, match="parite"):
        build_channel_replay(
            db_session, run.id, "ADS", 1, loaded["rows"], set(), 1.0
        )


def test_same_text_two_ids_not_collapsed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Kimlik keyword_id — aynı metinli iki satır iki ayrı pozitif sayılır
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("tekrar metin", 10, 1, 0.9), ("başka", 5, 2, 0.9)],
    )
    # İkinci bir keyword aynı metinle (global tabloda farklı id üretmek için
    # ufak varyasyon yerine doğrudan Keyword eklenir)
    from app.database.models import Keyword, WorkspaceKeyword

    dup = Keyword(keyword="tekrar metin ", is_active=True)  # trim'siz ham metin
    db_session.add(dup)
    db_session.flush()
    dup.keyword = "tekrar metin"  # aynı metin, farklı id
    db_session.add(WorkspaceKeyword(brand_profile_id=ws.id, keyword_id=dup.id))
    db_session.add(KeywordScore(scoring_run_id=run.id, keyword_id=dup.id,
                                ads_score=8, ads_rank=3))
    db_session.add(KeywordRelevance(scoring_run_id=run.id, keyword_id=dup.id,
                                    relevance_score=0.9))
    db_session.commit()

    loaded = load_run_inputs(db_session, run.id, ws.id)
    map_doc = {"t": {"mapping": [
        {"canonical": "tekrar metin", "labels": ["ADS"]},
    ]}}
    positives = resolve_positive_ids(loaded["rows"], map_doc, "t")
    assert positives["ADS"] == {kws[0].id, dup.id}  # ikisi de pozitif


def test_missing_positive_in_universe_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("var olan", 10, 1, 0.9)],
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    map_doc = {"t": {"mapping": [
        {"canonical": "evrende olmayan kelime", "labels": ["SEO"]},
    ]}}
    with pytest.raises(ReplayInputError, match="evreninde yok"):
        resolve_positive_ids(loaded["rows"], map_doc, "t")


def test_broken_relevance_identity_set_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("relevanssız", 10, 1, None), ("tam", 5, 2, 0.8)],
    )
    with pytest.raises(ReplayInputError, match="kimlik kümeleri"):
        load_run_inputs(db_session, run.id, ws.id)


def test_input_validations_fail_closed(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Yanlış workspace
    ws, run, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("a", 1, 1, 0.5)],
    )
    with pytest.raises(ReplayInputError, match="workspace"):
        load_run_inputs(db_session, run.id, ws.id + 999)
    # v2 dışı algoritma
    ws2, run2, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("b", 1, 1, 0.5)], algo="v2_1",
    )
    with pytest.raises(ReplayInputError, match="v2 değil"):
        load_run_inputs(db_session, run2.id, ws2.id)
    # multiplier != 1
    ws3, run3, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("c", 1, 1, 0.5)],
        manifest={"candidate_pool_multiplier": 4, "relevance_enabled": True},
    )
    with pytest.raises(ReplayInputError, match="multiplier"):
        load_run_inputs(db_session, run3.id, ws3.id)
    # relevance kapalı
    ws4, run4, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("d", 1, 1, 0.5)],
        manifest={"candidate_pool_multiplier": 1, "relevance_enabled": False},
    )
    with pytest.raises(ReplayInputError, match="relevance_enabled"):
        load_run_inputs(db_session, run4.id, ws4.id)


def test_incomplete_run_and_missing_task_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Codex 3. tur #1: yarım kalmış run reddedilir
    ws, run, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("a", 1, 1, 0.5)], status="channel_assigning",
    )
    with pytest.raises(ReplayInputError, match="final değil"):
        load_run_inputs(db_session, run.id, ws.id)
    # Status doğru ama completed TaskResult yoksa da red
    ws2, run2, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("b", 1, 1, 0.5)], with_task=False,
    )
    with pytest.raises(ReplayInputError, match="TaskResult yok"):
        load_run_inputs(db_session, run2.id, ws2.id)


def test_capacity_mismatch_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Codex 3. tur #2: kapasiteler run'dan okunur, beklenen ile eşit olmalı
    ws, run, _ = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("c", 1, 1, 0.5)], ads_capacity=7,
    )
    loaded = load_run_inputs(db_session, run.id, ws.id,
                             expected_capacities={"ADS": 7})
    assert loaded["capacities"]["ADS"] == 7
    with pytest.raises(ReplayInputError, match="kapasitesi"):
        load_run_inputs(db_session, run.id, ws.id,
                        expected_capacities={"ADS": 17})


def test_short_candidate_list_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Codex 3. tur #1: B_initial'dan az aday kısa prefix'le kapıdan geçemez
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("s1", 30, 1, 0.9), ("s2", 20, 2, 0.9), ("s3", 10, 3, 0.9),
         ("s4", 5, 4, 0.9)], ads_capacity=1,  # B_initial = 3
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    order = adjusted_ordering(loaded["rows"], "ADS", 1.0)
    _seed_candidates(db_session, run, order, 2)  # yalnız 2 aday (< 3)
    with pytest.raises(ReplayInputError, match="eksik"):
        build_channel_replay(db_session, run.id, "ADS", 1,
                             loaded["rows"], set(), 1.0)


def test_rank_health_gate_gap_reported_duplicate_rejected(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Boşluk üretim gerçeğidir (transfer/expansion offset) → raporlanır;
    duplicate rank bozulma sinyalidir → red."""
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("r1", 30, 1, 0.9), ("r2", 20, 2, 0.9), ("r3", 10, 3, 0.9)],
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    order = adjusted_ordering(loaded["rows"], "ADS", 1.0)
    _seed_candidates(db_session, run, order, 3)
    c3 = db_session.query(ChannelCandidate).filter_by(
        scoring_run_id=run.id, rank_in_channel=3).first()
    c3.rank_in_channel = 5  # boşluk: 1,2,5 — GEÇER ama raporlanır
    db_session.commit()
    result = build_channel_replay(db_session, run.id, "ADS", 1,
                                  loaded["rows"], set(), 1.0)
    stats = result["production_parity"]["rank_stats"]
    assert stats["dense_1_to_Ba"] is False
    assert stats["rank_gap_count"] == 1
    assert stats["rank_max"] == 5

    # Duplicate rank → red (rank kolonunda unique kısıt yok; kapı gerekli)
    c3.rank_in_channel = 2
    db_session.commit()
    with pytest.raises(ReplayInputError, match="kesin artan"):
        build_channel_replay(db_session, run.id, "ADS", 1,
                             loaded["rows"], set(), 1.0)


def test_universe_lock_two_way(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    # Codex 3. tur #3: etiketsiz survivor bile kaybolsa fail-closed
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("u1", 10, 1, 0.9), ("u2", 5, 2, 0.9)],
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    ok_map = {"t": {"mapping": [
        {"canonical": "u1", "labels": ["ADS"]},
        {"canonical": "u2", "labels": []},  # etiketsiz survivor
    ]}}
    verify_universe_lock(loaded["rows"], ok_map, "t")  # geçer
    # Haritadaki etiketsiz survivor DB'de yok → red
    missing_map = {"t": {"mapping": [
        {"canonical": "u1", "labels": ["ADS"]},
        {"canonical": "u2", "labels": []},
        {"canonical": "kayip survivor", "labels": []},
    ]}}
    with pytest.raises(ReplayInputError, match="evren kilidi"):
        verify_universe_lock(loaded["rows"], missing_map, "t")
    # DB'de haritada olmayan kelime → red (ters yön)
    partial_map = {"t": {"mapping": [
        {"canonical": "u1", "labels": ["ADS"]},
    ]}}
    with pytest.raises(ReplayInputError, match="evren kilidi"):
        verify_universe_lock(loaded["rows"], partial_map, "t")
    # Codex 4. tur: exact-metin çoğalması (aynı metin iki ID) resmi kanonik
    # evrende reddedilir — set eşitliği bunu göremezdi
    dup_rows = loaded["rows"] + [{**loaded["rows"][0], "keyword_id": 999999}]
    with pytest.raises(ReplayInputError, match="çoğalması"):
        verify_universe_lock(dup_rows, ok_map, "t")


def test_ba_parity_metrics_report_transfer_difference(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Codex 3. tur #4: B_initial paritesi geçse bile Ba'da transfer
    enjeksiyonu set farkı yaratabilir — metrikler dürüstçe raporlanır."""
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("t1", 30, 1, 0.9), ("t2", 20, 2, 0.9), ("t3", 10, 3, 0.9),
         ("t4", 5, 4, 0.9), ("t5", 4, 5, 0.9)], ads_capacity=1,
    )
    loaded = load_run_inputs(db_session, run.id, ws.id)
    order = adjusted_ordering(loaded["rows"], "ADS", 1.0)
    # İlk 3 (B_initial) üretim sıralı; 4. aday TRANSFER gibi sıra-dışı: t5
    _seed_candidates(db_session, run, order[:3] + [kws[4].id], 4)

    result = build_channel_replay(db_session, run.id, "ADS", 1,
                                  loaded["rows"], {kws[4].id}, 1.0)
    pp = result["production_parity"]
    assert pp["ordered_equal_at_B_initial"] is True
    assert pp["actual_set_equal"] is False
    assert pp["actual_ordered_equal"] is False
    assert pp["transfer_difference_count"] == 1
    assert pp["only_in_adjusted_at_Ba"] == 1  # t4 counterfactual'da var
    assert pp["actual_set_jaccard"] == pytest.approx(3 / 5, abs=1e-4)
    # Üretim baseline'ı yine DB kümesinden: t5 incelendi → reach 1
    assert result["production_baseline"]["reached"] == 1


def test_input_hash_changes_on_data_change(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws, run, kws = _mk_universe(
        db_session, make_workspace, make_scoring_run, make_keyword,
        [("h1", 10, 1, 0.9), ("h2", 5, 2, 0.8)],
    )
    h_before = load_run_inputs(db_session, run.id, ws.id)["input_hash"]
    ks = db_session.query(KeywordScore).filter_by(
        scoring_run_id=run.id, keyword_id=kws[0].id).first()
    ks.ads_score = 11
    db_session.commit()
    h_after = load_run_inputs(db_session, run.id, ws.id)["input_hash"]
    assert h_before != h_after


def test_artifact_payload_validation_and_committed_artifact():
    doc = {"provenance": {"x": 1}, "results": {"a": [1, 2]}}
    doc["provenance"]["artifact_payload_sha256"] = compute_payload_sha(doc)
    validate_artifact(doc)  # geçer
    doc["results"]["a"] = [1, 3]
    with pytest.raises(ReplayInputError, match="SHA"):
        validate_artifact(doc)

    # Repo'daki commit'li artifact (varsa) öz-bütünlükten geçmeli
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "benchmark", "candidate_retrieval_replay_run24_25.json")
    if not os.path.exists(path):
        pytest.skip("commit'li replay artifact'ı yok")
    committed = json.load(open(path, encoding="utf-8"))
    validate_artifact(committed)
