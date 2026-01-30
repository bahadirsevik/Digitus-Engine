"""
DOCX exporter module.
"""
from typing import Dict, Any
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
import tempfile


def export_to_docx(
    scoring_run_id: int,
    run_name: str,
    pools_data: Dict[str, list],
    include_content: bool = False
) -> str:
    """
    Kanal havuzlarını DOCX formatında dışa aktarır.
    
    Returns:
        Oluşturulan dosyanın yolu
    """
    doc = Document()
    
    # Title
    title = doc.add_heading(f'DIGITUS ENGINE - Rapor', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Info
    doc.add_paragraph(f'Scoring Run ID: {scoring_run_id}')
    doc.add_paragraph(f'Çalıştırma Adı: {run_name}')
    doc.add_paragraph()
    
    # Summary table
    doc.add_heading('Özet', level=1)
    summary_table = doc.add_table(rows=1, cols=2)
    summary_table.style = 'Table Grid'
    
    hdr = summary_table.rows[0].cells
    hdr[0].text = 'Kanal'
    hdr[1].text = 'Kelime Sayısı'
    
    for channel, keywords in pools_data.items():
        row = summary_table.add_row().cells
        row[0].text = channel
        row[1].text = str(len(keywords))
    
    doc.add_paragraph()
    
    # Each channel
    for channel, keywords in pools_data.items():
        doc.add_heading(f'{channel} Kanal Havuzu', level=1)
        
        if not keywords:
            doc.add_paragraph('Bu kanalda kelime bulunmuyor.')
            continue
        
        table = doc.add_table(rows=1, cols=5)
        table.style = 'Table Grid'
        
        headers = table.rows[0].cells
        headers[0].text = 'Sıra'
        headers[1].text = 'Anahtar Kelime'
        headers[2].text = 'Hacim'
        headers[3].text = 'Skor'
        headers[4].text = 'Stratejik'
        
        for kw in keywords[:50]:  # Limit
            row = table.add_row().cells
            row[0].text = str(kw.get('rank', '-'))
            row[1].text = kw.get('keyword', '')
            row[2].text = str(kw.get('volume', 0))
            row[3].text = f"{kw.get('score', 0):.2f}"
            row[4].text = '★' if kw.get('is_strategic') else ''
        
        doc.add_paragraph()
    
    # Save
    filepath = tempfile.mktemp(suffix='.docx')
    doc.save(filepath)
    
    return filepath
