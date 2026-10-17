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
Aşağıdaki {keyword_count} anahtar kelimeyi TAM {group_count} reklam grubuna ayır.

# KURALLAR
1. TAM {group_count} grup oluştur; grupların kelime sayıları tam olarak şunlar olmalı: {group_sizes}
   Her kelime TAM BİR grupta yer almalı (kelime atlama, tekrar etme).
2. Önce aynı ÜRÜN ve aynı ARAMA NİYETİndeki kelimeleri bir araya getir; küçük temaları
   en yakın temayla birleştir, gerekirse grup adını/temasını genişlet
   Grup adı ve teması gruptaki TÜM kelimeleri kapsamalı: hedef boyuta ulaşmak için farklı
   hizmetler/ürünler birleştirildiyse adı ve temayı buna göre genişlet
   (örn: "SEO ve Web Tasarım Hizmetleri"); gruptaki kelimelerin yalnızca bir kısmını
   anlatan dar bir ad verme
3. Niyet örnekleri (gruplar bu kadar ayrıntılı bölünemiyorsa en yakın niyette birleştir):
   - Satın alma niyeti (laptop al, notebook satın al)
   - Araştırma niyeti (laptop özellikleri, laptop karşılaştır)
   - Fiyat odaklı (ucuz laptop, laptop fiyatları)
   - Commercial (en iyi laptop)
4. Her gruba AÇIKLAYICI isim ver (örn: "Laptop Satın Alma - Fiyat Odaklı")
5. Kötü grup isimleri kullanma: "Grup 1", "Kelimeler A"
6. Grup sayısını ve boyutlarını yukarıdaki değerlerden değiştirme

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


# ==================== RSA CANDIDATES PROMPT (15 KW standardı) ====================
# Üretimde kullanılan aday prompt'u ADS_RSA_GENERATION_PROMPT'tan TÜRETİLİR:
# kaynak sabit benchmark/idiomatic_prompts/manifest.json'da SHA ile mühürlü
# (tests/unit/test_idiomatic_prompts.py) — sabitin kendisi DEĞİŞTİRİLMEZ.
# Yerleştirme noktası kaybolursa import anında patlar (sessiz kayma olmasın).

_KEYWORD_USE_BLOCK = '''# ANAHTAR KELİME KULLANIMI (ÖNEMLİ)
- Başlıklar ve açıklamalar AKICI, DOĞAL ve dilbilgisi doğru Türkçe olmalı; arama sorgusu gibi
  okunan, eksik ekli ya da yalnızca kelimeleri art arda sıralayan metin yazma
- Grubun anahtar kelimelerinden doğal okunanları başlıklarda ÖNCELİKLE kullan; bir anahtar
  kelimeyi yalnızca doğal okunduğu yerde, BÜTÜN hâlinde (ek almadan, kelimeleri bölmeden,
  aynı sırada) kullan
- Başlıkları ve açıklamaları BU GRUBUN anahtar kelimelerinden ve temasından üret. Marka ve
  ürün bilgisini yalnızca grubun temasıyla ilgili olduğu ölçüde kullan; markanın bu grupla
  ilgisi olmayan hizmetlerini başlık veya açıklamaya taşıma
- Bir anahtar kelime doğal bir başlık oluşturmuyorsa (ör. "... fiyat", "... vermek" gibi arama
  sorgusu kalıpları, eksik ekli kelimeler, şehir + genel kelime) ZORLAMA; aynı anlamı doğal
  Türkçeyle anlat. Anahtar kelime sayısını artırmak için dilbilgisini ASLA bozma
- Her açıklamada, grubun anahtar kelimelerinden cümleye doğal yerleşen EN AZ BİR tanesini
  BÜTÜN hâlinde kullan; diğer anahtar kelimeleri açıklamalara zorla doldurma
- 30 karakterden uzun anahtar kelimeler başlığa SIĞMAZ; bunları yalnızca açıklamalarda kullan: {long_keywords}
{completion_block}
---

'''

_CANDIDATES_EDITS = (
    ("- 3-15 adet başlık üret", "- {headline_instruction}"),
    ("- 2-4 adet açıklama üret", "- {description_instruction}"),
    ("# NEGATİF ANAHTAR KELİME KURALLARI",
     _KEYWORD_USE_BLOCK + "# NEGATİF ANAHTAR KELİME KURALLARI"),
)


def _derive_candidates_prompt(base: str) -> str:
    for old, new in _CANDIDATES_EDITS:
        if base.count(old) != 1:
            raise RuntimeError(f"ADS_RSA_GENERATION_PROMPT kaymış, bulunamadı: {old!r}")
        base = base.replace(old, new)
    return base


ADS_RSA_CANDIDATES_PROMPT = _derive_candidates_prompt(ADS_RSA_GENERATION_PROMPT)


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
