"""Sirket SEO/GEO checklist kriterleri + agirlikli skor (plan: checklist entegrasyonu).

Kapsam:
- 6 yeni programatik kriterin pass/partial/fail sinirlari
- Skorun artik ONCELIK-AGIRLIKLI hesaplandigi (esit sayim degil)
- current_year parametresiyle deterministik yil kontrolu
- ContentStructure geriye uyum + yeni 500-600 sema default'lari
- _collect_failed_criteria: yeni kriterler revizyon listesine Turkce akar
"""
import pytest

from app.compliance.seo_checker import SEOComplianceChecker
from app.generators.seo_geo.seo_geo_generator import SEOGEOGenerator
from app.schemas.seo_geo import ContentStructure, SEOGEOGenerateRequest

YEAR = 2026


def make_content(**overrides):
    """Checklist'e TAM uyumlu icerik; override'larla kriter dusurulur."""
    base = {
        'title': 'Hisse Takip Nedir? 2026 Rehberi',
        'url_suggestion': 'hisse-takip-rehberi',
        'intro_paragraph': (
            'Hisse takip, portfoyunuzu tek panelden izlemenizi saglar. '
            'Hisse takip araclari anlik veriyle calisir.'
        ),
        'subheadings': [
            'Hisse Takip Nedir?',
            'Hisse Takip Nasil Yapilir?',
            'Neden Onemli?',
        ],
        'body_sections': [
            'Kisa cevap: takip araci kullanin. Boylece zaman kazanirsiniz.\n\n'
            'Ikinci paragraf da kisa. Iki cumleden olusur.',
            'Nasil yapilir?\n1. Hesap acin.\n2. Portfoy ekleyin.',
            f'{YEAR} yilinda veri odakli takip one cikti. Kisa ve nettir.',
        ],
        'bullet_points': [{'text': 'Madde', 'order': 1}],
        'internal_link_anchor': 'analiz paneli',
        'internal_link_suggestion': '/analiz',
        'external_link_anchor': 'Borsa Istanbul',
        'external_link_url': 'https://borsaistanbul.com',
        'meta_description': 'Hisse takip rehberi.',
        'faq_items': [
            {'question': 'Hisse takip nedir?', 'answer': 'Portfoy izleme yontemidir.'},
            {'question': 'Ucretli mi?', 'answer': 'Temel surum ucretsizdir.'},
            {'question': 'Nasil baslanir?', 'answer': 'Hesap acarak baslanir.'},
        ],
        'image_alt_texts': ['hisse takip paneli ekrani', 'hisse takip grafigi'],
        'word_count': 550,
        'keyword_count': 6,
        'keyword_density': 1.2,
    }
    base.update(overrides)
    return base


def run_check(content, **kwargs):
    checker = SEOComplianceChecker()
    kwargs.setdefault('current_year', YEAR)
    # Kelime sayisi artik GERCEK metinden sayilir (AI beyani degil);
    # fixture metni kisa oldugu icin aralik ona gore verilir
    kwargs.setdefault('word_count_min', 30)
    kwargs.setdefault('word_count_max', 200)
    return checker.check(content=content, keyword='hisse takip', **kwargs)


def get_check(result, criterion):
    return next(c for c in result['checks'] if c['criterion'] == criterion)


class TestNewCriteria:
    def test_full_compliant_content_passes_all_new_criteria(self):
        result = run_check(make_content())
        for criterion in (
            'paragraphs_short', 'subheadings_are_questions', 'has_faq_items',
            'has_steps_or_table', 'mentions_current_year', 'has_alt_texts',
        ):
            assert get_check(result, criterion)['status'] == 'pass', criterion

    def test_long_paragraphs_fail(self):
        long_para = ' '.join(['Bu bir cumledir.'] * 8)
        result = run_check(make_content(body_sections=[long_para, long_para, long_para]))
        assert get_check(result, 'paragraphs_short')['status'] == 'fail'

    def test_statement_headings_fail_and_mixed_partial(self):
        result = run_check(make_content(subheadings=['Genel Bakis', 'Tarihce', 'Ozet']))
        assert get_check(result, 'subheadings_are_questions')['status'] == 'fail'

        mixed = run_check(make_content(subheadings=['Nedir?', 'Tarihce', 'Ozet', 'Genel']))
        # 1/4 = %25 -> fail; 2/4 = %50 -> partial
        mixed2 = run_check(make_content(subheadings=['Nedir?', 'Nasil Yapilir?', 'Ozet', 'Genel']))
        assert get_check(mixed, 'subheadings_are_questions')['status'] == 'fail'
        assert get_check(mixed2, 'subheadings_are_questions')['status'] == 'partial'

    def test_faq_partial_and_fail(self):
        one = run_check(make_content(faq_items=[{'question': 'S?', 'answer': 'C.'}]))
        assert get_check(one, 'has_faq_items')['status'] == 'partial'
        none = run_check(make_content(faq_items=[]))
        assert get_check(none, 'has_faq_items')['status'] == 'fail'

    def test_steps_or_table_variants(self):
        no_steps = run_check(make_content(body_sections=['Duz metin. Kisa cumle.']))
        assert get_check(no_steps, 'has_steps_or_table')['status'] == 'fail'

        table = run_check(make_content(
            body_sections=['| Ozellik | A | B |\n|---|---|---|\n| Fiyat | 1 | 2 |']
        ))
        assert get_check(table, 'has_steps_or_table')['status'] == 'pass'

    def test_year_previous_year_accepted_and_missing_fails(self):
        prev = run_check(make_content(
            body_sections=[f'{YEAR - 1} verilerine gore kisa cumle. Net cevap.']
        ))
        assert get_check(prev, 'mentions_current_year')['status'] == 'pass'

        missing = run_check(make_content(
            title='Hisse Takip Nedir? Rehber',
            body_sections=['Yilsiz kisa metin. Iki cumle.'],
        ))
        assert get_check(missing, 'mentions_current_year')['status'] == 'fail'

    def test_alt_texts_partial(self):
        one = run_check(make_content(image_alt_texts=['tek alt text']))
        assert get_check(one, 'has_alt_texts')['status'] == 'partial'


class TestWeightedScore:
    def test_score_is_weighted_not_equal_count(self):
        """Kritik (1.0) kriteri dusurmek, dusuk agirlikli (0.6) kriteri
        dusurmekten skoru DAHA COK dusurmeli — esit sayimda ikisi ayni olurdu."""
        full = run_check(make_content())['score']
        drop_critical = run_check(make_content(title='Rehber'))['score']  # title_has_keyword fail
        drop_low = run_check(make_content(image_alt_texts=[]))['score']  # has_alt_texts (0.6) fail

        assert drop_critical < drop_low < full

    def test_full_compliance_scores_one(self):
        result = run_check(make_content())
        failed = [c for c in result['checks'] if c['status'] != 'pass']
        assert failed == [], failed
        assert result['score'] == 1.0
        assert result['total_checks'] == 17


class TestRealWordCount:
    """Codex bulgusu: canli #19 icerikte AI 578 beyan etti, gercek govde 429
    idi ve compliance yanlis pass verdi. Kelime sayisi artik her zaman gercek
    metinden sayilir; AI beyani yalniz bilgi notu olarak raporlanir."""

    def test_declared_count_ignored_real_short_text_fails(self):
        # AI 578 diyor ama gercek metin ~70 kelime; 500-600 araliginda FAIL
        result = run_check(
            make_content(word_count=578),
            word_count_min=500, word_count_max=600,
        )
        item = get_check(result, 'word_count_in_range')
        assert item['status'] == 'fail'
        assert 'AI beyanı 578' in item['details']

    def test_real_count_in_range_passes_even_if_declared_wrong(self):
        # Gercek metin aralikta; AI beyani sacma olsa da PASS
        result = run_check(make_content(word_count=9999))
        assert get_check(result, 'word_count_in_range')['status'] == 'pass'

    def test_generator_recompute_overrides_ai_claim(self):
        content = make_content(word_count=578, keyword_count=99, keyword_density=42.0)
        SEOGEOGenerator._recompute_word_stats(content, 'hisse takip')
        real = len(
            (content['intro_paragraph'] + ' ' + ' '.join(content['body_sections'])).split()
        )
        assert content['word_count'] == real
        assert content['word_count'] < 578
        assert content['keyword_count'] != 99
        assert content['keyword_density'] != 42.0


class TestSchemaAndBackwardCompat:
    def test_request_defaults_500_600(self):
        req = SEOGEOGenerateRequest(keyword_id=1)
        assert req.word_count_min == 500
        assert req.word_count_max == 600
        # Tekil endpoint'in okudugu alan artik semada var (codex: yoktu -> 500)
        assert req.scoring_run_id is None
        assert SEOGEOGenerateRequest(keyword_id=1, scoring_run_id=7).scoring_run_id == 7

    def test_old_range_still_valid(self):
        req = SEOGEOGenerateRequest(keyword_id=1, word_count_min=300, word_count_max=450)
        assert req.word_count_min == 300

    def test_content_structure_without_new_fields_parses(self):
        data = make_content()
        data.pop('faq_items')
        data.pop('image_alt_texts')
        data['internal_link_suggestion'] = data.pop('internal_link_suggestion', '/analiz')
        parsed = ContentStructure(**data)
        assert parsed.faq_items == []
        assert parsed.image_alt_texts == []


class TestFailedCriteriaFlow:
    def test_new_criteria_flow_to_revision_list(self):
        seo_result = run_check(make_content(
            subheadings=['Genel Bakis', 'Tarihce', 'Ozet'],
            faq_items=[],
        ))
        failed = SEOGEOGenerator._collect_failed_criteria(
            SEOGEOGenerator.__new__(SEOGEOGenerator), seo_result, {}
        )
        joined = ' | '.join(failed)
        assert 'soru formatında' in joined  # subheadings_are_questions TR aciklamasi
        assert 'faq_items' in joined

    def test_unmapped_check_swept_from_checks_list(self):
        fake_result = {
            'score': 0.5,
            'checks': [
                {'criterion': 'gelecekte_eklenen', 'status': 'fail', 'details': 'aciklama'},
            ],
        }
        failed = SEOGEOGenerator._collect_failed_criteria(
            SEOGEOGenerator.__new__(SEOGEOGenerator), fake_result, {}
        )
        assert any('gelecekte_eklenen' in f for f in failed)


class TestFaqQuality:
    """FAQ yalniz cift sayisi degil: dolu soru/cevap + cevap 1-2 cumle."""

    def test_empty_question_or_answer_not_counted(self):
        items = [
            {'question': 'Hisse takip nedir?', 'answer': 'Portfoy izleme yontemidir.'},
            {'question': '   ', 'answer': 'Cevap var.'},
            {'question': 'Ucretli mi?', 'answer': ''},
            'duz metin',
        ]
        item = get_check(run_check(make_content(faq_items=items)), 'has_faq_items')
        assert item['status'] == 'partial'  # yalniz 1 gecerli cift
        assert 'boş soru/cevap: 3' in item['details']

    def test_long_answer_not_counted(self):
        long_answer = 'Bir. Iki. Uc cumle.'
        items = [
            {'question': 'S1?', 'answer': long_answer},
            {'question': 'S2?', 'answer': long_answer},
            {'question': 'S3?', 'answer': long_answer},
        ]
        item = get_check(run_check(make_content(faq_items=items)), 'has_faq_items')
        assert item['status'] == 'fail'
        assert '2 cümleyi aşan cevap: 3' in item['details']

    def test_two_sentence_answer_valid_and_schema_wording(self):
        items = [
            {'question': f'S{i}?', 'answer': 'Birinci cumle. Ikinci cumle.'} for i in range(3)
        ]
        item = get_check(run_check(make_content(faq_items=items)), 'has_faq_items')
        assert item['status'] == 'pass'
        # "schema uygulandi" denmez; hazir veri olarak raporlanir
        assert "Schema'ya hazır veridir, schema uygulanmadı" in item['details']


class TestAltTextQuality:
    """Alt metni: dolu + hedef keyword + keyword'un otesinde aciklama. Hala ONERI."""

    def test_empty_and_keywordless_not_valid(self):
        item = get_check(
            run_check(make_content(image_alt_texts=['', 'guzel bir grafik', 'hisse takip'])),
            'has_alt_texts',
        )
        # gecerli 0; ama dolu oneri var -> partial (fail degil)
        assert item['status'] == 'partial'
        assert 'boş: 1' in item['details']
        assert 'anahtar kelimesiz/açıklamasız: 2' in item['details']

    def test_all_empty_fails(self):
        item = get_check(run_check(make_content(image_alt_texts=['', '  '])), 'has_alt_texts')
        assert item['status'] == 'fail'

    def test_turkish_keyword_match_and_suggestion_wording(self):
        item = get_check(
            run_check(make_content(image_alt_texts=['Hisse Takip paneli ekranı', 'hİsse takİp grafiği'])),
            'has_alt_texts',
        )
        assert item['status'] == 'pass'
        assert 'gerçek görsele uygulanmadı' in item['details']



GEO_CRITERIA = (
    'intro_answers_question', 'snippet_extractable', 'info_hierarchy_strong',
    'tone_is_informative', 'no_fluff_content', 'direct_answer_present',
    'has_verifiable_info',
)


def make_geo(**overrides):
    base = {c: True for c in GEO_CRITERIA}
    base['evaluation_source'] = 'ai'
    base.update(overrides)
    return base


class TestPublishReview:
    def test_clean_content_needs_only_manual_checks(self):
        from app.compliance.publish_review import publish_review_from_results
        review = publish_review_from_results(run_check(make_content()), make_geo())
        assert review['required'] is False
        assert review['critical_failures'] == []
        assert len(review['manual_checks']) == 5

    def test_critical_seo_fail_requires_review_low_fail_does_not(self):
        from app.compliance.publish_review import publish_review_from_results
        failed = run_check(make_content(title='Rehber'))  # title_has_keyword fail (critical)
        review = publish_review_from_results(failed, make_geo())
        assert review['required'] is True
        assert review['label'] == 'Yayın öncesi inceleme gerekli'
        assert review['critical_failures'][0]['source'] == 'seo_auto'

        # Kritik olmayan (low) kriter dusuk -> inceleme sebebi DEGIL
        low = run_check(make_content(image_alt_texts=[]))
        assert publish_review_from_results(low, make_geo())['required'] is False

    def test_geo_p100_fail_and_fallback(self):
        from app.compliance.publish_review import publish_review_from_results
        seo = run_check(make_content())
        review = publish_review_from_results(seo, make_geo(info_hierarchy_strong=False))
        assert [f['criterion'] for f in review['critical_failures']] == ['info_hierarchy_strong']

        # Kritik olmayan GEO kriteri (ton) inceleme sebebi degil
        assert publish_review_from_results(
            seo, make_geo(tone_is_informative=False)
        )['required'] is False

        fb = publish_review_from_results(seo, make_geo(evaluation_source='fallback'))
        assert fb['required'] is True
        assert fb['critical_failures'][0]['criterion'] == 'geo_not_evaluated'


class TestGeoFallbackHonesty:
    """AI basarisizsa oznel GEO kriterleri regex ile 'gecti' isaretlenmez."""

    def test_fallback_marks_nothing_passed(self):
        from app.compliance.geo_checker import GEOComplianceChecker
        from app.compliance.publish_review import GEO_FALLBACK_NOTE_PREFIX

        class BrokenAI:
            def complete_json(self, *a, **kw):
                raise RuntimeError('quota')

        # Eski heuristik bu icerikte cogu kriteri 'gecti' sayardi
        result = GEOComplianceChecker(BrokenAI()).check(make_content(), 'hisse takip')
        assert result['evaluation_source'] == 'fallback'
        assert result['total_passed'] == 0
        assert result['score'] == 0.0
        assert not any(c['passed'] for c in result['checks'])
        assert result['improvement_notes'].startswith(GEO_FALLBACK_NOTE_PREFIX)

    def test_fallback_geo_not_sent_to_revision(self):
        seo_result = run_check(make_content())
        geo_fallback = make_geo(evaluation_source='fallback', direct_answer_present=False)
        failed = SEOGEOGenerator._collect_failed_criteria(
            SEOGEOGenerator.__new__(SEOGEOGenerator), seo_result, geo_fallback
        )
        assert not any(f.startswith('[GEO]') for f in failed)

        geo_ai = make_geo(direct_answer_present=False)
        failed_ai = SEOGEOGenerator._collect_failed_criteria(
            SEOGEOGenerator.__new__(SEOGEOGenerator), seo_result, geo_ai
        )
        assert any(f.startswith('[GEO]') for f in failed_ai)

    def test_geo_prompt_carries_first_60_and_200_words_single_call(self):
        from app.compliance.geo_checker import GEOComplianceChecker

        calls = []

        class RecordingAI:
            def complete_json(self, prompt, **kw):
                calls.append(prompt)
                return '{"direct_answer_present": true}'

        words = ' '.join(f'k{i}' for i in range(300))
        content = make_content(intro_paragraph=words, body_sections=['son bolum'])
        result = GEOComplianceChecker(RecordingAI()).check(content, 'hisse takip')
        assert len(calls) == 1  # ek AI cagrisi yok
        assert result['evaluation_source'] == 'ai'
        prompt = calls[0]
        first_60 = prompt.split('İlk 60 kelime')[1].split('---')[1].split()
        first_200 = prompt.split('İlk 200 kelime')[1].split('---')[1].split()
        assert len(first_60) == 60 and first_60[-1] == 'k59'
        assert len(first_200) == 200 and first_200[-1] == 'k199'
        assert 'İlk 200 kelime" bölümü konunun TAMAMINI özetliyor' in prompt


class TestPublishReviewMissingEvaluation:
    """Codex bulgusu: eksik degerlendirme (NULL GEO / SEO satiri yok) 'gecti' sayilmamali."""

    def test_null_geo_critical_is_not_passed(self):
        from app.compliance.publish_review import build_publish_review
        seo_checks = run_check(make_content())['checks']
        geo = make_geo(direct_answer_present=None)
        review = build_publish_review(seo_checks, geo, geo_evaluated=True)
        assert review['required'] is True
        assert [f['criterion'] for f in review['critical_failures']] == ['direct_answer_present']
        assert 'Değerlendirilmedi' in review['critical_failures'][0]['detail']

        # Kritik anahtar hic yoksa da ayni
        geo.pop('direct_answer_present')
        assert build_publish_review(seo_checks, geo)['required'] is True

    def test_missing_seo_evaluation_is_not_passed(self):
        from app.compliance.publish_review import build_publish_review
        for missing in (None, []):
            review = build_publish_review(missing, make_geo())
            assert review['required'] is True
            assert review['critical_failures'][0]['criterion'] == 'seo_not_evaluated'
