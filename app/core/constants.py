"""
Constants and configuration values for the scoring system.
"""

# ==================== SKORLAMA KATSAYILARI ====================

# ADS Skorlama
ADS_EPSILON = 0.01  # Sıfıra bölme hatası önleme

# Trend ağırlıkları
ADS_TREND_3M_WEIGHT = 0.6
ADS_TREND_12M_WEIGHT = 0.4

SEO_TREND_3M_WEIGHT = 2.0
SEO_TREND_12M_WEIGHT = 1.0

SOCIAL_TREND_3M_WEIGHT = 3.0
SOCIAL_TREND_12M_WEIGHT = 1.0

# Kanal havuzu boyutları (AI'ya gönderilecek aday sayısı)
ADS_POOL_SIZE = 120
SEO_POOL_SIZE = 60
SOCIAL_POOL_SIZE = 60

# Final kapasite hedefleri (Niyet analizinden sonra seçilecek)
ADS_FINAL_CAPACITY = 60
SEO_FINAL_CAPACITY = 30
SOCIAL_FINAL_CAPACITY = 30

# Havuz çarpanı (testlerde kullanılıyor olabilir)
POOL_MULTIPLIER = 2.0

# ==================== NİYET TİPLERİ ====================

INTENT_TYPES = {
    "transactional": "Satın alma niyetli",
    "informational": "Bilgi arayışı",
    "navigational": "Marka/site yönelimli",
    "commercial": "Araştırma + satın alma karışık",
    "trend_worthy": "Trend/viral potansiyeli"
}

# Kanal bazlı kabul edilen niyet tipleri
CHANNEL_ACCEPTED_INTENTS = {
    "ADS": ["transactional", "commercial"],
    "SEO": ["informational", "commercial"],
    # SOCIAL kanalında commercial da kabul edilir; aksi durumda içerikleştirilebilir
    # pek çok aday intent aşamasında aşırı eleniyor.
    "SOCIAL": ["trend_worthy", "informational", "commercial"]
}


# ==================== KANAL SABİTLERİ ====================

CHANNELS = ["ADS", "SEO", "SOCIAL"]


# ==================== İÇERİK TİPLERİ ====================

CONTENT_TYPES = {
    "ADS": ["ad_group", "responsive_search_ad", "display_ad"],
    "SEO": ["blog_post", "landing_page", "product_page"],
    "SOCIAL": ["instagram_post", "twitter_post", "linkedin_post"]
}


# ==================== UYUMLULUK KONTROL KRİTERLERİ ====================

SEO_COMPLIANCE_CRITERIA = [
    "keyword_in_title",
    "keyword_in_h1",
    "keyword_in_first_paragraph",
    "keyword_density_optimal",
    "meta_description_length",
    "internal_links",
    "external_links",
    "image_alt_tags",
    "schema_markup"
]

GEO_COMPLIANCE_CRITERIA = [
    "local_keywords",
    "local_business_schema",
    "nap_consistency",
    "local_citations",
    "google_my_business_optimization"
]


# ==================== SEO+GEO COMPLIANCE V2 (Roadmap2 Bölüm 6) ====================

# SEO Compliance Kriterleri (11 madde) - Programatik kontrol
SEO_COMPLIANCE_CRITERIA_V2 = [
    "title_has_keyword",      # Başlık keyword içeriyor mu?
    "title_length_ok",        # Başlık ≤70 karakter mi?
    "url_has_keyword",        # URL'de keyword var mı?
    "intro_keyword_count",    # Giriş paragrafında keyword ≥2 kez
    "word_count_in_range",    # 300-450 kelime arası mı?
    "subheading_count_ok",    # ≥3 alt başlık var mı?
    "subheadings_have_kw",    # Alt başlıklarda keyword var mı?
    "has_internal_link",      # Internal link var mı?
    "has_external_link",      # External link var mı?
    "has_bullet_list",        # Bullet list var mı?
    "sentences_readable"      # Ortalama cümle ≤20 kelime mi?
]

# GEO Compliance Kriterleri (7 madde) - AI ile değerlendirme
GEO_COMPLIANCE_CRITERIA_V2 = [
    "intro_answers_question",   # İlk paragraf soruya yanıt veriyor mu?
    "snippet_extractable",      # AI snippet olarak alabilir mi?
    "info_hierarchy_strong",    # Özet→Detay→Örnek yapısı var mı?
    "tone_is_informative",      # Ton bilgilendirici mi?
    "no_fluff_content",         # Dolgu içerik yok mu?
    "direct_answer_present",    # İlk 50 kelimede yanıt var mı?
    "has_verifiable_info"       # Doğrulanabilir bilgi var mı?
]

# Kriter açıklamaları (UI için)
SEO_CRITERIA_DESCRIPTIONS = {
    "title_has_keyword": "Başlıkta anahtar kelime bulunmalı",
    "title_length_ok": "Başlık 70 karakteri geçmemeli",
    "url_has_keyword": "URL yapısında anahtar kelime olmalı",
    "intro_keyword_count": "Giriş paragrafında anahtar kelime en az 2 kez geçmeli",
    "word_count_in_range": "İçerik 300-450 kelime arasında olmalı",
    "subheading_count_ok": "En az 3 alt başlık (H2) olmalı",
    "subheadings_have_kw": "En az bir alt başlıkta anahtar kelime olmalı",
    "has_internal_link": "En az 1 internal link önerisi olmalı",
    "has_external_link": "En az 1 external link olmalı",
    "has_bullet_list": "Madde işaretli liste bulunmalı",
    "sentences_readable": "Ortalama cümle uzunluğu 20 kelimeyi geçmemeli"
}

GEO_CRITERIA_DESCRIPTIONS = {
    "intro_answers_question": "İlk paragraf konuyla ilgili temel soruya yanıt vermeli",
    "snippet_extractable": "Giriş paragrafı bağlamdan bağımsız anlamlı olmalı",
    "info_hierarchy_strong": "Özet→Detay→Örnek yapısı takip edilmeli",
    "tone_is_informative": "Ton bilgilendirici, tarafsız ve otoriter olmalı",
    "no_fluff_content": "Gereksiz dolgu cümleler olmamalı",
    "direct_answer_present": "İlk 50 kelimede konunun özü verilmeli",
    "has_verifiable_info": "Somut veri, istatistik veya kaynak referansı olmalı"
}


# ==================== PRE-FILTER SABİTLERİ ====================
# Smaller batches reduce truncated/invalid JSON risk in pre-filter responses.
PREFILTER_BATCH_SIZE = 6
PREFILTER_MAX_RETRIES = 1
PREFILTER_MISSING_MAX_RETRIES = 2

# Backoff sabitleri (pre-filter retry'ları için)
PREFILTER_BASE_DELAY = 2  # seconds
PREFILTER_RETRIABLE_PATTERNS = (
    "429", "503", "overloaded", "Resource exhausted", "rate_limit"
)

# Etiketler (prompt çıktısıyla hizalı)
ADS_PREFILTER_LABELS = ["hot_sale", "lead"]
SEO_PREFILTER_LABELS = ["treasure", "shallow"]
SOCIAL_PREFILTER_LABELS = ["viral", "moderate", "weak"]

# Social engagement eşikleri
SOCIAL_ENGAGEMENT_VIRAL = 0.70
SOCIAL_ENGAGEMENT_MODERATE = 0.40

# Backfill tavanı (kapasitenin max %30'u)
BACKFILL_MAX_RATIO = 0.30

# Backfill'den dışlanacak reason_code'lar
BACKFILL_EXCLUDED_REASONS = (
    "NEGATIVE_TERM",
    "FALLBACK_QUARANTINE",
    "PRICE_TERM",
    "BRAND_TERM",
)

# ==================== MERKEZİ HARD-NEGATIVE TERİMLER ====================
# Tek kaynak (DRY) — IntentAnalyzer ve BasePreFilter burayı kullanır.
ADS_HARD_NEGATIVE_TERMS = (
    "2 el", "2. el", "ikinci el", "sahibinden", "satılık",
    "bedava", "ücretsiz", "en ucuz", "ucuz", "çıkma", "cikma",
    "kiralık", "kiralik", "pdf", "indir",
)

SOCIAL_HARD_NEGATIVE_TERMS = (
    "2 el", "2. el", "ikinci el", "sahibinden", "satılık",
    "bedava", "ücretsiz", "çıkma", "cikma", "kiralık", "kiralik",
)

# Intent fallback confidence (eşiğin ALTINDA — fallback ile geçmeyi engeller)
INTENT_FALLBACK_CONFIDENCE = {
    "ADS": 0.50,     # eşik 0.52 → altında kalır, geçemez
    "SEO": 0.43,     # eşik 0.45 → altında kalır
    "SOCIAL": 0.48,  # eşik 0.50 → altında kalır
}
