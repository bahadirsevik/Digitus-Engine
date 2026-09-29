"""
Yayın öncesi inceleme özeti (SEO+GEO içerik).

Üretilen içerik yayına HAZIR ilan edilmez: kritik checklist maddesi
başarısızsa sonuç açıkça "yayın öncesi inceleme gerekli" olarak işaretlenir.
Bu yalnız bir etikettir — ücretli üretimi başarısız saymaz, ek AI çağrısı
veya otomatik tekrar döngüsü açmaz (tek revizyon sınırı generator'da kalır).

Özet okuma anında türetilir (generator sonucu veya DB satırları); yeni
kolon/migration gerektirmez.

Madde kaynakları üç sınıftır:
- seo_auto   : programatik (deterministik) SEO denetçisi
- geo_ai     : GEO değerlendirmesi (AI yargısı — kesin ölçüm değildir)
- manual     : sistemde verisi olmayan, yayın ekibinin kontrol edeceği maddeler
"""
from typing import Any, Dict, Iterable, List, Optional

# Checklist P100 maddelerinin GEO karşılıkları (AI değerlendirir):
# ilk 40-60 kelimede net cevap / ilk ~200 kelimede konu özeti.
GEO_CRITICAL_CRITERIA = {
    'direct_answer_present': "İlk 40-60 kelimede net cevap yok (AI değerlendirmesi)",
    'info_hierarchy_strong': (
        "İlk ~200 kelime konuyu özetlemiyor / Özet → Detay → Örnek "
        "hiyerarşisi zayıf (AI değerlendirmesi)"
    ),
}

# GEO AI çağrısı başarısız olduğunda geo_checker bu önekle not yazar;
# DB'den okurken değerlendirilmemiş sonucu ayırt etmek için kullanılır.
GEO_FALLBACK_NOTE_PREFIX = "AI kontrolü başarısız oldu"

# Sistemde doğrulanmış verisi / yayın altyapısı olmayan maddeler. Uydurma
# alan üretilmez; yalnız yayın ekibine kontrol notu olarak gösterilir.
MANUAL_PUBLISH_CHECKS: List[str] = [
    "Yazar adı ve uzmanlığı (E-E-A-T) yayın sisteminde doğrulanıp eklenmeli.",
    "Metindeki istatistik, sayı ve kaynak iddiaları yayından önce doğrulanmalı.",
    "Article / FAQPage / BreadcrumbList JSON-LD üretilmedi: doğrulanmış yazar, "
    "yayın URL'si ve sayfa hiyerarşisi netleşince CMS'te eklenmeli. FAQ "
    "çiftleri schema'ya hazır veridir; schema uygulanmış değildir.",
    "Görsel alt metinleri öneridir; gerçek görsellere yayında uygulanmalı.",
    "3-6 aylık içerik güncelleme takvimi yayın ekibince planlanmalı.",
]

REVIEW_REQUIRED_LABEL = "Yayın öncesi inceleme gerekli"
MANUAL_ONLY_LABEL = "Kritik otomatik kontroller geçti; manuel yayın kontrolü gerekli"


def build_publish_review(
    seo_checks: Optional[Iterable[Dict[str, Any]]],
    geo_flags: Optional[Dict[str, Any]],
    geo_evaluated: bool = True,
) -> Dict[str, Any]:
    """Kritik başarısızlıklardan yayın öncesi inceleme özeti kurar.

    Args:
        seo_checks: SEO denetçisinin `checks` listesi (criterion/status/
            details/importance). Kritik (`importance == 'critical'`) ve
            `status == 'fail'` olanlar inceleme sebebidir. None/boş = SEO
            değerlendirmesi yok → "geçti" SAYILMAZ, inceleme istenir.
        geo_flags: GEO kriter → bool eşlemesi (None = GEO sonucu yok).
            Kritik kriter değeri True değilse (False VEYA NULL/eksik)
            geçmiş sayılmaz: False = AI "kaldı" dedi, NULL = değerlendirilmedi.
        geo_evaluated: False ise GEO AI değerlendirmesi yapılamamıştır;
            AI maddeleri geçti/kaldı SAYILMAZ, manuel kontrol istenir.
    """
    failures: List[Dict[str, str]] = []

    seo_checks = list(seo_checks or [])
    if not seo_checks:
        failures.append({
            'criterion': 'seo_not_evaluated',
            'source': 'seo_auto',
            'detail': "SEO kontrol sonucu bulunamadı: checklist manuel kontrol edilmeli",
        })
    for item in seo_checks:
        if item.get('importance') != 'critical':
            continue
        if item.get('status') == 'fail':
            failures.append({
                'criterion': item.get('criterion', ''),
                'source': 'seo_auto',
                'detail': item.get('details') or '',
            })
        elif item.get('status') == 'not_evaluated':
            failures.append({
                'criterion': item.get('criterion', ''),
                'source': 'seo_auto',
                'detail': "Değerlendirilmedi (kayıt boş) — manuel kontrol",
            })

    if geo_flags is None or not geo_evaluated:
        failures.append({
            'criterion': 'geo_not_evaluated',
            'source': 'geo_ai',
            'detail': (
                "GEO AI değerlendirmesi yapılamadı: ilk 40-60 kelimede cevap ve "
                "ilk ~200 kelimede özet manuel kontrol edilmeli"
            ),
        })
    else:
        for criterion, detail in GEO_CRITICAL_CRITERIA.items():
            value = geo_flags.get(criterion)
            if value is True:
                continue
            if value is False:
                failures.append({
                    'criterion': criterion,
                    'source': 'geo_ai',
                    'detail': detail,
                })
            else:
                # NULL/eksik: değerlendirme yok — geçti sayılmaz
                failures.append({
                    'criterion': criterion,
                    'source': 'geo_ai',
                    'detail': f"Değerlendirilmedi (kayıt boş) — manuel kontrol: {detail}",
                })

    required = bool(failures)
    return {
        'required': required,
        'status': 'review_required' if required else 'manual_check_only',
        'label': REVIEW_REQUIRED_LABEL if required else MANUAL_ONLY_LABEL,
        'critical_failures': failures,
        'manual_checks': list(MANUAL_PUBLISH_CHECKS),
    }


def publish_review_from_results(
    seo_result: Dict[str, Any], geo_result: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Generator'ın bellekteki SEO/GEO sonuçlarından özet."""
    geo_evaluated = bool(geo_result) and geo_result.get('evaluation_source') != 'fallback'
    return build_publish_review(seo_result.get('checks'), geo_result, geo_evaluated)


def legacy_seo_checks(seo_row: Any) -> List[Dict[str, Any]]:
    """checks_json'u olmayan eski SEO satırı için kolon-bazlı check listesi.

    NULL kolon 'fail' DEĞİL 'not_evaluated' olur (False = başarısız,
    NULL = değerlendirilmedi ayrımı)."""
    intro_count = seo_row.intro_keyword_count
    legacy = [
        ('title_has_keyword', seo_row.title_has_keyword, 'critical'),
        ('title_length_ok', seo_row.title_length_ok, 'critical'),
        ('url_has_keyword', seo_row.url_has_keyword, 'high'),
        ('intro_keyword_count', None if intro_count is None else intro_count >= 2, 'critical'),
        ('word_count_in_range', seo_row.word_count_in_range, 'high'),
        ('subheading_count_ok', seo_row.subheading_count_ok, 'medium'),
        ('subheadings_have_kw', seo_row.subheadings_have_kw, 'medium'),
        ('has_internal_link', seo_row.has_internal_link, 'medium'),
        ('has_external_link', seo_row.has_external_link, 'medium'),
        ('has_bullet_list', seo_row.has_bullet_list, 'low'),
        ('sentences_readable', seo_row.sentences_readable, 'medium'),
    ]
    return [
        {
            'criterion': name,
            'status': _bool_status(passed),
            'details': '',
            'importance': importance,
        }
        for name, passed, importance in legacy
    ]


def _bool_status(value: Any) -> str:
    """True -> pass, False -> fail, None/eksik -> not_evaluated."""
    if value is True:
        return 'pass'
    if value is False:
        return 'fail'
    return 'not_evaluated'


def geo_evaluation_source(geo_row: Any) -> Optional[str]:
    """'ai' | 'fallback' (AI çağrısı başarısız) | None (GEO kaydı yok)."""
    if geo_row is None:
        return None
    if (geo_row.improvement_notes or '').startswith(GEO_FALLBACK_NOTE_PREFIX):
        return 'fallback'
    return 'ai'


# Export/rapor için kriter adları (nötr; geçti/kaldı ifadesi taşımaz)
CRITERION_LABELS: Dict[str, str] = {
    'title_has_keyword': "Başlıkta anahtar kelime",
    'title_length_ok': "Başlık uzunluğu (≤70 karakter)",
    'url_has_keyword': "URL'de anahtar kelime",
    'intro_keyword_count': "Girişte anahtar kelime (≥2)",
    'word_count_in_range': "Kelime sayısı hedef aralıkta",
    'subheading_count_ok': "Alt başlık sayısı (≥3)",
    'subheadings_have_kw': "Alt başlıkta anahtar kelime",
    'has_internal_link': "İç link önerisi",
    'has_external_link': "Dış link önerisi",
    'has_bullet_list': "Madde işaretli liste",
    'sentences_readable': "Okunabilirlik (ort. ≤20 kelime/cümle)",
    'paragraphs_short': "Kısa paragraflar (2-3 cümle, maks 5)",
    'subheadings_are_questions': "Soru formatında alt başlıklar",
    'has_faq_items': "FAQ soru-cevap verisi (schema'ya hazır, uygulanmadı)",
    'has_steps_or_table': "Adım adım anlatım / tablo",
    'mentions_current_year': "Güncel yıl",
    'has_alt_texts': "Görsel alt metni önerileri (görsele uygulanmadı)",
    'seo_checks': "SEO kontrolleri",
    'intro_answers_question': "Giriş soruya doğrudan yanıt veriyor",
    'snippet_extractable': "Giriş bağımsız alıntılanabilir",
    'info_hierarchy_strong': "İlk ~200 kelimede özet / Özet→Detay→Örnek",
    'tone_is_informative': "Bilgilendirici ton",
    'no_fluff_content': "Dolgu içerik yok",
    'direct_answer_present': "İlk 40-60 kelimede net cevap",
    'has_verifiable_info': "Doğrulanabilir bilgi",
}

STATUS_LABELS: Dict[str, str] = {
    'pass': "Geçti",
    'partial': "Kısmi",
    'fail': "Başarısız",
    'not_evaluated': "Değerlendirilmedi",
}

SOURCE_LABELS: Dict[str, str] = {
    'seo_auto': "Otomatik kontrol",
    'geo_ai': "AI değerlendirmesi",
}

# GEO kriterleri (sıra = geo_checker.CRITERIA); geo_checker import edilmez
# (compliance -> generators import döngüsü)
GEO_CRITERIA_ORDER = [
    'intro_answers_question', 'snippet_extractable', 'info_hierarchy_strong',
    'tone_is_informative', 'no_fluff_content', 'direct_answer_present',
    'has_verifiable_info',
]


def checklist_items_from_rows(seo_row: Any, geo_row: Any) -> List[Dict[str, Any]]:
    """Export için madde bazlı SEO+GEO sonuçları (DB satırlarından).

    status: pass | partial | fail | not_evaluated. Eksik SEO satırı, NULL
    kolon, GEO kaydının olmaması ve GEO AI fallback'i 'not_evaluated' olur —
    hiçbiri 'fail' veya 'pass' sayılmaz.
    """
    items: List[Dict[str, Any]] = []

    if seo_row is None:
        items.append({
            'channel': 'SEO', 'criterion': 'seo_checks', 'source': 'seo_auto',
            'importance': 'critical', 'status': 'not_evaluated',
            'detail': "SEO kontrol sonucu bulunamadı",
        })
    else:
        for check in seo_row.checks_json or legacy_seo_checks(seo_row):
            status = check.get('status')
            if status not in STATUS_LABELS:
                status = 'not_evaluated'
            items.append({
                'channel': 'SEO', 'criterion': check.get('criterion', ''),
                'source': 'seo_auto',
                'importance': check.get('importance') or 'medium',
                'status': status, 'detail': check.get('details') or '',
            })

    source = geo_evaluation_source(geo_row)
    for criterion in GEO_CRITERIA_ORDER:
        if source is None:
            status, detail = 'not_evaluated', "GEO kaydı yok"
        elif source == 'fallback':
            status, detail = 'not_evaluated', "GEO AI değerlendirmesi yapılamadı"
        else:
            status = _bool_status(getattr(geo_row, criterion, None))
            detail = "Kayıt boş" if status == 'not_evaluated' else ''
        items.append({
            'channel': 'GEO', 'criterion': criterion, 'source': 'geo_ai',
            'importance': 'critical' if criterion in GEO_CRITICAL_CRITERIA else 'medium',
            'status': status, 'detail': detail,
        })

    for item in items:
        item['label'] = CRITERION_LABELS.get(item['criterion'], item['criterion'])
        item['status_label'] = STATUS_LABELS[item['status']]
        item['source_label'] = SOURCE_LABELS[item['source']]
    return items


def publish_review_from_rows(seo_row: Any, geo_row: Any) -> Dict[str, Any]:
    """DB satırlarından (SEOComplianceResult / GEOComplianceResult) özet."""
    seo_checks: List[Dict[str, Any]] = []
    if seo_row is not None:
        seo_checks = seo_row.checks_json or legacy_seo_checks(seo_row)
    geo_flags = None
    if geo_row is not None:
        geo_flags = {c: getattr(geo_row, c, None) for c in GEO_CRITICAL_CRITERIA}
    geo_evaluated = geo_evaluation_source(geo_row) == 'ai'
    return build_publish_review(seo_checks, geo_flags, geo_evaluated)
