"""Politika özgüllük katmanı (`policy-specificity-v1`) — TİPLİ ve SÜRÜMLÜ.

Kanıt zinciri: `benchmark/faz_e_profile/gr7_resolver_decision_record_v1.json`
(mühür 604888df…). GR-7 holdout'unda ham V2 15/20 pozitif korurken resolver
17/20'ye çıkardı, 15/15 politika negatifini dışladı, tekrar farkı 0 kaldı.

SÖZLEŞME SINIRLARI (kullanıcı kararı, 04.08):
  - Global bayrak (`ENABLE_POLICY_SPECIFICITY_RESOLVER`) varsayılan KAPALI.
  - Bayrak TEK BAŞINA yetmez: workspace profilinde AÇIK opt-in şart.
  - İlk kapsam ADS/SEO; SOCIAL kapsam DIŞI (politika SOCIAL isterse GEÇERSİZ).
  - Çekirdekler serbest metinden HER KOŞUDA TÜRETİLMEZ; workspace profilinde
    TİPLİ + SÜRÜMLÜ + ONAYLI politika olarak durur.
  - Geçersiz/eksik politikada resolver UYGULANMAZ: baseline davranış +
    TİPLİ audit sebebi.
  - Bu modül şu an YALNIZ SHADOW hesaplar; canlı havuza uygulama AYRI ONAY
    ister (`live_application_approved` sözleşmede YOK).

Politika şekli (`profile_data["policy_specificity"]`):
    {
      "enabled": true,
      "contract_version": "policy-specificity-v1",
      "policy_version": 1,
      "approved_on": "2026-08-04",
      "channels": ["ADS", "SEO"],
      "exclusion_cores": [
        {"id": "sampuan_sorgusu",
         "patron_theme": "Beyaz kapatıcı şampuan ve diğer şampuan sorguları",
         "all": [["sampuan"]]}
      ],
      "protection_cores": [
        {"id": "beyaz_gri_kapatici",
         "protected_theme": "Beyaz ve gri saç problemi",
         "all": [["beyaz", "gri"], ["kapatici", "boya", "boyama"]]}
      ]
    }
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

POLICY_KEY = "policy_specificity"
POLICY_CONTRACT_VERSION = "policy-specificity-v1"
SUPPORTED_CHANNELS = ("ADS", "SEO")

# Tipli audit sebepleri — resolver UYGULANMADIGINDA neden gorunur olsun
REASON_FLAG_OFF = "SPECIFICITY_FLAG_OFF"
REASON_POLICY_MISSING = "SPECIFICITY_POLICY_MISSING"
REASON_POLICY_DISABLED = "SPECIFICITY_POLICY_DISABLED"
REASON_CONTRACT_MISMATCH = "SPECIFICITY_CONTRACT_VERSION_MISMATCH"
REASON_INVALID_STRUCTURE = "SPECIFICITY_POLICY_INVALID_STRUCTURE"
REASON_NOT_APPROVED = "SPECIFICITY_POLICY_NOT_APPROVED"
REASON_CHANNEL_UNSUPPORTED = "SPECIFICITY_CHANNEL_NOT_SUPPORTED"
REASON_NO_CORES = "SPECIFICITY_POLICY_HAS_NO_CORES"
REASON_THEME_NOT_APPROVED = "SPECIFICITY_CORE_THEME_NOT_APPROVED"

DECISION_SOURCE_EXCLUSION = "deterministic_exclusion"
DECISION_SOURCE_PROTECTION = "deterministic_protection"
DECISION_SOURCE_MODEL = "model_theme_decision"


class SpecificityPolicy:
    """Doğrulanmış, sürümlü politika. Serbest metinden TÜRETİLMEZ."""

    __slots__ = ("policy_version", "approved_on", "channels",
                 "exclusion_cores", "protection_cores", "fingerprint")

    def __init__(self, *, policy_version: int, approved_on: str,
                 channels: Tuple[str, ...],
                 exclusion_cores: List[Dict[str, Any]],
                 protection_cores: List[Dict[str, Any]]):
        self.policy_version = policy_version
        self.approved_on = approved_on
        self.channels = channels
        self.exclusion_cores = exclusion_cores
        self.protection_cores = protection_cores
        self.fingerprint = _fingerprint(policy_version, approved_on, channels,
                                        exclusion_cores, protection_cores)

    def covers(self, channels) -> bool:
        """Satırın TÜM kanalları politika kapsamında olmalı.

        Codex turu 20 #1: `any(...)` karma satırı (`["ADS","SOCIAL"]`) ADS
        yüzünden kabul ediyordu; oysa satır SOCIAL kanalını da taşıyor ve
        SOCIAL bilinçli olarak KAPSAM DIŞI. Kanalsız satır da kapsam DIŞI
        (neyin kapsandığı belirsizken karar üretilmez).
        """
        normalized = {str(channel).upper() for channel in (channels or [])}
        if not normalized:
            return False
        return normalized.issubset(set(self.channels))

    def summary(self) -> Dict[str, Any]:
        return {
            "contract_version": POLICY_CONTRACT_VERSION,
            "policy_version": self.policy_version,
            "approved_on": self.approved_on,
            "channels": list(self.channels),
            "exclusion_core_ids": [c["id"] for c in self.exclusion_cores],
            "protection_core_ids": [c["id"] for c in self.protection_cores],
            "fingerprint": self.fingerprint,
        }


def _fingerprint(policy_version, approved_on, channels, exclusion_cores,
                 protection_cores) -> str:
    payload = {
        "contract_version": POLICY_CONTRACT_VERSION,
        "policy_version": policy_version,
        "approved_on": approved_on,
        "channels": list(channels),
        "exclusion_cores": exclusion_cores,
        "protection_cores": protection_cores,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Turkce BUYUK harf tuzagi: normalize_turkish("DİĞER") -> "di ger" (noktali
# I ayrisiyor). Tema/on-ek karsilastirmasi buyuk-kucuk farkindan etkilenmemeli.
_TR_UPPER_MAP = str.maketrans({"İ": "i", "I": "ı", "Ğ": "ğ", "Ü": "ü",
                               "Ş": "ş", "Ö": "ö", "Ç": "ç"})


def _canonical_text(text: str) -> str:
    """Kanonik metin anahtarı — Türkçe büyük harfler dahil."""
    return normalize_turkish(str(text or "").translate(_TR_UPPER_MAP).lower())


def _canonical_option(option: str) -> str:
    """Ön-ek de keyword token'ı ile AYNI kanonikleştirmeden geçer.

    Codex turu 20 #5: politika 'şampuan' yazarsa yalnız `.lower()` ile
    normalize edilmiş `sampuan` token'ına ASLA eşleşmezdi (sessiz ölü kural).
    `__digit__` sentinel'i korunur.
    """
    text = option.strip()
    if text == "__digit__":
        return text
    return _canonical_text(text).strip()


def _valid_groups(groups: Any) -> Optional[List[List[str]]]:
    """`all` grupları: liste-içinde-liste, boş olmayan string ön-ekler."""
    if not isinstance(groups, list) or not groups:
        return None
    parsed: List[List[str]] = []
    for group in groups:
        if not isinstance(group, list) or not group:
            return None
        options = []
        seen_options = set()
        for option in group:
            if isinstance(option, bool) or not isinstance(option, str):
                return None
            canonical = _canonical_option(option)
            if not canonical:
                return None
            if canonical in seen_options:
                continue
            seen_options.add(canonical)
            options.append(canonical)
        if not options:
            return None
        parsed.append(options)
    return parsed


def _approved_theme_keys(values: Any) -> set:
    if not isinstance(values, list):
        return set()
    return {_canonical_text(v) for v in values if str(v).strip()}


def _parse_cores(raw: Any, theme_field: str,
                 approved_themes: set) -> Optional[List[Dict[str, Any]]] | str:
    """Çekirdekleri doğrular; tema ONAY DIŞIYSA tipli sebep döndürür.

    Codex turu 20 #2: çekirdeğin bağlandığı tema patronun ONAYLI listesinde
    olmalı — aksi halde uydurma ya da bayat bir çekirdek üretimde geçerli
    sayılırdı. Karşılaştırma kanonik (Türkçe normalize) yapılır.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    cores: List[Dict[str, Any]] = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        core_id = entry.get("id")
        theme = entry.get(theme_field)
        if isinstance(core_id, bool) or not isinstance(core_id, str):
            return None
        core_id = core_id.strip()
        groups = _valid_groups(entry.get("all"))
        if (not core_id or isinstance(theme, bool)
                or not isinstance(theme, str) or not theme.strip()
                or groups is None):
            return None
        if core_id in seen:      # trim SONRASI tekillik
            return None
        seen.add(core_id)
        if _canonical_text(theme) not in approved_themes:
            return REASON_THEME_NOT_APPROVED
        cores.append({"id": core_id, theme_field: theme.strip(),
                      "all": groups})
    return cores


def load_policy(profile_data: Optional[Dict[str, Any]], *, flag_enabled: bool
                ) -> Tuple[Optional[SpecificityPolicy], Optional[str]]:
    """(policy, skip_reason) döndürür. Politika geçersizse policy=None.

    Global bayrak TEK BAŞINA yetmez; workspace opt-in şarttır.
    """
    if not flag_enabled:
        return None, REASON_FLAG_OFF
    raw = (profile_data or {}).get(POLICY_KEY)
    if not isinstance(raw, dict) or not raw:
        return None, REASON_POLICY_MISSING
    if raw.get("enabled") is not True:
        return None, REASON_POLICY_DISABLED
    if raw.get("contract_version") != POLICY_CONTRACT_VERSION:
        return None, REASON_CONTRACT_MISMATCH

    approved_on = raw.get("approved_on")
    if not isinstance(approved_on, str) or not approved_on.strip():
        return None, REASON_NOT_APPROVED
    policy_version = raw.get("policy_version")
    # `True` Python'da int sayilir — bool ACIKCA reddedilir (Codex #5)
    if (isinstance(policy_version, bool)
            or not isinstance(policy_version, int) or policy_version < 1):
        return None, REASON_INVALID_STRUCTURE

    channels_raw = raw.get("channels")
    if not isinstance(channels_raw, list) or not channels_raw:
        return None, REASON_INVALID_STRUCTURE
    channels = tuple(sorted({str(c).upper() for c in channels_raw}))
    if any(channel not in SUPPORTED_CHANNELS for channel in channels):
        # SOCIAL bilincli olarak KAPSAM DISI (ilk faz karari)
        return None, REASON_CHANNEL_UNSUPPORTED

    # Cekirdek temalari patronun ONAYLI listelerine oturmali
    approved_exclude = _approved_theme_keys(
        (profile_data or {}).get("exclude_themes"))
    approved_protected = _approved_theme_keys(
        (profile_data or {}).get("protected_themes"))
    exclusion_cores = _parse_cores(raw.get("exclusion_cores"), "patron_theme",
                                   approved_exclude)
    protection_cores = _parse_cores(raw.get("protection_cores"),
                                    "protected_theme", approved_protected)
    for parsed in (exclusion_cores, protection_cores):
        if parsed == REASON_THEME_NOT_APPROVED:
            return None, REASON_THEME_NOT_APPROVED
    if exclusion_cores is None or protection_cores is None:
        return None, REASON_INVALID_STRUCTURE
    if not exclusion_cores and not protection_cores:
        return None, REASON_NO_CORES

    return SpecificityPolicy(
        policy_version=policy_version, approved_on=approved_on.strip(),
        channels=channels, exclusion_cores=exclusion_cores,
        protection_cores=protection_cores), None


def _tokens(text: str) -> List[str]:
    return _canonical_text(text).split()


def _group_hit(tokens: List[str], options: List[str]) -> bool:
    for option in options:
        if option == "__digit__":
            if any(any(ch.isdigit() for ch in token) for token in tokens):
                return True
        elif any(token.startswith(option) for token in tokens):
            return True
    return False


def match_cores(keyword: str, cores: List[Dict[str, Any]]) -> List[str]:
    """Eşleşen çekirdek id'leri (tüm gruplardan EN AZ BİR ön-ek tutmalı)."""
    tokens = _tokens(keyword)
    return [core["id"] for core in cores
            if all(_group_hit(tokens, group) for group in core["all"])]


def resolve(keyword: str, model_applied: bool,
            policy: SpecificityPolicy) -> Dict[str, Any]:
    """Özgüllük sıralı karar: dışlama -> koruma -> modelin tema kararı."""
    exclusion_hits = match_cores(keyword, policy.exclusion_cores)
    if exclusion_hits:
        return {"decision": False, "source": DECISION_SOURCE_EXCLUSION,
                "cores": exclusion_hits}
    protection_hits = match_cores(keyword, policy.protection_cores)
    if protection_hits:
        return {"decision": True, "source": DECISION_SOURCE_PROTECTION,
                "cores": protection_hits}
    return {"decision": bool(model_applied), "source": DECISION_SOURCE_MODEL,
            "cores": []}


def apply_shadow(brand_filter_result: Dict[str, Any],
                 profile_data: Optional[Dict[str, Any]], *,
                 flag_enabled: bool) -> Dict[str, Any]:
    """SHADOW zenginleştirme: brand_filter ÇIKTISINI değiştirmeden sütun ekler.

    ÖNEMLİ: bu katman `app/core/channel/brand_filter.py` DOSYASINA DOKUNMAZ.
    O dosya mühürlü deney artifact'larında (plan v6 / holdout v5) SHA ile
    pinlidir; üretim entegrasyonu için değiştirilseydi tüm replay/holdout
    koşucuları — tasarım gereği — kendi kanıtlarını reddederdi. Bu yüzden
    zenginleştirme filtre DÖNÜŞÜ üzerinde, çağıran katmanda yapılır.

    `applied_decision` ASLA değişmez; yalnız karşı-olgusal sütunlar eklenir.
    Canlı havuza uygulama AYRI ONAY ister.
    """
    # Codex turu 20 #3: BAYRAK KAPALIYKEN nesneye HIC DOKUNULMAZ — telemetri
    # alani bile eklenmez; flag-off davranisi BUGUNKU ile bit-bit aynidir.
    if not flag_enabled:
        return brand_filter_result

    decisions = brand_filter_result.get("decisions")
    if not isinstance(decisions, list):
        return brand_filter_result

    policy, reason = load_policy(profile_data, flag_enabled=True)

    # Codex turu 20 #4: ATOMIK. Once TUM yamalar hesaplanir; yari yolda hata
    # cikarsa hicbir satir degismemis olur (engine orijinal sonucu dondurur).
    patches: List[Dict[str, Any]] = []
    evaluated = changes = kept = 0
    if policy is None:
        summary = {"applied": False, "mode": "shadow_only",
                   "skipped_reason": reason,
                   "live_application": "NOT_APPROVED"}
        patches = [{"specificity_applied": False,
                    "specificity_skipped_reason": reason}
                   for _ in decisions]
    else:
        for row in decisions:
            if not isinstance(row, dict):
                raise TypeError("decisions satiri dict olmali")
            channels = row.get("channels") or []
            if not policy.covers(channels):
                patches.append({
                    "specificity_applied": False,
                    "specificity_skipped_reason": REASON_CHANNEL_UNSUPPORTED,
                })
                continue
            applied = bool(row.get("applied_decision"))
            verdict = resolve(str(row.get("keyword") or ""), applied, policy)
            patches.append({
                "specificity_applied": True,
                "specificity_mode": "shadow_only",
                "resolver_decision": verdict["decision"],
                "decision_source": verdict["source"],
                "matched_cores": verdict["cores"],
                "resolver_changes_decision": verdict["decision"] != applied,
                "specificity_policy_fingerprint": policy.fingerprint,
            })
            evaluated += 1
            changes += int(verdict["decision"] != applied)
            kept += int(bool(verdict["decision"]))
        summary = {
            "applied": True,
            "mode": "shadow_only",
            "live_application": "NOT_APPROVED",
            "policy": policy.summary(),
            "evaluated": evaluated,
            "counterfactual_changes": changes,
            "would_keep": kept,
            "note": ("SHADOW: uygulanan karar DEĞİŞMEDİ; canlı havuza "
                     "uygulama ayrı onay ister."),
        }

    # Buradan sonrasi saf yazim: kismi zenginlesme imkansiz
    for row, patch in zip(decisions, patches):
        row.update(patch)
    brand_filter_result["policy_specificity"] = summary
    return brand_filter_result


def policy_fingerprint(profile_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Havuz künyesi / bayatlama için: geçerli politikanın fingerprint'i."""
    policy, _ = load_policy(profile_data, flag_enabled=True)
    return policy.fingerprint if policy is not None else None


def policy_identity_key(profile_data: Optional[Dict[str, Any]]) -> tuple:
    """Kanonik bayatlama anahtarı — HAM politika üzerinden.

    Fingerprint yalnız GEÇERLİ politikada üretilir; geçersiz politikanın da
    değişimi görünmelidir (ör. bozuk politika düzeltilirse havuz bayatlar).
    """
    raw = (profile_data or {}).get(POLICY_KEY)
    if raw is None:
        return ()
    blob = json.dumps(raw, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return (hashlib.sha256(blob.encode("utf-8")).hexdigest(),)
