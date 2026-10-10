"""Paylaşılan normalize_turkish büyük `İ` düzeltmesi (plan_v3_lokasyon_filtresi §12).

Önceden `.lower()` Türkçe haritadan önce çalışıyordu: 'İ'.lower() -> 'i' + U+0307,
noktalama temizliği birleşik noktayı boşluğa çevirip `İstanbul`u `i stanbul`
yapıyordu. Bu yüzden `İş Bankası` gibi rakip/tema/marka terimleri hiç eşleşmiyordu.

Testler üç şeyi sabitler:
1. `İ` (ve önceden lower() edilmiş `i̇`) tek token olarak katlanır.
2. `İ` İÇERMEYEN metinlerde çıktı eski algoritmayla BİREBİR aynıdır — embedding
   cache anahtarları ve mevcut politika kararları değişmez (NORMALIZER_VERSION
   bu yüzden artırılmadı).
3. Rakip, konu, marka savunması ve tema eşleştiricileri `İ`li terimleri yakalar.
"""
import re

import pytest

from app.core.channel.brand_defense import (
    build_brand_defense_context,
    is_own_brand_keyword,
)
from app.core.policy.competitor_policy import match_term
from app.core.policy.topic_policy import match_topic_term
from app.core.site_analyzer.relevance_scorer import RelevanceScorer
from app.core.site_analyzer.theme_matcher import matched_theme
from app.core.site_analyzer.turkish_normalizer import (
    TURKISH_CHAR_MAP,
    normalize_turkish,
)


def _legacy_normalize(text: str) -> str:
    """Düzeltme öncesi algoritmanın birebir kopyası (karşılaştırma tabanı)."""
    if not text:
        return ""
    text = text.lower().strip()
    for tr_char, ascii_char in TURKISH_CHAR_MAP.items():
        text = text.replace(tr_char, ascii_char)
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── 1. `İ` katlaması ─────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("İstanbul", "istanbul"),
    ("İş Bankası", "is bankasi"),
    ("İPRAGAZ", "ipragaz"),
    ("İdeal Data", "ideal data"),
    ("Şişli İlçesi", "sisli ilcesi"),
    ("İZMİR", "izmir"),
    # Başka bir yerde Python lower() edilmiş girdi: 'i' + U+0307
    ("i̇ş bankası", "is bankasi"),
])
def test_capital_dotted_i_folds_to_single_token(raw, expected):
    assert normalize_turkish(raw) == expected


def test_capital_i_matches_ascii_form():
    assert normalize_turkish("İş Bankası") == normalize_turkish("is bankasi")
    assert normalize_turkish("İş Bankası") == normalize_turkish("iş bankası")


def test_normalization_is_idempotent():
    for raw in ("İstanbul", "İş Bankası", "i̇ş", "Çağrı Ödeme"):
        once = normalize_turkish(raw)
        assert normalize_turkish(once) == once


# ── 2. `İ` içermeyen metinlerde davranış değişmez ────────────────────────
UNCHANGED_CORPUS = [
    "", "   ", "yapay zeka hisseleri", "Çağrı Ödeme", "IŞIK", "Işık Üniversitesi",
    "ankara'da avukat", "hello, world!", "gübre-fiyatları 2026", "ŞEKER Ç ö",
    "istanbul", "ısparta", "BIST 100 endeksi", "e-ticaret  sitesi", "ı i I",
]


@pytest.mark.parametrize("raw", UNCHANGED_CORPUS)
def test_output_unchanged_when_no_capital_dotted_i(raw):
    assert normalize_turkish(raw) == _legacy_normalize(raw)


@pytest.mark.parametrize("raw", [t for t in UNCHANGED_CORPUS if t.strip()])
def test_embedding_cache_key_unchanged_when_no_capital_dotted_i(raw):
    assert (RelevanceScorer._cache_key(normalize_turkish(raw))
            == RelevanceScorer._cache_key(_legacy_normalize(raw)))


def test_capital_i_text_gets_a_new_cache_key_naturally():
    # Eski kırık çıktı ile yeni çıktı farklı → farklı md5 → farklı anahtar;
    # eski (kırık) vektör yeniden kullanılmaz, sürüm artışı gerekmez.
    raw = "İstanbul"
    assert (RelevanceScorer._cache_key(normalize_turkish(raw))
            != RelevanceScorer._cache_key(_legacy_normalize(raw)))


# ── 3. Politika eşleştiricileri ──────────────────────────────────────────
def test_competitor_term_with_capital_i_matches_ascii_keyword():
    assert match_term("is bankasi kredi karti", ["İş Bankası"]) == "İş Bankası"
    assert match_term("ideal data fiyat", ["İdeal Data"]) == "İdeal Data"


def test_competitor_term_with_capital_i_does_not_overmatch():
    # Token sınırı korunur: 'deal' tek başına eşleşmez (eski kırık hal 'i deal data'ydı)
    assert match_term("deal data", ["İdeal Data"]) is None
    assert match_term("sbank kredi", ["İşbank"]) is None


def test_topic_term_with_capital_i_matches():
    assert match_topic_term("ipragaz tüp fiyatı", ["İpragaz"]) == "İpragaz"


def test_brand_defense_term_with_capital_i_matches():
    ctx = build_brand_defense_context(["İşbank"], company_name=None)
    assert ctx is not None
    assert is_own_brand_keyword("isbank kredi", ctx, "ADS") is True
    assert is_own_brand_keyword("isbank kredi karti basvurusu", ctx, "SEO") is True


def test_exclude_theme_with_capital_i_matches():
    assert matched_theme("inşaat malzemeleri", ["İnşaat"]) == "İnşaat"
