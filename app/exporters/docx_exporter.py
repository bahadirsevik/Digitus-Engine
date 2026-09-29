"""
DOCX Exporter Module.

Word belgesi olarak export eder.
Türkçe karakter desteği ve Roadmap2.md formatına uygun.
"""
from typing import List, Optional
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from datetime import datetime

from app.exporters.base_exporter import BaseExporter
from app.exporters.seo_checklist_format import (
    ALT_TEXT_HEADING, FAQ_HEADING, geo_evaluation_text, manual_check_lines,
    review_failure_lines, review_required_text, review_status_text,
    status_counts_text,
)
from app.schemas.export import (
    ExportSectionEnum, FullReport, SummaryData,
    SEOContentData, AdGroupData, SocialContentData, BrandProfileData
)


class DocxExporter(BaseExporter):
    """
    Word belgesi olarak export eder.
    
    Roadmap2.md formatına uygun:
    - Kapak sayfası
    - İçindekiler
    - Bölüm bazlı içerik
    - Türkçe karakter desteği (UTF-8)
    """
    
    def export(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum],
        filepath: str
    ) -> str:
        """Export işlemini yapar."""
        data = self.collect_data(scoring_run_id, sections)
        
        doc = Document()
        
        # Kapak sayfası
        self._add_cover_page(doc, data)
        
        # İçindekiler
        self._add_toc(doc, sections)
        
        # Bölümler
        if self._should_include(ExportSectionEnum.SUMMARY, sections):
            self._add_summary_section(doc, data.summary)

        if self._should_include(ExportSectionEnum.BRAND_PROFILE, sections):
            self._add_brand_profile_section(doc, data.brand_profile)

        if self._should_include(ExportSectionEnum.SCORING, sections) and data.scoring:
            self._add_scoring_section(doc, data.scoring)
        
        if self._should_include(ExportSectionEnum.CHANNELS, sections) and data.channels:
            self._add_channels_section(doc, data.channels)
        
        if self._should_include(ExportSectionEnum.SEO_CONTENT, sections) and data.seo_contents:
            self._add_seo_content_section(doc, data.seo_contents)
        
        if self._should_include(ExportSectionEnum.ADS, sections) and data.ads:
            self._add_ads_section(doc, data.ads)
        
        if self._should_include(ExportSectionEnum.SOCIAL, sections) and data.social:
            self._add_social_section(doc, data.social)
        
        doc.save(filepath)
        return filepath
    
    def _add_cover_page(self, doc: Document, data: FullReport):
        """Kapak sayfası ekler."""
        # Boşluk
        for _ in range(5):
            doc.add_paragraph()
        
        # Başlık
        title = doc.add_heading('DIGITUS ENGINE RAPORU', 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Alt bilgiler
        info = doc.add_paragraph()
        info.alignment = WD_ALIGN_PARAGRAPH.CENTER
        info.add_run(f'\nHazırlanma Tarihi: {data.generated_at.strftime("%Y-%m-%d")}\n')
        info.add_run(f'Scoring Run: #{data.scoring_run_id}\n')
        if data.summary:
            info.add_run(f'Toplam Kelime: {data.summary.total_keywords}\n')
        
        doc.add_page_break()
    
    def _add_toc(self, doc: Document, sections: List[ExportSectionEnum]):
        """İçindekiler ekler."""
        doc.add_heading('İÇİNDEKİLER', level=1)
        
        toc_items = []
        if self._should_include(ExportSectionEnum.SUMMARY, sections):
            toc_items.append('1. Ozet')
        if self._should_include(ExportSectionEnum.BRAND_PROFILE, sections):
            toc_items.append('Marka Profili')
        if self._should_include(ExportSectionEnum.SCORING, sections):
            toc_items.append('2. Skorlama Sonuçları')
        if self._should_include(ExportSectionEnum.CHANNELS, sections):
            toc_items.append('3. Kanal Havuzları')
        if self._should_include(ExportSectionEnum.SEO_CONTENT, sections):
            toc_items.append('4. SEO+GEO İçerikleri')
        if self._should_include(ExportSectionEnum.ADS, sections):
            toc_items.append('5. Google Ads Reklam Setleri')
        if self._should_include(ExportSectionEnum.SOCIAL, sections):
            toc_items.append('6. Sosyal Medya İçerikleri')
        
        for item in toc_items:
            doc.add_paragraph(item)
        
        doc.add_page_break()
    
    def _add_summary_section(self, doc: Document, summary: SummaryData):
        """Özet bölümü ekler."""
        doc.add_heading('1. ÖZET', level=1)
        
        doc.add_paragraph(f'Analiz Edilen Kelime: {summary.total_keywords}')
        doc.add_paragraph()
        
        # Kanal dağılımı tablosu
        doc.add_heading('Kanal Dağılımı', level=2)
        table = doc.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        
        hdr = table.rows[0].cells
        hdr[0].text = 'Kanal'
        hdr[1].text = 'Kelime'
        hdr[2].text = 'Stratejik'
        
        channels = [
            ('ADS', summary.ads_count),
            ('SEO', summary.seo_count),
            ('SOCIAL', summary.social_count),
        ]
        
        for channel, count in channels:
            row = table.add_row().cells
            row[0].text = channel
            row[1].text = str(count)
            row[2].text = '-'
        
        doc.add_paragraph()
        
        # Üretilen içerik
        doc.add_heading('Üretilen İçerik', level=2)
        doc.add_paragraph(f'• SEO+GEO Blog Yazısı: {summary.seo_content_count}')
        doc.add_paragraph(f'• Reklam Grubu: {summary.ad_group_count}')
        doc.add_paragraph(f'• Sosyal Medya İçeriği: {summary.social_content_count}')
        
        doc.add_page_break()

    def _add_brand_profile_section(self, doc: Document, brand_profile: Optional[BrandProfileData]):
        """Marka profili bolumunu ekler."""
        doc.add_heading('MARKA PROFILI', level=1)

        if not brand_profile:
            doc.add_paragraph('Bu scoring run icin marka profili bulunamadi.')
            doc.add_page_break()
            return

        doc.add_paragraph(f'Durum: {brand_profile.status}')
        if brand_profile.company_url:
            doc.add_paragraph(f'Firma URL: {brand_profile.company_url}')
        if brand_profile.company_name:
            doc.add_paragraph(f'Firma Adi: {brand_profile.company_name}')
        if brand_profile.sector:
            doc.add_paragraph(f'Sektor: {brand_profile.sector}')
        if brand_profile.target_audience:
            doc.add_paragraph(f'Hedef Kitle: {brand_profile.target_audience}')

        def add_list(title: str, items: list):
            if not items:
                return
            doc.add_heading(title, level=2)
            for item in items:
                doc.add_paragraph(f'- {item}')

        add_list('Urunler', brand_profile.products)
        add_list('Hizmetler', brand_profile.services)
        add_list('Kullanim Alanlari', brand_profile.use_cases)
        add_list('Cozulen Problemler', brand_profile.problems_solved)
        add_list('Marka Terimleri', brand_profile.brand_terms)
        add_list('Korunacak Temalar', brand_profile.protected_themes)
        add_list('Dislanacak Temalar', brand_profile.exclude_themes)

        if brand_profile.source_pages:
            doc.add_heading('Kaynak Sayfalar', level=2)
            table = doc.add_table(rows=1, cols=3)
            table.style = 'Table Grid'
            hdr = table.rows[0].cells
            hdr[0].text = 'URL'
            hdr[1].text = 'Baslik'
            hdr[2].text = 'Durum'
            for page in brand_profile.source_pages:
                if not isinstance(page, dict):
                    continue
                row = table.add_row().cells
                row[0].text = str(page.get('url', ''))
                row[1].text = str(page.get('title', ''))
                row[2].text = str(page.get('status', ''))

        if brand_profile.competitors:
            doc.add_heading('Rakip Analizi', level=2)
            comp_table = doc.add_table(rows=1, cols=4)
            comp_table.style = 'Table Grid'
            hdr = comp_table.rows[0].cells
            hdr[0].text = 'URL'
            hdr[1].text = 'Durum'
            hdr[2].text = 'Skor'
            hdr[3].text = 'Ozet'
            for comp in brand_profile.competitors:
                row = comp_table.add_row().cells
                row[0].text = comp.url or '-'
                row[1].text = comp.status or '-'
                row[2].text = f'{comp.consistency_score:.2f}' if comp.consistency_score is not None else '-'
                row[3].text = (comp.summary or '-')[:200]
        elif brand_profile.competitor_urls:
            doc.add_heading('Rakip URLler', level=2)
            for url in brand_profile.competitor_urls:
                doc.add_paragraph(f'- {url}')

        if brand_profile.validation_warnings:
            doc.add_heading('Dogrulama Uyarilari', level=2)
            for warning in brand_profile.validation_warnings:
                doc.add_paragraph(f'- {warning}')

        if brand_profile.error_message:
            doc.add_paragraph(f'Hata Mesaji: {brand_profile.error_message}')

        doc.add_page_break()

    def _add_scoring_section(self, doc: Document, scoring):
        """Skorlama bölümü ekler."""
        doc.add_heading('2. SKORLAMA SONUÇLARI', level=1)
        
        if not scoring.keywords:
            doc.add_paragraph('Skorlama verisi bulunamadı.')
            return
        
        # Tablo
        table = doc.add_table(rows=1, cols=6)
        table.style = 'Table Grid'
        
        hdr = table.rows[0].cells
        hdr[0].text = 'Kelime'
        hdr[1].text = 'ADS'
        hdr[2].text = 'SEO'
        hdr[3].text = 'SOCIAL'
        hdr[4].text = 'Birincil'
        hdr[5].text = 'Niyet'
        
        for kw in scoring.keywords[:100]:  # İlk 100
            row = table.add_row().cells
            row[0].text = kw.keyword[:30] if kw.keyword else ''
            row[1].text = f'{kw.ads_score:.0f}' if kw.ads_score else '-'
            row[2].text = f'{kw.seo_score:.1f}' if kw.seo_score else '-'
            row[3].text = f'{kw.social_score:.1f}' if kw.social_score else '-'
            row[4].text = kw.primary_channel or '-'
            row[5].text = kw.intent[:15] if kw.intent else '-'
        
        doc.add_page_break()
    
    def _add_channels_section(self, doc: Document, channels):
        """Kanal havuzları bölümü ekler."""
        doc.add_heading('3. KANAL HAVUZLARI', level=1)
        
        for channel_name, pool in [('ADS', channels.ads), ('SEO', channels.seo), ('SOCIAL', channels.social)]:
            doc.add_heading(f'3.{["ADS", "SEO", "SOCIAL"].index(channel_name)+1} {channel_name} Havuzu', level=2)
            
            if not pool.keywords:
                doc.add_paragraph(f'{channel_name} havuzunda kelime bulunmuyor.')
                continue
            
            table = doc.add_table(rows=1, cols=6)
            table.style = 'Table Grid'
            
            hdr = table.rows[0].cells
            hdr[0].text = 'Sıra'
            hdr[1].text = 'Kelime'
            hdr[2].text = 'Skor'
            hdr[3].text = 'Vektör Yakınlığı'
            hdr[4].text = 'Çarpım Skoru'
            hdr[5].text = 'Niyet'
            
            for i, kw in enumerate(pool.keywords[:50], 1):
                row = table.add_row().cells
                row[0].text = str(i)
                row[1].text = kw.keyword[:40] if kw.keyword else ''
                score = kw.ads_score or kw.seo_score or kw.social_score or 0
                row[2].text = f'{score:.2f}'
                row[3].text = f'{kw.vector_similarity:.3f}' if kw.vector_similarity is not None else '-'
                row[4].text = f'{kw.vector_adjusted_score:.4f}' if kw.vector_adjusted_score is not None else '-'
                row[5].text = kw.intent[:20] if kw.intent else '-'
            
            doc.add_paragraph()
        
        doc.add_page_break()
    
    def _add_seo_content_section(self, doc: Document, seo_contents):
        """SEO+GEO içerikler bölümü ekler."""
        doc.add_heading('4. SEO+GEO İÇERİKLERİ', level=1)
        
        for c in seo_contents.contents:  # TAMAMI (plan v4: kesme yok)
            doc.add_heading(f'Anahtar Kelime: {c.keyword}', level=2)
            
            doc.add_paragraph(f'BAŞLIK: {c.title}')
            if c.url_suggestion:
                doc.add_paragraph(f'URL ÖNERİSİ: {c.url_suggestion}')
            if c.meta_description:
                doc.add_paragraph(f'META AÇIKLAMA: {c.meta_description}')
            
            # İstatistikler
            stats_parts = []
            if c.word_count:
                stats_parts.append(f'Kelime Sayısı: {c.word_count}')
            if c.keyword_count:
                stats_parts.append(f'Keyword Tekrar: {c.keyword_count}')
            if c.keyword_density:
                stats_parts.append(f'Keyword Yoğunluğu: %{c.keyword_density:.2f}')
            if stats_parts:
                doc.add_paragraph(' | '.join(stats_parts))
            
            # Giriş paragrafı
            if c.intro_paragraph:
                doc.add_heading('GİRİŞ PARAGRAFI', level=3)
                doc.add_paragraph(c.intro_paragraph)
            
            # Body sections (alt başlıklarla birlikte)
            if c.subheadings and c.body_sections:
                doc.add_heading('İÇERİK BÖLÜMLERİ', level=3)
                for i, subheading in enumerate(c.subheadings):
                    p = doc.add_paragraph()
                    run = p.add_run(subheading)
                    run.bold = True
                    if i < len(c.body_sections):
                        doc.add_paragraph(c.body_sections[i])
            elif c.body_content:
                doc.add_heading('İÇERİK', level=3)
                doc.add_paragraph(c.body_content)
            
            # Bullet points
            if c.bullet_points:
                doc.add_heading('MADDELER', level=3)
                for bp in c.bullet_points:
                    text = bp.get('text', '') if isinstance(bp, dict) else str(bp)
                    doc.add_paragraph(f'• {text}')
            
            # FAQ (schema'ya hazır soru-cevap çiftleri — şirket checklist P90)
            if c.faq_items:
                doc.add_heading(FAQ_HEADING, level=3)
                for faq in c.faq_items:
                    question = faq.get('question', '') if isinstance(faq, dict) else str(faq)
                    answer = faq.get('answer', '') if isinstance(faq, dict) else ''
                    p = doc.add_paragraph()
                    run = p.add_run(f'S: {question}')
                    run.bold = True
                    if answer:
                        doc.add_paragraph(f'C: {answer}')

            # Görsel alt-text önerileri (şirket checklist P60)
            if c.image_alt_texts:
                doc.add_heading(ALT_TEXT_HEADING, level=3)
                for alt in c.image_alt_texts:
                    doc.add_paragraph(f'• {alt}')

            # Link önerileri
            if c.internal_link_anchor or c.external_link_anchor:
                doc.add_heading('LİNK ÖNERİLERİ', level=3)
                if c.internal_link_anchor:
                    doc.add_paragraph(f'Internal Link: "{c.internal_link_anchor}" → {c.internal_link_url or "-"}')
                if c.external_link_anchor:
                    doc.add_paragraph(f'External Link: "{c.external_link_anchor}" → {c.external_link_url or "-"}')
            
            # Uyumluluk skorları (özet). GEO AI değerlendirmesi yoksa skor
            # "Fail" DEĞİL "Değerlendirilmedi" yazılır.
            doc.add_heading('UYUMLULUK SKORLARI', level=3)
            score_table = doc.add_table(rows=1, cols=3)
            score_table.style = 'Table Grid'

            hdr = score_table.rows[0].cells
            hdr[0].text = 'Metrik'
            hdr[1].text = 'Skor'
            hdr[2].text = 'Durum'

            geo_evaluated = c.geo_evaluation_source == 'ai'
            for metric, score in [('SEO', c.seo_score), ('GEO', c.geo_score), ('Combined', c.combined_score)]:
                row = score_table.add_row().cells
                row[0].text = metric
                if metric == 'GEO' and not geo_evaluated:
                    row[1].text = '-'
                    row[2].text = geo_evaluation_text(c.geo_evaluation_source)
                    continue
                row[1].text = f'{score:.2f}' if score else '-'
                row[2].text = '✔ Pass' if score and score >= 0.7 else '✗ Fail' if score else '-'

            # Yayın öncesi kontrol — kritik madde başarısız/değerlendirilmemişse
            # inceleme gerekli; verisi olmayan maddeler manuel not olarak
            review = c.publish_review
            doc.add_heading('YAYIN ÖNCESİ KONTROL', level=3)
            p = doc.add_paragraph()
            run = p.add_run(f'Yayın öncesi inceleme: {review_required_text(review)} — {review_status_text(review)}')
            run.bold = True
            for line in review_failure_lines(review):
                doc.add_paragraph(f'• {line}')
            manual = manual_check_lines(review)
            if manual:
                doc.add_paragraph('Manuel yayın kontrol notları:')
                for note in manual:
                    doc.add_paragraph(f'• {note}')

            # Güncel checklist sonuçları (checks_json dahil, madde bazlı)
            if c.checklist_items:
                doc.add_heading('KONTROL LİSTESİ SONUÇLARI', level=3)
                doc.add_paragraph(status_counts_text(c.checklist_items))
                check_table = doc.add_table(rows=1, cols=5)
                check_table.style = 'Table Grid'
                hdr = check_table.rows[0].cells
                for idx, title in enumerate(['Kanal', 'Kriter', 'Kaynak', 'Durum', 'Detay']):
                    hdr[idx].text = title
                for item in c.checklist_items:
                    row = check_table.add_row().cells
                    row[0].text = item.get('channel', '')
                    row[1].text = item.get('label', '')
                    row[2].text = item.get('source_label', '')
                    row[3].text = item.get('status_label', '')
                    row[4].text = item.get('detail', '') or ''

            if c.seo_checks and c.seo_checks.get('improvement_notes'):
                doc.add_paragraph(f'SEO iyileştirme: {c.seo_checks["improvement_notes"]}')
            if c.geo_checks:
                if geo_evaluated and c.geo_checks.get('ai_snippet_preview'):
                    doc.add_paragraph(f'AI Snippet: "{c.geo_checks["ai_snippet_preview"][:200]}"')
                if c.geo_checks.get('improvement_notes'):
                    doc.add_paragraph(f'GEO notu: {c.geo_checks["improvement_notes"]}')

            doc.add_paragraph()
        
        doc.add_page_break()
    
    def _add_ads_section(self, doc: Document, ads):
        """Google Ads bölümü ekler."""
        doc.add_heading('5. GOOGLE ADS REKLAM SETLERİ', level=1)
        
        for g in ads.ad_groups:
            doc.add_heading(f'REKLAM GRUBU: {g.group_name}', level=2)
            
            if g.target_keywords:
                doc.add_paragraph(f'Hedef Kelimeler: {", ".join(g.target_keywords)}')

            headline_line = ' | '.join([h.headline_text for h in g.headlines[:3]])
            description_text = ' '.join([d.description_text for d in g.descriptions[:2]])
            if headline_line or description_text:
                doc.add_heading('REKLAM METNİ (ÖNİZLEME)', level=3)
                if headline_line:
                    doc.add_paragraph(headline_line)
                if description_text:
                    doc.add_paragraph(description_text)
            
            # Başlıklar
            if g.headlines:
                doc.add_heading(f'BAŞLIKLAR ({len(g.headlines)})', level=3)
                h_table = doc.add_table(rows=1, cols=3)
                h_table.style = 'Table Grid'
                
                hdr = h_table.rows[0].cells
                hdr[0].text = '#'
                hdr[1].text = 'Başlık'
                hdr[2].text = 'Tip'
                
                for i, h in enumerate(g.headlines, 1):
                    row = h_table.add_row().cells
                    row[0].text = str(i)
                    row[1].text = h.headline_text
                    row[2].text = h.headline_type or '-'
            
            # Açıklamalar
            if g.descriptions:
                doc.add_heading(f'AÇIKLAMALAR ({len(g.descriptions)})', level=3)
                for i, d in enumerate(g.descriptions, 1):
                    doc.add_paragraph(f'{i}. {d.description_text}')
            
            # Negatifler
            if g.negative_keywords:
                doc.add_heading(f'NEGATİF KELİMELER ({len(g.negative_keywords)})', level=3)
                negatives = ', '.join([n.keyword for n in g.negative_keywords])
                doc.add_paragraph(negatives)
            
            doc.add_paragraph()
        
        doc.add_page_break()
    
    def _add_social_section(self, doc: Document, social):
        """Sosyal medya bölümü ekler (brief bazında gruplanır, plan §8)."""
        from app.exporters.social_format import (
            format_payload_lines, duration_requested_text, duration_status_text,
        )
        doc.add_heading('6. SOSYAL MEDYA İÇERİKLERİ', level=1)

        for group in social.brief_groups:  # TAMAMI (plan v4: kesme yok)
            doc.add_heading(group.label, level=2)
            if group.keywords:
                doc.add_paragraph('Kelimeler: ' + ', '.join(k.keyword for k in group.keywords))

            for c in group.contents:
                doc.add_heading(f'İÇERİK: "{c.idea_title}"', level=3)
                doc.add_paragraph(f'Platform: {c.platform.title()} | Format: {c.content_format}')
                if c.idea_keyword:
                    doc.add_paragraph(f'Ana Kelime: {c.idea_keyword}')
                doc.add_paragraph(f'Viral Potansiyel: {c.trend_alignment:.2f}')

                if c.duration_min_sec is not None or c.duration_max_sec is not None or c.actual_duration_sec is not None:
                    requested = duration_requested_text(c.duration_min_sec, c.duration_max_sec)
                    actual = f'{c.actual_duration_sec} sn' if c.actual_duration_sec is not None else '-'
                    doc.add_paragraph(
                        f'Süre: istenen {requested}, gerçek {actual} '
                        f'({duration_status_text(c.duration_status)})'
                    )

                # Hooklar
                if c.hooks:
                    doc.add_heading("HOOK'LAR", level=4)
                    for i, h in enumerate(c.hooks, 1):
                        doc.add_paragraph(f'{i}. [{h.style}] "{h.text}"')

                # Caption
                if c.caption:
                    doc.add_heading('CAPTION', level=4)
                    doc.add_paragraph(c.caption)

                # Format detayı (video sahneleri / carousel slaytları / thread gönderileri)
                payload_lines = format_payload_lines(c.format_payload)
                if payload_lines:
                    doc.add_heading('İÇERİK DETAYI', level=4)
                    for line in payload_lines:
                        doc.add_paragraph(line)

                # Hashtagler
                if c.hashtags:
                    doc.add_heading("HASHTAG'LER", level=4)
                    doc.add_paragraph(' '.join([f'#{t}' for t in c.hashtags]))

                # Senaryo (plan v4 alan matrisi)
                if c.scenario:
                    doc.add_heading('SENARYO', level=4)
                    doc.add_paragraph(c.scenario)

                # Gorsel/video onerileri + notlar (plan v4 alan matrisi)
                if c.visual_suggestion:
                    doc.add_paragraph(f'Görsel Önerisi: {c.visual_suggestion}')
                if c.video_concept:
                    doc.add_paragraph(f'Video Konsepti: {c.video_concept}')
                if c.industry_posting_suggestion:
                    doc.add_paragraph(f'Sektör Paylaşım Önerisi: {c.industry_posting_suggestion}')
                if c.platform_notes:
                    doc.add_paragraph(f'Platform Notları: {c.platform_notes}')

                # CTA
                if c.cta_text:
                    doc.add_paragraph(f'CTA: "{c.cta_text}"')

                # Uyarılar (K8/§4 — süre tutmadığında ve grounding uyarılarında dolar)
                if c.validation_warnings:
                    doc.add_paragraph('Uyarılar: ' + '; '.join(c.validation_warnings))

                doc.add_paragraph()


