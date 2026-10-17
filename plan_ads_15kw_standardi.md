# ADS 15 KW Teslim Standardı — Uygulama Planı (rev.2.1, 17.10.2026 — Codex rev.2 incelemesi: "Faz 1'e başlanabilir")

Kaynak: ajans geri bildirimi ("her reklam grubu 15 KW + 15 başlık, KW'ler başlıkta,
4 açıklama"), Codex'in ilk planı, rev.1 Codex incelemesi (7 bulgu, hepsi kodda doğrulandı)
ve Google resmi dokümanlarıyla dış doğrulama (§8). Kod değişikliği henüz yok.
Kod tabanı: `Digitus-Engine-lean` / `deploy/lean-taslak`.

**Kapsam cümlesi:** Bu plan 15 KW / 15 başlık / 4 açıklama **hedefini** uygular; her üretimde
kesin 15/4 teslim **garantisi vermez**. Eksik sonuçlar kaydedilir ve açıkça bildirilir.

## 0. Kararlar

| # | Karar | Durum |
|---|-------|-------|
| K1 | Açıklama sınırı **90 karakter** (Google limiti; ajans metnindeki "30" yazım hatası sayılıyor) | Ajansa teyit ettirilecek |
| K2 | Grup başına **en az 15 KW**; grup sayısı `G = max(1, n // 15)`, `max_groups` verilmişse `G = min(G, max_groups)`. `max_groups` **grup sayısını** sınırlar, havuz **asla kırpılmaz** | Önerilen |
| K3 | "15 KW teslim standardı" bir **kalite standardıdır**, Google zorunluluğu değildir; arayüz/export bu ifadeyi kullanır | Önerilen |
| K4 | KW'lerin başlıkta kullanımı **zorunlu değil, en üst düzeye çıkarılır** (kullanıcı, 17.10). Kapsam eksikliği tek başına hata/“standart altı” değildir | Karar verildi |
| K5 | Rakip politikasıyla elenen KW'ler geri alınmaz (V3 `policy_gate.py` ADS havuzunu yazarken rakip terimlerini zaten eliyor) | Doğrulandı |
| K6 | KW'siz CTA/fayda başlıkları için **sabit rezerv yok** (K4 "olabildiği kadar çok" ile tutarlı). Google'ın "en az 3 başlık KW'siz olsun" önerisi bilinçli olarak öncelik dışı; KW'si sığmayan yerler doğal olarak CTA/fayda ile dolar | Önerilen |
| K7 | KW eşleşmesi **bizim deterministik kapsam ölçümümüzdür**: KW tek bir metnin içinde bütün hâlinde (Türkçe normalizasyon sonrası — İ/ı ve diğer Türkçe harfler ASCII'ye katlanır —, kelime sınırlı, ardışık ifade). Google'ın "keyword must be contained entirely within a single headline" ilkesinden esinlenir ama **Ad Strength hesabının taklidi değildir**; Google'ın Türkçe ekleri nasıl saydığı bilinmiyor. İlk sürümde ek toleransı **yok** | Önerilen |
| K8 | DKI uzunluk semantiği **değişmez** (ham metin ≤30, mevcut şema + doğrulayıcı); DKI yalnız kapsamda varsayılan metinle eşlenir | Önerilen |

## 1. Gruplama (`keyword_grouper.py`, `ads_generator.py`)

Bugünkü sorunlar:
- `ads_generator.py:101` havuzu gruplamadan önce `max_groups * 5`'e kırpıyor (çarpanı 15 yapmak
  da kırpar: 32 KW + `max_groups=2` → 2 KW kaybolur).
- 3–7 KW kuralı; alt sınırın altındaki AI grubu **atlanıyor**, kelimeleri "Diğer Kelimeler"
  kovasına düşüyor (satır 211, 238–255). Fallback 5'li parça (satır 276).

Yeni akış:
1. Kırpma satırı **silinir**. `n` = ADS havuzunun tamamı. G, K2 ile hesaplanır; hedef boyutlar
   kodda dengelenir (60 → 15×4, 32 → 16×2, 20 → 20, 12 → 12; 60 + `max_groups=2` → 30×2).
2. AI'ya **"tam G gruba, şu boyutlarla"** denir; aynı ürün + arama niyeti önceliklidir,
   küçük tema en yakın temayla birleştirilir, grup adı/teması gerekirse genişletilir.
3. Doğrulama (deterministik):
   - Bilinmeyen ID atılır; aynı KW iki grupta ise ilk geçtiği yerde kalır (tekrar yok).
   - AI'nın unuttuğu KW'ler ve boyut sapmaları **yer değiştirme** ile düzeltilir: fazla gruptan
     eksik gruba, normalize token örtüşmesi en yüksek KW taşınır. Taşınan/yerleştirilen her KW
     `fill_keyword_ids`'e yazılır.
   - **"Diğer Kelimeler" grubu kaldırılır.**
4. AI iki denemede de başarısızsa fallback: havuz sırasıyla aynı boyutlara bölünür.
5. Fallback grup adındaki `.title()` Türkçe İ'yi bozuyor ("istanbul" → "Istanbul") — düzeltilir.

Ek AI çağrısı yok (mevcut primary + retry korunur).

## 2. RSA üretimi (`prompt_templates.py`, `rsa_generator.py`, `validators.py`)

Bugünkü sorunlar:
- `_validate_headlines` ilk 15'i (satır 550), `_validate_descriptions` ilk 4'ü (satır 611) tutuyor;
  iddia kontrolü bundan **sonra** (satır 282). Fazla aday üretmek işe yaramaz, silinenin yerine aday kalmaz.
- Minimum altında tam üretim tekrarı (satır 160) var; üstüne tamamlama eklenirse çağrılar katlanır.
- Açıklamadaki KW kuralı yalnız prompt'ta; kısaltma/iddia düzeltmesi KW'yi düşürebilir.

Yeni sıra (her üretim turunda):
```
AI adayları (18–20 başlık ≤30, 5–6 açıklama ≤90)
→ uzunluk/kısaltma doğrulaması — KESME YOK (satır 550/611 kesmeleri seçime taşınır)
→ iddia kontrolü (CLAIM_REWRITE 1 kez, çözülmeyen elenir)
→ SON metinlerde tekrar temizliği
→ SON metinlerde KW kapsamı (K7)
→ seçim: 15 başlık / 4 açıklama
```

Seçim (deterministik, açgözlü):
- Başlık: önce yeni KW kapsayan aday (eşitlikte AI sırası), sonra kalanlar; 15'e kadar.
- Açıklama: önce KW içeren ve başlıkta karşılanamamış KW'yi (ör. 30 karakteri aşanlar) taşıyan aday,
  sonra KW içeren diğerleri; 4'e kadar. **KW içermeyen açıklama standarda sayılmaz**; yalnız
  KW'li aday biterse Google'a asset sağlamak için eklenir ve işaretlenir.

Tamamlama (mevcut tam retry'ın **yerine** geçer, tek mekanizma):
1. İlk turdan sonra başlık < 15, KW'li açıklama < 4 veya sert minimum (3/2) altındaysa
   **tek bir tamamlama çağrısı** yapılır. Temiz adaylar **korunur**; prompt'a bunlar verilir ve
   yalnız eksik sayı + başlığa girmemiş (≤30'a sığan) KW'ler için yeni aday istenir.
   Hiç temiz aday yoksa bu çağrı fiilen tam yeniden üretimdir (bugünkü strict retry'ın işini görür).
2. Yeni adaylar aynı sıradan geçer, havuza katılır, seçim yeniden yapılır.
3. "En fazla 1" **tamamlama çağrısını** tarif eder; kısaltma ve CLAIM_REWRITE çağrıları mevcut
   sınırlarıyla ayrıca sayılır.
4. Yalnız kapsam eksikliği (15/4 tamken) tamamlama **tetiklemez** (K4).
5. `enable_ai_regeneration=False` → tamamlama çağrısı yapılmaz (bugünkü retry davranışıyla aynı).
6. Sayılar hâlâ eksikse **jenerik şablonla 15'e doldurulmaz**; grup kaydedilir, "standart altı"
   (başlık < 15 veya KW'li açıklama < 4) raporlanır. `_build_deterministic_fallback` yalnız
   sert minimum (3 başlık / 2 açıklama) için kalır; onun da altı failed.

## 3. KW kapsam yardımcısı (`validators.py`)

`keyword_coverage(target_keywords, headlines, descriptions) -> {kw: "headline"|"description"|"none"}`

- Normalizasyon `app/core/site_analyzer/turkish_normalizer.normalize_turkish` (İ → i doğru).
  `app/core/keyword_dedup.normalize_turkish` **kullanılmaz**: `"İstanbul"` → `"Istanbul"`
  döndürüyor, küçük harfe çevirmiyor (doğrulandı; düzeltmesi ayrı iş).
- Eşleşme K7: normalize metinde KW, kelime sınırlarıyla ardışık ifade olarak geçmeli.
  "laptop fiyatı" ↔ "Laptop Fiyatları" **eşleşmez** (bilinçli katı tanım). Ek toleransı
  gerekirse sonradan, dar ve testli kurallarla.
- DKI `{KeyWord:Varsayılan}` → eşleşme varsayılan metin üzerinden (K8).
- Hem seçimde hem raporda aynı fonksiyon kullanılır.

## 4. Saklama ve raporlama

- Kapsam **okuma anında hesaplanır** (`target_keywords` + kayıtlı başlık/açıklama); kapsam için
  migration yok, eski setler de raporu alır (çoğu "standart altı" görünür — doğru davranış).
- Türetilemeyen tek bilgi: `AdGroup.fill_keyword_ids` (nullable JSON). Additive migration,
  kolon-varlık kontrolüyle idempotent (`20260712_001` kalıbı). Alan **bütün yollarda** taşınır:
  - ilk kayıt `ads_generator._save_to_database` (satır 312),
  - `clone_groups_to_set` (satır 406),
  - `regenerate_group_into_set` (satır 443) — kaynak grubun değeri kopyalanır,
  - `_model_to_schema` (satır 487) → GET şeması (`app/schemas/ads.py`),
  - export `app/exporters/data_collector.py` → csv/docx/excel/pdf.
- Gösterilecekler (grup başına): KW / başlık / KW'li açıklama sayısı; başlıkta, yalnız
  açıklamada ve hiç kullanılmayan KW listeleri; taşınan KW'ler; havuz yetersizliği (n < 15).
- Yüzeyler: `frontend/src/components/generation/AdsPanel.tsx` + `types/models.ts`, export.
- Export'ta **yalnız olgu ve liste**; yüzde, oran, puan yok (sunum raporu kuralı).

## 5. Testler

Backend (yalnız `docker-compose.test.yml`, önce `alembic upgrade head`, tek koşu):
- Gruplama: 60/32/20/12/0 KW; 32 KW + `max_groups=2` → 16/16 (kayıp yok); 60 + `max_groups=2` → 30/30;
  AI yanlış grup sayısı döndürürse (60 KW için 4 yerine 2 veya 6) doğrulayıcı yine 4 grup,
  her KW tam bir kez, hedef boyutlar üretir;
  KW kaybı yok, tekrar yok; unutulan KW yerleşir ve `fill_keyword_ids`'e girer; "Diğer" grubu
  oluşmaz; fallback aynı boyutlar; fallback adında "İstanbul".
- Sıra: 20 adaydan iddialı 3'ü elenince yerine 16.–18. adaylar girer (erken kesme yok).
- Açıklama: kısaltma/iddia düzeltmesi KW'yi düşürürse açıklama standarda sayılmaz.
- Tamamlama: en fazla 1 çağrı; temiz adaylar korunur; 15/4 tamken kapsam eksikliği çağrı yapmaz;
  `enable_ai_regeneration=False` → çağrı yok; eksik kalınca jenerik dolgu yok + "standart altı".
- Kapsam: **büyük İ** ("İstanbul" ↔ "istanbul") eşleşir; ekli biçim ("fiyatı" ↔ "Fiyatları")
  eşleşmez; kelime sınırı ("kalem" ≠ "kalemlik"); DKI varsayılanı; 30 karakteri aşan KW yalnız açıklamada.
- `fill_keyword_ids` klon ve grup yeniden üretiminde korunur.
- Mevcut ADS testleri yeşil kalır (`test_ads_generation_versioning`, `test_ads_negative_conflict`,
  `test_ads_zero_guard`, `test_export_ads_keyword_view`, migration zinciri).

Frontend: `npm run lint && npm run format:check && npm run build && npm run test`.

Canlı doğrulama: 1 workspace'te tek RSA üretimi (mikro maliyet); grup başına sayılar ve
kapsam listesi elle kontrol edilir.

## 6. Uygulama sırası

| Faz | İş | Kabul |
|-----|----|-------|
| 1 | Gruplama (kırpma kaldırma, G/boyut, rebalance, "Diğer" kaldırma) | Gruplama testleri yeşil |
| 2 | Kapsam yardımcısı (K7, İ, DKI) | Kapsam testleri yeşil |
| 3 | RSA: kesmesiz doğrulama → iddia → seçim; tek tamamlama mekanizması | Sıra/tamamlama testleri + mevcut ADS testleri yeşil |
| 4 | `fill_keyword_ids` migration + 4 yol + şema + AdsPanel + export | Migration zinciri + export + klon testleri + frontend kontrolleri |
| 5 | Canlı tek üretim, sonra `deploy/lean-taslak` commit + push | Kullanıcı onayı |

Deploy notu: generation task kodu değişiyor → `celery_worker` restart şart (önce
`task_results`'ta running görev kontrolü); migration için `alembic upgrade head`.

## 7. Bilinen riskler

- 30 karaktere KW + anlam sığdırmak uzun Türkçe KW'lerde zor; K7'nin katı eşleşmesiyle
  başlıkta kullanılamayan KW listesi sık dolu çıkabilir (K4 gereği hata değil). Faz 5 ölçer.
- Kapsamı önceleyen seçim aynı kök kelimeyi (ör. "laptop") çok başlıkta tekrarlatabilir;
  Google "tekrarlayan kelime/ifade" uyarısı verir ve Ad Strength düşebilir (yayını engellemez).
  Mevcut prompt çeşitlilik kuralları korunur; ölçüm Faz 5'te.
- 15'e ulaşmak için başka temadan KW taşımak grup alaka düzeyini düşürür; bu yüzden taşınanlar raporlanır.

## 8. Dış doğrulama (Google resmi dokümanları, 17.10)

| Plan öğesi | Google | Sonuç |
|---|---|---|
| 15 başlık ≤30, 4 açıklama ≤90 | "up to 15 headlines and 4 descriptions… provide as many as you can"; başlık 30, açıklama 90 | Uyumlu |
| Sert minimum 3 başlık / 2 açıklama | Yardım sayfası "minimum of three headlines"; Google Ads API "three to 15 headlines and two to four descriptions" | Uyumlu |
| KW tek metinde bütün hâlinde (K7) | "A keyword must be contained entirely within a single headline to improve Ad Strength" | İlkeyle uyumlu; Türkçe ek davranışı Google'da belirsiz, ölçüm bizim |
| Uzun KW açıklamada | Uzun KW'ler için "90-character description fields instead" | Uyumlu |
| 15 KW / grup | Google sayı vermez, "sıkı temalı grup" der; uygulayıcılar 5–20 önerir | Uyumlu (ajans standardı) |
| Tekrarsız başlık | "avoid adding repetitive words, phrases, or ideas" | Risk §7 |
| 3 KW'siz başlık | "Create effective Search ads" sayfasında öneri | Bilinçli öncelik dışı (K6) |
| Ad Strength yayını etkiler mi | "doesn't determine whether your ad is eligible to serve" | Ad Strength tek başına yayını engellemez |

**Doğru ifade:** Çıktı "RSA metin sayısı ve karakter sınırlarına uygundur; yayın uygunluğu
(politika, Final URL, hesap ayarları) ayrıca değerlendirilir." "Google'a tamamen uyumlu /
yayınlanabilir" denmez.

Kapsam dışı: Bu iş **reklam metni teslimidir**. Final URL reklam oluşturma akışının parçasıdır;
görünen yol (path1/path2) isteğe bağlıdır — ikisi aynı gereklilik değildir. Doğrudan yüklenebilir
Google Ads Editor dosyası hedeflenirse URL + dosya formatı ayrı iş olarak ele alınır.

## 9. Uygulama durumu ve bilinen sınırlamalar (17.10.2026)

Faz 1–4 + kalite düzeltmeleri uygulandı. Planın üstüne alınan kararlar:
- Sabitleme varsayılanı `any`: AI'ın konum önerisi kullanılmaz (bir grupta 15 başlığın 15'i `position_1` gelmişti).
- Türetilmiş prompt doğal Türkçeyi önceler: doğal okunan KW'ler başlıkta *öncelikle*; açıklamada cümleye doğal
  yerleşen en az bir KW; başlık/açıklama grubun KW + temasından. Mühürlü `ADS_RSA_GENERATION_PROMPT` değişmedi.
- Tekrar temizliği DKI varsayılanını açarak karşılaştırır; boş USP'de jenerik metin eklenmez;
  kural tabanlı kısaltma boşluk bırakmaz.
- K6 değişmedi (KW'siz başlıklara sabit yer ayrılmıyor).

Canlı ölçüm (yerel, Gemini 3.8-flash, ws29 run 83, 60 KW, tek koşu): 4 grup × 15 KW, her grupta 15 başlık +
4 KW'li açıklama, eksik yok; 50–63 sn; 5–7 çağrı; ≈ 0,034–0,041 $ (eski 13 grupluk üretim 113–139 sn).

Bilinen sınırlamalar:
1. KW kapsamı tam ifade ölçümüdür; doğal Türkçe önceliğiyle başlıkta geçen KW 28–30/60 (eski prompt 50/60,
   ama sorgu biçimli bozuk başlıklarla).
2. Gerçek akışta profil ürünlerinden türetilen USP ("Web Tasarım, Web Yazılım, E-Ticaret …") her grubun
   prompt'una girer; grup temasından kayan marka hizmeti başlıklarının olası kaynağı. Ayrı karar.
3. Gruplama AI'ı genişletilmiş tema için grup adını her zaman genişletmiyor (SEO + web tasarım grubu 3 koşuda
   da yalnız "SEO" adlı kaldı).
4. Açıklamaların bir kısmı hâlâ akıcı değil (küçük harf marka adı, sorgu biçimli KW yerleştirme); başlıklarda
   "Sosyal Medya Danışmanlık", "E Ticaret Ajansı" gibi dilbilgisi kusurları görülebilir.
5. Gözlemler tek koşuya dayanır; oran olarak genelleştirilemez.
6. Taslak setler ajans incelemesi olmadan aktif yapılmamalı.
