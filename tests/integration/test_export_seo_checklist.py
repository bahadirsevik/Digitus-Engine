"""SEO+GEO checklist sonuclarinin export'a tam yansimasi.

Zincir: SEOComplianceResult.checks_json + GEOComplianceResult -> data_collector
(checklist_items / geo_evaluation_source / publish_review) -> csv/docx/excel/pdf.

Durum anlami tum formatlarda ayni: False = Basarisiz, NULL/eksik kayit/GEO AI
fallback = Degerlendirilmedi. FAQ "schema'ya hazir veri", alt metin "oneri".
Senaryolar: basarili, kritik basarisizlik, GEO fallback, NULL/eksik kayit.
"""
import csv
import io
import zipfile

import pytest

from app.compliance.publish_review import GEO_FALLBACK_NOTE_PREFIX
from app.database.models import (
    ChannelPool,
    GEOComplianceResult,
    Keyword,
    SEOComplianceResult,
)
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator
from app.schemas.export import ExportSectionEnum
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

GEO_CRITERIA = (
    'intro_answers_question', 'snippet_extractable', 'info_hierarchy_strong',
    'tone_is_informative', 'no_fluff_content', 'direct_answer_present',
    'has_verifiable_info',
)
PASSING_GEO = {
    **{c: True for c in GEO_CRITERIA},
    'score': 1.0,
    'total_passed': 7,
    'ai_snippet_preview': 'ozet',
    'improvement_notes': '',
    'evaluation_source': 'ai',
}

SCENARIOS = ['pass', 'critical_fail', 'geo_fallback', 'null_geo', 'missing_seo', 'legacy_null']


@pytest.fixture
def seo_export(db_session, make_workspace, make_scoring_run, monkeypatch):
    """Tek SEO icerigi uretir (AI yok); scenario(name) ile satirlari bozar."""
    ws = make_workspace(name='export-checklist-ws', company_url='https://exp.example')
    run = make_scoring_run(brand_profile_id=ws.id, status='channel_assigned')
    kw = Keyword(keyword='hisse takip', monthly_volume=1000)
    db_session.add(kw)
    db_session.commit()
    db_session.add(ChannelPool(scoring_run_id=run.id, keyword_id=kw.id, channel='SEO', final_rank=1))
    db_session.commit()

    monkeypatch.setattr(
        SEOGEOGenerator, '_generate_raw_content', lambda self, **kwargs: dict(FIXED_CONTENT)
    )
    monkeypatch.setattr(
        'app.compliance.geo_checker.GEOComplianceChecker.check',
        lambda self, content, keyword: dict(PASSING_GEO),
    )
    monkeypatch.setattr(
        SEOGEOGenerator, '_maybe_revise_once',
        lambda self, keyword, content, seo_result, geo_result, **kw: (content, seo_result, geo_result),
    )
    result = SEOGEOGenerator(db_session, ai_service=None).generate_content(
        SEOGEOGenerateRequest(keyword_id=kw.id), scoring_run_id=run.id
    )
    content_id = result['id']

    def seo_q():
        return db_session.query(SEOComplianceResult).filter(
            SEOComplianceResult.seo_geo_content_id == content_id
        )

    def geo_q():
        return db_session.query(GEOComplianceResult).filter(
            GEOComplianceResult.seo_geo_content_id == content_id
        )

    def scenario(name):
        if name == 'critical_fail':
            row = seo_q().one()
            checks = [dict(c) for c in row.checks_json]
            for c in checks:
                if c['criterion'] == 'paragraphs_short':
                    c['status'] = 'fail'
            row.checks_json = checks
        elif name == 'geo_fallback':
            geo_q().update({
                **{c: False for c in GEO_CRITERIA},
                'total_score': 0,
                'improvement_notes': f'{GEO_FALLBACK_NOTE_PREFIX} (quota); degerlendirilmedi',
            })
        elif name == 'null_geo':
            geo_q().update({'direct_answer_present': None})
        elif name == 'missing_seo':
            seo_q().delete()
        elif name == 'legacy_null':
            seo_q().update({'checks_json': None, 'title_has_keyword': None})
        db_session.commit()

    return run, scenario


def _collect(db_session, run):
    from app.exporters.data_collector import ExportDataCollector
    data = ExportDataCollector(db_session).collect(run.id, sections=[ExportSectionEnum.SEO_CONTENT])
    return data.seo_contents.contents[0]


def _status(items, criterion):
    return next(i['status'] for i in items if i['criterion'] == criterion)


# ── Veri toplayici: ekrandaki anlamla ayni ─────────────────────────────

@pytest.mark.parametrize('name', SCENARIOS)
def test_collector_carries_checklist_and_review(db_session, seo_export, name):
    run, scenario = seo_export
    scenario(name)
    c = _collect(db_session, run)
    items = c.checklist_items
    review = c.publish_review

    if name == 'pass':
        assert review['required'] is False
        assert _status(items, 'paragraphs_short') == 'pass'
        assert _status(items, 'has_faq_items') == 'pass'  # checks_json yeni maddesi
        # Kritik olmayan eksikler (dis link, kisa metin) inceleme sebebi degil
        assert _status(items, 'has_external_link') == 'fail'
        assert 'not_evaluated' not in {i['status'] for i in items}
        assert {i['status'] for i in items if i['channel'] == 'GEO'} == {'pass'}
        assert c.geo_evaluation_source == 'ai'
    elif name == 'critical_fail':
        assert _status(items, 'paragraphs_short') == 'fail'
        assert review['required'] is True
    elif name == 'geo_fallback':
        assert c.geo_evaluation_source == 'fallback'
        geo = [i for i in items if i['channel'] == 'GEO']
        assert {i['status'] for i in geo} == {'not_evaluated'}  # 'fail' DEGIL
        assert review['critical_failures'][0]['criterion'] == 'geo_not_evaluated'
    elif name == 'null_geo':
        assert _status(items, 'direct_answer_present') == 'not_evaluated'
        assert _status(items, 'tone_is_informative') == 'pass'
        assert review['required'] is True
    elif name == 'missing_seo':
        # Onceden combined_score satiri eksik kayitta AttributeError veriyordu
        assert _status(items, 'seo_checks') == 'not_evaluated'
        assert review['critical_failures'][0]['criterion'] == 'seo_not_evaluated'
        assert c.seo_score is None
    elif name == 'legacy_null':
        assert _status(items, 'title_has_keyword') == 'not_evaluated'
        assert _status(items, 'title_length_ok') == 'pass'
        assert review['required'] is True
    assert review['manual_checks']


# ── CSV ────────────────────────────────────────────────────────────────

def _csv_rows(db_session, run, tmp_path, filename):
    from app.exporters.csv_exporter import CsvExporter
    path = str(tmp_path / 'seo.zip')
    CsvExporter(db_session).export(run.id, [ExportSectionEnum.SEO_CONTENT], path)
    with zipfile.ZipFile(path) as zf:
        assert 'seo_kontroller.csv' in zf.namelist()
        text = zf.read(filename).decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.parametrize('name', ['pass', 'critical_fail', 'geo_fallback', 'null_geo'])
def test_csv_checks_file(db_session, seo_export, tmp_path, name):
    run, scenario = seo_export
    scenario(name)
    rows = _csv_rows(db_session, run, tmp_path, 'seo_kontroller.csv')
    by = {r['Kriter Kodu']: r for r in rows}

    assert 'has_alt_texts' in by and 'paragraphs_short' in by  # yeni checklist maddeleri
    manual = [r for r in rows if r['Durum Kodu'] == 'manual']
    assert any('JSON-LD' in r['Kriter'] for r in manual)

    if name == 'pass':
        assert by['paragraphs_short']['Durum'] == 'Geçti'
        assert rows[0]['Yayın Öncesi İnceleme'] == 'Kritik sorun yok'
    elif name == 'critical_fail':
        assert by['paragraphs_short']['Durum'] == 'Başarısız'
        assert by['paragraphs_short']['Durum Kodu'] == 'fail'
        assert rows[0]['Yayın Öncesi İnceleme'] == 'Gerekli'
    elif name == 'geo_fallback':
        geo = [r for r in rows if r['Kanal'] == 'GEO']
        assert {r['Durum'] for r in geo} == {'Değerlendirilmedi'}
        assert not any(r['Durum Kodu'] == 'fail' for r in geo)
    elif name == 'null_geo':
        assert by['direct_answer_present']['Durum Kodu'] == 'not_evaluated'

    # Mevcut SEO dosyasi korunur + yeni kolonlar sona eklenir
    content_rows = _csv_rows(db_session, run, tmp_path, 'seo_icerikler.csv')
    assert content_rows[0]['Başlık'] == FIXED_CONTENT['title']
    if name == 'geo_fallback':
        assert content_rows[0]['GEO Değerlendirme'].startswith('Değerlendirilmedi')


def test_csv_missing_seo_row(db_session, seo_export, tmp_path):
    run, scenario = seo_export
    scenario('missing_seo')
    rows = _csv_rows(db_session, run, tmp_path, 'seo_kontroller.csv')
    seo = [r for r in rows if r['Kanal'] == 'SEO']
    assert [(r['Kriter Kodu'], r['Durum']) for r in seo] == [('seo_checks', 'Değerlendirilmedi')]


# ── DOCX ───────────────────────────────────────────────────────────────

def _docx_text(db_session, run, tmp_path):
    from docx import Document
    from app.exporters.docx_exporter import DocxExporter
    path = str(tmp_path / 'seo.docx')
    DocxExporter(db_session).export(run.id, [ExportSectionEnum.SEO_CONTENT], path)
    doc = Document(path)
    paragraphs = '\n'.join(p.text for p in doc.paragraphs)
    cells = [[cell.text for cell in row.cells] for t in doc.tables for row in t.rows]
    return paragraphs, cells


@pytest.mark.parametrize('name', ['pass', 'critical_fail', 'geo_fallback', 'null_geo', 'missing_seo'])
def test_docx_checklist(db_session, seo_export, tmp_path, name):
    run, scenario = seo_export
    scenario(name)
    text, cells = _docx_text(db_session, run, tmp_path)

    # Eski 11 kriterle sinirli tablo yok; guncel checklist tablosu var
    assert 'SEO DETAY (11 Kriter)' not in text
    assert 'YAYIN ÖNCESİ KONTROL' in text
    assert "schema'ya hazır veri — FAQ schema uygulanmadı" in text
    assert 'görsellere uygulanmadı' in text
    assert 'JSON-LD üretilmedi' in text  # manuel not; JSON-LD uydurulmaz
    labels = {row[1]: row[3] for row in cells if len(row) == 5}

    if name == 'pass':
        assert 'Yayın öncesi inceleme: Kritik sorun yok' in text
        assert labels['Kısa paragraflar (2-3 cümle, maks 5)'] == 'Geçti'
    elif name == 'critical_fail':
        assert 'Yayın öncesi inceleme: Gerekli' in text
        assert labels['Kısa paragraflar (2-3 cümle, maks 5)'] == 'Başarısız'
    elif name == 'geo_fallback':
        geo_score_row = next(row for row in cells if row[0] == 'GEO')
        assert 'Değerlendirilmedi' in geo_score_row[2]
        assert 'Fail' not in geo_score_row[2]
        assert labels['İlk 40-60 kelimede net cevap'] == 'Değerlendirilmedi'
    elif name == 'null_geo':
        assert labels['İlk 40-60 kelimede net cevap'] == 'Değerlendirilmedi'
        assert 'Yayın öncesi inceleme: Gerekli' in text
    elif name == 'missing_seo':
        assert labels['SEO kontrolleri'] == 'Değerlendirilmedi'


# ── Excel ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', ['pass', 'critical_fail', 'geo_fallback', 'null_geo', 'missing_seo'])
def test_excel_checks_sheet(db_session, seo_export, tmp_path, name):
    from openpyxl import load_workbook
    from app.exporters.excel_exporter import ExcelExporter

    run, scenario = seo_export
    scenario(name)
    path = str(tmp_path / 'seo.xlsx')
    ExcelExporter(db_session).export(run.id, [ExportSectionEnum.SEO_CONTENT], path)
    wb = load_workbook(path)

    ws = wb['SEO Kontroller']
    assert ws.auto_filter.ref  # filtrelenebilir
    header = [c.value for c in ws[1]]
    rows = [dict(zip(header, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    by = {r['Kriter Kodu']: r for r in rows}

    if name == 'pass':
        assert by['paragraphs_short']['Durum Kodu'] == 'pass'
    elif name == 'critical_fail':
        assert by['paragraphs_short']['Durum'] == 'Başarısız'
        assert by['paragraphs_short']['Yayın Öncesi İnceleme'] == 'Gerekli'
    elif name == 'geo_fallback':
        assert {r['Durum Kodu'] for r in rows if r['Kanal'] == 'GEO'} == {'not_evaluated'}
    elif name == 'null_geo':
        assert by['direct_answer_present']['Durum'] == 'Değerlendirilmedi'
    elif name == 'missing_seo':
        assert by['seo_checks']['Durum Kodu'] == 'not_evaluated'

    # Mevcut icerik sheet'i bozulmadi (ilk 14 kolon ayni sirada)
    content = wb['SEO İçerikler']
    content_header = [c.value for c in content[1]]
    assert content_header[:8] == ['Kelime', 'Başlık', 'URL', 'Meta Açıklama', 'Giriş Paragrafı',
                                  'İçerik Gövdesi', 'FAQ', 'Görsel Alt-Text']
    assert content_header[-2:] == ['GEO Değerlendirme', 'Yayın Öncesi İnceleme']
    assert content.cell(row=2, column=2).value == FIXED_CONTENT['title']


# ── PDF ────────────────────────────────────────────────────────────────

def _pdf_text(db_session, run):
    from app.exporters.pdf_exporter import PdfExporter
    from reportlab.platypus import Table
    exporter = PdfExporter(db_session)
    data = exporter.collect_data(run.id, [ExportSectionEnum.SEO_CONTENT])
    elements = exporter._create_seo_content(data.seo_contents)
    text = []
    table_rows = []
    for element in elements:
        if hasattr(element, 'getPlainText'):
            text.append(element.getPlainText())
        if isinstance(element, Table):
            for row in element._cellvalues:
                values = [cell.getPlainText() for cell in row]
                table_rows.append(values)
                text.extend(values)
    return '\n'.join(text), table_rows


@pytest.mark.parametrize('name', ['pass', 'critical_fail', 'geo_fallback', 'null_geo', 'missing_seo'])
def test_pdf_summary_and_warning(db_session, seo_export, tmp_path, name):
    from app.exporters.pdf_exporter import PdfExporter

    run, scenario = seo_export
    scenario(name)
    path = str(tmp_path / 'seo.pdf')
    PdfExporter(db_session).export(run.id, [ExportSectionEnum.SEO_CONTENT], path)
    with open(path, 'rb') as f:
        assert f.read(5) == b'%PDF-'

    text, table_rows = _pdf_text(db_session, run)
    assert 'Kontrol ozeti:' in text
    assert 'SEO/GEO kontrol listesi' in text
    assert 'Kanal' in text and 'Kriter' in text and 'Durum' in text
    assert 'Manuel yayin kontrol notlari' in text
    assert "FAQ schema uygulanmadı" in text
    assert len(table_rows) > 8

    def table_has(label, status):
        return any(row[1] == label and row[3] == status for row in table_rows)

    if name == 'pass':
        assert 'Yayin oncesi inceleme: Kritik sorun yok' in text
        assert 'Değerlendirilmedi 0' in text
        assert 'Kısa paragraflar (2-3 cümle, maks 5)' in text
        assert 'Geçti' in text
    elif name == 'critical_fail':
        assert 'Yayin oncesi inceleme: Gerekli' in text
        assert table_has('Kısa paragraflar (2-3 cümle, maks 5)', 'Başarısız')
    elif name == 'geo_fallback':
        assert 'GEO: Değerlendirilmedi (AI çağrısı başarısız)' in text
        assert table_has('İlk 40-60 kelimede net cevap', 'Değerlendirilmedi')
    elif name == 'null_geo':
        assert table_has('İlk 40-60 kelimede net cevap', 'Değerlendirilmedi')
    elif name == 'missing_seo':
        assert table_has('SEO kontrolleri', 'Değerlendirilmedi')
