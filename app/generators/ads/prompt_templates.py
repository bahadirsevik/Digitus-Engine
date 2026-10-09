"""
Prompt templates for Google Ads RSA generation.

Contains:
1. ADS_GROUPING_PROMPT - Keywords to ad groups
2. ADS_RSA_GENERATION_PROMPT - Headlines + Descriptions + Negatives
3. HEADLINE_REGENERATION_PROMPT - Single headline fix
"""

# ==================== KEYWORD GROUPING PROMPT ====================

ADS_GROUPING_PROMPT = '''
# ROL
Sen deneyimli bir Google Ads uzmanısın. Kelimeleri optimize reklam gruplarına ayırma konusunda uzmansın.

# GÖREV
Aşağıdaki anahtar kelimeleri reklam gruplarına ayır.

# KURALLAR
1. Her grupta 3-7 kelime olmalı (çok az = verimsiz, çok fazla = alakasız eşleşme)
2. Aynı SATIN ALMA NİYETİndeki kelimeler bir grupta olmalı
3. Farklı niyetler farklı gruplarda:
   - Satın alma niyeti (laptop al, notebook satın al)
   - Araştırma niyeti (laptop özellikleri, laptop karşılaştır)
   - Fiyat odaklı (ucuz laptop, laptop fiyatları)
   - Commercial (en iyi laptop)
4. Her gruba AÇIKLAYICI isim ver (örn: "Laptop Satın Alma - Fiyat Odaklı")
5. Kötü grup isimleri kullanma: "Grup 1", "Kelimeler A"
6. Toplam grup sayısı yaklaşık: ceil(kelime sayısı / 5)

# ANAHTAR KELİMELER
{keywords_json}

# ÇIKTI FORMATI (JSON)
{{
  "ad_groups": [
    {{
      "name": "Açıklayıcı Grup İsmi",
      "theme": "Bu gruptaki kullanıcıların ortak özelliği/niyeti",
      "keyword_ids": [1, 5, 12],
      "keywords": ["kelime1", "kelime2", "kelime3"]
    }}
  ]
}}

Sadece JSON döndür, açıklama ekleme.
'''


# ==================== RSA GENERATION PROMPT ====================

ADS_RSA_GENERATION_PROMPT = '''
# ROL
Sen Google Ads RSA (Responsive Search Ads) uzmanısın. 
Yüksek CTR ve Quality Score üreten reklamlar yazabilirsin.

# GÖREV
Aşağıdaki reklam grubu için tam RSA bileşenleri oluştur.

# REKLAM GRUBU BİLGİLERİ
- Grup İsmi: {group_name}
- Tema: {group_theme}
- Anahtar Kelimeler: {keywords}

# MARKA BİLGİSİ
- Marka Adı: {brand_name}
- USP (Unique Selling Proposition): {brand_usp}
- Ürün Bilgisi: {product_facts}

---

# İDDİA KURALLARI (ZORUNLU)
- YALNIZCA yukarıdaki marka bilgilerinde (USP + Ürün Bilgisi) yer alan iddiaları kullan.
- Marka bilgilerinde YOKSA şu tür SAYISAL iddialar KESİNLİKLE YASAK:
  müşteri/kullanıcı/yatırımcı sayısı, hizmet yılı, puan/yıldız, yüzde,
  "Türkiye'nin en ...", "1 numara", "lider" gibi liderlik iddiaları.
- Kazanç/getiri garantisi ifadeleri HER DURUMDA YASAK.
- Ödeme, iade, kargo, sertifika gibi operasyonel vaatleri marka bilgilerinde
  geçmiyorsa YAZMA.
- Emin değilsen iddiasız, nötr fayda/CTA metni yaz
  (örn: "Verileri Tek Ekranda İncele", "Analiz Araçlarını Keşfet").

---

# BAŞLIK (HEADLINE) KURALLARI

## Zorunlu Limitler
- 3-15 adet başlık üret
- ⚠️ HER BAŞLIK MUTLAKA 30 KARAKTER VEYA DAHA KISA OLMALI ⚠️
- Türkçe karakterler (ğ, ü, ş, ı, ö, ç) dahildir

## Başlık Tipleri (esnek dağılım, AI belirler)
| Tip | Açıklama | Örnek |
|-----|----------|-------|
| keyword | Anahtar kelimeyi doğal içerir | "Laptop Fiyatları" |
| cta | Aksiyon çağrısı | "Hemen Keşfet" |
| benefit | Fayda/avantaj vurgusu | "Verileri Tek Ekranda İncele" |
| trust | Güven sinyali (yalnız marka bilgisine dayanan) | "Marka Güvencesiyle" |
| dynamic | DKI formatında | "{{KeyWord:Teknoloji}}" |

## Çeşitlilik Kuralları (ÇOK ÖNEMLİ)
- Marka adını en fazla 3 başlıkta kullan
- Başlıklar birebir aynı olamaz
- Aynı ilk iki kelimeyle başlayan başlık en fazla 1 kez kullanılabilir
- "Kalitesi", "Fiyatları", "Modelleri" kalıplarını tekrar etme
- CTA başlıkları farklı fiillerle başlasın (Keşfet, İncele, Sipariş Ver, Teklif Al)

## DKI FORMAT KURALI (ÇOK ÖNEMLİ!)
Dynamic başlık yazarken TAM OLARAK bu formatı kullan:
✅ DOĞRU: {{KeyWord:Laptoplar}}
✅ DOĞRU: En İyi {{KeyWord:Ürünler}}
❌ YANLIŞ: {{Keyword}} (default yok)
❌ YANLIŞ: {{KeyWord: Laptoplar}} (iki noktadan sonra boşluk var)
❌ YANLIŞ: {{keyword:laptoplar}} (küçük harf)

---

# AÇIKLAMA (DESCRIPTION) KURALLARI

## Zorunlu Limitler
- 2-4 adet açıklama üret
- ⚠️ HER AÇIKLAMA MUTLAKA 90 KARAKTER VEYA DAHA KISA OLMALI ⚠️

## Açıklama Tipleri
| Tip | Açıklama | Örnek |
|-----|----------|-------|
| value_prop | Ana değer önerisi | "Laptop modellerini tek ekranda karşılaştırın." |
| features | Somut özellikler | "Intel i7, 16GB RAM, 512GB SSD." |
| cta | Call-to-action ile biter | "Hemen keşfet, sana uygun modeli bul!" |
| trust | Güven (yalnız marka bilgisine dayanan) | "Marka bilgilerindeki güven unsurunu kullan." |

---

# NEGATİF ANAHTAR KELİME KURALLARI

## Zorunlu
- Minimum 10 adet negatif kelime üret
- Hedef anahtar kelimeleri veya bunların parçalarını negatif anahtar kelime yapma
  (ör. hedef "ucuz laptop" ise "ucuz" veya "laptop" negatif olamaz)

## Kategoriler
| Kategori | Örnekler | Neden |
|----------|----------|-------|
| bilgi_amacli | nedir, nasıl, ne demek, öğren | Satın alma niyeti yok |
| ucretsiz | ücretsiz, bedava, free, parasız | Ödeme niyeti yok |
| dusuk_kalite | ucuz, en ucuz, eski, hurda | Marka algısı |
| ikinci_el | ikinci el, 2.el, kullanılmış | Yeni ürün satışı |
| sikayet | şikayet, sorun, arıza, bozuk | Negatif arama |
| diy | kendin yap, tamir, onarım | Ürün değil bilgi arıyor |

---

# ÇIKTI FORMATI (JSON)

{{
  "headlines": [
    {{"text": "Laptop Fiyatları", "type": "keyword", "position": "any"}},
    {{"text": "Bugün Keşfet", "type": "cta", "position": "position_3"}},
    {{"text": "Marka Güvencesiyle", "type": "trust", "position": "any"}},
    {{"text": "{{KeyWord:Teknoloji}}", "type": "dynamic", "position": "position_1"}}
  ],
  "descriptions": [
    {{"text": "Laptop seçeneklerini tek ekranda inceleyin. Hemen keşfedin!", "type": "value_prop", "position": "any"}}
  ],
  "negative_keywords": [
    {{"keyword": "nedir", "match_type": "phrase", "category": "bilgi_amacli", "reason": "Bilgi amaçlı arama"}}
  ]
}}

# SON KONTROL (Yanıt vermeden önce)
1. Tüm başlıklar ≤30 karakter mi?
2. Tüm açıklamalar ≤90 karakter mi?
3. DKI formatı {{KeyWord:Default}} şeklinde mi?
4. En az 10 negatif kelime var mı?
5. Marka adı 3'ten fazla başlıkta geçiyor mu?
6. Aynı başlangıç kalıbında başlık tekrarı var mı?
7. Marka bilgilerinde OLMAYAN sayısal iddia, liderlik iddiası veya kazanç
   garantisi içeren başlık/açıklama var mı? Varsa SİL veya iddiasız yaz.

Sadece JSON döndür, açıklama ekleme.
'''


# ==================== HEADLINE REGENERATION PROMPT ====================

HEADLINE_REGENERATION_PROMPT = '''
# GÖREV
Aşağıdaki Google Ads başlığını MUTLAKA 30 karakter veya daha kısa olacak şekilde yeniden yaz.

# ORİJİNAL BAŞLIK
"{original}"

# ANAHTAR KELİME
"{keyword}"

# MEVCUT KARAKTER SAYISI
{char_count} karakter (MAXIMUM 30 olmalı!)

# KURALLAR
1. Anlamı koru
2. Anahtar kelimeyi mümkünse içer
3. Akıcı ve doğal Türkçe olsun
4. Kısaltmalar kullanabilirsin (ve → &)
5. Gereksiz kelimeleri çıkar

# ÇIKTI
Sadece yeni başlığı yaz. Tırnak kullanma. Açıklama ekleme.
'''


# ==================== DESCRIPTION SHORTENING PROMPT ====================

DESCRIPTION_SHORTENING_PROMPT = '''
# GÖREV  
Aşağıdaki Google Ads açıklamasını MUTLAKA 90 karakter veya daha kısa olacak şekilde yeniden yaz.

# ORİJİNAL AÇIKLAMA
"{original}"

# MEVCUT KARAKTER SAYISI
{char_count} karakter (MAXIMUM 90 olmalı!)

# KURALLAR
1. Ana mesajı koru
2. Cümleyi doğal şekilde bitir (nokta ile)
3. CTA varsa koru
4. Gereksiz sıfatları çıkar

# ÇIKTI
Sadece yeni açıklamayı yaz. Tırnak kullanma.
'''


# ==================== CLAIM REWRITE PROMPT (grounding — Faz F) ====================
# Mevcut HEADLINE_REGENERATION / DESCRIPTION_SHORTENING yalnız uzunluk odaklı;
# bu şablon desteklenmeyen iddiayı kaldırarak yeniden yazar (headline+description).

CLAIM_REWRITE_PROMPT = '''
# GÖREV
Aşağıdaki Google Ads metni, marka bilgilerinde DOĞRULANMAMIŞ iddia içeriyor.
Metni bu iddialar OLMADAN yeniden yaz.

# METİN ({asset_type})
"{original}"

# TESPİT EDİLEN DESTEKSİZ İDDİALAR
{claims}

# KULLANILABİLİR MARKA BİLGİLERİ (yalnız bunlara dayanan iddia yazılabilir)
{grounding_facts}

# KURALLAR
1. Desteksiz sayısal iddia (müşteri sayısı, yıl, puan, yüzde), liderlik iddiası
   ("en iyi", "lider", "1 numara") ve kazanç garantisi KULLANMA.
2. Marka bilgilerinde geçen bir gerçek varsa onu kullanabilirsin; yoksa
   iddiasız, nötr fayda/CTA metni yaz.
3. Anahtar kelime bağlamını koru: {keyword}
4. MUTLAKA {max_length} karakter veya daha kısa olsun.
5. Akıcı ve doğal Türkçe olsun.

# ÇIKTI
Sadece yeni metni yaz. Tırnak kullanma. Açıklama ekleme.
'''
