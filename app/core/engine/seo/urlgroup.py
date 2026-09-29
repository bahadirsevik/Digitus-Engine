"""SEO kati-2 — URL grubu eslemesi ve IKI GECIS (URETIM KOPYASI).

plan_algoritma_entegrasyonu.md Faz 4 · SEO kod haritasi 7. adim. Kilitli
`scripts/seo_v31_final.url_group_map` + `run_one`in iki gecisli akisidir.

7. ADIM ALGORITMADIR, test kodu DEGILDIR: EK-F gruplari AILE ADIYLA
isimlendirilir (`"{family_id}|{gid}"`) ve KUME duzeyinde tutulur (B11) —
grup kimlikleri aile icinde uretildigi icin aileler arasi cakismayi bu
isimlendirme onler.

SIRA BAGLAYICIDIR:
    authority -> kapi -> adaylar (1. gecis) -> URL grubu karari -> gruplarla
    secim (2. gecis)
URL grubu aday seti `family_cap`ten BAGIMSIZDIR; bu yuzden 1. gecis
`with_secondary=False` ve grupsuz kosar.

Olcum kollari (duyarlilik, ARM_VOLUME, ARM_RANDOM permutasyonlari) BURAYA
TASINMAZ: bunlar yalniz olcum parametresidir.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from app.core.engine.seo.select import ContractV31, evaluate, production_contract


class UrlGroupError(RuntimeError):
    """URL grubu sonucu bu kosuya ait degil / eksik — ikinci gecis BASLAMAZ."""


def cluster_group_map(primary_by_cluster: Mapping[str, int],
                      by_id: Mapping[int, Mapping[str, Any]],
                      keyword_to_group: Mapping[Any, Any]) -> Dict[str, str]:
    """EK-F ciktisi (aday -> grup) -> KUME -> grup.

    Yalniz 1. gecisin ADAYLARI icin grup uretilir; grubu olmayan aday
    eslemede YER ALMAZ (kilitli davranis).
    """
    kid_to_group: Dict[int, str] = {}
    for key, value in (keyword_to_group or {}).items():
        try:
            kid_to_group[int(key)] = str(value)
        except (TypeError, ValueError):
            raise UrlGroupError(f"URL grubu ciktisinda sayisal olmayan id: {key!r}")

    out: Dict[str, str] = {}
    for cluster, keyword_id in primary_by_cluster.items():
        gid = kid_to_group.get(int(keyword_id))
        if gid is not None:
            row = by_id.get(int(keyword_id))
            if row is None:
                raise UrlGroupError(
                    f"URL grubu aday {keyword_id} bu kosunun satirlarinda yok")
            out[cluster] = f'{row["family_id"]}|{gid}'
    return out


def run_two_pass(rows: Sequence[Mapping[str, Any]],
                 keyword_to_group: Mapping[Any, Any], *,
                 contract: Optional[ContractV31] = None) -> Dict[str, Any]:
    """1. gecis (adaylar) -> URL grubu -> 2. gecis (gruplarla gercek secim).

    `contract` verilmezse URETIM sozlesmesi kullanilir (`family_cap=2`).
    """
    c = contract or production_contract()
    by_id = {int(r["keyword_id"]): r for r in rows}

    # 1. gecis: adaylari bul (gruplar aday kimligine baglidir)
    first = evaluate(rows, c, with_secondary=False)
    groups = cluster_group_map(first["primary_by_cluster"], by_id,
                               keyword_to_group)
    # 2. gecis: EK-F gruplariyla gercek kosu
    main = evaluate(rows, c, url_groups=groups)
    main["url_group_coverage"] = {"clusters": len(main["members"]),
                                  "grouped": len(groups)}
    main["cluster_group_map"] = groups
    return main
