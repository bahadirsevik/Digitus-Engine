# -*- coding: utf-8 -*-
"""Sosyal brief kampanya modu — saf girdi doğrulaması (plan_sosyal_kampanya_akisi.md §1-§2).

Saf modül: DB, ORM, HTTP veya AI bağımlılığı yoktur. Çıktı, `social_briefs.campaign_spec`
JSON kolonuna aynen yazılan düz dict'tir. Enum değerleri exact-match doğrulanır (coercion yok);
serbest metinler kırpılır.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

SPEC_VERSION = "campaign_v1"

MODES = ("defined", "suggest")
USAGES = ("paid", "organic")
OBJECTIVES = ("sales", "lead", "awareness")
DESTINATIONS = ("site", "form", "whatsapp", "phone", "store", "profile")
# "none" (teklifsiz devam) brief'te değil, içerik isteğinde seçilir (plan §2).
OFFER_TYPES = ("percent_discount", "bundle", "free_shipping", "gift")

PROMOTED_ITEM_MAX = 200
AUDIENCE_MAX = 200
FOCUS_NOTE_MAX = 300
INCLUDED_FEATURES_MAX = 500
OFFER_VALUE_MAX = 120
OFFER_APPLIES_TO_MAX = 200
OFFER_CONDITIONS_MAX = 300


class CampaignSpecError(ValueError):
    """Kampanya girdisi doğrulama hatası (HTTP bağımsız)."""

    def __init__(self, error_code: str, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.field = field


def _text(value: Any, *, field: str, label: str, max_len: int, required: bool) -> Optional[str]:
    if value is None:
        if required:
            raise CampaignSpecError("CAMPAIGN_FIELD_REQUIRED", f"{label} girin.", field)
        return None
    if not isinstance(value, str):
        raise CampaignSpecError("CAMPAIGN_INVALID_VALUE", f"{label} metin olmalıdır.", field)
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise CampaignSpecError("CAMPAIGN_FIELD_REQUIRED", f"{label} girin.", field)
        return None
    if len(cleaned) > max_len:
        raise CampaignSpecError(
            "CAMPAIGN_TEXT_TOO_LONG", f"{label} en fazla {max_len} karakter olabilir.", field
        )
    return cleaned


def _choice(value: Any, allowed: tuple[str, ...], *, field: str, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CampaignSpecError("CAMPAIGN_INVALID_VALUE", f"{label} için listeden bir seçim yapın.", field)
    return value


# shortcut: Türkiye 2016'dan beri sabit UTC+3 (yaz saati yok); tzdata bağımlılığı
# gerekmesin diye sabit ofset. Başka ülke/saat dilimi gelirse ZoneInfo'ya geç.
_BUSINESS_TZ = timezone(timedelta(hours=3))


def business_today(now: Optional[datetime] = None) -> date:
    """İş günü: sunucu saat diliminden bağımsız, Türkiye takvim günü."""
    return (now or datetime.now(timezone.utc)).astimezone(_BUSINESS_TZ).date()


def validate_offer_terms(offer: Any, *, today: date, field: str) -> dict[str, Any]:
    """Teklif koşullarını doğrular: tür, değer, kapsam zorunlu; şart ve bitiş opsiyonel.

    Geçmiş bitiş tarihi reddedilir (plan §2).
    """
    offer_type = _choice(
        getattr(offer, "type", None), OFFER_TYPES, field=f"{field}.type", label="Teklif türü"
    )
    ends_raw = getattr(offer, "ends", None)
    ends: Optional[str] = None
    if ends_raw is not None:
        try:
            ends_date = date.fromisoformat(ends_raw) if isinstance(ends_raw, str) else None
        except ValueError:
            ends_date = None
        if ends_date is None:
            raise CampaignSpecError(
                "CAMPAIGN_INVALID_VALUE", "Bitiş tarihi YYYY-AA-GG biçiminde olmalıdır.", f"{field}.ends"
            )
        if ends_date < today:
            raise CampaignSpecError(
                "CAMPAIGN_OFFER_EXPIRED", "Teklifin bitiş tarihi geçmiş. Tarihi güncelleyin.", f"{field}.ends"
            )
        ends = ends_date.isoformat()
    return {
        "type": offer_type,
        "value": _text(getattr(offer, "value", None), field=f"{field}.value",
                       label="Teklif detayı", max_len=OFFER_VALUE_MAX, required=True),
        "applies_to": _text(getattr(offer, "applies_to", None), field=f"{field}.applies_to",
                            label="Teklifin geçerli olduğu ürün/kapsam",
                            max_len=OFFER_APPLIES_TO_MAX, required=True),
        "conditions": _text(getattr(offer, "conditions", None), field=f"{field}.conditions",
                            label="Teklif şartları", max_len=OFFER_CONDITIONS_MAX, required=False),
        "ends": ends,
    }


def validate_campaign_spec(campaign: Any, *, today: Optional[date] = None) -> dict[str, Any]:
    """Brief'in kampanya bölümünü doğrular ve saklanacak düz dict'i üretir.

    Kurallar:
    - mode=defined: offer_draft zorunlu; disallowed_offer_types boş olmalı.
    - mode=suggest: offer_draft verilemez; disallowed_offer_types benzersiz ve en az bir
      teklif türü serbest kalmalı.
    """
    today = today or business_today()
    mode = _choice(getattr(campaign, "mode", None), MODES, field="campaign.mode", label="Kampanya yolu")

    disallowed_raw = getattr(campaign, "disallowed_offer_types", None) or []
    disallowed: list[str] = []
    for item in disallowed_raw:
        value = _choice(item, OFFER_TYPES, field="campaign.disallowed_offer_types",
                        label="İzin verilmeyen teklif türü")
        if value in disallowed:
            raise CampaignSpecError(
                "CAMPAIGN_INVALID_VALUE", "Aynı teklif türü birden fazla kez işaretlenmiş.",
                "campaign.disallowed_offer_types",
            )
        disallowed.append(value)

    offer_draft_raw = getattr(campaign, "offer_draft", None)
    offer_draft: Optional[dict[str, Any]] = None
    if mode == "defined":
        if offer_draft_raw is None:
            raise CampaignSpecError(
                "CAMPAIGN_FIELD_REQUIRED", "Kampanyanız belliyse teklif bilgilerini girin.",
                "campaign.offer_draft",
            )
        if disallowed:
            raise CampaignSpecError(
                "CAMPAIGN_MODE_MISMATCH",
                "İzin verilmeyen teklif türleri yalnız 'Kampanya öner' yolunda seçilebilir.",
                "campaign.disallowed_offer_types",
            )
        offer_draft = validate_offer_terms(offer_draft_raw, today=today, field="campaign.offer_draft")
    else:
        if offer_draft_raw is not None:
            raise CampaignSpecError(
                "CAMPAIGN_MODE_MISMATCH",
                "'Kampanya öner' yolunda teklif bilgisi girilmez; fikir seçiminden sonra girilir.",
                "campaign.offer_draft",
            )
        if len(disallowed) == len(OFFER_TYPES):
            raise CampaignSpecError(
                "CAMPAIGN_INVALID_VALUE", "En az bir teklif türüne izin verin.",
                "campaign.disallowed_offer_types",
            )

    return {
        "spec_version": SPEC_VERSION,
        "mode": mode,
        "usage": _choice(getattr(campaign, "usage", None), USAGES,
                         field="campaign.usage", label="Kullanım yeri"),
        "objective": _choice(getattr(campaign, "objective", None), OBJECTIVES,
                             field="campaign.objective", label="Kampanya hedefi"),
        "destination": _choice(getattr(campaign, "destination", None), DESTINATIONS,
                               field="campaign.destination", label="Yönlendirme kanalı"),
        "promoted_item": _text(getattr(campaign, "promoted_item", None), field="campaign.promoted_item",
                               label="Tanıtılacak ürün veya hizmet",
                               max_len=PROMOTED_ITEM_MAX, required=True),
        "audience": _text(getattr(campaign, "audience", None), field="campaign.audience",
                          label="Hedef kitle", max_len=AUDIENCE_MAX, required=False),
        "focus_note": _text(getattr(campaign, "focus_note", None), field="campaign.focus_note",
                            label="Odak notu", max_len=FOCUS_NOTE_MAX, required=False),
        # Kullanıcının onayladığı "bu pakete dahil" özellikler: iddia denetiminde dayanak sayılır
        "included_features": _text(getattr(campaign, "included_features", None),
                                   field="campaign.included_features", label="Pakete dahil özellikler",
                                   max_len=INCLUDED_FEATURES_MAX, required=False),
        "disallowed_offer_types": disallowed,
        "offer_draft": offer_draft,
    }


# ==================== Faz 2: kampanya fikri alanları ====================

# K9: CTA türü hedef kanaldan türer (WhatsApp'a giden kampanyada "satın al" sunulmaz).
CTA_TYPES_BY_DESTINATION: dict[str, tuple[str, ...]] = {
    "site": ("buy", "learn_more"),
    "form": ("fill_form", "learn_more"),
    "whatsapp": ("message",),
    "phone": ("call",),
    "store": ("visit_store",),
    "profile": ("follow", "message"),
}
IDEA_CAMPAIGN_FIELDS: frozenset[str] = frozenset({"offer_type", "offer_rationale", "cta_type"})
OFFER_RATIONALE_MAX = 500

# Fikir metninde teklif rakamı / yer tutucu olmaz: koşulu yalnız kullanıcı verir (K4).
# shortcut: yazıyla sayı sabit liste (bileşik "yirmi beş" ilk kelimeden yakalanır); "yüz" bilinçli
# yok ("yüzde yüz pamuk" ürün dilidir). Kaçan yazım çıkarsa listeyi büyüt.
_NUM_WORD = (
    r"(?:bir|[iİ]ki|üç|dört|beş|alt[ıi]|yedi|sekiz|dokuz|on|yirmi|otuz|k[ıi]rk|elli|"
    r"altm[ıi]ş|yetmiş|seksen|doksan)"
)
_NUM = rf"(?:\d[\d.,]*|\b{_NUM_WORD})"
_OFFER_NUMBER_RX = re.compile(
    rf"%\s*\d|\d\s*%|yüzde\s+(?:\d|{_NUM_WORD}\b)"
    rf"|₺\s*\d|{_NUM}\s*(?:₺|(?:tl|lira)\b)"
    rf"|{_NUM}\s*(?:adet\s+|tane\s+)?al\s*{_NUM}\s*(?:adet\s+|tane\s+)?öde"
    r"|[\[\]{}]",
    re.IGNORECASE,
)
# shortcut: gerekçede sonuç/kazanç dili için sabit kök listesi (marj verisi yok, K7);
# kaçan ifade çıkarsa listeyi büyüt, anlamsal denetim hedeflenmiyor.
_OUTCOME_CLAIM_RX = re.compile(
    r"kazand[ıi]r|garanti|kesin|art[ıi]r[ıi]r|katla|en (?:iyi|çok|yüksek)", re.IGNORECASE
)


def allowed_offer_types(spec: dict[str, Any]) -> tuple[str, ...]:
    """defined: yalnız taslağın türü; suggest: yasaklananlar dışındaki türler."""
    if spec["mode"] == "defined":
        return (spec["offer_draft"]["type"],)
    return tuple(t for t in OFFER_TYPES if t not in spec["disallowed_offer_types"])


def allowed_cta_types(spec: dict[str, Any]) -> tuple[str, ...]:
    return CTA_TYPES_BY_DESTINATION[spec["destination"]]


def campaign_prompt_context(spec: dict[str, Any]) -> dict[str, Any]:
    """Prompt'a giren kampanya bağlamı. Teklif DEĞERİ bilinçli olarak yok:
    kategori/fikir aşaması yalnız mekanizma üretir, rakamı içerik adımı alır."""
    return {
        "usage": spec["usage"],
        "objective": spec["objective"],
        "destination": spec["destination"],
        "promoted_item": spec["promoted_item"],
        "audience": spec["audience"],
        "focus_note": spec["focus_note"],
        "included_features": spec.get("included_features"),  # eski brief'lerde anahtar yok
        "allowed_offer_types": list(allowed_offer_types(spec)),
        "allowed_cta_types": list(allowed_cta_types(spec)),
    }


def validate_idea_campaign_fields(
    idea: dict[str, Any], spec: dict[str, Any], *, texts: tuple[str, ...] = ()
) -> dict[str, str]:
    """Fikrin kampanya alanlarını doğrular; uymayan fikir çağıran tarafından ATILIR.

    texts: fikrin başlık/açıklaması — teklif rakamı ve yer tutucu taraması için.
    """
    offer_type = idea.get("offer_type")
    if offer_type not in allowed_offer_types(spec):
        raise CampaignSpecError("IDEA_CAMPAIGN_OFFER_TYPE", "Teklif türü bu kampanyada kullanılamaz.", "offer_type")
    cta_type = idea.get("cta_type")
    if cta_type not in allowed_cta_types(spec):
        raise CampaignSpecError("IDEA_CAMPAIGN_CTA_TYPE", "CTA türü hedef kanala uymuyor.", "cta_type")
    rationale = idea.get("offer_rationale")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > OFFER_RATIONALE_MAX:
        raise CampaignSpecError("IDEA_CAMPAIGN_RATIONALE", "Teklif gerekçesi boş veya çok uzun.", "offer_rationale")
    if _OUTCOME_CLAIM_RX.search(rationale):
        raise CampaignSpecError("IDEA_CAMPAIGN_RATIONALE", "Gerekçe sonuç/kazanç iddiası içeriyor.", "offer_rationale")
    for text in (*texts, rationale):
        if _OFFER_NUMBER_RX.search(text):
            raise CampaignSpecError("IDEA_CAMPAIGN_OFFER_NUMBER", "Fikir metni teklif rakamı veya yer tutucu içeriyor.", "idea_text")
    return {"offer_type": offer_type, "offer_rationale": rationale.strip(), "cta_type": cta_type}


# ==================== Faz 3: içerik adımı (teklif koşulları + görsel prompt'u) ====================

CONTENT_OFFER_TYPES = (*OFFER_TYPES, "none")
NO_OFFER_TERMS: dict[str, Any] = {
    "type": "none", "value": None, "applies_to": None, "conditions": None, "ends": None,
}
IMAGE_PROMPT_MAX = 2000

_MONTHS_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
              "Eylül", "Ekim", "Kasım", "Aralık")


def format_ends_tr(ends: str) -> str:
    """'2026-11-30' -> '30 Kasım 2026' (içerikte yazılacak açık tarih)."""
    d = date.fromisoformat(ends)
    return f"{d.day} {_MONTHS_TR[d.month - 1]} {d.year}"


def validate_content_offer_terms(offer: Any, *, today: Optional[date] = None) -> dict[str, Any]:
    """İçerik isteğindeki kesinleşmiş teklif. type="none" = teklifsiz devam (diğer alanlar yok sayılır)."""
    if getattr(offer, "type", None) == "none":
        return dict(NO_OFFER_TERMS)
    return validate_offer_terms(offer, today=today or business_today(), field="offer_terms")


def offer_terms_expired(offer_terms: Optional[dict[str, Any]], *, today: Optional[date] = None) -> bool:
    ends = (offer_terms or {}).get("ends")
    return bool(ends) and date.fromisoformat(ends) < (today or business_today())


def offer_terms_grounding_text(offer_terms: Optional[dict[str, Any]]) -> str:
    """Genel iddia denetimi whitelist'i: kullanıcının onayladığı değer + şart + bitiş tarihi."""
    if not offer_terms or offer_terms["type"] == "none":
        return ""
    ends = offer_terms.get("ends")
    return "\n".join(filter(None, [
        offer_terms.get("value"), offer_terms.get("conditions"), format_ends_tr(ends) if ends else None,
    ]))


def content_campaign_context(
    spec: dict[str, Any], idea_payload: dict[str, Any], offer_terms: dict[str, Any]
) -> dict[str, Any]:
    """İçerik prompt'una giren kampanya bağlamı. Fikrin offer_type/offer_rationale'ı bilinçli YOK:
    teklifin tek kaynağı kullanıcının kesinleştirdiği offer_terms'tür (teklifsiz devamda da)."""
    return {
        "usage": spec["usage"],
        "objective": spec["objective"],
        "destination": spec["destination"],
        "promoted_item": spec["promoted_item"],
        "audience": spec["audience"],
        "focus_note": spec["focus_note"],
        "included_features": spec.get("included_features"),
        "cta_type": idea_payload["cta_type"],
        "offer_terms": offer_terms,
    }


def content_campaign_payload(context: dict[str, Any]) -> dict[str, Any]:
    """İçerik satırına yazılan snapshot (repair, revizyon ve export buradan okur)."""
    return {"offer_terms": context["offer_terms"], "cta_type": context["cta_type"], "usage": context["usage"]}


_IMAGE_RATIO = {"post": "4:5", "story": "9:16", "reels": "9:16", "short": "9:16", "video": "16:9"}


def finalize_image_prompt(text: str, content_format: str) -> str:
    """Görsel prompt'una en-boy oranını KOD ekler (idempotent)."""
    if content_format == "carousel":
        suffix = "Aspect ratio 4:5. This is the cover slide; keep the same visual style for the remaining slides."
    elif content_format in ("video", "reels", "short"):
        suffix = f"Aspect ratio {_IMAGE_RATIO[content_format]}. Video cover image."
    else:
        suffix = f"Aspect ratio {_IMAGE_RATIO.get(content_format, '16:9')}."
    text = text.strip()
    return text if text.endswith(suffix) else f"{text} {suffix}"


def closing_card_text(offer_terms: Optional[dict[str, Any]]) -> Optional[str]:
    """Video kapanış kartı: kilitli tekliften KODLA kurulur (model yazmaz; "Son gün" vb. çıkmaz)."""
    if not offer_terms or offer_terms.get("type") in (None, "none"):
        return None
    lines = [
        f"{offer_terms['applies_to']} için {offer_terms['value']}",
        offer_terms.get("conditions"),
        offer_terms.get("ends") and f"{format_ends_tr(offer_terms['ends'])} tarihine kadar",
    ]
    return "\n".join(line for line in lines if line)


def overlay_display(
    overlay: Optional[dict[str, Any]], campaign_payload: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Görsel üstü metnin teslim biçimi: AI'nin başlık/fayda/CTA'sı + kilitli tekliften KODLA
    kurulan teklif ve şart satırları (oran/şart/tarih modele yeniden yazdırılmaz)."""
    if not isinstance(overlay, dict):
        return None
    terms = (campaign_payload or {}).get("offer_terms") or {}
    offer_line = terms_line = None
    if terms.get("type") not in (None, "none"):
        offer_line = f"{terms['applies_to']} için {terms['value']}"
        parts = [terms.get("conditions"), terms.get("ends") and f"{format_ends_tr(terms['ends'])} tarihine kadar"]
        terms_line = " · ".join(p for p in parts if p) or None
    return {
        "headline": overlay.get("headline"),
        "benefit": overlay.get("benefit"),
        "offer_line": offer_line,
        "terms_line": terms_line,
        "cta": overlay.get("cta"),
    }
