"""Yasaklı tema (exclude_themes) eşleşme mantığı — ortak modül.

Hem anchor üretimi (anchor_builder) hem keyword import kapısı (crud/CSV)
aynı deterministik kuralı kullanır; mantık tek yerde durur.

Eşleşme kuralı bilinçli olarak KONSERVATİF:
- Çok kelimeli bir exclude temasının eşleşme sayılması için TÜM stem'leri
  metinde bulunmalı. Böylece "temettü takibi" teması, meşru "Portföy Takip
  Sistemi" kalemini YANLIŞLIKLA elemez (yalnız "takip" ortak; "temett" yok).
- Türkçe ünsüz yumuşaması için 4 karakterlik ortak önek yeterli sayılır
  ("takib" vs "takip").
"""
from __future__ import annotations

from typing import List, Optional

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

_MIN_STEM_LEN = 3
_PREFIX_OVERLAP = 4  # Türkçe ünsüz yumuşaması: "takib" vs "takip" → 4 ortak önek


def _stems(text: str) -> set:
    tokens = normalize_turkish(text or "").split()
    return {t for t in tokens if len(t) >= _MIN_STEM_LEN}


def _stem_matches(a: str, b: str) -> bool:
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n >= _PREFIX_OVERLAP


def _theme_stem_sets(exclude_themes: Optional[List[str]]) -> List[set]:
    out = []
    for theme in exclude_themes or []:
        if isinstance(theme, str):
            s = _stems(theme)
            if s:
                out.append(s)
    return out


def item_matches_any_theme(item: str, theme_stem_sets: List[set]) -> bool:
    """Kalem, verilen temalardan herhangi birinin TÜM stem'lerini içeriyor mu?

    Yön-bağımsız çekirdek kural: hem dışlanacak (`exclude_themes`) hem
    korunacak (`protected_themes`) temalar aynı eşleşme mantığını kullanır —
    iki taraf farklı kurallarla ölçülürse "korunan kazanır" önceliği
    tutarsızlaşır (plan Faz C/D).
    """
    if not isinstance(item, str) or not item.strip():
        return False
    item_stems = _stems(item)
    if not item_stems:
        return False
    for theme_stems in theme_stem_sets:
        if all(
            any(_stem_matches(ts, istem) for istem in item_stems)
            for ts in theme_stems
        ):
            return True
    return False


def item_is_excluded(item: str, theme_stem_sets: List[set]) -> bool:
    """Kalem, herhangi bir exclude temasının TÜM stem'lerini içeriyor mu?"""
    return item_matches_any_theme(item, theme_stem_sets)


def matched_theme(item: str, themes: Optional[List[str]]) -> Optional[str]:
    """Kalem bir temayla eşleşiyorsa eşleşen temanın METNİNİ döner.

    Yön-bağımsızdır (exclude veya protected). Eşleşme yoksa None.
    """
    if not isinstance(item, str) or not item.strip():
        return None
    item_stems = _stems(item)
    if not item_stems:
        return None
    for theme in themes or []:
        if not isinstance(theme, str):
            continue
        theme_stems = _stems(theme)
        if not theme_stems:
            continue
        if all(
            any(_stem_matches(ts, istem) for istem in item_stems)
            for ts in theme_stems
        ):
            return theme
    return None


def matched_exclude_theme(
    item: str, exclude_themes: Optional[List[str]]
) -> Optional[str]:
    """Kalem bir exclude temasıyla eşleşiyorsa eşleşen temanın METNİNİ döner.

    Import kapısı raporlaması için: kullanıcı hangi temanın elediğini görür.
    Eşleşme yoksa None.
    """
    return matched_theme(item, exclude_themes)


def resolve_declared_theme(
    value: Optional[str], themes: Optional[List[str]]
) -> Optional[str]:
    """Modelin beyan ettiği tema metnini KANONİK listeye oturtur (plan Faz C).

    Sözleşme: dönen tema, modele verilen listeden gelmelidir. Uydurma tema
    sessizce kabul edilmez → None döner (çağıran ihlal sayar).

    İki kademe: (1) normalize edilmiş birebir eşitlik, (2) stem tabanlı
    eşleşme (modelin ekleme/çekim farkını tolere eder — "boyaya alternatif
    arayışı" vs "boyaya alternatif arayisi"). İkisi de tutmazsa uydurmadır.
    """
    text = str(value or "").strip()
    if not text:
        return None
    canonical = normalize_turkish(text)
    if not canonical:
        return None
    for theme in themes or []:
        if isinstance(theme, str) and normalize_turkish(theme) == canonical:
            return theme
    return matched_theme(text, themes)


def filter_items_by_exclude(
    items: Optional[List[str]],
    exclude_themes: Optional[List[str]],
    protected_themes: Optional[List[str]] = None,
) -> List[str]:
    """Listeden exclude_themes ile örtüşen kalemleri düşürür.

    `protected_themes` verilirse KORUMA ÜSTÜNDÜR (plan Faz C/D): açıkça
    korunan bir temaya oturan kalem, geniş bir dışlama temasıyla örtüşse bile
    listede kalır. Böylece "kimyasal saç boyaları" tipi geniş bir dışlama,
    "saç boyasının zararları" gibi korunan içeriği anchor'dan silemez.
    """
    if not items:
        return []
    theme_sets = _theme_stem_sets(exclude_themes)
    if not theme_sets:
        return [i for i in items if isinstance(i, str) and i.strip()]
    protected_sets = _theme_stem_sets(protected_themes)
    return [
        i for i in items
        if isinstance(i, str) and i.strip()
        and (
            not item_matches_any_theme(i, theme_sets)
            or item_matches_any_theme(i, protected_sets)
        )
    ]
