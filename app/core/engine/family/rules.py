"""Family V2 — deterministik kurallar (AI YOK).

plan_algoritma_entegrasyonu.md Faz 2. Iki kaynak ayrilir:

  * `validate_families` / `dict_sha` / sabitler — kilitli
    `scripts/nihai_akis_family_v2_run.py` davranisinin uretim kopyasi.
  * A2B YENI AILE KABULU (destek >= 2) — kaynak:
    `benchmark/ads_nihai_niche_algorithm_LOCKED.json` ve
    `algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md` 5b.
    `scripts/nihai2_a2b_kural_uygulamasi.py` KOPYALANMAZ: o dosya
    Scento/Proteinim icin verilmis FIRMAYA OZEL el kararlarinin kaydidir.
    Kanonik `STAGE2B_SCHEMA` kelime bazli hukum URETMEZ (yalniz
    `new_families`); bu yuzden UC-VERDICT soyutlamasi uretim kodunda YOKTUR
    ve sahte `decisions` girdisi URETILMEZ. Destek, A2B'ye gonderilen
    UNMATCHED kelime metinleriyle BIREBIR eslesen GERCEK keyword ID'leri
    uzerinden olculur; A2B asamasinda keyword ATAMASI yapilmaz, atama
    genisletilmis ve DONDURULMUS sozlukle A2C'de yapilir.
  * Uretim kurallari (tezgahta olusmadi, plan Faz 2): A3 sonrasi UNMATCHED
    kalan kelime -> tekil aile `single:<keyword_id>`; A2 sonrasi
    UNMATCHED > 300 -> kosu failed; 0 uyeli aileler sozlukten duser.

Bu modul saglayici cagirmaz, veritabanina yazmaz.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence

UNMATCHED = "UNMATCHED"
ASSIGN_BATCH = 25
STAGE3_BATCH = 15
STAGE1_MAX_TOKENS = 16000
STAGE2_MAX_TOKENS = 3000
STAGE2B_MAX_TOKENS = 12000
STAGE3_MAX_TOKENS = 4000
# A2B tek cagri olmak zorunda; bundan cok UNMATCHED cikarsa A1 basarisizdir.
UNMATCHED_CEILING = 300
REQUIRED = ("family_id", "family_name", "core_need", "solution_type",
            "entity", "examples")
SINGLE_FAMILY_PREFIX = "single:"

class FamilyStageError(RuntimeError):
    """Aile asamasi sozlesmeyi bozdu — kosu durur (sessiz devam YOK)."""


def dict_sha(families: Sequence[Mapping[str, Any]]) -> str:
    """Sozluk kimligi — kilitli `nihai_akis_family_v2_run.dict_sha` ile ayni."""
    payload = json.dumps(
        [{k: f.get(k) for k in REQUIRED} for f in families],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_families(families: Sequence[Mapping[str, Any]],
                      existing: Sequence[str] = ()) -> List[str]:
    """Kilitli `nihai_akis_family_v2_run.validate_families` uretim kopyasi."""
    problems: List[str] = []
    if not families:
        problems.append("aile listesi BOS")
    ids = [f.get("family_id") for f in families]
    for fam in families:
        missing = [k for k in REQUIRED if not fam.get(k)]
        if missing:
            problems.append(f"{fam.get('family_id')!r} eksik alan: {missing}")
    dupes = [k for k, n in Counter(ids).items() if n > 1]
    if dupes:
        problems.append(f"family_id BENZERSIZ DEGIL: {dupes}")
    clash = sorted(set(ids) & set(existing))
    if clash:
        problems.append(f"mevcut sozlukle CAKISAN family_id: {clash}")
    if UNMATCHED in ids:
        problems.append("family_id olarak UNMATCHED kullanilamaz")
    return problems


def assert_unmatched_ceiling(unmatched_count: int) -> None:
    """A2 sonrasi tavan asilirsa kosu DURUR — sessizce devam edilmez."""
    if unmatched_count > UNMATCHED_CEILING:
        raise FamilyStageError(
            f"A2 sonrasi UNMATCHED {unmatched_count} > {UNMATCHED_CEILING} — "
            "A1 taksonomisi evreni kapsamiyor, kosu durduruldu")


# A2B yeni aile kabulu — kaynak: benchmark/ads_nihai_niche_algorithm_LOCKED.json
# ve algoritma/ADS_NIHAI_NICHE_KOD_HARITASI.md 5b
NEW_FAMILY_MIN_SUPPORT = 2


class A2BProposalOutcome:
    """A2B onerilerinin DETERMINISTIK degerlendirmesi — denetlenebilir kayit.

    `accepted` / `rejected`: family_id -> destek veren GERCEK keyword ID'leri
    (sirali, tekil). `unmatched_examples`: sozlukte/evrende karsiligi olmayan
    (uydurulmus veya baska batch'ten gelen) example metinleri.
    """

    __slots__ = ("accepted", "rejected", "unmatched_examples", "families")

    def __init__(self) -> None:
        self.accepted: Dict[str, List[int]] = {}
        self.rejected: Dict[str, List[int]] = {}
        self.unmatched_examples: Dict[str, List[str]] = {}
        self.families: List[Dict[str, Any]] = []

    def as_payload(self) -> Dict[str, Any]:
        """Stage payload'ina yazilan denetim kaydi."""
        return {
            "min_support": NEW_FAMILY_MIN_SUPPORT,
            "accepted": {fid: list(ids) for fid, ids in self.accepted.items()},
            "rejected": {fid: list(ids) for fid, ids in self.rejected.items()},
            "unmatched_examples": {fid: list(v) for fid, v
                                   in self.unmatched_examples.items()},
            "accepted_family_ids": [f["family_id"] for f in self.families],
        }


def evaluate_a2b_proposals(new_families: Sequence[Mapping[str, Any]],
                           unmatched_rows: Sequence[Mapping[str, Any]],
                           *, existing_family_ids: Sequence[str]
                           ) -> A2BProposalOutcome:
    """A2B'nin onerdigi aileleri destek sayisina gore kabul/red eder.

    Destek kurali (LOCKED kilit + kod haritasi 5b):
      * Destek = onerinin `examples` degerlerinden, A2B'ye GONDERILEN
        UNMATCHED kelime metinleriyle BIREBIR eslesen GERCEK keyword ID'leri
        kumesinin buyuklugu.
      * Eslesmeyen / uydurulmus example destek SAYILMAZ.
      * Ayni example'in tekrari destegi ARTIRMAZ (ID kumesi tekildir).
      * Destek >= 2 ise aile sozluge eklenir; degilse EKLENMEZ.

    A2B burada hicbir kelimeyi bir aileye ATAMAZ: kabul edilen aileler
    sozluge eklenir, sozluk dondurulur ve butun UNMATCHED kumesi A2C'de
    yeniden atanir.
    """
    by_text: Dict[str, List[int]] = {}
    for row in unmatched_rows:
        by_text.setdefault(str(row["keyword_text"]), []).append(
            int(row["keyword_id"]))

    existing = set(existing_family_ids)
    outcome = A2BProposalOutcome()
    for proposal in new_families:
        fid = str(proposal.get("family_id") or "")
        if not fid or fid in existing:
            # Sozlukte zaten olan (veya kimliksiz) oneri sozlugu DEGISTIREMEZ.
            outcome.rejected.setdefault(fid, [])
            continue
        support: set = set()
        missing: List[str] = []
        for example in (proposal.get("examples") or []):
            text = str(example)
            matched = by_text.get(text)
            if matched:
                support.update(matched)
            else:
                missing.append(text)
        support_ids = sorted(support)
        if missing:
            outcome.unmatched_examples[fid] = missing
        if len(support_ids) >= NEW_FAMILY_MIN_SUPPORT:
            outcome.accepted[fid] = support_ids
            outcome.families.append(dict(proposal))
        else:
            outcome.rejected[fid] = support_ids
    return outcome


def single_family_id(keyword_id: int) -> str:
    """A3 sonrasi hala UNMATCHED kalan kelimenin tekil ailesi (plan K6).

    MFV = 1 ve FamilyRelQ = kendi Rel'i olur; kelime ELENMEZ.
    """
    return f"{SINGLE_FAMILY_PREFIX}{int(keyword_id)}"


def finalize_assignments(assignments: Mapping[int, str],
                         families: Sequence[Mapping[str, Any]],
                         *, universe_ids: Sequence[int]
                         ) -> Dict[str, Any]:
    """Uretim kapanisi: tekil aile atamasi + 0 uyeli aile budama.

    Evrendeki HER kelime bir aile alir; UNMATCHED kalan (veya hic atanmamis)
    kelime `single:<keyword_id>` ailesine duser.
    """
    final: Dict[int, str] = {}
    singles: List[int] = []
    for keyword_id in universe_ids:
        fid = assignments.get(int(keyword_id))
        if not fid or fid == UNMATCHED:
            fid = single_family_id(keyword_id)
            singles.append(int(keyword_id))
        final[int(keyword_id)] = fid

    used = set(final.values())
    kept = [dict(f) for f in families if f.get("family_id") in used]
    dropped = sorted({str(f.get("family_id")) for f in families} - used)

    return {
        "family_by_id": final,
        "families": kept,
        "dropped_empty_families": dropped,
        "single_family_keyword_ids": singles,
        "dictionary_sha256": dict_sha(kept),
    }
