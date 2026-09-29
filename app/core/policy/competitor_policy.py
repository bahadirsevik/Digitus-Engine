"""Rakip marka politikası (plan A + plan v13 provenance modeli).

Veri modeli (BrandProfile):
- competitor_terms (v13): [{term, status, source: domain|user,
      source_urls: [canonical_key], manual_approved: bool, created_at}]
  status TÜRETİLMİŞ alandır: approved = manual_approved OR source_urls dolu.
  Eski kayıtlarda source_urls/manual_approved YOK — reconciliation onlara
  dokunmaz (görünür kalırlar; review onayında kontrollü backfill denenir).
- competitor_url_decisions: {canonical_key: {url, term, decision, updated_at}}
  — kullanıcının URL bazlı KALICI kararı (yeniden açmada UI bundan türetilir).
- competitor_policy: {ads|seo|social: block|allow} — eksik anahtar = block

Kanonik URL anahtarı = registrable domain (tldextract; paketlenmiş PSL,
runtime network yok). ÜRÜN SÖZLEŞMESİ: blog.rakip.com / www.rakip.com /
rakip.com/x hepsi 'rakip.com' anahtarına düşer; SaaS tenant URL'leri
(firma-a.platform.com vs firma-b.platform.com) AYIRT EDİLMEZ — rakipler
normal şirket siteleri varsayılır. Unicode/punycode eşleşmesi kapsam dışı.
Boş normalize sonucu = GEÇERSİZ URL; hiçbir zaman eşleşme anahtarı olamaz.

Terim eşleşmesi substring değil, Türkçe-normalize token sınırıyla yapılır.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from loguru import logger

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

CHANNELS = ("ads", "seo", "social")
POLICY_BLOCK = "block"
POLICY_ALLOW = "allow"

TERM_SUGGESTED = "suggested"
TERM_APPROVED = "approved"
TERM_REJECTED = "rejected"

# tldextract'i lazy import et: PSL snapshot'ı paketle gelir ama modül
# yüklemesi görece pahalı; yalnız öneri üretiminde gerekir.
_tld_extractor = None


def _extractor():
    global _tld_extractor
    if _tld_extractor is None:
        import tldextract

        # suffix_list_urls=() → runtime network KAPALI, paketlenmiş PSL kullanılır
        _tld_extractor = tldextract.TLDExtract(suffix_list_urls=())
    return _tld_extractor


def _term_tokens(term: str) -> List[str]:
    return [t for t in normalize_turkish(term).split() if t]


def _candidate_terms_from_domain(url: str) -> List[str]:
    """Registrable domain'den aday terimler üretir.

    ör. https://www.f-rayscoring.com.tr/x ->
        'f-rayscoring' + boşluklu varyant 'f rayscoring'
    Jenerik parçalar otomatik ÜRETİLMEZ (tire bölmesi tek tek parça çıkarmaz);
    kullanıcı onayı zaten zorunlu.
    """
    try:
        ext = _extractor()(url or "")
    except Exception:
        return []
    core = (ext.domain or "").strip().lower()
    if not core:
        return []
    candidates = [core]
    if "-" in core:
        candidates.append(core.replace("-", " "))
    return candidates


def suggest_competitor_terms(
    competitor_urls: Optional[List[str]],
    existing_terms: Optional[List[dict]],
) -> List[dict]:
    """Yeni öneri kayıtları döndürür (mevcutlar + reddedilenler atlanır).

    Dönen liste ÇAĞIRANIN mevcut listeyle birleştirip YENİ liste olarak
    ataması içindir (JSON kolonlarında in-place mutation YASAK).
    """
    known = {
        normalize_turkish(entry.get("term", ""))
        for entry in (existing_terms or [])
    }
    suggestions: List[dict] = []
    for url in competitor_urls or []:
        for term in _candidate_terms_from_domain(url):
            norm = normalize_turkish(term)
            if not norm or norm in known:
                continue
            known.add(norm)
            suggestions.append({
                "term": term,
                "status": TERM_SUGGESTED,
                "source": "domain",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
    return suggestions


def approved_competitor_terms(profile) -> List[str]:
    """Yalnız kullanıcı onaylı terimler (filtrenin tek girdisi)."""
    return [
        entry.get("term", "")
        for entry in (getattr(profile, "competitor_terms", None) or [])
        if entry.get("status") == TERM_APPROVED and entry.get("term")
    ]


def competitor_policy_for(profile) -> Dict[str, str]:
    """Kanal politikası; eksik/bozuk anahtar = block (güvenli taraf)."""
    raw = getattr(profile, "competitor_policy", None) or {}
    return {
        ch: (POLICY_ALLOW if raw.get(ch) == POLICY_ALLOW else POLICY_BLOCK)
        for ch in CHANNELS
    }


def normalize_competitor_url(url: str) -> str:
    """Kanonik karar anahtarı: registrable domain.

    Parse edilemeyen / registrable domain'i olmayan girdi → '' (GEÇERSİZ —
    çağıran reddetmeli; boş string asla eşleşme anahtarı olarak kullanılmaz).
    """
    if not url:
        return ""
    raw = str(url).strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = f"//{raw}"
    try:
        host = (urlsplit(raw).hostname or "").strip().lower()
    except ValueError:
        return ""
    if not host:
        return ""
    try:
        ext = _extractor()(host)
    except Exception:
        return ""
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}"


def _derive_status(entry: dict) -> dict:
    """status'u provenance'tan türet: approved = manual_approved OR source_urls dolu.

    Bağımsız status YAZILMAZ — 'manuel onayı kaldır ama URL hâlâ destekliyorsa
    approved kal; URL de kalkınca rejected ol' ancak böyle çelişkisiz çalışır.
    """
    approved = bool(entry.get("manual_approved")) or bool(entry.get("source_urls"))
    return {**entry, "status": TERM_APPROVED if approved else TERM_REJECTED}


def _upsert(
    entries: Optional[List[dict]],
    term: str,
    *,
    manual_approved: Optional[bool] = None,
    source_url: Optional[str] = None,
) -> List[dict]:
    norm = normalize_turkish(term)
    norm_url = normalize_competitor_url(source_url) if source_url else None
    updated: List[dict] = []
    found = False
    for entry in entries or []:
        if normalize_turkish(entry.get("term", "")) != norm:
            updated.append(entry)
            continue
        found = True
        merged = dict(entry)
        if manual_approved is not None:
            merged["manual_approved"] = manual_approved
        if norm_url:
            urls = list(merged.get("source_urls") or [])
            if norm_url not in urls:
                urls.append(norm_url)
            merged["source_urls"] = urls
        updated.append(_derive_status(merged))
    if not found:
        entry = {
            "term": term,
            "source": "user",
            "manual_approved": bool(manual_approved),
            "source_urls": [norm_url] if norm_url else [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        updated.append(_derive_status(entry))
    return updated


def set_manual_approval(entries: Optional[List[dict]], term: str, approved: bool) -> List[dict]:
    """Gelişmiş ayarlar: manuel ekle (True) / 'Manuel onayı kaldır' (False).

    source_urls'e DOKUNMAZ — terim bir rakip URL kararından hâlâ destekleniyorsa
    approved kalır (UI bunu provenance alanlarından açıklar)."""
    return _upsert(entries, term, manual_approved=approved)


def add_source_url(entries: Optional[List[dict]], term: str, source_url: str) -> List[dict]:
    """Rakipler kartı 'Rakip olarak engelle' kararı. manual_approved'a DOKUNMAZ."""
    return _upsert(entries, term, source_url=source_url)


def _release_source_url(entries: Optional[List[dict]], url: str) -> List[dict]:
    """url'yi HER kayıttan source_urls'ten çıkarır, status'u yeniden türetir.

    manual_approved=True kayıt approved kalır (manuel sahiplik URL kalkınca
    kaybolmaz). source_urls alanı hiç OLMAYAN legacy kayıtlara dokunulmaz.
    """
    norm = normalize_competitor_url(url)
    if not norm:
        return list(entries or [])
    result: List[dict] = []
    for entry in entries or []:
        urls = entry.get("source_urls") or []
        if norm in urls:
            result.append(_derive_status({
                **entry,
                "source_urls": [u for u in urls if u != norm],
            }))
        else:
            result.append(entry)
    return result


def reconcile_url_decision(
    entries: Optional[List[dict]],
    source_url: str,
    approved_term: Optional[str],
) -> List[dict]:
    """Bir URL için karar (yeniden) verilince: önce eski atamayı serbest bırak,
    SONRA (varsa) yeni terimi bu URL ile destekle.

    approved_term=None (not_competitor VEYA boş isim) → yalnız serbest bırakma;
    GLOBAL reject YAZILMAZ (manuel onaylı aynı terim yanlışlıkla kapanmasın).
    """
    entries = _release_source_url(entries, source_url)
    if approved_term:
        entries = add_source_url(entries, approved_term, source_url)
    return entries


def reconcile_removed_urls(
    entries: Optional[List[dict]],
    current_urls: Optional[List[str]],
) -> List[dict]:
    """competitor_urls listesinden tamamen çıkarılmış URL'lerin temizliği."""
    entries = list(entries or [])
    current_norm = {
        key for key in (normalize_competitor_url(u) for u in (current_urls or []) if u)
        if key
    }
    tracked = {u for e in entries for u in (e.get("source_urls") or [])}
    for url_key in tracked - current_norm:
        entries = _release_source_url(entries, url_key)
    return entries


def backfill_legacy_source_urls(
    entries: Optional[List[dict]],
    current_urls: Optional[List[str]],
) -> List[dict]:
    """Legacy (source_urls alanı olmayan) domain-türetimli kayıtlar için
    kontrollü backfill: terim, güncel URL'lerden birinin domain adayıyla
    (normalize_turkish) eşleşiyorsa o URL anahtarı source_urls'e yazılır ve
    kayıt normal yaşam döngüsüne katılır. Eşleşmeyen legacy kayıt DOKUNULMAZ
    (Gelişmiş ayarlar'da 'eski kayıt' rozetiyle görünür kalır)."""
    result: List[dict] = []
    url_candidates: List[tuple] = []  # (canonical_key, {normalized_candidate_terms})
    for url in current_urls or []:
        key = normalize_competitor_url(url)
        if not key:
            continue
        cands = {normalize_turkish(t) for t in _candidate_terms_from_domain(url)}
        url_candidates.append((key, cands))
    for entry in entries or []:
        if "source_urls" in entry:
            result.append(entry)
            continue
        norm_term = normalize_turkish(entry.get("term", ""))
        matched_keys = [key for key, cands in url_candidates if norm_term in cands]
        if matched_keys:
            result.append(_derive_status({
                **entry,
                "source_urls": matched_keys,
                "manual_approved": bool(entry.get("manual_approved")),
            }))
        else:
            result.append(entry)
    return result


def is_legacy_term(entry: dict) -> bool:
    """v13 öncesi şema: source_urls alanı hiç yok (otomatik reconciliation dışı)."""
    return "source_urls" not in entry


def match_term(text: str, terms: List[str]) -> Optional[str]:
    """Token-sınırlı eşleşme: term'in token dizisi, metnin token dizisinde
    ARDIŞIK olarak geçiyorsa eşleşir. Substring eşleşmesi YOK
    ('fin' -> 'finans' eşleşmez; 'hisse net' -> 'hisse net takip' eşleşir).
    """
    if not text or not terms:
        return None
    text_tokens = _term_tokens(text)
    if not text_tokens:
        return None
    for term in terms:
        term_tokens = _term_tokens(term)
        if not term_tokens or len(term_tokens) > len(text_tokens):
            continue
        for i in range(len(text_tokens) - len(term_tokens) + 1):
            if text_tokens[i:i + len(term_tokens)] == term_tokens:
                return term
    return None
