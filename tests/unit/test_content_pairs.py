"""content_pairs — DB'siz saf kısımlar. `extract_items` DB ister: BURADA KAPSANMAZ (needs DB)."""
from __future__ import annotations

import pytest

from app.core.benchmark import content_pairs as cp
from app.core.benchmark.content_pairs import ContentPairError

FACTS = "Firma 2010'dan beri kuruludur."
LINE = "**Tercih:** [ ] A  [ ] B  [ ] Berabere"


def rsa(kws, heads=None, descs=None, gid=1, facts=FACTS):
    return cp.build_rsa_item(
        {"group_id": gid, "group_name": "g", "target_keywords": kws,
         "headlines": heads or ["Bir", "İki", "Üç"], "descriptions": descs or ["Açıklama bir", "Açıklama iki"]},
        ws=1, grounding_facts=facts)


def blog(kid, **over):
    row = {"id": kid, "keyword_id": kid, "title": "Başlık", "meta_description": "Meta", "intro_paragraph": "Giriş",
           "body_content": "Gövde", "subheadings": ["a"], "faq_items": [], "keyword_text": f"kelime{kid}"}
    row.update(over)
    return cp.build_blog_item(row, ws=1)


def social_row(**over):
    row = {"id": 1, "idea_id": 1, "idea_title": "Fikir", "idea_description": "d", "keyword_text": "Kahve Makinesi",
           "hooks": [{"text": "Merhaba dünya", "style": "question"}], "caption": "Bir açıklama",
           "cta_text": "Hemen bak", "hashtags": ["a", "b", "c", "d", "e"], "format_payload": None,
           "target": {"id": 1, "platform": "instagram", "content_format": "post"}}
    row.update(over)
    return row


# ── hard rules ──
def test_rsa_hard_rules_limits_counts_and_claims():
    assert rsa(["kahve"])["hard_rule_ok"]
    bad = rsa(["kahve"], heads=["x" * 31, "b", "c"], descs=["d" * 91, "e"])
    assert not bad["hard_rule_ok"] and {"headline1>30", "description1>90"} <= set(bad["hard_rule_violations"])
    assert "headlines<3" in rsa(["k"], heads=["a", "b"])["hard_rule_violations"]
    claim = rsa(["kahve"], heads=["%50 indirim", "b", "c"])
    assert "headline1:ungrounded_claim" in claim["hard_rule_violations"]
    assert rsa(["kahve"], heads=["2010'dan beri kuruludur", "b", "c"])["hard_rule_ok"]  # sayı facts'te
    assert rsa(["2026 kahve"], heads=["2026 kahve", "b", "c"])["hard_rule_ok"]  # keyword muafiyeti


def test_rsa_key_is_sorted_normalized_keyword_tuple_and_order_independent():
    a, b = rsa(["Şeker", "Kahve"]), rsa(["kahve", "SEKER"], gid=2)
    assert a["key"] == b["key"] == ("kahve", "seker") and a["key_str"] == b["key_str"]
    assert rsa([])["meta"]["key_fallback"] == "group_name"


def test_blog_required_fields_and_key():
    ok = blog(7)
    assert ok["key"] == 7 and ok["hard_rule_ok"]
    bad = blog(8, body_content="  ", meta_description="m" * 161)
    assert set(bad["hard_rule_violations"]) == {"missing:body_content", "meta_description>160"}


def test_social_hard_rules_use_real_validator_and_key_has_ordinal():
    ok = cp.build_social_item(social_row(), ws=1, ordinal=2)
    assert ok["hard_rule_ok"], ok["hard_rule_violations"]
    assert ok["key"] == ("kahve makinesi", "instagram", "post", 2)
    bad = cp.build_social_item(social_row(caption=""), ws=1, ordinal=1)
    assert not bad["hard_rule_ok"] and bad["hard_rule_violations"][0].startswith("social_contract:")
    wrong = cp.build_social_item(social_row(target={"id": 1, "platform": "x", "content_format": "nope"}),
                                 ws=1, ordinal=1)
    assert not wrong["hard_rule_ok"]


# ── ortak katman ──
def _idx(items):
    return {i["key_str"]: i for i in items}


def test_common_layer_and_only_lists():
    ref = {"blog": _idx([blog(1), blog(2), blog(3)]), "rsa": _idx([rsa(["a"])])}
    cand = {"blog": _idx([blog(2), blog(3), blog(4)]), "social": {}}
    common, only_ref, only_cand = cp.common_layer(ref, cand)
    assert [t[0] for t in common["blog"]] == ["1|2", "1|3"]
    assert [i["key"] for i in only_ref["blog"]] == [1] and [i["key"] for i in only_cand["blog"]] == [4]
    assert common["rsa"] == [] and len(only_ref["rsa"]) == 1 and common["social"] == []


# ── kör normalizasyon ──
def test_normalize_for_blind_strips_fingerprints_identically():
    t = "“Merhaba” — Claude yazdı…   \n\n\n\nGemini ve Google  ‘x’  \t"
    n = cp.normalize_for_blind(t)
    assert n == "\"Merhaba\" - yazdı...\n\nve Google 'x'"   # "google" korunur (Google Ads mesru marka)
    assert cp.normalize_for_blind(n) == n and cp.normalize_for_blind("") == ""
    assert cp.normalize_for_blind(None) == ""


# ── kör çiftler ──
def make_common(n_per=40, fmts=("blog", "rsa")):
    common = {}
    for f in fmts:
        trips = []
        for i in range(n_per):
            ref, cand = blog(i), blog(i)
            ref["format"] = cand["format"] = f
            ref["text_blocks"] = [("Başlık", "REF metni")]
            cand["text_blocks"] = [("Başlık", "CAND metni")]
            cand["hard_rule_ok"] = i % 5 != 0
            trips.append((ref["key_str"], ref, cand))
        common[f] = trips
    return common


def test_blind_pairs_deterministic_balanced_and_secret_separate():
    common = make_common()
    p1, s1, t1 = cp.build_blind_pairs(common, seed=42)
    p2, s2, _ = cp.build_blind_pairs(common, seed=42)
    _, s3, _ = cp.build_blind_pairs(common, seed=43)
    assert p1 == p2 and s1 == s2 and s1 != s3
    assert t1 == {"blog": {"n_common": 40, "n_judge": 30, "n_patron": 15},
                  "rsa": {"n_common": 40, "n_judge": 30, "n_patron": 15}}
    assert len(p1) == 60 and sum(1 for p in p1 if p["patron"]) == 30
    assert all(set(p) == {"pair_id", "format", "ws", "key_label", "patron", "A", "B"} for p in p1)
    assert "ref" not in repr(p1).lower().replace("ref metni", "")  # provider izi yok
    a_cand = sum(1 for p in p1 if s1[p["pair_id"]]["A"] == "cand")
    assert 25 <= a_cand <= 35                                   # yarı-yarıya dengeli
    for p in p1:                                                # A/B sır haritasındaki tarafı taşıyor
        side = s1[p["pair_id"]]
        assert ("CAND" in p["A"][0][1]) == (side["A"] == "cand") and {side["A"], side["B"]} == {"ref", "cand"}
    first15 = [p for p in p1 if p["format"] == "blog"][:15]
    assert all(p["patron"] for p in first15)


def test_blind_pairs_small_set_uses_all():
    _, _, t = cp.build_blind_pairs(make_common(n_per=9, fmts=("rsa",)), seed=1)
    assert t["rsa"] == {"n_common": 9, "n_judge": 9, "n_patron": 9}


def test_itt_hard_rule_pairs_cover_non_sampled():
    common = make_common(n_per=40, fmts=("blog",))
    _, secret, _ = cp.build_blind_pairs(common, seed=3)
    rest = cp.itt_hard_rule_pairs(common, secret)
    assert len(rest) == 10 and all(r["patron_pref"] is None and r["judge_pref"] is None for r in rest)


# ── kartlar ──
def _cards():
    pairs, secret, _ = cp.build_blind_pairs(make_common(n_per=20, fmts=("blog",)), seed=5)
    return pairs, secret


MARKS = {"A": "**Tercih:** [x] A  [ ] B  [ ] Berabere", "B": "**Tercih:** [ ] A  [x] B  [ ] Berabere",
         "Berabere": "**Tercih:** [ ] A  [ ] B  [x] Berabere", "AB": "**Tercih:** [x] A  [x] B  [ ] Berabere"}


def _check(md, pid, mark):
    """pid kartının Tercih satırını MARKS[mark] ile değiştirir."""
    head, rest = md.split(f"## Çift {pid} —", 1)
    return head + f"## Çift {pid} —" + rest.replace(LINE, MARKS[mark], 1)


def test_cards_roundtrip_and_unblind(tmp_path):
    pairs, secret = _cards()
    out = tmp_path / "cards.md"
    md = cp.render_patron_cards(pairs, str(out))
    assert out.read_bytes().decode("utf-8") == md and b"\r" not in out.read_bytes()
    ids = [p["pair_id"] for p in pairs if p["patron"]]
    assert md.count("**Tercih:**") == len(ids) == 15 and "Marka/ürün sadakati (×3)" in md
    assert "Bilgi yeterliliği" in md
    boxes = ["A", "B", "Berabere"]
    for i, pid in enumerate(ids):
        md = _check(md, pid, boxes[i % 3])
    md = md.replace("[marka_sadakati]: A=_ B=_", "[marka_sadakati]: A=4 B=2", 1)
    parsed = cp.parse_patron_cards(md, expected_ids=ids)
    assert [parsed[p]["pref"] for p in ids[:3]] == ["A", "B", "tie"]
    assert parsed[ids[0]]["scores"]["marka_sadakati"] == {"A": 4, "B": 2}
    assert parsed[ids[0]]["scores"]["ozgunluk"] == {"A": None, "B": None}
    un = cp.unblind(parsed, secret)
    assert un[ids[0]] == secret[ids[0]]["A"] and un[ids[1]] == secret[ids[1]]["B"] and un[ids[2]] == "tie"


def test_parse_cards_fail_closed():
    pairs, _ = _cards()
    md = cp.render_patron_cards(pairs)
    ids = [p["pair_id"] for p in pairs if p["patron"]]
    with pytest.raises(ContentPairError, match="bir tercih"):
        cp.parse_patron_cards(md)                                       # hiç işaret yok
    with pytest.raises(ContentPairError, match="bir tercih"):
        cp.parse_patron_cards(_check(md, ids[0], "AB"))  # iki işaret
    first_only = md[: md.index(f"## Çift {ids[1]}")]
    marked = _check(first_only, ids[0], "A")
    with pytest.raises(ContentPairError, match="eksik kart"):
        cp.parse_patron_cards(marked, expected_ids=ids)
    with pytest.raises(ContentPairError, match="puan"):
        cp.parse_patron_cards(marked.replace("[marka_sadakati]: A=_", "[marka_sadakati]: A=9", 1))
    with pytest.raises(ContentPairError, match="Tercih"):
        cp.parse_patron_cards(first_only.replace("**Tercih:**", "Tercih:"))
    with pytest.raises(ContentPairError, match="iki kez"):
        cp.parse_patron_cards(marked + marked)


def test_unblind_rejects_unknown_and_invalid():
    sec = {"P1": {"A": "ref", "B": "cand"}}
    assert cp.unblind({"P1": "B"}, sec) == {"P1": "cand"}
    assert cp.unblind({"P1": {"pref": "tie"}}, sec) == {"P1": "tie"}
    with pytest.raises(ContentPairError):
        cp.unblind({"P2": "A"}, sec)
    with pytest.raises(ContentPairError):
        cp.unblind({"P1": "C"}, sec)
