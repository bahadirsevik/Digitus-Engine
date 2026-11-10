"""Cikti dili + hedef konum blogu (plan_pazar_dil_secimi.md Faz 2-a/c).

Uretici prompt'lari calisma pazarini (hedef konum + dil) tek yerden bu modulle
alir; sablonlar degismez (mühürlü SHA kilitleri korunur), blok render edilmis
prompt'un SONUNA eklenir. Snapshot yok: her uretimde calismadan turetilir.
Calisma baglami olmayan yollar Türkçe + "Seçilmedi" varsayilanina duser.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.core import markets

DEFAULT_LANGUAGE = "Türkçe"
DEFAULT_LOCATION = "Seçilmedi"

OUTPUT_BLOCK_TEMPLATE = (
    "ÇIKTI DİLİ: {output_language}. HEDEF KONUM: {target_location} "
    "(işletmenin adresi değil; adres/şehir uydurma). İstisnalar: marka/ürün adları, "
    "URL'ler, tıbbi/teknik terimler, yerleşik yabancı kelimeler. "
    "JSON anahtarları, enum değerleri ve ID'ler değişmez."
)


# Yalnız Türkçe-dışı dil / Türkiye-dışı pazarda eklenir; Türkçe + TR/Seçilmedi blok BAYT-AYNI kalır.
TITLE_RULE = (
    "Türkçe mesleki unvan kısaltmalarını (Uzm. Dr., Dt., Op. Dr.) hedef dilde doğal biçimde yaz; "
    "uzmanlık veya yetki EKLEME, ad-soyadı değiştirme."
)
MARKET_RULE = (
    "Hedef pazarda bulunmayan kurum, sigorta, para birimi ve ödeme sistemi adı kullanma; "
    "işletme bilgisinde olmayan şube veya adres yazma."
)


def _country_of(location: str) -> str:
    """Konum etiketinin ULKE parcasi: 'Istanbul, Türkiye — Şehir' / 'Türkiye (TR) — Ülke' -> 'Türkiye'."""
    head = location.split(" — ")[0].split(" (")[0]
    return head.rsplit(", ", 1)[-1].strip()


@dataclass(frozen=True)
class OutputContext:
    language: str = DEFAULT_LANGUAGE
    location: str = DEFAULT_LOCATION

    def block(self) -> str:
        text = OUTPUT_BLOCK_TEMPLATE.format(
            output_language=self.language, target_location=self.location
        )
        foreign_language = self.language != DEFAULT_LANGUAGE
        # Türkiye pazarı = ülke/şehir/bölge etiketinin ülke parçası Türkiye (şehir etiketi
        # 'Istanbul, Türkiye — Şehir' "Türkiye" ile BAŞLAMAZ; blok TR için bayt-aynı kalmalı)
        foreign_market = not (
            self.location == DEFAULT_LOCATION or _country_of(self.location) == "Türkiye"
        )
        extra = []
        if foreign_language:
            extra.append(TITLE_RULE)
        if foreign_language or foreign_market:
            extra.append(MARKET_RULE)
        return " ".join([text, *extra])


DEFAULT_CONTEXT = OutputContext()


def output_context(ws: Any) -> OutputContext:
    """Calisma satirindan (BrandProfile) baglam; None/eksik alan -> varsayilan."""
    if ws is None:
        return DEFAULT_CONTEXT
    language = markets.language_name(getattr(ws, "default_language_id", None)) or DEFAULT_LANGUAGE
    geo = getattr(ws, "default_geo_target_id", None)
    location = DEFAULT_LOCATION
    if geo:
        try:
            location = markets.location_label(geo)
        except Exception as exc:  # katalog okunamadi: uretim durmasin
            logger.warning(f"Hedef konum etiketi alinamadi ({geo}): {exc}")
    return OutputContext(language=language, location=location)


def output_context_for_run(db: Any, run_or_id: Any) -> OutputContext:
    """ScoringRun (ya da id) -> bagli calisma -> baglam. Fail-open: varsayilan."""
    if run_or_id is None:
        return DEFAULT_CONTEXT
    try:
        from app.database.models import BrandProfile, ScoringRun

        run = run_or_id
        if isinstance(run_or_id, int):
            run = db.query(ScoringRun).filter(ScoringRun.id == run_or_id).first()
        profile_id = getattr(run, "brand_profile_id", None)
        if not profile_id:
            return DEFAULT_CONTEXT
        ws = db.query(BrandProfile).filter(BrandProfile.id == profile_id).first()
        return output_context(ws)
    except Exception as exc:
        logger.warning(f"Cikti dili baglami okunamadi, varsayilan: {exc}")
        return DEFAULT_CONTEXT


def with_output_block(prompt: str, ctx: Optional[OutputContext] = None) -> str:
    """Render edilmis prompt'un sonuna ortak cikti dili blogunu ekler."""
    return f"{prompt}\n\n{(ctx or DEFAULT_CONTEXT).block()}"
