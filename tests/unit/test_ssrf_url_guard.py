"""
SSRF URL guard unit testleri — google_ads._assert_url_reachable katmani.

Ağ erişimi gerektirmez: yalnizca sema + IP/host dogrulama katmani test edilir.
Literal IP'li URL'ler kullanilir, boylece getaddrinfo harici DNS'e cikmaz.
"""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from fastapi import HTTPException

from app.api.v1.google_ads import (
    _assert_ip_is_public,
    _assert_url_host_is_public,
)


# ── _assert_ip_is_public ────────────────────────────────────────────

@pytest.mark.parametrize("ip", [
    "127.0.0.1",          # loopback
    "10.0.0.1",           # private A
    "172.16.5.4",         # private B
    "192.168.1.1",        # private C
    "169.254.169.254",    # link-local (cloud metadata)
    "0.0.0.0",            # unspecified
    "::1",                # IPv6 loopback
    "fe80::1",            # IPv6 link-local
    "fc00::1",            # IPv6 unique-local (private)
    "224.0.0.1",          # multicast
    "100.64.0.1",         # CGNAT (RFC 6598) — manuel listede kacardi
    "198.18.0.1",         # benchmark/special-use (RFC 2544)
    "192.0.2.1",          # TEST-NET-1 dokumantasyon (RFC 5737)
    "240.0.0.1",          # reserved (class E)
])
def test_blocks_internal_ips(ip):
    with pytest.raises(HTTPException) as exc:
        _assert_ip_is_public(ip)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("ip", [
    "8.8.8.8",            # Google DNS
    "1.1.1.1",            # Cloudflare
    "93.184.216.34",      # example.com
    "2606:2800:220:1:248:1893:25c8:1946",  # public IPv6
])
def test_allows_public_ips(ip):
    # Exception firlatmamali
    _assert_ip_is_public(ip)


def test_invalid_ip_rejected():
    with pytest.raises(HTTPException) as exc:
        _assert_ip_is_public("not-an-ip")
    assert exc.value.status_code == 422


# ── _assert_url_host_is_public ──────────────────────────────────────

@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_INFO",
    "data:text/plain;base64,AAAA",
])
def test_rejects_non_http_schemes(url):
    with pytest.raises(HTTPException) as exc:
        _assert_url_host_is_public(url)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5:8000/internal",
    "https://192.168.0.10/",
    "http://[::1]:6379/",
])
def test_rejects_internal_hosts(url):
    with pytest.raises(HTTPException) as exc:
        _assert_url_host_is_public(url)
    assert exc.value.status_code == 422


def test_rejects_missing_host():
    with pytest.raises(HTTPException) as exc:
        _assert_url_host_is_public("http:///path-only")
    assert exc.value.status_code == 422


def test_allows_public_literal_ip_host():
    # Literal public IP — getaddrinfo DNS'e cikmaz, public oldugu icin gecmeli
    _assert_url_host_is_public("http://8.8.8.8/")
    _assert_url_host_is_public("https://1.1.1.1/path?q=1")
