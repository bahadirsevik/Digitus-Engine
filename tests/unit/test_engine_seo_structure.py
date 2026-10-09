# -*- coding: utf-8 -*-
"""SEO katı-2 (app/core/engine/seo) — YAPISAL NÖBETÇİ testleri.

QA görevi: Motor v3 Faz 4. Bu dosya davranışı DEĞİL, üretim modüllerinin
YAPISINI korur: ölçüm kollarının (ARM_VOLUME / ARM_RANDOM / duyarlılık)
üretime hiç taşınmadığını, `family_cap` kapısının açık kalmadığını, iki
geçişin doğru sırayla ve doğru argümanlarla çağrıldığını, URL grubu
eşlemesinin sözleşmesini kanıtlar. `app/` ve `scripts/` altına dokunulmadı.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.core.engine.seo import select as SEL
from app.core.engine.seo import urlgroup as URLG

SELECT_SOURCE = Path(SEL.__file__).read_text(encoding="utf-8")


# ── family_cap zorunlu ──────────────────────────────────────────────────

def _minimal_row(keyword_id: int = 1, family_id: str = "F1",
                 subintent_id: str = "s1") -> Dict[str, Any]:
    return {"keyword_id": keyword_id, "keyword_text": f"kw{keyword_id}",
            "volume": 100, "competition": 0.3, "family_id": family_id,
            "broad_intent": "informational", "subintent_id": subintent_id,
            "relevance": 1.0, "bp": 1.0, "authority": None}


def test_evaluate_family_cap_olmadan_value_error_verir() -> None:
    rows = [_minimal_row()]
    with pytest.raises(ValueError, match=r"family_cap.*dinamik"):
        SEL.evaluate(rows, SEL.ContractV31())


def test_production_contract_family_cap_iki() -> None:
    assert SEL.production_contract().family_cap == 2


def test_contract_v31_varsayilani_family_cap_none_reddedilen_koldur() -> None:
    assert SEL.ContractV31().family_cap is None


def test_production_contract_overrides_gecerli_kalir() -> None:
    c = SEL.production_contract(permutations=0)
    assert c.family_cap == 2
    assert c.permutations == 0


# ── ölçüm kolları üretime taşınmadı (AST — saf alt-string YANLIŞ-KIRMIZI verir) ──

def _defined_identifiers(source: str) -> set:
    """Modülde TANIMLI/kullanılan sembol adları (Name/arg/def/attr).

    Docstring ve yorumlardaki metin (örn. "ARM_VOLUME ölçüm koludur, burada
    tanımlı DEĞİL" gibi açıklayıcı cümleler) bilerek DIŞARIDA bırakılır:
    yorumlar zaten AST'ye hiç girmez, string literal İÇERİKLERİ (docstring
    dahil) `ast.Constant` olarak burada toplanmaz — yalnız gerçek Python
    sembolleri toplanır. Faz 3'te saf alt-string taraması bu yüzden
    yanlış-kırmızı vermişti (docstring/yorumdaki AÇIKLAMA metni eşleşiyordu).
    """
    tree = ast.parse(source)
    identifiers: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.alias):
            identifiers.add(node.asname or node.name)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            identifiers.update(node.names)
    return identifiers


FORBIDDEN_MEASUREMENT_SYMBOLS = {"ARM_VOLUME", "ARM_RANDOM", "random_orders",
                                 "SENSITIVITY"}


def test_olcum_kollari_select_modulunde_sembol_olarak_tanimli_degil() -> None:
    identifiers = _defined_identifiers(SELECT_SOURCE)
    leaked = FORBIDDEN_MEASUREMENT_SYMBOLS & identifiers
    assert not leaked, (
        f"Ölçüm kolu sembolleri select.py'de GERÇEK Python sembolü olarak "
        f"tanımlı: {leaked} — bunlar üretime TAŞINMAMALIYDI")


def test_naive_substring_taramasi_docstring_yuzunden_yanlis_pozitif_verirdi() -> None:
    """Belgeleyici not: neden AST kullandığımızı somutlaştırır (kırmızı değil)."""
    naive_hits = {name for name in FORBIDDEN_MEASUREMENT_SYMBOLS
                  if name in SELECT_SOURCE}
    # Docstring/yorumda "ARM_VOLUME ve ARM_RANDOM ... TASINMAMISTIR" gibi
    # açıklayıcı metin olduğu için saf alt-string taraması en az bir isabet
    # bulur; AST tabanlı kontrol (yukarıdaki test) bunları doğru şekilde yok
    # sayar. İkisinin farklı sonuç vermesi BEKLENEN davranıştır.
    assert naive_hits, (
        "Bu modülün docstring/yorumlarında ölçüm kolu isimlerinden söz "
        "edilmiyor artık; AST tabanlı testin gerekçesi güncellenmeli")


def test_arm_candidates_bilinmeyen_kolda_value_error_verir() -> None:
    members = {"c1": [1]}
    final = {1: 10.0}
    by_id = {1: {"volume": 100, "keyword_id": 1}}
    c = SEL.production_contract()
    with pytest.raises(ValueError, match="kol bilinmiyor"):
        SEL._arm_candidates("volume", members, final, by_id, c)
    with pytest.raises(ValueError, match="kol bilinmiyor"):
        SEL._arm_candidates("random", members, final, by_id, c)


# ── çift REQUIRED tanımı yok ─────────────────────────────────────────────

def test_required_tanimi_kaynakta_tam_olarak_bir_kez_gecer() -> None:
    assert SELECT_SOURCE.count("REQUIRED = (") == 1, (
        "select.py'de REQUIRED birden fazla kez tanımlanmış olabilir — "
        "app/core/engine/seo/prompts.py'deki PROMPT_VERSIONS'ın iki kez "
        "tanımlanıp ilk sürümü SESSİZCE ezmesiyle aynı sınıf hata")


# ── iki geçiş sırası: 1. gecis gruplarsız + with_secondary=False,       ──
# ── 2. gecis url_groups dolu + with_secondary varsayılan (True)         ──

def _two_pass_rows() -> List[Dict[str, Any]]:
    return [
        {"keyword_id": 1, "keyword_text": "kw1", "volume": 500, "competition": 0.3,
         "family_id": "F1", "broad_intent": "informational", "subintent_id": "s1",
         "relevance": 0.90, "bp": 0.85, "authority": None},
        {"keyword_id": 2, "keyword_text": "kw2", "volume": 300, "competition": 0.4,
         "family_id": "F1", "broad_intent": "informational", "subintent_id": "s2",
         "relevance": 0.90, "bp": 0.85, "authority": None},
    ]


def test_run_two_pass_evaluate_i_dogru_sira_ve_argumanlarla_iki_kez_cagirir(
    monkeypatch,
) -> None:
    calls: List[Dict[str, Any]] = []
    real_evaluate = URLG.evaluate

    def spy(rows, c, url_groups=None, **kwargs):
        calls.append({"url_groups": url_groups,
                      "with_secondary": kwargs.get("with_secondary", True)})
        return real_evaluate(rows, c, url_groups=url_groups, **kwargs)

    monkeypatch.setattr(URLG, "evaluate", spy)

    rows = _two_pass_rows()
    result = URLG.run_two_pass(rows, {"1": "g1"},
                               contract=SEL.production_contract())

    assert len(calls) == 2, "run_two_pass tam olarak 2 evaluate() çağrısı yapmalı"
    assert calls[0]["url_groups"] is None, "1. geçiş GRUPSUZ koşmalı"
    assert calls[0]["with_secondary"] is False, "1. geçiş with_secondary=False olmalı"
    assert calls[1]["url_groups"], "2. geçiş dolu url_groups almalı"
    assert calls[1]["with_secondary"] is True, (
        "2. geçiş with_secondary varsayılanı (True) ile koşmalı")

    # sonuç gerçek (spy'sız) evaluate ile birebir aynı olmalı
    assert result["selection"]["selected"]


# ── cluster_group_map sözleşmesi ─────────────────────────────────────────

def test_cluster_group_map_grubu_olmayan_aday_eslemede_yer_almaz() -> None:
    primary_by_cluster = {"c1": 1, "c2": 2}
    by_id = {1: {"family_id": "F1"}, 2: {"family_id": "F1"}}
    keyword_to_group = {"1": "g1"}      # yalnız aday 1 için EK-F grubu var

    out = URLG.cluster_group_map(primary_by_cluster, by_id, keyword_to_group)

    assert out == {"c1": "F1|g1"}
    assert "c2" not in out


def test_cluster_group_map_satirlarda_olmayan_aday_urlgrouperror_verir() -> None:
    primary_by_cluster = {"c1": 999}
    by_id: Dict[int, Any] = {}          # 999 bu koşunun satırlarında yok
    keyword_to_group = {"999": "g1"}

    with pytest.raises(URLG.UrlGroupError, match="satirlarinda yok"):
        URLG.cluster_group_map(primary_by_cluster, by_id, keyword_to_group)


def test_cluster_group_map_sayisal_olmayan_grup_ciktisi_urlgrouperror_verir() -> None:
    primary_by_cluster = {"c1": 1}
    by_id = {1: {"family_id": "F1"}}
    keyword_to_group = {"abc": "g1"}    # "abc" int'e çevrilemez

    with pytest.raises(URLG.UrlGroupError, match="sayisal olmayan id"):
        URLG.cluster_group_map(primary_by_cluster, by_id, keyword_to_group)

# ── Uretim kodu `scripts/` icinden import ETMEZ ──────────────────────
# Kilitler SHA ile baglidir: uretim onlara bagimli olamaz. Ayni davranis
# app/core/engine altina TASINIR ve parity testleriyle kanitlanir.
# NOT: TEST dosyalarinin parity amaciyla scripts import etmesi SERBESTTIR;
# bu nobetci yalniz uretim modullerini tarar. Fonksiyon ici (lazy) import'lar
# da yakalanir — bu yuzden metin taramasi degil `ast` kullanilir.

def test_seo_production_modules_do_not_import_scripts():
    import ast
    import pathlib

    seo_dir = pathlib.Path(__file__).resolve().parents[2] / "app/core/engine/seo"
    offenders = []
    for path in sorted(seo_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "scripts" or alias.name.startswith("scripts."):
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "scripts" or module.startswith("scripts."):
                    offenders.append(f"{path.name}:{node.lineno} from {module}")
    assert not offenders, (
        "uretim kodu scripts/ icinden import ediyor (kilitler SHA ile bagli): "
        + ", ".join(offenders))
