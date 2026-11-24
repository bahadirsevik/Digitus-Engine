"""Denetim icin ag katmani: SSRF-korumali sayfa okuma, robots.txt uyumu, TLS, alan adi kaydi.

Tum sayfa istekleri `safe_fetch` uzerinden gider (SSRF + boyut + butce).
"""
from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.digital_audit.signals import BLOCK_STATUSES, detect_block
from app.core.site_analyzer.crawler import (
    ACCEPT_ENCODING_HEADER,
    MAX_HTML_LENGTH,
    SUPPORTED_CONTENT_ENCODINGS,
    TEXT_TYPES,
)
from app.core.url_guard import (
    HTML_TYPES,
    FetchBudget,
    FetchBudgetExceeded,
    HttpStatusError,
    TlsVerificationError,
    UnsafeUrlError,
    UnsupportedContentType,
    assert_url_host_is_public,
    safe_fetch,
)

USER_AGENT = "Mozilla/5.0 (compatible; DigitusAudit/1.0)"
REQUEST_DELAY_SECONDS = 0.5  # rakip sitelere nazik tarama

# Sayfa okuma sonuclari
OK, BLOCKED, ROBOTS, ERROR = "ok", "blocked", "robots", "error"


@dataclass
class PageResponse:
    status: str  # ok | blocked | robots | error
    url: str
    html: str = ""
    http_status: Optional[int] = None
    reason: Optional[str] = None


class SiteFetcher:
    """Bir hedefin tum sayfa isteklerini tek butce + robots kurallariyla yapar."""

    def __init__(self, origin: str, budget: FetchBudget, delay: float = REQUEST_DELAY_SECONDS):
        self.origin = origin
        self.budget = budget
        self.delay = delay
        self.headers = {"User-Agent": USER_AGENT, "Accept-Encoding": ACCEPT_ENCODING_HEADER}
        self.robots_present: Optional[bool] = None
        self._robots = RobotFileParser()
        self._robots.parse([])
        self._load_robots()

    def _load_robots(self) -> None:
        try:
            result = safe_fetch(f"{self.origin}/robots.txt", allowed_types=TEXT_TYPES,
                                headers=self.headers, budget=self.budget)
            self.robots_present = True
            self._robots.parse(result.text.splitlines())
        except FetchBudgetExceeded:
            raise
        except HttpStatusError as exc:
            # 404 = dosya yok. Engel kodlarinda varligi bilinemez.
            self.robots_present = None if exc.status_code in BLOCK_STATUSES else False
        except UnsupportedContentType:
            self.robots_present = False  # HTML donduruyor: gercek robots.txt degil
        except UnsafeUrlError:
            self.robots_present = None

    def get_html(self, url: str) -> PageResponse:
        """Sayfayi okur. Butce asiminda FetchBudgetExceeded firlatir (cagiran durur)."""
        if not self._robots.can_fetch(USER_AGENT, url):
            return PageResponse(ROBOTS, url, reason="robots_disallow")
        if self.delay:
            time.sleep(self.delay)
        try:
            result = safe_fetch(url, allowed_types=HTML_TYPES, headers=self.headers,
                                max_bytes=MAX_HTML_LENGTH, budget=self.budget)
        except FetchBudgetExceeded:
            raise
        except HttpStatusError as exc:
            status = BLOCKED if exc.status_code in BLOCK_STATUSES else ERROR
            return PageResponse(status, url, http_status=exc.status_code, reason=f"http_{exc.status_code}")
        except TlsVerificationError:
            return PageResponse(ERROR, url, reason="tls_error")
        except UnsupportedContentType:
            return PageResponse(ERROR, url, reason="not_html")
        except UnsafeUrlError as exc:
            return PageResponse(ERROR, url, reason=f"unreachable: {exc}"[:160])
        if result.content_encoding not in SUPPORTED_CONTENT_ENCODINGS:
            return PageResponse(ERROR, result.final_url, reason="unsupported_encoding")
        block = detect_block(result.text)
        if block:
            return PageResponse(BLOCKED, result.final_url, http_status=result.status, reason=block)
        return PageResponse(OK, result.final_url, html=result.text, http_status=result.status)

    def check_link(self, url: str) -> Dict:
        """Site ICI baglanti canli mi? {state: ok|broken|blocked|unknown|skipped, status}.

        robots.txt ile yasaklanan adrese istek GONDERILMEZ (skipped). Yalniz bu sitenin
        robots kurallari bilindigi icin dis alan adlari cagiran tarafinda elenir.
        """
        if not self._robots.can_fetch(USER_AGENT, url):
            return {"state": "skipped"}
        if self.delay:
            time.sleep(self.delay / 2)  # 40 baglantiyi ayni siteye art arda yagdirma
        try:
            safe_fetch(url, allowed_types=HTML_TYPES, headers=self.headers,
                       max_bytes=2048, budget=self.budget)
            return {"state": "ok"}
        except FetchBudgetExceeded:
            raise
        except UnsupportedContentType:
            return {"state": "ok"}  # PDF/gorsel vb.: yanit verdi
        except HttpStatusError as exc:
            if exc.status_code in BLOCK_STATUSES or exc.status_code == 999:
                return {"state": "blocked", "status": exc.status_code}
            return {"state": "broken", "status": exc.status_code}
        except UnsafeUrlError as exc:
            # Baglanti hatasi karsi tarafin gecici sorunu olabilir: kirik demeyiz.
            return {"state": "unknown", "status": str(exc)[:80]}


def tls_info(host: str) -> Dict:
    """Sertifika gecerliligi ve bitis tarihi. Hata -> {'valid': False|None}."""
    try:
        assert_url_host_is_public(f"https://{host}/")
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return {"valid": True, "expires": expires.date().isoformat(),
                "days_left": (expires - datetime.now(timezone.utc)).days}
    except ssl.SSLCertVerificationError as exc:
        return {"valid": False, "error": str(exc)[:160]}
    except Exception as exc:  # ag hatasi: sertifika hakkinda hukum yok
        return {"valid": None, "error": f"{type(exc).__name__}"}


def domain_info(host: str) -> Dict:
    """RDAP alan adi kaydi (bitis, kayit firmasi). Sabit dis servis: rdap.org.

    shortcut: yalniz rdap.org yonlendirmesini izler; .tr gibi RDAP sunmayan
    uzantilarda bos doner (bulgu "bilinmiyor" olur). Uzanti bazli WHOIS gerekirse eklenir.
    """
    domain = registrable_domain(host)
    try:
        response = httpx.get(f"https://rdap.org/domain/{domain}", timeout=12, follow_redirects=True,
                             headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            return {"available": False, "reason": f"http_{response.status_code}"}
        data = response.json()
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__}
    out: Dict = {"available": True, "domain": domain}
    for event in data.get("events") or []:
        if event.get("eventAction") in ("expiration", "registration"):
            out[event["eventAction"]] = (event.get("eventDate") or "")[:10]
    for entity in data.get("entities") or []:
        if "registrar" in (entity.get("roles") or []):
            for item in (entity.get("vcardArray") or [None, []])[1]:
                if item and item[0] == "fn":
                    out["registrar"] = item[3]
    return out


_SECOND_LEVEL = frozenset({"com", "net", "org", "gov", "edu", "co", "gen", "web", "info", "biz",
                           "av", "dr", "bel", "pol", "k12", "name", "tv", "ac"})


def registrable_domain(host: str) -> str:
    """ornek.com -> ornek.com; www.ornek.com.tr -> ornek.com.tr.

    shortcut: ulke kodu + bilinen ikinci seviye listesi; tam Public Suffix List degil.
    Yanlis cozumleme yalniz alan adi bulgusunu "bilinmiyor" yapar. Gerekirse tldextract.
    """
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"
