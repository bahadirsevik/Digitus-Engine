"""Arastirma iddialarini kaynak metninde arar. Karari KOD verir, model degil.

Otomatik kontrol yalniz ADAY bulur: rakam kaynakta geciyor mu? Baglam cumlesi
analiste gosterilir; rakamin dogru sey icin gectigini insan onaylar (pilot: 19
iddiadan 1'i yanlis eslesmeydi).
"""
from __future__ import annotations

import io
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from app.core.url_guard import HTML_TYPES, FetchBudget, FetchBudgetExceeded, UnsafeUrlError, safe_fetch

USER_AGENT = "Mozilla/5.0 (compatible; DigitusAudit/1.0)"
SOURCE_TYPES = HTML_TYPES | {"application/pdf", "text/plain"}
MAX_SOURCES = 25
MAX_SOURCE_BYTES = 8_000_000  # resmi raporlar buyuk PDF olabiliyor
MAX_PDF_PAGES = 60
# Daha kisa rakam her metinde gecer: "bulundu" denmez, elle kontrol. Canli denemede (15.11.2026)
# "yaklasik 250" iddiasi kaynaktaki sayfa numarasiyla ("- 250 -") eslesti; esik bu yuzden 4.
MIN_DIGITS = 4

# Ilk dal: binlik ayraci bosluk olan yazim ("45 000") tek rakamdir
_NUMBER = re.compile(r"\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|\d[\d.,]*\d|\d")
# Tarih ve yil iddia degildir: 12.11.2025, 2024
_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b(?:19|20)\d{2}\b")
_SPACE = re.compile(r"\s+")


def _pdf_text(content: bytes) -> str:
    import pypdf

    try:
        pages = pypdf.PdfReader(io.BytesIO(content)).pages[:MAX_PDF_PAGES]
        return " ".join((page.extract_text() or "") for page in pages)
    except Exception:
        return ""


def fetch_source(url: str, budget: FetchBudget) -> Dict:
    """{url, final_url, kind: html|pdf|text|unreadable, text}. Butce asimi yukari firlar."""
    try:
        result = safe_fetch(url, allowed_types=SOURCE_TYPES, headers={"User-Agent": USER_AGENT},
                            max_bytes=MAX_SOURCE_BYTES, budget=budget)
    except FetchBudgetExceeded:
        raise
    except UnsafeUrlError as exc:
        return {"url": url, "final_url": url, "kind": "unreadable", "text": "", "reason": str(exc)[:120]}
    if result.content_type == "application/pdf":
        text = _pdf_text(result.content)
        kind = "pdf" if text.strip() else "unreadable"
    elif result.content_type == "text/plain":
        text, kind = result.text, "text"
    else:
        # Ham bayttan: sunucu kodlama bildirmezse bs4 kendi tespit eder (pilot dersi)
        soup = BeautifulSoup(result.content or result.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text, kind = soup.get_text(" ", strip=True), "html"
    return {"url": url, "final_url": result.final_url, "kind": kind, "text": _SPACE.sub(" ", text)}


def fetch_sources(urls: List[str], budget: FetchBudget) -> List[Dict]:
    sources: List[Dict] = []
    for url in list(dict.fromkeys(urls))[:MAX_SOURCES]:
        try:
            sources.append(fetch_source(url, budget))
        except FetchBudgetExceeded:
            break  # eldeki kaynaklarla devam: okunamayan kaynak "bulunamadi" sonucunu verir
    return sources


def numbers_in(text: str) -> List[str]:
    """Metindeki rakamlar (tarih ve yillar haric)."""
    return _NUMBER.findall(_DATE.sub(" ", text or ""))


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number)


def _pattern(number: str) -> re.Pattern:
    # Bicim korunur (750 ile 750,00 ayni sey degil); binlik ayraci nokta/bosluk/yok olabilir.
    parts = [re.escape(part) for part in re.split(r"[.\s]", number) if part]
    return re.compile(r"(?<![\d.,])" + r"[.\s ]?".join(parts) + r"(?![.,]?\d)")


def find_number(number: str, sources: List[Dict], year: Optional[str] = None) -> Optional[Dict]:
    """Rakamin gectigi ilk kaynak: {source_url, context, year_in_context}."""
    pattern = _pattern(number)
    for source in sources:
        match = pattern.search(source["text"])
        if not match:
            continue
        text = source["text"]
        year_digits = _digits(year or "")[:4]
        nearby = text[max(0, match.start() - 300): match.end() + 300]
        return {
            "source_url": source["final_url"],
            "context": text[max(0, match.start() - 90): match.end() + 70].strip(),
            "year_in_context": (year_digits in nearby) if len(year_digits) == 4 else None,
        }
    return None


def check_claim(value: str, sources: List[Dict], year: Optional[str] = None) -> Dict:
    """Sayisal iddia: {status: found|short|not_found|no_number, source_url, context, year_in_context}."""
    # Tek haneli rakam da aranir ("5 sube ve 45.000 hekim"): bulunamazsa iddia reddedilir,
    # bulunursa kisa rakam olarak analist onayina kalir.
    numbers = numbers_in(str(value or ""))
    if not numbers:
        return {"status": "no_number"}
    # Degerdeki rakamlarin HEPSI aranir ("45.000 ile 99.999 arasi"): ilkinin bulunmasi digerlerini tasimaz.
    hits = [find_number(n, sources, year) for n in numbers]
    if not all(hits):
        return {"status": "not_found"}
    short = any(len(_digits(n)) < MIN_DIGITS for n in numbers)
    context = " … ".join(dict.fromkeys(hit["context"] for hit in hits))
    return {"status": "short" if short else "found", **hits[0], "context": context}


def text_numbers_verified(text: str, sources: List[Dict]) -> Optional[bool]:
    """Anlati metnindeki rakamlarin HEPSI kaynakta geciyor mu? Rakam yoksa None."""
    numbers = numbers_in(text)  # tek haneliler dahil: "5 subesi var" kaynaksiz gecmez
    if not numbers:
        return None
    return all(find_number(n, sources) for n in numbers)


def quote_found(quote: str, sources: List[Dict]) -> bool:
    """Ornek ifade kaynakta birebir geciyor mu? (bosluk ve buyuk/kucuk harf farki yok sayilir)"""
    # Kaynak "..." ile kisaltilmis olabilir: her parca ayri aranir, hepsi ayni kaynakta gecmeli.
    parts = [part for part in (_fold(p) for p in re.split(r"\.{3}|…", quote or "")) if part]
    if not parts or sum(len(part) for part in parts) < 12:
        return False
    return any(all(part in _fold(source["text"]) for part in parts) for source in sources)


def _fold(text: str) -> str:
    """Karsilastirma bicimi: tirnak/kesme turleri tek tipe, bosluklar tek bosluga, kucuk harf."""
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "\u00a0": " "}))
    return _SPACE.sub(" ", text).strip(" \"'.").casefold()
