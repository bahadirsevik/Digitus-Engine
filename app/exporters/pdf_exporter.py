"""
PDF Exporter Module.

PDF formatında export eder.
Türkçe karakter desteği: DejaVuSans font.
"""
from typing import List, Optional
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

from app.exporters.base_exporter import BaseExporter
from app.schemas.export import ExportSectionEnum, FullReport, BrandProfileData


class PdfExporter(BaseExporter):
    """
    PDF dosyası olarak export eder.
    
    Türkçe karakter desteği için DejaVuSans font kullanır.
    Roadmap2.md formatına uygun bölümler.
    """
    
    def __init__(self, db):
        super().__init__(db)
        self._register_fonts()
        self.styles = self._create_styles()
    
    def _register_fonts(self):
        """Türkçe karakter desteği için font kaydeder."""
        try:
            # Sistem fontlarını dene
            font_paths = [
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                'C:/Windows/Fonts/arial.ttf',
                '/System/Library/Fonts/Helvetica.ttc',
            ]
            
            for font_path in font_paths:
                if os.path.exists(font_path):
                    pdfmetrics.registerFont(TTFont('Turkish', font_path))
                    return
            
            # Fallback: Helvetica kullan
        except Exception:
            pass
    
    def _create_styles(self):
        """PDF stilleri oluşturur."""
        styles = getSampleStyleSheet()
        
        # Türkçe destekli stiller
        styles.add(ParagraphStyle(
            name='TurkishTitle',
            parent=styles['Title'],
            fontName='Helvetica-Bold',
            fontSize=24,
            spaceAfter=30,
        ))
        
        styles.add(ParagraphStyle(
            name='TurkishHeading',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=16,
            spaceBefore=20,
            spaceAfter=10,
        ))
        
        styles.add(ParagraphStyle(
            name='TurkishNormal',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
        ))
        
        return styles
    
    def export(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum],
        filepath: str
    ) -> str:
        """Export işlemini yapar."""
        data = self.collect_data(scoring_run_id, sections)
        
        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )
        
        elements = []
        
        # Kapak
        elements.extend(self._create_cover(data))
        elements.append(PageBreak())
        
        # Bölümler
        if self._should_include(ExportSectionEnum.SUMMARY, sections) and data.summary:
            elements.extend(self._create_summary(data.summary, data.scoring_run_id))

        if self._should_include(ExportSectionEnum.BRAND_PROFILE, sections):
            elements.extend(self._create_brand_profile(data.brand_profile))
        
        if self._should_include(ExportSectionEnum.SCORING, sections) and data.scoring:
            elements.extend(self._create_scoring(data.scoring))
        
        if self._should_include(ExportSectionEnum.CHANNELS, sections) and data.channels:
            elements.extend(self._create_channels(data.channels))
        
        if self._should_include(ExportSectionEnum.SEO_CONTENT, sections) and data.seo_contents:
            elements.extend(self._create_seo_content(data.seo_contents))
        
        if self._should_include(ExportSectionEnum.ADS, sections) and data.ads:
            elements.extend(self._create_ads(data.ads))
        
        if self._should_include(ExportSectionEnum.SOCIAL, sections) and data.social:
            elements.extend(self._create_social(data.social))
        
        doc.build(elements)
        return filepath
    
    def _create_cover(self, data: FullReport) -> list:
        """Kapak sayfası."""
        elements = []
        
        elements.append(Spacer(1, 5*cm))
        elements.append(Paragraph('DIGITUS ENGINE RAPORU', self.styles['TurkishTitle']))
        elements.append(Spacer(1, 2*cm))
        
        info_text = f"""
        Hazirlanma Tarihi: {data.generated_at.strftime('%Y-%m-%d')}<br/>
        Scoring Run: #{data.scoring_run_id}<br/>
        Toplam Kelime: {data.summary.total_keywords if data.summary else 0}
        """
        elements.append(Paragraph(info_text, self.styles['TurkishNormal']))
        
        return elements
    
    def _create_summary(self, summary, scoring_run_id: int) -> list:
        """Özet bölümü."""
        elements = []
        
        elements.append(Paragraph('1. OZET', self.styles['TurkishHeading']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Kanal dağılımı tablosu
        data = [
            ['Kanal', 'Kelime Sayisi', 'Stratejik'],
            ['ADS', str(summary.ads_count), '-'],
            ['SEO', str(summary.seo_count), '-'],
            ['SOCIAL', str(summary.social_count), '-'],
        ]
        
        table = Table(data, colWidths=[5*cm, 4*cm, 4*cm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 1*cm))
        
        # Üretilen içerik
        elements.append(Paragraph('Uretilen Icerik:', self.styles['TurkishNormal']))
        elements.append(Paragraph(f'- SEO+GEO Blog Yazisi: {summary.seo_content_count}', self.styles['TurkishNormal']))
        elements.append(Paragraph(f'- Reklam Grubu: {summary.ad_group_count}', self.styles['TurkishNormal']))
        elements.append(Paragraph(f'- Sosyal Medya Icerigi: {summary.social_content_count}', self.styles['TurkishNormal']))
        
        elements.append(PageBreak())
        return elements
    
    def _create_scoring(self, scoring) -> list:
        """Skorlama bölümü."""
        elements = []
        
        elements.append(Paragraph('2. SKORLAMA SONUCLARI', self.styles['TurkishHeading']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Tablo (ilk 50)
        data = [['Kelime', 'ADS', 'SEO', 'SOCIAL', 'Birincil']]
        
        for kw in scoring.keywords[:50]:
            data.append([
                kw.keyword[:25] if kw.keyword else '',
                f'{kw.ads_score:.0f}' if kw.ads_score else '-',
                f'{kw.seo_score:.1f}' if kw.seo_score else '-',
                f'{kw.social_score:.1f}' if kw.social_score else '-',
                kw.primary_channel or '-'
            ])
        
        table = Table(data, colWidths=[6*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3*cm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(table)
        
        elements.append(PageBreak())
        return elements

    def _create_brand_profile(self, brand_profile: Optional[BrandProfileData]) -> list:
        """Marka profili bolumu."""
        elements = []
        elements.append(Paragraph('MARKA PROFILI', self.styles['TurkishHeading']))
        elements.append(Spacer(1, 0.4 * cm))

        if not brand_profile:
            elements.append(Paragraph('Bu scoring run icin marka profili bulunamadi.', self.styles['TurkishNormal']))
            elements.append(PageBreak())
            return elements

        elements.append(Paragraph(f'Durum: {brand_profile.status}', self.styles['TurkishNormal']))
        if brand_profile.company_url:
            elements.append(Paragraph(f'Firma URL: {brand_profile.company_url}', self.styles['TurkishNormal']))
        if brand_profile.company_name:
            elements.append(Paragraph(f'Firma Adi: {brand_profile.company_name}', self.styles['TurkishNormal']))
        if brand_profile.sector:
            elements.append(Paragraph(f'Sektor: {brand_profile.sector}', self.styles['TurkishNormal']))
        if brand_profile.target_audience:
            elements.append(Paragraph(f'Hedef Kitle: {brand_profile.target_audience}', self.styles['TurkishNormal']))

        def add_list(title: str, values: list):
            if not values:
                return
            elements.append(Spacer(1, 0.2 * cm))
            elements.append(Paragraph(f'<b>{title}</b>', self.styles['TurkishNormal']))
            for value in values[:20]:
                elements.append(Paragraph(f'- {value}', self.styles['TurkishNormal']))

        add_list('Urunler', brand_profile.products)
        add_list('Hizmetler', brand_profile.services)
        add_list('Kullanim Alanlari', brand_profile.use_cases)
        add_list('Cozulen Problemler', brand_profile.problems_solved)
        add_list('Marka Terimleri', brand_profile.brand_terms)
        add_list('Dislanacak Temalar', brand_profile.exclude_themes)

        if brand_profile.competitors:
            elements.append(Spacer(1, 0.3 * cm))
            elements.append(Paragraph('<b>Rakip Analizi</b>', self.styles['TurkishNormal']))
            comp_rows = [['URL', 'Durum', 'Skor', 'Ozet']]
            for comp in brand_profile.competitors:
                score = f'{comp.consistency_score:.2f}' if comp.consistency_score is not None else '-'
                comp_rows.append([comp.url or '-', comp.status or '-', score, (comp.summary or '-')[:80]])
            table = Table(comp_rows, colWidths=[5 * cm, 2.5 * cm, 2 * cm, 5.5 * cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            elements.append(table)
        elif brand_profile.competitor_urls:
            elements.append(Spacer(1, 0.3 * cm))
            elements.append(Paragraph('<b>Rakip URLler</b>', self.styles['TurkishNormal']))
            for url in brand_profile.competitor_urls:
                elements.append(Paragraph(f'- {url}', self.styles['TurkishNormal']))

        if brand_profile.validation_warnings:
            elements.append(Spacer(1, 0.3 * cm))
            elements.append(Paragraph('<b>Dogrulama Uyarilari</b>', self.styles['TurkishNormal']))
            for warning in brand_profile.validation_warnings:
                elements.append(Paragraph(f'- {warning}', self.styles['TurkishNormal']))

        if brand_profile.error_message:
            elements.append(Spacer(1, 0.2 * cm))
            elements.append(Paragraph(f'Hata Mesaji: {brand_profile.error_message}', self.styles['TurkishNormal']))

        elements.append(PageBreak())
        return elements
    
    def _create_channels(self, channels) -> list:
        """Kanal havuzları."""
        elements = []
        
        elements.append(Paragraph('3. KANAL HAVUZLARI', self.styles['TurkishHeading']))
        
        for name, pool in [('ADS', channels.ads), ('SEO', channels.seo), ('SOCIAL', channels.social)]:
            elements.append(Spacer(1, 0.5*cm))
            elements.append(Paragraph(f'{name} Havuzu ({pool.total} kelime)', self.styles['TurkishNormal']))
            
            if pool.keywords:
                data = [['#', 'Kelime', 'Skor', 'Vektör', 'Çarpım']]
                for i, kw in enumerate(pool.keywords[:20], 1):
                    score = kw.ads_score or kw.seo_score or kw.social_score or 0
                    vector = f'{kw.vector_similarity:.3f}' if kw.vector_similarity is not None else '-'
                    adjusted = f'{kw.vector_adjusted_score:.4f}' if kw.vector_adjusted_score is not None else '-'
                    data.append([str(i), kw.keyword[:30] if kw.keyword else '', f'{score:.2f}', vector, adjusted])
                
                table = Table(data, colWidths=[1*cm, 8*cm, 2*cm, 2*cm, 2*cm])
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]))
                elements.append(table)
        
        elements.append(PageBreak())
        return elements
    
    def _create_seo_content(self, seo_contents) -> list:
        """SEO içerikler."""
        elements = []
        
        elements.append(Paragraph('4. SEO+GEO ICERIKLERI', self.styles['TurkishHeading']))
        
        for c in seo_contents.contents[:10]:
            elements.append(Spacer(1, 0.5*cm))
            elements.append(Paragraph(f'Kelime: {c.keyword}', self.styles['TurkishNormal']))
            elements.append(Paragraph(f'Baslik: {c.title}', self.styles['TurkishNormal']))
            
            scores = f'SEO: {c.seo_score:.2f}' if c.seo_score else 'SEO: -'
            scores += f' | GEO: {c.geo_score:.2f}' if c.geo_score else ' | GEO: -'
            scores += f' | Combined: {c.combined_score:.2f}' if c.combined_score else ''
            elements.append(Paragraph(scores, self.styles['TurkishNormal']))
        
        elements.append(PageBreak())
        return elements
    
    def _create_ads(self, ads) -> list:
        """Ads bölümü."""
        elements = []
        
        elements.append(Paragraph('5. GOOGLE ADS REKLAM SETLERI', self.styles['TurkishHeading']))
        
        for g in ads.ad_groups[:5]:
            elements.append(Spacer(1, 0.5*cm))
            elements.append(Paragraph(f'Reklam Grubu: {g.group_name}', self.styles['TurkishNormal']))
            elements.append(Paragraph(f'Baslik Sayisi: {len(g.headlines)} | Aciklama: {len(g.descriptions)}', self.styles['TurkishNormal']))
        
        elements.append(PageBreak())
        return elements
    
    def _create_social(self, social) -> list:
        """Sosyal medya bölümü."""
        elements = []
        
        elements.append(Paragraph('6. SOSYAL MEDYA ICERIKLERI', self.styles['TurkishHeading']))
        
        for c in social.contents[:5]:
            elements.append(Spacer(1, 0.5*cm))
            elements.append(Paragraph(f'Icerik: {c.idea_title}', self.styles['TurkishNormal']))
            elements.append(Paragraph(f'Platform: {c.platform} | Trend: {c.trend_alignment:.2f}', self.styles['TurkishNormal']))
            
            if c.hooks:
                hooks_text = ', '.join([f'[{h.style}] {h.text[:30]}' for h in c.hooks[:2]])
                elements.append(Paragraph(f'Hooklar: {hooks_text}', self.styles['TurkishNormal']))
        
        return elements

