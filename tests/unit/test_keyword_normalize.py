"""
Zorunlu unit testler — keyword_normalize.

Plan v6: Python "İ".lower() edge-case için TRANSLATE ÖNCE, lower SONRA.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from app.core.keyword_normalize import normalize_keyword


def test_capital_i_dot():
    """İ (büyük İ, i dot) → i"""
    assert normalize_keyword('İSTANBUL') == 'istanbul'


def test_dotless_i():
    """I (dotless I) ve ı (küçük dotless) → i"""
    assert normalize_keyword('TIRAŞ') == 'tiras'


def test_full_turkish_set():
    """Tüm Türkçe karakter seti çalışıyor mu?"""
    assert normalize_keyword('FÖN TARAĞI') == 'fon taragi'


def test_combining_marks_cleaned():
    """Python 'İ'.lower()'ın ürettiği combining mark formu normalize ediliyor mu?"""
    # "i" + combining dot above (U+0307) + "stanbul"
    weird = 'i' + '\u0307' + 'stanbul'
    assert normalize_keyword(weird) == 'istanbul'


def test_empty_string():
    assert normalize_keyword('') == ''


def test_none_safe():
    """None gelirse de handle edilmeli (boş string dönmeli ek davranış)"""
    assert normalize_keyword(None) == ''  # type: ignore


def test_whitespace_normalize():
    assert normalize_keyword('  saç    bakım   ') == 'sac bakim'


def test_mixed_case_with_turkish():
    assert normalize_keyword('Şampuan Fiyatları') == 'sampuan fiyatlari'

# ── Token-hizalama korumasi (Codex, Hissefy denetimi) ─────────────────


def _dedup_kw(text, vol=100, comp=50):
    return {"keyword": text, "monthly_volume": vol,
            "competition_score": comp}


def test_antonym_pair_not_merged_despite_high_ratio():
    """Uretim bug'i: genel oran 88 ile zit-niyet cifti birlesiyordu;
    patron birini pozitif digerini negatif isaretlemisti."""
    from app.core.keyword_dedup import deduplicate_keywords

    out = deduplicate_keywords([_dedup_kw("en yüksek fiyatlı hisseler"),
                                _dedup_kw("en dusuk fiyatli hisseler")])
    assert len(out) == 2


def test_different_leading_token_not_merged():
    from app.core.keyword_dedup import deduplicate_keywords

    out = deduplicate_keywords([_dedup_kw("anlık hisse senedi"),
                                _dedup_kw("yabanci hisse senedi")])
    assert len(out) == 2


def test_suffix_variants_still_merge():
    from app.core.keyword_dedup import deduplicate_keywords

    assert len(deduplicate_keywords([_dedup_kw("hisse tavsiyeleri"),
                                     _dedup_kw("hisse tavsiyesi")])) == 1
    assert len(deduplicate_keywords([_dedup_kw("borsa sektörleri"),
                                     _dedup_kw("borsa sektörler")])) == 1


def test_char_normalize_variants_still_merge():
    from app.core.keyword_dedup import deduplicate_keywords

    assert len(deduplicate_keywords([_dedup_kw("canlı borsa"),
                                     _dedup_kw("canli borsa")])) == 1


def test_token_alignment_helper_contract():
    from app.core.keyword_dedup import _tokens_aligned

    assert _tokens_aligned("hisse tavsiye", "hisse tavsiye") is True
    assert _tokens_aligned("en yuksek fiyatl hisse",
                           "en dusuk fiyatl hisse") is False
    assert _tokens_aligned("an hisse sened", "yabanc hisse sened") is False
