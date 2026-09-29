"""
SEO+GEO kontrol listesi export yardımcıları.

Veri toplayıcının `SEOContentData.checklist_items` / `publish_review` /
`geo_evaluation_source` alanlarını okunabilir metne ve tablo satırlarına
çeviren TEK ortak kaynak — 4 exporter (csv, docx, excel, pdf) burayı kullanır;
durum anlamı (False = Başarısız, NULL/eksik/GEO fallback = Değerlendirilmedi)
formatlar arasında sapmasın.

FAQ çiftleri "schema'ya hazır veri", alt metinler "öneri" olarak adlandırılır;
JSON-LD, yazar, kaynak veya tarih üretilmez.
"""
from typing import Any, Dict, List, Optional

FAQ_HEADING = "FAQ soru-cevap (schema'ya hazır veri — FAQ schema uygulanmadı)"
ALT_TEXT_HEADING = "Görsel alt metni önerileri (görsellere uygulanmadı)"
NOT_EVALUATED = "Değerlendirilmedi"

CHECK_ROW_HEADERS = [
    'Kelime', 'İçerik ID', 'Kanal', 'Kriter Kodu', 'Kriter', 'Kaynak',
    'Önem', 'Durum', 'Durum Kodu', 'Detay', 'Yayın Öncesi İnceleme',
]


def geo_evaluation_text(source: Optional[str]) -> str:
    """GEO değerlendirme kaynağının okunur hali."""
    if source == 'ai':
        return "AI değerlendirmesi"
    if source == 'fallback':
        return f"{NOT_EVALUATED} (AI çağrısı başarısız)"
    return f"{NOT_EVALUATED} (GEO kaydı yok)"


def review_status_text(review: Optional[Dict[str, Any]]) -> str:
    """Yayın öncesi inceleme durumunun tek satırlık hali."""
    if not review:
        return f"{NOT_EVALUATED} — yayın öncesi inceleme gerekli"
    return review.get('label') or ''


def review_required_text(review: Optional[Dict[str, Any]]) -> str:
    return 'Gerekli' if (not review or review.get('required')) else 'Kritik sorun yok'


def review_failure_lines(review: Optional[Dict[str, Any]]) -> List[str]:
    """İncelemeyi gerektiren kritik maddeler (kaynağıyla)."""
    lines = []
    for f in (review or {}).get('critical_failures') or []:
        source = 'AI değerlendirmesi' if f.get('source') == 'geo_ai' else 'Otomatik kontrol'
        lines.append(f"[{source}] {f.get('detail') or f.get('criterion', '')}")
    return lines


def manual_check_lines(review: Optional[Dict[str, Any]]) -> List[str]:
    return list((review or {}).get('manual_checks') or [])


def status_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {'pass': 0, 'partial': 0, 'fail': 0, 'not_evaluated': 0}
    for item in items or []:
        status = item.get('status') or 'not_evaluated'
        counts[status] = counts.get(status, 0) + 1
    return counts


def status_counts_text(items: List[Dict[str, Any]]) -> str:
    c = status_counts(items)
    return (
        f"Geçti {c['pass']} · Kısmi {c['partial']} · Başarısız {c['fail']} · "
        f"{NOT_EVALUATED} {c['not_evaluated']}"
    )


def check_rows(content: Any) -> List[List[Any]]:
    """Excel/CSV için madde bazlı satırlar (+ manuel yayın kontrolü satırları)."""
    review = content.publish_review
    review_text = review_required_text(review)
    rows = []
    for item in content.checklist_items or []:
        rows.append([
            content.keyword, content.id, item.get('channel', ''),
            item.get('criterion', ''), item.get('label', ''),
            item.get('source_label', ''), item.get('importance', ''),
            item.get('status_label', ''), item.get('status', ''),
            item.get('detail', ''), review_text,
        ])
    for note in manual_check_lines(review):
        rows.append([
            content.keyword, content.id, 'MANUEL', 'manual_check', note,
            'Manuel yayın kontrolü', '', 'Manuel kontrol', 'manual', '', review_text,
        ])
    return rows
