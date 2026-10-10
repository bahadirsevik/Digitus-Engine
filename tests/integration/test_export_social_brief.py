# -*- coding: utf-8 -*-
"""Sosyal export'un brief sözleşmesi testleri (plan_social_brief_akisi.md §8).

Kapsam:
- Collector: brief bazında gruplama + "Eski (brief'siz)" grubu + ana kelime,
  istenen/gerçek süre, süre durumu, uyarılar, format_payload alanları.
- Her exporter (csv/docx/excel/pdf) yeni alanları/render'ı kırmadan üretiyor.

Eski (brief'siz) davranış `tests/integration/test_export_distribution.py`
içindeki mevcut testlerle ayrıca korunuyor.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.database.models import (
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialIdea,
)


def _build_brief_and_legacy_chain(db_session, run, keyword):
    """Bir brief'li (video, süre + format_payload) + bir legacy içerik kurar."""
    brief = SocialBrief(
        scoring_run_id=run.id,
        brand_name_snapshot="Test Marka",
        locked_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        is_stale=False,
    )
    db_session.add(brief)
    db_session.flush()

    brief_keyword = SocialBriefKeyword(
        brief_id=brief.id,
        keyword_id=keyword.id,
        keyword_snapshot=keyword.keyword,
        position=0,
    )
    db_session.add(brief_keyword)

    target = SocialBriefTarget(
        brief_id=brief.id,
        platform="tiktok",
        content_format="short",
        duration_preset_id="16-30",
        duration_min_sec=16,
        duration_max_sec=30,
    )
    db_session.add(target)
    db_session.flush()

    category = SocialCategory(
        scoring_run_id=run.id, brief_id=brief.id, category_name="Ürün Tanıtımı",
    )
    db_session.add(category)
    db_session.flush()

    idea = SocialIdea(
        category_id=category.id,
        brief_id=brief.id,
        brief_target_id=target.id,
        keyword_id=keyword.id,
        idea_title="Brief Fikri",
        target_platform="tiktok",
        content_format="short",
    )
    db_session.add(idea)
    db_session.flush()

    content = SocialContent(
        idea_id=idea.id,
        brief_id=brief.id,
        caption="Brief içeriği captionu",
        hooks=[{"text": "Brief hook", "style": "question"}],
        hashtags=["briefhash"],
        cta_text="Şimdi izle",
        format_payload={
            "kind": "video",
            "segments": [
                {"start_sec": 0, "end_sec": 10, "scene": "Açılış",
                 "on_screen_text": "Merhaba", "voiceover": "Merhaba diyoruz"},
                {"start_sec": 10, "end_sec": 25, "scene": "Ürün tanıtımı",
                 "on_screen_text": "Ürün burada", "voiceover": "Ürünü gösteriyoruz"},
            ],
        },
        duration_status="mismatch",
        actual_duration_sec=25,
        validation_warnings=["duration_mismatch"],
    )
    db_session.add(content)

    # Legacy (brief_id IS NULL) zincir — plan §8: yeni kurallarla yeniden
    # doğrulanmaz, "Eski (brief'siz)" başlığıyla raporda kalır.
    legacy_category = SocialCategory(scoring_run_id=run.id, category_name="Eğitim")
    db_session.add(legacy_category)
    db_session.flush()

    legacy_idea = SocialIdea(
        category_id=legacy_category.id,
        idea_title="Legacy Fikri",
        target_platform="instagram",
        content_format="post",
    )
    db_session.add(legacy_idea)
    db_session.flush()

    legacy_content = SocialContent(
        idea_id=legacy_idea.id,
        caption="Legacy içerik captionu",
        hooks=[{"text": "Legacy hook", "style": "statement"}],
        hashtags=["legacyhash"],
    )
    db_session.add(legacy_content)
    db_session.commit()

    return brief, content, legacy_content


def test_collect_social_groups_by_brief_and_legacy(
    db_session, make_workspace, make_keyword, make_scoring_run
):
    """Collector brief'i ayrı grupta, legacy'yi "Eski (brief'siz)" grubunda
    döndürür; ana kelime/süre/uyarı/format_payload alanları taşınır."""
    from app.exporters.data_collector import ExportDataCollector
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace(name="Brief Export WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    keyword = make_keyword("brief anahtar kelime", brand_profile_id=ws.id)

    brief, content, legacy_content = _build_brief_and_legacy_chain(db_session, run, keyword)

    report = ExportDataCollector(db_session).collect(run.id, [ExportSectionEnum.SOCIAL])
    social = report.social
    assert social is not None

    groups_by_brief_id = {g.brief_id: g for g in social.brief_groups}
    assert set(groups_by_brief_id.keys()) == {brief.id, None}

    # Legacy grup her zaman en sonda (K6 — rapora eklenmeye devam eder)
    assert social.brief_groups[-1].brief_id is None
    assert social.brief_groups[-1].label == "Eski (brief'siz)"
    assert len(social.brief_groups[-1].contents) == 1
    assert social.brief_groups[-1].contents[0].caption == "Legacy içerik captionu"
    assert social.brief_groups[-1].contents[0].brief_id is None

    brief_group = groups_by_brief_id[brief.id]
    assert brief_group.label.startswith(f"Brief #{brief.id}")
    assert "Test Marka" in brief_group.label
    assert [k.keyword for k in brief_group.keywords] == ["brief anahtar kelime"]

    assert len(brief_group.contents) == 1
    item = brief_group.contents[0]
    assert item.brief_id == brief.id
    assert item.idea_keyword == "brief anahtar kelime"
    assert item.duration_preset_id == "16-30"
    assert item.duration_min_sec == 16
    assert item.duration_max_sec == 30
    assert item.actual_duration_sec == 25
    assert item.duration_status == "mismatch"
    assert item.validation_warnings == ["duration_mismatch"]
    assert item.format_payload["kind"] == "video"
    assert len(item.format_payload["segments"]) == 2

    # Flat `contents` geriye uyumluluk için tüm brief+legacy içerikleri taşır
    assert len(social.contents) == 2


def test_social_csv_export_renders_brief_groups_and_duration(
    db_session, make_workspace, make_keyword, make_scoring_run, tmp_path
):
    import zipfile

    from app.exporters.csv_exporter import CsvExporter
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace(name="Brief CSV WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    keyword = make_keyword("csv brief kelime", brand_profile_id=ws.id)
    _build_brief_and_legacy_chain(db_session, run, keyword)

    path = str(tmp_path / "social.zip")
    CsvExporter(db_session).export(run.id, [ExportSectionEnum.SOCIAL], path)

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "sosyal_briefler.csv" in names
        assert "sosyal_icerikler.csv" in names
        contents_csv = zf.read("sosyal_icerikler.csv").decode("utf-8-sig")

    assert "Eski (brief'siz)" in contents_csv
    assert "csv brief kelime" in contents_csv
    assert "16-30 sn" in contents_csv
    assert "25 sn" in contents_csv
    assert "Tutmadı" in contents_csv
    assert "duration_mismatch" in contents_csv
    assert "Sahne 1" in contents_csv
    assert "Sahne 2" in contents_csv


def test_social_docx_export_renders_brief_groups_and_duration(
    db_session, make_workspace, make_keyword, make_scoring_run, tmp_path
):
    from docx import Document

    from app.exporters.docx_exporter import DocxExporter
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace(name="Brief DOCX WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    keyword = make_keyword("docx brief kelime", brand_profile_id=ws.id)
    brief, _, _ = _build_brief_and_legacy_chain(db_session, run, keyword)

    path = str(tmp_path / "social.docx")
    DocxExporter(db_session).export(run.id, [ExportSectionEnum.SOCIAL], path)
    text = "\n".join(p.text for p in Document(path).paragraphs)

    assert f"Brief #{brief.id}" in text
    assert "Eski (brief'siz)" in text
    assert "docx brief kelime" in text
    assert "16-30 sn" in text
    assert "Tutmadı" in text
    assert "Sahne 1" in text
    assert "Açılış" in text
    assert "duration_mismatch" in text


def test_social_excel_export_renders_brief_groups_and_duration(
    db_session, make_workspace, make_keyword, make_scoring_run, tmp_path
):
    from openpyxl import load_workbook

    from app.exporters.excel_exporter import ExcelExporter
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace(name="Brief XLSX WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    keyword = make_keyword("xlsx brief kelime", brand_profile_id=ws.id)
    brief, _, _ = _build_brief_and_legacy_chain(db_session, run, keyword)

    path = str(tmp_path / "social.xlsx")
    ExcelExporter(db_session).export(run.id, [ExportSectionEnum.SOCIAL], path)
    wb = load_workbook(path)

    assert "Sosyal Briefler" in wb.sheetnames
    brief_rows = [[c.value for c in row] for row in wb["Sosyal Briefler"].iter_rows()]
    labels = [r[0] for r in brief_rows[1:]]
    assert any(lbl and lbl.startswith(f"Brief #{brief.id}") for lbl in labels)
    assert "Eski (brief'siz)" in labels

    content_rows = [[c.value for c in row] for row in wb["Sosyal İçerikler"].iter_rows()]
    header = content_rows[0]
    idx = {name: i for i, name in enumerate(header)}
    body = content_rows[1:]
    brief_row = next(r for r in body if r[idx["Brief"]] and r[idx["Brief"]].startswith(f"Brief #{brief.id}"))
    assert brief_row[idx["Ana Kelime"]] == "xlsx brief kelime"
    assert brief_row[idx["İstenen Süre"]] == "16-30 sn"
    assert brief_row[idx["Gerçek Süre"]] == "25 sn"
    assert brief_row[idx["Süre Durumu"]] == "Tutmadı"
    assert "duration_mismatch" in (brief_row[idx["Uyarılar"]] or "")
    assert "Sahne 1" in (brief_row[idx["Format Detayı"]] or "")

    legacy_row = next(r for r in body if r[idx["Brief"]] == "Eski (brief'siz)")
    assert legacy_row[idx["Fikir"]] == "Legacy Fikri"


def test_social_pdf_export_renders_without_error(
    db_session, make_workspace, make_keyword, make_scoring_run, tmp_path
):
    from app.exporters.pdf_exporter import PdfExporter
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace(name="Brief PDF WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    keyword = make_keyword("pdf brief kelime", brand_profile_id=ws.id)
    _build_brief_and_legacy_chain(db_session, run, keyword)

    path = str(tmp_path / "social.pdf")
    PdfExporter(db_session).export(run.id, [ExportSectionEnum.SOCIAL], path)

    import os

    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


def test_social_export_without_briefs_still_renders_legacy_only(
    db_session, make_workspace, make_scoring_run, tmp_path
):
    """Brief hiç yoksa (tamamen eski veri) rapor kırılmadan tek grup üretir."""
    from openpyxl import load_workbook

    from app.exporters.excel_exporter import ExcelExporter
    from app.schemas.export import ExportSectionEnum

    ws = make_workspace(name="Legacy Only WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")

    category = SocialCategory(scoring_run_id=run.id, category_name="Eğitim")
    db_session.add(category)
    db_session.flush()
    idea = SocialIdea(
        category_id=category.id, idea_title="Eski Fikir",
        target_platform="instagram", content_format="post",
    )
    db_session.add(idea)
    db_session.flush()
    db_session.add(SocialContent(idea_id=idea.id, caption="Eski caption"))
    db_session.commit()

    path = str(tmp_path / "legacy_only.xlsx")
    ExcelExporter(db_session).export(run.id, [ExportSectionEnum.SOCIAL], path)
    wb = load_workbook(path)
    rows = [[c.value for c in row] for row in wb["Sosyal İçerikler"].iter_rows()]
    assert rows[1][0] == "Eski (brief'siz)"
    assert rows[1][1] == "Eski Fikir"
