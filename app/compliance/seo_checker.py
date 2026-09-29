"""
SEO Compliance Checker - 11 temel + 6 şirket-checklist kriteri.
Roadmap2.md - Bölüm 6 + şirket SEO/GEO checklist entegrasyonu.

Programatik kontrol yapar, AI gerektirmez.
Skor, checklist öncelikleriyle hizalanmış AĞIRLIKLI ortalamadır
(100→1.0 ... 60→0.6; pass=1, partial=0.5, fail=0).
"""
from datetime import datetime
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

    # Kriter ağırlıkları — şirket checklist öncelikleriyle hizalı
    # (P100→1.0, P95→0.95, P90→0.9, P85→0.85, P80→0.8, P75→0.75, P70→0.7, P60→0.6).
    # Skor bu ağırlıklarla hesaplanır: Σ(weight×puan)/Σweight.
    CRITERIA_WEIGHTS = {
        # Temel SEO kriterleri
        "title_has_keyword": {"weight": 1.0, "importance": "critical"},
        "title_length_ok": {"weight": 1.0, "importance": "critical"},
        "url_has_keyword": {"weight": 0.8, "importance": "high"},
        "intro_keyword_count": {"weight": 1.0, "importance": "critical"},
        "word_count_in_range": {"weight": 0.8, "importance": "high"},
        "subheading_count_ok": {"weight": 0.6, "importance": "medium"},
        "subheadings_have_kw": {"weight": 0.6, "importance": "medium"},
        "has_internal_link": {"weight": 0.5, "importance": "medium"},
        "has_external_link": {"weight": 0.5, "importance": "medium"},
        "has_bullet_list": {"weight": 0.75, "importance": "medium"},  # P75 yapılandırma
        "sentences_readable": {"weight": 0.6, "importance": "medium"},
        # Şirket checklist kriterleri
        "paragraphs_short": {"weight": 1.0, "importance": "critical"},        # P100
        "subheadings_are_questions": {"weight": 0.95, "importance": "critical"},  # P95
        "has_faq_items": {"weight": 0.9, "importance": "high"},               # P90
        "has_steps_or_table": {"weight": 0.75, "importance": "medium"},       # P75
        "mentions_current_year": {"weight": 0.7, "importance": "medium"},     # P70
        "has_alt_texts": {"weight": 0.6, "importance": "low"},                # P60
    }

    # Soru-başlık tespiti: "?" veya Türkçe soru kalıpları
    _QUESTION_PATTERN = re.compile(
        r"\?|(?:\b(?:nedir|nelerdir|nasil|neden|nicin|ne kadar|hangi|kimler|"
        r"ne zaman|mi|mu|mudur|midir)\b)",
        re.IGNORECASE,
    )
    # Adım adım / markdown tablo desenleri (P75)
    _STEP_PATTERN = re.compile(r"(?:^|\n)\s*(?:1[.)]\s|adim\s*1)", re.IGNORECASE)
    _TABLE_PATTERN = re.compile(r"\|.+\|\s*\n\s*\|[\s:-]+\|")
    
    def check(
        self,
        content: Dict[str, Any],
        keyword: str,
        word_count_min: int = 500,
        word_count_max: int = 600,
        current_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        11 temel + 6 şirket-checklist kriterini kontrol eder.

        Args:
            content: İçerik verisi (ContentStructure formatında)
            keyword: Hedef anahtar kelime
            word_count_min: Minimum kelime sayısı
            word_count_max: Maximum kelime sayısı
            current_year: Güncellik kriteri için yıl (None → bugünün yılı;
                testlerde deterministiklik için parametreli)

        Returns:
            SEO uyumluluk raporu (score = ağırlıklı ortalama)
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
        
        # 5. Kelime Sayısı — content dict'indeki (AI beyanı olabilecek) değere
        # GÜVENİLMEZ; gerçek metinden (intro + body_sections) sayılır (codex)
        word_count = len(self._get_full_content(content).split())
        declared = content.get('word_count')
        word_count_ok = word_count_min <= word_count <= word_count_max
        results['word_count_in_range'] = word_count_ok
        detail = f"Kelime sayısı (gerçek metin): {word_count} ({word_count_min}-{word_count_max} arası)"
        if declared is not None and declared != word_count:
            detail += f"; AI beyanı {declared} idi"
        checks.append(self._create_check_item(
            "word_count_in_range",
            "pass" if word_count_ok else "fail",
            detail,
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
        
        # ==================== ŞİRKET CHECKLIST KRİTERLERİ ====================

        # 12. Kısa paragraflar (P100): tüm paragraflar ≤5 cümle, ort ≤3
        paragraphs = self._split_paragraphs(content)
        para_status, para_detail = self._check_paragraphs_short(paragraphs)
        results['paragraphs_short'] = para_status == 'pass'
        checks.append(self._create_check_item(
            "paragraphs_short", para_status, para_detail, "critical"
        ))

        # 13. Soru formatında alt başlıklar (P95): ≥%60 pass, ≥%40 partial
        question_ratio = self._question_heading_ratio(subheadings)
        q_status = 'pass' if question_ratio >= 0.6 else ('partial' if question_ratio >= 0.4 else 'fail')
        results['subheadings_are_questions'] = q_status == 'pass'
        checks.append(self._create_check_item(
            "subheadings_are_questions", q_status,
            f"Soru formatında alt başlık oranı: %{question_ratio * 100:.0f} (hedef ≥%60)",
            "critical"
        ))

        # 14. FAQ çiftleri (P90): yalnız GEÇERLİ çift sayılır — soru ve cevap
        # boş değil, cevap 1-2 cümle. ≥3 geçerli pass, 1-2 geçerli partial.
        # Bu veri FAQ schema'ya HAZIRDIR; schema uygulanmış değildir.
        faq_items = content.get('faq_items') or []
        faq_status, faq_detail = self._check_faq_items(faq_items)
        results['has_faq_items'] = faq_status == 'pass'
        checks.append(self._create_check_item(
            "has_faq_items", faq_status, faq_detail, "high"
        ))

        # 15. Adım adım veya tablo (P75)
        body_text = '\n'.join(
            s if isinstance(s, str) else str(s) for s in content.get('body_sections', [])
        )
        has_steps = bool(self._STEP_PATTERN.search(self._normalize_tr(body_text)))
        has_table = bool(self._TABLE_PATTERN.search(body_text))
        steps_ok = has_steps or has_table
        results['has_steps_or_table'] = steps_ok
        checks.append(self._create_check_item(
            "has_steps_or_table",
            "pass" if steps_ok else "fail",
            f"Adım adım anlatım: {'var' if has_steps else 'yok'}, tablo: {'var' if has_table else 'yok'}",
            "medium"
        ))

        # 16. Güncel yıl (P70): güncel veya bir önceki yıl metinde geçmeli
        year = current_year or datetime.now().year
        year_text = f"{full_content} {title} {intro}"
        mentions_year = str(year) in year_text or str(year - 1) in year_text
        results['mentions_current_year'] = mentions_year
        checks.append(self._create_check_item(
            "mentions_current_year",
            "pass" if mentions_year else "fail",
            f"Güncel yıl ({year}) metinde {'geçiyor' if mentions_year else 'geçmiyor'}",
            "medium"
        ))

        # 17. Görsel alt metni ÖNERİLERİ (P60): geçerli = boş olmayan, hedef
        # anahtar kelimeyi içeren ve kelimenin ötesinde açıklama taşıyan metin.
        # ≥2 geçerli pass; 1 geçerli ya da yalnız kusurlu öneri partial.
        # Öneridir — gerçek görsele uygulanmış alt etiketi değildir.
        alt_texts = content.get('image_alt_texts') or []
        alt_status, alt_detail = self._check_alt_texts(alt_texts, keyword_lower)
        results['has_alt_texts'] = alt_status == 'pass'
        checks.append(self._create_check_item(
            "has_alt_texts", alt_status, alt_detail, "low"
        ))

        # Skor hesaplama — checklist öncelikleriyle AĞIRLIKLI ortalama
        _STATUS_POINTS = {'pass': 1.0, 'partial': 0.5, 'fail': 0.0}
        weighted_sum = 0.0
        weight_total = 0.0
        for c in checks:
            weight = self.CRITERIA_WEIGHTS.get(c['criterion'], {}).get('weight', 0.5)
            weighted_sum += weight * _STATUS_POINTS.get(c['status'], 0.0)
            weight_total += weight
        total_passed = sum(1 for c in checks if c['status'] == 'pass')
        score = weighted_sum / weight_total if weight_total else 0.0
        
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
        """Türkçe karakterleri ASCII eşdeğerlerine çevirir ve küçük harfe dönüştürür.

        Önce çeviri, sonra lower: 'İ'.lower() 'i̇' (i + birleşik nokta) verir
        ve tablodaki 'İ' eşlemesi hiç tetiklenmezdi."""
        return text.translate(self._TR_CHAR_MAP).lower()

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
    
    def _split_paragraphs(self, content: Dict[str, Any]) -> List[str]:
        """Intro + body_sections'ı paragraflara böler (boş satırla; boş satır
        yoksa her bölüm tek paragraf sayılır)."""
        blocks = [content.get('intro_paragraph', '')]
        for section in content.get('body_sections', []):
            blocks.append(section if isinstance(section, str) else str(section))
        paragraphs: List[str] = []
        for block in blocks:
            for para in re.split(r'\n\s*\n', block):
                para = para.strip()
                if para:
                    paragraphs.append(para)
        return paragraphs

    def _check_paragraphs_short(self, paragraphs: List[str]):
        """P100: her paragraf ≤5 cümle ve ort ≤3 → pass; ≥%80 uyum → partial."""
        if not paragraphs:
            return 'fail', 'Paragraf bulunamadı'
        sentence_counts = [len(self._split_sentences(p)) for p in paragraphs]
        ok_count = sum(1 for n in sentence_counts if n <= 5)
        avg = sum(sentence_counts) / len(sentence_counts)
        detail = (
            f"{len(paragraphs)} paragraf; ort {avg:.1f} cümle, "
            f"{ok_count}/{len(paragraphs)} paragraf ≤5 cümle"
        )
        if ok_count == len(paragraphs) and avg <= 3:
            return 'pass', detail
        if ok_count / len(paragraphs) >= 0.8:
            return 'partial', detail
        return 'fail', detail

    def _check_faq_items(self, faq_items: List[Any]):
        """P90: soru/cevap boş değil ve cevap 1-2 cümle olan çiftleri sayar."""
        valid = empty = long_answer = 0
        for item in faq_items:
            if not isinstance(item, dict):
                empty += 1
                continue
            question = str(item.get('question') or '').strip()
            answer = str(item.get('answer') or '').strip()
            if not question or not answer:
                empty += 1
                continue
            if not 1 <= len(self._split_sentences(answer)) <= 2:
                long_answer += 1
                continue
            valid += 1
        status = 'pass' if valid >= 3 else ('partial' if valid >= 1 else 'fail')
        detail = (
            f"Geçerli FAQ çifti: {valid}/{len(faq_items)} (hedef ≥3; soru+cevap dolu, "
            f"cevap 1-2 cümle)"
        )
        if empty:
            detail += f"; boş soru/cevap: {empty}"
        if long_answer:
            detail += f"; 2 cümleyi aşan cevap: {long_answer}"
        detail += ". Schema'ya hazır veridir, schema uygulanmadı"
        return status, detail

    def _check_alt_texts(self, alt_texts: List[Any], keyword_norm: str):
        """P60: boş olmayan, keyword içeren, açıklayıcı alt metni önerilerini sayar."""
        valid = empty = no_keyword = 0
        keyword_words = len(keyword_norm.split())
        for alt in alt_texts:
            text = alt.strip() if isinstance(alt, str) else ''
            if not text:
                empty += 1
                continue
            norm = self._normalize_tr(text)
            # Yalnız keyword'ün kendisi açıklama sayılmaz
            if keyword_norm not in norm or len(norm.split()) <= keyword_words:
                no_keyword += 1
                continue
            valid += 1
        non_empty = len(alt_texts) - empty
        if valid >= 2:
            status = 'pass'
        elif valid >= 1 or non_empty >= 1:
            status = 'partial'
        else:
            status = 'fail'
        detail = (
            f"Görsel alt metni önerisi (gerçek görsele uygulanmadı): "
            f"geçerli {valid}/{len(alt_texts)} (hedef ≥2; dolu, anahtar kelimeli, açıklayıcı)"
        )
        if empty:
            detail += f"; boş: {empty}"
        if no_keyword:
            detail += f"; anahtar kelimesiz/açıklamasız: {no_keyword}"
        return status, detail

    def _question_heading_ratio(self, subheadings: List[str]) -> float:
        """Soru formatındaki alt başlık oranı (normalize edilmiş metinde)."""
        if not subheadings:
            return 0.0
        question_count = sum(
            1 for sh in subheadings
            if self._QUESTION_PATTERN.search(self._normalize_tr(sh))
        )
        return question_count / len(subheadings)

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
