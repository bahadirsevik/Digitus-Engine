"""Senkron SEO ucu: dispatch kilidi uretim boyunca TUTULMAZ (collector kilitlenmesi),
ama kayittan hemen once ayni kapi kisa kilit altinda yeniden kosar (Codex ca2c2f5 P2).

Yarış: AI beklerken politika/profil degisirse eski baglamdan uretilen icerik taze
diye KAYDEDILMEZ; tipli 409 (POLICY_STALE) doner ve satir yazilmaz.
"""
import json

import pytest
from fastapi import HTTPException

from app.api.v1 import generation as generation_api
from app.database.models import ChannelPool, Keyword, SEOGeoContent
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator
from tests.integration.test_seo_claim_persistence import FIXED_CONTENT, PASSING_GEO, RepairAI


@pytest.fixture
def seo_setup(db_session, make_workspace, make_scoring_run, monkeypatch):
    ws = make_workspace(name='presave-ws', company_url='https://presave.example')
    run = make_scoring_run(brand_profile_id=ws.id, status='channel_assigned')
    kw = Keyword(keyword='hisse takip', monthly_volume=1000)
    db_session.add(kw)
    db_session.commit()
    db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel='SEO', final_rank=1))
    db_session.commit()

    monkeypatch.setattr(
        SEOGEOGenerator, '_generate_raw_content',
        lambda self, **kwargs: dict(FIXED_CONTENT),
    )
    monkeypatch.setattr(
        'app.compliance.geo_checker.GEOComplianceChecker.check',
        lambda self, content, keyword, **kw: dict(PASSING_GEO),
    )
    monkeypatch.setattr(
        SEOGEOGenerator, '_maybe_revise_once',
        lambda self, keyword, content, seo_result, geo_result, **kw: (content, seo_result, geo_result),
    )
    # Uc, AI'yi Depends(get_ai) ile alir; onarim yolu bu testte tetiklenmez
    monkeypatch.setattr(generation_api, 'get_ai', lambda: RepairAI(dict(FIXED_CONTENT)), raising=False)
    return ws, run, kw


def _post(client, ws, run, kw):
    return client.post(
        f"/api/v1/generation/seo-geo?brand_profile_id={ws.id}",
        json={'keyword_id': kw.id, 'scoring_run_id': run.id},
    )


def test_generator_runs_before_save_hook_once_after_gate_and_before_save(
    db_session, seo_setup, monkeypatch
):
    ws, run, kw = seo_setup
    events = []
    real_save = SEOGEOGenerator._save_to_database

    def spy_save(self, **kwargs):
        events.append('save')
        return real_save(self, **kwargs)

    monkeypatch.setattr(SEOGEOGenerator, '_save_to_database', spy_save)
    from app.schemas.seo_geo import SEOGEOGenerateRequest
    result = SEOGEOGenerator(db_session, ai_service=RepairAI(dict(FIXED_CONTENT))).generate_content(
        SEOGEOGenerateRequest(keyword_id=kw.id), scoring_run_id=run.id,
        before_save=lambda: events.append('guard'),
    )
    assert events == ['guard', 'save']
    assert result['claim_check']['status'] == 'pass'


def test_endpoint_rechecks_freshness_before_save_and_blocks_stale(
    client, db_session, seo_setup, monkeypatch
):
    ws, run, kw = seo_setup
    calls = []

    def guard(db, run_id):
        calls.append(run_id)
        if len(calls) == 2:  # ikinci cagri = kayit oncesi; AI beklerken politika degisti
            raise HTTPException(status_code=409, detail={'code': 'POLICY_STALE', 'message': 'test'})

    monkeypatch.setattr(generation_api, '_guard_generation_dispatch', guard)

    res = _post(client, ws, run, kw)

    assert res.status_code == 409, res.text  # genis except 500'e cevirmez
    assert res.json()['detail']['code'] == 'POLICY_STALE'
    assert calls == [run.id, run.id]  # dispatch + kayit oncesi
    assert db_session.query(SEOGeoContent).count() == 0  # eski baglamdan taze kayit YOK


def test_policy_change_during_ai_call_blocks_save_with_real_guard(
    client, db_session, make_workspace, make_scoring_run, monkeypatch
):
    """Gercek guard + ikinci oturumdan politika mutasyonu (Codex onerisi):
    ilk kapi gecer, AI beklerken policy_version artar, kayit oncesi kapi
    TAZE satiri okuyup 409 doner; satir yazilmaz."""
    from sqlalchemy import text
    from app.database.connection import SessionLocal

    ws = make_workspace(
        name='race-ws', company_url='https://race.example', status='confirmed',
        profile_data={'company_name': 'Race', 'sector': 'Finans',
                      'services': ['hisse takip'], 'products': []},
    )
    run = make_scoring_run(brand_profile_id=ws.id, status='channel_assigned')
    kw = Keyword(keyword='hisse takip', monthly_volume=1000)
    db_session.add(kw)
    db_session.commit()
    db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel='SEO', final_rank=1))
    db_session.commit()

    def raw_content_with_concurrent_policy_change(self, **kwargs):
        other = SessionLocal()  # baska bir istek: politika degisti
        try:
            other.execute(text(
                "UPDATE brand_profiles SET policy_version = policy_version + 1 WHERE id = :i"
            ), {'i': ws.id})
            other.commit()
        finally:
            other.close()
        return dict(FIXED_CONTENT)

    monkeypatch.setattr(SEOGEOGenerator, '_generate_raw_content', raw_content_with_concurrent_policy_change)
    monkeypatch.setattr(
        'app.compliance.geo_checker.GEOComplianceChecker.check',
        lambda self, content, keyword, **kw: dict(PASSING_GEO),
    )
    monkeypatch.setattr(
        SEOGEOGenerator, '_maybe_revise_once',
        lambda self, keyword, content, seo_result, geo_result, **kw: (content, seo_result, geo_result),
    )

    res = _post(client, ws, run, kw)

    assert res.status_code == 409, res.text
    assert res.json()['detail']['code'] == 'POLICY_STALE'
    assert db_session.query(SEOGeoContent).count() == 0


def test_endpoint_saves_when_still_fresh(client, db_session, seo_setup, monkeypatch):
    ws, run, kw = seo_setup
    calls = []
    monkeypatch.setattr(generation_api, '_guard_generation_dispatch', lambda db, run_id: calls.append(run_id))

    res = _post(client, ws, run, kw)

    assert res.status_code == 200, res.text
    assert len(calls) == 2
    assert db_session.query(SEOGeoContent).count() == 1
    assert res.json()['claim_check']['status'] == 'pass'


def test_recheck_409_releases_workspace_lock_so_collector_flush_does_not_hang(
    client, db_session, seo_setup, monkeypatch
):
    """Kayit oncesi kapi 409 verince brand_profiles FOR UPDATE kilidi BIRAKILIR.

    Aksi halde istek sonunda get_ai teardown'u collector.finalize() ile ayri oturumdan
    ai_usage_events yazar (FK -> brand_profiles = kilitli satira key-share) ve istek sonsuza dek
    asili kalirdi. Gercek guard + gercek UsageCollector: bekleyen olay var, 409 hizli doner,
    satir kilidi sonrasinda ikinci oturumdan NOWAIT ile alinabilir."""
    import threading

    from sqlalchemy import text

    from app.core.telemetry import UsageCollector
    from app.database.connection import SessionLocal
    from app.dependencies import get_ai as get_ai_dependency
    from app.main import app

    ws, run, kw = seo_setup
    holder = {}

    def fake_get_ai():
        ai = RepairAI(dict(FIXED_CONTENT))
        ai.collector = UsageCollector()
        holder['collector'] = ai.collector
        try:
            yield ai
        finally:
            ai.collector.finalize()  # gercek get_ai ile ayni: ayri oturumdan flush

    def raw_content_recording_usage(self, **kwargs):
        holder['collector'].record(stage='seo_geo', model='test-model')  # kimlik _scope_usage ile atandi
        return dict(FIXED_CONTENT)

    monkeypatch.setattr(SEOGEOGenerator, '_generate_raw_content', raw_content_recording_usage)
    calls = []
    real_fresh = generation_api.require_fresh_channel_pool

    def fresh_then_409(db, run_obj):
        calls.append(1)
        if len(calls) == 2:  # ikinci cagri = kayit oncesi; FOR UPDATE bu noktada alinmis durumda
            raise HTTPException(status_code=409, detail={'code': 'POLICY_STALE', 'message': 'test'})
        return real_fresh(db, run_obj)

    monkeypatch.setattr(generation_api, 'require_fresh_channel_pool', fresh_then_409)
    app.dependency_overrides[get_ai_dependency] = fake_get_ai
    outcome = {}

    def call():
        outcome['res'] = _post(client, ws, run, kw)

    worker = threading.Thread(target=call, daemon=True)
    try:
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive(), "istek asili kaldi: FOR UPDATE kilidi hata yolunda birakilmadi"
    finally:
        app.dependency_overrides.pop(get_ai_dependency, None)

    assert outcome['res'].status_code == 409, outcome['res'].text
    assert outcome['res'].json()['detail']['code'] == 'POLICY_STALE'
    assert len(calls) == 2
    # Kilit birakildi: ikinci oturum satiri beklemeden kilitleyebilir
    other = SessionLocal()
    try:
        other.execute(text("SET lock_timeout = '3s'"))
        got = other.execute(
            text("SELECT id FROM brand_profiles WHERE id = :i FOR UPDATE NOWAIT"), {'i': ws.id}
        ).scalar()
        assert got == ws.id
        # collector olayi da yazildi (flush bekleyen kilide takilmadi)
        count = other.execute(
            text("SELECT count(*) FROM ai_usage_events WHERE brand_profile_id = :i"), {'i': ws.id}
        ).scalar()
        assert count >= 1
    finally:
        other.rollback()
        other.close()
