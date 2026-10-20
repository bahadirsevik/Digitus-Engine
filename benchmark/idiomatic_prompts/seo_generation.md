# seo_generation — Claude-idiomatik prompt (Faz 2, K16 tek atış)

Bu dosya SHA ile mühürlüdür (`manifest.json`). Faz 1 sonuçları görüldükten sonra DEĞİŞTİRİLEMEZ.
Faz 1'de KULLANILMAZ; yalnız Faz 2 holdout'ta G/G/C-idiomatik kolunda tek atış koşulur.

## Başlık bloğu

- stage: `seo_generation` (blog; `app/generators/seo_geo/seo_geo_generator.py` `scoped(ai_service, "seo_generation")`)
- source_template: `app.generators.seo_geo.prompt_templates.SEO_GEO_GENERATION_PROMPT`
  (`app/generators/seo_geo/prompt_templates.py:13`)
- source_sha256: `79dfa7e0fcd592254e5a0fee5543538f328c789ee2466ae2351cb3e794d29cfd`
- variables (13, kaynakla aynı küme): `keyword, sector, target_market, tone, word_count_min, word_count_max,
  intro_words, section_count, section_words_min, section_words_max, current_year, brand_context, link_pool_context`
- çıktı sözleşmesi: yapılandırılmış şema YOK (`complete_json`, düz JSON); zorunlu alanlar
  `title, intro_paragraph, subheadings, body_sections` (`_generate_raw_content.required_fields`);
  çıktı alan kümesi = kaynak şablondaki JSON iskeleti (title, url_suggestion, intro_paragraph, subheadings,
  body_sections, bullet_points, internal_link_*, external_link_*, meta_description, faq_items,
  image_alt_texts, word_count, keyword_count, keyword_density); sonrası SEO/GEO uyumluluk denetimi
  (`app/compliance/seo_checker.py`, `geo_checker.py`).
- Not: üretimde `prefilter_context` (varsa) formatlanmış prompt'un SONUNA düz metin olarak eklenir; bu davranış
  iki prompt için de aynıdır ve adaptör katmanındadır, şablonun parçası değildir.

## Prompt metni

<!-- PROMPT_BEGIN -->
<role>
Sen ileri seviye bir içerik yazarı, SEO stratejisti ve GEO (Generative Engine Optimization) uyum uzmanısın.
10+ yıl deneyimle, hem arama motorları hem de AI sistemleri için optimize edilmiş içerikler üretiyorsun.
</role>

<context>
Verilen anahtar kelime için TEK bütüncül blog yazısı üreteceksin. Bu içerik aynı anda SEO kurallarına uygun,
GEO (AI snippet) uyumlu, kullanıcı dostu, okunabilir ve semantik olarak güçlü olmalıdır.

Girdi bilgileri:
- Anahtar Kelime: {keyword}
- Sektör: {sector}
- Hedef Kitle: {target_market}
- İstenen Ton: {tone}
- Kelime Sayısı Aralığı: {word_count_min}-{word_count_max}
- Güncel Yıl: {current_year}
{brand_context}
{link_pool_context}
</context>

<facts>
Kelime bütçesi (ZORUNLU; toplam hedefi tutturmanın tek güvenilir yolu her bölüme bütçesini vermektir):
- Giriş paragrafı: ~{intro_words} kelime
- TAM {section_count} alt başlık; HER BİRİNİN gövde bölümü {section_words_min}-{section_words_max} kelime
- Toplam gövde (giriş + tüm bölümler): {word_count_min}-{word_count_max} kelime
- Bölümü erken kesme: her bölümde net cevap + detay + somut örnek/uygulama bulunsun. Kısa kalan bölüm,
  o bölümün konusunu derinleştirerek (dolguyla DEĞİL) bütçesine ulaşmalı.
</facts>

<rules>
Şirket checklist (öncelik sıralı; YÜKSEK öncelik = daha önemli):

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

Yapılacaklar:
- Soruya NET ve doğrudan yanıt ver.
- İlk paragrafta konuyu özetle (AI snippet için kritik).
- Doğal ve akıcı yaz.
- Güçlü bilgi hiyerarşisi kur (Özet → Detay → Örnek).
- Her bölüm tek başına anlamlı olsun.
- Somut örnek ve doğrulanabilir bilgi ver.

Yapılmayacaklar:
- Keyword stuffing yapma (anahtar kelimeyi doğal kullan).
- Dolgu cümleler ekleme.
- "Bu yazımızda göreceğiz", "Gelin birlikte inceleyelim" gibi boş girişler yazma.
- Aynı bilgiyi tekrarlama.
- Belirsiz veya muğlak ifadeler kullanma.
- Kaynağı doğrulanamayan istatistik/sayı UYDURMA ("kullanıcıların %73'ü..." gibi); gerçek bir kaynağın yoksa sayı yerine nitel ve doğrulanabilir ifade kullan.
- External link için uydurma URL yazma; gerçek, güvenilir ve konuya uygun bir kaynak bilmiyorsan external_link_url ve external_link_anchor alanlarını null bırak.
- Anahtar kelimenin konusu DIŞINDAKİ ürün/hizmet kategorilerinden bahsetme (marka bağlamında listelenmemiş kategorileri yazıya sokma).
- JSON-LD/schema kodu, yazar adı/unvanı, yayın tarihi veya kaynakça UYDURMA; bu alanlar doğrulanmış veriyle yayın öncesi manuel eklenir.

SEO gereksinimleri (11 kriter):

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

GEO gereksinimleri (7 kriter):

| # | Kriter | Gereksinim | Neden Önemli |
|---|--------|------------|--------------|
| 1 | Doğrudan Yanıt | İlk 40-60 kelimede ana cevabı ver | AI bu kısmı alıntılar |
| 2 | Snippet Uyumu | Giriş paragrafı bağımsız anlam taşımalı | Parça alıntı için |
| 3 | Bilgi Hiyerarşisi | Özet → Detay → Örnek yapısı | AI yapıyı seviyor |
| 4 | Ton Tutarlılığı | Bilgilendirici, tarafsız, otoriter ton | Güvenilir kaynak algısı |
| 5 | Dolgu Yok | Gereksiz/tekrar eden cümleler olmamalı | Kalite sinyali |
| 6 | Bölüm Bağımsızlığı | Her bölüm tek başına anlamlı | Parça alıntı için |
| 7 | Doğrulanabilirlik | Doğrulanabilir bilgi ver (istatistik uydurma!) | Güvenilirlik |

Son kontrol (yanıtı vermeden önce doğrula):
1. Başlık 70 karakterin altında mı?
2. İlk paragraf bağımsız okunabilir mi? İlk 40-60 kelimede net cevap var mı?
3. Keyword doğal bir şekilde dağıtılmış mı?
4. En az 3 alt başlık var mı ve çoğu soru formatında mı?
5. Tüm paragraflar 2-3 cümle mi (max 5)?
6. Bullet list ve (uygunsa) adım adım anlatım eklenmiş mi?
7. Kelime sayısı hedef aralıkta mı ({word_count_min}-{word_count_max})?
8. faq_items'ta 3-5 soru-cevap çifti var mı?
9. image_alt_texts'te 2-3 öneri var mı?
10. Güncel yıl ({current_year}) metinde geçiyor mu?
11. Link önerileri eklenmiş mi? Meta description 160 karakterin altında mı?
</rules>

<task>
Yukarıdaki bağlam, kelime bütçesi ve kurallara uyarak verilen anahtar kelime için tek bir blog yazısı üret.
Yazıyı bölüm bölüm planla: önce giriş ve her alt başlığın bütçesini tut, sonra tüm kriterleri son kontrol listesine göre doğrula.
</task>

<output>
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
</output>
<!-- PROMPT_END -->

## Değişmeyenler

Sayısal ve iş kuralları kaynakla BİREBİR aynıdır (testte rakam kümesi kaynağın alt kümesidir; yeni rakam yok):
- Başlık ≤70 karakter; meta description 155 karakter hedefi ve 160 karakter altı kontrolü.
- Kelime aralığı `{word_count_min}-{word_count_max}`, giriş `~{intro_words}`, TAM `{section_count}` alt başlık,
  bölüm başına `{section_words_min}-{section_words_max}` kelime.
- Checklist tablosu satır satır aynı (öncelikler 100/95/90/85/80/75/70/60): paragraf 2-3 cümle (en fazla 5),
  ilk 40-60 kelimede net cevap, ilk ~200 kelimede özet, alt başlıkların en az üçte ikisi soru, faq_items 3-5 çift
  (1-2 cümlelik cevap), image_alt_texts 2-3 öneri, güncel yıl ({current_year}) en az 1 kez, en az 1 madde listesi.
- Uydurma yasakları: istatistik/sayı, kaynak URL'si, JSON-LD/yazar/yayın tarihi/kaynakça; konu dışı kategori yasağı;
  `external_link_url`/`external_link_anchor` bilinmiyorsa null.
- SEO 11 kriter ve GEO 7 kriter tabloları aynen (keyword ≥2 kez girişte, ≥3 H2, ortalama cümle ≤20 kelime vb.).
- 11 maddelik son kontrol listesi aynı.
- Çıktı: aynı JSON iskeleti, aynı alanlar, "SADECE JSON" talimatı.
- Aynı 13 değişken; hiçbiri eklenmedi/çıkarılmadı. Değişken yerleşimi (brand_context, link_pool_context) girdi bloğunda.

## Değişenler

- Yalnız yapı ve sıra: düz `# BAŞLIK` bölümleri yerine `<role> <context> <facts> <rules> <task> <output>` XML bölümleri.
- Kısıtlar (kural/kontrol listeleri) görevden ÖNCE; çıktı biçimi EN SONDA (orijinalde çıktı ortadaydı, son kontrol sondaydı).
- Orijinaldeki ✓/✗ simgeli YAPMALISIN/YAPMAMALISIN blokları düz "Yapılacaklar/Yapılmayacaklar" listelerine çevrildi.
- Ayrı bir `<task>` bölümü eklendi (tek cümlelik görev + bölüm bölüm planla); yeni kural içermez.
- Kelime bütçesi bloğu `<facts>` altına alındı.
- Few-shot yok; orijinaldeki gibi çıktı iskeletindeki yer-tutucu değerler (ve 550/8/1.45 örnek sayıları) korunmuştur.
