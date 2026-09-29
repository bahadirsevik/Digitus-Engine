"""
PDF Exporter Module.

PDF formatında export eder.
Türkçe karakter desteği: DejaVuSans font.
"""
import os
from typing import List, Optional

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.exporters.base_exporter import BaseExporter
from app.exporters.safe_text import safe_paragraph_text
from app.exporters.seo_checklist_format import (
    ALT_TEXT_HEADING, FAQ_HEADING, geo_evaluation_text, manual_check_lines,
    review_failure_lines, review_required_text, review_status_text,
    status_counts_text,
)
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
        """Register an embedded Unicode font family with Turkish glyphs.

        ReportLab's built-in Helvetica is WinAnsi encoded and cannot render
        every Turkish character. ReportLab ships Vera TTF files, so the final
        fallback is deterministic even in the slim production container.
        """
        self.font_name = 'DigitusUnicode'
        self.font_name_bold = 'DigitusUnicode-Bold'

        reportlab_fonts = os.path.join(os.path.dirname(reportlab.__file__), 'fonts')
        font_pairs = [
            (
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            ),
            ('C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/arialbd.ttf'),
            (
                os.path.join(reportlab_fonts, 'Vera.ttf'),
                os.path.join(reportlab_fonts, 'VeraBd.ttf'),
            ),
        ]

        for regular_path, bold_path in font_pairs:
            if not (os.path.exists(regular_path) and os.path.exists(bold_path)):
                continue
            pdfmetrics.registerFont(TTFont(self.font_name, regular_path))
            pdfmetrics.registerFont(TTFont(self.font_name_bold, bold_path))
            pdfmetrics.registerFontFamily(
                self.font_name,
                normal=self.font_name,
                bold=self.font_name_bold,
                italic=self.font_name,
                boldItalic=self.font_name_bold,
            )
            return

        raise RuntimeError('PDF export requires a Unicode TrueType font')
    
    def _create_styles(self):
        """PDF stilleri oluşturur."""
        styles = getSampleStyleSheet()
        
        # Türkçe destekli stiller
        styles.add(ParagraphStyle(
            name='TurkishTitle',
            parent=styles['Title'],
            fontName=self.font_name_bold,
            fontSize=24,
            spaceAfter=30,
        ))
        
        styles.add(ParagraphStyle(
            name='TurkishHeading',
            parent=styles['Heading1'],
            fontName=self.font_name_bold,
            fontSize=16,
            spaceBefore=20,
            spaceAfter=10,
        ))
        
        styles.add(ParagraphStyle(
            name='TurkishNormal',
            parent=styles['Normal'],
            fontName=self.font_name,
            fontSize=10,
        ))
        styles.add(ParagraphStyle(
            name='ChecklistCell',
            parent=styles['TurkishNormal'],
            fontSize=7,
            leading=9,
        ))
        styles.add(ParagraphStyle(
            name='ChecklistHeader',
            parent=styles['ChecklistCell'],
            fontName=self.font_name_bold,
            textColor=colors.white,
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
            ('FONTNAME', (0, 0), (-1, -1), self.font_name),
            ('FONTNAME', (0, 0), (-1, 0), self.font_name_bold),
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
            ('FONTNAME', (0, 0), (-1, -1), self.font_name),
            ('FONTNAME', (0, 0), (-1, 0), self.font_name_bold),
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
        add_list('Korunacak Temalar', brand_profile.protected_themes)
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
                ('FONTNAME', (0, 0), (-1, -1), self.font_name),
                ('FONTNAME', (0, 0), (-1, 0), self.font_name_bold),
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
                    ('FONTNAME', (0, 0), (-1, -1), self.font_name),
                    ('FONTNAME', (0, 0), (-1, 0), self.font_name_bold),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]))
                elements.append(table)
        
        elements.append(PageBreak())
        return elements
    
    def _p(self, text, bold: bool = False) -> Paragraph:
        """Guvenli paragraf: mini-HTML escape (plan v4 tur-5 #2)."""
        safe = safe_paragraph_text(text)
        if bold:
            safe = f'<b>{safe}</b>'
        return Paragraph(safe, self.styles['TurkishNormal'])

    def _create_seo_content(self, seo_contents) -> list:
        """SEO içerikler — tam gövde (plan v4: kesme yok)."""
        elements = []

        elements.append(Paragraph('4. SEO+GEO ICERIKLERI', self.styles['TurkishHeading']))

        for c in seo_contents.contents:
            elements.append(Spacer(1, 0.5*cm))
            elements.append(self._p(f'ICERIK: {c.keyword}', bold=True))
            elements.append(self._p(f'Baslik: {c.title}'))
            if c.url_suggestion:
                elements.append(self._p(f'URL Onerisi: {c.url_suggestion}'))
            if c.meta_description:
                elements.append(self._p(f'Meta Aciklama: {c.meta_description}'))

            scores = f'SEO: {c.seo_score:.2f}' if c.seo_score else 'SEO: -'
            if c.geo_evaluation_source != 'ai':
                scores += f' | GEO: {geo_evaluation_text(c.geo_evaluation_source)}'
            else:
                scores += f' | GEO: {c.geo_score:.2f}' if c.geo_score else ' | GEO: -'
            scores += f' | Combined: {c.combined_score:.2f}' if c.combined_score else ''
            scores += f' | Kelime Sayisi: {c.word_count}'
            elements.append(self._p(scores))

            elements.extend(self._seo_check_summary(c))

            if c.intro_paragraph:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p('Giris', bold=True))
                elements.append(self._p(c.intro_paragraph))

            body = c.body_content or '\n\n'.join(c.body_sections or [])
            if body:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p('Icerik Govdesi', bold=True))
                for para in body.split('\n\n'):
                    if para.strip():
                        elements.append(self._p(para))
                        elements.append(Spacer(1, 0.1*cm))

            if c.faq_items:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p(FAQ_HEADING, bold=True))
                for item in c.faq_items:
                    q = item.get('question') or item.get('q') or ''
                    a = item.get('answer') or item.get('a') or ''
                    elements.append(self._p(f'S: {q}'))
                    elements.append(self._p(f'C: {a}'))

            if c.image_alt_texts:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p(ALT_TEXT_HEADING, bold=True))
                for alt in c.image_alt_texts:
                    elements.append(self._p(f'- {alt}'))

            links = []
            if c.internal_link_anchor or c.internal_link_url:
                links.append(f'Internal: {c.internal_link_anchor or "-"} -> {c.internal_link_url or "-"}')
            if c.external_link_anchor or c.external_link_url:
                links.append(f'External: {c.external_link_anchor or "-"} -> {c.external_link_url or "-"}')
            if links:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p('Link Onerileri', bold=True))
                for link in links:
                    elements.append(self._p(f'- {link}'))

        elements.append(PageBreak())
        return elements

    def _seo_check_summary(self, c) -> list:
        """Yayın uyarısı ve SEO/GEO maddelerinin tamamını içeren PDF tablosu."""
        review = c.publish_review
        elements = [Spacer(1, 0.2*cm)]
        elements.append(self._p(
            f'Yayin oncesi inceleme: {review_required_text(review)} — {review_status_text(review)}',
            bold=True,
        ))
        if c.checklist_items:
            elements.append(self._p(f'Kontrol ozeti: {status_counts_text(c.checklist_items)}'))
            elements.append(self._p('SEO/GEO kontrol listesi', bold=True))
            headers = ['Kanal', 'Kriter', 'Kaynak', 'Durum', 'Detay']
            rows = [[Paragraph(safe_paragraph_text(label), self.styles['ChecklistHeader'])
                     for label in headers]]
            for item in c.checklist_items:
                rows.append([
                    Paragraph(safe_paragraph_text(str(item.get(key) or '')),
                              self.styles['ChecklistCell'])
                    for key in ('channel', 'label', 'source_label', 'status_label', 'detail')
                ])
            table = Table(rows, colWidths=[1.2*cm, 4.5*cm, 2.4*cm, 2.3*cm, 6.1*cm],
                          repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1c232f')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#c9ced6')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f5f7')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(table)
        for line in review_failure_lines(review):
            elements.append(self._p(f'! {line}'))
        manual = manual_check_lines(review)
        if manual:
            elements.append(self._p('Manuel yayin kontrol notlari', bold=True))
            for note in manual:
                elements.append(self._p(f'- {note}'))
        return elements

    def _create_ads(self, ads) -> list:
        """Ads bölümü — tam metinler (plan v4: kesme yok)."""
        elements = []

        elements.append(Paragraph('5. GOOGLE ADS REKLAM SETLERI', self.styles['TurkishHeading']))

        for g in ads.ad_groups:
            elements.append(Spacer(1, 0.5*cm))
            elements.append(self._p(f'REKLAM GRUBU: {g.group_name}', bold=True))
            if g.group_theme:
                elements.append(self._p(f'Tema: {g.group_theme}'))
            if g.target_keywords:
                elements.append(self._p(f'Hedef Kelimeler: {", ".join(g.target_keywords)}'))

            if g.headlines:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p(f'Basliklar ({len(g.headlines)})', bold=True))
                for i, h in enumerate(g.headlines, 1):
                    suffix = f' [{h.headline_type}]' if h.headline_type else ''
                    elements.append(self._p(f'{i}. {h.headline_text}{suffix}'))

            if g.descriptions:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p(f'Aciklamalar ({len(g.descriptions)})', bold=True))
                for i, d in enumerate(g.descriptions, 1):
                    elements.append(self._p(f'{i}. {d.description_text}'))

            if g.negative_keywords:
                elements.append(Spacer(1, 0.2*cm))
                elements.append(self._p(f'Negatif Kelimeler ({len(g.negative_keywords)})', bold=True))
                elements.append(self._p(', '.join(n.keyword for n in g.negative_keywords)))

        elements.append(PageBreak())
        return elements

    def _create_social(self, social) -> list:
        """Sosyal medya bölümü — tam icerik paketi (brief bazinda gruplanir, plan §8)."""
        from app.exporters.social_format import (
            format_payload_text, duration_requested_text, duration_status_text,
        )
        elements = []

        elements.append(Paragraph('6. SOSYAL MEDYA ICERIKLERI', self.styles['TurkishHeading']))

        for g in social.brief_groups:
            elements.append(Spacer(1, 0.4*cm))
            elements.append(self._p(g.label, bold=True))
            if g.keywords:
                elements.append(self._p('Kelimeler: ' + ', '.join(k.keyword for k in g.keywords)))

            for c in g.contents:
                elements.append(Spacer(1, 0.5*cm))
                elements.append(self._p(f'ICERIK: {c.idea_title}', bold=True))
                elements.append(self._p(f'Platform: {c.platform} | Format: {c.content_format} | Trend: {c.trend_alignment:.2f}'))
                if c.idea_keyword:
                    elements.append(self._p(f'Ana Kelime: {c.idea_keyword}'))

                if c.duration_min_sec is not None or c.duration_max_sec is not None or c.actual_duration_sec is not None:
                    requested = duration_requested_text(c.duration_min_sec, c.duration_max_sec)
                    actual = f'{c.actual_duration_sec} sn' if c.actual_duration_sec is not None else '-'
                    elements.append(self._p(
                        f'Sure: istenen {requested}, gercek {actual} '
                        f'({duration_status_text(c.duration_status)})'
                    ))

                if c.hooks:
                    elements.append(Spacer(1, 0.2*cm))
                    elements.append(self._p("Hook'lar", bold=True))
                    for i, h in enumerate(c.hooks, 1):
                        elements.append(self._p(f'{i}. [{h.style}] {h.text}'))

                if c.caption:
                    elements.append(Spacer(1, 0.2*cm))
                    elements.append(self._p('Caption', bold=True))
                    elements.append(self._p(c.caption))

                payload_text = format_payload_text(c.format_payload)
                if payload_text:
                    elements.append(Spacer(1, 0.2*cm))
                    elements.append(self._p('Icerik Detayi', bold=True))
                    elements.append(self._p(payload_text))

                if c.scenario:
                    elements.append(Spacer(1, 0.2*cm))
                    elements.append(self._p('Senaryo', bold=True))
                    elements.append(self._p(c.scenario))

                if c.visual_suggestion:
                    elements.append(self._p(f'Gorsel Onerisi: {c.visual_suggestion}'))
                if c.video_concept:
                    elements.append(self._p(f'Video Konsepti: {c.video_concept}'))
                if c.industry_posting_suggestion:
                    elements.append(self._p(f'Sektor Paylasim Onerisi: {c.industry_posting_suggestion}'))
                if c.platform_notes:
                    elements.append(self._p(f'Platform Notlari: {c.platform_notes}'))

                if c.hashtags:
                    elements.append(self._p('Hashtagler: ' + ' '.join(f'#{t}' for t in c.hashtags)))
                if c.cta_text:
                    elements.append(self._p(f'CTA: {c.cta_text}'))
                if c.validation_warnings:
                    elements.append(self._p('Uyarilar: ' + '; '.join(c.validation_warnings)))

        return elements
