"""ADS 15 KW teslim standardı — export metinleri (csv/docx/excel/pdf ortak).

Yalnız olgu ve liste: yüzde, oran, puan YOK (sunum raporu kuralı). "15 KW teslim
standardı" bir kalite standardıdır, Google zorunluluğu değildir (plan K3).
"""
from typing import List

from app.core.constants import ADS_KEYWORDS_PER_GROUP

_N = ADS_KEYWORDS_PER_GROUP

# Tablo (csv/excel) sütunları: 'Reklam Grupları' tablosunun sonuna eklenir
STANDARD_HEADERS = [
    'KW Sayısı', "KW'li Açıklama Sayısı", 'Standart Durumu',
    'Başlıkta Kullanılan KW', 'Yalnız Açıklamada KW', 'Hiç Kullanılmayan KW',
    f"{_N}'e Tamamlamak İçin Eklenen KW",
]


def standard_notes(report) -> List[str]:
    """Standart altı (eksik sayılar) ve havuz notu; sorun yoksa boş."""
    notes = []
    if report.below_standard:
        parts = []
        if report.missing_headlines:
            parts.append(f'{report.missing_headlines} başlık eksik')
        if report.missing_kw_descriptions:
            parts.append(f"{report.missing_kw_descriptions} KW'li açıklama eksik")
        notes.append('Standart altı: ' + ', '.join(parts))
    if report.pool_below_target:
        notes.append(f"Havuz {_N} KW'den az ({report.keyword_count} KW)")
    return notes


def standard_cells(group) -> list:
    """Tablo hücreleri (STANDARD_HEADERS sırasıyla); rapor yoksa boş hücreler."""
    r = group.standard_report
    if r is None:
        return [''] * len(STANDARD_HEADERS)
    return [
        r.keyword_count, r.kw_description_count, '; '.join(standard_notes(r)),
        ', '.join(r.keywords_in_headlines), ', '.join(r.keywords_description_only),
        ', '.join(r.keywords_unused), ', '.join(r.fill_keywords),
    ]


def standard_lines(group) -> List[str]:
    """Docx/PDF satırları; rapor yoksa boş. Boş listeler yazılmaz."""
    r = group.standard_report
    if r is None:
        return []
    lines = [
        f"{_N} KW teslim standardı: {r.keyword_count} KW, {r.headline_count} başlık, "
        f"{r.kw_description_count} KW'li açıklama"
    ]
    lines += standard_notes(r)
    for label, items in (
        ('Başlıkta kullanılan KW', r.keywords_in_headlines),
        ('Yalnız açıklamada geçen KW', r.keywords_description_only),
        ('Hiç kullanılmayan KW', r.keywords_unused),
        (f"{_N}'e tamamlamak için eklenen KW", r.fill_keywords),
    ):
        if items:
            lines.append(f"{label}: {', '.join(items)}")
    return lines
