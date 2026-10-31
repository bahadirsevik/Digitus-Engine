"""İddia denetimi sözlüğü: hizmet/marka kavramları, sertifika, mutlak sonuç kalıpları.

Kural (plan_iddia_denetimi A1): her hizmet ve her marka AYRI kavram kimliğidir;
yalnız aynı kavramın dil karşılıkları tek kimlikte birleşir (botoks != dolgu,
Osstem != Straumann). Tüm kalıplar katlanmış metinde (Türkçe-duyarlı küçük harf,
ı→i) çalışır; kalıplar yazılırken doğal Türkçe yazılır, derlemede aynı katlama uygulanır.

shortcut: sözlük kısa ve sektör-bağımsız yapıda; yeni hizmet/marka sahada çıktıkça
SERVICE_CONCEPTS'e satır eklenir (kod değişmez).
shortcut: çıplak, niteleyicisiz TR "dolgu" belirsizdir (diş dolgusu mu estetik dolgu mu):
ne iddia olarak işaretlenir ne de izin sayılır; yalnız "diş dolgusu", "dudak dolgusu" gibi
nitelikli biçimler kavram eşleşmesi yapar. Gerekirse nitelikli alias eklenir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Pattern, Tuple


def fold(text: str) -> str:
    """Türkçe-duyarlı küçük harf + ı→i (validators._tr_lower ile aynı sonuç)."""
    return text.replace("I", "ı").replace("İ", "i").lower().replace("ı", "i")


# Harf olmayan karakterden sonra başlar (hashtag/noktalama önü serbest); sonu \w* ile
# çekim ekini yutar.
_LEAD = r"(?<![^\W\d_])"

SERVICE_CONCEPTS: Dict[str, Dict[str, List[str]]] = {
    "sedation": {
        "tr": ["sedasyon"], "ru": ["седаци"], "uk": ["седаці"],
        "en": ["sedation"], "de": ["sedierung"],
    },
    "general_anesthesia": {
        "tr": ["narkoz", "genel anestezi"], "ru": ["наркоз", r"общ\w+ анестези"],
        "uk": ["наркоз", r"загальн\w+ анестези"],
        "en": ["general an(?:a)?esthesia"], "de": ["vollnarkose", "allgemeinan(?:ä|ae)sthesie"],
    },
    "laser": {
        "tr": ["lazer"], "ru": ["лазер"], "uk": ["лазер"], "en": ["lasers?"], "de": ["laser"],
    },
    "botox": {
        "tr": ["botoks"], "ru": ["ботокс"], "uk": ["ботокс"], "en": ["botox"], "de": ["botox"],
    },
    # Diş dolgusu ve estetik dolgu AYRI kavram: biri diğerine izin vermez.
    "dental_filling": {
        "tr": ["diş dolgu", "dolgu tedavisi", "kompozit dolgu", "amalgam dolgu"],
        "ru": ["пломб"], "uk": ["пломб"],
        "en": ["dental fillings?", "tooth fillings?"], "de": ["zahnfüllung", "füllung"],
    },
    "filler": {
        "tr": ["dudak dolgu", "dermal dolgu", "yüz dolgu", "dolgu enjeksiyon"],
        "ru": ["филлер"], "uk": ["філер", "філлер"],
        "en": ["dermal fillers?", "lip fillers?", "fillers?"], "de": ["filler", "unterspritzung"],
    },
    "zirconium_crown": {
        "tr": ["zirkonyum"], "ru": ["циркони"], "uk": ["цирконі"],
        "en": ["zirconi(?:um|a)"], "de": ["zirkon"],
    },
    "all_on_4": {"*": [r"all[- ]?on[- ]?(?:4(?!\d)|four)"]},
    "brand_nobel_biocare": {"*": ["nobel biocare"], "ru": [r"нобел\w* биокеа?р"], "uk": [r"нобел\w* біокеа?р"]},
    "brand_straumann": {"*": ["straumann"], "ru": ["штрауман"], "uk": ["штрауман"]},
    "brand_osstem": {"*": ["osstem"], "ru": ["осстем"], "uk": ["осстем"]},
    "brand_megagen": {"*": ["megagen"], "ru": ["мегаген"], "uk": ["мегаген"]},
}


def _compile_aliases(aliases: List[str]) -> Pattern:
    body = "|".join(fold(a) for a in aliases)
    return re.compile(rf"{_LEAD}(?:{body})\w*")


SERVICE_RX: Dict[str, Pattern] = {
    cid: _compile_aliases([a for lang_aliases in langs.values() for a in lang_aliases])
    for cid, langs in SERVICE_CONCEPTS.items()
}

# Kuruluş + opsiyonel kod. Kodsuz geçiş her zaman ihlal (belirsiz → insan incelemesi).
CERT_PATTERN = re.compile(
    r"(?<![^\W\d_])(ISO|CE|FDA|T[ÜU]V|JCI)(?![^\W\d_])(?:\s*/\s*IEC)?"
    r"(?:\s*[-–:]?\s*(\d{3,5}(?::\d{4})?))?",
    re.IGNORECASE,
)

# Mutlak sonuç/güvenlik kalıpları; kaynağa BAKILMAZ. tamamen/полностью/completely
# tek başına yasak değil, yalnız sonuç/risk/ağrı kalıbı içinde.
# RU/UK ağrısızlık kökleri (безбол*, без боли) BAŞTA sınır olmadan aranır:
# '#лечениезубовбезболи' gibi bitişik hashtag'ler yakalansın.
ABSOLUTE_OUTCOME: Dict[str, List[str]] = {
    "tr": [r"ağrısız", r"acısız", r"hiçbir (?:ağrı|risk)", r"sıfır risk", r"%\s*100(?!\d)",
           r"yüzde yüz", r"kesin (?:sonuç|çözüm)", r"garantili (?:sonuç|tedavi)"],
    "ru": [r"безболезн\w*", r"без\s?боли", r"никакой боли", r"абсолютно безопасн\w*",
           r"(?<!\d)100\s*%", r"полностью (?:восстанавливает|безопасн\w*)"],
    "uk": [r"безболісн\w*", r"без\s?болю", r"абсолютно безпечн\w*",
           r"повністю (?:відновлює|безпечн\w*)"],
    "en": [r"painless", r"pain[- ]free", r"100\s*%\s*(?:safe|success)", r"completely safe"],
    "de": [r"schmerzfrei", r"100\s*%\s*(?:sicher|erfolg)", r"völlig sicher"],
}
ABSOLUTE_RX: Tuple[Pattern, ...] = tuple(
    re.compile(fold(p)) for ps in ABSOLUTE_OUTCOME.values() for p in ps
)

# Olumsuz cümle kaynak izni sayılmaz.
NEGATION_RX = re.compile(
    rf"{_LEAD}(?:yapmiyor\w*|yapilmaz|yoktur|yok|değil|degil|hariç|haric|не|нет|без|немає|"
    r"\w+m[iuü]yor\w*|\w+(?:me|ma)(?:mektedir|maktadir)|"
    r"\w+m[ae]z|\w+m[ae]yiz|"  # sunulmaz, uygulanmaz, verilmez, yapmayız
    r"не предоставля\w*|не проводи\w*|не делаем|не виконуємо|не надаємо|"
    r"not offered|not available|don'?t offer|do not offer|nicht angeboten|"
    r"not|no|kein\w*|ohne)(?![^\W\d_])"
)

_POSITIVE_LABELS = {
    "ürünler": "products", "urunler": "products",
    "hizmetler": "services",
    "kullanim alanlari": "use_cases",
}
# İzin kaynağı DEĞİL: firma, sektör, hedef kitle, problem metni.
_IGNORED_LABELS = {
    "firma", "sektör", "sektor", "hedef kitle", "çözdüğü problemler", "cozdugu problemler",
}


# Tüm onarım/yeniden yazım prompt'larında ortak kural cümlesi (A2/A3/A4).
CLAIM_REPAIR_RULE = (
    "İddiayı metinden ÇIKAR, yumuşatma; marka bilgilerinde olmayan hiçbir hizmet, cihaz, "
    "marka veya sertifikayı anma; hiçbir dilde mutlak sonuç/güvenlik vaadi "
    "(\"ağrısız\", \"%100\", \"kesin sonuç\") yazma."
)


@dataclass(frozen=True)
class GroundingSource:
    """Dayanak kaynağı: yalnız onaylı ürün/hizmet/kullanım alanı + güvenilir USP."""

    services: Tuple[str, ...] = ()
    products: Tuple[str, ...] = ()
    use_cases: Tuple[str, ...] = ()
    usp: Tuple[str, ...] = ()
    raw_text: str = ""  # sayısal/süperlatif dayanak için tüm düz metin

    @classmethod
    def from_text(cls, text: str) -> "GroundingSource":
        buckets: Dict[str, List[str]] = {"services": [], "products": [], "use_cases": [], "usp": []}
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"([^:]{1,40}):\s*(.*)$", line)
            if m:
                label = fold(m.group(1)).strip()
                if label in _IGNORED_LABELS:
                    continue
                bucket = _POSITIVE_LABELS.get(label)
                if bucket:
                    buckets[bucket].extend(s.strip() for s in m.group(2).split(",") if s.strip())
                    continue
            buckets["usp"].append(line)
        return cls(
            services=tuple(buckets["services"]), products=tuple(buckets["products"]),
            use_cases=tuple(buckets["use_cases"]), usp=tuple(buckets["usp"]),
            raw_text=text or "",
        )

    def facts_text(self) -> str:
        if self.raw_text:
            return self.raw_text
        return "\n".join((*self.services, *self.products, *self.use_cases, *self.usp))

    def permission_units(self) -> List[str]:
        """Olumlu izin birimleri (ham harf; olumsuz cümleler hariç)."""
        units: List[str] = [*self.services, *self.products, *self.use_cases]
        for line in self.usp:
            units.extend(re.split(r"[.;!?,]+(?:\s|$)", line))
        return [u for u in units if u.strip() and not NEGATION_RX.search(fold(u))]
