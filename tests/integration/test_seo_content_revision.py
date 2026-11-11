"""SEO "Revize Et": geri bildirimli revizyon → önizleme → kabul.

Kapsam: kapsam dışı alanların kodla korunması (iki kapı), kapsama sınırlı iddia
onarımı, ilk üretimin genişletme turunun ÇALIŞMAMASI, önizlemede canlı içeriğin
değişmemesi, kabulde tek transaction (içerik + content_data + uyumluluk + sayaç),
export kimliğinin değişmesi, idempotency / tek üretim / lease / geç worker,
eski önerinin yeni içeriği ezememesi ve workspace izolasyonu.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core import content_revisions as cr
from app.database.models import (
    ChannelPool,
    ContentOutput,
    ContentRevision,
    Keyword,
    SEOComplianceResult,
    SEOGeoContent,
)
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator

SOURCE = {
    'title': 'Hisse Takip Nedir? 2026 Rehberi',
    'url_suggestion': 'hisse-takip-rehberi',
    'intro_paragraph': 'Hisse takip, portfoyu tek panelden izletir. Hisse takip anlik calisir.',
    'subheadings': ['Hisse Takip Nedir?', 'Nasil Yapilir?', 'Neden Onemli?'],
    'body_sections': [
        'Kisa paragraf. Iki cumle.',
        'Nasil yapilir?\n1. Hesap acin.\n2. Portfoy ekleyin.',
        '2026 yilinda takip kolaylasti. Kisa cumle.',
    ],
    'bullet_points': [{'text': 'Madde', 'order': 1}],
    'internal_link_anchor': 'panel',
    'internal_link_suggestion': 'https://ornek.example/panel',
    'external_link_anchor': None,
    'external_link_url': None,
    'meta_description': 'Hisse takip rehberi.',
    'faq_items': [
        {'question': 'Hisse takip nedir?', 'answer': 'Portfoy izleme yontemidir.'},
        {'question': 'Nasil baslanir?', 'answer': 'Hesap acarak.'},
        {'question': 'Mobilde var mi?', 'answer': 'Evet, uygulama var.'},
    ],
    'image_alt_texts': ['hisse takip paneli', 'hisse takip grafigi'],
    'word_count': 30,
    'keyword_count': 2,
    'keyword_density': 6.67,
}
PASSING_GEO = {
    **{c: True for c in (
        'intro_answers_question', 'snippet_extractable', 'info_hierarchy_strong',
        'tone_is_informative', 'no_fluff_content', 'direct_answer_present',
        'has_verifiable_info')},
    'score': 1.0, 'total_passed': 7, 'ai_snippet_preview': 'ozet',
    'improvement_notes': '', 'evaluation_source': 'ai',
}
NEW_TITLE = 'Hisse Takip: Samimi ve Kolay Rehber'
NEW_META = 'Hisse takibini adım adım, sade bir dille anlatan rehber.'


class RevisionAI:
    """Revizyon prompt'una verilen yanıtı döner; diğer (onarım) çağrılarını ayrıca sayar."""

    def __init__(self, revision_response, repair_response=None):
        self.revision_response = revision_response
        self.repair_response = repair_response
        self.prompts = []
        self.collector = None

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.prompts.append(prompt)
        if 'KULLANICI TALEBİ' in prompt:
            return json.dumps(self.revision_response, ensure_ascii=False)
        return json.dumps(self.repair_response or {}, ensure_ascii=False)

    def complete(self, prompt, max_tokens=None):
        return ''


@pytest.fixture(autouse=True)
def _geo_passes(monkeypatch):
    monkeypatch.setattr(
        'app.compliance.geo_checker.GEOComplianceChecker.check',
        lambda self, content, keyword, **kw: dict(PASSING_GEO),
    )


def _source(db, make_workspace, make_scoring_run, name='rev-seo', content=None):
    ws = make_workspace(name=name, company_url='https://ornek.example')
    run = make_scoring_run(brand_profile_id=ws.id, status='channel_assigned')
    kw = Keyword(keyword='hisse takip', monthly_volume=1000)
    db.add(kw)
    db.commit()
    db.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel='SEO', final_rank=1))
    db.commit()
    gen = SEOGEOGenerator(db, RevisionAI({}))
    row = gen._save_to_database(
        kw, dict(content or SOURCE), {'score': 0.6, 'checks': [], 'total_passed': 6},
        dict(PASSING_GEO), scoring_run_id=run.id,
    )
    return ws, run, row


def _revise(db, row, ai, scope, feedback='Başlığı daha samimi yap'):
    output = db.get(ContentOutput, row.content_output_id)
    return SEOGEOGenerator(db, ai).revise_content(row, output, feedback, scope)


# ── Üretim: kapsam koruması ─────────────────────────────────────────────────

def test_only_scope_fields_change_and_no_expansion(db_session, make_workspace, make_scoring_run):
    _, _, row = _source(db_session, make_workspace, make_scoring_run)
    ai = RevisionAI({
        'title': NEW_TITLE, 'meta_description': NEW_META,
        # kapsam DIŞI: yok sayılmalı
        'intro_paragraph': 'Tamamen yeni giriş.', 'url_suggestion': 'baska-url',
        'internal_link_suggestion': 'https://uydurma.example',
    })
    payload = _revise(db_session, row, ai, ['title_meta'])

    content = payload['content']
    assert content['title'] == NEW_TITLE and content['meta_description'] == NEW_META
    for field in ('intro_paragraph', 'url_suggestion', 'internal_link_suggestion',
                  'body_sections', 'subheadings', 'faq_items', 'image_alt_texts'):
        assert content[field] == SOURCE[field], field
    assert [c['field'] for c in payload['changes']] == ['title', 'meta_description']
    # Talep + yalnız izinli alanlar prompt'ta; kelime sayısı min altında olsa da genişletme YOK
    assert len(ai.prompts) == 1
    assert 'Başlığı daha samimi yap' in ai.prompts[0]
    assert '- title\n- meta_description' in ai.prompts[0]
    assert '- intro_paragraph' not in ai.prompts[0]
    # Önizleme canlı içeriği değiştirmez
    db_session.expire_all()
    assert db_session.get(SEOGeoContent, row.id).title == SOURCE['title']


def test_claim_repair_is_limited_to_scope(db_session, make_workspace, make_scoring_run):
    risky_intro = 'Hisse takip, ağrısız bir panel sunar. Hisse takip anlik calisir.'
    _, _, row = _source(db_session, make_workspace, make_scoring_run,
                        content={**SOURCE, 'intro_paragraph': risky_intro})
    # Onarım yanıtı kapsam dışındaki girişi de "düzeltmeye" çalışır → yok sayılmalı
    ai = RevisionAI(
        {'title': NEW_TITLE, 'meta_description': NEW_META},
        repair_response={**SOURCE, 'title': NEW_TITLE, 'meta_description': NEW_META,
                         'intro_paragraph': 'Temiz giriş. Hisse takip anlik calisir.'},
    )
    payload = _revise(db_session, row, ai, ['title_meta'])
    assert len(ai.prompts) == 2  # revizyon + tek onarım turu
    assert payload['content']['intro_paragraph'] == risky_intro
    assert payload['claim_check']['status'] == 'fail'  # kapsam dışı risk bulgu olarak kalır


def test_scope_gate_blocks_when_merge_leaks(db_session, make_workspace, make_scoring_run, monkeypatch):
    from app.generators.seo_geo import revision

    _, _, row = _source(db_session, make_workspace, make_scoring_run)
    monkeypatch.setattr(revision, 'merge_scoped',
                        lambda source, revised, scope: {**source, **revised})
    ai = RevisionAI({'title': NEW_TITLE, 'intro_paragraph': 'Sızan giriş.'})
    with pytest.raises(revision.RevisionScopeError, match='Giriş paragrafı'):
        _revise(db_session, row, ai, ['title_meta'])


# ── Uçtan uca: başlat → worker → önizle → kabul ──────────────────────────────

def _start(client, ws, row, monkeypatch, key='k1', scope=('title_meta',)):
    from app.tasks import generation_tasks

    sent = []
    monkeypatch.setattr(generation_tasks.revise_seo_content_task, 'apply_async',
                        lambda *a, **k: sent.append(k['kwargs']))
    res = client.post(
        f'/api/v1/generation/seo-geo/{row.id}/revise?brand_profile_id={ws.id}',
        json={'feedback': 'Başlığı daha samimi yap', 'scope': list(scope), 'idempotency_key': key},
    )
    return res, sent


def _run_worker(monkeypatch, sent, ai):
    from app.tasks import generation_tasks

    monkeypatch.setattr('app.generators.ai_service.get_ai_service', lambda *a, **k: ai)
    return generation_tasks.revise_seo_content_task.apply(kwargs=sent[-1]).result


def test_full_flow_preview_accept_and_export_identity(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    from app.exporters.data_collector import collect_exportable_identity

    ws, run, row = _source(db_session, make_workspace, make_scoring_run)
    res, sent = _start(client, ws, row, monkeypatch)
    assert res.status_code == 202, res.text
    rev_id = res.json()['revision_id']

    # Aynı gönderim tekrarı aynı revizyon; farklı gönderim süren üretimde 409
    again, _ = _start(client, ws, row, monkeypatch)
    assert again.json() == {'revision_id': rev_id, 'status': 'generating', 'created': False}
    clash, _ = _start(client, ws, row, monkeypatch, key='k2')
    assert clash.status_code == 409

    result = _run_worker(monkeypatch, sent, RevisionAI({'title': NEW_TITLE, 'meta_description': NEW_META}))
    assert result == {'status': 'ready', 'revision_id': rev_id}

    list_url = f'/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}'
    item = client.get(list_url).json()['items'][0]
    assert item['title'] == SOURCE['title']  # önizleme: canlı içerik değişmedi
    pending = item['pending_revision']
    assert pending['status'] == 'ready'
    assert {c['field'] for c in pending['changes']} == {'title', 'meta_description'}
    before = collect_exportable_identity(db_session, run.id, 'seo_content')

    accept_url = f'/api/v1/generation/seo-geo/revisions/{rev_id}/accept?brand_profile_id={ws.id}'
    ok = client.post(accept_url)
    assert ok.status_code == 200, ok.text
    assert ok.json()['revision_count'] == 1
    # Tekrar eden kabul ikinci güncelleme yapmaz
    assert client.post(accept_url).json()['revision_count'] == 1

    db_session.expire_all()
    content = db_session.get(SEOGeoContent, row.id)
    output = db_session.get(ContentOutput, row.content_output_id)
    assert content.title == NEW_TITLE and content.meta_description == NEW_META
    assert content.intro_paragraph == SOURCE['intro_paragraph']
    assert output.content_data['title'] == NEW_TITLE
    seo_row = db_session.query(SEOComplianceResult).filter_by(seo_geo_content_id=row.id).one()
    assert any(c['criterion'] == 'no_ungrounded_claims' for c in seo_row.checks_json)
    rev = db_session.get(ContentRevision, rev_id)
    assert rev.status == 'accepted' and rev.previous_payload['content']['title'] == SOURCE['title']
    # Satır sayısı artmadı (liste/dashboard aynı içeriği görür), export kimliği değişti
    assert db_session.query(ContentOutput).filter_by(scoring_run_id=run.id).count() == 1
    assert collect_exportable_identity(db_session, run.id, 'seo_content') != before
    item = client.get(list_url).json()['items'][0]
    assert item['pending_revision'] is None and item['revision_count'] == 1


def test_stale_proposal_cannot_overwrite_newer_content(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    ws, _, row = _source(db_session, make_workspace, make_scoring_run)
    res, sent = _start(client, ws, row, monkeypatch)
    _run_worker(monkeypatch, sent, RevisionAI({'title': NEW_TITLE, 'meta_description': NEW_META}))
    # Öneri hazırlandıktan sonra içerik başka bir kabulle değişti
    db_session.expire_all()
    content = db_session.get(SEOGeoContent, row.id)
    content.revision_count = 1
    content.title = 'Başka Revizyonla Gelen Başlık'
    db_session.commit()

    rev_id = res.json()['revision_id']
    r = client.post(f'/api/v1/generation/seo-geo/revisions/{rev_id}/accept?brand_profile_id={ws.id}')
    assert r.status_code == 409
    db_session.expire_all()
    assert db_session.get(SEOGeoContent, row.id).title == 'Başka Revizyonla Gelen Başlık'
    assert db_session.get(ContentRevision, rev_id).status == 'discarded'


def test_expired_lease_fails_and_late_worker_cannot_reopen(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    ws, run, row = _source(db_session, make_workspace, make_scoring_run)
    res, sent = _start(client, ws, row, monkeypatch)
    rev_id = res.json()['revision_id']
    db_session.expire_all()
    rev = db_session.get(ContentRevision, rev_id)
    rev.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    item = client.get(f'/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}').json()['items'][0]
    assert item['pending_revision']['status'] == 'failed'
    assert 'zamanında tamamlanmadı' in item['pending_revision']['error_message']
    # Geç kalan worker: token doğru olsa da kapatılmış revizyonu 'ready' yapamaz
    result = _run_worker(monkeypatch, sent, RevisionAI({'title': NEW_TITLE, 'meta_description': NEW_META}))
    assert result == {'status': 'aborted'}
    assert cr.mark_ready(db_session, rev_id, sent[-1]['attempt_token'], {'x': 1}) is False
    db_session.expire_all()
    assert db_session.get(ContentRevision, rev_id).status == 'failed'
    # Süren üretim bittiği için yeni revizyon açılabilir
    fresh, _ = _start(client, ws, row, monkeypatch, key='k-new')
    assert fresh.status_code == 202 and fresh.json()['created'] is True


def test_worker_ai_failure_keeps_content(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    ws, _, row = _source(db_session, make_workspace, make_scoring_run)
    res, sent = _start(client, ws, row, monkeypatch)
    # Zorunlu alan (title) hiç yok → öneri üretilemez
    result = _run_worker(monkeypatch, sent, RevisionAI({'meta_description': NEW_META}))
    assert result == {'status': 'failed', 'reason': 'ai_output'}
    db_session.expire_all()
    rev = db_session.get(ContentRevision, res.json()['revision_id'])
    assert rev.status == 'failed' and 'içerik değişmedi' in rev.error_message
    assert db_session.get(SEOGeoContent, row.id).title == SOURCE['title']


def test_workspace_isolation_and_validation(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    ws, _, row = _source(db_session, make_workspace, make_scoring_run)
    other = make_workspace(name='baska-ws')
    res, sent = _start(client, ws, row, monkeypatch)
    rev_id = res.json()['revision_id']
    _run_worker(monkeypatch, sent, RevisionAI({'title': NEW_TITLE, 'meta_description': NEW_META}))

    assert client.post(
        f'/api/v1/generation/seo-geo/revisions/{rev_id}/accept?brand_profile_id={other.id}'
    ).status_code == 404
    assert client.post(
        f'/api/v1/generation/seo-geo/{row.id}/revise?brand_profile_id={other.id}',
        json={'feedback': 'Başlığı değiştir', 'scope': ['title_meta']},
    ).status_code == 404
    url = f'/api/v1/generation/seo-geo/{row.id}/revise?brand_profile_id={ws.id}'
    assert client.post(url, json={'feedback': '   ab ', 'scope': ['intro']}).status_code == 422
    assert client.post(url, json={'feedback': 'Girişi kısalt', 'scope': []}).status_code == 422
    assert client.post(url, json={'feedback': 'Girişi kısalt', 'scope': ['links']}).status_code == 422

    # İptal: öneri kapanır, canlı içerik değişmez; kapanmış öneri kabul edilemez
    discard = client.post(f'/api/v1/generation/seo-geo/revisions/{rev_id}/discard?brand_profile_id={ws.id}')
    assert discard.json()['status'] == 'discarded'
    assert client.post(
        f'/api/v1/generation/seo-geo/revisions/{rev_id}/accept?brand_profile_id={ws.id}'
    ).status_code == 409


def test_same_key_with_different_request_is_rejected(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    ws, _, row = _source(db_session, make_workspace, make_scoring_run)
    res, _ = _start(client, ws, row, monkeypatch, key='same')
    assert res.status_code == 202
    other = client.post(
        f'/api/v1/generation/seo-geo/{row.id}/revise?brand_profile_id={ws.id}',
        json={'feedback': 'Bambaşka bir talep', 'scope': ['intro'], 'idempotency_key': 'same'},
    )
    assert other.status_code == 409


def test_worker_stops_without_ai_when_source_changed(
    client, db_session, make_workspace, make_scoring_run, monkeypatch,
):
    ws, _, row = _source(db_session, make_workspace, make_scoring_run)
    res, sent = _start(client, ws, row, monkeypatch)
    db_session.expire_all()
    db_session.get(SEOGeoContent, row.id).revision_count = 1
    db_session.commit()
    ai = RevisionAI({'title': NEW_TITLE, 'meta_description': NEW_META})
    assert _run_worker(monkeypatch, sent, ai) == {'status': 'failed', 'reason': 'source_changed'}
    assert ai.prompts == []
