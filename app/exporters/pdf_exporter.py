"""
PDF exporter module.
"""
from typing import Dict, Any
from reportlab.lib.pagesizes import letter, A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import tempfile


def export_to_pdf(
    scoring_run_id: int,
    run_name: str,
    pools_data: Dict[str, list]
) -> str:
    """
    Kanal havuzlarını PDF formatında dışa aktarır.
    
    Returns:
        Oluşturulan dosyanın yolu
    """
    filepath = tempfile.mktemp(suffix='.pdf')
    doc = SimpleDocTemplate(filepath, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    
    # Title
    title = Paragraph(f"DIGITUS ENGINE - Rapor", styles['Title'])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Info
    elements.append(Paragraph(f"Scoring Run ID: {scoring_run_id}", styles['Normal']))
    elements.append(Paragraph(f"Çalıştırma Adı: {run_name or 'N/A'}", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    # Each channel
    for channel, keywords in pools_data.items():
        elements.append(Paragraph(f"{channel} Kanal Havuzu", styles['Heading1']))
        elements.append(Spacer(1, 12))
        
        if not keywords:
            elements.append(Paragraph("Bu kanalda kelime bulunmuyor.", styles['Normal']))
            elements.append(Spacer(1, 12))
            continue
        
        # Table data
        table_data = [['Sıra', 'Anahtar Kelime', 'Hacim', 'Skor', 'Str.']]
        
        for kw in keywords[:30]:  # Limit for PDF
            table_data.append([
                str(kw.get('rank', '-')),
                kw.get('keyword', '')[:30],  # Truncate long keywords
                str(kw.get('volume', 0)),
                f"{kw.get('score', 0):.2f}",
                '★' if kw.get('is_strategic') else ''
            ])
        
        table = Table(table_data, colWidths=[40, 200, 60, 60, 40])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        elements.append(table)
        elements.append(Spacer(1, 24))
    
    doc.build(elements)
    return filepath
