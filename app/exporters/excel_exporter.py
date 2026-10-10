"""
Excel Exporter Module.

Excel dosyası olarak export eder. Türkçe karakter desteği (openpyxl UTF-8).

Plan v4 (export dağıtımı) sözleşmeleri:
- Alan matrisi TAM: SEO gövde/meta/FAQ/alt-text, SOCIAL tüm hook/caption/
  scenario/görsel-video/CTA/hashtag/platform notları, ADS tüm metinler.
  Adet/karakter kesmesi YOK.
- Özet sayfası yalnız `all`/SUMMARY istendiğinde eklenir — tekil içerik
  bölümü export'u yalnız o kanalın verisini taşır.
- Tüm metin hücreleri `safe_excel_text`'ten geçer (formula injection).
"""
from typing import List, Optional
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.exporters.base_exporter import BaseExporter
from app.exporters.safe_text import safe_excel_text as _sx
from app.exporters.seo_checklist_format import (
    CHECK_ROW_HEADERS, check_rows, geo_evaluation_text, review_status_text,
)
from app.schemas.export import ExportSectionEnum, FullReport, BrandProfileData


class ExcelExporter(BaseExporter):
    """
    Excel dosyası olarak export eder.

    Sheet'ler (istenen bölümlere göre):
    Özet (yalnız all/SUMMARY) · Marka Profili · Tüm Kelimeler ·
    ADS/SEO/SOCIAL Havuzları + Stratejik · SEO İçerikler ·
    Ads (Metinler/Gruplar/Başlıklar/Açıklamalar/Negatifler) ·
    Sosyal (Fikirler/İçerikler)
    """

    HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    HEADER_FONT = Font(bold=True, color="FFFFFF")
    BORDER = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    def export(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum],
        filepath: str
    ) -> str:
        """Export işlemini yapar."""
        data = self.collect_data(scoring_run_id, sections)

        wb = Workbook()
        default_sheet = wb.active

        # Özet yalnız tam raporda / açıkça istendiğinde (plan v4 tur-4 #6):
        # tekil içerik export'u yalnız o kanalın verisini taşır
        include_summary = (
            ExportSectionEnum.ALL in sections
            or ExportSectionEnum.SUMMARY in sections
        )
        if include_summary:
            default_sheet.title = "Özet"
            self._add_summary_sheet(default_sheet, data)

        if self._should_include(ExportSectionEnum.BRAND_PROFILE, sections):
            self._add_brand_profile_sheets(wb, data.brand_profile)

        if self._should_include(ExportSectionEnum.SCORING, sections) and data.scoring:
            self._add_keywords_sheet(wb, data.scoring)

        if self._should_include(ExportSectionEnum.CHANNELS, sections) and data.channels:
            self._add_channel_sheets(wb, data.channels)

        if self._should_include(ExportSectionEnum.SEO_CONTENT, sections) and data.seo_contents:
            self._add_seo_content_sheet(wb, data.seo_contents)

        if self._should_include(ExportSectionEnum.ADS, sections) and data.ads:
            self._add_ads_sheets(wb, data.ads)

        if self._should_include(ExportSectionEnum.SOCIAL, sections) and data.social:
            self._add_social_sheets(wb, data.social)

        # Özet eklenmediyse boş default sheet'i kaldır (başka sheet varsa)
        if not include_summary and len(wb.sheetnames) > 1:
            wb.remove(default_sheet)

        wb.save(filepath)
        return filepath

    def _style_header(self, ws, row: int, cols: int):
        """Header satırını stillendirir."""
        for col in range(1, cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.fill = self.HEADER_FILL
            cell.font = self.HEADER_FONT
            cell.alignment = Alignment(horizontal='center')

    def _add_summary_sheet(self, ws, data: FullReport):
        """Özet sheet'i."""
        ws.append(['DIGITUS ENGINE RAPORU'])
        ws.append([])
        ws.append(['Metrik', 'Değer'])
        self._style_header(ws, 3, 2)

        if data.summary:
            ws.append(['Scoring Run ID', data.scoring_run_id])
            ws.append(['Tarih', data.generated_at.strftime('%Y-%m-%d %H:%M')])
            ws.append(['Toplam Kelime', data.summary.total_keywords])
            ws.append([])
            ws.append(['Kanal Dağılımı', ''])
            ws.append(['ADS Havuzu', data.summary.ads_count])
            ws.append(['SEO Havuzu', data.summary.seo_count])
            ws.append(['SOCIAL Havuzu', data.summary.social_count])
            ws.append(['Stratejik', data.summary.strategic_count])
            ws.append([])
            ws.append(['Üretilen İçerik', ''])
            ws.append(['SEO+GEO İçerik', data.summary.seo_content_count])
            ws.append(['Reklam Grubu', data.summary.ad_group_count])
            ws.append(['Sosyal İçerik', data.summary.social_content_count])

        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 20

    def _add_keywords_sheet(self, wb: Workbook, scoring):
        """Tüm kelimeler sheet'i."""
        ws = wb.create_sheet("Tüm Kelimeler")

        headers = ['Kelime', 'Hacim', 'Trend 3ay', 'Trend 12ay', 'Rekabet',
                   'ADS Skor', 'SEO Skor', 'SOCIAL Skor',
                   'ADS Rank', 'SEO Rank', 'SOCIAL Rank', 'Birincil', 'Niyet']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for kw in scoring.keywords:
            ws.append([
                _sx(kw.keyword),
                kw.volume,
                kw.trend_3m,
                kw.trend_12m,
                kw.competition,
                kw.ads_score,
                kw.seo_score,
                kw.social_score,
                kw.ads_rank,
                kw.seo_rank,
                kw.social_rank,
                _sx(kw.primary_channel),
                _sx(kw.intent)
            ])

        # Kolon genişlikleri
        ws.column_dimensions['A'].width = 35
        for col in 'BCDEFGHIJKLM':
            ws.column_dimensions[col].width = 12

    def _add_brand_profile_sheets(self, wb: Workbook, brand_profile: Optional[BrandProfileData]):
        """Marka profili ve rakip bilgisi sheetleri."""
        ws = wb.create_sheet("Marka Profili")
        ws.append(['Alan', 'Deger'])
        self._style_header(ws, 1, 2)

        if not brand_profile:
            ws.append(['Durum', 'Profil bulunamadi'])
            ws.column_dimensions['A'].width = 28
            ws.column_dimensions['B'].width = 80
            return

        ws.append(['Durum', _sx(brand_profile.status)])
        ws.append(['Firma URL', _sx(brand_profile.company_url or '')])
        ws.append(['Firma Adi', _sx(brand_profile.company_name or '')])
        ws.append(['Sektor', _sx(brand_profile.sector or '')])
        ws.append(['Hedef Kitle', _sx(brand_profile.target_audience or '')])
        ws.append(['Urunler', _sx(', '.join(brand_profile.products))])
        ws.append(['Hizmetler', _sx(', '.join(brand_profile.services))])
        ws.append(['Kullanim Alanlari', _sx(', '.join(brand_profile.use_cases))])
        ws.append(['Cozulen Problemler', _sx(', '.join(brand_profile.problems_solved))])
        ws.append(['Marka Terimleri', _sx(', '.join(brand_profile.brand_terms))])
        ws.append(['Korunacak Temalar', _sx(', '.join(brand_profile.protected_themes))])
        ws.append(['Dislanan Temalar', _sx(', '.join(brand_profile.exclude_themes))])
        ws.append(['Anchor Textler', _sx(' | '.join(brand_profile.anchor_texts))])
        ws.append(['Hata Mesaji', _sx(brand_profile.error_message or '')])

        if brand_profile.validation_warnings:
            ws.append(['Dogrulama Uyarilari', _sx(' | '.join(brand_profile.validation_warnings))])

        ws.column_dimensions['A'].width = 28
        ws.column_dimensions['B'].width = 100

        src = wb.create_sheet("Profil Kaynaklar")
        src.append(['URL', 'Baslik', 'Durum'])
        self._style_header(src, 1, 3)
        if brand_profile.source_pages:
            for page in brand_profile.source_pages:
                if not isinstance(page, dict):
                    continue
                src.append([_sx(page.get('url', '')), _sx(page.get('title', '')), _sx(page.get('status', ''))])
        else:
            src.append(['', 'Kaynak sayfa kaydi yok', ''])
        src.column_dimensions['A'].width = 60
        src.column_dimensions['B'].width = 50
        src.column_dimensions['C'].width = 12

        comp = wb.create_sheet("Rakipler")
        comp.append(['URL', 'Durum', 'Skor', 'Ozet'])
        self._style_header(comp, 1, 4)
        if brand_profile.competitors:
            for c in brand_profile.competitors:
                comp.append([
                    _sx(c.url or ''),
                    _sx(c.status or ''),
                    c.consistency_score,
                    _sx(c.summary or '')
                ])
        elif brand_profile.competitor_urls:
            for url in brand_profile.competitor_urls:
                comp.append([_sx(url), 'analiz_yok', None, ''])
        else:
            comp.append(['', 'Rakip kaydi yok', None, ''])
        comp.column_dimensions['A'].width = 55
        comp.column_dimensions['B'].width = 20
        comp.column_dimensions['C'].width = 12
        comp.column_dimensions['D'].width = 80

    def _add_channel_sheets(self, wb: Workbook, channels):
        """Kanal havuzları sheetleri (zengin kolonlar — plan v4 §4)."""
        for name, pool in [('ADS Havuzu', channels.ads),
                          ('SEO Havuzu', channels.seo),
                          ('SOCIAL Havuzu', channels.social)]:
            ws = wb.create_sheet(name)

            headers = ['Sıra', 'Kelime', 'Hacim', 'Skor', 'Vektör Yakınlığı',
                       'Çarpım Skoru', 'Niyet', 'Stratejik', 'Etiket']
            ws.append(headers)
            self._style_header(ws, 1, len(headers))

            for i, kw in enumerate(pool.keywords, 1):
                score = kw.ads_score or kw.seo_score or kw.social_score or 0
                ws.append([
                    kw.final_rank or i,
                    _sx(kw.keyword),
                    kw.volume,
                    score,
                    kw.vector_similarity,
                    kw.vector_adjusted_score,
                    _sx(kw.intent),
                    'Evet' if kw.is_strategic else '',
                    _sx(kw.pool_label or ''),
                ])

            ws.column_dimensions['A'].width = 8
            ws.column_dimensions['B'].width = 40
            ws.column_dimensions['C'].width = 10
            ws.column_dimensions['D'].width = 12
            ws.column_dimensions['E'].width = 16
            ws.column_dimensions['F'].width = 14
            ws.column_dimensions['G'].width = 20
            ws.column_dimensions['H'].width = 10
            ws.column_dimensions['I'].width = 20

        # Stratejik
        ws = wb.create_sheet("Stratejik")
        headers = ['Kelime', 'ADS Rank', 'SEO Rank', 'ADS Skor', 'SEO Skor']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for kw in channels.strategic:
            ws.append([_sx(kw.keyword), kw.ads_rank, kw.seo_rank, kw.ads_score, kw.seo_score])

        ws.column_dimensions['A'].width = 40

    def _add_seo_content_sheet(self, wb: Workbook, seo_contents):
        """SEO içerikler sheet'i — TAM alan matrisi (gövde/meta/FAQ/alt-text)."""
        ws = wb.create_sheet("SEO İçerikler")

        headers = ['Kelime', 'Başlık', 'URL', 'Meta Açıklama', 'Giriş Paragrafı',
                   'İçerik Gövdesi', 'FAQ', 'Görsel Alt-Text',
                   'Internal Link', 'External Link',
                   'Kelime Sayısı', 'SEO Skor', 'GEO Skor', 'Combined',
                   'GEO Değerlendirme', 'Yayın Öncesi İnceleme']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for c in seo_contents.contents:
            faq_text = '\n'.join(
                f"S: {f.get('question', '')} | C: {f.get('answer', '')}"
                for f in (c.faq_items or [])
                if isinstance(f, dict)
            )
            alt_text = '\n'.join(c.image_alt_texts or [])
            internal = f"{c.internal_link_anchor or ''} -> {c.internal_link_url or ''}".strip(' ->')
            external = f"{c.external_link_anchor or ''} -> {c.external_link_url or ''}".strip(' ->')
            ws.append([
                _sx(c.keyword),
                _sx(c.title),
                _sx(c.url_suggestion),
                _sx(c.meta_description or ''),
                _sx(c.intro_paragraph or ''),
                _sx(c.body_content or '\n\n'.join(c.body_sections or [])),
                _sx(faq_text),
                _sx(alt_text),
                _sx(internal),
                _sx(external),
                c.word_count,
                c.seo_score,
                c.geo_score,
                c.combined_score,
                _sx(geo_evaluation_text(c.geo_evaluation_source)),
                _sx(review_status_text(c.publish_review)),
            ])

        widths = [30, 45, 28, 45, 50, 90, 50, 35, 30, 30, 12, 10, 10, 10, 30, 45]
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width

        # Madde bazlı, filtrelenebilir kontrol sonuçları (+ manuel notlar)
        checks = wb.create_sheet("SEO Kontroller")
        checks.append(CHECK_ROW_HEADERS)
        self._style_header(checks, 1, len(CHECK_ROW_HEADERS))
        for c in seo_contents.contents:
            for row in check_rows(c):
                checks.append([_sx(v) if isinstance(v, str) else v for v in row])
        checks.auto_filter.ref = checks.dimensions
        checks.freeze_panes = 'A2'
        for i, width in enumerate([28, 10, 9, 24, 45, 20, 10, 18, 14, 60, 18], 1):
            checks.column_dimensions[get_column_letter(i)].width = width

    def _add_ads_sheets(self, wb: Workbook, ads):
        """Ads sheetleri (6 tane). Başlık/Açıklama/Negatif sheet'leri TÜM
        kayıtları taşır; 'Reklam Metinleri' bilinçli bir ÖNİZLEME satırıdır."""
        # Kelime Bazlı Reklamlar: (reklam grubu, hedef kelime) başına bir satır.
        # Açıklamalar gruba aittir; grubun her kelimesi için tekrarlanır.
        # Bu görünüm yeni reklam üretmez, mevcut grup içeriğini kelimeye açar.
        ws = wb.create_sheet("Kelime Bazlı Reklamlar")
        desc_count = max((len(g.descriptions) for g in ads.ad_groups), default=0)
        headers = (['Kelime', 'Reklam Grubu']
                   + [f'Açıklama {i}' for i in range(1, desc_count + 1)]
                   + ['Başlıklar'])
        ws.append(headers)
        self._style_header(ws, 1, len(headers))
        for g in ads.ad_groups:
            descs = [d.description_text for d in g.descriptions]
            descs += [''] * (desc_count - len(descs))
            headline_cell = ' | '.join(h.headline_text for h in g.headlines)
            for kw in g.target_keywords:
                ws.append([_sx(v) for v in [kw, g.group_name, *descs, headline_cell]])
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for i in range(1, len(headers) + 1):
            width = 30 if i <= 2 else (90 if i == len(headers) else 60)
            ws.column_dimensions[get_column_letter(i)].width = width

        # Hazir reklam metinleri (RSA onizleme: ilk 3 baslik + 2 aciklama —
        # tam listeler asagidaki ozel sheet'lerde)
        ws = wb.create_sheet("Reklam Metinleri")
        headers = ['Grup Adı', 'Hedef Kelimeler', 'Başlık Satırı (önizleme)', 'Açıklama Metni (önizleme)']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for g in ads.ad_groups:
            headline_line = ' | '.join([h.headline_text for h in g.headlines[:3]])
            description_text = ' '.join([d.description_text for d in g.descriptions[:2]])
            ws.append([
                _sx(g.group_name),
                _sx(', '.join(g.target_keywords)),
                _sx(headline_line),
                _sx(description_text)
            ])

        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 45
        ws.column_dimensions['C'].width = 90
        ws.column_dimensions['D'].width = 120

        # Reklam Grupları
        ws = wb.create_sheet("Reklam Grupları")
        headers = ['Grup Adı', 'Hedef Kelimeler', 'Başlık Sayısı', 'Açıklama Sayısı', 'Negatif Sayısı']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for g in ads.ad_groups:
            ws.append([
                _sx(g.group_name),
                _sx(', '.join(g.target_keywords)),
                len(g.headlines),
                len(g.descriptions),
                len(g.negative_keywords)
            ])

        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 40

        # Başlıklar
        ws = wb.create_sheet("Başlıklar")
        headers = ['Grup', 'Başlık', 'Tip', 'DKI']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for g in ads.ad_groups:
            for h in g.headlines:
                ws.append([_sx(g.group_name), _sx(h.headline_text), _sx(h.headline_type), '✔' if h.is_dki else ''])

        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 35

        # Açıklamalar
        ws = wb.create_sheet("Açıklamalar")
        headers = ['Grup', 'Açıklama', 'Tip']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for g in ads.ad_groups:
            for d in g.descriptions:
                ws.append([_sx(g.group_name), _sx(d.description_text), _sx(d.description_type)])

        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 80

        # Negatifler
        ws = wb.create_sheet("Negatifler")
        headers = ['Grup', 'Kelime', 'Eşleme', 'Sebep']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for g in ads.ad_groups:
            for n in g.negative_keywords:
                ws.append([_sx(g.group_name), _sx(n.keyword), _sx(n.match_type), _sx(n.reason)])

        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 25
        ws.column_dimensions['D'].width = 40

    def _add_social_sheets(self, wb: Workbook, social):
        """Sosyal medya sheetleri — TAM alan matrisi (kesme yok, plan §8)."""
        from app.exporters.social_format import (
            format_payload_text, duration_requested_text, duration_status_text,
        )

        # Brief'ler — grup başlığı, oluşturulma tarihi, kelimeler
        ws = wb.create_sheet("Sosyal Briefler")
        headers = ['Brief', 'Oluşturulma', 'Kelimeler']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))
        for g in social.brief_groups:
            ws.append([
                _sx(g.label),
                g.created_at.strftime('%Y-%m-%d %H:%M') if g.created_at else '',
                _sx(', '.join(k.keyword for k in g.keywords)),
            ])
        ws.column_dimensions['A'].width = 40
        ws.column_dimensions['C'].width = 50

        # Kategoriler & Fikirler
        ws = wb.create_sheet("Sosyal Fikirler")
        headers = ['Başlık', 'Platform', 'Format', 'Ana Kelime', 'İstenen Süre',
                    'Trend Alignment', 'Seçildi']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for i in social.ideas:
            ws.append([
                _sx(i.get('title', '')),
                _sx(i.get('platform', '')),
                _sx(i.get('format', '')),
                _sx(i.get('keyword') or ''),
                _sx(duration_requested_text(i.get('duration_min_sec'), i.get('duration_max_sec'))),
                i.get('trend_alignment', 0),
                '✔' if i.get('is_selected') else ''
            ])

        ws.column_dimensions['A'].width = 40
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 15

        # İçerikler — brief grubu, tüm hook'lar, tam caption/scenario,
        # görsel/video önerileri, CTA, tüm hashtag'ler, platform notları,
        # süre (istenen/gerçek/durum), uyarılar, format detayı
        ws = wb.create_sheet("Sosyal İçerikler")
        headers = ['Brief', 'Fikir', 'Platform', 'Format', 'Ana Kelime', "Hook'lar",
                   'Caption', 'Senaryo', 'Görsel Önerisi', 'Video Konsepti', 'CTA',
                   'Hashtagler', 'Sektör Paylaşım Önerisi', 'Platform Notları',
                   'İstenen Süre', 'Gerçek Süre', 'Süre Durumu', 'Uyarılar',
                   'Format Detayı']
        ws.append(headers)
        self._style_header(ws, 1, len(headers))

        for g in social.brief_groups:
            for c in g.contents:
                hooks_text = '\n'.join(
                    f"[{h.style}] {h.text}" if h.style else h.text for h in c.hooks
                )
                ws.append([
                    _sx(g.label),
                    _sx(c.idea_title),
                    _sx(c.platform),
                    _sx(c.content_format),
                    _sx(c.idea_keyword or ''),
                    _sx(hooks_text),
                    _sx(c.caption or ''),
                    _sx(c.scenario or ''),
                    _sx(c.visual_suggestion or ''),
                    _sx(c.video_concept or ''),
                    _sx(c.cta_text or ''),
                    _sx(' '.join(f'#{t.lstrip("#")}' for t in c.hashtags)),
                    _sx(c.industry_posting_suggestion or ''),
                    _sx(c.platform_notes or ''),
                    _sx(duration_requested_text(c.duration_min_sec, c.duration_max_sec)),
                    f'{c.actual_duration_sec} sn' if c.actual_duration_sec is not None else '-',
                    _sx(duration_status_text(c.duration_status)),
                    _sx('; '.join(c.validation_warnings) if c.validation_warnings else ''),
                    _sx(format_payload_text(c.format_payload)),
                ])

        widths = [30, 35, 14, 12, 20, 55, 70, 70, 40, 40, 30, 45, 40, 40, 16, 14, 16, 40, 55]
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
