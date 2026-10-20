"""content_pairs.extract_items'in SQL yolu GERÇEK Postgres'te (ücretsiz, ağsız).

Fixture: test_hybrid_run_db'deki sahte-AI uçtan uca koşusu (G/G/G referans + Aday), aynı
workspace'te art arda. Tek test: iki koşu pahalı, bölünürse her parça yeniden koşardı.
"""
from __future__ import annotations

import pytest

import app.core.benchmark.content_pairs as cp
from app.core.channel.brand_defense import load_product_definition
from app.database.models import AdHeadline, ScoringRun
# fixture'lar (world/open_plan/_ledger_in_tmp) pytest tarafından bu modülde bulunsun diye içe alınır
from tests.integration.test_hybrid_run_db import (  # noqa: F401
    HybridFakeAI, _ledger_in_tmp, open_plan, run_cfg, sealed_plan, world,
)


def test_extract_items_pairs_and_cards_on_real_db(db_session, world, open_plan, monkeypatch):
    db = db_session
    res_ref, _ = run_cfg(db, world, open_plan, "G/G/G", HybridFakeAI(), monkeypatch)
    res_cand, _ = run_cfg(db, world, open_plan, "Aday", HybridFakeAI(), monkeypatch)
    ref_id, cand_id = res_ref["scoring_run_id"], res_cand["scoring_run_id"]
    assert ref_id != cand_id
    db.expire_all()

    def items(run_id):
        run = db.query(ScoringRun).get(run_id)
        facts = load_product_definition(db, run) or ""   # _grounding_context ile aynı (trusted USP boş)
        return cp.extract_items(db, run_id, ws=world.id, grounding_facts=facts)

    ref, cand = items(ref_id), items(cand_id)

    # (1) üç format, öğe alanları, RSA sert kuralları
    for name, got in (("ref", ref), ("cand", cand)):
        for fmt in cp.FORMATS:
            assert got[fmt], f"{name}: {fmt} için öğe yok (fixture üretmedi mi?)"
            for it in got[fmt].values():
                assert it["text_blocks"] and "hard_rule_ok" in it and isinstance(it["meta"], dict)
        for it in got["rsa"].values():
            assert it["hard_rule_ok"], (name, it["key_label"], it["hard_rule_violations"])

    # (2) ortak katman
    common, only_ref, only_cand = cp.common_layer(ref, cand)
    for fmt in cp.FORMATS:
        n = len(common[fmt])
        assert n <= min(len(ref[fmt]), len(cand[fmt]))
        assert n + len(only_ref[fmt]) == len(ref[fmt])
        assert n + len(only_cand[fmt]) == len(cand[fmt])
    assert any(common[f] for f in cp.FORMATS), "aynı kelimelerle hiçbir formatta ortak anahtar yok"

    # (3) kör çift -> kart -> ayrıştırma
    public, secret, table = cp.build_blind_pairs(common, seed=7)
    assert public and set(secret) == {p["pair_id"] for p in public}
    for fmt, t in table.items():
        assert t["n_common"] == len(common[fmt]) and t["n_judge"] <= t["n_common"]
    md = cp.render_patron_cards(public)
    patron_ids = [p["pair_id"] for p in public if p["patron"]]
    assert patron_ids
    filled = md.replace("[ ] A ", "[x] A ")    # her karta A işaretle
    parsed = cp.parse_patron_cards(filled, expected_ids=patron_ids)
    assert set(parsed) == set(patron_ids) and all(v["pref"] == "A" for v in parsed.values())
    assert set(cp.unblind(parsed, secret).values()) <= {"cand", "ref"}

    # (4) false-green savunması: DB kolonu varchar(30) oldugu icin 31 karakter
    # yazilamaz (sinir DB'de de zorlanir); dayanaksiz sayisal iddia ile
    # `find_ungrounded_claims` yolu dusurulur.
    key_str, item = next(iter(ref["rsa"].items()))
    gid = item["meta"]["group_id"]
    h = (db.query(AdHeadline).filter(AdHeadline.ad_group_id == gid)
         .order_by(AdHeadline.sort_order, AdHeadline.id).first())
    h.headline_text = "%100 Garantili Kazanc"            # 21 karakter, iddia
    db.commit()
    again = items(ref_id)["rsa"][key_str]
    assert again["hard_rule_ok"] is False
    assert any(v.endswith(":ungrounded_claim") for v in again["hard_rule_violations"]),         again["hard_rule_violations"]
