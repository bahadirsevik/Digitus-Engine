"""
Prompt templates for Social Media content generation.

3 Main Prompts:
1. SOCIAL_CATEGORY_PROMPT - Generate content categories
2. SOCIAL_IDEA_PROMPT - Generate content ideas per category
3. SOCIAL_CONTENT_PROMPT - Generate full content package

2 Regenerate Prompts:
4. IDEA_REGENERATE_PROMPT - Regenerate a single idea
5. CONTENT_REGENERATE_PROMPT - Regenerate content for an idea
"""

# ==================== AŞAMA 1: KATEGORİ ====================

SOCIAL_CATEGORY_PROMPT = '''
# ROL
Sen deneyimli bir sosyal medya stratejistisin.

# GÖREV
Aşağıdaki marka ve anahtar kelimeler için uygun içerik KATEGORİLERİ belirle.

# MARKA BİLGİSİ
- Marka: {brand_name}
- Ek Bağlam: {brand_context}

# ANAHTAR KELİMELER
{keywords_json}

# KATEGORİ TİPLERİ
| Tip | Açıklama | Örnek |
|-----|----------|-------|
| educational | Bilgilendirici, öğretici | "5 Kritik Laptop Seçim Hatası" |
| product_benefit | Ürün/hizmet faydası odaklı | "Bu Özellik Hayatınızı Değiştirecek" |
| social_proof | Müşteri hikayesi, case study | "Müşterimizin Başarı Hikayesi" |
| brand_story | Perde arkası, marka hikayesi | "Ürünlerimiz Nasıl Üretiliyor?" |
| community | Challenge, anket, Q&A | "Sizce Hangisi? Oylama Zamanı!" |
| trending | Trend format, viral içerik | "POV: Laptop Alırken..." |

# KURALLAR
- 4-6 kategori belirle
- Her kategoriye uygun anahtar kelimeleri eşleştir
- Her kategori için 0-1 arası relevance_score ver
- Marka değerleriyle uyumlu kategorilere yüksek skor ver

# ÇIKTI FORMATI (JSON)
{{
  "categories": [
    {{
      "category_name": "Teknik Eğitim Serisi",
      "category_type": "educational",
      "description": "Kullanıcıları bilgilendiren, karar vermelerine yardımcı olan içerikler",
      "relevance_score": 0.85,
      "suggested_keywords": ["laptop seçimi", "bilgisayar karşılaştırma"]
    }}
  ]
}}
'''

# ==================== AŞAMA 2: FİKİR ====================

SOCIAL_IDEA_PROMPT = '''
# ROL
Sen viral içerik stratejisti ve sosyal medya uzmanısın.

# GÖREV
Aşağıdaki kategori için içerik FİKİRLERİ üret.

# KATEGORİ BİLGİSİ
- Kategori: {category_name}
- Tip: {category_type}
- Açıklama: {category_description}
- Anahtar Kelimeler: {keywords}

# MARKA
- Marka: {brand_name}

# PLATFORM VE FORMAT MATRİSİ
| Platform | Uygun Formatlar | Karakter Limiti | Özel Notlar |
|----------|-----------------|-----------------|-------------|
| instagram | reels, carousel, story, post | 2200 | Hashtag ayrı, ilk satır önemli |
| tiktok | short | 2200 | Trending ses, ilk 3sn kritik |
| twitter | post, thread | 280/tweet | Thread için 10-15 tweet |
| linkedin | post, carousel | 3000 | Profesyonel ton, sabah paylaşım |
| youtube | short | 5000 (desc) | Thumbnail önemli |

# TREND UYUMU DEĞERLENDİRME (trend_alignment)
DİKKAT: Bu skor "virallik garantisi" DEĞİL, sadece güncel trendlerle uyum derecesidir.
| Faktör | Ağırlık |
|--------|---------|
| Trend format uyumu | 0.25 |
| Duygusal tetik | 0.20 |
| Paylaşılabilirlik | 0.20 |
| Basitlik | 0.15 |
| Özgünlük | 0.10 |
| Marka uyumu | 0.10 |

# KURALLAR
- {ideas_count} fikir üret
- Her fikir için en uygun platform ve format seç
- trend_alignment 0-1 arası (dürüst değerlendir, 0.9+ çok nadir)
- İlgili anahtar kelimeyi belirt

# ÇIKTI FORMATI (JSON)
{{
  "ideas": [
    {{
      "idea_title": "5 Laptop Hatası Reels",
      "idea_description": "En sık yapılan 5 laptop seçim hatasını kısa ve eğlenceli bir şekilde anlatan video",
      "target_platform": "instagram",
      "content_format": "reels",
      "trend_alignment": 0.72,
      "related_keyword": "laptop seçimi"
    }}
  ]
}}
'''

# ==================== AŞAMA 3: İÇERİK ====================

SOCIAL_CONTENT_PROMPT = '''
# ROL
Sen viral içerik yazarı ve sosyal medya uzmanısın.

# GÖREV
Aşağıdaki fikir için TAM İÇERİK PAKETİ üret.

# FİKİR BİLGİSİ
- Başlık: {idea_title}
- Açıklama: {idea_description}
- Platform: {target_platform}
- Format: {content_format}
- İlgili Kelime: {related_keyword}

# MARKA
- Marka: {brand_name}
- Ton: {brand_tone}

# PLATFORM LİMİTLERİ
- Instagram: 2200 karakter
- TikTok: 2200 karakter
- Twitter/X: 280 karakter/tweet
- LinkedIn: 3000 karakter
- YouTube: 5000 karakter (açıklama)

# İÇERİK BİLEŞENLERİ

## 1. HOOKS (En az 3 adet, farklı stiller)
| Stil | Açıklama | Örnek |
|------|----------|-------|
| question | Soru ile merak uyandır | "Hiç merak ettiniz mi...?" |
| shocking | Şaşırtıcı bilgi | "%80'i bu hatayı yapıyor" |
| relatable | Ortak deneyim | "Hepimiz bunu yaşadık..." |
| curiosity | Merak uyandırma | "Bunu öğrenince şaşıracaksınız" |

## 2. CAPTION
- Platform limitine uygun
- Yapı: Hook → Değer → CTA
- Doğal ve zorlamayan

## 3. SENARYO (Video/Carousel için)
- Video: Sahne sahne, zamanlama
- Carousel: Slide slide içerik

## 4. GÖRSEL ÖNERİSİ
- Görsel stili (minimal, bold, lifestyle)
- Renk paleti
- Metin overlay

## 5. CTA VE HASHTAG'LER
- CTA: Doğal (yorum, kaydet, paylaş)
- Hashtag mix (10-15):
  * 3-4 geniş (yüksek hacim)
  * 3-4 niş (düşük rekabet)
  * 3-4 branded/kampanya

## 6. PLATFORM NOTLARI
- Platform-specific tavsiyeler
- GENEL ENDÜSTRİ STANDARDI paylaşım zamanı (kullanıcı hesabına özel DEĞİL)

# ÇIKTI FORMATI (JSON)
{{
  "hooks": [
    {{"text": "Laptop alırken en çok yapılan hata ne biliyor musunuz?", "style": "question"}},
    {{"text": "%80'i aynı hatayı yapıyor ve farkında bile değil", "style": "shocking"}},
    {{"text": "Hepimiz ilk laptop'umuzu alırken bunu yaptık", "style": "relatable"}}
  ],
  "caption": "Laptop alırken en çok yapılan 5 hata 👇\\n\\n1. Sadece fiyata bakmak\\n2. RAM'i göz ardı etmek\\n...",
  "scenario": "0-3sn: Hook (şaşırtıcı istatistik)\\n3-10sn: Problem tanıtımı\\n10-25sn: 5 hata sırayla\\n25-30sn: CTA",
  "visual_suggestion": "Minimalist beyaz arka plan, ürün merkezli çekimler, bold tipografi overlays",
  "video_concept": "B-roll: laptop kullanım sahneleri, hızlı geçişler, trending müzik",
  "cta_text": "Kaydet ve sonra okuya bas 🔖",
  "hashtags": ["laptop", "teknoloji", "alışveriş", "bilgisayar", "tech", "reels", "viral", "ipuçları", "bilgilendir", "markaadı"],
  "industry_posting_suggestion": "Genel öneri: Instagram Reels için Salı-Perşembe 18:00-21:00. NOT: Bu genel endüstri standardıdır, kendi hesap analitiklerinizi kullanmanız önerilir.",
  "platform_notes": "İlk 3 saniye kritik - hook ile başla. Trending ses kullan. Altyazı ekle."
}}
'''

# ==================== REGENERATE PROMPTS ====================

IDEA_REGENERATE_PROMPT = '''
# ROL
Sen sosyal medya stratejistisin.

# GÖREV
Aşağıdaki fikir beğenilmedi. FARKLI bir fikir üret.

# ESKİ FİKİR (beğenilmedi)
- Başlık: {old_idea_title}
- Platform: {old_platform}
- Format: {old_format}

# KATEGORİ
- Kategori: {category_name}
- Tip: {category_type}
- Anahtar Kelimeler: {keywords}

# MARKA
- Marka: {brand_name}

# EK BAĞLAM
{additional_context}

# KURAL
- ESKİ FİKİRDEN TAMAMEN FARKLI bir fikir üret
- Farklı platform veya format tercih edilebilir
- Daha yaratıcı ve özgün ol

# ÇIKTI FORMATI (JSON)
{{
  "idea_title": "...",
  "idea_description": "...",
  "target_platform": "...",
  "content_format": "...",
  "trend_alignment": 0.XX,
  "related_keyword": "..."
}}
'''

CONTENT_REGENERATE_PROMPT = '''
# ROL
Sen viral içerik yazarısın.

# GÖREV
Aşağıdaki içerik beğenilmedi. FARKLI bir içerik üret.

# FİKİR
- Başlık: {idea_title}
- Platform: {target_platform}
- Format: {content_format}

# ESKİ İÇERİK (beğenilmedi)
- Eski Hook: {old_hook}
- Eski Caption başlangıcı: {old_caption_start}

# MARKA
- Marka: {brand_name}

# EK BAĞLAM
{additional_context}

# KURAL
- Aynı fikir için FARKLI yaklaşımla içerik üret
- Farklı hook stilleri dene
- Daha yaratıcı ol

# ÇIKTI FORMATI
(SOCIAL_CONTENT_PROMPT ile aynı JSON formatı)
'''
