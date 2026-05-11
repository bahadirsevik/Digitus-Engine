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