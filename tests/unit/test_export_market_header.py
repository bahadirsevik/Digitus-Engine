# -*- coding: utf-8 -*-
"""Faz 1-8 (backend): export basliginda hedef konum / cikti dili / hacim kaynagi.

Collector alanlari, CSV (ozet satirlari + sondaki 3 kolon; ilk satir hep baslik),
DOCX/XLSX geri okuma, PDF kapak paragrafi. Ucretli cagri yok.
"""
import csv
import io
import os
import zipfile

import pytest
from docx import Document
from openpyxl import load_workbook

from app.database.models import AdDescription, AdGenerationSet, AdGroup, AdHeadline
from app.exporters.base_exporter import market_header_line
from app.exporters.csv_exporter import CsvExporter
from app.exporters.data_collector import ExportDataCollector
from app.exporters.docx_exporter import DocxExporter
from app.exporters.excel_exporter import ExcelExporter
from app.exporters.pdf_exporter import PdfExporter
from app.schemas.export import ExportSectionEnum

ADS = [ExportSectionEnum.ADS]
SUMMARY = [ExportSectionEnum.SUMMARY]
LINE = "Hedef konum: Almanya (DE) — Ülke · Çıktı dili: Rusça · Hacim kaynağı: Google Ads"
TAIL = ["Almanya (DE) — Ülke", "Rusça", "Google Ads"]


def _run(db_session, make_workspace, make_scoring_run, **ws_kwargs):
    ws = make_workspace(name="Pazar Baslik WS", **ws_kwargs)
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    gen_set = AdGenerationSet(scoring_run_id=run.id, version_number=1, status="active")
    db_session.add(gen_set)
    db_session.flush()
    group = AdGroup(
        scoring_run_id=run.id, generation_set_id=gen_set.id, group_name="Grup A",
        target_keyword_ids=[1], target_keywords=["kaufen"],
    )
    db_session.add(group)
    db_session.flush()
    db_session.add(AdHeadline(ad_group_id=group.id, headline_text="Jetzt kaufen",
                              headline_type="benefit", sort_order=0))
    db_session.add(AdDescription(ad_group_id=group.id, sort_order=0,
                                 description_text="Schnelle Lieferung.", description_type="value_prop"))
    db_session.commit()
    return run


@pytest.fixture
def de_run(db_session, make_workspace, make_scoring_run):
    return _run(db_session, make_workspace, make_scoring_run,
                default_geo_target_id="2276", default_language_id="1031")


def test_collector_fields_on_summary_and_brand_profile(db_session, de_run):
    report = ExportDataCollector(db_session).collect(de_run.id, [ExportSectionEnum.ALL])

    for part in (report.summary, report.brand_profile):
        assert part.market_label == "Almanya (DE) · Rusça"
        assert part.target_location == "Almanya (DE) — Ülke"
        assert part.output_language == "Rusça"
        assert part.volume_source == "Google Ads"
    # brand_profile bolumu istenmese de ozet (dolayisiyla baslik) her export'ta var
    assert ExportDataCollector(db_session).collect(de_run.id, ADS).summary.target_location == "Almanya (DE) — Ülke"


def test_collector_null_and_unknown_market(db_session, make_workspace, make_scoring_run):
    null_run = _run(db_session, make_workspace, make_scoring_run,
                    default_geo_target_id=None, default_language_id=None)
    s = ExportDataCollector(db_session).collect(null_run.id, ADS).summary
    assert (s.target_location, s.output_language) == ("Seçilmedi", "Seçilmedi")

    odd_run = _run(db_session, make_workspace, make_scoring_run,
                   default_geo_target_id="999999999", default_language_id="1234")
    s = ExportDataCollector(db_session).collect(odd_run.id, ADS).summary
    assert s.target_location == "Tanımlanamayan mevcut seçim (999999999)"
    assert s.output_language == "Tanımlanamayan mevcut seçim (1234)"


def test_csv_summary_rows_and_trailing_columns_keep_header_first(db_session, de_run, tmp_path):
    path = str(tmp_path / "x.zip")
    CsvExporter(db_session).export(
        de_run.id, [ExportSectionEnum.SUMMARY, ExportSectionEnum.SCORING,
                    ExportSectionEnum.CHANNELS, ExportSectionEnum.ADS], path)

    def read(name):
        with zipfile.ZipFile(path) as zf:
            return list(csv.reader(io.StringIO(zf.read(name).decode("utf-8-sig"))))

    summary = read("ozet.csv")
    assert summary[0] == ["Metrik", "Değer"]
    assert ["Hedef Konum", "Almanya (DE) — Ülke"] in summary
    assert ["Çıktı Dili", "Rusça"] in summary
    assert ["Hacim Kaynağı", "Google Ads"] in summary
    assert all(len(r) == 2 for r in summary)  # ozet kolon sayisi degismedi

    market_headers = ["Hedef Konum", "Çıktı Dili", "Hacim Kaynağı"]
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n != "ozet.csv"]
    assert "reklam_gruplari.csv" in names and "tum_kelimeler.csv" in names and "ads_havuzu.csv" in names
    for name in names:
        rows = read(name)
        assert rows[0][-3:] == market_headers, name          # aciklama satiri yok: ilk satir baslik
        assert rows[0][0] in {"Kelime", "Sıra", "Grup Adı", "Grup", "Brief", "Başlık"}, name
        for row in rows[1:]:
            assert row[-3:] == TAIL, name
    assert len(read("reklam_gruplari.csv")) == 2


def test_csv_without_summary_context_has_no_extra_columns(tmp_path):
    # export() disinda dogrudan cagri (mevcut birim testleri): kolon eklenmez
    exporter = CsvExporter(None)
    f = exporter._write_csv(str(tmp_path / "a.csv"), ["A", "B"], [[1, 2]])
    with open(f, encoding="utf-8-sig", newline="") as fh:
        assert list(csv.reader(fh)) == [["A", "B"], ["1", "2"]]


def test_docx_cover_has_market_line(db_session, de_run, tmp_path):
    path = str(tmp_path / "x.docx")
    DocxExporter(db_session).export(de_run.id, ADS, path)

    assert LINE in "\n".join(p.text for p in Document(path).paragraphs)


def test_xlsx_summary_sheet_has_market_rows(db_session, de_run, tmp_path):
    path = str(tmp_path / "x.xlsx")
    ExcelExporter(db_session).export(de_run.id, SUMMARY, path)

    rows = [list(r) for r in load_workbook(path)["Özet"].iter_rows(values_only=True)]
    assert rows[2] == ["Metrik", "Değer"]  # baslik satiri yerinde
    assert ["Hedef Konum", "Almanya (DE) — Ülke"] in rows
    assert ["Çıktı Dili", "Rusça"] in rows
    assert ["Hacim Kaynağı", "Google Ads"] in rows


def test_pdf_cover_paragraph_and_full_export(db_session, de_run, tmp_path):
    exporter = PdfExporter(db_session)
    report = ExportDataCollector(db_session).collect(de_run.id, ADS)

    texts = [e.getPlainText() for e in exporter._create_cover(report) if hasattr(e, "getPlainText")]
    assert LINE in texts

    path = str(tmp_path / "x.pdf")
    exporter.export(de_run.id, ADS, path)
    assert os.path.getsize(path) > 0  # PDF icerigi PDF'ten geri okunmadi (yalniz kapak paragraf metni)


def test_header_line_none_summary():
    assert market_header_line(None) == ""
