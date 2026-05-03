"""
CSV Exporter Module.

CSV formatında export eder.
Her bölüm için ayrı CSV dosyası, ZIP bundle olarak.
Türkçe karakter desteği: utf-8-sig (BOM ile Excel uyumu).
"""
import csv
import zipfile
import tempfile
import os
from typing import List
from datetime import datetime

from app.exporters.base_exporter import BaseExporter
from app.schemas.export import ExportSectionEnum, FullReport


class CsvExporter(BaseExporter):
    """
    CSV dosyaları olarak export eder.
    
    Her section için ayrı CSV dosyası oluşturur.
    Tüm dosyalar ZIP olarak paketlenir.
    UTF-8-sig encoding (BOM) ile Excel uyumu sağlanır.
    """
    
    def export(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum],
        filepath: str
    ) -> str:
        """Export işlemini yapar."""
        data = self.collect_data(scoring_run_id, sections)
        
        # Geçici klasör
        temp_dir = tempfile.mkdtemp()
        csv_files = []
        
        try:
            # Her bölüm için CSV
            if self._should_include(ExportSectionEnum.SUMMARY, sections) and data.summary:
                csv_files.append(self._write_summary_csv(temp_dir, data))
            
            if self._should_include(ExportSectionEnum.SCORING, sections) and data.scoring:
                csv_files.append(self._write_scoring_csv(temp_dir, data.scoring))
            
            if self._should_include(ExportSectionEnum.CHANNELS, sections) and data.channels:
                csv_files.extend(self._write_channels_csv(temp_dir, data.channels))
            
            if self._should_include(ExportSectionEnum.SEO_CONTENT, sections) and data.seo_contents:
                csv_files.append(self._write_seo_csv(temp_dir, data.seo_contents))
            
            if self._should_include(ExportSectionEnum.ADS, sections) and data.ads:
                csv_files.extend(self._write_ads_csv(temp_dir, data.ads))
            
            if self._should_include(ExportSectionEnum.SOCIAL, sections) and data.social:
                csv_files.extend(self._write_social_csv(temp_dir, data.social))
            
            # ZIP oluştur
            with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for csv_file in csv_files:
                    if csv_file and os.path.exists(csv_file):
                        zipf.write(csv_file, os.path.basename(csv_file))
            
            return filepath
            
        finally:
            # Temizlik
            for f in csv_files:
                if f and os.path.exists(f):
                    os.remove(f)
            os.rmdir(temp_dir)
    
    def _write_csv(self, filepath: str, headers: List[str], rows: List[List]):
        """CSV dosyası yazar (UTF-8 BOM)."""
        with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return filepath
    
    def _write_summary_csv(self, temp_dir: str, data: FullReport) -> str:
        """Özet CSV."""
        filepath = os.path.join(temp_dir, 'ozet.csv')
        summary = data.summary
        
        headers = ['Metrik', 'Değer']
        rows = [
            ['Scoring Run ID', data.scoring_run_id],
            ['Tarih', data.generated_at.strftime('%Y-%m-%d %H:%M')],
            ['Toplam Kelime', summary.total_keywords],
            ['ADS Havuzu', summary.ads_count],
            ['SEO Havuzu', summary.seo_count],
            ['SOCIAL Havuzu', summary.social_count],
            ['Stratejik', summary.strategic_count],
            ['SEO+GEO İçerik', summary.seo_content_count],
            ['Reklam Grubu', summary.ad_group_count],
            ['Sosyal İçerik', summary.social_content_count],
        ]
        
        return self._write_csv(filepath, headers, rows)
    
    def _write_scoring_csv(self, temp_dir: str, scoring) -> str:
        """Skorlama CSV."""
        filepath = os.path.join(temp_dir, 'tum_kelimeler.csv')
        
        headers = ['Kelime', 'Hacim', 'Trend 3ay', 'Trend 12ay', 'Rekabet',
                   'ADS Skor', 'SEO Skor', 'SOCIAL Skor',
                   'ADS Rank', 'SEO Rank', 'SOCIAL Rank', 'Birincil', 'Niyet']
        
        rows = []
        for kw in scoring.keywords:
            rows.append([
                kw.keyword, kw.volume, kw.trend_3m, kw.trend_12m, kw.competition,
                kw.ads_score, kw.seo_score, kw.social_score,
                kw.ads_rank, kw.seo_rank, kw.social_rank,
                kw.primary_channel, kw.intent
            ])
        
        return self._write_csv(filepath, headers, rows)
    
    def _write_channels_csv(self, temp_dir: str, channels) -> List[str]:
        """Kanal havuzları CSV."""
        files = []
        
        for name, pool in [('ads_havuzu', channels.ads),
                          ('seo_havuzu', channels.seo),
                          ('social_havuzu', channels.social)]:
            filepath = os.path.join(temp_dir, f'{name}.csv')
            headers = ['Sıra', 'Kelime', 'Skor', 'Vektör Yakınlığı', 'Çarpım Skoru', 'Niyet']
            
            rows = []
            for i, kw in enumerate(pool.keywords, 1):
                score = kw.ads_score or kw.seo_score or kw.social_score or 0
                rows.append([
                    i,
                    kw.keyword,
                    score,
                    kw.vector_similarity,
                    kw.vector_adjusted_score,
                    kw.intent,
                ])
            
            files.append(self._write_csv(filepath, headers, rows))
        
        # Stratejik
        filepath = os.path.join(temp_dir, 'stratejik.csv')
        headers = ['Kelime', 'ADS Rank', 'SEO Rank', 'ADS Skor', 'SEO Skor']
        rows = [[kw.keyword, kw.ads_rank, kw.seo_rank, kw.ads_score, kw.seo_score]
                for kw in channels.strategic]
        files.append(self._write_csv(filepath, headers, rows))
        
        return files
    
    def _write_seo_csv(self, temp_dir: str, seo_contents) -> str:
        """SEO içerikler CSV."""
        filepath = os.path.join(temp_dir, 'seo_icerikler.csv')
        
        headers = ['Kelime', 'Başlık', 'URL', 'Kelime Sayısı', 'SEO Skor', 'GEO Skor', 'Combined']
        rows = [[c.keyword, c.title, c.url_suggestion, c.word_count,
                 c.seo_score, c.geo_score, c.combined_score]
                for c in seo_contents.contents]
        
        return self._write_csv(filepath, headers, rows)
    
    def _write_ads_csv(self, temp_dir: str, ads) -> List[str]:
        """Ads CSV'leri."""
        files = []
        
        # Gruplar
        filepath = os.path.join(temp_dir, 'reklam_gruplari.csv')
        headers = ['Grup Adı', 'Hedef Kelimeler', 'Başlık Sayısı', 'Açıklama Sayısı', 'Negatif Sayısı']
        rows = [[g.group_name, ', '.join(g.target_keywords[:3]),
                 len(g.headlines), len(g.descriptions), len(g.negative_keywords)]
                for g in ads.ad_groups]
        files.append(self._write_csv(filepath, headers, rows))
        
        # Başlıklar
        filepath = os.path.join(temp_dir, 'basliklar.csv')
        headers = ['Grup', 'Başlık', 'Tip', 'DKI']
        rows = []
        for g in ads.ad_groups:
            for h in g.headlines:
                rows.append([g.group_name, h.headline_text, h.headline_type, 'Evet' if h.is_dki else ''])
        files.append(self._write_csv(filepath, headers, rows))
        
        # Açıklamalar
        filepath = os.path.join(temp_dir, 'aciklamalar.csv')
        headers = ['Grup', 'Açıklama', 'Tip']
        rows = []
        for g in ads.ad_groups:
            for d in g.descriptions:
                rows.append([g.group_name, d.description_text, d.description_type])
        files.append(self._write_csv(filepath, headers, rows))
        
        # Negatifler
        filepath = os.path.join(temp_dir, 'negatifler.csv')
        headers = ['Grup', 'Kelime', 'Eşleme', 'Sebep']
        rows = []
        for g in ads.ad_groups:
            for n in g.negative_keywords:
                rows.append([g.group_name, n.keyword, n.match_type, n.reason])
        files.append(self._write_csv(filepath, headers, rows))
        
        return files
    
    def _write_social_csv(self, temp_dir: str, social) -> List[str]:
        """Sosyal medya CSV'leri."""
        files = []
        
        # Fikirler
        filepath = os.path.join(temp_dir, 'sosyal_fikirler.csv')
        headers = ['Başlık', 'Platform', 'Format', 'Trend Alignment', 'Seçildi']
        rows = [[i.get('title', ''), i.get('platform', ''), i.get('format', ''),
                 i.get('trend_alignment', 0), 'Evet' if i.get('is_selected') else '']
                for i in social.ideas]
        files.append(self._write_csv(filepath, headers, rows))
        
        # İçerikler
        filepath = os.path.join(temp_dir, 'sosyal_icerikler.csv')
        headers = ['Fikir', 'Platform', 'Hook 1', 'Caption', 'Hashtagler', 'CTA']
        rows = []
        for c in social.contents:
            hook1 = c.hooks[0].text if c.hooks else ''
            rows.append([c.idea_title, c.platform, hook1,
                         c.caption[:200] if c.caption else '',
                         ' '.join([f'#{t}' for t in c.hashtags[:5]]),
                         c.cta_text or ''])
        files.append(self._write_csv(filepath, headers, rows))
        
        return files
