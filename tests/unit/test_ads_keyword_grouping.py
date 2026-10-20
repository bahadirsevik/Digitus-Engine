"""ADS keyword gruplama testleri (plan_ads_15kw_standardi §1).

Kapsam: grup sayisi/boyutu (G = max(1, n // 15), max_groups yalniz sayiyi
sinirlar), AI cevabinin deterministik onarimi (yanlis grup sayisi, eksik /
tekrar / bilinmeyen id), fill_keyword_ids, "Diger Kelimeler" grubunun
olmamasi, Turkce-dogru fallback adi ve generate_ads'in havuzu kesmemesi.
Ucretli AI cagrisi yok (_FakeAI), DB yok.
"""
import json

import pytest

from app.generators.ads.ads_generator import AdsGenerator
from app.generators.ads.keyword_grouper import KeywordGrouper
from app.schemas.ads import AdsGenerateRequest, KeywordForGrouping

THEMES = ["laptop", "monitör", "klavye", "kulaklık"]


class _FakeAI:
    """Hep ayni cevabi doner; Exception verilirse her cagrida firlatir."""

    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _kws(n):
    return [
        KeywordForGrouping(id=i, keyword=f"{THEMES[(i - 1) % len(THEMES)]} model {i}")
        for i in range(1, n + 1)
    ]


def _answer(*id_lists):
    return json.dumps({
        "ad_groups": [
            {"name": f"Grup {i}", "theme": "t", "keyword_ids": ids, "keywords": []}
            for i, ids in enumerate(id_lists, 1)
        ]
    })


def _chunks(ids, size):
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def _sizes(groups):
    return sorted((len(g.keyword_ids) for g in groups), reverse=True)


def _assert_partition(groups, keywords):
    """Her kelime tam bir kez, keywords listesi ids ile uyumlu, 'Diger' yok."""
    all_ids = [i for g in groups for i in g.keyword_ids]
    assert sorted(all_ids) == sorted(k.id for k in keywords)
    text = {k.id: k.keyword for k in keywords}
    for g in groups:
        assert g.keywords == [text[i] for i in g.keyword_ids]
        assert set(g.fill_keyword_ids) <= set(g.keyword_ids)
        assert "Diğer" not in g.name


# ==================== 1. GRUP SAYISI VE BOYUTU ====================

class TestGroupSizes:
    @pytest.mark.parametrize("n, max_groups, expected", [
        (60, None, [15, 15, 15, 15]),
        (32, None, [16, 16]),
        (20, None, [20]),
        (12, None, [12]),
        (0, None, []),
        (32, 2, [16, 16]),
        (60, 2, [30, 30]),
    ])
    def test_sizes(self, n, max_groups, expected):
        kws = _kws(n)
        ai = _FakeAI(_answer(*_chunks([k.id for k in kws], 10)))  # AI'nin boyutu yanlis
        groups = KeywordGrouper(ai).group_keywords(kws, max_groups=max_groups)
        assert _sizes(groups) == expected
        _assert_partition(groups, kws)

    def test_single_group_makes_no_ai_call(self):
        ai = _FakeAI(RuntimeError("cagrilmamali"))
        groups = KeywordGrouper(ai).group_keywords(_kws(20))
        assert ai.prompts == []
        assert len(groups) == 1 and len(groups[0].keyword_ids) == 20

    def test_prompts_ask_for_exact_group_sizes(self):
        ai = _FakeAI(RuntimeError("boom"))
        KeywordGrouper(ai).group_keywords(_kws(32))
        assert len(ai.prompts) == 2  # primary + retry
        for prompt in ai.prompts:
            assert "16, 16" in prompt
            assert "3-7" not in prompt

    def test_prompts_require_name_and_theme_to_cover_all_keywords(self):
        # Kanit: "SEO ve Arama Motoru ..." adli grupta 6 web tasarim kelimesi vardi
        ai = _FakeAI(RuntimeError("boom"))
        KeywordGrouper(ai).group_keywords(_kws(32))
        assert len(ai.prompts) == 2  # primary + retry
        for prompt in ai.prompts:
            assert "TÜM kelimeleri kapsamalı" in prompt
            assert "SEO ve Web Tasarım Hizmetleri" in prompt


# ==================== 2. AI CEVABI ONARIMI ====================

class TestRepair:
    @pytest.mark.parametrize("group_size", [30, 10])  # 2 grup / 6 grup
    def test_wrong_group_count_still_four_groups_of_fifteen(self, group_size):
        kws = _kws(60)
        ai = _FakeAI(_answer(*_chunks([k.id for k in kws], group_size)))
        groups = KeywordGrouper(ai).group_keywords(kws)
        assert _sizes(groups) == [15, 15, 15, 15]
        _assert_partition(groups, kws)

    def test_split_off_keywords_are_marked_as_fill(self):
        kws = _kws(60)
        ai = _FakeAI(_answer(*_chunks([k.id for k in kws], 30)))  # 2 grup -> 2 bolme
        groups = KeywordGrouper(ai).group_keywords(kws)
        _assert_partition(groups, kws)
        filled = {i for g in groups for i in g.fill_keyword_ids}
        # Bolunen gruplarin ikinci yarisi AI'nin koydugu yerde degil -> fill
        assert filled == set(range(16, 31)) | set(range(46, 61))
        split = [g for g in groups if g.name in ("Grup 1 2", "Grup 2 2")]
        assert len(split) == 2
        assert all(g.fill_keyword_ids == g.keyword_ids for g in split)

    def test_missing_duplicate_and_unknown_ids(self):
        kws = _kws(60)
        ids = [k.id for k in kws]
        omitted = {1, 16, 31, 46, 47}
        g0, g1, g2, g3 = (
            [i for i in ids[a:a + 15] if i not in omitted] for a in (0, 15, 30, 45)
        )
        # 2, 3 ikinci kez; 4 ikinci kez; 9999 bilinmeyen
        ai = _FakeAI(_answer(g0, g1 + [2, 3], g2 + [9999], g3 + [4]))
        groups = KeywordGrouper(ai).group_keywords(kws)
        assert _sizes(groups) == [15, 15, 15, 15]
        _assert_partition(groups, kws)
        filled = {i for g in groups for i in g.fill_keyword_ids}
        assert omitted <= filled
        # tekrar eden id ilk geciste kaldi (g0), g1'e tasinmadi
        assert 2 in groups[0].keyword_ids and 2 not in groups[1].keyword_ids

    def test_exact_answer_records_no_fill(self):
        kws = _kws(60)
        ai = _FakeAI(_answer(*_chunks([k.id for k in kws], 15)))
        groups = KeywordGrouper(ai).group_keywords(kws)
        assert all(g.fill_keyword_ids == [] for g in groups)

    def test_unplaced_keyword_joins_the_group_it_overlaps_with(self):
        # 30 kelime, G=2: ilk 15 'laptop ...', son 15 'monitör ...'; AI iki
        # kelimeyi unutuyor -> kendi tema grubuna donmeli
        kws = [KeywordForGrouping(id=i, keyword=f"laptop model {i}") for i in range(1, 16)]
        kws += [KeywordForGrouping(id=i, keyword=f"monitör model {i}") for i in range(16, 31)]
        ai = _FakeAI(_answer(list(range(1, 15)), list(range(17, 31))))  # 15 ve 16 yok
        groups = KeywordGrouper(ai).group_keywords(kws)
        assert _sizes(groups) == [15, 15]
        assert 15 in groups[0].keyword_ids and 16 in groups[1].keyword_ids
        assert groups[0].fill_keyword_ids == [15] and groups[1].fill_keyword_ids == [16]

    def test_unusable_answer_falls_back(self):
        kws = _kws(60)
        ai = _FakeAI(_answer([9998, 9999]))  # hicbir bilinen id yok
        groups = KeywordGrouper(ai).group_keywords(kws)
        assert len(ai.prompts) == 2
        assert _sizes(groups) == [15, 15, 15, 15]
        _assert_partition(groups, kws)


# ==================== 3. FALLBACK ====================

class TestFallback:
    def test_fallback_uses_same_sizes_and_turkish_title(self):
        kws = _kws(32)
        kws[0] = KeywordForGrouping(id=1, keyword="istanbul laptop fiyatları")
        ai = _FakeAI(RuntimeError("boom"))
        groups = KeywordGrouper(ai).group_keywords(kws)
        assert len(ai.prompts) == 2
        assert [len(g.keyword_ids) for g in groups] == [16, 16]
        assert groups[0].keyword_ids == list(range(1, 17))  # girdi sirasi
        assert groups[0].name.startswith("İstanbul")
        assert groups[1].name == "Laptop Model 17 Grubu"
        _assert_partition(groups, kws)
        assert all(g.fill_keyword_ids == [] for g in groups)

    def test_single_group_name_is_turkish_correct(self):
        kws = [KeywordForGrouping(id=1, keyword="ışık istanbul")]
        groups = KeywordGrouper(_FakeAI("{}")).group_keywords(kws)
        assert groups[0].name == "Işık İstanbul - Genel"


# ==================== 4. generate_ads HAVUZU KESMEZ ====================

def test_generate_ads_passes_whole_pool_to_grouper(monkeypatch):
    gen = AdsGenerator(db=None, ai_service=_FakeAI("{}"))
    pool = [{"id": i, "keyword": f"kelime {i}", "sector": None} for i in range(1, 33)]
    monkeypatch.setattr(gen, "_get_ads_keywords", lambda run_id: pool)
    monkeypatch.setattr(gen, "_grounding_context", lambda *a, **k: ("", ""))
    monkeypatch.setattr(gen, "_save_to_database", lambda *a, **k: [])
    seen = {}

    def fake_group(keywords, max_groups=None):
        seen["ids"] = [k.id for k in keywords]
        seen["max_groups"] = max_groups
        return []

    monkeypatch.setattr(gen.grouper, "group_keywords", fake_group)
    result = gen.generate_ads(AdsGenerateRequest(scoring_run_id=1, max_groups=2))
    assert seen == {"ids": list(range(1, 33)), "max_groups": 2}
    assert result.total_keywords == 32
