# -*- coding: utf-8 -*-
"""AI aynı yanıtta bir keyword'ü iki kez döndürürse atama ÇÖKMEMELİ.

Canlı koşuda yakalandı: `uq_pre_filter` ihlali TÜM channel_assignment
görevini düşürdü (run 24, keyword 12022, SOCIAL). Oturum `autoflush=False`
olduğu için `_save_results` içindeki "önce ara sonra ekle" kalıbı aynı
çağrıdaki bekleyen INSERT'i göremiyordu.
"""
import pytest

from app.core.channel.pre_filters.social_prefilter import SocialPreFilter
from app.database.models import Keyword, KeywordScore, PreFilterResult, ScoringRun


@pytest.fixture
def run_with_keyword(db_session, make_workspace):
    ws = make_workspace("Dedup WS")
    run = ScoringRun(run_name="dedup", brand_profile_id=ws.id,
                     total_keywords=1, ads_capacity=5, seo_capacity=5,
                     social_capacity=5, status="scored")
    db_session.add(run)
    db_session.commit()
    kw = Keyword(keyword="reklam ajansi", monthly_volume=100)
    db_session.add(kw)
    db_session.flush()
    db_session.add(KeywordScore(scoring_run_id=run.id, keyword_id=kw.id,
                                ads_score=1, seo_score=1, social_score=1))
    db_session.commit()
    return run, kw


def _row(keyword_id, reasoning):
    return {
        "keyword_id": keyword_id, "is_kept": True, "label": "viral",
        "ai_class": 3, "ai_reasoning": reasoning,
        "extra_data": {"reason_code": "TALKABILITY_CLASS_3"},
        "transfer_channel": None, "is_fallback": False,
    }


def test_duplicate_ids_in_one_response_write_single_row(db_session,
                                                        run_with_keyword):
    run, kw = run_with_keyword
    flt = SocialPreFilter(db_session, ai_service=None)
    # AI aynı yanıtta id'yi İKİ KEZ döndürdü (gerçek olay)
    flt._save_results(run.id, [_row(kw.id, "ilk"), _row(kw.id, "ikinci")])
    rows = (db_session.query(PreFilterResult)
            .filter_by(scoring_run_id=run.id, keyword_id=kw.id,
                       channel="SOCIAL").all())
    assert len(rows) == 1
    assert rows[0].ai_reasoning == "ilk"      # İLK kayıt kazanır


def test_duplicate_guard_logs_contract_violation(db_session,
                                                 run_with_keyword, caplog):
    from loguru import logger

    run, kw = run_with_keyword
    messages = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        flt = SocialPreFilter(db_session, ai_service=None)
        flt._save_results(run.id, [_row(kw.id, "a"), _row(kw.id, "b")])
    finally:
        logger.remove(sink_id)
    assert any("yinelenen id" in m for m in messages)


def test_normal_rows_are_unaffected(db_session, run_with_keyword,
                                    make_keyword):
    run, kw = run_with_keyword
    second = Keyword(keyword="dijital ajans", monthly_volume=50)
    db_session.add(second)
    db_session.commit()
    flt = SocialPreFilter(db_session, ai_service=None)
    flt._save_results(run.id, [_row(kw.id, "a"), _row(second.id, "b")])
    assert (db_session.query(PreFilterResult)
            .filter_by(scoring_run_id=run.id, channel="SOCIAL").count()) == 2
