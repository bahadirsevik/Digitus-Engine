"""
Brand Profile Extractor.
Uses AI to analyze crawled website content and generate a structured brand profile.
"""
import json
import logging
from typing import Dict, Any, Optional, List

from app.generators.ai_service import AIService, scoped
from app.core.site_analyzer.crawler import (
    SiteCrawler,
    CrawlContentUnusable,
    assess_text_quality,
)

logger = logging.getLogger(__name__)

# Max chars to send to AI per site
MAX_AI_INPUT_CHARS = 8000

PROFILE_EXTRACTION_PROMPT = """
# ROL
Sen bir dijital pazarlama ve marka analiz uzmanısın.

# GÖREV
Aşağıda bir firmanın web sitesinden alınmış sayfa içerikleri var.
Bu içeriklerden firmanın detaylı profilini çıkar.

# SİTE İÇERİKLERİ
{site_content}

# DESTEKLEYİCİ BİLGİ (varsa, kullanıcıdan)
{preliminary_info}

# ÇELİŞKİ KURALI
- Site içeriği önceliklidir.
- Destekleyici bilgi sadece sitede AÇIK OLMAYAN noktalarda yön verici olarak kullanılır.
- Çelişki varsa SİTE bilgisi esas alınır.

# ÇIKTI FORMATI
SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "company_name": "Firma adı",
    "sector": "Ana sektör (kısa, 2-5 kelime)",
    "brand_summary": "Markayı 1-2 cümlede özetleyen açıklama",
    "products": ["ürün1", "ürün2", "..."],
    "services": ["hizmet1", "..."],
    "target_audience": "Hedef kitle tanımı",
    "use_cases": ["kullanım senaryosu 1", "..."],
    "problems_solved": ["çözülen problem 1", "..."],
    "brand_terms": ["marka adı", "marka + ürün varyasyonları", "..."],
    "exclude_themes": ["firmanın SATMADIĞI/İLGİSİZ konular", "..."],
    "suggested_keywords": ["kelime1", "kelime2", "...", "~10 adet Google Ads aranabilir"]
}}

# KURALLAR
- brand_summary: Markanın ne yaptığını 1-2 cümlede, kullanıcıya gösterilecek sadelikte özetle
- products: Firmanın gerçekten sattığı/ürettiği ürünleri yaz
- use_cases: Bu ürünlerin nasıl kullanıldığını yaz
- problems_solved: Bu ürünlerin hangi sorunları çözdüğünü yaz
- brand_terms: Marka adı + ürün kombinasyonlarını yaz (arama terimi olarak)
- exclude_themes: Sitede OLMADIĞI halde karıştırılabilecek konuları yaz
- suggested_keywords: Bu marka için Google Ads'te aranabilecek ~10 anahtar kelime önerisi
- Türkçe yaz
- Tahmin yapma, sadece sitede gördüğün bilgileri kullan
"""

COMPETITOR_VALIDATION_PROMPT = """
# GÖREV
Ana firma profili ile rakip firma profillerini karşılaştır.
Ana firmanın profil doğruluğunu doğrula.
Her rakip site için, site içeriğinden firmanın/markanın adını tespit et
(detected_name). Tespit edemiyorsan detected_name alanını boş bırak.

# ANA FİRMA PROFİLİ
{main_profile}

# RAKİP FİRMA PROFİLLERİ
{competitor_profiles}

# ÇIKTI FORMATI
SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "consistency_score": 0.85,
    "competitors": [
        {{
            "url": "rakip url",
            "status": "same_sector|nearby|mismatch",
            "summary": "1-2 cümle açıklama",
            "detected_name": "Rakip firmanın/markanın adı (tespit edilemezse boş)"
        }}
    ],
    "warnings": ["Uyarı mesajları varsa"],
    "profile_adjustments": ["Profilde düzeltilmesi gereken noktalar varsa"]
}}
"""

KEYWORD_SUGGESTION_PROMPT = """
# ROL
Sen bir dijital pazarlama ve Google Ads anahtar kelime uzmanısın.

# GOREV
Aşağıdaki firma sitesi içeriği ve kullanıcı notlarına göre, marka için Google Ads'te
aranabilir yaklaşık 10 anahtar kelime öner.

# SITE ICERIGI
{site_content}

# MUTLAKA OLMASI GEREKENLER
{must_have_info}

# MUTLAKA OLMAMASI / DISLANMASI GEREKENLER
{excluded_info}

# KURALLAR
- Sadece firmanın sattığı/sağladığı ürün, hizmet ve kullanım alanlarıyla ilgili keyword öner.
- Mutlaka olmaması gereken temalara giren keyword önerme.
- Keyword'ler Türkçe, kısa ve Google Ads'te aranabilir olsun.
- Marka adı + ürün/hizmet varyasyonlarını gerekiyorsa dahil et.
- 8-12 arası keyword yeterli; hedef 10.

# CIKTI
SADECE şu JSON formatında dön:
{{
  "suggested_keywords": ["keyword 1", "keyword 2"]
}}
"""

PROFILE_FROM_KEYWORDS_PROMPT = """
# ROL
Sen bir dijital pazarlama ve marka analiz uzmanısın.

# GOREV
Aşağıdaki site içeriğinden marka profilini çıkar. Kullanıcının onayladığı keyword
listesi, markanın hedeflediği aramaları gösteren YONLENDIRICI sinyaldir.

# SITE ICERIGI
{site_content}

# MUTLAKA OLMASI GEREKENLER
{must_have_info}

# MUTLAKA OLMAMASI / DISLANMASI GEREKENLER
{excluded_info}

# KULLANICI ONAYLI KEYWORD LISTESI
{approved_keywords}

# KAYNAK ONCELIGI
- Site içeriği ana kaynaktır.
- Keyword listesi profili yönlendirir ama site gerçekliğine aykırı bilgi üretme.
- Çelişki varsa site içeriği esas alınır.
- exclude_themes'i SEN üret: site içeriğine göre firmanın SATMADIĞI/sunmadığı
  ama karıştırılabilecek konuları tespit et (benzer ürün kategorileri, yakın
  ama ilgisiz hizmetler, sektörde karıştırılan temalar). 3-6 tema hedefle.
- Kullanıcının "MUTLAKA OLMAMASI GEREKENLER" listesi sisteme AYRICA eklenecek;
  o maddeleri exclude_themes içinde TEKRARLAMA — onlara EK, YENİ temalar yaz.

# CIKTI FORMATI
SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "company_name": "Firma adı",
    "sector": "Ana sektör",
    "brand_summary": "Markayı 1-2 cümlede özetleyen açıklama",
    "products": ["ürün1"],
    "services": ["hizmet1"],
    "target_audience": "Hedef kitle",
    "use_cases": ["kullanım senaryosu"],
    "problems_solved": ["çözülen problem"],
    "brand_terms": ["marka adı"],
    "exclude_themes": ["firmanın satmadığı ama karıştırılabilecek konu (kullanıcı listesine EK, tekrar değil)"]
}}
"""

KEYWORD_SUGGESTION_FROM_PROFILE_PROMPT = """
# ROL
Sen bir dijital pazarlama ve Google Ads anahtar kelime uzmanısın.

# GOREV
Aşağıdaki firma sitesi içeriği ve KULLANICI ONAYLI marka profiline göre,
marka için Google Ads'te aranabilir yaklaşık 10 anahtar kelime öner.

# SITE ICERIGI
{site_content}

# ONAYLI MARKA PROFILI
{profile_json}

# MUTLAKA OLMASI GEREKENLER
{must_have_info}

# MUTLAKA OLMAMASI / DISLANMASI GEREKENLER
{excluded_info}

# KULLANICI ONAYLI RAKIP MARKALAR (KEYWORD OLARAK ONERME)
{competitor_terms}

# KURALLAR
- Profildeki ürünler, hizmetler ve kullanım alanlarıyla UYUMLU keyword öner;
  profil kullanıcı tarafından onaylandı, ana referans odur.
- Mutlaka olmaması gereken temalara ve profildeki exclude_themes'e giren keyword önerme.
- Mutlaka olması gereken konular varsa onları kapsayan keyword'lere öncelik ver.
- Keyword'ler Türkçe, kısa ve Google Ads'te aranabilir olsun.
- Marka adı + ürün/hizmet varyasyonlarını gerekiyorsa dahil et.
- Onaylı rakip marka adlarını veya bu adları içeren keyword'leri önerme.
- 8-12 arası keyword yeterli; hedef 10.

# CIKTI
SADECE şu JSON formatında dön:
{{
  "suggested_keywords": ["keyword 1", "keyword 2"]
}}
"""

PROFILE_REVISION_PROMPT = """
# ROL
Sen bir dijital pazarlama ve marka analiz uzmanısın.

# GOREV
Aşağıdaki marka profili site içeriğinden çıkarıldı ve kullanıcı tarafından incelendi.
Kullanıcı "mutlaka olması gerekenler" ve "mutlaka olmaması gerekenler" bilgisi verdi.
Profili bu bilgilere göre REVIZE et.

# MEVCUT PROFIL
{profile_json}

# MUTLAKA OLMASI GEREKENLER (kullanıcıdan)
{must_have_info}

# MUTLAKA OLMAMASI / DISLANMASI GEREKENLER (kullanıcıdan)
{excluded_info}

# KURALLAR
- Mutlaka-olsun konularını uygun alanlara işle (products / services / use_cases /
  problems_solved); zaten varsa tekrar ekleme.
- Mutlaka-olmasın konularıyla çelişen kalemleri ilgili listelerden ÇIKAR.
- Profilin geri kalanını KORU; yalnız gerekli alanları değiştir.
- exclude_themes listesini olduğu gibi koru (sisteme ayrıca yönetiliyor).
- Türkçe yaz. Uydurma bilgi ekleme; kullanıcının verdiği konuları sadeleştirerek kullan.

# CIKTI FORMATI
SADECE mevcut profil ile aynı alanları içeren JSON döndür (tam profil):

{{
    "company_name": "Firma adı",
    "sector": "Ana sektör",
    "brand_summary": "1-2 cümle marka özeti",
    "products": ["ürün1"],
    "services": ["hizmet1"],
    "target_audience": "Hedef kitle",
    "use_cases": ["kullanım senaryosu"],
    "problems_solved": ["çözülen problem"],
    "brand_terms": ["marka adı"],
    "exclude_themes": ["mevcut exclude_themes aynen"]
}}
"""

# Profil REVIZYONUNDA AI'in degistirebilecegi alanlar (plan_marka_profili_
# sadakati.md P0.1). Bu kume DISINDA kalan her sey base profilden korunur:
#   - company_name / sector : kullaniciya readOnly gosteriliyor
#     (`LOCKED_PROFILE_FIELDS`, app/api/v1/brand_profile.py) — AI'a da kilitli
#     olmali, aksi halde kilit yalnizca kullaniciya uygulanmis olur.
#   - target_audience       : kullanici duzenleyebiliyor (sihirbaz kart 4 +
#     KeywordAnchorReview "sector_audience" grubu)
#   - exclude_themes / protected_themes : sistemce yonetilir (marka filtresi
#     ENFORCEMENT girdisi)
#   - bilinmeyen anahtarlar : AI semasinda olmayan teknik alanlar, orn.
#     policy_specificity — dusmesi policy_version artirip TUM ciktilari bayatlatir
# NOT: buradaki alanlar da kullanici tarafindan duzenlenebilir; bu yuzden
# revizyon ciktisi keyword yeniden uretiminde DB'ye YAZILMAZ (birinci savunma
# hatti cagirandadir). Bu kume yalnizca prompt baglamini sekillendirir.
_AI_REVISABLE_FIELDS = (
    "brand_summary",
    "products",
    "services",
    "use_cases",
    "problems_solved",
    "brand_terms",
)

PROFILE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "sector": {"type": "string"},
        "brand_summary": {"type": "string"},
        "products": {"type": "array", "items": {"type": "string"}},
        "services": {"type": "array", "items": {"type": "string"}},
        "target_audience": {"type": "string"},
        "use_cases": {"type": "array", "items": {"type": "string"}},
        "problems_solved": {"type": "array", "items": {"type": "string"}},
        "brand_terms": {"type": "array", "items": {"type": "string"}},
        "exclude_themes": {"type": "array", "items": {"type": "string"}},
        "suggested_keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "company_name",
        "sector",
        "products",
        "services",
        "target_audience",
        "use_cases",
        "problems_solved",
        "brand_terms",
        "exclude_themes",
    ],
}

KEYWORD_SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "suggested_keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["suggested_keywords"],
}

COMPETITOR_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "consistency_score": {"type": "number"},
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    # Opsiyonel (required'a GIRMEZ): site içeriğinden tespit
                    # edilen rakip marka adı — Rakipler kartı ön-dolgusu.
                    "detected_name": {"type": "string"},
                },
                "required": ["url", "status", "summary"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "profile_adjustments": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["consistency_score", "competitors", "warnings", "profile_adjustments"],
}


COMPETITOR_NAME_PREVIEW_PROMPT = """
# GÖREV
Aşağıda rakip sitelerin ana sayfa içerik özetleri var. Her site için firmanın/
markanın adını tespit et. Tespit edemiyorsan detected_name alanını boş bırak.

# SİTELER
{sites}

# ÇIKTI FORMATI
SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "competitors": [
        {{"url": "site url", "detected_name": "Marka adı (yoksa boş)"}}
    ]
}}
"""

COMPETITOR_NAME_PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "detected_name": {"type": "string"},
                },
                "required": ["url", "detected_name"],
            },
        },
    },
    "required": ["competitors"],
}


def preview_competitor_names(ai_service: AIService, urls: List[str]) -> List[Dict[str, Any]]:
    """Hafif marka-adı tespiti (plan v13 preview task'ı).

    Full validate_with_competitors DEĞİL: URL başına YALNIZ ana sayfa,
    SSRF-korumalı safe_fetch (SiteCrawler KULLANILMAZ — verify=False +
    doğrulanmasız redirect taşınmaz), max 3 URL, tek AI çağrısı. Fail-open:
    fetch/AI hatasında domain-fallback adıyla kayıt döner. Persist ETMEZ.
    """
    import re as _re

    from bs4 import BeautifulSoup

    from app.core.url_guard import UnsafeUrlError, safe_fetch_html

    scoped_ai = scoped(ai_service, "competitor_preview")
    sites = []
    results: List[Dict[str, Any]] = []
    for url in (urls or [])[:3]:
        entry: Dict[str, Any] = {
            "url": url,
            "detected_name": _domain_fallback_name(url),
            "source": "domain_fallback",
        }
        try:
            html = safe_fetch_html(url)
            soup = BeautifulSoup(html, "lxml")
            title = (soup.title.string or "").strip() if soup.title else ""
            text = _re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:2000]
            sites.append({"url": url, "title": title, "content": text})
        except UnsafeUrlError as exc:
            entry["error"] = str(exc)
        except Exception as exc:
            entry["error"] = f"Sayfa okunamadı: {exc}"
        results.append(entry)

    if sites:
        try:
            prompt = COMPETITOR_NAME_PREVIEW_PROMPT.format(
                sites=json.dumps(sites, ensure_ascii=False, indent=2)
            )
            response = scoped_ai.complete_json(
                prompt,
                max_tokens=500,
                response_schema=COMPETITOR_NAME_PREVIEW_SCHEMA,
            )
            from app.core.channel.ai_json import parse_ai_json_object

            parsed = parse_ai_json_object(response) or {}
            from app.core.policy.competitor_policy import normalize_competitor_url

            detected = {
                normalize_competitor_url(c.get("url", "")): str(
                    c.get("detected_name") or ""
                ).strip()
                for c in parsed.get("competitors") or []
                if isinstance(c, dict)
            }
            for entry in results:
                key = normalize_competitor_url(entry["url"])
                name = detected.get(key)
                if name:
                    entry["detected_name"] = name
                    entry["source"] = "ai"
        except Exception as exc:
            logger.warning(f"Competitor name preview AI failed (fail-open): {exc}")

    return results


def _domain_fallback_name(url: str) -> str:
    """Registrable-domain'den düzenlenebilir marka adı ÖNERİSİ üretir.

    (ör. https://www.fintables.com → 'Fintables'). Otomatik onay DEĞİLDİR —
    kullanıcı Rakipler kartında düzeltebilir/reddedebilir."""
    try:
        from app.core.policy.competitor_policy import _candidate_terms_from_domain

        candidates = _candidate_terms_from_domain(url)
        return candidates[0].title() if candidates else ""
    except Exception:
        return ""


def _fill_missing_competitors(ai_competitors, competitor_urls) -> list:
    """HER girdi URL'si için garanti kayıt (plan v13, Codex v9 #8).

    AI'nin atladığı / hiç dönmediği URL'ler için {url, status: 'unknown',
    summary: '', detected_name: <domain fallback>} sentezlenir; AI'nin boş
    bıraktığı detected_name alanları da domain fallback ile doldurulur.
    Eşleştirme kanonik URL anahtarıyla yapılır (index/string değil).
    """
    from app.core.policy.competitor_policy import normalize_competitor_url

    by_key: dict = {}
    for comp in ai_competitors or []:
        if not isinstance(comp, dict):
            continue
        key = normalize_competitor_url(comp.get("url", ""))
        if key and key not in by_key:
            by_key[key] = comp

    result = []
    seen_keys: set = set()
    for url in competitor_urls or []:
        key = normalize_competitor_url(url)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        comp = by_key.get(key)
        if comp is None:
            comp = {"url": url, "status": "unknown", "summary": ""}
        else:
            comp = dict(comp)
        if not str(comp.get("detected_name") or "").strip():
            comp["detected_name"] = _domain_fallback_name(url)
        result.append(comp)
    return result


def resolve_site_content(extractor, company_url: str, cached_content: Optional[str]):
    """Cache OKUMA KAPISI — bozuk cache'in AI'a gitmesini engeller.

    Arka plan: `crawl_content_cache` gecmiste cozulememis (brotli) ikili icerikle
    dolmus olabilir. Dolu-ama-bozuk bir cache, kalite kapisini atlayip dogrudan
    AI'a gidiyordu.

    Sozlesme:
      1. Cache doluysa kalite kontrolunden gecer.
      2. Bozuksa cache SILINMEZ; yeniden crawl denenir.
      3. Yeni crawl da basarisizsa AI CAGRILMADAN acik hata doner.
      4. Basariliysa cache yalnizca o zaman yenilenir (source_pages != None).
      5. Bozuk cache + basarisiz crawl: eski cache KORUNUR fakat KULLANILMAZ.

    Returns:
        (site_content, source_pages, error)
        * source_pages None ise cache YENILENMEZ (taze crawl olmadi).
        * error dolu ise cagiran, AI'a gitmeden gorevi kapatmalidir.
    """
    cached = (cached_content or "").strip()
    if cached:
        quality = assess_text_quality(cached)
        if quality["usable"]:
            return cached, None, None
        logger.warning(
            "crawl_content_cache kalitesiz (%s) — cache KULLANILMIYOR, yeniden crawl deneniyor "
            "(chars=%s repl=%s ctrl=%s)",
            quality["reason"], quality["chars"],
            quality["replacement_ratio"], quality["control_ratio"],
        )

    crawl = extractor.crawl_for_profile_content(company_url)
    if crawl.get("error") and not crawl.get("site_content"):
        # Eski cache'e DOKUNULMAZ; yalnizca kullanilmaz.
        return "", None, crawl.get("error") or "crawl_content_unusable"
    return crawl["site_content"], crawl["source_pages"], None


class ProfileExtractor:
    """Extracts structured brand profile from crawled website content."""

    def __init__(self, ai_service: AIService):
        self.ai_service = scoped(ai_service, "profile_extract")
        self.crawler = SiteCrawler()

    def extract_profile(
        self,
        company_url: str,
        preliminary_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Crawl site and extract brand profile.

        Args:
            company_url: Company website URL to crawl
            preliminary_info: Optional user-provided context (vision, target audience, USPs)

        Returns:
            {
                "profile": {...},
                "source_pages": [...],
                "anchor_texts": [...],
                "error": None
            }
        """
        self._preliminary_info = preliminary_info  # Store for _format_prompt
        # Crawl the site
        crawl_result = self.crawler.crawl_site(company_url)

        if crawl_result["error"] and not crawl_result["pages"]:
            return {
                "profile": None,
                "source_pages": [],
                "anchor_texts": [],
                "error": crawl_result["error"],
            }

        # Prepare content for AI
        site_content = self._prepare_content(crawl_result["pages"])

        # Extract profile via AI
        try:
            prompt = PROFILE_EXTRACTION_PROMPT.format(
                site_content=site_content,
                preliminary_info=preliminary_info or "(kullanıcı tarafından sağlanmadı)",
            )
            response = self.ai_service.complete_json(
                prompt,
                max_tokens=2000,
                response_schema=PROFILE_RESPONSE_SCHEMA,
            )
            profile = self._parse_json_response(response)
        except Exception as e:
            logger.error(f"Profile extraction AI error: {e}")
            return {
                "profile": None,
                "source_pages": self._page_summaries(crawl_result["pages"]),
                "anchor_texts": [],
                "error": f"AI extraction failed: {e}",
            }

        # Generate anchor texts from profile
        anchor_texts = self._generate_anchors(profile)
        profile["anchor_texts"] = anchor_texts

        return {
            "profile": profile,
            "source_pages": self._page_summaries(crawl_result["pages"]),
            "anchor_texts": anchor_texts,
            "error": None,
        }

    def crawl_for_profile_content(self, company_url: str) -> Dict[str, Any]:
        """Crawl a site and return prepared AI input plus lightweight page summaries.

        FAIL-CLOSED: cikarilan metin kalite kapisini gecemezse `error` doldurulur ve
        `site_content` BOS birakilir. Boylece kullanilamaz icerik AI'a gidemez ve
        `preliminary_info` ile SESSIZCE telafi edilemez.
        """
        crawl_result = self.crawler.crawl_site(company_url)
        if crawl_result["error"] and not crawl_result["pages"]:
            return {
                "site_content": "",
                "source_pages": [],
                "error": crawl_result["error"],
                "content_quality": None,
            }
        prepared = self._prepare_content(crawl_result["pages"])
        quality = assess_text_quality(prepared)
        if not quality["usable"]:
            logger.warning(
                "crawl_content_unusable (%s) for %s — chars=%s repl=%s ctrl=%s",
                quality["reason"], company_url, quality["chars"],
                quality["replacement_ratio"], quality["control_ratio"],
            )
            return {
                "site_content": "",
                "source_pages": self._page_summaries(crawl_result["pages"]),
                "error": f"crawl_content_unusable: {quality['reason']}",
                "content_quality": quality,
            }
        return {
            "site_content": prepared,
            "source_pages": self._page_summaries(crawl_result["pages"]),
            "error": None,
            "content_quality": quality,
        }

    def extract_profile_from_site_content(
        self,
        site_content: str,
        preliminary_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Hazır site içeriğinden (cache) profil çıkarır; crawl ETMEZ.

        Profil-önce onboarding'in ilk adımı: suggested_keywords bu aşamada
        ÜRETİLMEZ/sızmaz (10 KW profil onayından sonra ayrıca istenir).

        FAIL-CLOSED: `site_content` kalite kapısını geçemezse ``CrawlContentUnusable``
        fırlatılır. Aksi halde model, boş/bozuk site içeriğini görmezden gelip
        ``preliminary_info`` (kullanıcı beyanı) üzerinden MAKUL AMA KANITSIZ bir profil
        üretir; bu hata sessizdir ve insan onayından da geçebilir.
        """
        quality = assess_text_quality(site_content)
        if not quality["usable"]:
            raise CrawlContentUnusable(
                f"crawl_content_unusable: {quality['reason']} "
                f"(chars={quality['chars']}, repl={quality['replacement_ratio']}, "
                f"ctrl={quality['control_ratio']})"
            )
        prompt = PROFILE_EXTRACTION_PROMPT.format(
            site_content=site_content,
            preliminary_info=preliminary_info or "(kullanıcı tarafından sağlanmadı)",
        )
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=4000,
            response_schema=PROFILE_RESPONSE_SCHEMA,
        )
        try:
            profile = self._parse_json_response(response)
        except ValueError as first_error:
            logger.warning(
                "Profile JSON parse failed, retrying with larger output budget: %s",
                first_error,
            )
            response = self.ai_service.complete_json(
                prompt,
                max_tokens=6000,
                response_schema=PROFILE_RESPONSE_SCHEMA,
            )
            profile = self._parse_json_response(response)
        profile.pop("suggested_keywords", None)
        profile["anchor_texts"] = self._generate_anchors(profile)
        return profile

    def suggest_keywords_from_profile(
        self,
        site_content: str,
        profile_data: Dict[str, Any],
        must_have_info: Optional[str] = None,
        excluded_info: Optional[str] = None,
        competitor_terms: Optional[List[str]] = None,
    ) -> List[str]:
        """Onaylı profile dayalı 10'luk keyword önerisi (profil-önce akış, adım 4)."""
        profile_json = {
            k: v for k, v in (profile_data or {}).items() if k != "anchor_texts"
        }
        prompt = KEYWORD_SUGGESTION_FROM_PROFILE_PROMPT.format(
            site_content=site_content,
            profile_json=json.dumps(profile_json, ensure_ascii=False, indent=2),
            must_have_info=must_have_info or "(kullanıcı tarafından sağlanmadı)",
            excluded_info=excluded_info or "(kullanıcı tarafından sağlanmadı)",
            competitor_terms=(
                ", ".join(competitor_terms)
                if competitor_terms else "(onaylı rakip marka yok)"
            ),
        )
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=1200,
            response_schema=KEYWORD_SUGGESTION_SCHEMA,
        )
        parsed = self._parse_json_response(response)
        raw_keywords = parsed.get("suggested_keywords", [])
        if not isinstance(raw_keywords, list):
            return []
        return [str(item).strip() for item in raw_keywords if str(item).strip()]

    def revise_profile_with_requirements(
        self,
        profile_data: Dict[str, Any],
        must_have_info: Optional[str] = None,
        excluded_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Kullanıcının mutlaka-olsun/olmasın notlarını profile işler (fail-open).

        AI hatasında girdi profili DEĞİŞMEDEN döner; akış kırılmaz.

        SADAKAT (plan_marka_profili_sadakati.md P0.1, ikinci savunma hattı):
        çıktı base'ten kopyalanarak kurulur ve AI YALNIZ `_AI_REVISABLE_FIELDS`
        alanlarını değiştirebilir. Böylece (a) kullanıcıya kilitli company_name/
        sector, (b) kullanıcının düzenlediği target_audience, (c) sistemce
        yönetilen exclude_themes/protected_themes ve (d) AI şemasında HİÇ
        bulunmayan teknik anahtarlar (örn. policy_specificity) AI dönüşünde
        kaybolamaz. Daha önce AI'ın tam dönüşü kullanılıyordu ve şema dışı her
        anahtar sessizce düşüyordu.

        Birinci savunma hattı çağıranda: keyword yeniden üretimi bu çıktıyı
        DB'ye hiç yazmaz (`_run_keyword_suggestion_from_profile`).
        """
        if not (must_have_info or "").strip():
            return profile_data
        base = {k: v for k, v in (profile_data or {}).items() if k != "anchor_texts"}
        try:
            prompt = PROFILE_REVISION_PROMPT.format(
                profile_json=json.dumps(base, ensure_ascii=False, indent=2),
                must_have_info=must_have_info or "(kullanıcı tarafından sağlanmadı)",
                excluded_info=excluded_info or "(kullanıcı tarafından sağlanmadı)",
            )
            response = self.ai_service.complete_json(
                prompt,
                max_tokens=4000,
                response_schema=PROFILE_RESPONSE_SCHEMA,
            )
            revised = self._parse_json_response(response)
            # Allowlist birleştirme: base her şeyi taşır, AI yalnız izinli
            # alanları üstüne yazar. "AI dönüşünü al, birkaç alanı geri koy"
            # deseninin tersi — yeni bir profil anahtarı eklendiğinde onu geri
            # taşımayı unutmak mümkün olmasın diye bilinçli bu yönde.
            result = dict(base)
            for field in _AI_REVISABLE_FIELDS:
                if field in revised:
                    result[field] = revised[field]
            return result
        except Exception as e:
            logger.warning(f"Profile revision failed, keeping original profile: {e}")
            return profile_data

    def suggest_keywords(
        self,
        site_content: str,
        must_have_info: Optional[str] = None,
        excluded_info: Optional[str] = None,
    ) -> List[str]:
        """Generate the first-stage keyword suggestions for user review."""
        prompt = KEYWORD_SUGGESTION_PROMPT.format(
            site_content=site_content,
            must_have_info=must_have_info or "(kullanıcı tarafından sağlanmadı)",
            excluded_info=excluded_info or "(kullanıcı tarafından sağlanmadı)",
        )
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=1200,
            response_schema=KEYWORD_SUGGESTION_SCHEMA,
        )
        parsed = self._parse_json_response(response)
        raw_keywords = parsed.get("suggested_keywords", [])
        if not isinstance(raw_keywords, list):
            return []
        return [str(item).strip() for item in raw_keywords if str(item).strip()]

    def extract_profile_from_keywords(
        self,
        site_content: str,
        approved_keywords: List[str],
        must_have_info: Optional[str] = None,
        excluded_info: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a full profile from prepared site content and approved keywords."""
        prompt = PROFILE_FROM_KEYWORDS_PROMPT.format(
            site_content=site_content,
            must_have_info=must_have_info or "(kullanıcı tarafından sağlanmadı)",
            excluded_info=excluded_info or "(kullanıcı tarafından sağlanmadı)",
            approved_keywords=json.dumps(approved_keywords, ensure_ascii=False, indent=2),
        )
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=4000,
            response_schema=PROFILE_RESPONSE_SCHEMA,
        )
        try:
            profile = self._parse_json_response(response)
        except ValueError as first_error:
            logger.warning("Profile JSON parse failed, retrying with larger output budget: %s", first_error)
            response = self.ai_service.complete_json(
                prompt,
                max_tokens=6000,
                response_schema=PROFILE_RESPONSE_SCHEMA,
            )
            profile = self._parse_json_response(response)
        profile.pop("suggested_keywords", None)
        anchor_texts = self._generate_anchors(profile)
        profile["anchor_texts"] = anchor_texts
        return profile

    def validate_with_competitors(
        self,
        main_profile: Dict,
        competitor_urls: List[str],
    ) -> Dict[str, Any]:
        """
        Validate main profile against competitor sites.

        Returns validation result with consistency score.
        """
        competitor_profiles = []

        for url in competitor_urls[:3]:  # Max 3 competitors
            try:
                crawl_result = self.crawler.crawl_site(url)
                if crawl_result["pages"]:
                    content = self._prepare_content(
                        crawl_result["pages"][:3]  # Fewer pages per competitor
                    )
                    competitor_profiles.append({
                        "url": url,
                        "content_summary": content[:2000],
                    })
            except Exception as e:
                logger.warning(f"Competitor crawl failed for {url}: {e}")
                competitor_profiles.append({
                    "url": url,
                    "content_summary": f"Crawl failed: {e}",
                })

        if not competitor_profiles:
            return {
                "consistency_score": None,
                "competitors": _fill_missing_competitors([], competitor_urls[:3]),
                "warnings": ["Hiçbir rakip site crawl edilemedi"],
                "profile_adjustments": [],
            }

        try:
            prompt = COMPETITOR_VALIDATION_PROMPT.format(
                main_profile=json.dumps(main_profile, ensure_ascii=False, indent=2),
                competitor_profiles=json.dumps(
                    competitor_profiles, ensure_ascii=False, indent=2
                ),
            )
            response = self.ai_service.complete_json(
                prompt,
                max_tokens=1500,
                response_schema=COMPETITOR_VALIDATION_SCHEMA,
            )
            result = self._parse_json_response(response)
            if isinstance(result, dict):
                result["competitors"] = _fill_missing_competitors(
                    result.get("competitors"), competitor_urls[:3]
                )
            return result
        except Exception as e:
            logger.error(f"Competitor validation AI error: {e}")
            return {
                "consistency_score": None,
                "competitors": _fill_missing_competitors([], competitor_urls[:3]),
                "warnings": [f"Doğrulama AI hatası: {e}"],
                "profile_adjustments": [],
            }

    def _prepare_content(self, pages: List[Dict]) -> str:
        """Prepare crawled pages into a single text for AI."""
        parts = []
        total_chars = 0

        for page in pages:
            text = page.get("text", "")
            title = page.get("title", "")
            url = page.get("url", "")

            section = f"## Sayfa: {title}\nURL: {url}\n\n{text}"

            if total_chars + len(section) > MAX_AI_INPUT_CHARS:
                remaining = MAX_AI_INPUT_CHARS - total_chars
                if remaining > 200:
                    parts.append(section[:remaining])
                break

            parts.append(section)
            total_chars += len(section)

        return "\n\n---\n\n".join(parts)

    def _page_summaries(self, pages: List[Dict]) -> List[Dict]:
        """Create lightweight page summaries for storage."""
        return [
            {
                "url": p.get("url", ""),
                "title": p.get("title", ""),
                "status": p.get("status", 0),
            }
            for p in pages
        ]

    def _generate_anchors(self, profile: Dict) -> List[str]:
        """
        Generate embedding anchor texts from profile (C1).
        exclude_themes ile örtüşen kalemler ayıklanır ki relevance,
        dışlanan temaları ödüllendirmesin (plan5 Kök Neden 3).
        """
        from app.core.site_analyzer.anchor_builder import build_anchor_texts
        return build_anchor_texts(profile)

    def _parse_json_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse model response defensively.
        Supports clean JSON and JSON followed by extra text.
        """
        text = (response_text or "").strip()
        if not text:
            raise ValueError("Empty AI response")

        # Fast path: fully valid JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            raise ValueError("AI response root must be an object")
        except json.JSONDecodeError:
            pass

        # Recovery path 1: find first JSON object and decode only that part
        decoder = json.JSONDecoder()
        for start_char in ("{", "["):
            start_idx = text.find(start_char)
            if start_idx == -1:
                continue
            try:
                parsed, _ = decoder.raw_decode(text[start_idx:])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

        # Recovery path 2 (plan P7): ai_json kurtarma zinciri — fence,
        # akilli tirnak, trailing virgul, dengesiz parantez (truncation).
        # raw_decode truncated govdede basarisiz kalir; canlida ws13 profili
        # tam bu sinif yuzunden failed olmustu.
        from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object

        try:
            return parse_ai_json_object(text)
        except AIJsonParseError:
            pass

        preview = text[:200].replace("\n", " ")
        raise ValueError(f"Failed to parse AI JSON response. preview={preview!r}")
