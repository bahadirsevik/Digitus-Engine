"""Kelime bazlı reklam görünümü (Excel sheet + CSV) — DB'siz birim testi."""
import csv
import os

from openpyxl import Workbook

from app.exporters.csv_exporter import CsvExporter
from app.exporters.excel_exporter import ExcelExporter
from app.schemas.export import AdGroupData, AdsData, DescriptionData, HeadlineData

HEADER = ['Kelime', 'Reklam Grubu', 'Açıklama 1', 'Açıklama 2',
          'Açıklama 3', 'Açıklama 4', 'Başlıklar']


def _ads() -> AdsData:
    a = AdGroupData(
        id=1, group_name='Grup A',
        target_keywords=['a1', 'a2', 'a3', 'ortak', 'a5'],
        headlines=[HeadlineData(headline_text='A-h1'), HeadlineData(headline_text='A-h2')],
        descriptions=[DescriptionData(description_text=f'A-desc-{i}') for i in range(1, 5)],
    )
    b = AdGroupData(
        id=2, group_name='Grup B',
        target_keywords=['ortak', 'b2'],
        headlines=[HeadlineData(headline_text='B-h1')],
        descriptions=[DescriptionData(description_text=f'B-desc-{i}') for i in range(1, 3)],
    )
    return AdsData(ad_groups=[a, b], total=2)


def _check_rows(rows):
    assert len(rows) == 7
    by_kw = {}
    for r in rows:
        by_kw.setdefault(r[0], []).append(r)
    a5 = by_kw['a5'][0]
    assert a5[1] == 'Grup A' and a5[5] == 'A-desc-4'
    ortak = {r[1]: r for r in by_kw['ortak']}
    assert len(by_kw['ortak']) == 2
    assert ortak['Grup A'][2:6] == [f'A-desc-{i}' for i in range(1, 5)]
    assert ortak['Grup A'][6] == 'A-h1 | A-h2'
    b = ortak['Grup B']
    assert b[2:4] == ['B-desc-1', 'B-desc-2']
    assert not b[4] and not b[5]
    assert b[6] == 'B-h1'


def test_excel_keyword_sheet():
    wb = Workbook()
    ExcelExporter(None)._add_ads_sheets(wb, _ads())
    assert wb.sheetnames.index('Kelime Bazlı Reklamlar') < wb.sheetnames.index('Reklam Metinleri')
    ws = wb['Kelime Bazlı Reklamlar']
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    assert rows[0] == HEADER
    _check_rows(rows[1:])
    assert ws.freeze_panes == 'A2'
    assert ws.auto_filter.ref


def test_csv_keyword_file_and_no_truncation(tmp_path):
    files = CsvExporter(None)._write_ads_csv(str(tmp_path), _ads())
    assert any(os.path.basename(f) == 'kelime_bazli_reklamlar.csv' for f in files)

    def read(name):
        with open(tmp_path / name, encoding='utf-8-sig', newline='') as f:
            return list(csv.reader(f))

    rows = read('kelime_bazli_reklamlar.csv')
    assert rows[0] == HEADER
    _check_rows(rows[1:])

    groups = read('reklam_gruplari.csv')
    assert 'a5' in groups[1][1]
