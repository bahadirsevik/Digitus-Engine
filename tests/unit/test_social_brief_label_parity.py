"""Sosyal brief arayüz etiketleri ↔ backend sözleşmesi sapma koruması.

frontend/src/components/generation/socialBrief/labels.ts kaynak olarak okunur
(test_location_policy.py'deki TS il listesi kalıbı). Backend'e yeni bir
kategori tipi / kanca stili / format eklenip Türkçe etiketi unutulursa, ya da
geçmiş ekranının yedek format adı matristen ayrışırsa bu test kırmızıya düşer.
"""
import re
from pathlib import Path

from app.core.social.category_contract import CANONICAL_CATEGORY_TYPES
from app.core.social.content_contract import ALLOWED_HOOK_STYLES
from app.generators.social.format_matrix import get_format_matrix

LABELS_TS = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"
             / "generation" / "socialBrief" / "labels.ts")


def _ts_record(name: str) -> dict:
    source = LABELS_TS.read_text(encoding="utf-8")
    start = source.index(f"const {name}: Record<string, string> = {{")
    body = source[start:source.index("}", start)]
    return dict(re.findall(r"^\s*([a-z_]+):\s*'([^']*)',?\s*$", body, flags=re.M))


def test_every_backend_category_type_has_a_turkish_label():
    labels = _ts_record("CATEGORY_TYPE_LABEL")
    assert set(labels) == set(CANONICAL_CATEGORY_TYPES)
    for key, label in labels.items():
        assert label and label != key and "_" not in label


def test_every_backend_hook_style_has_a_turkish_label():
    labels = _ts_record("HOOK_STYLE_LABEL")
    assert set(labels) == set(ALLOWED_HOOK_STYLES)
    for key, label in labels.items():
        assert label and label != key


def test_history_format_fallback_matches_format_matrix_labels():
    fallback = _ts_record("FORMAT_FALLBACK")
    matrix = get_format_matrix()
    matrix_labels: dict = {}
    for platform in matrix.platforms:
        for fmt in platform.formats:
            matrix_labels.setdefault(fmt.id, set()).add(fmt.label)
    assert set(fallback) == set(matrix_labels)
    for fmt_id, label in fallback.items():
        # Aynı format tüm platformlarda aynı adı taşır; geçmiş de onu gösterir
        assert matrix_labels[fmt_id] == {label}, fmt_id
