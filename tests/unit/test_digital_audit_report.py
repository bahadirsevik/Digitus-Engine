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
    # Rakip tek yazimla (alan adi) gecer; denetlenen alt adres kapsam satirinda bir kez yazilir
    assert "rakip.test (rakip; denetlenen adres rakip.test/tr): site erişimi engelledi, 0 sayfa incelendi" in text
    assert all("rakip.test/tr" not in cell for b in blocks if b[0] == "table" for cell in b[1])
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
    assert "rakip.test: engellendi" in text
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


def test_brief_declaration_and_verified_data_are_labelled_apart():
    audit = _audit()
    audit.answers = {
        "business_model": {"value": "E-ticaret", "note": None},
        "crm": {"value": "Evet", "note": "HubSpot"},
        "search_console": {"value": "Evet", "note": None, "evidence": {
            "source": "Search Console paneli", "checked_at": "2026-11-17", "verified_by": "Analist A",
            "period": "son 28 gün"}},
    }
    blocks = report_doc.build_blocks(WORKSPACE, audit, None)

    def after(heading):
        return blocks[blocks.index(("h2", heading)) + 1][1]

    assert after("Analist brifi") == ["İş modeli nedir? E-ticaret"]
    assert after("Müşteri beyanları [Beyan]") == ["CRM ya da talep takip yöntemi var mı? Evet — HubSpot"]
    assert after("Doğrulanmış hesap verisi [Doğrulandı]") == [
        "Google Search Console bağlı mı? Evet (kaynak: Search Console paneli; kontrol tarihi: 2026-11-17; "
        "doğrulayan: Analist A; dönem: son 28 gün)"]


def test_ecommerce_section_is_hidden_unless_the_brand_site_is_a_shop():
    def audit_with(state):
        audit = _audit()
        audit.targets[0].findings.append(
            finding("ecommerce", "ecommerce.platform", "E-ticaret altyapısı", state, "info",
                    value={"platforms": ["Shopify"]} if state == "observed" else None))
        audit.targets[1].findings.append(finding("ecommerce", "ecommerce.platform", "E-ticaret altyapısı", "observed", "info",
                                                 value={"platforms": ["WooCommerce"]}))
        return audit

    hidden = repr(report_doc.build_blocks(WORKSPACE, audit_with("not_observed"), None))
    assert "E-ticaret" not in hidden and "WooCommerce" not in hidden  # rakipte olsa bile bolum cikmaz
    assert "Shopify" in repr(report_doc.build_blocks(WORKSPACE, audit_with("observed"), None))
    brief = audit_with("not_observed")
    brief.answers = {"business_model": {"value": "E-ticaret", "note": None}}
    assert ("h2", "E-ticaret") in report_doc.build_blocks(WORKSPACE, brief, None)


def _pitch(findings, status="completed", visibility=None):
    from app.core.digital_audit import pitch_deck

    stamp = datetime(2026, 11, 16, tzinfo=timezone.utc)
    audit = SimpleNamespace(created_at=stamp, completed_at=stamp, answers={}, visibility=visibility, targets=[
        SimpleNamespace(url="https://www.marka.test/", kind="own", status=status, pages_checked=10, findings=findings)])
    assert pitch_deck.render(WORKSPACE, audit).startswith(b"%PDF")
    return pitch_deck.build(audit)


def test_reputation_results_are_labelled_by_relevance():
    audit = _audit()
    audit.visibility = {"sites": ["marka.test"], "serp": {"status": "ok", "keywords": [], "brand": {
        "query": "Marka Klinik", "own_position": 1, "knowledge_panel": False, "other_ads": []}, "reputation": [
        {"query": "Marka Klinik şikayet", "results": [
            {"position": 1, "title": "Marka Klinik şikayetleri", "domain": "sikayet.test", "shown": ""},
            {"position": 2, "title": "Genel diş kliniği şikayetleri", "domain": "forum.test", "shown": ""},
            {"position": 3, "title": "İletişim", "domain": "marka.test", "shown": ""}]}]}}
    text = repr(report_doc.build_blocks(WORKSPACE, audit, None))
    assert "1. [Başlıkta marka adı geçiyor] Marka Klinik şikayetleri" in text
    assert "2. [İlgisi doğrulanmadı] Genel diş kliniği şikayetleri" in text  # markaya atfedilmez
    assert "3. [Markanın kendi sitesi] İletişim" in text
    assert "“İlgisi doğrulanmadı” işaretli sonuçlar marka hakkında bulgu sayılmaz" in text


def test_pitch_deck_keeps_urgent_and_improvement_apart():
    data = _pitch(_audit().targets[0].findings + [
        finding("seo", "seo.noindex_home", "x", "unknown"),
        finding("tracking", "tracking.meta_pixel", "Meta Pixel izi", "not_observed"),
        finding("ecommerce", "ecommerce.platform", "E-ticaret altyapısı", "not_observed", "info"),
    ])
    # Normal site: "sorun" yoksa acil bolumu BOS kalir; uyarilar oncelikli iyilestirmedir
    assert data["urgent"] == [] and data["counts"]["urgent"] == 0
    titles = [i["title"] for i in data["priority"]]
    assert "Telefonda açılış hızı iyileştirilebilir" in titles  # 79/100 "yavas" diye yazilmaz
    ga4 = next(i for i in data["priority"] if i["check"] == "tracking.ga4")
    assert "görülmedi" in ga4["seen"] and "kontrol ediyor, eksikse" in ga4["fix"]  # kesin yokluk iddiasi yok
    text = repr(data)
    assert "E-ticaret" not in text and "Meta Pixel" not in text  # magaza degil; reklam pikseli eksik sayilmaz
    assert data["unmeasured"] == 2 and data["limited"] is False  # olculemeyen eksik sayilmaz
    # Plan son bolumdur ve ayni siniflamayi kullanir: acil yokken ilk adim "oncelikli iyilestirmeler"
    assert [(p[0], p[1]) for p in data["plan"]][0] == ("İlk 30 gün", "Öncelikli iyileştirmeler")
    assert data["plan"][0][2] == [i["fix"] for i in data["priority"][:3]]
    assert data["plan"][1][2] == ([i["fix"] for i in data["priority"][3:]][:6]
                                  or ["Yapılan düzeltmelerin etkisini ölçüyor, eksik kalanları tamamlıyoruz."])


def test_pitch_deck_never_pushes_a_real_problem_out_of_the_urgent_section():
    problems = [finding("site", check, check, "observed", "problem") for check in (
        "site.reachable", "site.ssl", "site.speed_mobile", "site.broken_links", "seo.noindex", "seo.titles",
        "seo.meta_descriptions", "seo.h1")]
    data = _pitch(problems + [finding("seo", "seo.sitemap", "Sitemap", "not_observed")])
    assert len(data["urgent"]) == 8 and all(i["level"] == 0 for i in data["urgent"])  # sekizi de acil kalir
    assert [i["check"] for i in data["priority"]] == ["seo.sitemap"]
    assert data["plan"][0][1] == "Acil düzeltmeler" and data["plan"][1][2] == [data["priority"][0]["fix"]]


def test_pitch_deck_does_not_claim_more_than_the_measurement():
    data = _pitch([
        finding("site", "site.cta", "CTA", "observed", "ok", value={"phone": True, "whatsapp": False, "forms": 0}),
        finding("site", "site.broken_links", "Kırık bağlantılar", "observed", "ok", value={"checked": 20, "broken": []}),
    ], visibility={"sites": ["marka.test"], "serp": {"status": "ok", "keywords": [
        {"keyword": "diş hekimi", "positions": {"marka.test": None}, "checked_top": 20, "unresolved": 0},
        {"keyword": "implant", "positions": {"marka.test": None}, "checked_top": 20, "unresolved": 3},
        {"keyword": "kanal", "positions": {"marka.test": 4}, "checked_top": 20, "unresolved": 0}]}})
    # Yalniz telefon gorulduyse "arama, WhatsApp ve form var" denmez
    assert data["good"] == ["Sitede görülen iletişim yolu: telefon", "Kontrol edilen site içi bağlantılarda kırık bulunmadı"]
    serp = data["priority"][0]
    # Adresi okunamayan sonucu olan arama sayilmaz; baslik olcumun kapsamini soyler
    assert serp["title"] == "Ölçtüğümüz 1 aramada sitenizi ilk 20 sonuçta görmedik" and "implant" not in serp["seen"]


def test_pitch_deck_says_so_when_the_site_could_not_be_read():
    note = "Sayfa içeriği okunamadığı için değerlendirilemedi."
    data = _pitch([finding("seo", "seo.titles", "Sayfa başlıkları", "blocked", note=note),
                   finding("tracking", "tracking.ga4", "GA4 izi", "blocked", note=note),
                   finding("site", "site.ssl", "SSL sertifikası", "observed", "ok")], status="blocked")
    assert data["limited"] is True and data["unmeasured"] == 2 and data["urgent"] == [] and data["priority"] == []
    # Eksik bulunmamis gibi gorunmez: ilk adim erisim ve incelemeyi tamamlamak
    assert data["plan"][0][1] == "İnceleme ve erişim" and "incelemeyi tamamlıyoruz" in data["plan"][0][2][0]
    assert data["status"] == "site erişimi engelledi"


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_audit_report_renders(fmt):
    content, _ = report_doc.render(WORKSPACE, _audit(), None, fmt)
    assert len(content) > 2000
