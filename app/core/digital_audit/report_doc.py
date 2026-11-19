"""Bagimsiz denetim + arastirma raporu (DOCX / PDF). ScoringRun gerektirmez.

Rapor once bicimden bagimsiz bloklara cevrilir, sonra iki kucuk cizici ayni bloklari yazar.
Etiketler dosyada korunur: [Gözlem] otomatik olcum, [Beyan] musteri cevabi,
[Kaynak] kaynakta dogrulanan bilgi, [Hipotez] yorum. Dogrulanamayan iddia rapora GIRMEZ.
"""
from __future__ import annotations

import io
import re
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from app.core.digital_audit import research
from app.core.digital_audit.service import QUESTIONS

Block = Tuple  # ("h1"|"h2"|"p", text) | ("bullets", [text]) | ("table", [header], [[cell]])

_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SECTION = {"site": "Web sitesi", "seo": "SEO", "tracking": "Ölçümleme", "contact": "İletişim kanalları",
            "ecommerce": "E-ticaret", "speed": "Hız"}
LEGEND = ("[Gözlem] otomatik ölçüm · [Beyan] müşteri cevabı, doğrulanmadı · "
          "[Doğrulandı] analistin kaynak göstererek doğruladığı hesap verisi · [Kaynak] kaynakta doğrulanan bilgi · "
          "[Hipotez] bulgulardan türetilen, doğrulanması gereken yorum")
_EVIDENCE = (("source", "kaynak"), ("checked_at", "kontrol tarihi"), ("verified_by", "doğrulayan"),
             ("period", "dönem"), ("account", "hesap"))
STALE_NOTE = ("Rapora alınan rakamlar ya da brif/beyan bilgileri bu bölüm üretildikten sonra değişti "
              "ya da bölümün hangi bilgilerle üretildiği doğrulanamadı; bölüm güncel olmadığı için rapora alınmadı. "
              "Yeniden üretilmesi gerekir.")


_SECTION_ORDER = ("site", "seo", "tracking", "contact", "ecommerce")
_TARGET_STATUS = {"pending": "bekliyor", "completed": "denetlendi", "blocked": "site erişimi engelledi",
                  "failed": "denetlenemedi"}
_PAGE_TYPE = {"iletisim": "İletişim", "hakkimizda": "Hakkımızda", "hizmet": "Hizmetler", "blog": "Blog",
              "kvkk": "KVKK", "randevu": "Randevu", "fiyat": "Fiyat", "sss": "SSS"}
_SOCIAL = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube", "tiktok": "TikTok",
           "linkedin": "LinkedIn", "x": "X"}
# Siteye ozel kapsam notlari: bolum sinirlarinda tekrarlanmaz, site satirinda bir kez yazilir.
_PER_SITE_LIMIT = re.compile(r"^(\d+ sayfa incelendi\.|Ana sayfanın içeriği JavaScript|Tarama bütçesi doldu"
                             r"|Bu site için gerçek kullanıcı hız verisi yok)")  # sonuncusu hiz tablosunda site site yazar
# Siteye gore degisen sayi hucrede zaten var; sinir cumlesi bolumde bir kez yazilsin.
_LINK_COUNT = re.compile(r"^\d+ site içi bağlantı denetlendi; e")
# Okunamayan hedefte her kontrole kopyalanan genel not: hucrede tekrarlanmaz.
_GENERIC_NOTE = ("Sayfa içeriği okunamadığı", "Site açılamadığı")
_SCORE_ROWS = (("performance", "Performans (mobil)"), ("seo", "SEO teknik kontrolü (mobil)"),
               ("accessibility", "Erişilebilirlik (mobil)"), ("best-practices", "En iyi uygulamalar (mobil)"))
_LAB_ROWS = (("largest-contentful-paint", "En büyük öğenin yüklenmesi, LCP (mobil)"),
             ("cumulative-layout-shift", "Düzen kayması, CLS (mobil)"),
             ("total-blocking-time", "Engelleme süresi, TBT (mobil)"))
_FIELD = {"LARGEST_CONTENTFUL_PAINT_MS": "LCP", "CUMULATIVE_LAYOUT_SHIFT_SCORE": "CLS",
          "INTERACTION_TO_NEXT_PAINT": "INP", "FIRST_CONTENTFUL_PAINT_MS": "FCP"}
_FIELD_CATEGORY = {"FAST": "iyi", "AVERAGE": "orta", "SLOW": "zayıf"}
_OUT_OF_SCOPE = "Kapsam dışı (rakipte ölçülmez)"


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").strip("/")


def _status(f: Optional[Dict]) -> str:
    """Ekrandaki etiketle ayni sozluk: olcum durumu ile degerlendirme karismaz."""
    if not f:
        return "Ölçülmedi"
    if f["state"] != "observed":
        return {"blocked": "Engellendi", "unknown": "Bilinmiyor", "error": "Hata"}.get(f["state"], "Görülmedi")
    return {"ok": "İyi", "warning": "Uyarı", "problem": "Sorun"}.get(f.get("assessment") or "", "Görüldü")


def _counts(parts, pages: int) -> str:
    shown = [f"{n} {label}" for label, n in parts if n]
    return f"{', '.join(shown) or 'eksik yok'} (incelenen {pages} sayfada)"


def _page_count(v: Dict, key: str) -> str:
    """Saklanan liste en fazla 5 ornek adrestir; toplam ayri alandadir. Eski kayitta toplam yoksa tavan 'en az' diye yazilir."""
    sample = len(v.get(key) or [])
    if v.get("count") is not None:
        return str(v["count"])
    return f"en az {sample}" if sample >= 5 else str(sample)


def _summary(f: Dict, pages: int) -> str:
    """Bulgunun saklanan degerinden kisa ozet. Toplanmayan bilgi URETILMEZ: deger yoksa bos doner."""
    v = f.get("value")
    if not v:
        return ""
    check = f["check"]
    if check == "site.reachable":
        return v.get("final_url") or (f"HTTP {v['http_status']}" if v.get("http_status") else "")
    if check == "site.ssl":
        return f"bitiş {v['expires']} ({v['days_left']} gün)" if v.get("expires") else ""
    if check == "site.domain":
        return " · ".join(filter(None, [v.get("expiration") and f"bitiş {v['expiration']}",
                                        v.get("registration") and f"kayıt {v['registration']}", v.get("registrar")]))
    if check in ("site.speed_mobile", "site.speed_desktop"):
        score = (v.get("scores") or {}).get("performance")
        return f"performans {score}/100" if score is not None else ""
    if check == "site.mobile_viewport":
        return f"{_page_count(v, 'pages_without_viewport')} sayfada ayar yok" if v.get("pages_without_viewport") else ""
    if check == "site.structure":
        return ", ".join(_PAGE_TYPE.get(t, t) for t in v.get("page_types") or {})
    if check == "site.cta":
        return ", ".join(filter(None, [v.get("phone") and "telefon", v.get("whatsapp") and "WhatsApp",
                                       v.get("forms") and "form"]))
    if check == "site.broken_links":
        return f"{len(v.get('broken') or [])} kırık / {v.get('checked')} bağlantı"
    if check == "seo.titles":
        return _counts([("eksik", v.get("missing")), ("tekrar", v.get("duplicate")), ("uzun", v.get("too_long"))], pages)
    if check == "seo.meta_descriptions":
        return _counts([("eksik", v.get("missing")), ("tekrar", v.get("duplicate"))], pages)
    if check == "seo.h1":
        return _counts([("eksik", v.get("missing")), ("birden fazla", v.get("multiple"))], pages)
    if check == "seo.noindex":
        if v.get("noindex_pages"):
            return f"{_page_count(v, 'noindex_pages')} sayfa aramaya kapalı"
        return f"incelenen {pages} sayfada kapalı sayfa yok"
    if check == "seo.structured_data":
        return ", ".join((v.get("types") or [])[:4])
    if check == "seo.image_alt":
        return f"{v.get('missing_alt')} / {v.get('total')} görselde alt metni eksik"
    if check == "seo.blog":
        # dated_items farkli tarih sayisidir, yazi adedi degil (ayni gun 8 yazi = 1)
        return (f"en yeni yazı {v['newest_date']}, sayfada okunan farklı tarih sayısı {v.get('dated_items')}"
                if v.get("newest_date") else "")
    if check == "seo.sitemap":
        return f"{v['urls']}{'+' if v.get('capped') else ''} adres" if v.get("urls") else ""
    if check in ("tracking.gtm", "tracking.ga4", "tracking.google_ads"):
        return ", ".join(v.get("ids") or [])
    if check == "tracking.other_pixels":
        return ", ".join(v.get("found") or [])
    if check == "tracking.cookie_banner":
        return ", ".join(v.get("tools") or [])
    if check == "tracking.privacy_page":
        return _host(v.get("url") or "")
    if check == "contact.phone":
        return ", ".join(link.replace("tel:", "") for link in v.get("links") or [])
    if check == "contact.email":
        return ", ".join((v.get("emails") or [])[:2])
    if check == "contact.forms":
        return ", ".join(filter(None, [v.get("contact_forms") and f"{v['contact_forms']} iletişim formu",
                                       v.get("newsletter_form") and "abonelik formu"]))
    if check == "contact.social_links":
        return ", ".join(_SOCIAL.get(name, name) for name in v.get("accounts") or {})
    if check == "ecommerce.platform":
        return ", ".join(v.get("platforms") or [])
    return ""


def _cell(f: Optional[Dict], check: str, target) -> str:
    if not f:
        # Rakiplerde masaustu hiz bilerek olculmez: "olculmedi" degil kapsam disi.
        return _OUT_OF_SCOPE if check == "site.speed_desktop" and target.kind != "own" else "Ölçülmedi"
    note = f.get("note") or ""
    # "Görülmedi · ... görülmedi." tekrari yazilmaz
    if note.startswith(_GENERIC_NOTE) or (f["state"] == "not_observed" and note.endswith("görülmedi.")):
        note = ""
    return " · ".join(filter(None, [_status(f), _summary(f, target.pages_checked or 0), note]))


def _speed_blocks(targets, found) -> List[Block]:
    """Lighthouse skorlari ve laboratuvar olcumleri. Saha verisinin yalniz KATEGORISI saklidir."""
    mobile = [by_check.get("site.speed_mobile") for by_check in found]
    if not any(f and f["state"] == "observed" for f in mobile):
        return []

    def pick(f, getter) -> str:
        if not f:
            return "Ölçülmedi"
        if f["state"] != "observed":
            return _status(f)
        value = getter(f.get("value") or {})
        return "Ölçülmedi" if value in (None, "") else str(value)

    def score(key):
        return lambda v: None if (v.get("scores") or {}).get(key) is None else f"{v['scores'][key]}/100"

    def field(v):
        seen = [f"{_FIELD[m]} {_FIELD_CATEGORY.get(c, c)}" for m, c in (v.get("field") or {}).items() if m in _FIELD]
        return ", ".join(seen) or "Bu site için veri yok"

    rows = [[label] + [pick(f, score(key)) for f in mobile] for key, label in _SCORE_ROWS]
    rows += [[label] + [pick(f, lambda v, k=key: (v.get("lab") or {}).get(k)) for f in mobile] for key, label in _LAB_ROWS]
    rows.append(["Gerçek kullanıcı verisi (mobil, kategori)"] + [pick(f, field) for f in mobile])
    desktop = [by_check.get("site.speed_desktop") for by_check in found]
    rows.append(["Performans (masaüstü)"] + [
        _OUT_OF_SCOPE if not f and t.kind != "own" else pick(f, score("performance")) for f, t in zip(desktop, targets)])
    return [("h2", "Hız ve Lighthouse skorları"),
            ("table", ["Ölçüm"] + [_host(t.url) for t in targets], rows),
            ("p", "Skorlar ana sayfanın Lighthouse laboratuvar ölçümüdür (0-100) ve ölçümden ölçüme birkaç puan değişir. "
                  "SEO skoru sayfa kodunun teknik kontrolüdür; arama sıralamasını göstermez. Gerçek kullanıcı verisinin "
                  "yalnızca kategorisi gösterilir; sayısal değerler bu denetimde toplanmadı.")]


def _audit_blocks(audit) -> List[Block]:
    from app.core.digital_audit import runner

    blocks: List[Block] = [("h1", "Site denetimi [Gözlem]")]
    targets = list(audit.targets)
    found = [{f["check"]: f for f in t.findings or []} for t in targets]
    measured = audit.completed_at or audit.created_at
    blocks.append(("p", f"Denetim tarihi: {measured.strftime('%d.%m.%Y') if measured else '-'}. Site başına en fazla "
                        f"{runner.MAX_PAGES} sayfa ve {runner.MAX_LINK_CHECKS} site içi bağlantı incelenir; bulgular bu "
                        "örneklem içindir, sitenin tamamı için değildir."))
    scope = []
    for t, by_check in zip(targets, found):
        extra = list(dict.fromkeys(limit for f in by_check.values() for limit in f.get("limits") or []
                                   if _PER_SITE_LIMIT.match(limit) and not limit[0].isdigit()))
        scope.append(f"{_host(t.url)} ({'marka' if t.kind == 'own' else 'rakip'}): {_TARGET_STATUS.get(t.status, t.status)}, "
                     f"{t.pages_checked or 0} sayfa incelendi" + "".join(f". {e.rstrip('.')}" for e in extra))
    blocks += [("h2", "Denetlenen siteler ve kapsam"), ("bullets", scope)]
    if any(t.status != "completed" for t in targets):
        blocks.append(("p", "Okunamayan sitede kontroller “Engellendi” ya da “Bilinmiyor” döner; bu, öğenin sitede "
                            "olmadığı anlamına gelmez."))
    blocks.append(("p", "Durum sözlüğü: İyi / Uyarı / Sorun değerlendirmedir. Görüldü: bulundu, değerlendirme yapılmadı. "
                        "Görülmedi: incelenen sayfalarda bulunamadı. Bilinmiyor: okunamadı ya da ölçülemedi. "
                        "Kapsam dışı: bu site için bilerek ölçülmedi."))

    order: Dict[str, List[str]] = {}
    labels: Dict[str, str] = {}
    for by_check in found:
        for f in by_check.values():
            checks = order.setdefault(f["section"], [])
            if f["check"] not in checks:
                checks.append(f["check"])
            labels[f["check"]] = f["label"]
    for section in sorted(order, key=lambda s: _SECTION_ORDER.index(s) if s in _SECTION_ORDER else len(_SECTION_ORDER)):
        checks = order[section]
        blocks.append(("h2", _SECTION.get(section, section)))
        blocks.append(("table", ["Kontrol"] + [_host(t.url) for t in targets],
                       [[labels[c]] + [_cell(by_check.get(c), c, t) for by_check, t in zip(found, targets)]
                        for c in checks]))
        limits: Dict[str, List[str]] = {}
        for c in checks:
            for by_check in found:
                for limit in (by_check.get(c) or {}).get("limits") or []:
                    limit = _LINK_COUNT.sub("E", limit)
                    if not _PER_SITE_LIMIT.match(limit) and labels[c] not in limits.setdefault(limit, []):
                        limits[limit].append(labels[c])
        if limits:
            blocks += [("p", "Bu bölümün sınırları:"),
                       ("bullets", [f"{', '.join(names)}: {limit}" for limit, names in limits.items()])]
        if section == "site":
            blocks += _speed_blocks(targets, found)
        if section == "contact":
            social = []
            for t, by_check in zip(targets, found):
                f = by_check.get("contact.social_links")
                accounts = ((f or {}).get("value") or {}).get("accounts") or {}
                social.append(f"{_host(t.url)}: " + (", ".join(f"{_SOCIAL.get(n, n)} {u}" for n, u in accounts.items())
                                                      or _status(f).lower()))
            blocks += [("h2", "Sitede bağlantısı verilen sosyal hesaplar"), ("bullets", social),
                       ("p", "Yalnızca siteden bağlantı verilen hesaplardır. Hesabın markaya ait olduğu doğrulanmadı; "
                             "takipçi ve etkileşim sayıları bu denetimde ölçülmedi. Bağlantı görülmemesi hesabın "
                             "olmadığı anlamına gelmez.")]
    answers = audit.answers or {}
    groups: Dict[str, List[str]] = {"brief": [], "account": [], "verified": []}
    for q in QUESTIONS:
        a = answers.get(q["key"])
        if not a:
            continue
        text = f"{q['label']} {a.get('value') or ''} {('— ' + a['note']) if a.get('note') else ''}".strip()
        ev = a.get("evidence")
        if ev:
            text += " (" + "; ".join(f"{name}: {ev[f]}" for f, name in _EVIDENCE if ev.get(f)) + ")"
        groups["verified" if ev else q["group"]].append(text)
    if groups["brief"]:
        blocks += [("h2", "Analist brifi"), ("bullets", groups["brief"])]
    if groups["verified"]:
        blocks += [("h2", "Doğrulanmış hesap verisi [Doğrulandı]"), ("bullets", groups["verified"]),
                   ("p", "Analistin belirtilen kaynakta, belirtilen tarihte kontrol ettiği bilgilerdir; "
                         "sistem bu hesaplara bağlanıp otomatik ölçüm yapmadı.")]
    if groups["account"]:
        blocks += [("h2", "Müşteri beyanları [Beyan]"), ("bullets", groups["account"]),
                   ("p", "Müşterinin cevabıdır; hesap üzerinden doğrulanmadı.")]
    return blocks


def _rank(row: Dict, site: str) -> str:
    if row.get("error"):
        return "Ölçülmedi"
    position = (row.get("positions") or {}).get(site)
    if position:
        return str(position)
    return f"İlk {row.get('checked_top') or 0}'de " + _unseen(row.get("unresolved"))


def _unseen(unresolved) -> str:
    """Adresi okunamayan sonuc varsa 'gozlenmedi' kesin yokluk gibi yazilmaz."""
    return f"eşleşmedi ({unresolved} sonucun adresi okunamadı)" if unresolved else "gözlenmedi"


def _ads(domains, unresolved) -> str:
    if unresolved:
        return ", ".join(list(domains) + [f"{unresolved} reklamın adresi okunamadı"])
    return ", ".join(domains)


def _visibility_blocks(audit) -> List[Block]:
    """Arama gorunurlugu, YouTube ve Google profil BAGLANTILARI.

    Google profil icerigi (puan, yorum, adres) rapora GIRMEZ: yalniz harita baglantisi (plan 3.3).
    """
    data = audit.visibility or {}
    blocks: List[Block] = []
    sites = data.get("sites") or []
    serp = data.get("serp") or {}
    if serp.get("status") == "ok":
        blocks.append(("h1", "Arama görünürlüğü [Gözlem]"))
        blocks.append(("p", f"Ölçüm: {(data.get('measured_at') or '')[:10]}, konum {serp.get('location_used') or data.get('location')}, "
                            "masaüstü, Google Türkiye. Anlık ölçümdür; sıra konuma, cihaza ve güne göre değişir. "
                            f"Kullanılan sorgu: {serp.get('queries_used')} / {serp.get('query_limit')}."))
        rows = serp.get("keywords") or []
        if rows:
            blocks.append(("h2", "Anahtar kelime sıraları"))
            blocks.append(("table", ["Kelime"] + sites, [[r["keyword"]] + [_rank(r, s) for s in sites] for r in rows]))
            blocks.append(("h2", "Reklam ve harita kutusu"))
            blocks.append(("table", ["Kelime", "Reklamı görülen siteler", "Harita kutusu"], [
                [r["keyword"], "Ölçülmedi" if r.get("error") else (_ads(r["ads"], r.get("ads_unresolved")) or "Bu sorguda reklam görülmedi"),
                 "Ölçülmedi" if r.get("error") else ("Görüldü" if r.get("local_pack") else "Görülmedi")] for r in rows]))
            blocks.append(("p", "Reklamın görülmemesi kampanyanın kapalı olduğu anlamına gelmez."))
        brand = serp.get("brand")
        if brand and not brand.get("error"):
            blocks += [("h2", f"Marka araması: {brand['query']}"), ("bullets", [
                f"Marka sitesinin sırası: {brand['own_position'] or 'ilk 10 sonuçta ' + _unseen(brand.get('unresolved'))}",
                f"Bilgi paneli: {'görüldü' if brand['knowledge_panel'] else 'görülmedi'}",
                "Marka adında reklamı görülen başka siteler: " + (_ads(brand["other_ads"], brand.get("ads_unresolved")) or "görülmedi")])]
        for entry in serp.get("reputation") or []:
            if entry.get("results"):
                blocks += [("h2", f"Arama: {entry['query']}"), ("bullets", [
                    f"{r['position']}. {r['title']} ({r['domain'] or r['shown']})" for r in entry["results"]])]
        if serp.get("reputation"):
            blocks.append(("p", "İlk sonuç sayfasında çıkan başlıklardır; tüm internetin sayımı değildir ve içerikleri okunmamıştır."))

    channels = (data.get("youtube") or {}).get("channels") or {}
    found = {site: c for site, c in channels.items() if c.get("status") == "ok"}
    if channels:
        blocks.append(("h1", "YouTube [Gözlem]"))
        if found:
            blocks.append(("table", ["Site", "Kanal", "Abone", "Video", "Son yükleme", "Son 90 günde (incelenen son videolar içinde)", "Son videolarda ort. izlenme"], [
                [site, c["title"], "gizli" if c["subscribers"] is None else str(c["subscribers"]), str(c["video_count"]),
                 c["last_upload"] or "-", f"{c['uploads_90d']} (son {c['recent_sample']} video içinde)",
                 f"{c['recent_avg_views']} ({c['recent_sample']} video)" if c["recent_avg_views"] is not None else "-"]
                for site, c in found.items()]))
            blocks.append(("p", "Abone sayısı YouTube'un gösterdiği yuvarlanmış değerdir. Ortalama izlenme, son videoların izlenme ortalamasıdır. "
                                "Son 90 gün sayısı yalnız incelenen son videolar içindedir; dönem toplamı değildir."))
        blocks += _bullets("Kanalı ölçülemeyen siteler", [f"{site}: {c.get('reason')}" for site, c in channels.items() if c.get("status") != "ok"])

    profiles = (data.get("places") or {}).get("profiles") or {}
    if profiles:
        blocks.append(("h1", "Google İşletme Profili"))
        blocks.append(("bullets", [
            f"{site}: {p['maps_url']}" if p.get("maps_url") else f"{site}: {p.get('reason')}" for site, p in profiles.items()]))
        blocks.append(("p", "Google'ın kullanım koşulları gereği puan, yorum ve adres bilgisi bu rapora alınmaz; güncel bilgi bağlantıdan görülür."))
    return blocks


def _bullets(title: str, items: List[str]) -> List[Block]:
    return [("h2", title), ("bullets", items)] if items else []


def _texts(items, field: str = "text") -> List[str]:
    return [i[field] for i in research.usable(items)]


def _research_blocks(report, briefs: bool = True) -> List[Block]:
    sections = report.sections or {}
    overrides = report.claim_overrides or []
    inputs = report.inputs or {}
    blocks: List[Block] = []

    sector = sections.get("sector") or {}
    if sector.get("status") == "ok":
        blocks.append(("h1", f"Sektör analizi: {inputs.get('sector', '')} ({inputs.get('geography', '')})"))
        blocks += _bullets("Tanım ve alt kategoriler", _texts(sector.get("definition")))
        blocks += _bullets("Başlıca ürün ve hizmetler", _texts(sector.get("services")))
        blocks += _bullets("Büyüme yönü", _texts(sector.get("growth")))
        claims = research.verified_claims(sector, overrides)
        if claims:
            blocks.append(("h2", "Kaynakta geçen rakamlar [Kaynak]"))
            blocks.append(("table", ["Bilgi", "Değer", "Yıl / coğrafya", "Kaynak"],
                           [[c["claim"], f"{c['value']} {c['unit']}".strip(), f"{c['year']} / {c['geography']}",
                             c["check"].get("source_url", "")] for c in claims]))
        else:
            blocks += [("h2", "Rakamlar"), ("p", "Kaynakta doğrulanan rakam bulunamadı; rapora rakam alınmadı.")]
        blocks += _bullets("Güncel gelişmeler", [
            f"{d['text']} ({d['date'] or 'tarih yok'}; {d['source_name'] or 'kaynak adı yok'})"
            for d in research.usable(sector.get("developments"))])
        restrictions = [f"{r['text']} ({r['source_name'] or 'kaynak adı yok'})"
                        + (" [içindeki rakam kaynakta doğrulanamadı]" if r.get("numbers_ok") is False else "")
                        for r in sector.get("restrictions") or []]
        if restrictions:
            blocks += [("h2", "Yasal ve reklam kısıtları"), ("bullets", restrictions),
                       ("p", "Bu bölüm tarihli bir özettir; hukuki görüş değildir.")]
        blocks += _bullets("Sektörel platform ve dizinler", _texts(sector.get("directories")))
        blocks += _bullets("Veri bulunamayan başlıklar", sector.get("not_found") or [])

    demand = sections.get("demand") or {}
    if demand.get("status") == "ok":
        blocks.append(("h1", "Arama talebi [Gözlem]"))
        blocks.append(("p", f"Kaynak: Google Ads, {len(demand['keywords'])} anahtar kelimenin son 12 aylık arama "
                            "hacmi. Arama talebidir; satış mevsimselliği değildir."))
        if demand.get("by_month"):
            blocks.append(("table", ["Ay", "Toplam arama", "Ortalamaya oran"],
                           [[m["month"], str(m["searches"]), str(m["index"])] for m in demand["by_month"]]))
        bids = demand.get("bid_range")
        if bids:
            blocks.append(("p", f"Sayfa başı teklif aralığı (hesap para birimi): ortanca {bids['low_median']} - "
                                f"{bids['high_median']}; en düşük {bids['min']}, en yüksek {bids['max']}. "
                                "Beklenen tıklama maliyeti değildir."))

    audience = sections.get("audience") or {}
    if audience.get("status") == "ok":
        blocks.append(("h1", "Hedef kitle analizi"))

        def themed(items) -> List[str]:
            return [i["theme"] + (f" — “{i['quote']}” ({i['source_name']})" if i.get("quote_found") else "")
                    for i in research.usable(items)]

        blocks += _bullets("İhtiyaç ve problemler", themed(audience.get("needs")))
        blocks += _bullets("Tereddüt ve şikayetler", themed(audience.get("hesitations")))
        blocks += _bullets("Sık sorulan sorular", _texts(audience.get("questions")))
        if audience.get("question_keywords"):
            blocks.append(("h2", "Hacmi olan soru aramaları [Gözlem]"))
            blocks.append(("table", ["Arama", "Aylık hacim"],
                           [[q["keyword"], str(q["monthly_volume"])] for q in audience["question_keywords"]]))
        blocks += _bullets("Güven beklentileri", _texts(audience.get("trust")))

    # Analistin rakam kararlari degistiyse eski sentez ve briefler reddedilen rakami tasiyabilir: girmez.
    synthesis = sections.get("synthesis") or {}
    if research.synthesis_stale(report):
        blocks += [("h1", "Olası segmentler [Hipotez]"), ("p", STALE_NOTE)]
    elif synthesis.get("status") == "ok":
        blocks.append(("h1", "Olası segmentler [Hipotez]"))
        for s in synthesis["segments"]:
            blocks.append(("h2", f"{s['name']} ({s['stage']})"))
            blocks.append(("bullets", [f"İhtiyaç: {s['need']}", f"Dayandığı bulgu: {s['evidence']}",
                                       f"Doğrulanacak varsayım: {s['assumption']}",
                                       f"Değer teklifi: {s['value_proposition']}",
                                       *[f"Mesaj: {m}" for m in s["messages"]],
                                       *[f"Uyarı, kanıtı olmayan iddia: {w}" for w in s.get("warnings") or []]]))
        blocks += _bullets("Fırsat ve farklılaşma [Hipotez]",
                           [f"{o['text']} (dayanak: {o['evidence']})" for o in synthesis.get("opportunities") or []])
        ads = synthesis.get("google_ads") or {}
        blocks += _bullets("Google Ads önerisi [Hipotez]",
                           [f"{g['group']}: {', '.join(g['keywords'])}" for g in ads.get("keyword_groups") or []]
                           + ([f"Negatif kelimeler: {', '.join(ads['negatives'])}"] if ads.get("negatives") else []))
        blocks += _bullets("Meta hedef kitle önerisi [Hipotez]", [
            f"{m['segment']}: {m['location']}; yaş hipotezi {m['age_hypothesis']}; ilgi alanları (serbest metin, "
            f"platformda doğrulanmadı): {', '.join(m['interests'])}" for m in synthesis.get("meta_audiences") or []])

    briefs = (report.briefs or {}) if briefs else {}  # detayli analizde uzun briefler yer almaz
    if briefs and research.briefs_stale(report):
        blocks += [("h1", "Reklam ve kreatif briefler [Hipotez]"), ("p", STALE_NOTE)]
    elif briefs.get("status") == "ok":
        blocks.append(("h1", "Reklam ve kreatif briefler [Hipotez]"))
        notes = {b.get("restriction_note") for b in briefs["ad_ideas"] + briefs["creative_briefs"]} - {None}
        for note in notes:
            blocks.append(("p", f"UYARI: {note}"))
        for idea in briefs["ad_ideas"]:
            blocks.append(("h2", f"Reklam fikri: {idea['segment']} · {idea['channel']}"))
            blocks.append(("bullets", [f"İçgörü: {idea['insight']}", f"Teklif: {idea['offer']}",
                                       f"Mesaj: {idea['message']}", f"Çağrı: {idea['cta']}",
                                       f"Test hipotezi: {idea['test_hypothesis']}",
                                       *[f"Uyarı, kanıtı olmayan iddia: {w}" for w in idea["warnings"]]]))
        for brief in briefs["creative_briefs"]:
            blocks.append(("h2", f"Kreatif brief: {brief['segment']} · {brief['channel']}"))
            blocks.append(("bullets", [f"Biçim: {brief['format']}; süre: {brief['duration']}",
                                       f"Açılış: {brief['hook']}", f"Görsel yön: {brief['visual_direction']}",
                                       f"Çekim listesi: {'; '.join(brief['shot_list'])}", f"Metin: {brief['copy']}",
                                       f"Gereken gerçek kanıt: {'; '.join(brief['proof_needed'])}",
                                       f"A/B varyantı: {brief['ab_variant']}",
                                       *[f"Uyarı, kanıtı olmayan iddia: {w}" for w in brief["warnings"]]]))

    urls = list(dict.fromkeys(
        s["url"] for name in ("sector", "audience") for s in (sections.get(name) or {}).get("sources") or []
        if s["kind"] != "unreadable"))
    blocks += _bullets("Araştırmada okunan kaynaklar", urls)
    return blocks


LIMIT_BLOCKS: List[Block] = [("h1", "Sınırlar"), ("bullets", [
    "Bot engelli ya da JavaScript ile çizilen sitelerde kontroller “Bilinmiyor” döner; bu, özelliğin olmadığı anlamına gelmez.",
    "Rakip trafiği, backlink ve hesap içi veriler (GA4, reklam hesabı) bu raporda yoktur.",
    "Rakamlar yalnız kaynak metninde bulunduysa rapora alınır; bağlamın doğruluğunu analist kontrol eder.",
    "Segmentler, öneriler ve briefler hipotezdir; ölçülmüş demografi ya da sonuç garantisi değildir.",
])]


def build_blocks(workspace, audit=None, report=None, view: Optional[str] = None) -> List[Block]:
    if view:  # karar odakli uc belgeden biri (report_views); eski birlesik rapor asagida aynen durur
        from app.core.digital_audit import report_views

        return [(kind, *[_clean(part) for part in rest])
                for kind, *rest in report_views.build(workspace, audit, report, view)]
    stamp = (report or audit).created_at
    blocks: List[Block] = [
        ("title", f"Dijital Varlık Analizi: {workspace.name or _host(workspace.company_url)}"),
        ("p", f"Rapor verisinin toplandığı tarih: {stamp.strftime('%d.%m.%Y') if stamp else '-'}"),
        ("p", LEGEND),
    ]
    if audit is not None:
        blocks += _audit_blocks(audit)
        blocks += _visibility_blocks(audit)
    if report is not None:
        blocks += _research_blocks(report)
    blocks += LIMIT_BLOCKS
    return [(kind, *[_clean(part) for part in rest]) for kind, *rest in blocks]


def _clean(value):
    if isinstance(value, str):
        return _ILLEGAL.sub("", value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def render_docx(blocks: List[Block]) -> bytes:
    from docx import Document

    doc = Document()
    for kind, *rest in blocks:
        if kind == "title":
            doc.add_heading(rest[0], level=0)
        elif kind in ("h1", "h2"):
            doc.add_heading(rest[0], level=1 if kind == "h1" else 2)
        elif kind == "p":
            doc.add_paragraph(rest[0])
        elif kind == "bullets":
            for item in rest[0]:
                doc.add_paragraph(item, style="List Bullet")
        elif kind == "table":
            header, rows = rest
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            for cell, text in zip(table.rows[0].cells, header):
                cell.text = text
            for row in rows:
                for cell, text in zip(table.add_row().cells, row):
                    cell.text = text
    # Alt bilgi: sayfa numarasi (Word alani; dosya acilinca hesaplanir)
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    footer = doc.sections[0].footer.paragraphs[0]
    footer.add_run("Sayfa ")
    run = footer.add_run()
    for tag, char_type in (("w:fldChar", "begin"), ("w:instrText", None), ("w:fldChar", "end")):
        element = OxmlElement(tag)
        if char_type:
            element.set(qn("w:fldCharType"), char_type)
        else:
            element.text = "PAGE"
        run._r.append(element)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_pdf(blocks: List[Block]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from app.exporters.pdf_exporter import PdfExporter
    from app.exporters.safe_text import safe_paragraph_text as safe

    fonts = SimpleNamespace()
    PdfExporter._register_fonts(fonts)  # Turkce karakterli gomulu font (tek kayit noktasi)
    base = dict(fontName=fonts.font_name, leading=14)
    styles = {
        "title": ParagraphStyle("title", fontName=fonts.font_name_bold, fontSize=18, leading=24, spaceAfter=10),
        "h1": ParagraphStyle("h1", fontName=fonts.font_name_bold, fontSize=14, leading=18, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName=fonts.font_name_bold, fontSize=11, leading=15, spaceBefore=8, spaceAfter=4),
        "p": ParagraphStyle("p", fontSize=9.5, spaceAfter=4, **base),
        "cell": ParagraphStyle("cell", fontSize=8, fontName=fonts.font_name, leading=10),
    }
    width = A4[0] - 3 * cm
    story = []
    for kind, *rest in blocks:
        if kind in ("title", "h1", "h2", "p"):
            story.append(Paragraph(safe(rest[0]), styles[kind]))
        elif kind == "bullets":
            story += [Paragraph("• " + safe(item), styles["p"]) for item in rest[0]]
        elif kind == "table":
            header, rows = rest
            data = [[Paragraph(safe(cell), styles["cell"]) for cell in row] for row in [header] + rows]
            table = Table(data, colWidths=[width / len(header)] * len(header), repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story += [table, Spacer(1, 6)]
    def page_number(canvas, doc):
        canvas.setFont(fonts.font_name, 8)
        canvas.drawRightString(A4[0] - 1.5 * cm, 0.8 * cm, f"Sayfa {doc.page}")

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                      topMargin=1.5 * cm, bottomMargin=1.5 * cm).build(
        story, onFirstPage=page_number, onLaterPages=page_number)
    return buffer.getvalue()


RENDERERS = {
    "docx": (render_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": (render_pdf, "application/pdf"),
}


def render(workspace, audit, report, fmt: str, view: Optional[str] = None) -> Tuple[bytes, str]:
    renderer, media_type = RENDERERS[fmt]
    return renderer(build_blocks(workspace, audit, report, view)), media_type


def filename(workspace, fmt: str, stamp: Optional[str] = None) -> str:
    return f"dijital-varlik-analizi-{workspace.name or 'rapor'}{('-' + stamp) if stamp else ''}.{fmt}"
