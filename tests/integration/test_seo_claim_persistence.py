"""İddia kapısı sonucunun KALICI kaydı ve tüm tüketicilere akışı (plan_iddia_denetimi A4).

Zincir: generate_content → _claim_gate → checks_json('no_ungrounded_claims') →
compliance/list uçları + publish_review → checklist_items_from_rows → CSV/DOCX export.
Eski kayıtlar (kriter satırda yok) 'geçti' sayılmaz: not_evaluated → review_required.
"""
import csv
import io
import json
import zipfile

import pytest

from app.compliance.publish_review import (
    checklist_items_from_rows,
    publish_review_from_rows,
)
from app.database.models import (
    ChannelPool,
    GEOComplianceResult,
    Keyword,
    SEOComplianceResult,
)
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator
from app.schemas.export import ExportSectionEnum
from app.schemas.seo_geo import SEOGEOGenerateRequest

BAD_INTRO = 'Hisse takip, ISO ve CE belgeli lazer hizmetidir. Hisse takip anlik calisir.'
GOOD_INTRO = 'Hisse takip, portfoyu tek panelden izletir. Hisse takip anlik calisir.'

FIXED_CONTENT = {
    'title': 'Hisse Takip Nedir? 2026 Rehberi',
    'url_suggestion': 'hisse-takip-rehberi',
    'intro_paragraph': GOOD_INTRO,
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
    **{c: True for c in (
        'intro_answers_question', 'snippet_extractable', 'info_hierarchy_strong',
        'tone_is_informative', 'no_fluff_content', 'direct_answer_present',
        'has_verifiable_info')},
    'score': 1.0, 'total_passed': 7, 'ai_snippet_preview': 'ozet',
    'improvement_notes': '', 'evaluation_source': 'ai',
}


class RepairAI:
    """Onarım turunda verilen içeriği döndürür (kaç kez çağrıldığını sayar)."""

    def __init__(self, content):
        self.content = content
        self.calls = 0

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.calls += 1
        return json.dumps(self.content, ensure_ascii=False)


def _generate(db_session, make_workspace, make_scoring_run, monkeypatch, intro, repair_intro):
    ws = make_workspace(name='claim-ws', company_url='https://claim.example')
    run = make_scoring_run(brand_profile_id=ws.id, status='channel_assigned')
    kw = Keyword(keyword='hisse takip', monthly_volume=1000)
    db_session.add(kw)
    db_session.commit()
    db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel='SEO', final_rank=1))
    db_session.commit()

    monkeypatch.setattr(
        SEOGEOGenerator, '_generate_raw_content',
        lambda self, **kwargs: {**FIXED_CONTENT, 'intro_paragraph': intro},
    )
    monkeypatch.setattr(
        'app.compliance.geo_checker.GEOComplianceChecker.check',
        lambda self, content, keyword, **kw: dict(PASSING_GEO),
    )
    monkeypatch.setattr(
        SEOGEOGenerator, '_maybe_revise_once',
        lambda self, keyword, content, seo_result, geo_result, **kw: (content, seo_result, geo_result),
    )
    ai = RepairAI({**FIXED_CONTENT, 'intro_paragraph': repair_intro})
    result = SEOGEOGenerator(db_session, ai_service=ai).generate_content(
        SEOGEOGenerateRequest(keyword_id=kw.id), scoring_run_id=run.id
    )
    return ws, run, result, ai


@pytest.fixture
def blocked(db_session, make_workspace, make_scoring_run, monkeypatch):
    """Onarım da ihlalli → kayıt fail ile saklanır."""
    return _generate(db_session, make_workspace, make_scoring_run, monkeypatch, BAD_INTRO, BAD_INTRO)


@pytest.fixture
def clean(db_session, make_workspace, make_scoring_run, monkeypatch):
    return _generate(db_session, make_workspace, make_scoring_run, monkeypatch, GOOD_INTRO, GOOD_INTRO)


def _claim_check(row):
    return next(c for c in row.checks_json if c['criterion'] == 'no_ungrounded_claims')


def test_unresolved_claim_persisted_and_review_required(client, db_session, blocked):
    ws, _, result, ai = blocked
    assert ai.calls == 1  # tek onarım turu
    assert result['claim_check']['status'] == 'fail'
    assert result['publish_review']['status'] == 'review_required'

    row = db_session.query(SEOComplianceResult).filter(
        SEOComplianceResult.seo_geo_content_id == result['id']).one()
    stored = _claim_check(row)
    assert stored['status'] == 'fail' and stored['importance'] == 'critical'
    assert 'cert:' in stored['details'] and 'service:' in stored['details']

    # İçerik silinmedi; okuma uçları aynı özeti görür
    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    review = body['publish_review']
    assert review['status'] == 'review_required'
    failure = next(f for f in review['critical_failures'] if f['criterion'] == 'no_ungrounded_claims')
    assert 'cert:' in failure['detail']
    assert any(c['criterion'] == 'no_ungrounded_claims' for c in body['seo_compliance']['checks'])

    assert client.get(
        f"/api/v1/generation/seo-geo/{result['id']}?brand_profile_id={ws.id}").status_code == 200
    listed = client.get(
        f"/api/v1/generation/seo-geo/list/{blocked[1].id}?brand_profile_id={ws.id}").json()
    assert listed['items'][0]['publish_review']['status'] == 'review_required'


def test_clean_content_passes_without_repair(db_session, clean):
    _, _, result, ai = clean
    assert ai.calls == 0
    assert result['claim_check'] == {
        'criterion': 'no_ungrounded_claims', 'status': 'pass',
        'importance': 'critical', 'details': ''}
    assert result['publish_review']['status'] == 'manual_check_only'
    row = db_session.query(SEOComplianceResult).filter(
        SEOComplianceResult.seo_geo_content_id == result['id']).one()
    assert _claim_check(row)['status'] == 'pass'


def test_old_row_without_criterion_is_not_evaluated_and_review_required(client, db_session, clean):
    ws, _, result, _ = clean
    row = db_session.query(SEOComplianceResult).filter(
        SEOComplianceResult.seo_geo_content_id == result['id']).one()
    row.checks_json = [c for c in row.checks_json if c['criterion'] != 'no_ungrounded_claims']
    db_session.commit()
    geo = db_session.query(GEOComplianceResult).filter(
        GEOComplianceResult.seo_geo_content_id == result['id']).one()

    items = checklist_items_from_rows(row, geo)
    item = next(i for i in items if i['criterion'] == 'no_ungrounded_claims')
    assert item['status'] == 'not_evaluated'
    assert item['label'] == 'Dayanaksız iddia yok'
    assert item['detail'] == 'İddia denetimi yapılmadı'

    review = publish_review_from_rows(row, geo)
    assert review['status'] == 'review_required'
    assert 'no_ungrounded_claims' in [f['criterion'] for f in review['critical_failures']]

    res = client.get(
        f"/api/v1/generation/seo-geo/{result['id']}/compliance?brand_profile_id={ws.id}")
    assert res.json()['publish_review']['status'] == 'review_required'


def test_exports_carry_claim_text(db_session, blocked, tmp_path):
    _, run, _, _ = blocked
    from docx import Document
    from app.exporters.csv_exporter import CsvExporter
    from app.exporters.docx_exporter import DocxExporter

    path = str(tmp_path / 'seo.zip')
    CsvExporter(db_session).export(run.id, [ExportSectionEnum.SEO_CONTENT], path)
    with zipfile.ZipFile(path) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read('seo_kontroller.csv').decode('utf-8-sig'))))
    claim = next(r for r in rows if r['Kriter Kodu'] == 'no_ungrounded_claims')
    assert claim['Durum Kodu'] == 'fail'
    assert claim['Kriter'] == 'Dayanaksız iddia yok'
    assert 'cert:' in claim['Detay']
    assert rows[0]['Yayın Öncesi İnceleme'] == 'Gerekli'

    docx_path = str(tmp_path / 'seo.docx')
    DocxExporter(db_session).export(run.id, [ExportSectionEnum.SEO_CONTENT], docx_path)
    doc = Document(docx_path)
    text = '\n'.join(p.text for p in doc.paragraphs)
    cells = ' '.join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert 'Dayanaksız iddia yok' in cells
    assert 'Dayanaksız iddia' in text + cells
    assert 'Yayın öncesi inceleme: Gerekli' in text


def test_load_grounding_facts_full_confirmed_lists_only(db_session, make_workspace, make_scoring_run):
    from app.core.channel.brand_defense import load_grounding_facts

    services = [f"hizmet{i}" for i in range(12)]
    ws = make_workspace(
        name='gf-ws', status='confirmed',
        profile_data={
            'company_name': 'Firma X', 'sector': 'Sağlık', 'target_audience': 'Genç hedef kitle',
            'problems_solved': ['sorun1'], 'products': ['urun1'], 'services': services,
            'use_cases': ['kullanim1'],
        })
    run = make_scoring_run(brand_profile_id=ws.id, status='channel_assigned')

    text = load_grounding_facts(db_session, run, "Güvenilir USP")
    assert all(s in text for s in services)  # 10 sınırı yok
    assert 'Ürünler: urun1' in text and 'Kullanım Alanları: kullanim1' in text
    assert text.endswith('Güvenilir USP')
    for absent in ('Firma', 'Sektör', 'Hedef kitle', 'Çözdüğü', 'sorun1', 'Sağlık'):
        assert absent not in text

    draft = make_workspace(name='gf-draft', status='draft', profile_data={'services': ['x']})
    run2 = make_scoring_run(brand_profile_id=draft.id, status='channel_assigned')
    assert load_grounding_facts(db_session, run2) == ''
    assert load_grounding_facts(db_session, None, 'usp') == 'usp'
