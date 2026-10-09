# Yapılacaklar Planı (V3)

Tarih: 09.10.2026 · Dal: `deploy/lean-taslak` · Durum: TASLAK, onay bekliyor

Revizyonlar:
- **rev. 1:** rekabet verisi onarımı ve yabancı pazar çıkarıldı (yerel veri test
  amaçlı, sunucu DB'si sıfırdan kuruluyor; UK talebi tek seferlikti).
- **rev. 2:** Codex incelemesi işlendi; iddiaları kodda doğrulandı. Değişenler:
  - Plan fazlar yerine **paketler** halinde; her paket sonucuna bakılarak bir
    sonrakine geçilir.
  - Embedding kapatma ilk pakete alındı; migration downgrade mesajı kapsamdan çıktı.
  - Görev takibine backend alanı eklendi (`TaskStatusResponse`'ta `scoring_run_id`
    yok).
  - Negatif çakışması Google Ads eşleşme türü kurallarına göre hesaplanacak; önek
    ve "hepsi broad" kuralları kaldırıldı.
  - GTIN kuralı ilk kapsamdan çıkarıldı.
  - Profil yarışının özü kilit değil, eski yazımın engellenmesi.
  - Dışlama kutularında önce veri kaybı düzeltilecek, tek "Kaydet" ertelendi.
  - Screening, "servisleri kapat" ve "kodu sil" diye iki ayrı işe bölündü.
  - ESLint 9 kabul şartı değil.
- **rev. 3:** Codex'in ikinci turundaki notlar işlendi ve kodda / kaynakta doğrulandı:
  - Janitor yazımları da koşullu (2.2).
  - Form kaydı, B'nin kesin dışlamalarını eski veriyle ezmemeli (3.1).
  - Negatif kontrolü Google'ı taklit ettiğini iddia etmiyor (2.1). Google belgesine
    göre negatifler büyük/küçük harf farkını ve yazım hatasını hesaba katar, çoğul ve
    yakın varyantları katmaz.
  - Görev alanı hem tek görev yanıtına hem run'ın görev listesine eklenir (1.3).

Kod incelemesi lean `f463feb` üzerinde yapıldı; satır numaraları o ana aittir.

Genel kurallar (CLAUDE.md'den):
- Backend → `docker-compose.test.yml` ile tam paket (taban 4388 passed / 0 kırmızı).
  Frontend → lint + format:check + build + test (taban 213).
- `models.py` değişikliği → idempotent migration.
- Celery task imzası değişirse worker restart (önce çalışan görev kontrolü).
- Hiçbir madde V3 motoruna (prompt / firm_block / formül) dokunmaz.
- Her madde ayrı commit.

---

## Paket 1 — Hatalar (≈1-2 gün)

### 1.1 Kullanılmayan profil onayı ucu workspace kontrolü yapmıyor
- **Sorun:** `PUT /brand-profile/runs/{run_id}/profile/confirm` eski akıştan kalma
  (profilin bir analiz run'ına bağlı olduğu dönem). Run'ı kapsamsız yüklüyor ve bağlı
  profili onaylıyor. Run numarasını bilen biri başka bir (arşivlenmiş bile) workspace'in
  profilini ezip onaylayabilir ve relevance tetikleyebilir. Arayüz bu ucu çağırmıyor.
- **Kanıt:** `app/api/v1/brand_profile.py:516-577` (kapsamsız sorgu :531).
- **Çözüm:** Ucu sil. Ardından `services/api.ts:425-434`'teki `analyzeProfile` /
  `confirmProfile` yardımcılarının ve bu uca giden test çağrılarının
  (`tests/integration/test_legacy_run_read_only.py:312`) kalan kullanımını kontrol et.
  Aynı dosyadaki run-düzeyi `POST .../profile/analyze` ucu (:449) da arayüzde
  kullanılmıyor; aynı commit'te kaldırılması değerlendirilir.
- **Test:** ucun 404/405 döndüğü; tam backend paketi.

### 1.2 Türkçe run adında Excel indirmesi çöküyor
- **Sorun:** Run adı `Content-Disposition` başlığına doğrudan yazılıyor. Starlette
  başlıkları latin-1 kodladığı için `Ş` içeren adda `StreamingResponse` oluşturulurken
  `UnicodeEncodeError` (500) oluşuyor. **Codex mekanizmayı bağımsız olarak yeniden
  üretti.**
- **Kanıt:** `app/api/v1/scoring.py:1038-1042`. Aynı kalıp `channels.py:345` ve
  export indirmesinde de var.
- **Çözüm:** Ortak `content_disposition(filename)` yardımcısı yaz: ASCII-güvenli
  `filename=` + RFC 5987 `filename*=UTF-8''…`. Elle başlık kuran tüm indirme
  uçlarında kullan. Kapsam bu kadar; MIME haritalarının birleştirilmesi ve CORS
  `expose_headers` bu düzeltmeye bağlanmaz.
- **Test:** `run_name="Şişli İçerik"` ile xlsx indirme → 200, başlık doğru; tırnak
  içeren ad.

### 1.3 Görev takibi run'lar arasında karışıyor
- **Sorun:** ADS / SEO / kanal atama görevleri localStorage'da yalnız workspace'e
  bağlı anahtarla tutuluyor (`{key}:{ws}`). Run değişince eski run'ın görevi yeni
  run'ın panelinde ilerliyor görünüyor; B'de başlatılan görev A'nınkini eziyor.
  Ayrıca A run'ı için başlamış bir HTTP isteği B'ye geçildikten sonra dönüp B
  panelini değiştirebilir.
- **Kanıt:** `frontend/src/hooks/useTaskPolling.ts:39-41`;
  `components/generation/AdsPanel.tsx:282, 300-305`; `SeoGeoPanel.tsx:340`;
  `ChannelWorkspaceView.tsx:60`; `Keywords.tsx:251`. Doğru örnek:
  `SocialStepper.tsx:75-78`. Backend: `TaskStatusResponse` (`app/api/v1/tasks.py:22`)
  `scoring_run_id` döndürmüyor (model alanı var: `task_status.py:84`).
- **Çözüm:**
  1. Backend: `scoring_run_id` hem tek görev yanıtına (`TaskStatusResponse` + görev
     durumu sözlüğü) hem run'ın görev listesine (`GET /tasks/run/{run_id}`) eklenir.
  2. Frontend: anahtar `${key}:${ws}:${runId}`; run değişiminde `taskId` ve görev
     durumu sıfırlanır.
  3. Hook, `task.scoring_run_id !== runId` olan görevi kendisinin saymaz.
  4. Run'a bağlı her fetch, yanıt geldiğinde run'ın hâlâ aynı olduğunu kontrol eder
     (cancelled bayrağı / istek kimliği). Mevcut örnek: AdsPanel autofill effect'i.
- **Test (Vitest):**
  - A → B geçişinde panel A'nın görevini göstermez.
  - **A'nın geç gelen yanıtı B panelini değiştirmez.**
  - B'de başlatılan görev A'nın kaydını ezmez.

### 1.4 V3 run'larında boşa embedding harcaması
- **Sorun:** `POST /brand-profile/runs/{id}/relevance/compute` V3 run'larında da
  çalışıyor ve embedding API'sine para harcıyor. V3 `KeywordRelevance`'ı hiç okumuyor;
  kendi `seo_rel` / `social_rel` AI aşamaları var (Codex de kullanım bulamadı).
  Otomatik tetik (Path A) V3'ün geçici `scored` durumunu yakalayabiliyor.
- **Kanıt:** `brand_profile.py:580` (yalnız legacy run'ı engelliyor), :370-400 (Path A).
- **Çözüm:** V3 run'ında manuel uç 409 `RELEVANCE_NOT_USED_BY_V3` döner; otomatik
  tetikler V3 run'larını hiç seçmez. Okuma ucu (`GET /relevance`) eski veriler için
  kalır.
- **Test:** V3 run'ında compute → 409 ve embedding çağrısı yok; legacy okuma bozulmadı.

---

## Paket 2 — Ürün doğruluğu (≈2-3 gün)

### 2.1 Reklam grubu kendi hedef kelimesini negatife alabiliyor
- **Sorun:** Negatifler LLM'den geliyor; promptta "hedefi negatifleme" kuralı yok,
  ayrıştırmada kontrol yok. Otomatik tamamlamadaki varsayılan negatifler de
  (`ucuz`, `tamir`, `ikinci el`, `ücretsiz`, `nasıl`) hedefle çakışabiliyor
  ("ucuz laptop").
- **Kanıt:** prompt `app/generators/ads/prompt_templates.py:134-147`, sıkı tekrar
  `rsa_generator.py:416`; `_parse_negatives` :594-613; `_add_default_negatives`
  :712-742; tamamlama :203-205.
- **Çözüm:**
  1. `validators.py`'ye saf fonksiyon `negative_blocks_target(negative, match_type,
     target) -> bool`. Google Ads negatif eşleşme türlerinin kelime kuralları
     uygulanır. Negatifler yakın varyantlara (çoğul, ek) genişlemez; Google ayrıca
     büyük/küçük harf farkını ve yazım hatasını hesaba katar
     (support.google.com/google-ads/answer/2453972). **Bu fonksiyon Google'ın
     eşleştirmesini taklit ettiğini iddia etmez. Amacı açık metinsel çakışmaları
     broad / phrase / exact kurallarıyla yakalamaktır;** fuzzy ya da yazım hatası
     eşleştirmesi bu kapsamda yapılmaz.
     - broad: negatifin tüm kelimeleri hedefte, sıra önemsiz.
     - phrase: kelimeler aynı sırada ve bitişik.
     - exact: birebir aynı.

     Normalizasyon yalnız küçük harf (Türkçe-duyarlı) ve boşluk. Önek veya ek eşitleme
     YOK (`masa` ≠ `masaj`). Bilinmeyen match_type → broad.
  2. Kanca: `RSAGenerator.generate_rsa` sonunda, tamamlamadan sonra. Grubun kendi
     hedefleriyle çakışan negatif atılır.
  3. Otomatik tamamlama yalnız hedefle çakışmayan varsayılanlardan seçer. Güvenli
     aday kalmazsa **daha az negatif döner**; sayı doldurmak için uygunsuz kelime
     eklenmez.
  4. Önleyici prompt satırı: "Hedef anahtar kelimeleri veya parçalarını negatif
     yapma." (Garanti deterministik filtrededir.)
  5. Görünürlük **mevcut yolla**: `finalize_ads_success(warnings=...)` parametresi var
     ve `AdGenerationSet.warnings`'e yazıyor ama worker hiç geçmiyor
     (`generation_tasks.py:527-532`) → atılan negatifleri oraya geçir ve
     `logger.warning`. Arayüzde gösterim bu kapsamda değil.
- **Test** (`test_claim_grounding.py`'deki `CannedAI` kalıbı):
  - broad `ücretsiz` + hedef `ücretsiz diş macunu` → atılır.
  - phrase `diş macunu ucuz` + hedef `ucuz diş macunu` → kalır (sıra farklı).
  - `masa` + hedef `masaj yağı` → kalır.
  - Varsayılan tamamlama çakışan kelimeyi geri eklemez.
  - Mevcut prompt-format testleri (:103-135) yeşil kalır.

### 2.2 Profil analizinde eski sonucun yeni profili ezmesi
- **Sorun:** Profil analizi, "Yeni Çalışma" sihirbazında site taranırken ve marka
  profili çıkarılırken çalışan arka plan işidir. Profil onayında "keyword'leri yeniden
  üret" seçildiğinde ve keyword onayından sonra da tetiklenir.

  15 dakikayı aşan canlı bir analiz okuma anında `failed` görünüyor. Yeniden
  başlatılırsa iki koşu paralel çalışıyor. Bitişteki koşulsuz yazımlar (başarı ve
  **hata**) yeni koşunun sonucunu ya da onaylanmış profili eziyor; `confirmed`'ı
  `draft`'a çeviriyor. ADR-002'de risk olarak kabul edilmişti; teknik sorun duruyor.
- **Kanıt:**
  - Başlatma uçları: `brand_profile.py:870-935` (`keywords/approve`), :938-1045
    (`profile/approve` + `rerun_keywords`), workspace oluşturma :692-771.
  - Koşulsuz son yazımlar: :2260, :2422, :2554, :2665, :2751.
  - Koşulsuz `failed` yazımları: :2274, :2318, :2348, :2392, :2434, :2502, :2551,
    :2570, :2619, :2626, :2677, :2763.
- **Çözüm (öz: eski attempt'in yazamaması):**
  1. `brand_profiles.analysis_attempt_id` kolonu (idempotent migration).
  2. Her başlatma yeni bir UUID yazar ve task'a geçirir.
  3. Task'ın **her** yazımı token'a koşulludur: `running` geçişi, başarı yazımı ve
     hata yazımı. Token kontrolü ile yazım **aynı transaction'da** yapılır: koşullu
     `UPDATE … WHERE analysis_attempt_id = :tok`, ya da `with_for_update` ile yeniden
     okuma + eşitlik kontrolü + yazım. Eşleşmezse hiçbir şey yazılmaz.
  4. **Janitor da koşullu yazar.** `fail_if_stuck` (okuma anında) ve açılıştaki
     `fail_stuck_profiles` bugün profili okuyup koşulsuz `failed` yazıyor
     (`stuck_janitor.py`). Durum, süre ve attempt kontrolü **yazım anında** doğrulanır:
     ya koşullu `UPDATE … WHERE status IN (...) AND updated_at < :esik AND
     analysis_attempt_id = :okunan_tok`, ya da kilit altında yeniden kontrol.
     `failed`'a çevirirken token döndürülür; geç biten eski koşu no-op olur.
  5. Başlatmada 409 "Profil analizi sürüyor" ek bir kolaylıktır; tek başına yeterli
     değildir.
  6. Celery'ye taşıma veya genel iş yönetimi YOK. Ölü `_run_workspace_profile_analysis`
     (:2687) silinir.
- **Test:**
  - Eski token'lı başarı yazımı yeni onaylı profili ezmez.
  - Eski token'lı hata yazımı yeni koşuyu `failed` yapmaz.
  - Janitor'ın `failed` yaptığı koşu geç bittiğinde no-op olur.
  - **Janitor eski koşuyu incelerken yeni koşu başlarsa yeni koşu etkilenmez.**
- **Belge:** ADR-002'ye ek: risk kabulü, eski-yazım koruması ile kapatıldı.

---

## Paket 3 — Kullanıcı deneyimi ve kalite (≈2 gün)

### 3.1 Dışlama kutularında kaydedilmemiş düzenlemelerin kaybolması
- **Sorun:** Onaylı profilde iki kutu var:
  - **A, "Dışlanacak Temalar":** yumuşaktır; eleme yapmaz, import uyarısı verir ve V3
    prompt'una ipucu olarak girer (`BrandProfile.tsx:82, 667-682`).
  - **B, "Mutlaka olmaması gerekenler":** kesin elemedir; terimlerini A'ya da kopyalar
    (`PolicyReviewSection.tsx:61-75`, `review.py:316-342`).

  B kaydedilince sayfa profili yeniden yüklüyor ve A'daki kaydedilmemiş düzenlemeler
  sessizce siliniyor (`BrandProfile.tsx:227-229`).
- **Çözüm (ilk aşama):**
  1. **Veri kaybı:** form kirliyse yeniden yüklemede `profileForm` sıfırlanmaz
     (dirty bayrağı). Yalnız dondurmak yetmez: A sonradan kaydedilirse profil kaydı
     gelen liste alanlarını kullandığı için (`confirm_workspace`,
     `EDITABLE_LIST_FIELDS`), B'nin sunucuya yeni eklediği terimler eski form
     verisiyle ezilir. Çözüm: kullanıcının düzenlediği A değerleri ile B'den gelen
     salt-okunur değerler ayrı tutulur; kayıtta A yalnız kullanıcı terimlerini
     gönderir, sunucu B kaynaklı terimleri korur. Yeni genel form altyapısı gerekmez.
  2. **Etiketler:**
     - A → "Kaçınılacak temalar (eleme yapmaz, yapay zekâyı yönlendirir)".
     - B → "Kesin dışlama (bu konuları içeren kelimeler elenir)", yardım metniyle.
  3. **B'den gelen terimler:** A'da salt-okunur "kesin dışlamadan gelir" çipi olarak
     gösterilir; A'dan silinip "kaldırıldı" sanılmaz.
  4. İki ayrı "Kaydet" akışı korunur. Tek kaydet ertelendi: iki isteğin kısmi başarısı
     ayrıca çözülmeli.
- **Test:**
  - Vitest: B kaydı A'daki kirli düzenlemeyi silmez; B'den gelen terim A'da
    silinemez.
  - Uçtan uca zincir (backend entegrasyon + frontend testi): **A'yı düzenle → B'ye
    kesin dışlama ekleyip kaydet → A'yı kaydet → hem A düzenlemesi hem B'nin
    dışlaması korunur.**

### 3.2 İlerleme göstergesini V3'e uyarlama (sade)
- **Sorun:** `AnalysisProgress` v2 zincirini gösteriyor:
  - V3'te olmayan "İlgi skoru" ve "Önceliklendirme (screening)" adımları var.
  - Alt adım etiketleri V3 aşamalarıyla örtüşmüyor.
  - Motorun gerçek mesajı gösterilmiyor.
  - Görev bulunmadan run `failed` olursa banner takılı kalıyor.
- **Kanıt:** `AnalysisProgress.tsx:22-58`; V3 ilerleme noktaları
  `engine/orchestrator.py:191` (15 Family), :219 (40 ADS), :258 (65 SEO),
  :358 (85 SOCIAL), :407 (92 politika), :424 (100).
- **Çözüm:**
  - Adımlar `run.enable_*`'a göre kurulur: başladı → aileler (ADS veya SEO açıksa) →
    ADS → SEO → SOCIAL → havuz teslimi → bitti/hata.
  - Yüzde doğrudan `progress`, alt başlık `result_data.current_message`.
  - `relevance` / `screening` aşamaları ve Keywords'teki `getScreeningStatus`
    yoklaması (5 sn'de bir) kaldırılır.
  - `chainRunStatus==='failed'` → hata.
- **Test:** `AnalysisProgress.test.tsx` V3 senaryolarıyla (tek kanal, üç kanal, hata).

### 3.3 Ortak çöp kelime filtresi (dar kapsam)
- **Sorun:** `_is_junk_keyword` yalnız CSV ayrıştırıcısında. Manuel ekleme,
  `/keywords/import` (Google Ads, URL-seed, manuel, "Geri al"), `/google-ads/import`,
  kampanya importu ve workspace yenilemesi filtresiz.
- **Kanıt:** `google_ads_parser.py:277-283, 34-39, 125-129`; ortak yazıcı
  `crud.create_keywords_bulk` (sanitize döngüsü `crud.py:136-156`); yenileme
  `workspace_refresh.py:94`, `brand_profile.py:2151, 2165`.
- **Çözüm:**
  1. `app/core/keyword_junk.py` → `junk_reason(text)`. İlk kapsam: boş metin, yalnız
     noktalama/sembol, yalnız sayı (mevcut "tümü rakam/.,%-" kuralı).
     **GTIN / uzun sayı token'ı kuralı YOK.** Uzun sayı model, parça veya başka
     tanımlayıcı olabilir; GTIN'li aramaları dışlamak ayrı bir ürün kararıdır.
  2. CSV'ye özgü yapısal kurallar (başlık satırı, tarih satırı) parser'da kalır.
  3. `create_keywords_bulk` sanitize döngüsünde, batch dedup'tan önce uygulanır.
     `force_include` atlamaz. Yenilemenin `added_rows`'una da uygulanır.
  4. Raporlama mevcut yapılarla: `skipped_details` içinde `reason='skipped_junk'`;
     `importReasons.ts`'e etiket.
- **Test:** her import yolunda boş / sayı-only kelime atlanır ve raporlanır.
  "iphone 15 pro 256" ve "8681234567890 sensodyne" gibi kelimeler KALIR.

---

## Paket 4 — Altyapı (≈1 gün)
1. **Basit CI (GitHub Actions)**, mevcut testlerle:
   - Backend: `docker compose -f docker-compose.test.yml run test_app sh -c "alembic
     upgrade head && pytest tests/ -q"`.
   - Frontend: `npm ci && npm run lint && npm run format:check && npm run build &&
     npm test`.
   - Tetik: `deploy/lean-taslak` push ve PR.
2. **ruff** sınırlı kurallarla (ör. F, E9), formatlama yok. Mevcut uyarılar ayrı
   commit'te.
3. ESLint 9 geçişi ve mypy bu planın kabul şartı DEĞİL; ayrı iş.

---

## Paket 5 — İhtiyaç oldukça temizlik
Çalışan üründeki hataların önüne geçmez; fırsat oldukça yapılır.

### 5.1 Corpus screening (DeepSeek tam evren taraması) — iki ayrı iş
V3'te hiçbir screening işi oluşmuyor; dispatcher V3'te modu sabit `off` yapıyor
(`assignment_dispatcher.py:502-529`).

- **5.1a Servisleri devreden çıkar (küçük; Paket 3.2'deki yoklama kaldırmayla
  birlikte):**
  - `celery_beat`'in tek işi screening uzlaştırması (`celery_app.py:47-52`);
    `celery_screening_worker` yalnız `corpus_screening` kuyruğunu tüketiyor.
  - Ön kontrol: bekleyen screening işi yok (sunucu DB'si V3-only) ve başka bir
    periyodik görev yok.
  - Yapılacak: iki compose dosyasından iki servis çıkarılır. Sunucu güncellemesinde
    `up -d --remove-orphans` gerekir.
  - Kod silinmez.
- **5.1b Kodu sil (büyük; ayrı plan):**
  - V3'ün canlı kullandığı parçalar önce ayrılmalı:
    - `screening/inflight.py` (motorun global AI slotu, `engine/ai_runner.py:348-357`)
    - `attempt_state.py` (`claim_assignment`, `try_finish_attempt`,
      `reconcile_stale_attempts`)
    - `dispatch.py` (`create_attempt`, `fail_attempt`)
    - `runner.compute_cost_usd` (`telemetry/downstream_ledger.py:89`)
    - `channel_assignment_attempts` ve `ai_cost_reservations` tabloları
  - Sonra yaklaşık 8,8 bin satır üretim kodu ve 8,1 bin satır test kaldırılır.
  - Tablolar, FK'lar ve migration'lar kalır.
  - Tahmin: **1-2 günün üzerinde** (Codex değerlendirmesi: iyimser); yapılacaksa ayrı
    plan yazılır.

### 5.2 Route'suz eski sayfalar (fırsat işi, ≈2 saat)
- **Silinecek:**
  - `pages/Scoring.tsx`, `Channels.tsx`, `Relevance.tsx`, `Relevance.css`
  - Yalnız bunları test eden 3 test dosyası
- **KALACAK:**
  - `Scoring.css` (`ScoreResults.tsx:5`) ve `Channels.css`
    (`ChannelWorkspaceView.tsx:14`)
  - `scoringSort.ts` ve `Scoring.test.ts`
  - `App.tsx:23-25` yönlendirmeleri
- **Sahipsiz kalan API yardımcıları** (`services/api.ts`) da temizlenir.

### 5.3 Diğer v2 modülleri (5.1b'den sonra)
| Modül | Durum |
|---|---|
| `channel/channel_engine.py` | Yalnız `get_channel_pools` canlı (havuz okuma uçları) → onu ayır, kalanı sil |
| `intent_analyzer.py`, `pool_builder.py`, `brand_filter.py`, `competitor_filter.py`, `ai_budget.py` | Ölü |
| `pre_filters/*` | Ölü; ama `SeoPreFilter.PRICE_ROOTS` V3 sonuç rozetlerinde canlı (`scoring.py:761`) → taşı |
| `scoring/*_scorer.py`, `normalizer.py`, `ScoreEngine.run_scoring` | Ölü; `create_scoring_run` ve `get_top_keywords_by_channel` kalır |
| `brand_defense.py`, `ai_json.py`, `state_machine.py` | CANLI, dokunma |

### Kapsam dışı bırakılanlar
- **Migration 20260918_002 downgrade mesajı:** veri v3 iken eski kısıta dönüşün
  reddedilmesi beklenen davranış. Açıklayıcı hata ürünü düzeltmiyor; sunucu DB'si
  sıfırdan kuruluyor.
- **Yabancı pazar (UK):** ertelendi. Kayıt için: UK eklemek yalnız Google Ads
  metriklerini düzeltir; profil, rakip keşfi ve içerik promptları Türkçe çıktı ister.

---

## Karar listesi

| # | Karar | Öneri |
|---|---|---|
| 1.1 | Kullanılmayan run-düzeyi profil uçları silinsin mi? | Evet (Codex de onayladı; analiz ucu, kalan çağrıları kontrol edilerek aynı commit'te) |
| 2.2 | ADR-002 riski eski-yazım koruması ile kapatılsın mı? | Evet |
| 3.1 | Yeni etiket metinleri uygun mu? | — |
| 3.3 | GTIN'li aramalar dışlansın mı? (ayrı ürün kararı) | Şimdilik hayır |
| 5.1a | Screening servisleri kapatılsın mı? | Evet |

## Süre
Paket 1 ≈ 1-2 gün; Paket 2 ≈ 2-3 gün. Sonuçlara bakılarak Paket 3 ve 4'e geçilir.

Tüm listeyi bitirme tahmini 10-11 gündür; bu, ürünü güvenilir hâle getirmek için
zorunlu süre değildir. Ürün güvenilirliği için asıl gereken Paket 1 ve 2'dir.
