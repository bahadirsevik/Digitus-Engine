# Yapılacaklar Planı (V3)

Tarih: 09.10.2026 (rev. 1: 1.5 ve Faz 5 çıkarıldı — test verisi önemsiz, sunucu DB'si sıfırdan) · Dal: `deploy/lean-taslak` · Durum: TASLAK, onay bekliyor

Bu plan `yapilacaklar.md`'deki maddeleri koda bakarak açar: her madde için sorun,
kanıt (dosya:satır), çözüm adımları, test ve tahmini efor. Kod incelemesi 09.10'da
lean HEAD `f463feb` üzerinde yapıldı; satır numaraları o ana aittir.

Fazlar, risk ve etki sırasına göre dizildi. Faz 1 küçük ama gerçek hatalardır; önce
onlar kapanmalı. **Karar** etiketli maddeler kodlamaya başlamadan önce kullanıcı
onayı ister (özet: §6).

Genel kurallar (CLAUDE.md'den):
- Backend değişikliği → `docker-compose.test.yml` ile tam paket (taban 4388 passed /
  0 kırmızı). Frontend → lint + format:check + build + test (taban 213).
- `models.py` değişikliği → idempotent migration (kolon-varlık kontrolü).
- Celery task imzası değişirse worker restart (önce çalışan görev kontrolü).
- V3 motor promptları ve firm_block hash'lidir: dokunulursa parity testleri ve
  havuz tazeliği etkilenir. Bu plandaki maddelerin hiçbiri motora dokunmaz
  (istisna: Faz 5-B, ayrıca işaretli).

---

## Faz 1 — Hatalar (küçük, yüksek öncelik)

### 1.1 Run üzerinden profil onayı workspace kontrolü yapmıyor
- **Sorun:** `PUT /brand-profile/runs/{run_id}/profile/confirm` `brand_profile_id`
  almıyor; run'ı kapsamsız yükleyip bağlı profili onaylıyor. run_id bilen biri başka
  bir (arşivlenmiş olsa bile) workspace'in profilini ezip onaylayabilir ve relevance
  tetikleyebilir. Frontend bu ucu artık kullanmıyor.
- **Kanıt:** `app/api/v1/brand_profile.py:516-577` (kapsamsız sorgu :531, kilitsiz
  durum kontrolü :544, kilit :551). Frontend gönderdiği `brand_profile_id`
  sessizce yok sayılıyor (`frontend/src/services/api.ts:431-433`).
- **Çözüm (önerilen: kaldır):** Uç UI'da kullanılmadığı için silinsin; `api.ts`'teki
  `confirmProfile`/`analyzeProfile` yardımcıları da. Kaldırmak istenmezse:
  `brand_profile_id: int = Query(...)` + `verify_scoring_run(db, run_id,
  brand_profile_id)`, durum kontrolünden ÖNCE `with_for_update`, ve
  `find_active_workspace_work` 409'u (`confirm_workspace` :1106 kalıbı).
- **Test:** başka workspace'in run'ı ile çağrı → 404; mevcut
  `tests/integration/test_legacy_run_read_only.py:312` URL'i güncellenir.
- **Efor:** 0,5 saat (kaldırma) / 1-2 saat (koruma).

### 1.2 Excel indirmesi: Türkçe run adında olası 500 + dosya adı tutarsızlığı
- **Sorun:** `scoring.py` run adını doğrudan `Content-Disposition` başlığına yazıyor.
  Starlette başlıkları latin-1 kodlar; `ş/ğ/ı/İ` içeren adda kodlama hatası (500),
  tırnak işaretinde bozuk başlık bekleniyor. **Çalışma anında doğrulanmadı — ilk adım
  bunu yeniden üretmek.** Ayrıca CORS `Content-Disposition`'ı açmadığı için tarayıcı
  sunucu adını göremeyip frontend'in farklı yedek adını kullanıyor.
- **Kanıt:** `app/api/v1/scoring.py:1038-1042`; `app/main.py:66-72` (expose_headers
  yok); yedek adlar `Keywords.tsx:602`, `ExportMenu.tsx:75` sunucudakinden farklı
  (`scoring.py:1038`, `channels.py:345`).
- **Çözüm:** (1) Ortak `content_disposition(filename)` yardımcısı: ASCII-güvenli
  `filename=` + RFC 5987 `filename*=UTF-8''...`; scoring, channels ve export
  indirmelerinde kullan. (2) `CORSMiddleware(expose_headers=["Content-Disposition"])`.
  (3) Frontend yedek adlarını sunucununkiyle eşitle. (4) `excel`→(`.xlsx`, mime)
  eşlemesi üç yerde tekrarlanıyor (`export.py:173-179, 241-246`,
  `export_tasks.py:31-37`) → `app/schemas/export.py`'de tek harita.
- **Test:** `run_name="Şişli İçerik"` ile xlsx indirme → 200 ve doğru başlık.
- **Efor:** 2-3 saat.

### 1.3 Görev takibi run'a değil yalnız workspace'e bağlı
- **Sorun:** ADS, SEO ve kanal atama görevleri localStorage'da `{key}:{ws}`
  anahtarıyla tutuluyor. Run değiştirilince eski run'ın görevi yeni run'ın panelinde
  ilerliyor görünüyor; B run'ında başlatılan görev A'nınkini eziyor.
- **Kanıt:** `frontend/src/hooks/useTaskPolling.ts:39-41`; `AdsPanel.tsx:282`
  (run değişince taskId temizlenmiyor :312-321), `SeoGeoPanel.tsx:340`,
  `ChannelWorkspaceView.tsx:60`, `Keywords.tsx:251`. Doğru örnek:
  `SocialStepper.tsx:75-78` (`{key}:{ws}:{runId}`).
- **Çözüm:** Anahtarı `${key}:${ws}:${runId}` yap; run değişim effect'inde `taskId`'yi
  sıfırla; hook bir görevi "bizim" saymadan önce `task.scoring_run_id === runId`
  kontrolü yapsın.
- **Test:** Vitest — iki run arasında geçişte panel diğer run'ın görevini göstermez.
- **Efor:** 2-3 saat.

### 1.4 Migration 20260918_002 downgrade'i ham CHECK hatası veriyor
- **Sorun:** Downgrade, `v3` satırları varken `algorithm_version IN ('v2','v2_1')`
  kısıtını yeniden kuruyor → açıklamasız veritabanı hatası. Artık `v3` varsayılan
  olduğu için her gerçek veritabanında bu durum var.
- **Kanıt:** `migrations/versions/20260918_002_allow_v3_algorithm_version.py:52-65`.
- **Çözüm:** Kısıtı düşürmeden önce `v3` satırlarını say; >0 ise anlaşılır mesajla
  `RuntimeError`. `v3` satırlarını `v2_1` olarak yeniden etiketleme (yanlış olur).
  Not: mevcut migration dosyaları "dokunulmaz" listesinde; bu düzeltme yalnız
  downgrade gövdesine dokunur ve upgrade davranışı aynı kalır — yine de **Karar**
  (alternatif: dokunmayıp CLAUDE.md'ye "v3 sonrası downgrade desteklenmez" notu).
- **Test:** v3 satırı olan DB'de downgrade → beklenen mesaj.
- **Efor:** 1 saat.

---

## Faz 2 — Ürün kalitesi

### 2.1 Çöp/GTIN kelime filtresi tüm import yollarında çalışmalı
- **Sorun:** `_is_junk_keyword` yalnız CSV ayrıştırıcısında. Manuel ekleme,
  `/keywords/import` (frontend'in Google Ads, URL-seed, manuel ve "Geri al" akışları),
  `/google-ads/import`, kampanya importu ve workspace yenilemesi filtresiz.
  Kuralın kendisi de zayıf: gerçek bir GTIN kuralı yok ("sensodyne 8681234567890"
  geçer) ve tarih deseni ("15 temmuz 2025 ...") API kelimelerinde yanlış pozitif
  üretir.
- **Kanıt:** tanım `app/core/csv_import/google_ads_parser.py:277-283`, desenler
  :34-39, tek çağrı :125-129. Tüm yollar `crud.create_keywords_bulk`'ta birleşiyor
  (sanitize döngüsü `app/database/crud.py:136-156`), yenileme hariç
  (`app/core/workspace_refresh.py:94`, `brand_profile.py:2151, 2165` ham insert).
- **Çözüm:**
  1. `app/core/keyword_junk.py` → `junk_reason(text) -> Optional[str]`. İçerik
     kuralları: boş, yalnız sayı/noktalama, **8 veya 12-14 haneli sayı token'ı
     (GTIN)**. CSV'ye özgü yapısal kurallar (başlık satırı, tarih satırı) ayrı ve
     yalnız ayrıştırıcıda.
  2. `create_keywords_bulk` sanitize döngüsünde, batch dedup'tan ÖNCE uygula
     (yoksa çöp `batch_duplicate` sayılır). `force_include` bunu atlamaz.
  3. Yenileme `added_rows`'una da uygula; `build_import_plan`'a savunma hattı.
  4. Raporlama: `skipped_junk` sayacı + `SkippedKeywordDetail(reason='skipped_junk',
     matched='gtin'|'numeric'|'empty')`; `KeywordImportResponse`
     (`app/schemas/keyword.py:107`), `importReasons.ts`, `KeywordImportResult.tsx`.
     Legacy yol int döndürüyor; `service.py:478, 653` çöpü sessizce "fuzzy" sayar →
     o iki yerde de detay döndür.
- **Test:** mevcut `test_google_ads_parser.py`, `test_keyword_import_*`; yeni:
  her import yolu için GTIN'li kelime atlanır ve sebebi raporlanır. `/google-ads/import`
  için hiç test yok → eklenmeli.
- **Efor:** 0,5-1 gün.

### 2.2 Reklam grubu kendi hedef kelimesini negatife alabiliyor
- **Sorun:** Negatifler LLM'den geliyor; promptta "hedefi negatifleme" kuralı yok,
  ayrıştırmada kontrol yok. Varsayılan negatif listesi de (`ücretsiz`, `ucuz`,
  `ikinci el`, `tamir`, `nasıl`) "ucuz laptop" gibi hedefleri vurabilir.
- **Kanıt:** prompt `app/generators/ads/prompt_templates.py:134-147` ve sıkı tekrar
  `rsa_generator.py:416`; ayrıştırma `_parse_negatives` :594-613; varsayılanlar
  `_add_default_negatives` :712-742; hedefler `AdGroup.target_keywords`
  (`models.py:589`).
- **Çözüm:**
  1. `validators.py`'ye saf fonksiyon `find_negative_target_conflicts(negatives,
     targets) -> (kept, conflicts)`. `normalize_turkish` ile normalize, token
     bazlı eşleşme: negatifin tüm token'ları hedefte varsa çakışma (match_type'a
     güvenilmez, tümüne "broad" kuralı). Ek uyumu için: biri diğerinin öneki ve
     kısası ≥4 harf ise eşit say ("sensodyn"/"sensodyne", "macun"/"macunu").
     Yalnız grubun KENDİ hedefleriyle karşılaştır.
  2. Kanca: `RSAGenerator.generate_rsa` sonunda, varsayılan tamamlamadan sonra
     (`rsa_generator.py:203-205` ile :207 arası) — ilk deneme, sıkı tekrar,
     deterministik yedek ve grup yeniden üretimini tek noktada kapsar.
  3. Çakışmada negatif **atılır** (yeniden üretim yok); sayı 10'un altına düşerse
     hedef-duyarlı varsayılanlarla tamamlanır.
  4. İki prompta önleyici satır: "Hedef anahtar kelimeleri veya parçalarını negatif
     yapma." (Kalıcı garanti deterministik filtrededir.)
  5. Görünürlük: `AdGroupFullSchema.negative_conflicts` → `AdsGenerateResponse.warnings`
     → worker `finalize_ads_success(warnings=...)` (parametre var ama hiç
     geçilmiyor: `generation_tasks.py:527-532`) → `AdGenerationSetSummary` →
     `AdsPanel.tsx`'te uyarı.
- **Test:** `tests/unit/test_claim_grounding.py`'deki `CannedAI` kalıbıyla:
  "Sensodyn" grubu + "sensodyne" negatifi → atılır, uyarı oluşur. Prompt değişikliği
  sonrası mevcut prompt-format testleri (:103-135) yeşil kalmalı.
- **Efor:** 1 gün.

### 2.3 İki "Dışlanacak Temalar" kutusu kafa karıştırıyor
- **Sorun:** Onaylı profil ekranında iki kutu var:
  - **Kutu A** "Dışlanacak Temalar" (`BrandProfile.tsx:82, 667-682`) →
    `profile_data.exclude_themes`. **Yumuşak:** kelime elemez, yalnız import
    uyarısı + V3 prompt yönlendirmesi (`engine/context.py:74, 94-98`).
  - **Kutu B** "Mutlaka olmaması gerekenler" (`PolicyReviewSection.tsx:61-75`) →
    `excluded_info` + **sert** `topic_policy.excluded_terms`; terimleri Kutu A'ya da
    kopyalıyor (`app/core/policy/review.py:316-342`).

  Sonuçlar:
  - A'dan bir terimi silmek işe yaramıyor, sert kural B'de duruyor.
  - A'ya yazılan bir terim kelimeyi elemiyor.
  - B kaydedilince A'daki kaydedilmemiş düzenlemeler sessizce siliniyor
    (`BrandProfile.tsx:227-229`).
- **Çözüm (önerilen):**
  1. Etiketler: A → "Kaçınılacak temalar (yumuşak: eleme yapmaz, AI'ı yönlendirir)",
     B → "Kesin dışlama (bu konuları içeren kelimeler elenir)" + yardım metni.
  2. A'da B'den gelen terimleri salt-okunur "kesin dışlamadan gelir" çipi olarak
     göster; silinebilir listeye koyma.
  3. A'yı politika bölümüne B'nin yanına taşı veya tek "Kaydet" ile ikisini birlikte
     gönder; en azından kirli form varken yeniden yüklemede `profileForm` sıfırlanmasın.
  4. (Opsiyonel) V3 `policy_gate` sert listesi yalnız `approved_topic_terms`;
     import kapısı `excluded_info`'yu da birleştiriyor → ikisini eşitle.
- **Test:** Vitest — B kaydı A'daki kirli düzenlemeyi silmez; B'den gelen terim A'da
  silinemez.
- **Efor:** 0,5-1 gün. **Karar:** etiket metinleri ve tek-kaydet mi iki-kaydet mi.

### 2.4 Profil analizinde tek-uçuş kilidi yok
- **Sorun:** 15 dakikayı aşan canlı analiz okuma anında `failed` görünüyor; kullanıcı
  tekrar başlatırsa iki koşu paralel çalışıyor ve eski koşunun koşulsuz son yazımı
  yenisini (hatta onaylanmış profili) eziyor, `confirmed`'ı `draft`'a geri
  çeviriyor. ADR-002'de "Seçenek A" ile bilinçli olarak ertelenmişti.
- **Kanıt:**
  - Başlatma uçları: `brand_profile.py:449-513` (`analyze_profile`, hiçbir kontrol yok,
    çift tık iki koşu başlatır), `:870-935` (`keywords/approve`), `:938-1045`
    (`profile/approve` + `rerun_keywords`).
  - Koşulsuz son yazımlar: :2260, :2422, :2554, :2665, :2751; except'lerdeki `failed`
    yazımları da koşulsuz.
  - `fail_if_stuck` (`stuck_janitor.py:91-126`) `updated_at`'e bakıyor; koşu sırasında
    bu alan güncellenmiyor.
- **Çözüm:**
  1. `brand_profiles.analysis_attempt_id` kolonu (migration, idempotent).
  2. Başlatmada: satırı `FOR UPDATE` kilitle; durum `pending/running` ve eşikten
     gençse 409 "Profil analizi sürüyor"; değilse yeni UUID yaz, commit, token'ı
     task'a geçir. Kalıp: `create_generation_set_locked`
     (`app/generators/ads/generation_sets.py:170-255`).
  3. Her `_run_*` task'ında compare-and-set: `running`'e geçiş
     `WHERE analysis_attempt_id=:tok AND status='pending'`; son yazım ve `failed`
     yazımı öncesi token eşit değilse hiçbir şey yazmadan çık.
  4. `fail_if_stuck` `failed`'a çevirirken token'ı döndürsün (geç biten koşu no-op olur).
     İsteğe bağlı: aşamalar arasında `updated_at` dokunuşu (heartbeat).
  5. `_run_workspace_profile_analysis` (:2687) ölü kod, silinsin.
- **Test:** iki başlatma → ikincisi 409; eski token'lı geç bitiş yeni onaylı profili
  ezmez.
- **Efor:** 1-1,5 gün. **Karar:** ADR-002'deki "Seçenek A" kararını geri almak
  (bu madde o kararın tersine dönüşü; ADR güncellenmeli).

### 2.5 Analiz ilerleme göstergesi V3'ü yanlış anlatıyor
- **Sorun:** `AnalysisProgress` v2 zincirini gösteriyor: "Skorlama" adımı hep bitmiş
  görünüyor, kısa süreliğine "İlgi skoru" adımı beliriyor, "Önceliklendirme"
  (screening) hiç çalışmadığı halde tamamlandı işaretleniyor. Alt adım etiketleri v2
  eşiklerine göre (V3'te %40'ta ADS çalışırken "niyet analizi", %65'te SEO
  çalışırken "marka filtresi" yazıyor). Motorun gerçek mesajı
  (`result_data.current_message`) gösterilmiyor; kapalı kanallar yansımıyor; görev
  bulunmadan run `failed` olursa banner takılı kalıyor.
- **Kanıt:** `frontend/src/components/AnalysisProgress.tsx:22-58`; V3 ilerleme
  noktaları `app/core/engine/orchestrator.py:191` (15 Family), :219 (40 ADS), :258
  (65 SEO), :358 (85 SOCIAL), :407 (92 politika), :424 (100).
- **Çözüm:** Adım listesi `run.enable_*`'dan kurulur:

  | Adım | Ne zaman gösterilir / tamamlanır |
  |---|---|
  | Analiz başladı | her zaman |
  | Kelime aileleri | yalnız ADS veya SEO açıksa |
  | ADS | ADS açıksa |
  | SEO | SEO açıksa |
  | SOCIAL | SOCIAL açıksa |
  | Politika ve havuz teslimi | ilerleme ≥ 92 |
  | Tamamlandı / Hata | görev ya da run durumuna göre |

  - Yüzde doğrudan `progress`; alt başlık `current_message`.
  - `relevance`/`screening` aşamaları ve `getScreeningStatus` yoklaması kaldırılır.
  - `chainRunStatus==='failed'` → hata.
- **Test:** `AnalysisProgress.test.tsx` V3 senaryolarıyla yeniden yazılır (tek
  kanal, üç kanal, hata).
- **Efor:** 0,5-1 gün.

---

## Faz 3 — v2'den kalan ölü kodun temizliği

Sıra önemli: 3.1 bağımsız; 3.2 bağımsız; 3.3 kararlı; 3.4 ancak 3.3'ten sonra.
Eski run'ların okunması ve export'u saklı tablo satırlarını kullanır; bu fazda
tablolar ve migration'lar SİLİNMEZ.

### 3.1 Route'suz eski sayfalar
- **Silinecek:** `pages/Scoring.tsx` (825 satır), `Channels.tsx` (747),
  `Relevance.tsx` (341), `Relevance.css` (254) ve yalnız bunları test eden
  `Scoring.component.test.tsx`, `Channels.locationAudit.test.tsx`,
  `ChannelsScreening.test.tsx`.
- **KALACAK:** `Scoring.css` (`ScoreResults.tsx:5` kullanıyor), `Channels.css`
  (`ChannelWorkspaceView.tsx:14`), `scoringSort.ts` + `Scoring.test.ts`, ve
  `App.tsx:23-25`'teki yönlendirmeler (`generationRouting.test.tsx` doğruluyor).
- **Sahipsiz kalan API yardımcıları:** `channelsApi.assign`, `scoringApi.deleteRun`,
  `brandProfileApi.computeRelevance/getRelevance/getLocationAudit` + tipleri,
  `getAssignmentPreflight` (`services/api.ts`).
- **Efor:** 2 saat. Çalışma anında hiçbir şey bozulmaz.

### 3.2 V3 run'larında boşa embedding harcaması
- **Sorun:** `POST /brand-profile/runs/{id}/relevance/compute` V3 run'larında da
  çalışıyor ve embedding API'sine para harcıyor; V3 `KeywordRelevance`'ı hiç okumuyor
  (kendi `seo_rel`/`social_rel` AI aşamaları var). Otomatik tetik (Path A) V3'te
  yalnız anlık `scored` durumunu yakalayabiliyor.
- **Kanıt:** `brand_profile.py:580` (yalnız legacy run'ı engelliyor), :370-400.
- **Çözüm:** V3 run'ları için uç 409 `RELEVANCE_NOT_USED_BY_V3`; Path A tetiği V3'te
  hiç çalışmasın. Okuma ucu (`GET /relevance`) eski veriler için kalsın.
- **Test:** V3 run'ında compute → 409, embedding çağrısı yok.
- **Efor:** 1-2 saat.

### 3.3 Corpus screening'in kaldırılması — **Karar**
- **Durum:**
  - V3'te hiçbir screening işi oluşmuyor; dispatcher V3 için modu sabit `off`
    yapıyor (`assignment_dispatcher.py:502-529`).
  - Buna rağmen her ortamda ayrı bir `celery_screening_worker` ve yalnız screening
    için çalışan `celery_beat` var; Keywords sayfası her run için 5 saniyede bir
    screening durumunu yokluyor.
  - Ölü kod yaklaşık 8,8 bin satır üretim kodu ve 8,1 bin satır test.
- **V3'ün kullandığı, KORUNMASI gereken parçalar** (önce `app/core/assignment/` ve
  `app/core/ai/` altına taşınır):
  - `screening/inflight.py` (`RedisInflightLimiter`) — motorun global AI slotu,
    `engine/ai_runner.py:348-357`.
  - `screening/attempt_state.py`: `claim_assignment`, `try_finish_attempt`,
    `reconcile_stale_attempts`.
  - `screening/dispatch.py`: `create_attempt`, `fail_attempt`.
  - `screening/runner.compute_cost_usd`: `telemetry/downstream_ledger.py:89`
    kullanıyor.
  - `channel_assignment_attempts` ve `ai_cost_reservations` tabloları: V3 bütçe
    tavanları bunlarda.
- **Adımlar:**
  1. Korunacak parçaları taşı, eski yollarda geçici re-export bırak.
  2. `enqueue_channel_assignment`'ı yalnız V3 dalına indir (else dalı :530-602 ve
     deferred-parent kodu :129-205 ölü).
  3. `/assignment-preflight` ucunu kaldır. `/screening` ucu `{exists:false}` dönen
     ince bir okuma ucuna dönüşsün. Freshness'ta `assistive` havuzlar bayat sayılsın.
  4. `screening_tasks.py`, Celery include/route/beat girdisi; iki compose dosyasından
     `celery_screening_worker` ve `celery_beat` (beat'in başka işi yok).
  5. `CORPUS_*` ayarları ve doğrulayıcıları; `app/core/screening/**` ve
     `app/core/benchmark/**`.
  6. Frontend: screening yoklaması, ilgili tipler ve 3 test.
  7. ORM modelleri, FK'lar ve migration'lar KALIR (eski veri geçerli kalsın).
- **Sunucu etkisi:** prod compose'dan iki servis kalkar. Sunucuda `up -d
  --remove-orphans` gerekir.
- **Efor:** 1-2 gün (karışık testlerin düzeltilmesi dahil).

### 3.4 Diğer v2 modülleri (3.3'ten sonra)
| Modül | Durum | Yapılacak |
|---|---|---|
| `core/channel/channel_engine.py` (2014 satır) | Yalnız `get_channel_pools` canlı (havuz okuma uçları, V3 dahil) | O fonksiyonu ayrı modüle çıkar, kalan ~1.700 satırı sil |
| `intent_analyzer.py`, `pool_builder.py`, `brand_filter.py`, `competitor_filter.py`, `ai_budget.py` | Ölü | Sil |
| `pre_filters/*` | Ölü; ama `SeoPreFilter.PRICE_ROOTS` V3 sonuç rozetlerinde kullanılıyor (`scoring.py:761`), `enricher.py` üreticilerde no-op | Sabiti taşı, enricher çağrılarını kaldır, sil |
| `scoring/ads_scorer.py`, `seo_scorer.py`, `social_scorer.py`, `normalizer.py`, `ScoreEngine.run_scoring` | Ölü | Sil. `create_scoring_run` ve `get_top_keywords_by_channel` kalır |
| `site_analyzer/relevance_scorer.py` | 3.2 sonrası yalnız eski veri okuması | Okuma modeli kalır, hesaplama silinir |
| `brand_defense.py`, `ai_json.py`, `state_machine.py` | CANLI | Dokunma |

- **Efor:** 1 gün. Eski run okuma ve export saklı satırları kullandığı için
  etkilenmez; yine de export testleri tam koşulmalı.

---

## Faz 4 — Altyapı
1. **CI (GitHub Actions):** iki iş tanımlanır.
   - Backend: `docker compose -f docker-compose.test.yml run test_app sh -c
     "alembic upgrade head && pytest tests/ -q"`.
   - Frontend: `npm ci && npm run lint && npm run format:check && npm run build &&
     npm test`.
   - Tetik: `deploy/lean-taslak`'a push ve PR. Efor: yarım gün.
2. **ruff:** önce yalnız `ruff check` (hata sınıfları: F, E9), formatlamasız.
   Mevcut kodda çıkan uyarılar ayrı commit'te. Efor: yarım gün.
3. **ESLint 9 (flat config):** `@typescript-eslint` v8 ile. Efor: yarım gün.
4. mypy ertelenir (büyük kod tabanında gürültüsü yüksek).

---

## Faz 5 — Yabancı pazar (UK) desteği — ERTELENDİ

09.10 kullanıcı kararı: şu an gerek yok (Lucibook tek seferlik). Araştırma bulgusu
kayıt için: UK eklemek yalnız Google Ads metriklerini düzeltir; profil, rakip keşfi
ve tüm içerik promptları Türkçe çıktı ister, dil parametresi hiçbir prompta ulaşmaz.
Talep gelirse ayrı plan yazılır.

---

## 6. Karar listesi

| # | Karar | Önerim |
|---|---|---|
| 1.1 | Run üzerinden onay ucu kaldırılsın mı, korunsun mu? | Kaldır |
| 1.4 | Migration downgrade'ine koruma eklensin mi? | Ekle (yalnız downgrade gövdesi) |
| 2.3 | Etiket metinleri; tek mi iki mi "Kaydet"? | Tek kaydet + yeni etiketler |
| 2.4 | ADR-002 "Seçenek A" geri alınıp kilit eklensin mi? | Evet; ADR güncellenir |
| 3.3 | Corpus screening kaldırılsın mı? | Evet; V3'te ölü, iki servisi boşa çalıştırıyor |

## 7. Önerilen sıra ve toplam efor

1. **Faz 1:** 1.1–1.4, yaklaşık 1 gün.
2. **Faz 2:** 2.1, 2.2, 2.5 kararsız; 2.3 ve 2.4 karar sonrası. Yaklaşık 4–5 gün.
3. **Faz 3:** 3.1 ve 3.2 hemen; 3.3 karar sonrası; 3.4 en son. Yaklaşık 3–4 gün.
4. **Faz 4:** yaklaşık 1,5 gün. Faz 3'ten önce de yapılabilir; büyük silmeleri CI
   altında yapmak daha güvenli.

Toplam: yaklaşık 10–11 iş günü. Her madde ayrı commit; her fazın
sonunda tam test paketi.
