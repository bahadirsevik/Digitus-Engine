"""Keyword import tema kapisi — TEK karar fonksiyonu (plan_marka_profili_sadakati.md P1.6).

SORUN. Iki dislama kaynagi FARKLI guctedir:

  profile_data.exclude_themes   -> YUMUSAK; AI da yazar (profile_extractor.py)
  topic_policy.excluded_terms   -> SERT;   yalniz kullanici onayli (models.py)

Import kapisi ise yumusak listeyi deterministik hard-drop olarak kullaniyordu.
Yani AI'in urettigi bir tema, kullanici hic onaylamadan keyword'u daha
SKORLAMAYA ULASMADAN eliyordu — sessiz kelime kaybinin en agir yolu.

YENI SOZLESME.

  hard      : kullanici onayli terimler          -> ELER
  advisory  : yalniz AI'in urettigi temalar      -> UYARI, satir KABUL edilir
  protected : kullanicinin acik koruma beyani    -> IKISINI DE EZER
  force_include: mevcut kurtarma davranisi       -> hard elemeyi atlar

Eslesme kurali DEGISMEDI: her uc liste de `theme_matcher` stem kuralini
kullanir. Yon-bagimsiz tek kural sart — iki taraf farkli olculurse "korunan
kazanir" onceligi tutarsizlasir.

TUM import yollari (CSV dry-run, tekil ekleme, JSON toplu import) bu modulu
kullanir; CSV commit plani replay ettigi icin yapisal olarak tutarlidir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

from app.core.site_analyzer.theme_matcher import matched_theme
from app.core.site_analyzer.turkish_normalizer import normalize_turkish

DECISION_INCLUDE = "include"
DECISION_EXCLUDE = "exclude"
DECISION_PROTECTED = "protected"


def _clean(values: Any) -> List[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


@dataclass(frozen=True)
class ImportThemePolicy:
    """Import kapisinin uc listesi; provenance'a gore ayrilmis."""

    hard_themes: List[str] = field(default_factory=list)
    advisory_themes: List[str] = field(default_factory=list)
    protected_themes: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.hard_themes or self.advisory_themes or self.protected_themes)


@dataclass(frozen=True)
class ThemeDecision:
    """Tipli import karari.

    decision == "exclude" TEK basina eleme sebebidir; cagiranlar baska bir
    tema alanina bakarak eleme karari VERMEMELIDIR.
    """

    decision: str
    matched_exclude_theme: Optional[str] = None
    matched_protected_theme: Optional[str] = None
    matched_advisory_theme: Optional[str] = None

    @property
    def is_excluded(self) -> bool:
        return self.decision == DECISION_EXCLUDE

    @property
    def has_advisory_warning(self) -> bool:
        """Kabul edildi ama AI dislama temasina takildi (kullaniciya gosterilir).

        YALNIZ `include` kararinda dogrudur. `protected` kararinda AI temasi
        eslesmis olsa bile UYARI URETMEZ: "protected ikisini de ezer"
        sozlesmesi geregi, kullanicinin acikca kapsam-ici ilan ettigi konu
        icin "gozden gecirin" demek celiskidir. `matched_advisory_theme`
        protected kararinda da DOLU kalir — denetim/aciklanabilirlik icin
        tasinir, kullanici yuzeyine cikmaz.
        """
        return (
            self.decision == DECISION_INCLUDE
            and self.matched_advisory_theme is not None
        )


def resolve_import_theme_policy(workspace) -> ImportThemePolicy:
    """Workspace'ten uc listeyi provenance'a gore ayirir.

    hard = onayli topic policy terimleri UNION kullanicinin "mutlaka olmasin"
    metni. Ikinci kaynak fazladan gorunebilir (`apply_competitor_review`
    excluded_info'yu zaten topic_policy'ye senkronlar) ama senkron kosmamis
    workspace'lerde kullanicinin ACIK niyeti kaybolmasin diye birlestirilir.

    advisory = exclude_themes MINUS hard. Geriye yalnizca AI'in ekledigi (veya
    onayli profil formundan gelen yumusak) temalar kalir.
    """
    from app.core.policy.topic_policy import (
        approved_topic_terms,
        parse_excluded_info,
    )

    profile_data = getattr(workspace, "profile_data", None) or {}

    hard: List[str] = []
    seen_hard: set = set()
    for source in (
        approved_topic_terms(workspace),
        parse_excluded_info(getattr(workspace, "excluded_info", None)),
    ):
        for term in _clean(source):
            key = normalize_turkish(term)
            if key and key not in seen_hard:
                seen_hard.add(key)
                hard.append(term)

    advisory = [
        theme for theme in _clean(profile_data.get("exclude_themes"))
        if normalize_turkish(theme) not in seen_hard
    ]

    return ImportThemePolicy(
        hard_themes=hard,
        advisory_themes=advisory,
        protected_themes=_clean(profile_data.get("protected_themes")),
    )


def decide_keyword_theme(
    keyword: str,
    policy: ImportThemePolicy,
    *,
    force_include: bool = False,
) -> ThemeDecision:
    """Tek keyword icin tipli import karari.

    Oncelik sirasi:
      1. protected  — kullanicinin acik koruma beyani her seyi ezer
      2. force_include — kullanicinin satir bazli kurtarmasi
      3. hard exclude — kullanici onayli politika
      4. advisory — AI temasi eslesti, satir KABUL edilir (yalniz uyari)
    """
    text = (keyword or "").strip()
    if not text or policy.is_empty:
        return ThemeDecision(DECISION_INCLUDE)

    protected_hit = matched_theme(text, policy.protected_themes)
    hard_hit = matched_theme(text, policy.hard_themes)
    advisory_hit = matched_theme(text, policy.advisory_themes)

    if protected_hit:
        # Koruma USTUNDUR: genis bir dislama temasi, kullanicinin acikca
        # kapsam-ici ilan ettigi konuyu eleyemez.
        return ThemeDecision(
            DECISION_PROTECTED,
            matched_exclude_theme=hard_hit,
            matched_protected_theme=protected_hit,
            matched_advisory_theme=advisory_hit,
        )

    if hard_hit and not force_include:
        return ThemeDecision(
            DECISION_EXCLUDE,
            matched_exclude_theme=hard_hit,
            matched_advisory_theme=advisory_hit,
        )

    return ThemeDecision(
        DECISION_INCLUDE,
        matched_exclude_theme=hard_hit if force_include else None,
        matched_advisory_theme=advisory_hit,
    )


def decide_many(
    keywords: Sequence[str], policy: ImportThemePolicy
) -> List[ThemeDecision]:
    """Toplu karar (test/rapor kolayligi; tekil fonksiyonla ayni kural)."""
    return [decide_keyword_theme(kw, policy) for kw in keywords]
