"""Checklist alanlarinin KALICI kaydi (codex sarti).

Zincir: generate_content -> _save_to_database -> SEOGeoContent.faq_items/
image_alt_texts + SEOComplianceResult.checks_json -> compliance endpoint
'checks' alani (eski satirda kolon-fallback) -> export data_collector.
"""
import pytest

from app.database.models import (
    ChannelPool,
    Keyword,
    SEOComplianceResult,
    SEOGeoContent,
)
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator
from app.schemas.seo_geo import SEOGEOGenerateRequest

FIXED_CONTENT = {
    'title': 'Hisse Takip Nedir? 2026 Rehberi',
    'url_suggestion': 'hisse-takip-rehberi',
    'intro_paragraph': (
        'Hisse takip, portfoyu tek panelden izletir. '
        'Hisse takip araclari anlik veriyle calisir.'
    ),
    'subheadings': ['Hisse Takip Nedir?', 'Nasil Yapilir?', 'Neden Onemli?'],
    'body_sections': [
        'Kisa paragraf. Iki cumle.',
        'Nasil yapilir?\n1. Hesap acin.\n2. Portfoy ekleyin.',
        '2026 yilinda takip kolaylasti. Kisa cumle.',
    ],
    'bullet_points': [{'text': 'Madde', 'order': 1}],
    'internal_link_anchor': None,
    'internal_link_suggestion': None,
    'external_link_anchor': None,
    'external_link_url': None,
    'meta_description': 'Hisse takip rehberi.',
    'faq_items': [
        {'question': 'Hisse takip nedir?', 'answer': 'Portfoy izleme yontemidir.'},
        {'question': 'Ucretli mi?', 'answer': 'Temel surum ucretsizdir.'},
        {'question': 'Nasil baslanir?', 'answer': 'Hesap acarak.'},
    ],
    'image_alt_texts': ['hisse takip paneli', 'hisse takip grafigi'],
    'word_count': 550,
    'keyword_count': 6,
    'keyword_density': 1.2,
}

PASSING_GEO = {
    'intro_answers_question': True,
    'snippet_extractable': True,
    'info_hierarchy_strong': True,
    'tone_is_informative': True,
    'no_fluff_content': True,
    'direct_answer_present': True,
    'has_verifiable_info': True,
    'score': 1.0,
    'total_passed': 7,
    'ai_snippet_preview': 'ozet',
    'improvement_notes': '',
    'detailed_analysis': [],
}


@pytest.fixture
def generated_content(db_session, make_workspace, make_scoring_run, monkeypatch):
    ws = make_workspace(name='checklist-ws', company_url='https://checklist.example')
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
        lambda self, content, keyword: dict(PASSING_GEO),
    )
    # Revizyon turu bu testte devre disi (AI yok)
    monkeypatch.setattr(
        SEOGEOGenerator, '_maybe_revise_once',
        lambda self, keyword, content, seo_result, geo_result, **kw: (content, seo_result, geo_result),
    )

    generator = SEOGEOGenerator(db_session, ai_service=None)
    result = generator.generate_content(
        SEOGEOGenerateRequest(keyword_id=kw.id), scoring_run_id=run.id
    )
    return ws, run, result


def test_checklist_fields_persisted(db_session, generated_content):
    _, _, result = generated_content
    row = db_session.query(SEOGeoContent).filter(SEOGeoContent.id == result['id']).one()
    assert len(row.faq_items) == 3
    assert row.faq_items[0]['question'] == 'Hisse takip nedir?'
    assert row.image_alt_texts == ['hisse takip paneli', 'hisse takip grafigi']


def test_checks_json_persisted_with_new_criteria(db_session, generated_content):
    _, _, result = generated_content
    compliance = (
        db_session.query(SEOComplianceResult)
        .filter(SEOComplianceResult.seo_geo_content_id == result['id'])
        .one()
    )
    criteria = {c['criterion'] for c in compliance.checks_json}
    for expected in ('paragraphs_short', 'subheadings_are_questions', 'has_faq_items',
                     'has_steps_or_table', 'mentions_current_year', 'has_alt_texts'):
        assert expected in criteria, expected


def test_compliance_endpoint_returns_checks(client, db_session, generated_content):
    ws, _, result = generated_content
    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    )
    assert res.status_code == 200, res.text
    checks = res.json()['seo_compliance']['checks']
    criteria = {c['criterion'] for c in checks}
    assert 'has_faq_items' in criteria
    assert 'paragraphs_short' in criteria


def test_compliance_endpoint_legacy_row_fallback(client, db_session, generated_content):
    """checks_json=null (eski satir) -> kolonlardan AYNI formatta 'checks' kurulur."""
    ws, _, result = generated_content
    db_session.query(SEOComplianceResult).filter(
        SEOComplianceResult.seo_geo_content_id == result['id']
    ).update({'checks_json': None})
    db_session.commit()

    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    )
    assert res.status_code == 200, res.text
    checks = res.json()['seo_compliance']['checks']
    criteria = {c['criterion'] for c in checks}
    # Eski format: 11 temel kriter var, checklist kriterleri yok
    assert 'title_has_keyword' in criteria
    assert 'has_faq_items' not in criteria
    assert all({'criterion', 'status', 'importance'} <= set(c) for c in checks)


def test_list_endpoint_includes_card_fields(client, generated_content):
    ws, run, _ = generated_content
    res = client.get(
        f"/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}"
    )
    assert res.status_code == 200, res.text
    item = res.json()['items'][0]
    assert len(item['faq_items']) == 3
    assert len(item['image_alt_texts']) == 2


def test_single_endpoint_requires_scoring_run_id(client, generated_content):
    """Tekil endpoint: scoring_run_id eksikse okunur 400 (codex notu);
    yanlis workspace'in run'i ile 404 (izolasyon)."""
    ws, run, _ = generated_content

    res = client.post(
        f"/api/v1/generation/seo-geo?brand_profile_id={ws.id}",
        json={"keyword_id": 1},
    )
    assert res.status_code == 400
    assert "scoring_run_id" in res.json()["detail"]

    res = client.post(
        f"/api/v1/generation/seo-geo?brand_profile_id={ws.id + 999}",
        json={"keyword_id": 1, "scoring_run_id": run.id},
    )
    assert res.status_code == 404


def test_export_data_collector_carries_checklist_fields(db_session, generated_content):
    _, run, _ = generated_content
    from app.exporters.data_collector import ExportDataCollector
    from app.schemas.export import ExportSectionEnum

    collector = ExportDataCollector(db_session)
    data = collector.collect(run.id, sections=[ExportSectionEnum.SEO_CONTENT])
    seo_content = data.seo_contents.contents[0]
    assert len(seo_content.faq_items) == 3
    assert seo_content.image_alt_texts == ['hisse takip paneli', 'hisse takip grafigi']


# ── Yayin oncesi inceleme ozeti (okuma aninda turetilir, DB kolonu yok) ──

def test_generate_result_carries_publish_review(generated_content):
    _, _, result = generated_content
    review = result['publish_review']
    # Fixture tum kritik kriterleri geciyor + GEO AI tarafindan degerlendirildi
    assert review['required'] is False
    assert review['status'] == 'manual_check_only'
    assert review['critical_failures'] == []
    # Verisi olmayan maddeler uydurulmaz, manuel not olarak doner
    joined = ' '.join(review['manual_checks'])
    assert 'JSON-LD' in joined and 'Yazar' in joined and '3-6' in joined


def test_compliance_endpoint_flags_review_on_geo_critical_fail(client, db_session, generated_content):
    from app.database.models import GEOComplianceResult

    ws, _, result = generated_content
    db_session.query(GEOComplianceResult).filter(
        GEOComplianceResult.seo_geo_content_id == result['id']
    ).update({'direct_answer_present': False})
    db_session.commit()

    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    review = body['publish_review']
    assert review['required'] is True
    assert review['label'] == 'Yayın öncesi inceleme gerekli'
    assert [(f['criterion'], f['source']) for f in review['critical_failures']] == [
        ('direct_answer_present', 'geo_ai')
    ]
    assert body['geo_compliance']['evaluation_source'] == 'ai'
    # Geriye uyum: mevcut alanlar degismedi
    assert 'critical_issues' in body and 'recommendations' in body


def test_compliance_endpoint_flags_review_on_seo_critical_fail(client, db_session, generated_content):
    ws, _, result = generated_content
    row = db_session.query(SEOComplianceResult).filter(
        SEOComplianceResult.seo_geo_content_id == result['id']
    ).one()
    checks = [dict(c) for c in row.checks_json]
    for c in checks:
        if c['criterion'] == 'paragraphs_short':
            c['status'] = 'fail'
    row.checks_json = checks
    db_session.commit()

    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    )
    review = res.json()['publish_review']
    assert review['required'] is True
    assert ('paragraphs_short', 'seo_auto') in {
        (f['criterion'], f['source']) for f in review['critical_failures']
    }


def test_geo_fallback_row_is_not_evaluated(client, db_session, generated_content):
    """GEO AI basarisiz satiri: kriterler 'gecti' sayilmaz, manuel inceleme ister."""
    from app.compliance.publish_review import GEO_FALLBACK_NOTE_PREFIX
    from app.database.models import GEOComplianceResult

    ws, run, result = generated_content
    db_session.query(GEOComplianceResult).filter(
        GEOComplianceResult.seo_geo_content_id == result['id']
    ).update({'improvement_notes': f'{GEO_FALLBACK_NOTE_PREFIX} (timeout); ...'})
    db_session.commit()

    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    )
    body = res.json()
    assert body['geo_compliance']['evaluation_source'] == 'fallback'
    assert body['publish_review']['required'] is True
    assert body['publish_review']['critical_failures'][0]['criterion'] == 'geo_not_evaluated'

    listed = client.get(
        f"/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}"
    ).json()['items'][0]
    assert listed['publish_review']['required'] is True


def test_list_endpoint_includes_publish_review(client, generated_content):
    ws, run, _ = generated_content
    item = client.get(
        f"/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}"
    ).json()['items'][0]
    assert item['publish_review']['required'] is False
    assert item['publish_review']['manual_checks']


def test_null_geo_columns_require_review(client, db_session, generated_content):
    """Codex bulgusu: NULL GEO kolonlari 'kritik kontroller gecti' dememeli."""
    from app.database.models import GEOComplianceResult

    ws, run, result = generated_content
    db_session.query(GEOComplianceResult).filter(
        GEOComplianceResult.seo_geo_content_id == result['id']
    ).update({'direct_answer_present': None, 'info_hierarchy_strong': None})
    db_session.commit()

    review = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    ).json()['publish_review']
    assert review['required'] is True
    assert {f['criterion'] for f in review['critical_failures']} == {
        'direct_answer_present', 'info_hierarchy_strong'
    }

    listed = client.get(
        f"/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}"
    ).json()['items'][0]
    assert listed['publish_review']['required'] is True


def test_missing_seo_row_requires_review(client, db_session, generated_content):
    """SEO kontrol satiri yoksa etiket 'gecti' olmamali."""
    ws, run, result = generated_content
    db_session.query(SEOComplianceResult).filter(
        SEOComplianceResult.seo_geo_content_id == result['id']
    ).delete()
    db_session.commit()

    review = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}"
    ).json()['publish_review']
    assert review['required'] is True
    assert review['critical_failures'][0]['criterion'] == 'seo_not_evaluated'

    listed = client.get(
        f"/api/v1/generation/seo-geo/list/{run.id}?brand_profile_id={ws.id}"
    ).json()['items'][0]
    assert listed['publish_review']['required'] is True
