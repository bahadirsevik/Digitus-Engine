"""
GEO (local SEO) compliance checker.
"""
from typing import Dict, Any, List
from decimal import Decimal

from app.core.constants import GEO_COMPLIANCE_CRITERIA


class GeoChecker:
    """GEO (yerel SEO) uyumluluk kontrolcüsü."""
    
    def check_content(
        self,
        keyword: str,
        content: Dict[str, Any],
        target_location: str = None
    ) -> Dict[str, Any]:
        """
        İçeriğin GEO uyumluluğunu kontrol eder.
        
        Args:
            keyword: Hedef anahtar kelime
            content: Kontrol edilecek içerik
            target_location: Hedef lokasyon
        
        Returns:
            Uyumluluk raporu
        """
        checks = []
        passed = 0
        
        if not target_location:
            return {
                'keyword': keyword,
                'geo_score': 1.0,
                'note': 'Hedef lokasyon belirtilmedi, GEO kontrolü atlandı',
                'checks': []
            }
        
        location_lower = target_location.lower()
        
        # 1. Local keywords in title
        title = content.get('title', '')
        if location_lower in title.lower():
            checks.append({'criteria': 'local_keyword_in_title', 'status': 'pass'})
            passed += 1
        else:
            checks.append({'criteria': 'local_keyword_in_title', 'status': 'fail'})
        
        # 2. Local keywords in content
        body_text = self._extract_body_text(content)
        location_mentions = body_text.lower().count(location_lower)
        if location_mentions >= 3:
            checks.append({'criteria': 'local_mentions', 'status': 'pass', 'count': location_mentions})
            passed += 1
        elif location_mentions >= 1:
            checks.append({'criteria': 'local_mentions', 'status': 'partial', 'count': location_mentions})
            passed += 0.5
        else:
            checks.append({'criteria': 'local_mentions', 'status': 'fail', 'count': 0})
        
        # 3. Local business schema
        schema = content.get('schema_markup', {})
        schema_type = schema.get('@type', '') if isinstance(schema, dict) else ''
        if 'LocalBusiness' in schema_type or 'local' in str(schema).lower():
            checks.append({'criteria': 'local_business_schema', 'status': 'pass'})
            passed += 1
        else:
            checks.append({'criteria': 'local_business_schema', 'status': 'fail'})
        
        # Calculate score
        total_checks = len(checks)
        score = Decimal(str(passed / total_checks)).quantize(Decimal('0.01')) if total_checks > 0 else Decimal('0')
        
        return {
            'keyword': keyword,
            'target_location': target_location,
            'geo_score': float(score),
            'total_checks': total_checks,
            'passed': passed,
            'checks': checks
        }
    
    def _extract_body_text(self, content: Dict[str, Any]) -> str:
        """İçerikten body metnini çıkarır."""
        text_parts = []
        
        if 'title' in content:
            text_parts.append(content['title'])
        
        if 'h1' in content:
            text_parts.append(content['h1'])
        
        for section in content.get('sections', []):
            if isinstance(section, dict):
                text_parts.append(section.get('heading', ''))
                text_parts.append(section.get('content', ''))
        
        return ' '.join(text_parts)
