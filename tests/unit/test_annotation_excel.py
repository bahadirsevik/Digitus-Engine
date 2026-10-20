"""K-150 Excel formu gidiş-dönüş sözleşmesi testleri.

Sözleşmeler:
- build: gerçek json/md'den üretim; kısmi etiketler forma taşınır
  (idempotent yeniden üretim).
- import: fail-closed — geçersiz değer, oynanmış kelime, eksik satır
  HİÇBİR yazım yapmadan reddedilir; kabul edilen aktarım md'yi gerçek
  parser'dan geçen biçimde yazar ve json ile kimlik uyumu bozulmaz.
"""
import json
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.core.benchmark.annotation_excel import (
    SHEET_NAME,
    build_workbook,
    import_workbook,
)
from app.core.benchmark.annotation_io import (
    AnnotationError,
    parse_primary_md,
    verify_consistency,
)

BENCH = Path(__file__).resolve().parents[2] / "benchmark"


@pytest.fixture()
def bench_copy(tmp_path):
    for name in ("annotation_sheet.json", "annotation_sheet.md",
                 "annotation_sheet_second.json", "annotation_sheet_second.md"):
        shutil.copy(BENCH / name, tmp_path / name)
    return tmp_path


def _build_primary(bench, out=None):
    out = out or bench / "form.xlsx"
    stats = build_workbook(bench / "annotation_sheet.json",
                           bench / "annotation_sheet.md", out, second=False)
    return out, stats


class TestBuild:
    def test_builds_all_items_with_locked_identity(self, bench_copy):
        out, stats = _build_primary(bench_copy)
        assert stats["items"] == 150
        wb = load_workbook(out)
        assert set(wb.sheetnames) == {"1_TALIMAT", "2_TANIMLAR",
                                      "3_ORNEKLER", SHEET_NAME}
        ws = wb[SHEET_NAME]
        assert ws.max_row == 151
        doc = json.loads((bench_copy / "annotation_sheet.json")
                         .read_text(encoding="utf-8"))
        assert ws.cell(row=2, column=1).value == doc["entries"][0]["item_id"]
        assert ws.cell(row=2, column=3).value == doc["entries"][0]["keyword"]
        # kimlik/kelime kilitli, etiket hücreleri açık; sayfa korumalı
        assert ws.protection.sheet is True
        assert ws.cell(row=2, column=3).protection.locked is True
        assert ws.cell(row=2, column=4).protection.locked is False

    def test_second_form_has_only_double_items_and_no_cift_column(
            self, bench_copy):
        out = bench_copy / "form2.xlsx"
        stats = build_workbook(bench_copy / "annotation_sheet_second.json",
                               bench_copy / "annotation_sheet_second.md",
                               out, second=True)
        assert stats["items"] == 30
        ws = load_workbook(out)[SHEET_NAME]
        assert ws.cell(row=1, column=2).value == "Kelime"

    def test_rebuild_carries_existing_labels(self, bench_copy):
        out, _ = _build_primary(bench_copy)
        ws = load_workbook(out)[SHEET_NAME]
        # sütunlar: 4 intent, 5 GT, 6 GA, 7 ADS(2/1/-1!), 8-10 dims, 11 rel
        for col, v in ((4, "informational"), (5, 0), (6, 0), (7, -1),
                       (8, 0), (9, 0), (10, 0), (11, "mid")):
            ws.cell(row=2, column=col).value = v
        wb = ws.parent
        wb.save(out)
        import_workbook(out, bench_copy / "annotation_sheet.json",
                        bench_copy / "annotation_sheet.md", second=False)
        out2, stats2 = _build_primary(bench_copy, bench_copy / "again.xlsx")
        ws2 = load_workbook(out2)[SHEET_NAME]
        assert ws2.cell(row=2, column=4).value == "informational"
        assert ws2.cell(row=2, column=11).value == "mid"
        # The committed K-150 fixture is now fully adjudicated and frozen.
        # Rebuilding the workbook must preserve every existing label.
        assert stats2["labeled"] == 150


class TestImportFailClosed:
    def test_rejects_invalid_value_without_writing(self, bench_copy):
        out, _ = _build_primary(bench_copy)
        before = (bench_copy / "annotation_sheet.md").read_bytes()
        wb = load_workbook(out)
        wb[SHEET_NAME].cell(row=2, column=4).value = "alaka"
        wb.save(out)
        with pytest.raises(AnnotationError, match="izinli değil"):
            import_workbook(out, bench_copy / "annotation_sheet.json",
                            bench_copy / "annotation_sheet.md", second=False)
        assert (bench_copy / "annotation_sheet.md").read_bytes() == before

    def test_rejects_tampered_keyword(self, bench_copy):
        out, _ = _build_primary(bench_copy)
        wb = load_workbook(out)
        wb[SHEET_NAME].cell(row=2, column=3).value = "bambaska kelime"
        wb.save(out)
        with pytest.raises(AnnotationError, match="kelime değiştirilmiş"):
            import_workbook(out, bench_copy / "annotation_sheet.json",
                            bench_copy / "annotation_sheet.md", second=False)

    def test_rejects_missing_rows(self, bench_copy):
        out, _ = _build_primary(bench_copy)
        wb = load_workbook(out)
        wb[SHEET_NAME].delete_rows(2, 5)
        wb.save(out)
        with pytest.raises(AnnotationError, match="eksik satırlar"):
            import_workbook(out, bench_copy / "annotation_sheet.json",
                            bench_copy / "annotation_sheet.md", second=False)


class TestRoundTrip:
    def test_filled_form_reaches_json_pipeline(self, bench_copy):
        """Excel → md → (gerçek) verify_consistency zinciri uçtan uca."""
        out, _ = _build_primary(bench_copy)
        wb = load_workbook(out)
        ws = wb[SHEET_NAME]
        fill = ["transactional", 1, 1, 2, 0, 0, 0, "high"]
        for r in range(2, ws.max_row + 1):
            for off, v in enumerate(fill):
                ws.cell(row=r, column=4 + off).value = v
        # '?' ve int'in metin hali de kabul edilmeli
        # (satır 2 = K001, satır 3 = K002)
        ws.cell(row=2, column=4).value = "?"
        ws.cell(row=3, column=7).value = "-1"  # K002 ADS, metin olarak
        wb.save(out)

        stats = import_workbook(out, bench_copy / "annotation_sheet.json",
                                bench_copy / "annotation_sheet.md",
                                second=False)
        assert stats["items"] == 150
        assert stats["with_question"] == 1
        assert stats["fully_labeled"] == 149

        md_text = (bench_copy / "annotation_sheet.md").read_text(
            encoding="utf-8")
        schema = json.loads((bench_copy / "annotation_sheet.json")
                            .read_text(encoding="utf-8"))["label_schema"]
        parsed = parse_primary_md(md_text, schema)
        assert parsed["K003"]["labels"]["intent_type"] == "transactional"
        assert parsed["K002"]["labels"]["ads_class"] == -1  # "-1" metni int'e
        assert parsed["K001"]["labels"]["intent_type"] == "?"
        # md<->json kimlik uyumu bozulmadı (etiketler henüz aktarılmadığı
        # için fark BEKLENİR; kimlik/kelime uyuşmazlığı ise OLMAMALI)
        diffs = verify_consistency(bench_copy / "annotation_sheet.md",
                                   bench_copy / "annotation_sheet.json")
        assert not any("kelime" in d or "kimlik" in d for d in diffs)

    def test_cift_markers_preserved_in_md(self, bench_copy):
        out, _ = _build_primary(bench_copy)
        wb = load_workbook(out)
        ws = wb[SHEET_NAME]
        for r in range(2, ws.max_row + 1):
            for off, v in enumerate(["informational", 0, 0, -1, 0, 0, 0,
                                     "low"]):
                ws.cell(row=r, column=4 + off).value = v
        wb.save(out)
        import_workbook(out, bench_copy / "annotation_sheet.json",
                        bench_copy / "annotation_sheet.md", second=False)
        md_text = (bench_copy / "annotation_sheet.md").read_text(
            encoding="utf-8")
        assert "| K005 | ✓ |" in md_text
        assert "| K001 |  |" in md_text
