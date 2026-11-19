"""Yeni musteri toplantisi icin kisa sunum (16:9 PDF): acil duzeltilecekler + genel aksiyon plani.

Yalniz SITE DENETIMINDEN, kod ile uretilir: yapay zeka, arastirma ya da musteri bilgisi gerekmez.
Teknik ayrinti yerine sade dil kullanir. Olculemeyen kontrol eksik diye gosterilmez; "gorulmedi"
olan sey "yok" diye degil "gormedik, kontrol edip tamamlayacagiz" diye yazilir. Vaat edilen
yapilacak istir, sonuc (siralama, satis) degil.
"""
from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Dict, List, Optional

from app.core.digital_audit import report_doc

W, H = 960.0, 540.0
NAVY, TEAL, RED, AMBER, GREEN = "#0F2A3D", "#12B5A6", "#D64545", "#E0A100", "#2E9E6B"
INK, MUTED, PAPER, LINE = "#1F2933", "#6B7780", "#F5F7F9", "#DDE3E8"

# kontrol -> (sade baslik, neden onemli, biz ne yapacagiz)
PLAIN: Dict[str, tuple] = {
    "site.reachable": ("Site açılmıyor ya da hata veriyor", "Siteye ulaşamayan ziyaretçi başka bir firmaya yönelebilir.",
                       "Erişim sorununu barındırma tarafıyla birlikte gideriyoruz."),
    "site.ssl": ("Güvenlik sertifikası (SSL)", "Tarayıcılar sertifikası sorunlu siteyi uyarıyla gösterebilir.",
                 "Sertifikayı kurup otomatik yenilenecek şekilde ayarlıyoruz."),
    "site.speed_mobile": ("Site telefonda yavaş açılıyor", "Telefondan gelen ziyaretçi yavaş açılan sayfayı beklemeden kapatabilir.",
                          "Görselleri ve sayfa kodunu hafifleterek telefonda açılış hızını iyileştiriyoruz."),
    "site.speed_desktop": ("Site bilgisayarda yavaş açılıyor", "Yavaş açılan sayfa ziyaretçiyi bekletir.",
                           "Sayfa yükünü azaltarak açılış hızını iyileştiriyoruz."),
    "site.mobile_viewport": ("Telefon görünümü ayarı eksik", "Bu ayar olmadan sayfa telefonda küçük ve zor okunur görünebilir.",
                             "Tüm sayfaları telefona uyumlu hale getiriyoruz."),
    "site.structure": ("Temel sayfalar eksik", "Ziyaretçi sizi tanıyacağı ya da size ulaşacağı sayfayı bulamıyor.",
                       "Eksik temel sayfaları (İletişim, Hakkımızda) hazırlıyoruz."),
    "site.cta": ("Ziyaretçiyi harekete geçiren buton yok", "Arama, WhatsApp ya da form butonu olmayan sitede ziyaretçinin size ulaşması zorlaşır.",
                 "Her sayfaya görünür arama, WhatsApp ve form butonları ekliyoruz."),
    "site.broken_links": ("Çalışmayan bağlantılar var", "Tıklanan sayfa açılmazsa ziyaretçi aradığını bulamaz.",
                          "Kırık bağlantıları düzeltip doğru sayfalara yönlendiriyoruz."),
    "seo.titles": ("Sayfa başlıklarında eksik ya da tekrar var", "Google'da görünen ilk satır budur; eksikse sayfa aramada zayıf kalır.",
                   "Her sayfaya kendine özgü, açıklayıcı bir başlık yazıyoruz."),
    "seo.meta_descriptions": ("Arama sonucu açıklamaları eksik", "Google'da başlığın altında çıkan tanıtım cümlesi tıklanmayı etkiler.",
                              "Her sayfa için tıklamaya davet eden açıklamalar yazıyoruz."),
    "seo.h1": ("Sayfa ana başlıkları düzensiz", "Arama motoru sayfanın konusunu ana başlıktan anlar.",
               "Her sayfada tek ve net bir ana başlık düzenliyoruz."),
    "seo.noindex": ("Bazı sayfalar Google'a kapalı", "Aramaya kapatılmış sayfa arama sonuçlarında gösterilmez.",
                    "Yanlışlıkla kapatılmış sayfaları aramaya açıyoruz."),
    "seo.structured_data": ("Google'a işletme bilgisi işaretlemesi görülmedi", "Bu işaretleme adres, saat ve hizmet bilgisinin aramada doğru görünmesine yardım eder.",
                            "İşletme bilgilerinizi Google'ın okuyacağı biçimde ekliyoruz."),
    "seo.image_alt": ("Görsellerde açıklama metni eksik", "Açıklaması olmayan görsel aramada bulunmaz, erişilebilirliği düşürür.",
                      "Görsellere açıklama metinlerini ekliyoruz."),
    "seo.blog": ("Güncel içerik (blog) görülmedi", "Düzenli içerik, sizi arayan kişilerin sorularında görünmenizi sağlar.",
                 "Müşterilerinizin sorduğu konularda düzenli içerik planı hazırlıyoruz."),
    "seo.robots_txt": ("Arama motoru yönlendirme dosyası sorunlu", "Arama motorları hangi sayfaları okuyacağını buradan öğrenir.",
                       "Dosyayı düzenleyip arama motorlarına doğru yönlendirme veriyoruz."),
    "seo.sitemap": ("Site haritası görülmedi", "Site haritası, sayfalarınızın Google tarafından bulunmasını kolaylaştırır.",
                    "Site haritasını oluşturup Google'a bildiriyoruz."),
    "tracking.gtm": ("Ölçüm altyapısı (etiket yöneticisi) görülmedi", "Ölçüm olmadan hangi çalışmanın işe yaradığı bilinemez.",
                     "Ölçüm altyapısını kurup ziyaret ve talepleri izlenebilir hale getiriyoruz."),
    "tracking.ga4": ("Ziyaretçi ölçümü (Google Analytics) sayfada görülmedi", "Kaç kişinin geldiği ve kaçının size ulaştığı ölçülmeden karar verilemez.",
                     "Kurulumu kontrol ediyor, eksikse tamamlayıp arama, WhatsApp ve form taleplerini ölçüyoruz."),
    "tracking.cookie_banner": ("Çerez bildirimi görülmedi", "KVKK kapsamında ziyaretçinin çerezler hakkında bilgilendirilmesi beklenir.",
                               "Mevzuata uygun çerez bildirimi ekliyoruz."),
    "tracking.privacy_page": ("KVKK / gizlilik sayfası görülmedi", "Kişisel veri toplayan sitede aydınlatma metni beklenir.",
                              "KVKK aydınlatma ve gizlilik sayfalarını hazırlıyoruz."),
    "contact.phone": ("Tıklanabilir telefon numarası görülmedi", "Telefondaki ziyaretçi numaraya tek dokunuşla ulaşamayabilir.",
                      "Telefon numarasını her sayfada tıklanabilir hale getiriyoruz."),
    "contact.whatsapp": ("WhatsApp bağlantısı görülmedi", "Aramak yerine yazmak isteyen ziyaretçi için kolay bir yol görülmedi.",
                         "Siteye WhatsApp ile yazma butonu ekliyoruz."),
    "contact.email": ("E-posta adresi görülmedi", "Kurumsal iletişim için e-posta beklenir.",
                      "Kurumsal e-posta adresini iletişim alanlarına ekliyoruz."),
    "contact.forms": ("İletişim formu görülmedi", "Form yoksa mesai dışında gelen ziyaretçi talep bırakmakta zorlanır.",
                      "Kısa ve çalışan bir iletişim formu ekliyoruz."),
    "contact.social_links": ("Sosyal medya bağlantıları görülmedi", "Sitede bağlantı yoksa ziyaretçi sosyal medya hesaplarınıza kolayca geçemez.",
                             "Sosyal medya hesaplarınızı siteyle bağlıyoruz."),
}
# "Gorulmedi" oldugunda musteriye soylenmeye deger kontroller. Reklam pikselleri BILEREK yok:
# reklam verilmeyen (ya da kisitli) sektorde eksik sayilmaz.
_ABSENT_MATTERS = {"seo.structured_data", "seo.blog", "seo.sitemap", "tracking.gtm", "tracking.ga4",
                   "tracking.cookie_banner", "tracking.privacy_page", "contact.phone", "contact.whatsapp",
                   "contact.forms", "contact.social_links", "site.cta"}
_GOOD_TITLE = {"site.reachable": "Site açılıyor", "site.ssl": "Güvenlik sertifikası geçerli",
               "site.speed_mobile": "Telefonda açılış hızı iyi", "site.speed_desktop": "Bilgisayarda açılış hızı iyi",
               "site.mobile_viewport": "Telefon görünümü ayarı sayfa kodunda mevcut", "site.structure": "Temel sayfalar mevcut",
               "site.broken_links": "Kontrol edilen site içi bağlantılarda kırık bulunmadı",
               "seo.titles": "İncelenen sayfalarda başlık eksiği yok",
               "seo.meta_descriptions": "İncelenen sayfalarda arama sonucu açıklaması eksiği yok",
               "seo.h1": "İncelenen sayfalarda ana başlık eksiği yok",
               "seo.noindex": "İncelenen sayfalar aramaya kapatılmamış",
               "seo.image_alt": "Görsel açıklamaları büyük ölçüde tam", "seo.robots_txt": "Arama motoru yönlendirme dosyası mevcut",
               "seo.sitemap": "Site haritası mevcut", "contact.phone": "Tıklanabilir telefon var",
               "contact.whatsapp": "WhatsApp bağlantısı var", "contact.email": "E-posta adresi var",
               "tracking.privacy_page": "KVKK / gizlilik sayfası var", "tracking.cookie_banner": "Çerez bildirimi var"}


def build(audit) -> Dict:
    """Sunumun icerigi (bicimden bagimsiz): acil, diger, iyi, plan. Test edilebilir cekirdek."""
    own = next((t for t in audit.targets if t.kind == "own"), None)
    findings = list((own.findings if own else None) or [])
    pages = (own.pages_checked if own else 0) or 0
    shop = report_doc.is_ecommerce(audit)
    issues, good, unmeasured = [], [], 0
    for f in findings:
        check = f["check"]
        if check.startswith("ecommerce.") and not shop:
            continue
        if f["state"] not in ("observed", "not_observed"):
            unmeasured += 1  # olculemeyen kontrol eksik diye GOSTERILMEZ
            continue
        level = {"problem": 0, "warning": 1}.get(f.get("assessment") or "")
        if level is None and f["state"] == "not_observed" and check in _ABSENT_MATTERS:
            level = 2
        if level is not None and check in PLAIN:
            title, why, fix = PLAIN[check]
            if check == "site.speed_mobile" and level == 1:  # uyari duzeyi "yavas" degildir
                title = "Telefonda açılış hızı iyileştirilebilir"
            seen = report_doc._summary(f, pages) or ("Sayfa kodunda görülmedi." if f["state"] == "not_observed" else "")
            issues.append({"check": check, "level": level, "title": title, "seen": seen, "why": why, "fix": fix})
        elif f.get("assessment") == "ok" and check == "site.cta":
            # Kontrol ucunden BIRINI gormekle "iyi" doner: yalniz gorulenler yazilir
            value = f.get("value") or {}
            seen_cta = [name for name, key in (("telefon", "phone"), ("WhatsApp", "whatsapp"), ("form", "forms")) if value.get(key)]
            if seen_cta:
                good.append("Sitede görülen iletişim yolu: " + ", ".join(seen_cta))
        elif f.get("assessment") == "ok" and check in _GOOD_TITLE:
            good.append(_GOOD_TITLE[check])
    # Arama gorunurlugu olculduyse: sitenin ilk sonuclarda GORULMEDIGI aramalar (adresi okunamayan
    # sonuc varsa o arama sayilmaz; "gormedik" denir, "yoksunuz" denmez).
    visibility = getattr(audit, "visibility", None) or {}
    serp = visibility.get("serp") or {}
    own_site = (visibility.get("sites") or [None])[0]
    if serp.get("status") == "ok" and own_site:
        unseen = [r["keyword"] for r in serp.get("keywords") or []
                  if not r.get("error") and not r.get("unresolved") and (r.get("positions") or {}).get(own_site) is None]
        if unseen:
            top = max((r.get("checked_top") or 0) for r in serp["keywords"]) or 10
            issues.insert(0, {
                "check": "visibility.serp", "level": 1,
                "title": f"Ölçtüğümüz {len(unseen)} aramada sitenizi ilk {top} sonuçta görmedik",
                "seen": ", ".join(f"“{k}”" for k in unseen[:4]) + ". Anlık ölçümdür; sıra konuma ve güne göre değişir.",
                "why": "Arama yapan kişi önce ilk sonuçları görür; orada olmayan site daha az ziyaret alır.",
                "fix": "Bu aramalar için sayfa içi ve yerel arama (SEO) çalışması yapıyoruz."})
    # ACIL yalniz gercekten "sorun" olanlardir; hepsi acil bolumunde kalir (sayi kac olursa olsun).
    # Uyari ve "gorulmedi" olanlar ONCELIKLI IYILESTIRMEdir. Iyi sitede zorla acil sorun uretilmez.
    urgent = [i for i in issues if i["level"] == 0]
    priority = sorted((i for i in issues if i["level"] > 0), key=lambda i: i["level"])
    # Site okunamadiysa (ya da kontrollerin cogu olculemediyse) eksik bulunmamis gibi gosterilmez
    measured_count = len(findings) - unmeasured
    limited = bool(own) and (own.status != "completed" or unmeasured > measured_count)
    first = [i["fix"] for i in urgent] or [i["fix"] for i in priority[:3]]
    rest = [i["fix"] for i in (priority if urgent else priority[3:])]
    if limited:
        first = ["Sitenize erişimi ve teknik doğrulamayı sizinle birlikte sağlıyor, incelemeyi tamamlıyoruz."] + first
    plan = [
        ("İlk 30 gün", "Acil düzeltmeler" if urgent else "İnceleme ve erişim" if limited and not priority
         else "Öncelikli iyileştirmeler", first[:7] or ["Tespit edilen acil bir sorun yok; ölçüm altyapısını doğruluyoruz."]),
        ("30-60 gün", "İyileştirme", rest[:6]
         or (["İnceleme sonucuna göre düzeltme planını sizinle birlikte netleştiriyoruz."] if limited else
             ["Yapılan düzeltmelerin etkisini ölçüyor, eksik kalanları tamamlıyoruz."])),
        ("60-90 gün", "Takip ve raporlama", ["Hız, arama görünürlüğü ve ölçüm kontrollerini düzenli tekrarlıyoruz.",
                                           "Yapılan işleri ve ölçülen sonuçları sizinle aylık olarak paylaşıyoruz."]),
    ]
    speed = []
    for t in audit.targets:
        f = next((x for x in t.findings or [] if x["check"] == "site.speed_mobile" and x["state"] == "observed"), None)
        score = (((f or {}).get("value") or {}).get("scores") or {}).get("performance")
        if score is not None:
            speed.append({"site": report_doc._host(t.url), "own": t.kind == "own", "score": int(score)})
    measured = audit.completed_at or audit.created_at
    return {"host": report_doc._host(own.url) if own else "", "date": measured.strftime("%d.%m.%Y") if measured else "-",
            "pages": pages, "urgent": urgent, "priority": priority, "good": good[:12], "plan": plan, "speed": speed,
            "counts": {"urgent": len(urgent), "improve": len(priority), "good": len(good)},
            "unmeasured": unmeasured, "limited": limited,
            "status": report_doc._TARGET_STATUS.get(own.status, own.status) if own else "denetlenmedi"}


def render(workspace, audit) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    from app.exporters.pdf_exporter import PdfExporter

    data = build(audit)
    fonts = SimpleNamespace()
    PdfExporter._register_fonts(fonts)  # Turkce karakterli gomulu font
    regular, bold = fonts.font_name, fonts.font_name_bold
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(W, H))
    c.setTitle(f"Dijital Varlık Ön İncelemesi: {data['host']}")
    page = [0]

    def text(x, y, value, size=12, color=INK, font=None, width: Optional[float] = None, leading=None,
             max_lines: Optional[int] = None) -> float:
        """Sarmali metin; bir sonraki satirin y'sini dondurur."""
        font = font or regular
        c.setFont(font, size)
        c.setFillColor(HexColor(color))
        lines = simpleSplit(str(value), font, size, width) if width else [str(value)]
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip(" .,;") + "…"
        step = leading or size * 1.32
        for line in lines:
            c.drawString(x, y, line)
            y -= step
        return y

    def box(x, y, w, h, fill, radius=10, stroke=None):
        c.setFillColor(HexColor(fill))
        c.setStrokeColor(HexColor(stroke or fill))
        c.roundRect(x, y, w, h, radius, fill=1, stroke=1 if stroke else 0)

    def slide(title: str, subtitle: str = ""):
        if page[0]:
            c.showPage()
        page[0] += 1
        box(0, 0, W, H, "#FFFFFF", 0)
        box(0, H - 8, W, 8, TEAL, 0)
        text(48, H - 62, title, 27, NAVY, bold)
        if subtitle:
            text(48, H - 88, subtitle, 12.5, MUTED, width=W - 96)
        text(48, 22, f"{data['host']}  ·  {data['date']}", 9, MUTED)
        c.setFont(regular, 9)
        c.drawRightString(W - 48, 22, str(page[0]))

    # 1) Kapak
    page[0] = 1
    box(0, 0, W, H, NAVY, 0)
    box(48, 150, 6, 240, TEAL, 3)
    text(72, 350, "Dijital Varlık", 44, "#FFFFFF", bold)
    text(72, 300, "Ön İncelemesi", 44, "#FFFFFF", bold)
    text(72, 248, data["host"], 20, TEAL, bold)
    text(72, 216, f"Ölçüm tarihi: {data['date']}", 13, "#C9D5DD")
    text(72, 170, "Sitenizde gördüklerimiz, öncelikle düzeltilmesi gerekenler ve sizin için yapacaklarımız.",
         13, "#C9D5DD", width=560)

    # 2) Ozet
    slide("Bir bakışta siteniz", "Site içeriğini yeterince inceleyemedik; aşağıdaki sayılar yalnız okunabilen kısım içindir."
          if data["limited"] else f"Sitenizin dışarıdan görülebilen {data['pages']} sayfasını inceledik.")
    tiles = [(str(data["counts"]["urgent"]), "Acil düzeltilmeli", RED),
             (str(data["counts"]["improve"]), "İyileştirilebilir", AMBER),
             (str(data["counts"]["good"]), "İyi durumda", GREEN)]
    own_speed = next((s for s in data["speed"] if s["own"]), None)
    if data["limited"] or (data["unmeasured"] and not own_speed):
        tiles.append((str(data["unmeasured"]), "İncelenemeyen kontrol", MUTED))
    elif own_speed:
        tiles.append((f"{own_speed['score']}", "Telefonda açılış hızı (100 üzerinden)", NAVY))
    width = (W - 96 - 20 * (len(tiles) - 1)) / len(tiles)
    for i, (number, label, color) in enumerate(tiles):
        x = 48 + i * (width + 20)
        box(x, 220, width, 190, PAPER, 12)
        box(x, 400, width, 10, color, 4)
        text(x + 22, 318, number, 64, color, bold)
        text(x + 22, 262, label, 13.5, INK, width=width - 40)
    lead = (data["urgent"] or data["priority"] or [None])[0]
    if data["limited"]:
        text(48, 170, "Önce yapılacak", 12, MUTED)
        text(48, 144, f"Site içeriğini yeterince inceleyemedik (durum: {data['status']})", 20, NAVY, bold, width=W - 96)
        text(48, 118, "Bu, sitenizde eksik olmadığı anlamına gelmez. İlk adımda erişimi birlikte sağlayıp incelemeyi tamamlıyoruz.",
             13, INK, width=W - 96)
    elif lead:
        text(48, 170, "En önce ele alınacak konu" if data["urgent"] else "Acil bir sorun görmedik. İlk ele alınacak iyileştirme",
             12, MUTED)
        text(48, 144, lead["title"], 20, NAVY, bold, width=W - 96)
        text(48, 118, lead["why"], 13, INK, width=W - 96)
    if data["unmeasured"] and not data["limited"]:
        text(48, 84, f"{data['unmeasured']} kontrol ölçülemedi; bunlar eksik sayılmadı.", 10.5, MUTED)

    # 3) Acil sorunlar ve oncelikli iyilestirmeler AYRI basliklarda (slayt basina 3 kart)
    colors = {0: RED, 1: AMBER, 2: TEAL}
    labels = {0: "ACİL", 1: "ÖNCELİKLİ", 2: "GÖRÜLMEDİ"}

    def cards(title: str, subtitle: str, items: List[Dict]) -> None:
        for start in range(0, len(items), 3):
            slide(title + (" (devam)" if start else ""), subtitle)
            cw = (W - 96 - 40) / 3
            for i, item in enumerate(items[start:start + 3]):
                x = 48 + i * (cw + 20)
                box(x, 60, cw, 360, PAPER, 12)
                box(x, 60, 7, 360, colors[item["level"]], 3)
                text(x + 24, 392, labels[item["level"]], 9.5, colors[item["level"]], bold)
                y = text(x + 24, 366, item["title"], 16.5, NAVY, bold, width=cw - 44, max_lines=3)
                for head, body in (("Gördüğümüz", item["seen"]), ("Neden önemli", item["why"]), ("Çözümümüz", item["fix"])):
                    if body:
                        y = text(x + 24, y - 12, head, 9.5, MUTED, bold)
                        y = text(x + 24, y - 2, body, 11.5, INK, width=cw - 44, max_lines=5)

    cards("İvedilikle çözülmesi gerekenler", "Sitenizin çalışmasını ya da aramada görünmesini doğrudan engelleyen sorunlar.",
          data["urgent"])
    shown = data["priority"][:6]
    cards("Öncelikli iyileştirmeler", "Acil değil; ancak ziyaretçi deneyimini ve görünürlüğü güçlendirecek konular.", shown)

    # 4) Kalan iyilestirmeler + iyi durumda olanlar
    more = data["priority"][6:14]
    if more or data["good"]:
        slide("Diğer bulgular", "Kalan iyileştirme alanları ve sitenizde iyi durumda olanlar.")
        half = (W - 96 - 30) / 2
        text(48, 400, "Diğer iyileştirme alanları", 15, AMBER, bold)
        y = 372
        for item in more or [{"title": "Önceki sayfalardakilerin dışında öne çıkan bir eksik görmedik."}]:
            box(48, y + 1, 6, 6, AMBER, 3)
            y = text(64, y, item["title"], 12, INK, width=half - 20, max_lines=2) - 7
        text(48 + half + 30, 400, "İyi durumda olanlar", 15, GREEN, bold)
        y = 372
        for title in data["good"] or ["Bu incelemede iyi durumda olduğunu ölçebildiğimiz bir kontrol yok."]:
            box(48 + half + 30, y + 1, 6, 6, GREEN, 3)
            y = text(64 + half + 30, y, title, 12, INK, width=half - 20, max_lines=2) - 7

    # 5) Rakiplerle hiz karsilastirmasi (en az iki site olculduyse)
    if len(data["speed"]) >= 2 and own_speed:
        slide("Rakiplerinize göre telefonda açılış hızı",
              "100 üzerinden puan; yüksek olan daha hızlıdır. Ana sayfa ölçümüdür ve günden güne birkaç puan değişir.")
        y = 380
        for row in sorted(data["speed"], key=lambda s: -s["score"]):
            color = TEAL if row["own"] else "#9AA7B0"
            text(48, y + 6, row["site"] + (" (siz)" if row["own"] else ""), 13, NAVY if row["own"] else INK,
                 bold if row["own"] else regular, width=250, max_lines=1)
            box(310, y, 520, 24, PAPER, 6)
            box(310, y, max(520 * row["score"] / 100.0, 8), 24, color, 6)
            text(842, y + 6, str(row["score"]), 14, NAVY, bold)
            y -= 54

    # 6) Aksiyon plani (her zaman son slayt)
    slide("Sizin için yapacaklarımız: 90 günlük plan", "Öncelik sırasına göre üç adımda ilerliyoruz.")
    cw = (W - 96 - 40) / 3
    for i, (period, name, items) in enumerate(data["plan"]):
        x = 48 + i * (cw + 20)
        box(x, 78, cw, 342, PAPER, 12)
        box(x, 378, cw, 42, NAVY if i == 0 else "#27475C" if i == 1 else "#3D6078", 12)
        text(x + 20, 393, period, 15, "#FFFFFF", bold)
        text(x + 20, 352, name, 12, TEAL, bold)
        y = 328
        for item in items:
            box(x + 20, y + 1, 6, 6, TEAL, 3)
            y = text(x + 36, y, item, 11, INK, width=cw - 56, max_lines=4, leading=14) - 6
            if y < 96:
                break
    text(48, 56, ("Site içeriği yeterince incelenemedi; plan, erişim sağlanıp inceleme tamamlandığında güncellenecektir. "
                  if data["limited"] else "")
         + f"Bu ön inceleme sitenizin dışarıdan görülebilen {data['pages']} sayfasına ve {data['date']} tarihli ölçüme dayanır. "
                 "Analitik ve reklam hesaplarınızın içi incelenmemiştir; ilk adımda birlikte kontrol edilir. Plan, yapılacak işleri "
                 "gösterir; sıralama ya da satış sonucu garantisi değildir.", 8.5, MUTED, width=W - 96, leading=11)
    c.save()
    return buffer.getvalue()
