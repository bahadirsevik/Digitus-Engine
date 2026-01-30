"""
Excel exporter module.
"""
from typing import Dict, Any
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import tempfile


def export_to_excel(
    scoring_run_id: int,
    run_name: str,
    pools_data: Dict[str, list],
    include_scores: bool = True
) -> str:
    """
    Kanal havuzlarını Excel formatında dışa aktarır.
    
    Returns:
        Oluşturulan dosyanın yolu
    """
    wb = Workbook()
    
    # Styles
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    strategic_fill = PatternFill(start_color="FFD700", end_color="FFD700", fill_type="solid")
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Summary sheet
    ws_summary = wb.active
    ws_summary.title = "Özet"
    
    ws_summary.append(["DIGITUS ENGINE - Rapor"])
    ws_summary.merge_cells('A1:D1')
    ws_summary['A1'].font = Font(bold=True, size=16)
    
    ws_summary.append([])
    ws_summary.append(["Scoring Run ID:", scoring_run_id])
    ws_summary.append(["Çalıştırma Adı:", run_name or "N/A"])
    ws_summary.append([])
    ws_summary.append(["Kanal", "Kelime Sayısı", "Stratejik Sayısı"])
    
    for channel, keywords in pools_data.items():
        strategic_count = sum(1 for kw in keywords if kw.get('is_strategic'))
        ws_summary.append([channel, len(keywords), strategic_count])
    
    # Channel sheets
    for channel, keywords in pools_data.items():
        ws = wb.create_sheet(title=channel)
        
        # Headers
        headers = ["Sıra", "Anahtar Kelime", "Aylık Hacim", "Sektör", "Skor", "Stratejik"]
        ws.append(headers)
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')
            cell.border = thin_border
        
        # Data
        for row_num, kw in enumerate(keywords, 2):
            row_data = [
                kw.get('rank', ''),
                kw.get('keyword', ''),
                kw.get('volume', 0),
                kw.get('sector', ''),
                round(kw.get('score', 0), 4) if include_scores else '',
                'Evet' if kw.get('is_strategic') else ''
            ]
            
            for col, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_num, column=col, value=value)
                cell.border = thin_border
                if kw.get('is_strategic'):
                    cell.fill = strategic_fill
        
        # Column widths
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 40
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 20
        ws.column_dimensions['E'].width = 12
        ws.column_dimensions['F'].width = 12
    
    filepath = tempfile.mktemp(suffix='.xlsx')
    wb.save(filepath)
    
    return filepath
