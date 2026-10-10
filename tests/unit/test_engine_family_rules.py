"""Family V2 deterministik kural testleri (`app/core/engine/family/rules.py`).

QA gorevi: Motor v3 Faz 2 = Family V2 portu. Bu dosya AI-ciktisi
DEGERLENDIRMESINI (A2B destek kurali) ve deterministik kapanis kurallarini
(validate_families / assert_unmatched_ceiling / finalize_assignments /
dict_sha) test eder. Ucretli saglayici cagrisi YOK — bu modul zaten AI
cagirmiyor (yalniz saf fonksiyonlar).

GUNCELLEME: Bu dosya ilk yazildiginda `validate_families`, `dict_sha`,
`assert_unmatched_ceiling` ve `FamilyStageError` `rules.py`'de TANIMLI
DEGILDI (A2B bolumu duzenlenirken kazara silinmisti); ilgili kusur
raporlandi ve geri kondu. Dosya sonunda ayrica IKI "golden parity" testi
var: biri izlenen `benchmark/ads_nihai_niche_golden_v1.json` uzerinden
`finalize_assignments`'in kilitli aile atamasini BOZMADIGINI HER ZAMAN
dogrular (skip YOK); digeri `benchmark/private/` altindaki (gitignore'da)
dondurulmus sozluk dosyalariyla `dict_sha` esitligini, veri yerelde
yoksa ACIK gerekceli skip ile dogrular.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.engine.family import rules as R

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "benchmark" / "ads_nihai_niche_golden_v1.json"
PRIVATE_DIR = REPO_ROOT / "benchmark" / "private"

# ---------------------------------------------------------------------------
# evaluate_a2b_proposals
# ---------------------------------------------------------------------------


def _rows(*pairs):
    return [{"keyword_id": kid, "keyword_text": text} for kid, text in pairs]


def _proposal(family_id: str, examples: list, **overrides) -> dict:
    payload = {
        "family_id": family_id,
        "family_name": overrides.pop("family_name", family_id),
        "core_need": overrides.pop("core_need", "core need"),
        "solution_type": overrides.pop("solution_type", "solution"),
        "entity": overrides.pop("entity", "entity"),
        "examples": examples,
    }
    payload.update(overrides)
    return payload


def test_evaluate_a2b_proposals_support_two_is_accepted():
    unmatched = _rows((1, "beyaz saç kapatıcı"), (2, "beyaz saç kapatıcı ürün"))
    proposal = _proposal(
        "yeni_aile", ["beyaz saç kapatıcı", "beyaz saç kapatıcı ürün"]
    )

    outcome = R.evaluate_a2b_proposals(
        [proposal], unmatched, existing_family_ids=["mevcut_aile"]
    )

    assert outcome.accepted == {"yeni_aile": [1, 2]}
    assert outcome.rejected == {}
    assert [f["family_id"] for f in outcome.families] == ["yeni_aile"]


def test_evaluate_a2b_proposals_support_one_is_rejected_and_not_added_to_dictionary():
    unmatched = _rows((1, "tek kelime"))
    proposal = _proposal("az_destek", ["tek kelime"])

    outcome = R.evaluate_a2b_proposals([proposal], unmatched, existing_family_ids=[])

    assert outcome.rejected == {"az_destek": [1]}
    assert outcome.accepted == {}
    assert outcome.families == []


def test_evaluate_a2b_proposals_support_zero_is_rejected():
    unmatched = _rows((1, "alakasız kelime"))
    proposal = _proposal("sifir_destek", ["evrende olmayan metin"])

    outcome = R.evaluate_a2b_proposals([proposal], unmatched, existing_family_ids=[])

    assert outcome.rejected == {"sifir_destek": []}
    assert outcome.accepted == {}
    assert outcome.families == []


def test_evaluate_a2b_proposals_duplicate_example_does_not_increase_support():
    unmatched = _rows((1, "aynı metin"))
    proposal = _proposal("tekrar", ["aynı metin", "aynı metin", "aynı metin"])

    outcome = R.evaluate_a2b_proposals([proposal], unmatched, existing_family_ids=[])

    # Uc kez tekrarlanan tek gercek ID -> destek hala 1, kabul EDILMEDI.
    assert outcome.rejected == {"tekrar": [1]}
    assert outcome.accepted == {}


def test_evaluate_a2b_proposals_fabricated_example_falls_to_unmatched_examples_and_does_not_count():
    unmatched = _rows((1, "gerçek metin"), (2, "gerçek metin 2"))
    proposal = _proposal(
        "karisik", ["gerçek metin", "gerçek metin 2", "uydurulmuş örnek"]
    )

    outcome = R.evaluate_a2b_proposals([proposal], unmatched, existing_family_ids=[])

    assert outcome.accepted == {"karisik": [1, 2]}
    assert outcome.unmatched_examples == {"karisik": ["uydurulmuş örnek"]}


def test_evaluate_a2b_proposals_two_different_keyword_ids_with_same_text_both_count():
    unmatched = _rows((1, "aynı metin"), (2, "aynı metin"))
    proposal = _proposal("ikiz", ["aynı metin"])

    outcome = R.evaluate_a2b_proposals([proposal], unmatched, existing_family_ids=[])

    assert outcome.accepted == {"ikiz": [1, 2]}


def test_evaluate_a2b_proposals_existing_family_id_is_rejected_outright():
    unmatched = _rows((1, "x"), (2, "y"))
    proposal = _proposal("mevcut_aile", ["x", "y"])

    outcome = R.evaluate_a2b_proposals(
        [proposal], unmatched, existing_family_ids=["mevcut_aile"]
    )

    assert outcome.rejected == {"mevcut_aile": []}
    assert outcome.accepted == {}
    assert outcome.families == []


def test_evaluate_a2b_proposals_produces_no_keyword_assignment_api_surface():
    """A2B hicbir keyword atamasi URETMEZ — donen nesnede assignment alani yok."""
    outcome = R.evaluate_a2b_proposals([], [], existing_family_ids=[])

    assert outcome.__slots__ == ("accepted", "rejected", "unmatched_examples", "families")
    for forbidden in ("assignment", "assignments", "family_by_id", "keyword_assignments"):
        assert not hasattr(outcome, forbidden)


def test_evaluate_a2b_proposals_as_payload_carries_min_support_and_ids_for_audit():
    unmatched = _rows((1, "a"), (2, "b"), (3, "c"))
    accepted_proposal = _proposal("kabul", ["a", "b"])
    rejected_proposal = _proposal("red", ["c"])

    outcome = R.evaluate_a2b_proposals(
        [accepted_proposal, rejected_proposal], unmatched, existing_family_ids=[]
    )
    payload = outcome.as_payload()

    assert payload["min_support"] == R.NEW_FAMILY_MIN_SUPPORT
    assert payload["accepted"] == {"kabul": [1, 2]}
    assert payload["rejected"] == {"red": [3]}
    assert payload["accepted_family_ids"] == ["kabul"]


# ---------------------------------------------------------------------------
# assert_unmatched_ceiling
# ---------------------------------------------------------------------------


def test_assert_unmatched_ceiling_passes_at_exactly_300():
    R.assert_unmatched_ceiling(300)  # patlamamali (tavan DAHIL gecer)


def test_assert_unmatched_ceiling_raises_above_300():
    with pytest.raises(R.FamilyStageError, match="301"):
        R.assert_unmatched_ceiling(301)


# ---------------------------------------------------------------------------
# validate_families
# ---------------------------------------------------------------------------


def _family(family_id: str, **overrides) -> dict:
    payload = {
        "family_id": family_id,
        "family_name": overrides.pop("family_name", family_id),
        "core_need": overrides.pop("core_need", "need"),
        "solution_type": overrides.pop("solution_type", "solution"),
        "entity": overrides.pop("entity", "entity"),
        "examples": overrides.pop("examples", ["x"]),
    }
    payload.update(overrides)
    return payload


def test_validate_families_empty_list_is_a_problem():
    problems = R.validate_families([])
    assert any("BOS" in p for p in problems)


def test_validate_families_missing_required_field_is_a_problem():
    fam = {"family_id": "x", "family_name": "X"}  # core_need/solution_type/... eksik

    problems = R.validate_families([fam])

    assert any("eksik alan" in p for p in problems)


def test_validate_families_duplicate_family_id_is_a_problem():
    problems = R.validate_families([_family("dup"), _family("dup")])
    assert any("BENZERSIZ" in p for p in problems)


def test_validate_families_clash_with_existing_dictionary_is_a_problem():
    problems = R.validate_families([_family("mevcut")], existing=["mevcut"])
    assert any("CAKISAN" in p for p in problems)


def test_validate_families_unmatched_as_family_name_is_rejected():
    problems = R.validate_families([_family(R.UNMATCHED)])
    assert any("UNMATCHED" in p for p in problems)


def test_validate_families_valid_list_has_no_problems():
    assert R.validate_families([_family("gecerli_aile")]) == []


# ---------------------------------------------------------------------------
# finalize_assignments
# ---------------------------------------------------------------------------


def test_finalize_assignments_unassigned_and_unmatched_become_single():
    families = [_family("fam_a")]
    assignments = {1: "fam_a", 2: R.UNMATCHED}  # 3 hic atanmadi

    result = R.finalize_assignments(assignments, families, universe_ids=[1, 2, 3])

    assert result["family_by_id"][1] == "fam_a"
    assert result["family_by_id"][2] == R.single_family_id(2)
    assert result["family_by_id"][3] == R.single_family_id(3)
    assert sorted(result["single_family_keyword_ids"]) == [2, 3]


def test_finalize_assignments_drops_empty_families():
    families = [_family("fam_a"), _family("fam_bos")]
    assignments = {1: "fam_a"}

    result = R.finalize_assignments(assignments, families, universe_ids=[1])

    assert [f["family_id"] for f in result["families"]] == ["fam_a"]
    assert result["dropped_empty_families"] == ["fam_bos"]


def test_finalize_assignments_dictionary_sha_only_from_kept_families():
    families = [_family("fam_a"), _family("fam_bos")]
    assignments = {1: "fam_a"}

    result = R.finalize_assignments(assignments, families, universe_ids=[1])

    assert result["dictionary_sha256"] == R.dict_sha(result["families"])
    assert result["dictionary_sha256"] != R.dict_sha(families)  # bos aile disarida


def test_finalize_assignments_every_universe_keyword_gets_a_family():
    families = [_family("fam_a")]
    universe_ids = [1, 2, 3]

    result = R.finalize_assignments({}, families, universe_ids=universe_ids)

    assert set(result["family_by_id"]) == set(universe_ids)
    assert all(result["family_by_id"][kid] for kid in universe_ids)


# ---------------------------------------------------------------------------
# dict_sha
# ---------------------------------------------------------------------------


def test_dict_sha_is_independent_of_field_order():
    fam_ordered = _family("x", family_name="X", core_need="n", solution_type="s",
                          entity="e", examples=["a"])
    fam_reordered = {
        "examples": ["a"], "entity": "e", "solution_type": "s",
        "core_need": "n", "family_name": "X", "family_id": "x",
    }

    assert R.dict_sha([fam_ordered]) == R.dict_sha([fam_reordered])


def test_dict_sha_changes_when_content_changes():
    fam_a = _family("x", family_name="X", core_need="n", solution_type="s",
                    entity="e", examples=["a"])
    fam_b = dict(fam_a, core_need="baska ihtiyac")

    assert R.dict_sha([fam_a]) != R.dict_sha([fam_b])


# ---------------------------------------------------------------------------
# Golden parity 1/2 — izlenen (tracked) evren replay, SKIP YOK
# ---------------------------------------------------------------------------


def _load_golden_universe(slug: str) -> dict:
    """`benchmark/ads_nihai_niche_golden_v1.json`'dan {keyword_id: family_id}."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    firm = doc["firmalar"][slug]
    fields = firm["evren_alanlari"]
    assert fields == ["keyword_id", "volume", "competition_raw",
                       "trend_3m_percent_raw", "family_id"], (
        f"{slug}: beklenmeyen evren_alanlari sirasi/icerigi: {fields}"
    )
    kid_idx = fields.index("keyword_id")
    fid_idx = fields.index("family_id")
    return {row[kid_idx]: row[fid_idx] for row in firm["evren"]}


@pytest.mark.parametrize("slug", ["optimice", "dijital", "gr7"])
def test_finalize_assignments_golden_replay_preserves_locked_family_assignment(slug):
    """Izlenen golden dosya — repoya commit'li, bu test ASLA skip EDILMEZ.

    `finalize_assignments`'a golden evrenin kendi (kilitli) atamalari
    `assignments` olarak verilince, donen `family_by_id` GIRDIYLE BIREBIR
    ayni olmali (kilitli aile ataması bozulmuyor); `families=[]` verildigi
    icin `families`/`dropped_empty_families` bos donmeli; tezgahta
    aile_eksik=0 idi, o yuzden tekil aile (`single:<id>`) yolu burada HIC
    tetiklenmemeli ve evrendeki her keyword_id ciktida bulunmali.
    """
    assignments = _load_golden_universe(slug)
    universe_ids = list(assignments)
    assert universe_ids, f"{slug}: golden evren BOS — dosya bozulmus olabilir"

    result = R.finalize_assignments(assignments, families=[], universe_ids=universe_ids)

    assert result["family_by_id"] == assignments
    assert set(result["family_by_id"]) == set(universe_ids)
    assert result["single_family_keyword_ids"] == []
    assert result["families"] == []
    assert result["dropped_empty_families"] == []


# ---------------------------------------------------------------------------
# Golden parity 2/2 — dondurulmus sozluk kimligi (benchmark/private/, gitignore)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug, expected_sha", [
    ("optimice", "196471202f325a6383d83157a4891a601f937ba3adbb694e423253b09438dd7f"),
    ("dijital", "71c357cf7aeefc996d2abbd71aa14aed5551112621528881f309a1f6557940d8"),
    ("gr7", "2637238692b17ac3eb2df9282ec3e89ac623c12120f2bdc7954f10abaa1f66a8"),
])
def test_dict_sha_matches_frozen_private_dictionary_final_sha(slug, expected_sha):
    """`benchmark/private/nihai_aile_v2_<slug>.json` gitignore'da — yerelde
    yoksa (ornegin CI/temiz checkout) ACIK gerekceyle SKIP edilir, sessiz
    gecilmez. Yerelde varsa `dict_sha(families)` hem dosyanin kendi
    `dictionary_sha256_FINAL` alaniyla hem de elle dogrulanmis beklenen
    SHA ile eslesmeli (dosyanin kendisi de bozulmus/degistirilmis olamaz)."""
    path = PRIVATE_DIR / f"nihai_aile_v2_{slug}.json"
    if not path.exists():
        pytest.skip(
            "benchmark/private/ yok — dondurulmus aile ciktisi bu checkout'ta "
            f"bulunmuyor (gitignore'da, beklenen dosya: {path})"
        )

    doc = json.loads(path.read_text(encoding="utf-8"))

    computed = R.dict_sha(doc["families"])
    assert computed == doc["dictionary_sha256_FINAL"]
    assert computed == expected_sha
