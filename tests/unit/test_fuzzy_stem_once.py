"""Fuzzy stem-once refactor davranış-kilit testleri.

Kilitlenen davranış: _find_fuzzy_match İLK eşleşeni döndürür (en iyiyi değil)
ve önceden hesaplanmış stem cache'i sonucu DEĞİŞTİRMEZ — yalnızca hızlandırır.
"""
from app.core.csv_import.import_plan import _find_fuzzy_match
from app.core.keyword_dedup import strip_turkish_suffixes


def _cand(text: str, vol: int = 100, cs: float = 0.4, stemmed: bool = False) -> dict:
    c = {
        "keyword": text,
        "keyword_id": 1,
        "monthly_volume": vol,
        "competition_score": cs,
        "matched_in_workspace": True,
    }
    if stemmed:
        c["stemmed_keyword"] = strip_turkish_suffixes(text.lower().strip())
    return c


ROW = {"keyword": "hisse analizi", "monthly_volume": 100, "competition_score": 0.4}


def test_first_match_semantics_preserved_not_best_match():
    # Her ikisi de eşik üstü; İLK aday dönmeli (best-match'e geçilmedi — codex şartı)
    first = _cand("hisse analiz")     # yüksek benzerlik
    second = _cand("hisse analizi")   # birebir (daha da yüksek)
    result = _find_fuzzy_match(ROW, [first, second])
    assert result is not None
    matched, ratio, reason, should_skip = result
    assert matched is first  # best (second) DEĞİL, ilk eşleşen


def test_precomputed_stem_gives_identical_result():
    cands_plain = [_cand("hisse analiz"), _cand("portfoy takibi", vol=50)]
    cands_stemmed = [_cand("hisse analiz", stemmed=True), _cand("portfoy takibi", vol=50, stemmed=True)]

    r1 = _find_fuzzy_match(ROW, cands_plain)
    r2 = _find_fuzzy_match(ROW, cands_stemmed)

    assert r1 is not None and r2 is not None
    assert r1[0]["keyword"] == r2[0]["keyword"]
    assert r1[1] == r2[1]  # ratio ayni
    assert r1[2] == r2[2] and r1[3] == r2[3]  # reason + skip karari ayni


def test_no_match_below_threshold_with_and_without_cache():
    cands = [_cand("tamamen alakasiz kelime obegi", stemmed=True)]
    assert _find_fuzzy_match(ROW, cands) is None
    cands_plain = [_cand("tamamen alakasiz kelime obegi")]
    assert _find_fuzzy_match(ROW, cands_plain) is None
