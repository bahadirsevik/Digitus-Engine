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

POOL_MULTIPLIER = 2

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
    "SOCIAL": ["trend_worthy", "informational"]
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
