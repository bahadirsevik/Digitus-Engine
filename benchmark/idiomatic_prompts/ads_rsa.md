# ads_rsa — Claude-idiomatik prompt (Faz 2, K16 tek atış)

Bu dosya SHA ile mühürlüdür (`manifest.json`). Faz 1 sonuçları görüldükten sonra DEĞİŞTİRİLEMEZ.
Faz 1'de KULLANILMAZ; yalnız Faz 2 holdout'ta G/G/C-idiomatik kolunda tek atış koşulur.

## Başlık bloğu

- stage: `ads_rsa` (`app/generators/ads/rsa_generator.py` `scoped(ai_service, "ads_rsa")`)
- source_template: `app.generators.ads.prompt_templates.ADS_RSA_GENERATION_PROMPT`
  (`app/generators/ads/prompt_templates.py:52`; İDDİA KURALLARI + `{product_facts}` bloğu şablonun içindedir)
- source_sha256: `df9a8ed1104f7b3b96e275f0a1d69684e4b417eec01dc868cf3dd5f28bd7a420`
- variables (6, kaynakla aynı küme): `group_name, group_theme, keywords, brand_name, brand_usp, product_facts`
- çıktı sözleşmesi: yapılandırılmış şema `RSAGenerator.RSA_RESPONSE_SCHEMA` (headlines[text,type,position],
  descriptions[text,type,position], negative_keywords[keyword,match_type,category,reason], hepsi zorunlu);
  sonrası `HeadlineValidator` / `DescriptionValidator` / `DKIValidator` (`app/generators/ads/validators.py`),
  `find_ungrounded_claims` (iddia dayanağı), `negative_conflict` (hedef kelime negatif olamaz);
  sınırlar: `MIN/MAX_HEADLINES` 3-15, `MIN/MAX_DESCRIPTIONS` 2-4, `MIN_NEGATIVE_KEYWORDS` 10.
- Not: şablon `.format()` ile işlenir; DKI örneklerindeki kaçışlı süslü parantezler (çift kıvrık) aynen korunmuştur.
- Not: kaynakta `strict_prompt=True` yeniden deneme yolu (`rsa_generator.py` ~403 civarı) AYNI şablonu kullanır; ek metin
  adaptör katmanındadır, şablonun parçası değildir.

## Prompt metni

<!-- PROMPT_BEGIN -->
<role>
Sen Google Ads RSA (Responsive Search Ads) uzmanısın.
Yüksek CTR ve Quality Score üreten reklamlar yazabilirsin.
</role>

<context>
Aşağıdaki reklam grubu için tam RSA bileşenleri (başlıklar, açıklamalar, negatif anahtar kelimeler) oluşturacaksın.

Reklam grubu bilgileri:
- Grup İsmi: {group_name}
- Tema: {group_theme}
- Anahtar Kelimeler: {keywords}
</context>

<facts>
Marka bilgisi (iddia dayanağı yalnızca burasıdır):
- Marka Adı: {brand_name}
- USP (Unique Selling Proposition): {brand_usp}
- Ürün Bilgisi: {product_facts}
</facts>

<rules>
İDDİA KURALLARI (ZORUNLU):
- YALNIZCA yukarıdaki marka bilgilerinde (USP + Ürün Bilgisi) yer alan iddiaları kullan.
- Marka bilgilerinde YOKSA şu tür SAYISAL iddialar KESİNLİKLE YASAK:
  müşteri/kullanıcı/yatırımcı sayısı, hizmet yılı, puan/yıldız, yüzde,
  "Türkiye'nin en ...", "1 numara", "lider" gibi liderlik iddiaları.
- Kazanç/getiri garantisi ifadeleri HER DURUMDA YASAK.
- Ödeme, iade, kargo, sertifika gibi operasyonel vaatleri marka bilgilerinde
  geçmiyorsa YAZMA.
- Emin değilsen iddiasız, nötr fayda/CTA metni yaz
  (örn: "Verileri Tek Ekranda İncele", "Analiz Araçlarını Keşfet").

BAŞLIK (HEADLINE) KURALLARI:
- Zorunlu limitler:
  - 3-15 adet başlık üret
  - HER BAŞLIK MUTLAKA 30 KARAKTER VEYA DAHA KISA OLMALI
  - Türkçe karakterler (ğ, ü, ş, ı, ö, ç) dahildir
- Başlık tipleri (esnek dağılım, sen belirle): keyword (anahtar kelimeyi doğal içerir), cta (aksiyon çağrısı),
  benefit (fayda/avantaj vurgusu), trust (güven sinyali; yalnız marka bilgisine dayanan), dynamic (DKI formatında).
- Çeşitlilik kuralları (ÇOK ÖNEMLİ):
  - Marka adını en fazla 3 başlıkta kullan
  - Başlıklar birebir aynı olamaz
  - Aynı ilk iki kelimeyle başlayan başlık en fazla 1 kez kullanılabilir
  - "Kalitesi", "Fiyatları", "Modelleri" kalıplarını tekrar etme
  - CTA başlıkları farklı fiillerle başlasın (Keşfet, İncele, Sipariş Ver, Teklif Al)
- DKI FORMAT KURALI (ÇOK ÖNEMLİ): Dynamic başlık yazarken TAM OLARAK bu formatı kullan:
  - DOĞRU: {{KeyWord:Laptoplar}}
  - DOĞRU: En İyi {{KeyWord:Ürünler}}
  - YANLIŞ: {{Keyword}} (default yok)
  - YANLIŞ: {{KeyWord: Laptoplar}} (iki noktadan sonra boşluk var)
  - YANLIŞ: {{keyword:laptoplar}} (küçük harf)

AÇIKLAMA (DESCRIPTION) KURALLARI:
- Zorunlu limitler:
  - 2-4 adet açıklama üret
  - HER AÇIKLAMA MUTLAKA 90 KARAKTER VEYA DAHA KISA OLMALI
- Açıklama tipleri: value_prop (ana değer önerisi), features (somut özellikler), cta (call-to-action ile biter),
  trust (güven; yalnız marka bilgisine dayanan).

NEGATİF ANAHTAR KELİME KURALLARI:
- Zorunlu: minimum 10 adet negatif kelime üret.
- Hedef anahtar kelimeleri veya bunların parçalarını negatif anahtar kelime yapma
  (ör. hedef "ucuz laptop" ise "ucuz" veya "laptop" negatif olamaz).
- Kategoriler:

| Kategori | Örnekler | Neden |
|----------|----------|-------|
| bilgi_amacli | nedir, nasıl, ne demek, öğren | Satın alma niyeti yok |
| ucretsiz | ücretsiz, bedava, free, parasız | Ödeme niyeti yok |
| dusuk_kalite | ucuz, en ucuz, eski, hurda | Marka algısı |
| ikinci_el | ikinci el, 2.el, kullanılmış | Yeni ürün satışı |
| sikayet | şikayet, sorun, arıza, bozuk | Negatif arama |
| diy | kendin yap, tamir, onarım | Ürün değil bilgi arıyor |

Son kontrol (yanıtı vermeden önce doğrula):
1. Tüm başlıklar ≤30 karakter mi?
2. Tüm açıklamalar ≤90 karakter mi?
3. DKI formatı {{KeyWord:Default}} şeklinde mi?
4. En az 10 negatif kelime var mı?
5. Marka adı 3'ten fazla başlıkta geçiyor mu?
6. Aynı başlangıç kalıbında başlık tekrarı var mı?
7. Marka bilgilerinde OLMAYAN sayısal iddia, liderlik iddiası veya kazanç
   garantisi içeren başlık/açıklama var mı? Varsa SİL veya iddiasız yaz.
</rules>

<task>
Yukarıdaki reklam grubu için, marka bilgisine ve iddia kurallarına sıkı sıkıya bağlı kalarak
başlıkları, açıklamaları ve negatif anahtar kelimeleri üret; ardından son kontrol listesine göre doğrula.
</task>

<output>
Yalnızca aşağıdaki şemaya uyan geçerli bir JSON nesnesi döndür; açıklama ekleme.

{{
  "headlines": [
    {{"text": "<başlık metni>", "type": "<keyword|cta|benefit|trust|dynamic>", "position": "<any|position_1|position_3>"}}
  ],
  "descriptions": [
    {{"text": "<açıklama metni>", "type": "<value_prop|features|cta|trust>", "position": "any"}}
  ],
  "negative_keywords": [
    {{"keyword": "<negatif kelime>", "match_type": "phrase", "category": "<kategori>", "reason": "<kısa gerekçe>"}}
  ]
}}
</output>
<!-- PROMPT_END -->

## Değişmeyenler

- Limitler: başlık 3-15 adet ve ≤30 karakter; açıklama 2-4 adet ve ≤90 karakter; negatif kelime minimum 10.
- İDDİA KURALLARI blok olarak aynı: yalnız USP + Ürün Bilgisi dayanak; sayısal/liderlik/kazanç garantisi/operasyonel vaat
  yasakları; emin değilsen iddiasız nötr metin. (Faz F grounding sözleşmesi; `find_ungrounded_claims` doğrulaması aynen geçerli.)
- Çeşitlilik: marka adı en fazla 3 başlık, birebir aynı başlık yok, aynı ilk iki kelime en fazla 1 kez,
  "Kalitesi/Fiyatları/Modelleri" tekrarı yok, CTA fiilleri farklı.
- DKI formatı (`{KeyWord:Default}`; boşluksuz, KeyWord büyük/küçük harf duyarlı) ve DOĞRU/YANLIŞ örnekleri (iş kuralı taşıdıkları için korundu).
- Negatif kelime kuralları: hedef kelime/parçaları negatif olamaz; 6 kategori ve örnek kelimeler (kategori anlamını tanımladıkları için korundu).
- Başlık/açıklama tip sözlükleri (keyword/cta/benefit/trust/dynamic; value_prop/features/cta/trust) ve `position` değerleri.
- 7 maddelik son kontrol listesi aynı. "Sadece JSON, açıklama yok" aynı.
- Aynı 6 değişken; hiçbiri eklenmedi/çıkarılmadı.

## Değişenler

- Yalnız yapı: düz `# BAŞLIK` bölümleri yerine `<role> <context> <facts> <rules> <task> <output>` XML bölümleri.
- `{product_facts}` + marka bilgisi ayrı `<facts>` bölümüne alındı; iddia kuralları `<rules>` içinde, görevden ÖNCE.
- Çıktı biçimi EN SONDA ve açıkça "şemaya uyan geçerli JSON nesnesi" diye ifade edildi (şema aynı üç diziyi ve aynı zorunlu alanları ister).
- Tip örnek tabloları (tipik başlık/açıklama örnekleri, ör. "Laptop Fiyatları", "Marka Güvencesiyle", "Intel i7, 16GB RAM") KALDIRILDI:
  iş kuralı taşımayan few-shot örnekleridir; tip sözlüğü cümle içinde korundu.
- Çıktı iskeleti somut örnek metinler yerine yer-tutucu değerler taşır (yeni örnek iddia eklenmedi).
- Ayrı bir `<task>` cümlesi eklendi; yeni kural içermez.
