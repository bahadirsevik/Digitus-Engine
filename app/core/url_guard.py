"""SSRF-korumalı URL doğrulama ve güvenli HTML fetch (plan v13).

google_ads.py'deki guard'ların core'a taşınmış hali + `safe_fetch_html`:
policy competitor preview'ı kullanıcı kontrollü URL'lere crawl yüzeyi açar;
mevcut SiteCrawler (follow_redirects=True, verify=False) bu iş için
KULLANILMAZ — redirect hedefleri doğrulanmadan takip edilir.

Katmanlama: bu modül HTTPException FIRLATMAZ; `UnsafeUrlError` üretir,
router'lar HTTP koduna çevirir.

Bilinen artık risk (bilinçli kabul): getaddrinfo kontrolü ile httpx'in kendi
DNS çözümlemesi arasında dar bir DNS-rebinding penceresi vardır; IP-pinning bu
kapsamda uygulanmamıştır.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import httpx

MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 1_000_000  # 1MB — streaming sırasında uygulanır
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 8.0


class UnsafeUrlError(Exception):
    """URL güvenlik/doğrulama ihlali (SSRF, şema, çözümlenemeyen host...)."""


class HttpStatusError(UnsafeUrlError):
    """HTTP status >= 400; çağıran (crawler) kullanıcı mesajını koda göre seçer."""

    def __init__(self, status_code: int):
        super().__init__(f"URL erişilemedi veya hata döndü: HTTP {status_code}")
        self.status_code = status_code


class TlsVerificationError(UnsafeUrlError):
    """TLS sertifika doğrulaması başarısız (verify=True bilinçli)."""


class FetchBudgetExceeded(UnsafeUrlError):
    """İstek sayısı / toplam süre bütçesi aşıldı; istek GÖNDERİLMEDEN fırlatılır."""


class FetchBudget:
    """Bir taramanın paylaşılan istek-sayısı + toplam-süre bütçesi.

    Her HTTP isteği (redirect adımları dahil) `consume()` çağırır; dönen değer
    kalan süredir ve istek timeout'unu kırpmak için kullanılır.

    Süre YUMUŞAK sınırdır: süre dolduktan sonra yeni istek ve yeni gövde parçası
    başlamaz, ama o an bekleyen tek okuma en fazla kırpılmış read timeout'u
    (<= READ_TIMEOUT) kadar sürebilir. DNS çözümlemesi (`getaddrinfo`, zaman
    aşımsız) bu sınırın DIŞINDADIR. Kötü durum ≈ total_seconds + READ_TIMEOUT + DNS.
    """

    def __init__(self, max_requests: int = 12, total_seconds: float = 45.0):
        self.max_requests = max_requests
        self.requests_used = 0
        self._deadline = time.monotonic() + total_seconds

    def consume(self) -> float:
        remaining = self._deadline - time.monotonic()
        if self.requests_used >= self.max_requests or remaining <= 0:
            raise FetchBudgetExceeded("Tarama bütçesi (istek sayısı / süre) aşıldı")
        self.requests_used += 1
        return remaining

    def expired(self) -> bool:
        return time.monotonic() >= self._deadline


@dataclass
class FetchResult:
    final_url: str
    status: int
    content_type: str  # yalnız medya türü, küçük harf (parametresiz)
    content_encoding: str  # küçük harf; çağıran fail-closed kararını verir
    text: str


def assert_ip_is_public(ip_str: str) -> None:
    """Yalnızca global olarak yönlendirilebilir (public) IP'lere izin ver.

    `is_global` negatifi private/loopback/link-local/reserved/unspecified VE
    CGNAT (100.64.0.0/10), benchmark (198.18.0.0/15), dokümantasyon
    (192.0.2.0/24) bloklarını tek seferde kapsar. Multicast ayrıca eklenir.
    IPv4-mapped IPv6 (::ffff:10.0.0.1) `ipv4_mapped` üzerinden IPv4 kurallarına
    indirgenir.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        raise UnsafeUrlError("URL çözümlenemedi")
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_multicast or not ip.is_global:
        raise UnsafeUrlError(
            "Güvenlik nedeniyle iç ağ / yerel / özel-kullanım adreslerine erişim engellendi"
        )


def assert_url_host_is_public(url: str) -> None:
    """Şema (http/https) + host'un çözümlendiği TÜM IP'ler public olmalı.

    Kullanıcı bilgisi (user:pass@host) taşıyan URL'ler reddedilir — redirect
    ile credential-leak / parser-confusion yüzeyi kapatılır.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("Yalnızca http/https URL'leri desteklenir")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Kullanıcı bilgisi içeren URL'ler desteklenmez")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL host bilgisi içermiyor")
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise UnsafeUrlError("URL çözümlenemedi")
    for info in infos:
        assert_ip_is_public(info[4][0])


HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


def _media_type(headers: httpx.Headers) -> str:
    # "text/html; charset=utf-8" gibi parametreli formlar da kabul edilir.
    return (headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def _is_tls_failure(exc: BaseException) -> bool:
    while exc is not None:
        if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def safe_fetch(
    url: str,
    *,
    allowed_types: Iterable[str],
    headers: Optional[dict] = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    budget: Optional[FetchBudget] = None,
) -> FetchResult:
    """SSRF-korumalı, boyut-sınırlı genel fetch (HTML / XML / düz metin).

    - Redirect'ler MANUEL takip edilir; HER hedef (şema dahil) yeniden doğrulanır
      (max MAX_REDIRECTS; file:/ftp:/userinfo'lu hedefe geçiş RED; loop koruması).
    - Yanıt STREAMING sırasında max_bytes'ta kesilir (decompress edilmiş bayt
      sayılır → decompression bombası da bu sınıra takılır).
    - Yalnız `allowed_types` (parametresiz medya türleri) kabul edilir; verify=True.
    - `budget` verilirse HER istek (redirect adımı dahil) bütçeden düşer ve istek
      timeout'u kalan süreyle kırpılır.
    - Content-Encoding burada REDDEDİLMEZ (safe_fetch_html sözleşmesi); `result.
      content_encoding` ile çağıran karar verir.
    """
    allowed = {t.lower() for t in allowed_types}
    current = url
    seen: set[str] = set()
    try:
        with httpx.Client(follow_redirects=False, verify=True) as client:
            for _ in range(MAX_REDIRECTS + 1):
                assert_url_host_is_public(current)
                if current in seen:
                    raise UnsafeUrlError("URL yönlendirme döngüsü içeriyor")
                seen.add(current)
                read_t, connect_t = READ_TIMEOUT, CONNECT_TIMEOUT
                if budget is not None:
                    remaining = budget.consume()
                    read_t, connect_t = min(read_t, remaining), min(connect_t, remaining)
                with client.stream(
                    "GET", current, headers=headers,
                    timeout=httpx.Timeout(read_t, connect=connect_t),
                ) as resp:
                    if resp.is_redirect and resp.has_redirect_location:
                        location = resp.headers.get("location", "")
                        current = urljoin(str(resp.url), location)
                        continue
                    if resp.status_code >= 400:
                        raise HttpStatusError(resp.status_code)
                    content_type = _media_type(resp.headers)
                    if content_type not in allowed:
                        raise UnsafeUrlError(
                            "Yalnızca HTML içerik desteklenir"
                            if allowed <= HTML_TYPES
                            else "Desteklenmeyen içerik türü"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        # read timeout parca basinadir; damla damla gonderen sunucu
                        # toplam sureyi ancak bu kontrolle asamaz
                        if budget is not None and budget.expired():
                            raise FetchBudgetExceeded("Tarama bütçesi (süre) okuma sırasında aşıldı")
                        total += len(chunk)
                        if total > max_bytes:
                            break  # sınır aşıldı: eldekiyle yetin, indirmeyi kes
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    encoding = resp.charset_encoding or "utf-8"
                    return FetchResult(
                        final_url=current,
                        status=resp.status_code,
                        content_type=content_type,
                        content_encoding=(resp.headers.get("content-encoding") or "").strip().lower(),
                        text=body.decode(encoding, errors="replace"),
                    )
            raise UnsafeUrlError("URL çok fazla yönlendirme içeriyor")
    except UnsafeUrlError:
        raise
    except httpx.HTTPError as exc:
        if _is_tls_failure(exc):
            raise TlsVerificationError(f"URL erişilemedi: {exc}")
        raise UnsafeUrlError(f"URL erişilemedi: {exc}")


def safe_fetch_html(url: str, *, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    """SSRF-korumalı, boyut-sınırlı HTML fetch (`safe_fetch` üzerine ince sarmalayıcı)."""
    return safe_fetch(url, allowed_types=HTML_TYPES, max_bytes=max_bytes).text
