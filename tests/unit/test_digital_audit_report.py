"""Denetim raporu: saklanan deger, kapsam ve sinirlar dosyaya yazilir; toplanmayan bilgi uretilmez."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.digital_audit import report_doc
from app.core.digital_audit.runner import TRACKER_LIMIT, finding

SCOPE = "10 sayfa incelendi."
BLOCKED_NOTE = "Sayfa içeriği okunamadığı için değerlendirilemedi; bu, öğenin sitede olmadığı anlamına gelmez."


def _audit():
    speed = {"available": True, "scores": {"performance": 79, "seo": 92, "accessibility": 88, "best-practices": 96},
             "lab": {"largest-contentful-paint": "4,2 sn", "cumulative-layout-shift": "0.01"}, "field": {}}
    own = [
        finding("site", "site.domain", "Alan adı kaydı", "observed", "info",
                value={"expiration": "2027-03-01", "registration": "2015-03-01", "registrar": "Kayıt AŞ"}),
        finding("site", "site.speed_mobile", "Açılış hızı (mobil)", "observed", "warning", value=speed),
        finding("site", "site.speed_desktop", "Açılış hızı (masaüstü)", "unknown", note="Hız ölçümü alınamadı (timeout)."),
        finding("site", "site.broken_links", "Kırık bağlantılar", "observed", "ok", value={"checked": 20, "broken": []},
                limits=["20 site içi bağlantı denetlendi; engel ya da bağlantı hatası veren adresler kırık sayılmadı."]),
        finding("seo", "seo.titles", "Sayfa başlıkları (title)", "observed", "warning",
                value={"missing": 1, "duplicate": 2, "too_long": 0}, limits=[SCOPE]),
        finding("seo", "seo.noindex", "Arama motorlarına kapalı sayfa (noindex)", "observed", "ok",
                value={"noindex_pages": []}, limits=[SCOPE]),
        finding("seo", "seo.blog", "Blog ve güncellik", "observed", "info",
                value={"url": "https://marka.test/blog", "newest_date": "2026-10-28", "dated_items": 8}),
        finding("tracking", "tracking.ga4", "GA4 izi", "not_observed", value={"ids": []},
                note="Sayfa kodunda iz bulunamadı.", limits=[TRACKER_LIMIT, SCOPE]),
        finding("contact", "contact.social_links", "Sosyal medya hesap bağlantıları", "observed", "info",
                value={"accounts": {"instagram": "https://instagram.com/marka", "facebook": "https://facebook.com/marka"}}),
    ]
    rival = [
        finding("site", "site.speed_mobile", "Açılış hızı (mobil)", "unknown", note="Hız ölçümü alınamadı (timeout)."),
        finding("seo", "seo.titles", "Sayfa başlıkları (title)", "blocked", note=BLOCKED_NOTE),
        finding("tracking", "tracking.ga4", "GA4 izi", "blocked", note=BLOCKED_NOTE),
        finding("contact", "contact.social_links", "Sosyal medya hesap bağlantıları", "blocked", note=BLOCKED_NOTE),
    ]
    stamp = datetime(2026, 11, 16, tzinfo=timezone.utc)
    return SimpleNamespace(
        created_at=stamp, completed_at=stamp, answers={}, visibility=None,
        targets=[SimpleNamespace(url="https://www.marka.test/", kind="own", status="completed", pages_checked=10, findings=own),
                 SimpleNamespace(url="https://rakip.test/tr", kind="competitor", status="blocked", pages_checked=0, findings=rival)])


WORKSPACE = SimpleNamespace(name="Marka", company_url="https://marka.test")


def _table(blocks, first_cell):
    for kind, *rest in blocks:
        if kind == "table":
            for row in rest[1]:
                if row[0] == first_cell:
                    return row
    raise AssertionError(first_cell)


def test_cells_carry_stored_values_with_scope():
    blocks = report_doc.build_blocks(WORKSPACE, _audit(), None)
    text = repr(blocks)
    assert "Denetim tarihi: 16.11.2026" in text
    assert "marka.test (marka): denetlendi, 10 sayfa incelendi" in text
    assert "rakip.test/tr (rakip): site erişimi engelledi, 0 sayfa incelendi" in text
    assert _table(blocks, "Alan adı kaydı")[1] == "Görüldü · bitiş 2027-03-01 · kayıt 2015-03-01 · Kayıt AŞ"
    assert _table(blocks, "Sayfa başlıkları (title)")[1:] == ["Uyarı · 1 eksik, 2 tekrar (incelenen 10 sayfada)", "Engellendi"]
    assert _table(blocks, "Arama motorlarına kapalı sayfa (noindex)")[1] == "İyi · incelenen 10 sayfada kapalı sayfa yok"
    assert _table(blocks, "Blog ve güncellik")[1] == "Görüldü · en yeni yazı 2026-10-28, sayfada okunan farklı tarih sayısı 8"
    assert "Instagram https://instagram.com/marka, Facebook https://facebook.com/marka" in text
    assert "Gözlendi (iyi)" not in text


def test_sample_url_list_is_not_printed_as_total():
    urls = [f"https://marka.test/{i}" for i in range(5)]

    def summary(check, key, **extra):
        return report_doc._summary(finding("seo", check, "x", "observed", "warning", value={key: urls, **extra}), 10)

    # Yeni kayit: toplam ayri saklanir
    assert summary("seo.noindex", "noindex_pages", count=10) == "10 sayfa aramaya kapalı"
    assert summary("site.mobile_viewport", "pages_without_viewport", count=10) == "10 sayfada ayar yok"
    # Eski kayit: 5'lik ornek tavani kesin sayi diye yazilmaz
    assert summary("seo.noindex", "noindex_pages") == "en az 5 sayfa aramaya kapalı"
    assert report_doc._summary(finding("seo", "seo.noindex", "x", "observed", "warning",
                                       value={"noindex_pages": urls[:2]}), 10) == "2 sayfa aramaya kapalı"


def test_unknown_not_seen_and_out_of_scope_stay_distinct():
    blocks = report_doc.build_blocks(WORKSPACE, _audit(), None)
    text = repr(blocks)
    # Gorulmedi (okundu, bulunamadi) != Engellendi (okunamadi); engelli hedefte genel not tekrarlanmaz
    assert _table(blocks, "GA4 izi")[1:] == ["Görülmedi · Sayfa kodunda iz bulunamadı.", "Engellendi"]
    # Olculemeyen "Bilinmiyor"; rakipte hic olculmeyen masaustu "Kapsam disi"
    assert _table(blocks, "Açılış hızı (masaüstü)")[1:] == [
        "Bilinmiyor · Hız ölçümü alınamadı (timeout).", "Kapsam dışı (rakipte ölçülmez)"]
    assert _table(blocks, "Performans (masaüstü)")[1:] == ["Bilinmiyor", "Kapsam dışı (rakipte ölçülmez)"]
    assert "rakip.test/tr: engellendi" in text
    # Bolum siniri bolumun altinda; siteye ozel kapsam cumlesi sinir listesinde tekrarlanmaz
    assert f"GA4 izi: {TRACKER_LIMIT}" in text
    assert f": {SCOPE}" not in text
    # Siteye gore degisen baglanti sayisi hucrede kalir, sinir cumlesi tek satir olur
    assert _table(blocks, "Kırık bağlantılar")[1] == "İyi · 0 kırık / 20 bağlantı"
    assert "Kırık bağlantılar: Engel ya da bağlantı hatası veren adresler kırık sayılmadı." in text


def test_speed_table_prints_only_collected_measurements():
    blocks = report_doc.build_blocks(WORKSPACE, _audit(), None)
    assert _table(blocks, "Performans (mobil)")[1:] == ["79/100", "Bilinmiyor"]
    assert _table(blocks, "SEO teknik kontrolü (mobil)")[1] == "92/100"
    assert _table(blocks, "En büyük öğenin yüklenmesi, LCP (mobil)")[1] == "4,2 sn"
    # Toplanmayan laboratuvar olcumu ve saha verisi uydurulmaz
    assert _table(blocks, "Engelleme süresi, TBT (mobil)")[1] == "Ölçülmedi"
    assert _table(blocks, "Gerçek kullanıcı verisi (mobil, kategori)")[1] == "Bu site için veri yok"


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_audit_report_renders(fmt):
    content, _ = report_doc.render(WORKSPACE, _audit(), None, fmt)
    assert len(content) > 2000
