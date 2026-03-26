"""
SEO Compliance Checker - 11 Kriter.
Roadmap2.md - Bölüm 6

Programatik kontrol yapar, AI gerektirmez.
"""
from typing import Dict, Any, List, Optional
from decimal import Decimal
import re

from app.core.constants import SEO_COMPLIANCE_CRITERIA_V2


class SEOComplianceChecker:
    """
    Programatik SEO uyumluluk kontrolü.
    11 kriteri kontrol eder ve sonuç döndürür.
    """

    # Türkçe karakter → ASCII normalizasyon tablosu.
    # Keyword input'u (örn. "kivircik") ile AI çıktısı (örn. "Kıvırcık")
    # arasındaki karakter uyumsuzluğunu önler.
    _TR_CHAR_MAP = str.maketrans({
        'ı': 'i', 'İ': 'i',
        'ş': 's', 'Ş': 's',
        'ç': 'c', 'Ç': 'c',
        'ö': 'o', 'Ö': 'o',
        'ü': 'u', 'Ü': 'u',
        'ğ': 'g', 'Ğ': 'g',
    })

    # Kriter ağırlıkları
    CRITERIA_WEIGHTS = {
        "title_has_keyword": {"weight": 1.0, "importance": "critical"},
        "title_length_ok": {"weight": 1.0, "importance": "critical"},
        "url_has_keyword": {"weight": 0.8, "importance": "high"},
        "intro_keyword_count": {"weight": 1.0, "importance": "critical"},
        "word_count_in_range": {"weight": 0.8, "importance": "high"},
        "subheading_count_ok": {"weight": 0.6, "importance": "medium"},
        "subheadings_have_kw": {"weight": 0.6, "importance": "medium"},
        "has_internal_link": {"weight": 0.5, "importance": "medium"},
        "has_external_link": {"weight": 0.5, "importance": "medium"},
        "has_bullet_list": {"weight": 0.4, "importance": "low"},
        "sentences_readable": {"weight": 0.6, "importance": "medium"},
    }
    
    def check(
        self,
        content: Dict[str, Any],
        keyword: str,
        word_count_min: int = 300,
        word_count_max: int = 450
    ) -> Dict[str, Any]:
        """
        11 SEO kriterini kontrol eder.
        
        Args:
            content: İçerik verisi (ContentStructure formatında)
            keyword: Hedef anahtar kelime
            word_count_min: Minimum kelime sayısı
            word_count_max: Maximum kelime sayısı
            
        Returns:
            SEO uyumluluk raporu
        """
        checks = []
        results = {}

        keyword_lower = self._normalize_tr(keyword)

        # 1. Başlık Keyword
        title = content.get('title', '')
        title_has_kw = keyword_lower in self._normalize_tr(title)
        results['title_has_keyword'] = title_has_kw
        checks.append(self._create_check_item(
            "title_has_keyword",
            "pass" if title_has_kw else "fail",
            f"Keyword {'bulundu' if title_has_kw else 'bulunamadı'}: '{keyword}'",
            "critical"
        ))
        
        # 2. Başlık Uzunluğu
        title_length = len(title)
        title_length_ok = title_length <= 70
        results['title_length_ok'] = title_length_ok
        checks.append(self._create_check_item(
            "title_length_ok",
            "pass" if title_length_ok else "fail",
            f"Başlık uzunluğu: {title_length} karakter (max 70)",
            "critical"
        ))
        
        # 3. URL Keyword
        url = content.get('url_suggestion', '')
        url_norm = self._normalize_tr(url)
        url_keyword = keyword_lower.replace(' ', '-')
        url_has_kw = url_keyword in url_norm or keyword_lower.replace(' ', '') in url_norm.replace('-', '')
        results['url_has_keyword'] = url_has_kw
        checks.append(self._create_check_item(
            "url_has_keyword",
            "pass" if url_has_kw else "fail",
            f"URL: {url}",
            "high"
        ))
        
        # 4. Giriş Paragrafında Keyword Sayısı
        intro = content.get('intro_paragraph', '')
        intro_kw_count = self._normalize_tr(intro).count(keyword_lower)
        intro_kw_ok = intro_kw_count >= 2
        results['intro_keyword_count'] = intro_kw_count
        checks.append(self._create_check_item(
            "intro_keyword_count",
            "pass" if intro_kw_ok else ("partial" if intro_kw_count == 1 else "fail"),
            f"Giriş paragrafında keyword: {intro_kw_count} kez (min 2)",
            "critical"
        ))
        
        # 5. Kelime Sayısı
        word_count = content.get('word_count', 0)
        word_count_ok = word_count_min <= word_count <= word_count_max
        results['word_count_in_range'] = word_count_ok
        checks.append(self._create_check_item(
            "word_count_in_range",
            "pass" if word_count_ok else "fail",
            f"Kelime sayısı: {word_count} ({word_count_min}-{word_count_max} arası)",
            "high"
        ))
        
        # 6. Alt Başlık Sayısı
        subheadings = content.get('subheadings', [])
        subheading_count = len(subheadings)
        subheading_ok = subheading_count >= 3
        results['subheading_count_ok'] = subheading_ok
        checks.append(self._create_check_item(
            "subheading_count_ok",
            "pass" if subheading_ok else ("partial" if subheading_count >= 2 else "fail"),
            f"Alt başlık sayısı: {subheading_count} (min 3)",
            "medium"
        ))
        
        # 7. Alt Başlıklarda Keyword
        subheadings_have_kw = any(keyword_lower in self._normalize_tr(sh) for sh in subheadings)
        results['subheadings_have_kw'] = subheadings_have_kw
        checks.append(self._create_check_item(
            "subheadings_have_kw",
            "pass" if subheadings_have_kw else "fail",
            f"Alt başlıklarda keyword {'var' if subheadings_have_kw else 'yok'}",
            "medium"
        ))
        
        # 8. Internal Link
        internal_link = content.get('internal_link_anchor') or content.get('internal_link_suggestion')
        has_internal = bool(internal_link)
        results['has_internal_link'] = has_internal
        checks.append(self._create_check_item(
            "has_internal_link",
            "pass" if has_internal else "fail",
            f"Internal link {'var' if has_internal else 'yok'}",
            "medium"
        ))
        
        # 9. External Link
        external_link = content.get('external_link_url')
        has_external = bool(external_link)
        results['has_external_link'] = has_external
        checks.append(self._create_check_item(
            "has_external_link",
            "pass" if has_external else "fail",
            f"External link {'var' if has_external else 'yok'}",
            "medium"
        ))
        
        # 10. Bullet List
        bullet_points = content.get('bullet_points', [])
        has_bullets = len(bullet_points) > 0
        results['has_bullet_list'] = has_bullets
        checks.append(self._create_check_item(
            "has_bullet_list",
            "pass" if has_bullets else "fail",
            f"Bullet list: {len(bullet_points)} madde",
            "low"
        ))
        
        # 11. Okunabilirlik (ortalama cümle uzunluğu)
        full_content = self._get_full_content(content)
        sentences = self._split_sentences(full_content)
        avg_sentence_length = self._calculate_avg_sentence_length(sentences)
        readable = avg_sentence_length <= 20
        results['sentences_readable'] = readable
        checks.append(self._create_check_item(
            "sentences_readable",
            "pass" if readable else ("partial" if avg_sentence_length <= 25 else "fail"),
            f"Ortalama cümle uzunluğu: {avg_sentence_length:.1f} kelime (max 20)",
            "medium"
        ))
        
        # Skor hesaplama
        total_passed = sum(1 for c in checks if c['status'] == 'pass')
        partial_count = sum(0.5 for c in checks if c['status'] == 'partial')
        score = (total_passed + partial_count) / len(checks)
        
        # İyileştirme notları
        improvement_notes = self._generate_improvement_notes(checks)
        
        return {
            **results,
            'checks': checks,
            'total_passed': total_passed,
            'total_checks': len(checks),
            'score': round(score, 2),
            'improvement_notes': improvement_notes
        }
    
    def _normalize_tr(self, text: str) -> str:
        """Türkçe karakterleri ASCII eşdeğerlerine çevirir ve küçük harfe dönüştürür."""
        return text.lower().translate(self._TR_CHAR_MAP)

    def _create_check_item(
        self,
        criterion: str,
        status: str,
        details: str,
        importance: str
    ) -> Dict[str, Any]:
        """Check item oluşturur."""
        return {
            'criterion': criterion,
            'status': status,
            'details': details,
            'importance': importance
        }
    
    def _get_full_content(self, content: Dict[str, Any]) -> str:
        """Tüm içeriği birleştirir."""
        parts = []
        parts.append(content.get('intro_paragraph', ''))
        for section in content.get('body_sections', []):
            parts.append(section if isinstance(section, str) else str(section))
        return ' '.join(parts)
    
    def _split_sentences(self, text: str) -> List[str]:
        """Metni cümlelere ayırır."""
        # Basit cümle ayırma
        sentences = re.split(r'[.!?]+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _calculate_avg_sentence_length(self, sentences: List[str]) -> float:
        """Ortalama cümle uzunluğunu hesaplar."""
        if not sentences:
            return 0
        
        word_counts = [len(s.split()) for s in sentences]
        return sum(word_counts) / len(word_counts)
    
    def _generate_improvement_notes(self, checks: List[Dict[str, Any]]) -> str:
        """İyileştirme notları üretir."""
        failed_critical = [c for c in checks if c['status'] == 'fail' and c['importance'] == 'critical']
        failed_high = [c for c in checks if c['status'] == 'fail' and c['importance'] == 'high']
        
        notes = []
        
        if failed_critical:
            criteria_names = [c['criterion'] for c in failed_critical]
            notes.append(f"KRİTİK: {', '.join(criteria_names)} düzeltilmeli.")
        
        if failed_high:
            criteria_names = [c['criterion'] for c in failed_high]
            notes.append(f"Yüksek öncelik: {', '.join(criteria_names)} iyileştirilmeli.")
        
        return ' '.join(notes) if notes else "SEO uyumluluğu iyi durumda."


# Backward compatibility için eski sınıf adı
SEOChecker = SEOComplianceChecker
