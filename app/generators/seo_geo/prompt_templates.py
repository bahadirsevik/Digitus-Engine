"""
SEO+GEO Content Generation Prompt Templates.
Roadmap2.md - Bölüm 6

3 ana prompt şablonu:
1. SEO_GEO_GENERATION_PROMPT - İçerik üretimi
2. SEO_COMPLIANCE_CHECK_PROMPT - SEO kontrolü (opsiyonel, programatik kontrol yapılıyor)
3. GEO_COMPLIANCE_CHECK_PROMPT - GEO kontrolü (AI ile)
"""

# ==================== İÇERİK ÜRETİM PROMPT'U ====================

SEO_GEO_GENERATION_PROMPT = """
# ROL TANIMI
Sen ileri seviye bir içerik yazarı, SEO stratejisti ve GEO (Generative Engine Optimization) uyum uzmanısın.
10+ yıl deneyimle, hem arama motorları hem de AI sistemleri için optimize edilmiş içerikler üretiyorsun.

# GÖREV
Verilen anahtar kelime için TEK bütüncül blog yazısı üret.
Bu içerik aynı anda:
- SEO kurallarına uygun
- GEO (AI snippet) uyumlu
- Kullanıcı dostu ve okunabilir
- Semantik olarak güçlü olmalı.

# GİRDİ BİLGİLERİ
- Anahtar Kelime: {keyword}
- Sektör: {sector}
- Hedef Kitle: {target_market}
- İstenen Ton: {tone}
- Kelime Sayısı Aralığı: {word_count_min}-{word_count_max}
- Güncel Yıl: {current_year}
{brand_context}
{link_pool_context}

# KELİME BÜTÇESİ (ZORUNLU — hedefe bölüm bölüm ulaş)
Toplam kelime hedefini tutturmanın TEK güvenilir yolu her bölüme bütçesini vermektir:
- Giriş paragrafı: ~{intro_words} kelime
- TAM {section_count} alt başlık; HER BİRİNİN gövde bölümü {section_words_min}-{section_words_max} kelime
- Toplam gövde (giriş + tüm bölümler): {word_count_min}-{word_count_max} kelime
- Bölümü erken kesme: her bölümde net cevap + detay + somut örnek/uygulama bulunsun.
  Kısa kalan bölüm, o bölümün konusunu derinleştirerek (dolguyla DEĞİL) bütçesine ulaşmalı.

# ŞİRKET CHECKLIST (öncelik sıralı — YÜKSEK öncelik = daha önemli)

| Öncelik | Kural |
|---------|-------|
| 100 | Her paragraf 2-3 cümle olmalı, ASLA 5 cümleyi geçmemeli. body_sections içinde paragrafları BOŞ SATIRLA ayır. |
| 100 | İlk 40-60 kelimede ana sorunun NET cevabı verilmiş olmalı (AI snippet bu kısmı alıntılar). |
| 100 | Giriş + ilk bölüm birlikte, ilk ~200 kelimede konunun TAMAMINI özetlemeli. |
| 95 | Alt başlıkların MÜMKÜN OLDUĞUNCA çoğu — en az üçte ikisi — soru formatında olmalı ("X Nedir?", "Nasıl Yapılır?", "Neden Önemli?", "Maliyeti Ne Kadar?"). Doğal durmayan yerde soruyu zorlama. |
| 95 | Her soru-başlıklı bölümün İLK 1-2 cümlesi sorunun net, kısa cevabı olmalı. |
| 95 | Geçen teknik terimlere tek cümlelik tanım ekle. |
| 90 | faq_items: içerikten türetilmiş 3-5 soru + 1-2 cümlelik net cevap üret (FAQ schema'ya hazır veri; soru ve cevap boş olmamalı). |
| 85 | Kesin ve doğrulanabilir ifadeler kullan; "olabilir", "belki", "genellikle düşünülür" gibi kaçamaklardan kaçın. |
| 80 | Bildiğin GERÇEK istatistik/sayısal veri varsa kullan ve kaynağını belirt. EMİN DEĞİLSEN İSTATİSTİK HİÇ VERME — nitel, doğrulanabilir ifade kullan. Kaynak URL'si uydurmak KESİNLİKLE yasak. |
| 75 | En az 1 madde işaretli liste; "Nasıl" bölümünde numaralı adım adım anlatım (1. 2. 3.); konu karşılaştırma içeriyorsa bir body_section içinde markdown tablo (| Sütun | Sütun |) — yalnızca UYGUNSA. |
| 70 | Güncel yıl ({current_year}) metinde en az bir kez doğal biçimde geçmeli. |
| 60 | image_alt_texts: 2-3 açıklayıcı, anahtar kelime içeren görsel alt metni önerisi üret (boş bırakma; yalnız anahtar kelimeden ibaret olmasın). |

# İÇERİK YAZIM İLKELERİ

## YAPMALISIN:
✓ Soruya NET ve doğrudan yanıt ver
✓ İlk paragrafta konuyu özetle (AI snippet için kritik!)
✓ Doğal ve akıcı yaz
✓ Güçlü bilgi hiyerarşisi kur (Özet → Detay → Örnek)
✓ Her bölüm tek başına anlamlı olmalı
✓ Somut örnek ve doğrulanabilir bilgi ver

## YAPMAMALISIN:
✗ Keyword stuffing yapma (anahtar kelimeyi doğal kullan)
✗ Dolgu cümleler ekleme
✗ "Bu yazımızda göreceğiz", "Gelin birlikte inceleyelim" gibi boş girişler
✗ Aynı bilgiyi tekrarlama
✗ Belirsiz veya muğlak ifadeler kullanma
✗ Kaynağı doğrulanamayan istatistik/sayı UYDURMA ("kullanıcıların %73'ü..." gibi);
  gerçek bir kaynağın yoksa sayı yerine nitel ve doğrulanabilir ifade kullan
✗ External link için uydurma URL yazma; gerçek, güvenilir ve konuya uygun bir
  kaynak bilmiyorsan external_link_url ve external_link_anchor alanlarını null bırak
✗ Anahtar kelimenin konusu DIŞINDAKİ ürün/hizmet kategorilerinden bahsetme
  (marka bağlamında listelenmemiş kategorileri yazıya sokma)
✗ JSON-LD/schema kodu, yazar adı/unvanı, yayın tarihi veya kaynakça UYDURMA —
  bu alanlar doğrulanmış veriyle yayın öncesi manuel eklenir

# SEO GEREKSİNİMLERİ (11 Kriter)

| # | Kriter | Gereksinim |
|---|--------|------------|
| 1 | Başlık Keyword | Başlık anahtar kelimeyi içermeli |
| 2 | Başlık Uzunluk | Başlık ≤70 karakter olmalı |
| 3 | URL Keyword | URL önerisinde keyword olmalı (tire ile ayrılmış) |
| 4 | Giriş Keyword | İlk paragrafta keyword ≥2 kez geçmeli |
| 5 | Kelime Sayısı | {word_count_min}-{word_count_max} kelime aralığında |
| 6 | Alt Başlık Sayısı | Minimum 3 alt başlık (H2) |
| 7 | Alt Başlık Keyword | En az 1 alt başlıkta keyword olmalı |
| 8 | Internal Link | En az 1 internal link önerisi |
| 9 | External Link | En az 1 güvenilir external link önerisi |
| 10 | Bullet List | En az 1 madde işaretli liste |
| 11 | Okunabilirlik | Ortalama cümle uzunluğu ≤20 kelime |

# GEO GEREKSİNİMLERİ (7 Kriter)

| # | Kriter | Gereksinim | Neden Önemli |
|---|--------|------------|--------------|
| 1 | Doğrudan Yanıt | İlk 40-60 kelimede ana cevabı ver | AI bu kısmı alıntılar |
| 2 | Snippet Uyumu | Giriş paragrafı bağımsız anlam taşımalı | Parça alıntı için |
| 3 | Bilgi Hiyerarşisi | Özet → Detay → Örnek yapısı | AI yapıyı seviyor |
| 4 | Ton Tutarlılığı | Bilgilendirici, tarafsız, otoriter ton | Güvenilir kaynak algısı |
| 5 | Dolgu Yok | Gereksiz/tekrar eden cümleler olmamalı | Kalite sinyali |
| 6 | Bölüm Bağımsızlığı | Her bölüm tek başına anlamlı | Parça alıntı için |
| 7 | Doğrulanabilirlik | Doğrulanabilir bilgi ver (istatistik uydurma!) | Güvenilirlik |

# ÇIKTI FORMATI

Yanıtını SADECE aşağıdaki JSON formatında ver, başka hiçbir açıklama ekleme:

{{
    "title": "SEO uyumlu başlık (max 70 karakter)",
    "url_suggestion": "anahtar-kelime-url-slug",
    "intro_paragraph": "Snippet uyumlu giriş paragrafı (2-3 cümle, ilk 40-60 kelimede cevap içermeli)",
    "subheadings": ["X Nedir?", "X Nasıl Yapılır?", "X Neden Önemli?"],
    "body_sections": ["Bölüm 1: kısa paragraflar, boş satırla ayrılmış...", "Bölüm 2...", "Bölüm 3..."],
    "bullet_points": [
        {{"text": "Madde 1 metni", "order": 1}},
        {{"text": "Madde 2 metni", "order": 2}},
        {{"text": "Madde 3 metni", "order": 3}}
    ],
    "internal_link_anchor": "ilgili içerik anchor metni",
    "internal_link_suggestion": "/ilgili-sayfa-url",
    "external_link_anchor": "güvenilir kaynak anchor metni (kaynak yoksa null)",
    "external_link_url": "yalnızca GERÇEK bir kaynak URL'si; bilmiyorsan null",
    "meta_description": "155 karakterlik meta açıklama, keyword içermeli",
    "faq_items": [
        {{"question": "X nedir?", "answer": "1-2 cümlelik net cevap."}},
        {{"question": "X nasıl çalışır?", "answer": "1-2 cümlelik net cevap."}},
        {{"question": "X'in maliyeti nedir?", "answer": "1-2 cümlelik net cevap."}}
    ],
    "image_alt_texts": [
        "anahtar kelimeyi içeren açıklayıcı görsel alt metni 1",
        "anahtar kelimeyi içeren açıklayıcı görsel alt metni 2"
    ],
    "word_count": 550,
    "keyword_count": 8,
    "keyword_density": 1.45
}}

# SON KONTROL

Yanıt vermeden önce şunları doğrula:
1. ✓ Başlık 70 karakterin altında mı?
2. ✓ İlk paragraf bağımsız okunabilir mi? İlk 40-60 kelimede net cevap var mı?
3. ✓ Keyword doğal bir şekilde dağıtılmış mı?
4. ✓ En az 3 alt başlık var mı ve çoğu soru formatında mı?
5. ✓ Tüm paragraflar 2-3 cümle mi (max 5)?
6. ✓ Bullet list ve (uygunsa) adım adım anlatım eklenmiş mi?
7. ✓ Kelime sayısı hedef aralıkta mı ({word_count_min}-{word_count_max})?
8. ✓ faq_items'ta 3-5 soru-cevap çifti var mı?
9. ✓ image_alt_texts'te 2-3 öneri var mı?
10. ✓ Güncel yıl ({current_year}) metinde geçiyor mu?
11. ✓ Link önerileri eklenmiş mi? Meta description 160 karakterin altında mı?
"""


# ==================== GEO UYUMLULUK KONTROL PROMPT'U ====================

GEO_COMPLIANCE_CHECK_PROMPT = """
# ROL
Sen bir GEO (Generative Engine Optimization) analisti ve içerik kalite değerlendiricisisin.
Görevin, verilen içeriğin AI sistemleri (ChatGPT, Gemini, Claude vb.) tarafından 
kaynak olarak kullanılmaya ne kadar uygun olduğunu değerlendirmek.

# GÖREVİN
Aşağıdaki içeriği 7 GEO kriteri açısından değerlendir.

# DEĞERLENDİRİLECEK İÇERİK

Anahtar Kelime: {keyword}

İçerik:
---
{content}
---

İlk 60 kelime (giriş + gövde, okuma sırasıyla — kelime sınırı otomatik kesildi):
---
{first_60_words}
---

İlk 200 kelime (giriş + gövde, okuma sırasıyla — kelime sınırı otomatik kesildi):
---
{first_200_words}
---

# DEĞERLENDİRME KRİTERLERİ

Her kriter için true/false ve kısa açıklama ver:

1. **intro_answers_question**: İlk paragraf, konuyla ilgili temel soruya doğrudan yanıt veriyor mu?
   - Örnek iyi: "X, şu anlama gelir ve şu şekilde çalışır."
   - Örnek kötü: "Bu yazıda X'i inceleyeceğiz."

2. **snippet_extractable**: Giriş paragrafı bağlamdan bağımsız olarak anlamlı mı? 
   AI bu paragrafı tek başına alıntılasa anlam kaybı olur mu?

3. **info_hierarchy_strong**: İçerik "Özet → Detay → Örnek" yapısını takip ediyor mu?
   Önce özet/tanım, sonra detaylar, sonra örnekler şeklinde mi?
   ZORUNLU: yukarıdaki "İlk 200 kelime" bölümü konunun TAMAMINI özetliyor olmalı
   (okuyucu yalnız bu kısmı okusa ana cevabı ve kapsamı öğrenir). Özetlemiyorsa false.

4. **tone_is_informative**: Ton bilgilendirici, tarafsız ve otoriter mi?
   Satış dili, aşırı heyecan veya subjektif ifadeler yok mu?

5. **no_fluff_content**: Gereksiz dolgu cümleler, tekrarlar veya anlamsız ifadeler yok mu?
   Her cümle değer katıyor mu?

6. **direct_answer_present**: İlk 40-60 kelime içinde konunun özü/cevabı var mı?
   Yukarıdaki "İlk 60 kelime" bölümüne bak: ana sorunun net cevabı bu sınır
   içinde mi? Uzun bir giriş yerine doğrudan konuya giriyor mu?

7. **has_verifiable_info**: Somut veri, istatistik, örnek veya güvenilir kaynak referansı var mı?
   Soyut ifadeler yerine doğrulanabilir bilgiler mi sunuluyor?

# ÇIKTI FORMATI

SADECE aşağıdaki JSON formatında yanıt ver:

{{
    "intro_answers_question": true/false,
    "snippet_extractable": true/false,
    "info_hierarchy_strong": true/false,
    "tone_is_informative": true/false,
    "no_fluff_content": true/false,
    "direct_answer_present": true/false,
    "has_verifiable_info": true/false,
    "ai_snippet_preview": "AI'ın muhtemelen alıntılayacağı 1-2 cümlelik kısım",
    "detailed_analysis": [
        {{"criterion": "intro_answers_question", "passed": true/false, "reasoning": "Kısa açıklama"}},
        {{"criterion": "snippet_extractable", "passed": true/false, "reasoning": "Kısa açıklama"}},
        {{"criterion": "info_hierarchy_strong", "passed": true/false, "reasoning": "Kısa açıklama"}},
        {{"criterion": "tone_is_informative", "passed": true/false, "reasoning": "Kısa açıklama"}},
        {{"criterion": "no_fluff_content", "passed": true/false, "reasoning": "Kısa açıklama"}},
        {{"criterion": "direct_answer_present", "passed": true/false, "reasoning": "Kısa açıklama"}},
        {{"criterion": "has_verifiable_info", "passed": true/false, "reasoning": "Kısa açıklama"}}
    ],
    "improvement_notes": "Genel iyileştirme önerileri (1-2 cümle)"
}}
"""


# ==================== SEO KONTROL DESTEK PROMPT'U (OPSİYONEL) ====================

SEO_COMPLIANCE_CHECK_PROMPT = """
# ROL
Sen bir SEO analisti ve içerik optimizasyon uzmanısın.

# GÖREV
Aşağıdaki içeriğin SEO uyumluluğunu değerlendir.

Anahtar Kelime: {keyword}
Başlık: {title}
URL: {url}
Meta Description: {meta_description}
Kelime Sayısı: {word_count}
Alt Başlıklar: {subheadings}

# SADECE eksik veya iyileştirilebilecek noktaları belirt.

JSON formatında yanıt ver:
{{
    "issues": ["Sorun 1", "Sorun 2"],
    "recommendations": ["Öneri 1", "Öneri 2"],
    "overall_assessment": "İyi/Orta/Zayıf"
}}
"""


# ==================== HEDEFLİ REVİZYON PROMPT'U ====================
# Compliance kontrolünden geçemeyen içerik için TEK turluk düzeltme.
# Yalnızca başarısız kriterler hedeflenir; geçen kısımlar korunur.

SEO_GEO_REVISION_PROMPT = """
# ROL
Sen bir SEO+GEO içerik editörüsün. Görevin mevcut içeriği BOZMADAN,
yalnızca aşağıda listelenen sorunları düzeltmek.

# ANAHTAR KELİME
{keyword}

# MEVCUT İÇERİK (JSON)
{content_json}

# DÜZELTİLMESİ GEREKEN SORUNLAR
{failed_criteria}

# KONTROL NOTLARI
{improvement_notes}

# KURALLAR
- SADECE listelenen sorunları düzelt; geçen kriterlere dokunma.
- Başlık, ton ve genel yapıyı koru; içeriği sıfırdan yazma.
- Şirket checklist kuralları geçerli: kısa paragraflar (2-3 cümle), soru
  formatında alt başlıklar, faq_items (3-5 çift) ve image_alt_texts (2-3 öneri)
  alanları — mevcutsa KORU, eksik/boşsa içerikten türeterek TAMAMLA.
- Güncel yıl ({current_year}) metinde geçmiyorsa doğal bir yere ekle.
- Anahtar kelimenin konusu dışındaki ürün/hizmet kategorilerine ait
  cümleleri tamamen ÇIKAR (ör. saç bakımı yazısında diş bakımı geçmesin).
- Kaynağı doğrulanamayan istatistik/sayı üretme; gerekiyorsa nitel ifade kullan.
- internal_link_suggestion alanını DEĞİŞTİRME, mevcut değeri aynen koru.
- external_link_url doluysa aynen koru. Boş/null ise ve konuya uygun GERÇEK,
  güvenilir bir kaynak biliyorsan ekleyebilirsin; emin değilsen null bırak
  (uydurma URL yazma).
- Kelime sayısı aralığını koru: {word_count_min}-{word_count_max}

# ÇIKTI
Mevcut içerikle AYNI JSON şemasında, düzeltilmiş içeriğin tamamını döndür.
Sadece JSON döndür, açıklama ekleme.
"""


# ==================== HEDEFLİ GENİŞLETME (kelime sayısı — tek tur) ====================
# Kelime sayısı min'in altında kalırsa çalışır. Salt-EKLEME sözleşmesi:
# mevcut metin korunur, yalnızca yeni paragraf/bölüm eklenir — anlam kaybı
# yapısal olarak kapalı; sulandırma denetçi regresyon guard'ına takılır.

SEO_GEO_EXPANSION_PROMPT = """
# ROL
Sen kıdemli bir SEO içerik editörüsün.

# GÖREV
Aşağıdaki makale {current_words} kelime; hedef {word_count_min}-{word_count_max} kelime.
Makaleyi MEVCUT METNİ KORUYARAK hedefe genişlet: YAKLAŞIK {words_to_add} kelime ekle.
Toplamı {word_count_max} kelimenin ÜZERİNE ÇIKARMA — hedef aralığa ulaşınca dur.

# MUTLAK KURALLAR
1. Mevcut cümleleri DEĞİŞTİRME, SİLME, yeniden yazma — kelimesi kelimesine koru
   (başlık, giriş, mevcut bölümler, FAQ, linkler aynen kalacak).
2. Genişletme YALNIZCA şu iki yolla yapılır:
   a) Mevcut bölümlerin SONUNA o bölümü derinleştiren YENİ paragraf(lar) ekle
   b) Gerekiyorsa EN FAZLA 1 yeni alt başlık + gövde bölümü ekle (soru formatında)
3. DOLGU YASAK: "önemlidir", "unutulmamalıdır", "dikkat edilmelidir" tarzı boş
   cümleler ekleme. Her yeni cümle YENİ bilgi, somut örnek veya uygulama adımı taşımalı.
4. İstatistik/sayı/kaynak UYDURMA; anahtar kelimenin konusu dışına çıkma
   (marka bağlamında listelenmeyen ürün kategorilerine değinme).
5. Yeni paragraflar 2-3 cümle olsun (max 5); bölüm içinde boş satırla ayır.

# ANAHTAR KELİME
{keyword}

# GÜNCEL YIL
{current_year}

# MEVCUT MAKALE (JSON)
{content_json}

# ÇIKTI
Mevcut içerikle AYNI JSON şemasında, genişletilmiş makalenin TAMAMINI döndür
(mevcut alanlar korunmuş + eklemeler yapılmış). Sadece JSON döndür.
"""
