"""Karar odakli uc belge gorunumu: yonetici raporu, detayli analiz, uygulama belgesi.

Ucu de AYNI kayitli icerikten uretilir (denetim + arastirma + strateji); yapay zeka cagrilmaz.
Guncelligini kaybetmis strateji, segment ya da brief hicbir gorunume girmez.
"""
from __future__ import annotations

import json
import re

from typing import Dict, List

from app.core.digital_audit import report_doc, research, strategy
from app.core.digital_audit.report_doc import Block
from app.core.digital_audit.research import CHANNELS
from app.core.digital_audit.service import QUESTIONS

VIEW_TITLE = {"executive": "Yönetici raporu", "detail": "Detaylı analiz", "implementation": "Uygulama belgesi"}
NO_STRATEGY = ("Strateji taslağı henüz üretilmedi. Özet, SWOT, karar noktaları ve aksiyon planı, "
               "“Strateji taslağı üret” adımından sonra bu belgeye girer.")
_ANCHORS = {"site": ("h2", "Web sitesi"), "seo": ("h2", "SEO"), "tracking": ("h2", "Ölçümleme"),
            "contact": ("h2", "İletişim kanalları"), "ecommerce": ("h2", "E-ticaret"),
            "visibility": ("h1", "Arama görünürlüğü"), "sector": ("h1", "Sektör analizi"),
            "audience": ("h1", "Hedef kitle analizi"), "youtube": ("h1", "YouTube"), "reputation": ("h2", "Arama: "),
            "demand": ("h1", "Arama talebi"), "segments": ("h1", "Olası segmentler")}  # 'competitors': ayri bolum yok
_AD_CHECK = {
    "önerilebilir": "Araştırmada reklam kısıtı bulunmadı. Bu, hukuki uygunluk onayı değildir.",
    "koşullu": "Sektörde reklam/tanıtım kısıtı bulundu; uygunluk değerlendirmesi yapılmadan yayımlanmaz.",
    "değerlendirilmedi": "Reklam kısıtı araştırması sonuç vermedi; uygunluk değerlendirilmedi.",
    "önerilmez": "Analist kararıyla önerilmiyor.",
}


# Brief metninde gecen kisi unvani (Uzm. Dr. Ad Soyad): tescil belgesi gorulmeden dogrulanmis sayilmaz
_TITLE = re.compile(r"(?:Prof\.|Doç\.|Uzm\.|Op\.|Dt\.|Dr\.)(?:\s*(?:Dr\.|Dt\.))*\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]+)?")


def _brand_claims(workspace, briefs: Dict) -> List[Block]:
    """Brieflerdeki marka olgulari nereden geliyor, ne dogrulanmadi. [Hipotez] etiketi bunlari dogrulamaz."""
    profile = workspace.profile_data if isinstance(workspace.profile_data, dict) else {}
    profile_text = json.dumps(profile, ensure_ascii=False)
    text = json.dumps([briefs.get("ad_ideas"), briefs.get("creative_briefs")], ensure_ascii=False)
    titles = list(dict.fromkeys(_TITLE.findall(text)))
    items = [f"Unvan “{t}”: " + ("markanın sitesinden okunan profilde geçiyor; " if t in profile_text
                                 else "kaynağı bulunamadı; ")
             + "uzmanlık/unvan tescil belgesi görülmeden yayımlanmaz." for t in titles]
    items.append("Hizmet anlatımı ve vaat içeren ifadeler (yaklaşım, seans biçimi, uygulanan tedavi, kullanılan "
                 "malzeme) müşteriyle tek tek teyit edilmeden yayımlanmaz.")
    return [("h2", "Yayın öncesi teyit edilecek marka ifadeleri"),
            ("p", "Brieflerdeki marka adı, unvan ve hizmet anlatımı markanın kendi sitesinden okunan profilden gelir; "
                  "markanın kendi beyanıdır ve bağımsız olarak doğrulanmamıştır. [Hipotez] etiketi bu olguları "
                  "doğrulamaz; yalnız önerinin test edilecek bir fikir olduğunu söyler."),
            ("bullets", items)]


def _date(value) -> str:
    return value.strftime("%d.%m.%Y") if value else "-"


def _refs(item: Dict) -> str:
    """Dayanak numaralari. Kanitsiz ifade uyarisi yaninda kalir; sessizce yayimlanmaz."""
    warning = f" [Uyarı, dayanağı olmayan ifade: {'; '.join(item['warnings'])}]" if item.get("warnings") else ""
    return ", ".join(item.get("refs") or []) + warning


def _cover(workspace, audit, report, view: str, record: Dict, state: str) -> List[Block]:
    approval = record.get("approval") or {}
    status = {
        "approved": f"Analist onaylı ({approval.get('approved_by')}, {(approval.get('approved_at') or '')[:10]})",
        "draft": "TASLAK: analist onayı yok",
        "stale": "GÜNCEL DEĞİL: girdiler değişti, strateji yeniden üretilmeli",
    }.get(state, "ÖN RAPOR: strateji taslağı üretilmedi")
    inputs = report.inputs or {}
    measured = (audit.completed_at or audit.created_at) if audit is not None else None
    rows = [
        ["Belge", VIEW_TITLE[view]],
        ["Marka", f"{workspace.name or ''} ({report_doc._host(workspace.company_url)})"],
        ["Site denetimi tarihi", _date(measured) if audit is not None else "Bağlı denetim yok"],
        ["Araştırma tarihi", _date(report.created_at)],
        ["Kapsam", f"{len(audit.targets) if audit is not None else 0} site; sektör: {inputs.get('sector', '-')}; "
                   f"coğrafya: {inputs.get('geography', '-')}"],
        ["Strateji sürümü", f"v{record['version']}" if record.get("version") else "-"],
        ["Durum", status],
    ]
    return [("title", f"{VIEW_TITLE[view]}: {workspace.name or report_doc._host(workspace.company_url)}"),
            ("p", "[Logo alanı: onaylı logo ve renk paleti tanımlandığında buraya eklenecek]"),
            ("table", ["Künye", ""], rows), ("p", report_doc.LEGEND)]


def _appendix(record: Dict, used: List[str]) -> List[Block]:
    wanted = set(used)
    rows = [[c["id"], c["kind"], c["text"]] for c in record.get("catalogue") or [] if c["id"] in wanted]
    return [("h1", "Dayanaklar"), ("p", "Metindeki B numaraları bu satırları gösterir."),
            ("table", ["No", "Tür", "Bulgu"], rows)] if rows else []


def _used(items) -> List[str]:
    return [ref for item in items for ref in item.get("refs") or []]


def _scores(audit) -> List[Block]:
    own = next((t for t in audit.targets if t.kind == "own"), None) if audit is not None else None
    speed = next((f for f in (own.findings if own else None) or [] if f["check"] == "site.speed_mobile"), None)
    scores = ((speed or {}).get("value") or {}).get("scores") or {}
    shown = [f"{label}: {scores[key]}/100" for key, label in report_doc._SCORE_ROWS if scores.get(key) is not None]
    blocks: List[Block] = [("h2", "Eldeki skorlar")]
    if shown:
        blocks += [("bullets", shown),
                   ("p", f"Kaynak: Lighthouse laboratuvar ölçümü, marka sitesinin ana sayfası, "
                         f"{_date(audit.completed_at or audit.created_at)}. Ölçümden ölçüme birkaç puan değişir.")]
    blocks.append(("p", "GEO, sosyal medya ve dönüşüm için sayısal skor üretilmedi: bu alanlarda ölçüm verisi yok."))
    return blocks


def _context(audit) -> List[Block]:
    blocks: List[Block] = [("h1", "Marka bağlamı ve ekosistem")]
    answers = (audit.answers or {}) if audit is not None else {}
    rows = []
    for q in QUESTIONS:
        a = answers.get(q["key"])
        if a:
            kind = "Analist doğrulaması" if a.get("evidence") else ("Analist brifi" if q["group"] == "brief" else "Müşteri beyanı")
            rows.append([q["label"], " — ".join(filter(None, [a.get("value"), a.get("note")])), kind])
    blocks.append(("table", ["Bilgi", "Değer", "Kaynak türü"], rows) if rows
                  else ("p", "Analist brifi ve müşteri cevapları girilmedi; iş modeli ve ana dönüşüm bilinmiyor."))
    own = next((t for t in audit.targets if t.kind == "own"), None) if audit is not None else None
    social = next((f for f in (own.findings if own else None) or [] if f["check"] == "contact.social_links"), None)
    accounts = ((social or {}).get("value") or {}).get("accounts") or {}
    if accounts:
        blocks += [("h2", "Sitede bağlantısı verilen kanallar [Gözlem]"),
                   ("bullets", [f"{report_doc._SOCIAL.get(n, n)}: {u}" for n, u in accounts.items()]),
                   ("p", "Hesapların markaya ait olduğu doğrulanmadı; takipçi ve etkileşim sayıları ölçülmedi.")]
    return blocks


def _regulation_box(audit, report) -> List[Block]:
    """'Marka icin ne anlama geliyor' kutusu. Markanin mevcut faaliyeti bilinmiyorsa bu ACIKCA yazilir."""
    restrictions = ((report.sections or {}).get("sector") or {}).get("restrictions") or []
    if not restrictions:
        return []
    confirmed = sum(1 for r in restrictions if r.get("verification"))
    channels = (((audit.answers or {}) if audit is not None else {}).get("ad_channels") or {}).get("value")
    return [("h1", "Mevzuat: marka için ne anlama geliyor"), ("bullets", [
        f"Sektörde {len(restrictions)} reklam/tanıtım kısıtı bulundu; {confirmed} tanesi birincil kaynaktan teyit edildi, "
        f"{len(restrictions) - confirmed} tanesi teyit bekliyor (detaylı analiz, 'Yasal ve reklam kısıtları').",
        (f"Analist brifine göre reklam verilen kanallar: {channels}. Bu kanallardaki yayınların kısıtlarla uyumu ayrıca "
         "incelenmelidir; inceleme bu raporda yapılmadı." if channels else
         "Markanın yayındaki reklam ve içerikleri bilinmiyor; mevcut faaliyetin kısıtlarla uyumu değerlendirilmedi."),
        "Ücretli reklam önerileri uygunluk kararı verilmeden plana alınmaz; karar noktalarında yer alır.",
        "Uyumsuzluk şüphesinde hukuk uzmanına danışılmalıdır. Bu bölüm hukuki görüş değildir."])]


def _plan(actions: List[Dict]) -> List[Block]:
    blocks: List[Block] = [("h1", "90 günlük aksiyon planı")]
    for period in strategy.PERIODS:
        rows = [[f"{strategy.ACTION_KINDS.get(a.get('kind'), 'Tür yok')}: {a['action']}",
                 f"{a['priority']}: {a['priority_reason']}",
                 "; ".join(filter(None, [a["owner_role"], a.get("channel") and
                                         f"yer: {strategy.STRATEGY_CHANNELS.get(a['channel'], a['channel'])}",
                                         a["effort"] and f"iş yükü {a['effort']}",
                                         a["dependency"] and f"bağımlılık: {a['dependency']}"])),
                 "; ".join(filter(None, [a["kpi"] and f"KPI: {a['kpi']}", a["contribution"],
                                         a.get("ad_basis") and f"reklam dayanağı (analist): {a['ad_basis']}"])),
                 _refs(a)]
                for a in sorted((a for a in actions if a["period"] == period), key=lambda a: a["priority"])]
        if rows:
            blocks += [("h2", f"{period} gün"),
                       ("table", ["Aksiyon", "Öncelik ve gerekçe", "Sorumlu rol / iş yükü / bağımlılık",
                                  "KPI ve beklenen katkı", "Dayanak"], rows)]
    if len(blocks) == 1:
        blocks.append(("p", "Dayanağı olan aksiyon üretilmedi."))
    blocks.append(("p", "Sorumlu alanı roldür, kişi değildir; iş yükü tahmindir ve bağlayıcı teslim tarihi içermez. "
                        "Başlangıç değeri bilinmeyen göstergeler için hedef yüzdesi verilmedi."))
    return blocks


def _executive(audit, report, record: Dict, state: str) -> List[Block]:
    if state in ("none", "failed", "stale"):
        note = report_doc.STALE_NOTE if state == "stale" else NO_STRATEGY
        return [("h1", "Yönetici özeti"), ("p", note)] + _scores(audit) + _context(audit)
    c = strategy.content(record)
    blocks: List[Block] = [("h1", "Yönetici özeti"), ("p", (
        "Amaç: markanın dijital varlığını mevcut ölçümlerle değerlendirip öncelikli kararları ve 90 günlük planı "
        "ortaya koymak. Bu belgedeki yorum ve öneriler, künyede tarihi yazan denetim ve araştırma verisine dayanır; "
        "yeni ölçüm yapılmamıştır."))]
    if c["summary"]:
        blocks += [("h2", "Öne çıkan bulgular"),
                   ("table", ["Bulgu", "Ticari anlamı", "Dayanak"], [[s["finding"], s["meaning"], _refs(s)] for s in c["summary"]])]
    blocks += _scores(audit)
    if c["opportunities"]:
        blocks += [("h2", "Fırsatlar [Hipotez]"),
                   ("table", ["Fırsat", "Tür", "Beklenen katkı", "Öncelik gerekçesi", "Dayanak"],
                    [[o["text"], o["type"], o["contribution"], o["priority_reason"], _refs(o)] for o in c["opportunities"]])]
    first = sorted((a for a in c["actions"] if a["period"] == "0-30"), key=lambda a: a["priority"])[:5]
    blocks += report_doc._bullets("İlk yapılacaklar (0-30 gün)", [f"{a['priority']}: {a['action']}" for a in first])
    # Ozet listesi ile karar tablosu AYNI listeyi kullanir (briefte bekleyen reklam kararlari dahil)
    decisions = c["decisions"] + strategy.brief_ad_decisions(report, record, state)
    blocks += report_doc._bullets("Karar bekleyen konular", [f"K{i}: {d['question']}" for i, d in enumerate(decisions, 1)])

    blocks += _context(audit)

    blocks.append(("h1", "Rekabet özeti"))
    rationale = (((audit.answers or {}) if audit is not None else {}).get("competitor_rationale") or {}).get("value")
    blocks.append(("p", f"Rakiplerin seçilme gerekçesi (analist brifi): {rationale}" if rationale
                   else "Rakiplerin seçilme gerekçesi bilinmiyor (analist brifinde girilmedi)."))
    if c["competition"]:
        blocks.append(("table", ["Karşılaştırılabilir gözlem", "Dayanak"], [[i["text"], _refs(i)] for i in c["competition"]]))
    else:
        blocks.append(("p", "Hem markada hem rakipte ölçülmüş, karşılaştırılabilir gözlem yok."))
    blocks.append(("p", "Yalnız incelenen sayfa örneklemine dayanır. Rakipte okunamayan sayfa ya da kanal eksiklik "
                        "sayılmaz; kapsamlı rakip konumlandırması bu raporun kapsamında değildir."))

    blocks.append(("h1", "SWOT"))
    for key, label in strategy.SWOT.items():
        items = c["swot"].get(key) or []
        blocks.append(("h2", label))
        blocks.append(("bullets", [f"{i['text']} ({_refs(i)})" for i in items]) if items
                      else ("p", "Yeterli kanıt yok; madde üretilmedi."))

    blocks += _regulation_box(audit, report)

    blocks.append(("h1", "Karar noktaları"))
    # Uygulama belgesinde bekleyen her sponsorlu reklam fikrinin burada bir karsiligi olur
    if decisions:
        blocks.append(("table", ["No", "Karar sorusu", "Seçenekler", "Beklenen bilgi", "Karar sahibi (rol)", "Dayanak"],
                       [[f"K{i}", d["question"], "; ".join(d.get("options") or []), d["needed_info"], d["owner_role"], _refs(d)]
                        for i, d in enumerate(decisions, 1)]))
        ads = [d for d in decisions if d.get("held_action") or d.get("derived")]
        if ads:
            organic = sum(1 for a in c["actions"] if a.get("publication") != "sponsorlu")
            blocks += [("h2", "Ücretli reklam: iki senaryo"), ("bullets", [
                "Uygunluk onaylanırsa (izinli senaryo): bekleyen reklam önerileri test olarak başlatılır; ölçüm planı "
                "uygulama belgesindedir. Onay, analistin kaydettiği dayanakla verilir.",
                f"Onaylanmazsa ya da karar verilene kadar (izinsiz senaryo): ücretli yayın yapılmaz; çalışma 90 günlük "
                f"plandaki {organic} organik ve teknik aksiyonla yürür."])]
    else:
        blocks.append(("p", "Dayanağı olan karar noktası üretilmedi."))
    blocks.append(("p", "Seçenekler öneridir; kararı analist ya da müşteri verir. Kısıt bulunan ya da uygunluğu "
                        "değerlendirilmemiş ücretli reklam önerileri plana alınmaz, burada karar olarak yer alır."))

    blocks += _plan(c["actions"])
    blocks += [("h1", "Açık eksikler ve veri ihtiyaçları"), ("bullets", strategy.live_gaps(report, record))]
    used = _used(c["summary"] + c["opportunities"] + c["competition"] + c["decisions"] + c["actions"]
                 + [i for items in c["swot"].values() for i in items])
    return blocks + _appendix(record, used)


def _ad_readiness(audit, report) -> List[Block]:
    """Reklam hazirligi: hesap erisimi yokken eldeki gozlemlerin TEK baslikta toplanmasi. Performans degildir."""
    own = next((t for t in audit.targets if t.kind == "own"), None) if audit is not None else None
    found = {f["check"]: f for f in (own.findings if own else None) or []}
    rows = [[label, report_doc._cell(found.get(check), check, own)] for check, label in (
        ("tracking.gtm", "Etiket yöneticisi"), ("tracking.ga4", "Ziyaret ölçümü (GA4)"),
        ("tracking.google_ads", "Google Ads etiketi"), ("tracking.meta_pixel", "Meta Pixel"),
        ("site.cta", "Açılış sayfasında iletişim yolu"), ("contact.forms", "Form"),
        ("site.speed_mobile", "Mobil açılış hızı")) if own is not None]
    status = strategy.ad_base_status(report.sections or {})
    rows += [["Dönüşüm olayları", "Bilinmiyor: hesap ya da Tag Assistant kanıtı yok"],
             ["Reklam performansı (harcama, sonuç, maliyet)", "Hesap erişimi yok; değerlendirilmedi"],
             ["Ücretli reklamın uygunluk durumu", f"{status}: {_AD_CHECK[status]}"]]
    return [("h1", "Reklam hazırlığı [Gözlem]"), ("table", ["Konu", "Durum"], rows),
            ("p", "Etiket satırları yalnız sayfa kodunda görüleni gösterir; etiketin çalıştığını ve verinin hesaba "
                  "kaydedildiğini kanıtlamaz. Bu tablo reklam performansı değildir.")]


def _detail(workspace, audit, report, record: Dict, state: str) -> List[Block]:
    blocks: List[Block] = []
    if audit is not None:
        blocks += report_doc._audit_blocks(audit) + report_doc._visibility_blocks(audit)
    blocks += _ad_readiness(audit, report)
    blocks += report_doc._research_blocks(report, briefs=False, brand=tuple(report_doc.brand_tokens(workspace)))
    if state not in ("draft", "approved"):
        note = report_doc.STALE_NOTE if state == "stale" else NO_STRATEGY
        return [("h1", "Bölüm yorumları"), ("p", note)] + blocks
    current = strategy.content(record)
    shop = report_doc.is_ecommerce(audit)
    notes = [n for n in current["section_notes"] if n["section"] != "ecommerce" or shop]
    # Yorumu olmayan bolum sessizce gecilmez: nedeniyle birlikte yerine yazilir (bu not yorum sartini karsilamaz)
    missing = current.get("note_gaps")
    if missing is None:  # bu alan gelmeden once uretilmis kayit
        missing = strategy.note_gaps(notes, record.get("catalogue") or [])
    missing = [g for g in missing if g["section"] != "ecommerce" or shop]
    leftovers = []
    for key, block in [(n["section"], _note(n)) for n in notes] + [
            (g["section"], ("p", f"Bölüm yorumu yok ({strategy.SECTIONS[g['section']]}): {g['reason']}.")) for g in missing]:
        level, prefix = _ANCHORS.get(key, (None, None))
        matches = [i for i, b in enumerate(blocks) if b[0] == level and b[1].startswith(prefix)] if level else []
        if not matches:
            leftovers.append(block)
            continue
        start = matches[-1]  # ayni baslik birden cok kez gecerse (itibar aramalari) sonuncunun sonuna
        stops = ("h1",) if level == "h1" else ("h1", "h2")
        end = next((i for i in range(start + 1, len(blocks)) if blocks[i][0] in stops), len(blocks))
        blocks[end:end] = [block]
    if leftovers:
        blocks += [("h1", "Diğer bölüm yorumları"), *leftovers]
    return blocks + _appendix(record, _used(notes))


def _note(note: Dict) -> Block:
    return ("table", ["Bölüm yorumu [Hipotez]", strategy.SECTIONS[note["section"]]],
            [["Bulgu", note["finding"]], ["Anlam / etki", note["impact"]], ["Aksiyon", note["action"]],
             ["Dayanak", _refs(note)]])


def _implementation(workspace, report, record: Dict, state: str) -> List[Block]:
    sections = report.sections or {}
    briefs = report.briefs or {}
    blocks: List[Block] = [("h1", "Reklam ve kreatif briefler [Hipotez]"), ("p", (
        "Bu belgede üretilmiş içerik ya da gerçek kampanya performansı yoktur. Briefler, test edilecek önerilerdir; "
        "görsel ya da video üretilmemiştir."))]
    if research.synthesis_stale(report) or research.briefs_stale(report):
        return blocks + [("p", report_doc.STALE_NOTE)]
    if briefs.get("status") != "ok":
        return blocks + [("p", "Seçilmiş segmentler için brief üretilmedi.")]
    base = strategy.ad_base_status(sections)
    # Analistin dayanak yazarak plana aldigi ucretli kanallar
    current = strategy.content(record) if state in ("draft", "approved") else {}
    cleared = {CHANNELS.get(a["channel"]) for a in current.get("actions") or [] if a.get("publication") == "sponsorlu"}
    decided = (any(d.get("held_action") for d in current.get("decisions") or [])
               or bool(strategy.brief_ad_decisions(report, record, state)))
    selection = briefs.get("selection") or {}
    segments = (sections.get("synthesis") or {}).get("segments") or []
    blocks += report_doc._bullets("Seçilen segmentler", [segments[i]["name"] for i in selection.get("segments", []) if i < len(segments)])
    restrictions = [r["text"] for r in (sections.get("sector") or {}).get("restrictions") or []]
    if restrictions:
        blocks += report_doc._bullets("Bilinen reklam/tanıtım kısıtları", restrictions)
    blocks += _brand_claims(workspace, briefs)

    def status_of(channel: str) -> str:
        return "önerilebilir" if base == "önerilebilir" or channel in cleared else base

    def idea_block(idea: Dict) -> List[Block]:
        status = status_of(idea["channel"])
        return [("h2", f"Reklam fikri: {idea['segment']} · {idea['channel']}"), ("bullets", [
            "Yayın türü: sponsorlu", f"Öneri durumu: {status}", f"Uyum kontrolü: {_AD_CHECK[status]}",
            f"İçgörü: {idea['insight']}", f"Teklif: {idea['offer']}", f"Mesaj: {idea['message']}", f"Çağrı: {idea['cta']}",
            f"Ölçüm planı (test hipotezi): {idea['test_hypothesis']}",
            *[f"Uyarı, kanıtı olmayan iddia: {w}" for w in idea["warnings"]]])]

    ready = [i for i in briefs["ad_ideas"] if status_of(i["channel"]) == "önerilebilir"]
    held = [i for i in briefs["ad_ideas"] if status_of(i["channel"]) != "önerilebilir"]
    if ready:
        blocks.append(("h1", "Uygulanabilir reklam fikirleri"))
        for idea in ready:
            blocks += idea_block(idea)
    if held:
        blocks += [("h1", "Karar bekleyen reklam fikirleri (yayına hazır değil)"),
                   ("p", "Aşağıdaki fikirler ücretli yayın gerektirir ve uygunlukları onaylanmamıştır. Analist ya da "
                         "müşteri kararı verilmeden uygulanmaz. " + (
                             "İlgili karar yönetici raporunun karar noktalarındadır." if decided else
                             "Güncel bir strateji taslağı olmadığı için yönetici raporunda bu fikirlere ait karar "
                             "noktası yoktur; uygulanmak istenirse önce bu karar verilmelidir."))]
        for idea in held:
            blocks += idea_block(idea)
    if briefs["creative_briefs"]:
        blocks.append(("h1", "Kreatif briefler"))
    for brief in briefs["creative_briefs"]:
        status = status_of(brief["channel"])
        blocks += [("h2", f"Kreatif brief: {brief['segment']} · {brief['channel']}"), ("bullets", [
            "Yayın türü: belirtilmedi (organik yayımlanabilir; sponsorlu kullanım aşağıdaki duruma tabidir)",
            f"Sponsorlu kullanım için öneri durumu: {status}", f"Uyum kontrolü: {_AD_CHECK[status]}",
            f"Biçim: {brief['format']}; süre: {brief['duration']}", f"Açılış: {brief['hook']}",
            f"Görsel yön: {brief['visual_direction']}", f"Çekim listesi: {'; '.join(brief['shot_list'])}",
            f"Metin: {brief['copy']}", f"Gereken gerçek kanıt: {'; '.join(brief['proof_needed'])}",
            f"Ölçüm planı (A/B varyantı): {brief['ab_variant']}",
            *[f"Uyarı, kanıtı olmayan iddia: {w}" for w in brief["warnings"]]])]
    return blocks


def _without_ecommerce(record: Dict) -> Dict:
    """Magaza olmayan sitede, YALNIZ e-ticaret satirina dayanan maddeler belgelere girmez
    (bu kural gelmeden once uretilmis stratejiler icin; kayit degismez, yalniz gorunum)."""
    shop_rows = {c["id"] for c in record.get("catalogue") or [] if str(c.get("check") or "").startswith("ecommerce.")
                 or "E-ticaret altyapısı" in c["text"]}

    def keep(items):
        return [i for i in items or [] if not i.get("refs") or not set(i["refs"]) <= shop_rows]

    def prune(content):
        if not content:
            return content
        return {**content, **{k: keep(content.get(k)) for k in strategy.LISTS},
                "section_notes": [n for n in keep(content.get("section_notes")) if n["section"] != "ecommerce"],
                "swot": {k: keep(v) for k, v in (content.get("swot") or {}).items()}}

    return {**record, "draft": prune(record.get("draft")), "edited": prune(record.get("edited"))}


def build(workspace, audit, report, view: str) -> List[Block]:
    record = (report.sections or {}).get("strategy") or {}
    if record.get("status") == "ok" and not report_doc.is_ecommerce(audit):
        record = _without_ecommerce(record)
    state = strategy.state(report)
    body = {"executive": lambda: _executive(audit, report, record, state),
            "detail": lambda: _detail(workspace, audit, report, record, state),
            "implementation": lambda: _implementation(workspace, report, record, state)}[view]()
    return _cover(workspace, audit, report, view, record, state) + body + report_doc.LIMIT_BLOCKS
