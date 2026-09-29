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
from urllib.parse import urljoin, urlparse

import httpx

MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 1_000_000  # 1MB — streaming sırasında uygulanır
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 8.0


class UnsafeUrlError(Exception):
    """URL güvenlik/doğrulama ihlali (SSRF, şema, çözümlenemeyen host...)."""


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


def _content_type_is_html(headers: httpx.Headers) -> bool:
    # "text/html; charset=utf-8" gibi parametreli formlar da kabul edilir.
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return content_type in ("text/html", "application/xhtml+xml")


def safe_fetch_html(url: str, *, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    """SSRF-korumalı, boyut-sınırlı HTML fetch.

    - Redirect'ler MANUEL takip edilir; HER hedef (şema dahil) yeniden doğrulanır
      (max MAX_REDIRECTS; file:/ftp:/userinfo'lu hedefe geçiş RED; loop koruması).
    - Yanıt STREAMING sırasında max_bytes'ta kesilir (decompress edilmiş bayt
      sayılır → decompression bombası da bu sınıra takılır).
    - Yalnız text/html kabul edilir; verify=True (TLS doğrulaması AÇIK).
    """
    current = url
    seen: set[str] = set()
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, verify=True) as client:
            for _ in range(MAX_REDIRECTS + 1):
                assert_url_host_is_public(current)
                if current in seen:
                    raise UnsafeUrlError("URL yönlendirme döngüsü içeriyor")
                seen.add(current)
                with client.stream("GET", current) as resp:
                    if resp.is_redirect and resp.has_redirect_location:
                        location = resp.headers.get("location", "")
                        current = urljoin(str(resp.url), location)
                        continue
                    if resp.status_code >= 400:
                        raise UnsafeUrlError(
                            f"URL erişilemedi veya hata döndü: HTTP {resp.status_code}"
                        )
                    if not _content_type_is_html(resp.headers):
                        raise UnsafeUrlError("Yalnızca HTML içerik desteklenir")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            break  # sınır aşıldı: eldekiyle yetin, indirmeyi kes
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    encoding = resp.charset_encoding or "utf-8"
                    return body.decode(encoding, errors="replace")
            raise UnsafeUrlError("URL çok fazla yönlendirme içeriyor")
    except UnsafeUrlError:
        raise
    except httpx.HTTPError as exc:
        raise UnsafeUrlError(f"URL erişilemedi: {exc}")
