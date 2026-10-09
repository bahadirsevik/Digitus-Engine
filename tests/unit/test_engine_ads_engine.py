"""ADS Niche motoru — birim testleri + tuzak nobetcileri.

`app/core/engine/ads/formula.py` (`competition_to_r`, `trend_to_t`) ve
`app/core/engine/ads/engine.py` (`preprocess`, `score`, `family_terms`,
`selection_score`, `order_pool`, `run_niche_engine`) icin. Golden parity
`tests/unit/test_engine_ads_parity.py` dosyasindadir; bu dosya BIRIM
seviyesinde davranisi ve `scripts/nihai2_motor.py`'nin sapmali kopyasindan
(trend [-1,+3] kirpmasi, rekabet korumasiz) AYRISTIRAN nobetcileri tasir.

Ucretli saglayici cagrisi YOK.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from app.core.engine.ads import engine as ENGINE_MODULE
from app.core.engine.ads.engine import (
    LN_FLOOR,
    NICHE_COMPETITION_COEF,
    NOMINAL_CUT,
    family_terms,
    order_pool,
    preprocess,
    run_niche_engine,
    score,
    selection_score,
)
from app.core.engine.ads.formula import competition_to_r, trend_to_t

# ---------------------------------------------------------------------------
# trend_to_t
# ---------------------------------------------------------------------------


def test_trend_to_t_minus_50_percent_clips_to_zero():
    assert trend_to_t(-50.0) == 0.0


def test_trend_to_t_400_percent_clips_to_one():
    assert trend_to_t(400.0) == 1.0


def test_trend_to_t_50_percent_is_half():
    assert trend_to_t(50.0) == 0.5


def test_trend_to_t_null_is_zero_and_flag_incremented():
    flags: dict = {}
    assert trend_to_t(None, flags) == 0.0
    assert flags["trend_null"] == 1
    assert trend_to_t(None, flags) == 0.0
    assert flags["trend_null"] == 2  # ikinci None de sayilir


def test_trend_to_t_deviant_negative150_percent_sentinel_against_nihai2_motor_clamp():
    """NOBETCI: `scripts/nihai2_motor.py` satir 63 trendi
    `max(-1.0, min(3.0, T3/100))` ile [-1,+3] araligina KIRPAR (plan disi
    sapma, bkz. ADS_NIHAI_NICHE_KOD_HARITASI.md "Tuzaklar"). Kanonik
    donusum ise [0,1]'e kirpar: `min(max(T3/100,0),1)`.

    T3 = -150 (%-150) icin:
      * kanonik (bu modul)     -> min(max(-1.5, 0), 1) = 0.0
      * sapmali nihai2_motor   -> max(-1.0, min(3.0, -1.5)) = -1.0

    Uretim kodu yanlislikla sapmali kopyayi taklit etseydi bu test 0.0 != -1.0
    farkiyla KIRILIRDI.
    """
    result = trend_to_t(-150.0)
    assert result == 0.0
    assert result != -1.0, (
        "trend_to_t [-1,+3] sapmali kirpmasini (nihai2_motor) taklit ediyor "
        "gibi gorunuyor — kanonik davranis [0,1]'dir"
    )


def test_trend_to_t_deviant_350_percent_sentinel_against_nihai2_motor_clamp():
    """Ayni nobetci, ust sinirda: T3=%350 -> kanonik 1.0, sapmali 3.0."""
    result = trend_to_t(350.0)
    assert result == 1.0
    assert result != 3.0


# ---------------------------------------------------------------------------
# competition_to_r
# ---------------------------------------------------------------------------


def test_competition_to_r_null_is_neutral_half_and_flag_incremented():
    flags: dict = {}
    assert competition_to_r(None, flags) == 0.5
    assert flags["competition_null"] == 1


def test_competition_to_r_lower_and_upper_boundary_pass_through():
    assert competition_to_r(0.0) == 0.0
    assert competition_to_r(1.0) == 1.0


def test_competition_to_r_above_one_raises_value_error():
    with pytest.raises(ValueError, match=r"competition 1\.01 > 1"):
        competition_to_r(1.01)


# ---------------------------------------------------------------------------
# preprocess — hacim dusmesi
# ---------------------------------------------------------------------------


def _row(keyword_id, *, volume, rel, intent=0.5, family="f", r=0.5, t=0.0):
    return {"keyword_id": keyword_id, "volume": volume, "Rel": rel,
            "Intent": intent, "family": family, "R": r, "T": t}


def test_preprocess_drops_zero_and_none_volume_rows():
    rows = [
        _row(1, volume=0, rel=0.9),
        _row(2, volume=None, rel=0.9),
        _row(3, volume=100, rel=0.9),
        _row(4, volume=50, rel=0.8),
    ]
    kept, checks = preprocess(rows, cut=0.0)
    assert {r["keyword_id"] for r in kept} == {3, 4}
    assert checks["hacim_dusen"] == 2


# ---------------------------------------------------------------------------
# preprocess — kesim sinir esitligi (SINIR DEGERINI paylasanlarin HEPSI kalir)
# ---------------------------------------------------------------------------


def test_preprocess_boundary_tie_keeps_all_rows_sharing_the_cut_value():
    """4 satir, cut=0.5 -> target=2. Siralama (Rel,id): (0.1,1),(0.2,2),
    (0.2,3),(0.3,4). Sinir = ordered[target-1]=ordered[1]=0.2. Nominal
    hedef 2 satir atilmaliydi (id1,id2) ama id3 sinirda id2'yle ESIT
    oldugu icin o da KALIR — gerceklesen atilan yalniz 1 (id1)."""
    rows = [
        _row(1, volume=100, rel=0.1),
        _row(2, volume=100, rel=0.2),
        _row(3, volume=100, rel=0.2),
        _row(4, volume=100, rel=0.3),
    ]
    kept, checks = preprocess(rows, cut=0.5, ln_floor=0.2)

    assert checks["kesim_sinir_degeri"] == 0.2
    assert checks["hedeflenen_atilan"] == 2
    assert checks["gerceklesen_atilan"] == 1        # sinir esitligi yuzunden HEDEFTEN AZ
    assert checks["kalan_satir"] == 3
    assert {r["keyword_id"] for r in kept} == {2, 3, 4}


def test_preprocess_boundary_tie_also_applies_at_capacity_edge_with_many_ties():
    """5 satir, uc tanesi AYNI sinir Rel degerinde -> hepsi kalmali."""
    rows = [
        _row(1, volume=100, rel=0.10),
        _row(2, volume=100, rel=0.30),
        _row(3, volume=100, rel=0.30),
        _row(4, volume=100, rel=0.30),
        _row(5, volume=100, rel=0.90),
    ]
    # target = int(5*0.5) = 2 -> ordered[1] = (0.30, 2) -> sinir 0.30
    kept, checks = preprocess(rows, cut=0.5, ln_floor=0.2)
    assert checks["kesim_sinir_degeri"] == 0.30
    assert {r["keyword_id"] for r in kept} == {2, 3, 4, 5}
    assert checks["gerceklesen_atilan"] == 1  # yalniz id1 (0.10) atildi


# ---------------------------------------------------------------------------
# preprocess — Ln (tek hacimde 0,5) ve Ln' (0,20 + 0,80*Ln) uclari
# ---------------------------------------------------------------------------


def test_preprocess_single_volume_gives_ln_half():
    rows = [_row(1, volume=500, rel=0.5), _row(2, volume=500, rel=0.6)]
    kept, checks = preprocess(rows, cut=0.0, ln_floor=0.2)
    for row in kept:
        assert row["Ln"] == pytest.approx(0.5)
        assert row["Ln_prime"] == pytest.approx(0.2 + 0.8 * 0.5)  # 0.6
    assert checks["Ln_araligi"] == [0.5, 0.5]


def test_preprocess_ln_prime_floor_and_ceiling_at_log_volume_extremes():
    """cut=0.0 ile kesim adimi bilincli devre disi — yalniz Ln'/Ln' testi.
    volume 10 (log10=1) ve 1000 (log10=3): span=2. min -> Ln=0 -> Ln'=floor
    (0.2); max -> Ln=1 -> Ln'=1.0 (tavan)."""
    rows = [_row(1, volume=10, rel=0.5), _row(9, volume=1000, rel=0.5)]
    kept, checks = preprocess(rows, cut=0.0, ln_floor=0.2)
    by_id = {r["keyword_id"]: r for r in kept}

    assert by_id[1]["Ln"] == pytest.approx(0.0)
    assert by_id[1]["Ln_prime"] == pytest.approx(0.2)
    assert by_id[9]["Ln"] == pytest.approx(1.0)
    assert by_id[9]["Ln_prime"] == pytest.approx(1.0)
    assert checks["Ln_araligi"] == [0.0, 1.0]
    assert checks["Ln_prime_araligi"] == [0.2, 1.0]


# ---------------------------------------------------------------------------
# preprocess — target <= 0 (kucuk evren): hic kesim yapilmaz
# ---------------------------------------------------------------------------


def test_preprocess_small_universe_target_zero_skips_cut_entirely():
    """4 satir * 0.20 = 0.8 -> int() = 0 -> kesim YOK, hepsi kalir, sinir None."""
    rows = [_row(i, volume=100, rel=r) for i, r in
            enumerate([0.1, 0.2, 0.3, 0.4], start=1)]
    kept, checks = preprocess(rows, cut=NOMINAL_CUT, ln_floor=LN_FLOOR)

    assert checks["kesim_sinir_degeri"] is None
    assert checks["hedeflenen_atilan"] == 0
    assert checks["gerceklesen_atilan"] == 0
    assert checks["kalan_satir"] == 4
    assert {r["keyword_id"] for r in kept} == {1, 2, 3, 4}


def test_preprocess_small_universe_target_zero_keeps_rows_with_none_relevance():
    """target<=0 dalinda filtre `r["Rel"] is not None` UYGULANMAZ (yalniz
    target>0 dalindaki liste kavramasinda var) — Rel'i eksik bir satir da
    kesim yapilmayan kucuk evrende KALIR, ama `Rel_eksik` sayaci onu yakalar."""
    rows = [
        _row(1, volume=100, rel=0.1),
        _row(2, volume=100, rel=None),
        _row(3, volume=100, rel=0.3),
        _row(4, volume=100, rel=0.4),
    ]
    kept, checks = preprocess(rows, cut=NOMINAL_CUT, ln_floor=LN_FLOOR)
    assert {r["keyword_id"] for r in kept} == {1, 2, 3, 4}
    assert checks["Rel_eksik"] == 1


# ---------------------------------------------------------------------------
# preprocess — checks sozlugu eksik/dogru alanlar
# ---------------------------------------------------------------------------


def test_preprocess_checks_dict_reports_missing_rel_intent_family_and_nan_free():
    rows = [
        _row(1, volume=100, rel=0.5, intent=None, family="f"),
        _row(2, volume=100, rel=0.6, intent=0.5, family=None),
        _row(3, volume=100, rel=0.7, intent=0.5, family="f"),
    ]
    kept, checks = preprocess(rows, cut=0.0, ln_floor=0.2)
    assert checks["kalan_satir"] == 3
    assert checks["Rel_eksik"] == 0
    assert checks["Intent_eksik"] == 1
    assert checks["aile_eksik"] == 1
    assert checks["nan_yok"] is True
    assert checks["gerceklesen_kesim_orani"] == 0.0


# ---------------------------------------------------------------------------
# family_terms — MFV = Core / ailedeki en yuksek Core
# ---------------------------------------------------------------------------


def test_family_terms_mfv_is_core_over_family_max_core():
    kept = [
        {"keyword_id": 1, "family": "f", "Core": 0.8, "Rel": 0.9},
        {"keyword_id": 2, "family": "f", "Core": 0.4, "Rel": 0.5},
    ]
    family_terms(kept)
    by_id = {r["keyword_id"]: r for r in kept}
    assert by_id[1]["MFV"] == pytest.approx(1.0)     # kendisi ailenin tepesi
    assert by_id[2]["MFV"] == pytest.approx(0.5)      # 0.4 / 0.8
    expected_relq = (0.9 + 0.5) / 2
    assert by_id[1]["FamilyRelQ"] == pytest.approx(expected_relq)
    assert by_id[2]["FamilyRelQ"] == pytest.approx(expected_relq)


def test_family_terms_single_member_family_mfv_is_one():
    kept = [{"keyword_id": 1, "family": "solo", "Core": 0.3, "Rel": 0.7}]
    family_terms(kept)
    assert kept[0]["MFV"] == pytest.approx(1.0)
    assert kept[0]["FamilyRelQ"] == pytest.approx(0.7)


def test_family_terms_zero_top_core_gives_mfv_one_for_all_members():
    """Ailenin tepesi Core=0 ise (Rel veya Intent sifir) sifira bolme
    yerine MFV=1.0 hem tepe hem digerleri icin."""
    kept = [
        {"keyword_id": 1, "family": "z", "Core": 0.0, "Rel": 0.0},
        {"keyword_id": 2, "family": "z", "Core": 0.0, "Rel": 0.0},
    ]
    family_terms(kept)
    assert kept[0]["MFV"] == pytest.approx(1.0)
    assert kept[1]["MFV"] == pytest.approx(1.0)
    assert kept[0]["FamilyRelQ"] == pytest.approx(0.0)


def test_family_terms_family_relq_excludes_members_eliminated_by_cut():
    """KRITIK: FamilyRelQ kesimden SAG CIKAN uyelerin ortalama Rel'idir.
    4 satir, cut=0.5 -> target=2, sinir=0.10 (id2). id1 (fam, Rel=0.05)
    KESILIR; ayni ailenin kalan iki uyesi (id3=0.80, id4=0.90) FamilyRelQ'yu
    yalniz KENDI aralarinda hesaplamali, id1'in 0.05'i ortalamaya GIRMEMELI.
    """
    rows = [
        _row(1, volume=100, rel=0.05, family="fam"),
        _row(2, volume=100, rel=0.10, family="other"),
        _row(3, volume=100, rel=0.80, family="fam"),
        _row(4, volume=100, rel=0.90, family="fam"),
    ]
    kept, checks = preprocess(rows, cut=0.5, ln_floor=0.2)
    assert {r["keyword_id"] for r in kept} == {2, 3, 4}   # id1 elendi

    score(kept, competition_coef=NICHE_COMPETITION_COEF)
    family_terms(kept)

    fam_members = {r["keyword_id"]: r for r in kept if r["family"] == "fam"}
    assert set(fam_members) == {3, 4}

    expected_relq_survivors_only = (0.80 + 0.90) / 2
    expected_relq_if_bug_included_eliminated = (0.05 + 0.80 + 0.90) / 3

    for row in fam_members.values():
        assert row["FamilyRelQ"] == pytest.approx(expected_relq_survivors_only)
        assert row["FamilyRelQ"] != pytest.approx(
            expected_relq_if_bug_included_eliminated
        ), "FamilyRelQ kesimde elenen uyeyi ICERIYOR gibi gorunuyor — kusur"


# ---------------------------------------------------------------------------
# order_pool — esitlik bozuculari: (-Selection, -hacim, keyword_id)
# ---------------------------------------------------------------------------


def test_order_pool_tie_breaks_by_volume_desc_then_keyword_id_asc():
    rows = [
        {"keyword_id": 5, "volume": 50, "Selection": 0.5},
        {"keyword_id": 3, "volume": 100, "Selection": 0.5},
        {"keyword_id": 1, "volume": 100, "Selection": 0.5},
        {"keyword_id": 9, "volume": 10, "Selection": 0.9},
    ]
    pool = order_pool(rows)
    assert [r["keyword_id"] for r in pool] == [9, 1, 3, 5]


# ---------------------------------------------------------------------------
# run_niche_engine — MFV/FamilyRelQ ACIK, rekabet katsayisi 0.5
# ---------------------------------------------------------------------------


def test_run_niche_engine_checks_report_mfv_and_familyrelq_open():
    rows = [_row(1, volume=100, rel=0.5, family="f1"),
            _row(2, volume=200, rel=0.6, family="f2")]
    pool, checks = run_niche_engine(rows)
    assert checks["mfv_acik"] is True
    assert checks["familyrelq_acik"] is True
    assert len(pool) == 2


def test_run_niche_engine_uses_0_5_competition_coefficient_by_default():
    """Tek satirlik tek-uyeli aile: MFV=FamilyRelQ/Rel=Rel (kendi Rel'i),
    boylece Selection'i tamamen manuel formulle karsilastirabiliriz ve
    0,5 katsayisinin GERCEKTEN kullanildigini kanitlariz."""
    rows = [{"keyword_id": 1, "volume": 1000, "Rel": 0.8, "Intent": 0.6,
            "family": "f1", "R": 0.4, "T": 0.5}]
    pool, _checks = run_niche_engine(rows)

    # tek satir -> target<=0 -> kesim yok; tek hacim -> Ln=0.5 -> Ln'=0.6
    ln_prime = 0.2 + 0.8 * 0.5
    expected_core = (0.8 ** 2) * (0.6 ** 2) * ln_prime * (1 - 0.5 * 0.4) * (1 + 0.2 * 0.5)
    expected_selection = expected_core * 1.0 * 0.8   # MFV=1 (tek uye), FamilyRelQ=Rel

    assert pool[0]["Core"] == pytest.approx(expected_core)
    assert pool[0]["Selection"] == pytest.approx(expected_selection)

    # coef=1.0 olsaydi farkli bir Core cikardi (nobetci: katsayi GERCEKTEN 0,5)
    wrong_core_if_coef_were_one = (
        (0.8 ** 2) * (0.6 ** 2) * ln_prime * (1 - 1.0 * 0.4) * (1 + 0.2 * 0.5)
    )
    assert pool[0]["Core"] != pytest.approx(wrong_core_if_coef_were_one)


def test_run_niche_engine_competition_coef_override_changes_core():
    rows = [{"keyword_id": 1, "volume": 1000, "Rel": 0.8, "Intent": 0.6,
            "family": "f1", "R": 0.4, "T": 0.5}]
    pool_default, _ = run_niche_engine(rows)
    pool_override, _ = run_niche_engine([dict(rows[0])], competition_coef=1.0)
    assert pool_default[0]["Core"] != pytest.approx(pool_override[0]["Core"])


# ---------------------------------------------------------------------------
# Kapsam nobetcisi: breadth/balanced motorlari ve otomatik yonlendirme
# URETIME ALINMADI (plan karari) — kaynak metninde de OLMAMALI.
# ---------------------------------------------------------------------------


def _module_source_without_module_docstring(module) -> str:
    """Modul DOCSTRING'i haric kaynak metni.

    Docstring'in kendisi ("URETIME ALINMAYANLAR: breadth ve balanced...")
    bilerek bu kelimeleri ANMAK icindir — kod taramasi bu yuzden YALNIZ
    docstring SONRASI govdeyi kapsamali, aksi halde dogru aciklamayi
    yanlis-pozitif olarak isaretler.
    """
    source = inspect.getsource(module)
    tree = ast.parse(source)
    docstring_end = 0
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        docstring_end = tree.body[0].end_lineno
    return "\n".join(source.splitlines()[docstring_end:])


def test_engine_module_does_not_contain_breadth_or_balanced_engine_or_auto_routing():
    code_only = _module_source_without_module_docstring(ENGINE_MODULE).lower()
    for forbidden in ("breadth", "balanced", "order_breadth",
                      "assigned", "engines = ", "route_engine",
                      "select_engine", "auto_route", "competition_coef = {"):
        assert forbidden not in code_only, (
            f"'{forbidden}' motor GOVDESINDE bulundu — plan yalniz Niche "
            "kolunu tasimali (breadth/balanced/otomatik yonlendirme YASAK)"
        )

    # Tek genel giris noktasi run_niche_engine'dir; kilitli script'teki
    # polimorfik `run_engine(rows, engine)` imzasi tasinmamis olmali.
    full_source = inspect.getsource(ENGINE_MODULE)
    assert "def run_niche_engine(" in full_source
    assert "def run_engine(" not in full_source

    # `score`/`run_niche_engine` bir motor SECICI parametresi (kilitli
    # script'teki `engine: str`) TASIMAMALI — Niche her zaman sabittir.
    assert "engine" not in inspect.signature(score).parameters
    assert "engine" not in inspect.signature(run_niche_engine).parameters

    # Motor-basina rekabet katsayisi SOZLUGU (kilitli scriptteki
    # `COMPETITION_COEF = {"breadth":..., "balanced":..., "niche":...}`)
    # tasinmamali — Niche icin TEK skaler sabit yeter.
    assert not hasattr(ENGINE_MODULE, "COMPETITION_COEF")
    assert not hasattr(ENGINE_MODULE, "ENGINES")
    assert not hasattr(ENGINE_MODULE, "ASSIGNED")
    assert not hasattr(ENGINE_MODULE, "order_breadth")
    assert not hasattr(ENGINE_MODULE, "run_engine")
    assert ENGINE_MODULE.NICHE_COMPETITION_COEF == 0.5
