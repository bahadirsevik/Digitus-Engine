"""
SEO compliance checker.
"""
from typing import Dict, Any, List
from decimal import Decimal

from app.core.constants import SEO_COMPLIANCE_CRITERIA


class SEOChecker:
    """SEO uyumluluk kontrolcüsü."""
    
    def check_content(
        self,
        keyword: str,
        content: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        İçeriğin SEO uyumluluğunu kontrol eder.
        
        Args:
            keyword: Hedef anahtar kelime
            content: Kontrol edilecek içerik
        
        Returns:
            Uyumluluk raporu
        """
        checks = []
        passed = 0
        
        # 1. Keyword in title
        title = content.get('title', '')
        if keyword.lower() in title.lower():
            checks.append({'criteria': 'keyword_in_title', 'status': 'pass'})
            passed += 1
        else:
            checks.append({'criteria': 'keyword_in_title', 'status': 'fail', 'note': 'Anahtar kelime başlıkta yok'})
        
        # 2. Keyword in H1
        h1 = content.get('h1', '')
        if keyword.lower() in h1.lower():
            checks.append({'criteria': 'keyword_in_h1', 'status': 'pass'})
            passed += 1
        else:
            checks.append({'criteria': 'keyword_in_h1', 'status': 'fail'})
        
        # 3. Meta description length
        meta = content.get('meta_description', '')
        if 150 <= len(meta) <= 160:
            checks.append({'criteria': 'meta_description_length', 'status': 'pass'})
            passed += 1
        elif 120 <= len(meta) <= 170:
            checks.append({'criteria': 'meta_description_length', 'status': 'partial', 'note': f'{len(meta)} karakter'})
            passed += 0.5
        else:
            checks.append({'criteria': 'meta_description_length', 'status': 'fail', 'note': f'{len(meta)} karakter (ideal: 150-160)'})
        
        # 4. Has sections/headings
        sections = content.get('sections', [])
        if len(sections) >= 3:
            checks.append({'criteria': 'heading_structure', 'status': 'pass'})
            passed += 1
        else:
            checks.append({'criteria': 'heading_structure', 'status': 'partial'})
            passed += 0.5
        
        # 5. Schema markup
        schema = content.get('schema_markup')
        if schema:
            checks.append({'criteria': 'schema_markup', 'status': 'pass'})
            passed += 1
        else:
            checks.append({'criteria': 'schema_markup', 'status': 'fail'})
        
        # Calculate score
        total_checks = len(checks)
        score = Decimal(str(passed / total_checks)).quantize(Decimal('0.01'))
        
        return {
            'keyword': keyword,
            'seo_score': float(score),
            'total_checks': total_checks,
            'passed': passed,
            'checks': checks
        }
