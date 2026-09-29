"""
Constants and configuration values for the scoring system.
"""

# ==================== SKORLAMA KATSAYILARI (v2 — doküman formülleri) ====================
# Kaynak: Digitus_Engine_v2_Skorlama_Algoritmalari.md (Bölüm 4-6 ve 8 parametre tablosu).
# H/Ln/MB skorlanan listeye göre hesaplanır: skorlar liste-görelidir,
# run'lar arası karşılaştırılamaz ve asla listeler arası cache'lenmez.

SCORE_W_VOLUME = 40              # w_hacim — her kanalda sabit tutun
ADS_W_COMPETITION = 15           # doymuş sektörde 20-25; niş B2B'de 5-10
ADS_W_TREND = 5                  # tazelik bonusu — sabit tutun
SEO_W_GT = 15                    # G_T: satın alma niyeti derecesi (Aşama 2, AI)
SEO_W_GA = 4                     # G_A: ürün-kategori araması derecesi (Aşama 2, AI)
SEO_W_TREND = 3                  # sürdürülebilirlik — sabit tutun
SEO_TREND_FLOOR = -0.5           # evergreen koruması: düşen kelime en fazla -1.5 puan yer
SOCIAL_W_MB = 50                 # mutlak büyüme persentili (ana sürücü)
SOCIAL_W_H = 20                  # kitle tabanı
SOCIAL_MOMENTUM_THRESHOLD = 0.3  # sınıf-0 kelimenin elenme eşiği (MB)

# Ön temizlik: DB'de yüzde saklanan trendler ondalık orana çevrilir
# (+%56 -> 0.56) ve uç değer koruması için kırpılır.
TREND_CLIP_MIN = -1.0
TREND_CLIP_MAX = 3.0
# Ln guard'ı: tüm hacimler eşitse (n=1 dahil) min-max paydası 0 olur.
LN_EQUAL_VOLUME_VALUE = 0.5

# Kanal havuzu boyutları (AI'ya gönderilecek aday sayısı)
ADS_POOL_SIZE = 120
SEO_POOL_SIZE = 60
SOCIAL_POOL_SIZE = 60
ADS_MAX_EXPANSION_POOL_SIZE = 240
SEO_MAX_EXPANSION_POOL_SIZE = 180
SOCIAL_MAX_EXPANSION_POOL_SIZE = 180
MAX_EXPANSION_ROUNDS = 2
MAX_EXPANSION_AI_BATCHES = 60
# EXPANSION_FILL_THRESHOLD kaldırıldı (v2 güvenilirlik işi):
# expansion artık %70 eşiğinde değil, kapasite dolana kadar dener.

# Final kapasite hedefleri (Niyet analizinden sonra seçilecek)
ADS_FINAL_CAPACITY = 60
SEO_FINAL_CAPACITY = 30
SOCIAL_FINAL_CAPACITY = 30

# Havuz çarpanı (testlerde kullanılıyor olabilir)
POOL_MULTIPLIER = 2.0

# Workspace keyword aday havuzu üst limiti (import kapısında uygulanır;
# frontend/src/constants.ts içindeki mirror sabitle senkron tutulmalı)
WORKSPACE_KEYWORD_LIMIT = 1000

# Profil analizi web-process BackgroundTasks'ta koşar; app restart'ında
# running/pending takılı kalan profiller startup janitörüyle bu eşikten
# eski ise failed işaretlenir (P7 Adım 6)
PROFILE_STALE_MINUTES = 15

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
# 8 → 5: JSON truncation riskini düşürür; parse hatasında split/retry devrede.
BRAND_FILTER_BATCH_SIZE = 5
# Brand filter AI çağrısı için token limiti (2000 → 3000).
BRAND_FILTER_MAX_TOKENS = 3000
PREFILTER_MAX_RETRIES = 1
PREFILTER_MISSING_MAX_RETRIES = 2

# ==================== AI ÇAĞRI BÜTÇELERİ (truncation mitigasyonu) ====================
# gemini-3-flash-preview düşünme token'ları max_output_tokens'tan yer —
# ~220 karakterde kesilen "Unterminated string" hatalarının şüphelisi.
# finish_reason WARNING logları (ai_service._extract_text) hipotezi doğrular.
INTENT_MAX_TOKENS = 6000
PREFILTER_MAX_TOKENS = 6000
SEO_METADATA_MAX_TOKENS = 6000
SEO_METADATA_BATCH_SIZE = 4      # metadata (h1/h2 listeli) en token-yoğun çıktı
INTENT_BATCH_SIZE = 8            # önceden intent_analyzer'da hardcoded 8
INTENT_MISSING_MAX_RETRIES = 2   # prefilter kalıbının intent karşılığı
# Tekil (1 kelimelik) kurtarma çağrısı sınırı — filter_candidates /
# analyze_candidates ÇAĞRISI başına toplam (batch başına sıfırlanmaz);
# öncelik (rank_in_channel, keyword_id).
SINGLE_RETRY_MAX_PER_STAGE = 10

# ==================== REASON CODE / SOURCE SABİTLERİ ====================
# ── AI stage registry (plan C) ─────────────────────────────
# Her provider cagrisi bilinen bir stage tasir; bilinmeyen stage default
# modele duser ama telemetride unknown_stage uyarisi uretir. Model-routing
# (plan D) bu registry uzerinden aktive edilir.
AI_STAGES = (
    "intent",
    "brand_filter",
    "ads_prefilter",
    "social_prefilter",
    "seo_metadata",
    "profile_extract",
    "ads_grouping",
    "ads_rsa",
    "claim_rewrite",
    "social_category",
    "social_idea",
    "social_content",
    # Sosyal brief akisi (plan_social_brief_akisi.md): legacy social_category/
    # idea/content'ten AYRI sahnelenir — telemetri brief vs legacy'yi ayirir.
    "social_brief_categories",
    "social_brief_ideas",
    "social_brief_contents",
    "seo_generation",
    "geo_compliance",
    "micro_shorten",
    "embedding",
    "competitor_preview",
    "competitor_discovery",
    # plan_ai: tam-evren ucuz tarama (bake-off + shadow/assistive)
    "corpus_screening",
    # Motor v3 asamalari (app/core/engine/*): telemetride `unknown:<stage>`
    # olarak gorunuyorlardi — olcum bozulmuyordu ama kayitlar anonimdi.
    "family_a1", "family_a2", "family_a2b", "family_a2c", "family_a3",
    "ads_funnel", "ads_intent",
    "seo_rel", "seo_bp", "seo_subintent", "seo_authority", "seo_urlgroup",
    "social_rel", "social_v4", "social_intent", "social_normbounds",
)
# Prompt/config surumu — run manifest'ine yazilir (plan C)
# v2.1 Faz D (23.07): SEO strategy_fit sözleşmesi + ADS/SOCIAL v2_1 blokları
# eklendi; v2 prompt'ları bayt-aynı kaldı (D-harness referansı korunur)
# KAPSAM: intent (SEO/ADS) + ADS/SOCIAL prefilter prompt'ları. Bu dört prompt
# `TestPromptContracts.PROMPT_HASH_REGISTRY` ile bayt-kilitlidir; kapsam dışı
# bir prompt değiştiğinde bu sürüm ARTMAZ (aynı hash setiyle sahte sürüm
# kaydı registry bütünlük kuralını ihlal ederdi).
PROMPT_CONFIG_VERSION = "2026-07-23-v21d"

# Marka filtresi prompt'u + çıktı sözleşmesi AYRI sürümlenir (firma profili
# düzeltmesi Faz C, 03.08): protected_themes bloğu, matched_protected_theme
# alanı, koruma önceliği ve kanonik tema zorunluluğu. Kendi append-only hash
# kilidi `test_brand_exclusion_filter.py` içindedir; run manifest'ine yazılır.
# ESKİ RUN/ARTIFACT YENİDEN YAZILMAZ — yalnız yeni run'lar bu sürümü taşır.
BRAND_FILTER_PROMPT_VERSION = "2026-08-03-protected-v2"

COMPETITOR_TERM_REASON = "COMPETITOR_TERM"
# Terminal (deterministik hard-block) reason'lari — intent/brand/prefilter/
# transfer/expansion sorgularinin TAMAMI bu satirlari atlar; _save_results
# bu satirlari EZEMEZ. Degerler brand_filter/seo_prefilter sabitleriyle
# ayni olmak ZORUNDA (test korumali).
TERMINAL_PREFILTER_REASONS = (
    COMPETITOR_TERM_REASON,
    "BRAND_EXCLUDED_THEME",
    "PRICE_TERM",
)
FALLBACK_QUARANTINE_REASON = "FALLBACK_QUARANTINE"
SEO_METADATA_REASON = "SEO_METADATA"
SEO_METADATA_UNAVAILABLE_REASON = "SEO_METADATA_UNAVAILABLE"
SEO_KEEP_DEFAULT_REASON = "SEO_KEEP_DEFAULT"

# IntentAnalysis.source değerleri ('transfer' legacy olarak kalır)
INTENT_SOURCE_AI = "ai"
INTENT_SOURCE_FALLBACK = "fallback"
INTENT_SOURCE_TRANSFER_AI = "transfer_ai"

# ==================== EXPANSION STOP REASON SABİTLERİ ====================
STOP_CAPACITY_REACHED = "capacity_reached"
STOP_BUDGET_REACHED = "budget_reached"
STOP_NO_MORE_CANDIDATES = "no_more_candidates"
STOP_MAX_ROUNDS_REACHED = "max_rounds_reached"
STOP_CHANNEL_DISABLED = "channel_disabled"

# ==================== ADS ÜRETİM VERSİYONLAMA ====================
# ADS task hard limiti 900s; bunu aşan pending/running task veya
# TaskResult'sız 'generating' set stale sayılır (E2d reconciliation).
ADS_TASK_STALE_SECONDS = 1200

# Backoff sabitleri (pre-filter retry'ları için)
PREFILTER_BASE_DELAY = 2  # seconds
PREFILTER_RETRIABLE_PATTERNS = (
    "429", "503", "overloaded", "Resource exhausted", "rate_limit"
)

# Etiketler (prompt çıktısıyla hizalı)
ADS_PREFILTER_LABELS = ["hot_sale", "lead"]
SEO_PREFILTER_LABELS = ["treasure", "shallow"]
SOCIAL_PREFILTER_LABELS = ["viral", "moderate", "weak"]

# ==================== AŞAMA 2 SINIF / SEÇİM SABİTLERİ (v2) ====================
# ai_class NULL olan kept satırlar (legacy run, fixture, brand-defense SOCIAL)
# seçim sıralamasında orta sınıf sayılır.
DEFAULT_AI_CLASS_WHEN_MISSING = 1

# "Yükselen Fırsat" etiketi: SOCIAL final havuz üst dilimi + yüksek H kesişimi
# (doküman Bölüm 7 çift kanal kuralı; eşikler dokümanda sayısal verilmedi)
RISING_OPPORTUNITY_TOP_RATIO = 0.2
RISING_OPPORTUNITY_MIN_H = 0.7
RISING_OPPORTUNITY_LABEL = "rising_opportunity"

# Intent güven eşikleri (önceden intent_analyzer içinde inline dict idi)
INTENT_MIN_CONFIDENCE = {
    "ADS": 0.52,
    "SEO": 0.45,   # SEO intent artık ELEMEZ; eşik yalnız audit alanına yazılır
    "SOCIAL": 0.50,
}

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
