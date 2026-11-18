"""Tek hedefin (marka ya da rakip sitesi) web denetimi: sayfalari okur, bulgu uretir.

Bulgu sozlesmesi (plan §5): olcum durumu (state) ile degerlendirme (assessment) AYRIDIR.
  state:      observed | not_observed | unknown | blocked | error
  assessment: ok | warning | problem | info | None
Kural: okunamayan (engellenen / JavaScript ile cizilen) sayfadan "not_observed" CIKMAZ.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Dict, List, Optional
from urllib.parse import urlparse

from app.core.digital_audit import site_fetch
from app.core.digital_audit.signals import classify_page, extract_signals
from app.core.digital_audit.site_fetch import BLOCKED, OK, ROBOTS, SiteFetcher, origin_of
from app.core.site_analyzer.crawler import MAX_SITEMAP_URLS, SiteCrawler
from app.core.url_guard import FetchBudget, FetchBudgetExceeded, UnsafeUrlError
from app.integrations.pagespeed import run_pagespeed

METHOD_VERSION = 1
MAX_PAGES = 10
MAX_LINK_CHECKS = 40
BUDGET_REQUESTS = 80
BUDGET_SECONDS = 240.0
FREE_MAIL = ("gmail.", "hotmail.", "outlook.", "yahoo.", "yandex.", "icloud.")

# Sayfa icerigine dayanan kontroller: hedef okunamazsa hepsi ayni durumla doner.
ON_PAGE_CHECKS = [
    ("site", "site.mobile_viewport", "Mobil görünüm ayarı (viewport)"),
    ("site", "site.structure", "Menü ve sayfa yapısı"),
    ("site", "site.cta", "CTA, form, telefon ve WhatsApp butonları"),
    ("site", "site.broken_links", "Kırık bağlantılar"),
    ("seo", "seo.titles", "Sayfa başlıkları (title)"),
    ("seo", "seo.meta_descriptions", "Meta açıklamaları"),
    ("seo", "seo.h1", "Ana başlıklar (H1)"),
    ("seo", "seo.noindex", "Arama motorlarına kapalı sayfa (noindex)"),
    ("seo", "seo.structured_data", "Yapısal veri (schema)"),
    ("seo", "seo.image_alt", "Görsel alt metinleri"),
    ("seo", "seo.blog", "Blog ve güncellik"),
    ("tracking", "tracking.gtm", "Google Tag Manager izi"),
    ("tracking", "tracking.ga4", "GA4 izi"),
    ("tracking", "tracking.google_ads", "Google Ads etiketi izi"),
    ("tracking", "tracking.meta_pixel", "Meta Pixel izi"),
    ("tracking", "tracking.other_pixels", "Diğer reklam etiketleri (TikTok, LinkedIn)"),
    ("tracking", "tracking.cookie_banner", "Çerez bildirimi aracı"),
    ("tracking", "tracking.privacy_page", "KVKK / gizlilik sayfası"),
    ("contact", "contact.phone", "Telefon bağlantısı"),
    ("contact", "contact.whatsapp", "WhatsApp bağlantısı"),
    ("contact", "contact.email", "E-posta adresi"),
    ("contact", "contact.forms", "İletişim ve abonelik formları"),
    ("contact", "contact.social_links", "Sosyal medya hesap bağlantıları"),
    ("ecommerce", "ecommerce.platform", "E-ticaret altyapısı"),
]
_LABELS = {check: label for _, check, label in ON_PAGE_CHECKS}
_SECTIONS = {check: section for section, check, _ in ON_PAGE_CHECKS}
TRACKER_LIMIT = "Etiket yöneticisi, çerez onayı ya da sunucu tarafı üzerinden yüklenen etiketler sayfa kodunda görülmez; bulunamaması olmadığını kanıtlamaz."


def finding(section: str, check: str, label: str, state: str, assessment: Optional[str] = None,
            value=None, note: Optional[str] = None, source: Optional[str] = None,
            limits: Optional[List[str]] = None) -> Dict:
    return {"section": section, "check": check, "label": label, "state": state,
            "assessment": assessment, "value": value, "note": note, "source": source,
            "limits": limits or []}


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


def _network_findings(url: str, host: str) -> List[Dict]:
    out = []
    tls = site_fetch.tls_info(host)
    if tls.get("valid") is True:
        soon = tls["days_left"] < 21
        out.append(finding("site", "site.ssl", "SSL sertifikası", "observed", "warning" if soon else "ok",
                           value=tls, note="Sertifika 21 günden kısa sürede bitiyor." if soon else None, source=url))
    elif tls.get("valid") is False:
        out.append(finding("site", "site.ssl", "SSL sertifikası", "observed", "problem", value=tls,
                           note="Sertifika doğrulanamadı.", source=url))
    else:
        out.append(finding("site", "site.ssl", "SSL sertifikası", "unknown",
                           note="Sertifika bilgisi alınamadı (bağlantı kurulamadı)."))
    dom = site_fetch.domain_info(host)
    limits = ["Alan adı ve hosting hesabının kimde olduğu kayıtlardan anlaşılmaz; müşteriye sorulmalı."]
    if dom.get("available") and dom.get("expiration"):
        days = (date.fromisoformat(dom["expiration"]) - date.today()).days
        out.append(finding("site", "site.domain", "Alan adı kaydı", "observed",
                           "warning" if days < 60 else "info", value=dom,
                           note="Alan adı 60 günden kısa sürede bitiyor." if days < 60 else None, limits=limits))
    else:
        out.append(finding("site", "site.domain", "Alan adı kaydı", "unknown",
                           note="Alan adı kaydı okunamadı (uzantı bu bilgiyi sunmuyor olabilir).", limits=limits))
    return out


def _speed_findings(url: str, own: bool) -> List[Dict]:
    out = []
    for strategy, label in (("mobile", "Açılış hızı (mobil)"), ("desktop", "Açılış hızı (masaüstü)")):
        if strategy == "desktop" and not own:
            continue  # rakiplerde yalniz mobil (plan: baslangic sinirlari)
        check = f"site.speed_{strategy}"
        result = run_pagespeed(url, strategy)
        if not result.get("available"):
            reason = result.get("reason")
            note = ("Hız ölçümü yapılandırılmamış (API anahtarı yok)." if reason == "not_configured"
                    else f"Hız ölçümü alınamadı ({reason}).")
            out.append(finding("site", check, label, "unknown", note=note))
            continue
        score = result["scores"].get("performance")
        assessment = "ok" if score >= 90 else "warning" if score >= 50 else "problem"
        limits = ["Laboratuvar ölçümüdür; ölçümden ölçüme birkaç puan değişir ve gerçek kullanıcı deneyimi farklı olabilir."]
        if not result["field"]:
            limits.append("Bu site için gerçek kullanıcı hız verisi yok (az ziyaretli sitelerde olağandır).")
        out.append(finding("site", check, label, "observed", assessment, value=result, source=url, limits=limits))
    return out


def _unreadable(state: str, note: str) -> List[Dict]:
    return [finding(section, check, label, state, note=note) for section, check, label in ON_PAGE_CHECKS]


def _select_pages(home: Dict, limit: int) -> List[Dict]:
    """Ana sayfanin baglantilarindan denetlenecek sayfalar (sitemap'ten DEGIL)."""
    by_url: Dict[str, Dict] = {}
    home_key = home["url"].rstrip("/")
    for link in home["links"]:
        if not link["internal"] or link["url"].rstrip("/") == home_key:
            continue
        if urlparse(link["url"]).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf", ".zip", ".svg", ".gif", ".mp4")):
            continue
        entry = by_url.setdefault(link["url"], {"url": link["url"], "anchors": [], "nav": False})
        if link["text"]:
            entry["anchors"].append(link["text"])
        entry["nav"] = entry["nav"] or link["nav"]
    for entry in by_url.values():
        entry["pre"] = classify_page(entry["url"], entry["anchors"], entry["nav"])
    seen_types, typed, rest = set(), [], []
    for entry in by_url.values():
        kind = entry["pre"]["type"]
        if kind and kind not in seen_types:
            seen_types.add(kind)
            typed.append(entry)
        else:
            rest.append(entry)
    rest.sort(key=lambda e: (not e["nav"], -e["pre"]["score"]))
    return (typed + rest)[:limit]


def _on_page_findings(pages: List[Dict], links_result: Dict, robots_present, sitemap_count,
                      host: str, truncated: bool) -> List[Dict]:
    """pages: [{'signals':..., 'class':...}], ilk eleman ana sayfa."""
    sig = [p["signals"] for p in pages]
    home = sig[0]
    n = len(sig)
    thin = home["js_thin"]
    # Okunabilir sayfada bulunmayan = "gozlenmedi"; JavaScript ile cizilen sayfada = "bilinmiyor".
    absent = "unknown" if thin else "not_observed"
    scope = [f"{n} sayfa incelendi."]
    if thin:
        scope.append("Ana sayfanın içeriği JavaScript ile çiziliyor; sayfa kodundan okunamayan öğeler 'bilinmiyor' sayıldı.")
    if truncated:
        scope.append("Tarama bütçesi doldu; daha az sayfa incelendi.")
    f: Dict[str, Dict] = {}

    def put(check, state, assessment=None, value=None, note=None, limits=None):
        # "bilinmiyor" bulguda deger tasinmaz: okunamayan sayfanin bos listesi "yok" gibi okunur.
        f[check] = finding(_SECTIONS[check], check, _LABELS[check], state, assessment,
                           value if state in ("observed", "not_observed") else None, note,
                           source=home["url"], limits=(limits or []) + scope)

    def union(key):
        out = []
        for s in sig:
            out += [v for v in s[key] if v not in out]
        return out

    # --- site
    no_viewport = [s["url"] for s in sig if not s["viewport"]]
    put("site.mobile_viewport", "observed" if not no_viewport else absent,
        "ok" if not no_viewport else (None if thin else "warning"),
        value={"pages_without_viewport": no_viewport[:5], "count": len(no_viewport)},
        limits=["Yalnızca sayfa kodundaki ayara bakar; menü ve formların telefonda kullanılabildiğini kanıtlamaz."])
    types = {}
    for p in pages[1:]:
        if p["class"]["type"]:
            types.setdefault(p["class"]["type"], {"url": p["signals"]["url"], "evidence": p["class"]["evidence"]})
    nav_items = [l["text"] for l in home["links"] if l["nav"] and l["text"]]
    missing = [t for t in ("iletisim", "hakkimizda") if t not in types]
    if nav_items or types:
        put("site.structure", "observed", "warning" if missing else "ok",
            value={"page_types": types, "nav_items": list(dict.fromkeys(nav_items))[:25], "missing": missing},
            note=("İncelenen sayfalarda bulunamayan temel sayfa: "
                  + ", ".join({"iletisim": "İletişim", "hakkimizda": "Hakkımızda"}[m] for m in missing)) if missing else None,
            limits=["Sayfa türü menü bağlantısı, bağlantı metni ve sayfa içeriğiyle belirlenir; incelenen sayfalarla sınırlıdır."])
    else:
        put("site.structure", absent, None if thin else "warning", note="Menü ya da iç bağlantı okunamadı.")
    tel, wa = union("tel"), union("whatsapp")
    # Sayfa basina en fazla: alt bilgideki ayni form her sayfada yeniden sayilmaz.
    contact_forms = max(sum(1 for fm in s["forms"] if not fm["is_search"] and not fm["is_newsletter"]) for s in sig)
    has_cta = bool(tel or wa or contact_forms)
    put("site.cta", "observed" if has_cta else absent, "ok" if has_cta else (None if thin else "warning"),
        value={"phone": bool(tel), "whatsapp": bool(wa), "forms": contact_forms},
        limits=["Butonun varlığı görülür; formun gerçekten iletildiği test edilmez."])
    if links_result["checked"]:
        broken = links_result["broken"]
        put("site.broken_links", "observed", "problem" if broken else "ok", value=links_result,
            limits=[f"{links_result['checked']} site içi bağlantı denetlendi; engel ya da bağlantı hatası veren adresler kırık sayılmadı.",
                    "Başka sitelere giden bağlantılar ve robots.txt ile yasaklanan adresler denetlenmez."])
    else:
        put("site.broken_links", "unknown", note="Denetlenecek bağlantı okunamadı.")

    # --- seo
    titles = [s["title"] for s in sig]
    dup_titles = sum(c - 1 for c in Counter(t for t in titles if t).values() if c > 1)
    t_val = {"missing": sum(1 for t in titles if not t), "duplicate": dup_titles,
             "too_long": sum(1 for t in titles if len(t) > 60), "home_title": home["title"]}
    put("seo.titles", "observed" if home["title"] else absent,
        "warning" if (t_val["missing"] or dup_titles or t_val["too_long"]) and not thin else ("ok" if home["title"] else None),
        value=t_val)
    descs = [s["meta_description"] for s in sig]
    d_val = {"missing": sum(1 for d in descs if not d),
             "duplicate": sum(c - 1 for c in Counter(d for d in descs if d).values() if c > 1)}
    put("seo.meta_descriptions", "observed" if any(descs) else absent,
        ("warning" if d_val["missing"] or d_val["duplicate"] else "ok") if (any(descs) or not thin) else None,
        value=d_val)
    h_val = {"missing": sum(1 for s in sig if not s["h1"]), "multiple": sum(1 for s in sig if len(s["h1"]) > 1)}
    put("seo.h1", "observed" if any(s["h1"] for s in sig) else absent,
        ("warning" if h_val["missing"] or h_val["multiple"] else "ok") if (any(s["h1"] for s in sig) or not thin) else None,
        value=h_val)
    noindex = [s["url"] for s in sig if s["noindex"]]
    put("seo.noindex", "observed", "problem" if home["noindex"] else ("warning" if noindex else "ok"),
        value={"noindex_pages": noindex[:5], "count": len(noindex)})
    ld = union("ld_types")
    put("seo.structured_data", "observed" if ld else absent, "info" if ld else None, value={"types": ld[:15]})
    img_total = sum(s["img_total"] for s in sig)
    img_missing = sum(s["img_no_alt"] for s in sig)
    if img_total:
        put("seo.image_alt", "observed", "warning" if img_missing / img_total > 0.3 else "ok",
            value={"total": img_total, "missing_alt": img_missing})
    else:
        put("seo.image_alt", absent, note="Sayfa kodunda görsel bulunamadı.")
    blog = next((p for p in pages[1:] if p["class"]["type"] == "blog"), None)
    if blog:
        dates = blog["signals"]["dates"]
        put("seo.blog", "observed", "info",
            value={"url": blog["signals"]["url"], "newest_date": dates[-1] if dates else None, "dated_items": len(dates)},
            note=("Blog bölümü var; yazı tarihleri sayfa kodunda okunamadı, güncellenme sıklığı bilinmiyor." if not dates
                  else "Yalnızca birkaç tarih okunabildi; en yeni yazı tarihi güvenilir olmayabilir." if len(dates) < 3 else None),
            limits=["Güncellik yalnızca blog sayfasında yayımlanan tarihlerden okunur; sitemap tarihleri kullanılmaz."])
    else:
        put("seo.blog", absent, "info" if not thin else None, note="İncelenen sayfalarda blog bölümü görülmedi.")

    # --- tracking
    ids = {k: sorted({v for s in sig for v in s["tracker_ids"][k]}) for k in ("ga4", "gtm", "google_ads")}
    flags = {k: any(s["tracker_flags"][k] for s in sig) for k in ("meta_pixel", "tiktok_pixel", "linkedin_insight")}
    for key in ("gtm", "ga4", "google_ads"):
        put(f"tracking.{key}", "observed" if ids[key] else "not_observed" if not thin else "unknown",
            "info" if ids[key] else None, value={"ids": ids[key]},
            note=None if ids[key] else "Sayfa kodunda iz bulunamadı.",
            limits=[TRACKER_LIMIT, "İz bulunması, verinin düzgün toplandığını kanıtlamaz."])
    put("tracking.meta_pixel", "observed" if flags["meta_pixel"] else "not_observed" if not thin else "unknown",
        "info" if flags["meta_pixel"] else None,
        note=None if flags["meta_pixel"] else "Sayfa kodunda iz bulunamadı.", limits=[TRACKER_LIMIT])
    others = [k for k in ("tiktok_pixel", "linkedin_insight") if flags[k]]
    put("tracking.other_pixels", "observed" if others else "not_observed" if not thin else "unknown",
        "info" if others else None, value={"found": others}, limits=[TRACKER_LIMIT])
    cmp = union("cmp")
    put("tracking.cookie_banner", "observed" if cmp else absent, "ok" if cmp else None, value={"tools": cmp},
        note=None if cmp else "Bilinen bir çerez bildirimi aracı bulunamadı.",
        limits=["Yalnızca bilinen araçlar aranır; siteye özel yazılmış bildirim görülmeyebilir. Hukuki uygunluk değerlendirilmez."])
    if "kvkk" in types:
        put("tracking.privacy_page", "observed", "ok", value=types["kvkk"],
            limits=["Sayfanın varlığına bakar; metnin hukuki yeterliliği değerlendirilmez."])
    else:
        put("tracking.privacy_page", absent, None if thin else "warning",
            note="İncelenen sayfalarda KVKK / gizlilik sayfası bulunamadı.")

    # --- contact
    put("contact.phone", "observed" if tel else absent, "ok" if tel else None, value={"links": tel})
    put("contact.whatsapp", "observed" if wa else absent, "ok" if wa else None, value={"links": wa},
        limits=["Bağlantıdan hattın WhatsApp Business olup olmadığı anlaşılmaz."])
    emails = union("emails")
    site = host.lower().removeprefix("www.")
    on_domain = [e for e in emails if e.split("@")[-1].endswith(site)]
    free = [e for e in emails if e.split("@")[-1].startswith(FREE_MAIL)]
    put("contact.email", "observed" if emails else absent,
        ("ok" if on_domain else "warning" if free else "info") if emails else None,
        value={"emails": emails[:5], "on_own_domain": bool(on_domain)},
        note="Kurumsal alan adı yerine ücretsiz e-posta servisi kullanılıyor." if free and not on_domain else None)
    newsletter = any(fm["is_newsletter"] for s in sig for fm in s["forms"])
    put("contact.forms", "observed" if (contact_forms or newsletter) else absent, "info" if (contact_forms or newsletter) else None,
        value={"contact_forms": contact_forms, "newsletter_form": newsletter},
        limits=["Formlar gönderilmez; çalışıp çalışmadığı ve listenin kullanımı bilinmez."])
    social = {}
    for s in sig:
        for name, urls in s["social"].items():
            social.setdefault(name, urls[0])
    put("contact.social_links", "observed" if social else absent, "info" if social else None, value={"accounts": social},
        limits=["Yalnızca siteden bağlantı verilen hesaplar bulunur; takipçi ve etkileşim bu sürümde ölçülmez."])

    # --- ecommerce
    platforms = union("platforms")
    has_product = "Product" in ld
    cart = any(s["cart_link"] for s in sig)
    if platforms or has_product or cart:
        put("ecommerce.platform", "observed", "info",
            value={"platforms": platforms, "product_schema": has_product, "cart_link": cart},
            limits=["Altyapı sayfa kodundaki izlerden tahmin edilir. Ödeme akışı test edilmez."])
    else:
        put("ecommerce.platform", absent, "info" if not thin else None, note="E-ticaret izi görülmedi.")

    out = [f[check] for _, check, _ in ON_PAGE_CHECKS]
    # robots.txt ve sitemap sayfa iceriginden bagimsiz: ayri eklenir
    out.append(finding("seo", "seo.robots_txt", "robots.txt",
                       "observed" if robots_present else "unknown" if robots_present is None else "not_observed",
                       "ok" if robots_present else None if robots_present is None else "warning"))
    # 0 adres: dosya yok DA olabilir, okunamamis da (baska alan adina yonlenen harita, engel). "Yok" denmez.
    out.append(finding("seo", "seo.sitemap", "Sitemap", "observed" if sitemap_count else "unknown",
                       "ok" if sitemap_count else None,
                       value={"urls": sitemap_count, "capped": sitemap_count >= MAX_SITEMAP_URLS} if sitemap_count else None,
                       note=None if sitemap_count else "Sitemap bulunamadı ya da okunamadı (robots.txt'deki adresler ve /sitemap.xml denendi).",
                       limits=["En fazla 3 alt harita ve 200 adres okunur; sayı alt sınırdır.",
                               "Dosyanın varlığı, sayfaların doğru indekslendiğini göstermez."]))
    return out


def run_target(url: str, own: bool) -> Dict:
    """Bir hedefi denetler. Donus: {status, pages_checked, findings, error_message}.

    status: completed | blocked | failed. Istisna FIRLATMAZ (beklenen ag hatalari bulguya donusur).
    """
    url = normalize_url(url)
    host = urlparse(url).hostname or ""
    origin = origin_of(url)
    budget = FetchBudget(max_requests=BUDGET_REQUESTS, total_seconds=BUDGET_SECONDS)
    try:
        fetcher = SiteFetcher(origin, budget)
        home = fetcher.get_html(url)
    except FetchBudgetExceeded:
        home = site_fetch.PageResponse(site_fetch.ERROR, url, reason="budget")
    except UnsafeUrlError as exc:
        return {"status": "failed", "pages_checked": 0, "error_message": str(exc)[:300],
                "findings": [finding("site", "site.reachable", "Site açılıyor mu", "error", "problem", note=str(exc)[:200])]}

    findings: List[Dict] = []
    if home.status != OK:
        if home.status in (BLOCKED, ROBOTS):
            note = ("Site, otomatik erişimi engelledi (doğrulama ya da engel sayfası döndü). Sayfa içeriği okunamadı."
                    if home.status == BLOCKED else "Sitenin robots.txt kuralları ana sayfanın taranmasına izin vermiyor.")
            findings.append(finding("site", "site.reachable", "Site açılıyor mu", "blocked",
                                    value={"http_status": home.http_status, "reason": home.reason}, note=note,
                                    limits=["Engel, sitenin ziyaretçilere kapalı olduğu anlamına gelmez."]))
            findings += _network_findings(url, host)
            findings += _unreadable("blocked", "Sayfa içeriği okunamadığı için değerlendirilemedi; bu, öğenin sitede olmadığı anlamına gelmez.")
            return {"status": "blocked", "pages_checked": 0, "findings": findings, "error_message": None}
        findings.append(finding("site", "site.reachable", "Site açılıyor mu", "error", "problem",
                                value={"http_status": home.http_status, "reason": home.reason},
                                note="Site yanıt vermedi ya da hata döndü.",
                                limits=["Tek denemenin sonucudur; geçici bir sorun olabilir."]))
        findings += _network_findings(url, host)
        findings += _unreadable("unknown", "Site açılamadığı için değerlendirilemedi.")
        return {"status": "failed", "pages_checked": 0, "findings": findings, "error_message": home.reason}

    https = home.url.startswith("https://")
    findings.append(finding("site", "site.reachable", "Site açılıyor mu", "observed", "ok" if https else "problem",
                            value={"final_url": home.url, "https": https},
                            note=None if https else "Site HTTPS üzerinden açılmıyor.", source=home.url))
    findings += _network_findings(url, host)

    home_signals = extract_signals(home.html, home.url)
    pages = [{"signals": home_signals, "class": {"type": "ana_sayfa", "evidence": []}}]
    truncated = False
    links_result = {"checked": 0, "broken": [], "blocked": 0, "unknown": 0, "skipped": 0}
    sitemap_count: Optional[int] = None
    try:
        for entry in _select_pages(home_signals, MAX_PAGES - 1):
            response = fetcher.get_html(entry["url"])
            if response.status != OK:
                continue  # okunamayan alt sayfa: bulguya katilmaz (yok sayilmaz, yalniz kapsam daralir)
            signals = extract_signals(response.html, response.url)
            pages.append({"signals": signals,
                          "class": classify_page(entry["url"], entry["anchors"], entry["nav"], signals)})
        fetched = {p["signals"]["url"].rstrip("/") for p in pages}
        to_check = []
        for p in pages:
            for link in p["signals"]["links"]:
                # Yalniz site ici: baska sitelerin robots kurallarini bilmeden istek atilmaz.
                if link["internal"] and link["url"].rstrip("/") not in fetched and link["url"] not in to_check:
                    to_check.append(link["url"])
        for link_url in to_check[:MAX_LINK_CHECKS]:
            result = fetcher.check_link(link_url)
            if result["state"] == "skipped":  # robots.txt yasakliyor: istek atilmadi
                links_result["skipped"] += 1
                continue
            links_result["checked"] += 1
            if result["state"] == "broken":
                links_result["broken"].append({"url": link_url, "status": result.get("status")})
            elif result["state"] in ("blocked", "unknown"):
                links_result[result["state"]] += 1
        # Mevcut tarayicinin sitemap kesfi yeniden kullanilir (robots Sitemap: satirlari + /sitemap.xml)
        sitemap_count = len(SiteCrawler()._try_sitemap(origin, budget))
    except FetchBudgetExceeded:
        truncated = True

    findings += _on_page_findings(pages, links_result, fetcher.robots_present, sitemap_count, host, truncated)
    findings += _speed_findings(home.url, own)
    return {"status": "completed", "pages_checked": len(pages), "findings": findings, "error_message": None}
