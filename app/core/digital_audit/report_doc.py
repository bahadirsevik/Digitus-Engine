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
_STATE = {"observed": "Gözlendi", "not_observed": "Gözlenmedi", "unknown": "Bilinmiyor",
          "blocked": "Engellendi", "error": "Hata"}
_ASSESSMENT = {"ok": "iyi", "warning": "uyarı", "problem": "sorun"}
_SECTION = {"site": "Web sitesi", "seo": "SEO", "tracking": "Ölçümleme", "contact": "İletişim kanalları",
            "ecommerce": "E-ticaret", "speed": "Hız"}
LEGEND = ("[Gözlem] otomatik ölçüm · [Beyan] müşteri cevabı · [Kaynak] kaynakta doğrulanan bilgi · "
          "[Hipotez] bulgulardan türetilen, doğrulanması gereken yorum")
STALE_NOTE = ("Rapora alınan rakamlar bu bölüm üretildikten sonra değişti; bölüm güncel olmadığı için "
              "rapora alınmadı. Yeniden üretilmesi gerekir.")


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").strip("/")


def _audit_blocks(audit) -> List[Block]:
    blocks: List[Block] = [("h1", "Site denetimi [Gözlem]")]
    targets = list(audit.targets)
    blocks.append(("p", "Denetlenen siteler: " + ", ".join(
        f"{_host(t.url)} ({'marka' if t.kind == 'own' else 'rakip'})" for t in targets)))
    order: Dict[str, List[str]] = {}
    labels: Dict[str, str] = {}
    cells: Dict[Tuple[int, str], str] = {}
    for index, target in enumerate(targets):
        for f in target.findings or []:
            order.setdefault(f["section"], [])
            if f["check"] not in order[f["section"]]:
                order[f["section"]].append(f["check"])
            labels[f["check"]] = f["label"]
            assessment = _ASSESSMENT.get(f.get("assessment") or "")
            cells[(index, f["check"])] = _STATE.get(f["state"], f["state"]) + (f" ({assessment})" if assessment else "")
    for section, checks in order.items():
        blocks.append(("h2", _SECTION.get(section, section)))
        blocks.append(("table", ["Kontrol"] + [_host(t.url) for t in targets],
                       [[labels[c]] + [cells.get((i, c), "Ölçülmedi") for i in range(len(targets))] for c in checks]))
    answers = audit.answers or {}
    declared = [f"{q['label']} {answers[q['key']].get('value') or ''} "
                f"{('— ' + answers[q['key']]['note']) if answers[q['key']].get('note') else ''}".strip()
                for q in QUESTIONS if q["key"] in answers]
    if declared:
        blocks += [("h2", "Müşteri beyanları [Beyan]"), ("bullets", declared)]
    return blocks


def _bullets(title: str, items: List[str]) -> List[Block]:
    return [("h2", title), ("bullets", items)] if items else []


def _texts(items, field: str = "text") -> List[str]:
    return [i[field] for i in research.usable(items)]


def _research_blocks(report) -> List[Block]:
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

    briefs = report.briefs or {}
    if research.briefs_stale(report):
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


def build_blocks(workspace, audit=None, report=None) -> List[Block]:
    stamp = (report or audit).created_at
    blocks: List[Block] = [
        ("title", f"Dijital Varlık Analizi: {workspace.name or _host(workspace.company_url)}"),
        ("p", f"Rapor verisinin toplandığı tarih: {stamp.strftime('%d.%m.%Y') if stamp else '-'}"),
        ("p", LEGEND),
    ]
    if audit is not None:
        blocks += _audit_blocks(audit)
    if report is not None:
        blocks += _research_blocks(report)
    blocks += [("h1", "Sınırlar"), ("bullets", [
        "Bot engelli ya da JavaScript ile çizilen sitelerde kontroller “Bilinmiyor” döner; bu, özelliğin olmadığı anlamına gelmez.",
        "Rakip trafiği, backlink ve hesap içi veriler (GA4, reklam hesabı) bu raporda yoktur.",
        "Rakamlar yalnız kaynak metninde bulunduysa rapora alınır; bağlamın doğruluğunu analist kontrol eder.",
        "Segmentler, öneriler ve briefler hipotezdir; ölçülmüş demografi ya da sonuç garantisi değildir.",
    ])]
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
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                      topMargin=1.5 * cm, bottomMargin=1.5 * cm).build(story)
    return buffer.getvalue()


RENDERERS = {
    "docx": (render_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": (render_pdf, "application/pdf"),
}


def render(workspace, audit, report, fmt: str) -> Tuple[bytes, str]:
    renderer, media_type = RENDERERS[fmt]
    return renderer(build_blocks(workspace, audit, report)), media_type


def filename(workspace, fmt: str, stamp: Optional[str] = None) -> str:
    return f"dijital-varlik-analizi-{workspace.name or 'rapor'}{('-' + stamp) if stamp else ''}.{fmt}"
