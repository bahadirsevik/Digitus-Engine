# Claude Analizi Doğrulama ve Hareket Planı

Tarih: 2026-05-11  
Referans commit: `c10fc88`

Bu belge Claude'un eksiklik/tutarsızlık raporundaki maddelerin repo üzerinde doğrulanması ve doğru olanlar için uygulanacak hareket planıdır.

## 1. Doğrulama Özeti

### Kritik Bulgular

| ID | Karar | Not |
| --- | --- | --- |
| K1 | Doğru | `app/api/v1/scoring.py` içinde `list_scoring_runs`, `get_scoring_run`, `delete_scoring_run`, `get_scoring_results`, `get_top_by_channel`, `export_scoring_xlsx` endpoint'leri workspace filtresi olmadan run id/global liste üzerinden çalışıyor. |
| K2 | Doğru | `frontend/src/pages/Dashboard.tsx` aktif workspace kullanmıyor; global keyword/run sayısı çekiyor ve workspace değişiminde yenilenmiyor. |
| K3 | Doğru | `frontend/src/pages/Tasks.tsx` doğrudan `fetch('/api/v1/tasks/')` ile global task listesi çekiyor. Backend `tasks.py` de workspace/run filtresi uygulamıyor. |
| K4 | Kısmen doğru | İki logging konfigürasyonu var. `main.py` sadece `app/core/logging_config.py` içindeki stdout/Loguru setup'ını çağırıyor; `app/core/logging/config.py` dosya handler'lı config olarak duruyor ama setup edilmiyor. "Production'da hiç log yok" değil, ama dosya/rotating task log beklentisi boşa düşüyor. |
| K5 | Doğru | Frontend'de mojibake var. Doğrulanan örnekler: `Generation.tsx` içinde `GÃ¼ncel DeÄŸil`, `GoogleAdsKeywordSearch.tsx` içinde `Hesap seÃ§in`. |

### Yüksek Bulgular

| ID | Karar | Not |
| --- | --- | --- |
| Y1 | Doğru | `/scoring/runs/{id}/scores` ve `/channels/runs/{id}/pools` workspace kontrolsüz. Aynı risk run detail/export/top endpoint'leri için de geçerli. |
| Y2 | Kısmen doğru | Generation endpoint'leri çoğunlukla `scoring_run_id` üzerinden dolaylı bağlam çözüyor; workspace guard yok. Tekil `/generation/seo-geo` endpoint'i workspace/run bağlamından kopuk kalabiliyor. |
| Y3 | Doğru | K1'in DELETE kısmı. `crud.delete_scoring_run(db, run_id)` doğrudan ID ile çalışıyor. |
| Y4 | Doğru ama geçiş dönemi | `BrandProfile.scoring_run_id` deprecated görünmesine rağmen `brand_profile.py`, `pool_builder.py`, `brand_defaults.py`, `data_collector.py`, `seo_geo_generator.py` içinde fallback/legacy query olarak kullanılıyor. Kolon kaldırılmadan önce refactor şart. |
| Y5 | Kısmen doğru | `generation.ads_generate` Celery task'ı var; "task yok" kısmı yanlış. Ancak UI'nin kullandığı `/generation/ads/rsa` endpoint'i senkron `AdsGenerator.generate_ads()` çağırıyor. Timeout riski doğru. |
| Y6 | Doğru | `socialStore` persist ediyor ve workspace değişiminde resetlenmiyor. Yanlış run/task state'i taşınabilir. |
| Y7 | Doğru | `scoringApi.listRuns()` `brand_profile_id` almıyor; sayfalar client-side filtreliyor. Backend leak ile birleşince risk büyüyor. |
| Y8 | Doğru | API key davranışı ve workspace izolasyonu için integration/API testleri yok. Mevcut testler sadece unit ağırlıklı. |

### Orta Bulgular

| ID | Karar | Not |
| --- | --- | --- |
| O1 | Doğru | `tests/unit` boş değil; 6 test dosyası var. `CLAUDE.md` bu bilgiyi yanlış yazmış. Eksik olan integration/API/channel/scoring/generation testleri. |
| O2 | Doğru | `ValidationLog.tsx` ve CSS import edilmiyor. |
| O3 | Doğru | `frontend/src/pages/GoogleAds.css` orphan görünüyor. |
| O4 | Doğru | Error handling karışık: `alert()`, inline state, `ErrorBanner`, raw `fetch` birlikte kullanılıyor. |
| O5 | Doğru | TypeScript `any` kullanımı yaygın; özellikle `profile_data`, Relevance, Generation ve SocialStepper tarafında. |
| O6 | Doğru | `Channels.tsx` içinde `ScoringRun` interface iki kez tanımlı. |
| O7 | Doğru | `ContentOutput.is_stale` ağırlıklı olarak `completed -> channel_assigning` transition'ında işaretleniyor; relevance recompute / workspace refresh / bazı rerun yolları kapsam dışı kalabilir. |
| O8 | Doğru | Embedding cache yok. Embedding failure durumunda `relevance_score=1.0` fallback'i kalite açısından tehlikeli. |
| O9 | Doğru | `DEBUG` modda `init_db()` `Base.metadata.create_all()` çağırıyor; Alembic drift riskini gizleyebilir. |
| O10 | Kısmen doğru | `run_scoring_task` ve `create_and_run_scoring` Celery include listesinde duruyor ancak ana UI/API akışında kullanılmıyor. Dead-code/karışıklık riski doğru. |

### Düşük / Notlar

| ID | Karar | Not |
| --- | --- | --- |
| N1 | Doğru | Frontend'de lint/test script standardı yok. |
| N2 | Doğru | `google_ads_probe` repo içinde duruyor; gerçek kullanım belirsiz. |
| N3 | Yanlış | `celerybeat-schedule` ve `celerybeat.pid` zaten `.gitignore` içinde. |
| N4 | Doğru | Status lint guard CI/pre-commit'e bağlı değil. |
| N5 | Kısmen doğru | `Channels.tsx` genel olarak workspace'e bağlı veri filtreliyor ama task polling ve bazı helper/state akışları workspace değişiminde eski seçimleri taşıyabilir. |
| N6 | Doğru | Vite proxy hedefleri dev/docker için yorum ve yapı olarak net değil. |
| N7 | Doğru | Legacy single generation endpoint'leri duruyor; UI bulk/RSA ağırlıklı kullanıyor. |

### Ek Bulgular (Plan2 Sonrası Kod Üzerinde Doğrulanan)

Aşağıdaki yedi bulgu, plan2 ilk turunda raporlanmamış ancak doğrudan kod üzerinde teyit edilmiş, plan2 kapsamına eklenmesi gereken maddelerdir.

| ID | Karar | Şiddet | Not |
| --- | --- | --- | --- |
| E1 | Doğru | Kritik | `app/api/v1/export.py` altyapısı çift hatalı. (a) `_export_status: Dict` modül-level in-memory; restart sonrası kayıp, prod 2-worker'da paylaşılmıyor. (b) `create_export` request-scoped `db: Session = Depends(get_db)` BackgroundTask'a `add_task(_run_export, ..., db)` ile taşınıyor; FastAPI istek sonunda session'ı kapatır → background task closed session üzerinde çalışmaya çalışır. (c) `create_export` ve `list_exports_for_run(run_id)` workspace guard içermiyor; line 212-218 dict iteration cross-workspace leak. |
| E2 | Doğru | Kritik | `app/api/v1/keywords.py` içinde `DELETE /keywords/all` (line 152) ve `DELETE /keywords/{id}` (line 173) `brand_profile_id` opsiyonel; verilmezse `crud.delete_all_keywords(db)` / `crud.delete_keyword(db, id)` global Keyword tablosunu siler. Tek bir frontend bug'ı ile tüm sistemin keyword verisi imha edilebilir. Docstring "legacy behavior" diyerek bunu meşrulaştırmış. `list_keywords` da aynı opsiyonel pattern. |
| E3 | Doğru | Kritik | `docker-compose.prod.yml:6` komutu: `init_db() && alembic stamp head && uvicorn ...`. Production'da Alembic upgrade **çalıştırılmıyor** — `Base.metadata.create_all()` + `stamp head` ile migration history sahte uygulanmış görünüyor. Migration chain empty DB'den head'e kadar test edilmiyor; yeni eklenen migration prod'da uygulanmıyor olabilir. O9'da bahsedilen DEBUG init_db drift'inden farklı, çok daha kritik bir altyapı sorunu. |
| E4 | Doğru | Yüksek | Raw fetch taraması: workspace context'i taşımayan **14 raw `fetch('/api/v1/...')` çağrısı** var (ilk turdaki "6 çağrı" iddiası eksik kalmış, geniş tarama sonrası 14 olarak güncellendi). Dosyalar: `useTaskPolling.ts:53`, `SocialStepper.tsx:27,44,76,103,132`, `Generation.tsx:62,84,96,153,167,190`, `Tasks.tsx:36,49`. POST yollarda backend hangi workspace adına çalıştığını bilmiyor. Plan kriteri: `rg "fetch\(" frontend/src` 0 sonuç. |
| E5 | Doğru | Orta | `Dashboard.tsx` kart metriklerinin scope tanımı yok ("aktif workspace toplamı mı, son run mı, tüm confirmed workspace mı?"). K2 workspace'e bağlamayı söylüyor ama metric semantikleri ürün/UX olarak tanımsız; yanlış güven hissi yaratabilir. |
| E6 | Doğru | Orta | Google Ads özelinde error UX tanımsız. `/google-ads/customers` credential eksik veya geçersiz olduğunda 500 dönüyor (kullanıcı deneyimiyle gözlemlendi); `language_id` / `geo_target_id` invalid değerlerinde anlaşılır mesaj yok (migration `20260509_002` zaten bu için Turkish language ID fix'i yapmış). Customer list başarısız olduğunda URL seed extraction akışı gereksiz bozulabilir. |
| E7 | Doğru | Düşük | URL seed cache fix'i commit `c10fc88` ile yapılmış ancak davranış kuralı dokümante edilmemiş: cache key formülü, parametre değişiminde bypass, "yeniden çek" force refresh semantiği, cache hit mesajının ne zaman gösterileceği. Kullanıcı önceden "ilk çekişte önceki sonuç kullanıldı" yanlış mesajını gördü. |

## 1.5 Senior Review Kararları (Codex eleştirileri)

Plan2'nin ilk uzun hali üzerinde yapılan senior review notları aşağıdaki kararlarla planın yeni P0-P4 yapısına yansıtıldı:

| Eleştiri | Karar | Yansıması |
| --- | --- | --- |
| Faz 1 çok şişmiş, küçük bir refactor sprint'i olmuş | Kabul | Faz 1 → P0 (hotfix), P1 (frontend wiring), P2 (export refactor), P3 (migration), P4 (UX/debt) olarak ayrıldı. |
| `verify_scoring_run(..., brand_profile_id=None)` çok gevşek | Kabul | Mutating endpoint'lerde **zorunlu**; read endpoint'lerde sadece P0 geçiş süresinde opsiyonel + warning log. Yeni frontend hiç `None` göndermez. |
| Export refactor (E1) Faz 1'de fazla büyük | Kabul (kısmen) | İkiye bölündü: **P0 hotfix** (workspace guard + Celery task içinde yeni session) + **P2 tam refactor** (ExportJob tablosu, status DB-backed, UI progress). |
| Migration bootstrap (E3) sırada tehlikeli | Kabul | "Önce empty-DB upgrade test et, sonra prod komutunu değiştir" sırasına çekildi. Migration chain validation önce, prod komut değişimi sonra. |
| Raw fetch sayısı 6 değil daha fazla | Kabul + düzeltildi | Doğrulama: useTaskPolling.ts:53, SocialStepper.tsx (5 yer), Generation.tsx (6 yer), Tasks.tsx (2 yer) — toplam **14 raw fetch**. Plan kriteri: `rg "fetch\(" frontend/src` 0 sonuç. |
| Google Ads allow-list bakım yükü | Kabul | Allow-list yerine: format normalize + tek helper + Google Ads API'sinden gelen `invalid argument` hatasını anlaşılır mesaja çevir. |
| Plan2.md mojibake | **Reddedildi** | Dosya byte seviyesinde teyit edildi: geçerli UTF-8 (`\xc4\x9f`=`ğ`, `\xc4\xb1`=`ı`). Codex'in görüntülemesi cp1252 olarak yorumlanmış. Düzeltilecek bir şey yok. |
| Test altyapısı belirsizliği | Kabul + karar | Aşağıdaki "1.6 Test Altyapısı Kararı" eklendi. |

## 1.6 Test Altyapısı Kararı

Kullanıcı kararı: **DB'de değerli veri yok**, testler doğrudan Docker + gerçek Postgres üzerinde çalışacak. SQLite ile uyumluluk derdi olmayacak.

- **Stack**: `docker-compose.test.yml` (veya mevcut `docker-compose.yml`'in `test` profile'ı) → `db` (postgres:15-alpine, ayrı volume veya tmpfs) + `app` (test runner) + opsiyonel `redis`.
- **Migration**: Her test session başlangıcında `alembic upgrade head` (smoke test'in kendisi olur).
- **Fixtures**: pytest fixture'ları `truncate_all_tables` ile her testten önce DB temizler; `BrandProfile` + `Keyword` + `ScoringRun` fixture factory'leri.
- **TestClient**: FastAPI `TestClient` + SQLAlchemy `SessionLocal` (mock yok). `verify_api_key` testlerinde `API_KEY` env'i fixture ile set/unset.
- **Komut**: `docker-compose -f docker-compose.test.yml run --rm app pytest tests/`.
- **CI hedefi**: GitHub Actions matrix'inde Postgres service + aynı komut.
- **Eski test'ler**: `tests/unit/test_workspace_phase_b_migration.py` ve diğer migration testleri zaten DB istiyor; bu çerçeveye uyacak.

Bu karar, "SQLite uyumluluğu var mı, mock'lasak mı, fixture nasıl?" gibi belirsizlikleri P0 öncesi kapatır.

## 2. Önceliklendirilmiş Hareket Planı (P0–P4)

Plan, dört bağımsız iş paketine ayrıldı. Her paket kendi başına merge edilebilir/release edilebilir. Sıra strikt (P0 önce, sonra P1, …) — aynı anda iki paket sürdürülmez.

### P0 — Veri Güvenliği Hotfix (en yüksek risk kapat)

Amaç: Veri sızıntısı ve veri kaybı risklerini minimum kod değişikliğiyle kapat. Refactor değil, guard.

**P0 — Commit Bölünmesi (5 commit, sırayla)**

P0 tek PR yerine 5 ardışık commit (veya küçük PR) olarak ilerletilir. Her commit testleri yeşil bırakır; ancak **C3'ten itibaren frontend'in bazı sayfaları (özellikle Keywords) workspace zorunluluğu nedeniyle 400 alabilir** — bu nedenle C3-C5 strictly "backend güvenlik commit'leri" olarak kabul edilir, kullanıcıya açık sürüm öncesi P1'in karşılığı tamamlanmalıdır. Net wording: C1/C2 bağımsız merge edilebilir; C3-C5 sonrası frontend'de görünür kırılma olur — bu kabul edilebilir ama kısa süreli olmalı.

| Commit | Kapsam | Bağımlılık | Frontend etkisi |
| --- | --- | --- | --- |
| **C1** | Docker/Postgres test altyapısı + empty-DB migration smoke (P0 madde 1-2) | yok | Yok |
| **C2** | `verify_scoring_run` helper + scoring.py + channels.py guard'ları (P0 madde 3-5) | C1 | Read'de warning, mutating'de henüz frontend "API çağrısında brand_profile_id yok" hatası alabilir |
| **C3** | keywords.py global delete/list/import güvenliği (P0 madde 6) | C2 (helper) | **Keywords sayfası 400 alır**. P1'in `keywordsApi` workspace param desteğinin **C3'ün hemen ardından** geliştirilmesi önerilir (bu süreç paralel başlatılabilir, P1 başlangıcı erkene çekilebilir). |
| **C4** | tasks.py + export.py minimal hotfix (P0 madde 7-8) | C2 | Tasks/Export sayfaları 400 alır; aynı şekilde P1'in ilgili wrapper'ları C4 sonrasını beklemesin. |
| **C5** | `tests/integration/test_workspace_isolation.py` cross-workspace + 400/404 testleri (P0 madde 9) | C1+C2+C3+C4 | Yok |

C1 ayrı PR olarak da merge edilebilir; C2 bağımsız. C3-C5 ardışık olmalı ve P1 wiring'i C3'ten sonra başlamalı (frontend boş bekletilmesin).

**Ortak guard helper isimlendirmesi (Codex önerisi sonrası):**

Tek bir `verify_scoring_run` helper'ı tüm endpoint türleri için yeterli değil. Kopya guard kodu yazılmasını önlemek için her endpoint kategorisi için aynı isim kuralıyla helper yazılacak (`app/core/workspace.py` içinde):

- `verify_workspace(db, brand_profile_id) -> BrandProfile` — mevcut, baz helper.
- `verify_scoring_run(db, run_id, brand_profile_id, *, mutating=False) -> ScoringRun` — yeni, P0/C2'de.
- `verify_task_in_workspace(db, task_id, brand_profile_id) -> TaskResult` — P0/C4'te.
- `verify_keyword_link(db, keyword_id, brand_profile_id) -> WorkspaceKeyword` — P0/C3'te.
- `verify_export_in_workspace(db, export_id, brand_profile_id) -> dict | ExportJob` — P0/C4'te (dict varyantı); P2'de `ExportJob` döner hale gelir.

Tüm helper'lar aynı kontratta: workspace yoksa 400, eşleşme yoksa 404. Endpoint kodu sadece tek satır `obj = verify_X(...)` çağırır.

**P0 Sıralı Adımlar**

1. Docker-Postgres test çerçevesi (`docker-compose.test.yml`, pytest fixture'ları, truncate, FastAPI TestClient).
   - 1.6'daki test altyapısı kararını uygulanır hale getir.
   - Komut: `docker-compose -f docker-compose.test.yml run --rm app pytest tests/`.

2. **Empty-DB migration smoke** (Codex önerisiyle P0'ın başına alındı; eski P3'ten erkene çekildi).
   - Boş Postgres container → `alembic upgrade head` → tablo varlık assertion'ları çalışmalı.
   - Test: `tests/integration/test_migration_chain.py` (yeni). Sadece migration'ı çalıştırır, başka şey yapmaz.
   - Eğer chain kırıksa, **P0'ın hiçbir maddesi ilerlemez** — önce workspace phase A/B/C dizisindeki kırılma onarılmalı. Bu yapıldıktan sonra P3'ün migration düzeltme adımlarının çoğu kendiliğinden çözülmüş olacak; P3 sadece prod komut değişikliği ile sınırlanır.

3. `verify_scoring_run(db, run_id, brand_profile_id, *, mutating=False)` helper'ı `app/core/workspace.py`'a ekle.
   - `mutating=True` → `brand_profile_id` zorunlu (None ise **`HTTPException(400, detail="brand_profile_id is required")`**, Codex'in net mesaj önerisiyle).
   - `mutating=False` → `brand_profile_id` opsiyonel; None ise `logger.warning("legacy scoring run access run_id=...")` + TODO marker.
   - Run'ın workspace ile eşleşmediği durumda **404** (existence leak'i önlemek için 403 değil).
   - Hata mesajları her durumda yapısal (`{"detail": "brand_profile_id is required"}`, `{"detail": "scoring run not found in workspace"}`) — frontend "unexpected error" göstermesin.

4. scoring.py guard'ları (mutating: assign/delete/execute/scoring run create; read: list/get/scores/top/xlsx).

5. channels.py guard'ları (mutating: `/assign`; read: `/pools[/{channel}]`).

6. keywords.py legacy global yolların kapatılması (E2).
   - `DELETE /keywords/all`: `brand_profile_id` **zorunlu** (None → 400 `{"detail": "brand_profile_id is required"}`).
   - `DELETE /keywords/{id}`: workspace verilmezse 400. `crud.delete_keyword` global çağrısı **tamamen kaldırılır** (Codex teyit).
   - `GET /keywords` ve `POST /keywords/import`: workspace zorunlu (None → 400 net mesaj).
   - Eski "legacy behavior" docstring'leri tamamen kaldırılır — geçmiş davranışı meşrulaştıran bir referans bırakılmaz.

7. tasks.py guard'ları.
   - `GET /tasks/`, `GET /tasks/run/{run_id}`: `brand_profile_id` zorunlu (read olsa bile global task leak kritik) — None → 400.
   - `GET /tasks/{task_id}`, `POST /cancel`: task'ın run'ı üzerinden workspace doğrulanır.

8. export.py minimal güvenlik hotfix'i (E1'in P0 parçası).
   - `create_export` ve `list_exports_for_run` endpoint'lerine `brand_profile_id` zorunlu parametre.
   - `_run_export` içinde **kendi `SessionLocal()` aç**, request-scope `db` parametresini kaldır → background task closed session bug'ı kapanır.
   - `_export_status` dict olarak kalır (P2'de DB-backed olacak), ama dict key'i artık `(brand_profile_id, export_id)` tuple; `list_exports_for_run` workspace filtresiyle iterate.
   - Bu adım bilinçli olarak yarım çözüm; tam refactor P2'de.

9. Integration test fixture'ı + cross-workspace leak test'leri.
   - `tests/integration/test_workspace_isolation.py`:
     - İki workspace, her birinde bir scoring run + bir task + **bir in-memory export status kaydı** (`_export_status` dict tuple key'i ile; DB-backed `ExportJob` P2'de gelecek) + birer keyword.
     - Workspace A için liste/get/delete/scores/pools/tasks/export çağrılarının Workspace B verisini hiç görmediği assertion'ları.
     - Workspace A'dan Workspace B'nin run'ını DELETE denemesinin 404 dönmesi.
     - `DELETE /keywords/all` brand_profile_id'siz çağrı → 400 + mesajın `"brand_profile_id is required"` içermesi.
   - Test altyapısı 1.6'daki Docker + Postgres çerçevesi.

10. Sürüm hedefi: P0 5 commit hâlinde merge edilir. Frontend değişikliği yok; sayfalar 400 dönerse bunu mevcut hata gösterimleriyle göstermeli. **P0 merge olur olmaz P1 başlasın** (uzun bekleme kabul edilemez, çünkü frontend bu süre boyunca 400 mesajıyla yaşamak zorunda).

### P1 — Frontend Workspace Wiring + Raw Fetch Temizliği

Amaç: Frontend hiçbir API çağrısında workspace context'i unutmasın, raw `fetch` kalmasın.

1. `services/api.ts` parametre genişletmeleri (Faz 1 eski madde 6 ile aynı).
   - scoringApi.listRuns/getRun/deleteRun/executeRun/getScores/getTopByChannel/exportXlsx.
   - channelsApi.assign/getPools/getPool.
   - tasksApi.list/getStatus/cancel/listByRun.
   - keywordsApi: workspace zorunlu varyantları.
   - exportApi.create/listForRun.

2. Raw fetch temizliği (E4 güncellenmiş).
   - **Doğrulanmış 14 yer**: `useTaskPolling.ts:53`, `SocialStepper.tsx:27,44,76,103,132`, `Generation.tsx:62,84,96,153,167,190`, `Tasks.tsx:36,49`.
   - Hepsi `services/api.ts` wrapper'ına taşı; her POST/GET workspace parametresi alsın.
   - Hedef: `rg "fetch\(" frontend/src` ya 0 sonuç ya da yalnızca açıkça whitelisted (ör. health check) bir sonuç dönsün.

3. Typecheck/build kapısı.
   - `npm run build` veya yeni `tsc --noEmit` scripti zorunlu kapı.

4. Sayfa wiring'i.
   - `Dashboard.tsx`: `useBrandStore`, workspace yoksa "Marka Çalışması seçin" empty state'i. Global sayım çağırma.
   - `Tasks.tsx`: workspace bağımlı, `tasksApi` üzerinden.
   - `Scoring.tsx`, `Channels.tsx`, `Relevance.tsx`, `Generation.tsx`, `Export.tsx`: backend filter param + client-side filter.
   - `useEffect` deps'inde `activeWorkspace?.id` bağımlılığı (workspace değişince refetch).

5. `socialStore` workspace değişiminde resetlensin.
   - `brandStore` subscription veya `Generation/SocialStepper` içinde `useEffect` ile reset.
   - Persist edilen `taskId`, `scoringRunId`, `categories`, `ideas`, `contents` eski workspace'ten taşınmasın.

6. Frontend integration test'leri (opsiyonel ama önerilen).
   - Workspace değiştirme sonrası tüm sayfaların doğru parametreyle istek attığını teyit eden Cypress/Playwright veya en azından React Testing Library testi.

### P2 — Export Altyapı Tam Refactor

Amaç: P0 hotfix'ini sağlam yapıya çevir. Multi-worker safe, restart-safe, izlenebilir.

1. `ExportJob` SQL modeli (`app/database/models.py`).
   - Kolonlar: id (uuid), brand_profile_id (FK, NOT NULL, indexed), scoring_run_id (FK, opsiyonel), status (enum: pending/processing/completed/failed), progress (int 0-100), file_name, filepath, error_message, format, created_at, updated_at.
   - Alembic migration: `20260512_001_add_export_jobs.py` (örnek).

2. Celery task'a taşıma.
   - `_run_export` → `app/tasks/export_tasks.py:run_export_task`.
   - Task kendi `SessionLocal()` açar. Status güncellemeleri DB'ye yazar.
   - Eski in-memory `_export_status` dict kaldırılır.

3. Endpoint'leri DB'ye bağla.
   - `POST /export`: ExportJob yarat, Celery task tetikle, job id dön.
   - `GET /export/{id}/status`: DB'den çek.
   - `GET /export/{id}/download`: status==completed kontrolü + workspace match.
   - `GET /export/run/{run_id}`: workspace + run filter ile DB'den.

4. UI `TaskProgress` veya benzer akış ile durum izlesin (P1'deki wrapper'ı kullanır).

5. Test: cross-worker simulation (paralel iki test process'i farklı job tetikler, status birbirini bozmaz).

### P3 — Migration / Production Bootstrap Düzeltmesi

Amaç: `init_db + stamp head` workaround'ını kaldır; production gerçek `alembic upgrade head` ile başlasın.

**Not (Codex önerisi sonrası):** Migration chain smoke testi ve onarımı **P0 madde 2'ye taşındı** (DB'de değerli veri yok kararıyla erken ele alındı). Bu nedenle P3 büyük ölçüde hafifledi — geriye sadece prod komut değişimi ve CI entegrasyonu kaldı. Eğer P0 madde 2'de chain kırıksa orada onarılmış olur; aksi halde aşağıdaki adımlar dümdüz uygulanır.

1. (P0 madde 2 tamamlanmadıysa) Migration chain'i empty PostgreSQL üzerinde test et ve gerekirse onar — özellikle workspace phase A/B/C dizisi.

2. CI smoke test'i ekle.
   - GitHub Actions iş akışı: yeni PG service → `alembic upgrade head` → tablo varlık assertion'ı.
   - Bu smoke geçmeden hiç prod komutuna dokunulmaz.
   - P0'da yazılan `tests/integration/test_migration_chain.py` CI matrix'ine bağlanır.

3. Production veritabanı state'ini doğrula (hafifletilmiş — Codex önerisi sonrası).
   - Geliştirme aşamasındaki DB'lerde **değerli veri olmadığı için drop + recreate** kabul edilebilir yol.
   - `alembic current` head'e eşit değilse: DB'yi sıfırla, `alembic upgrade head` ile baştan kur. Manual reconciliation migration **gerekli değil**.
   - Gerçek production deploy'undan önce bu adım tekrar gözden geçirilir; o noktada veri taşıma kararı verilir.

4. Komut değişimi.
   - `docker-compose.prod.yml:6` → sadece `alembic upgrade head && uvicorn ...`.
   - `init_db()` çağrısı prod komutundan çıkar.
   - `app/main.py` lifespan'de `init_db()` çağrısı yalnızca `DEBUG=true` koşulunda kalır (geliştirici kolaylığı).

5. Rollback planı.
   - Komut değiştirildikten sonra deploy başarısız olursa eski komuta dönülebilmesi için tag/revert hazır.

### P4 — UX, Logging, Test, Tip ve Cleanup (eski Faz 2-4 kalanı)

Amaç: Regresyon kapasını arttır, dış görünüş ve gözlemlenebilirliği düzelt.

1. Logging modüllerini tekleştir (K4 / eski Faz 2.1).
   - Tek `setup_logging()` source-of-truth, file/rotating handler aktif, Celery worker dahil.
   - Diğer modül silinir veya birleştirilir.

2. Mojibake temizliği (K5 / eski Faz 2.2).
   - **Frontend** mojibake taraması: `rg "Ã[\x80-\xBF]|Å[\x80-\xBF]|Ä[\x80-\xBF]" frontend/src` 0 sonuç verene kadar düzelt.
   - Düzeltme sonrası UTF-8 BOM kontrolü.
   - Backend kullanıcıya dönen metinler için ayrı tarama.

3. Error handling standardize et.
   - `services/api.ts` Axios response interceptor.
   - 401/403/500 hata mesajları tek noktada normalize.
   - `alert()` kullanımları `ErrorBanner` veya merkezi toast'a taşı.

4. Google Ads error UX (E6 güncellenmiş — allow-list yerine).
   - Credential eksik/geçersizse 500 yerine 400/422 + anlaşılır mesaj.
   - `language_id` / `geo_target_id` için **format normalize** + tek resource path helper (allow-list bakım yükü olmasın).
   - Google Ads API'sinden gelen `invalid_argument` hatasını kullanıcı diliyle çevir.
   - `/google-ads/customers` başarısız olduğunda URL seed extraction blok olmasın (fallback).

5. Generation workspace guard'ı (eski Faz 3.1).
   - Bulk SEO, Ads RSA, Social, get/list/regenerate endpoint'leri workspace param + helper.
   - Tekil `/generation/seo-geo` workspace zorunlu veya deprecated.

6. Ads RSA üretimini async hale getir (Y5 / eski Faz 3.2).
   - UI RSA endpoint'ini mevcut `generation.ads_generate` Celery task'ına bağla veya yeni RSA task.
   - `TaskProgress` ile takip.

7. `BrandProfile.scoring_run_id` fallback query'lerini azalt (Y4 / eski Faz 3.3).
   - `brand_profile.py`, `pool_builder.py`, `brand_defaults.py`, `data_collector.py`, `seo_geo_generator.py` refactor.
   - Yeni kaynak `ScoringRun.brand_profile_id`.

8. `verify_scoring_run` zorunlu mod'a tamamen geç (eski Faz 3.4).
   - Read endpoint'lerde de `brand_profile_id=None` çağrılarını grep ile listele, hepsini kapat.
   - Helper imzasından opsiyonel kaldır, `allow_legacy=True` istisnası nadir özel durumlar için bırak.

9. Export stale semantiği netleşsin (O7 / eski Faz 3.5).
   - `is_stale` set/reset olaylarının tam matrisi (channel reassign, relevance recompute, workspace refresh, scoring rerun).
   - Tek source-of-truth (state_machine veya workspace_refresh).

10. CLAUDE.md test bilgisini düzelt (O1).
    - `tests/unit` 6 dosyalık liste + eksik kategoriler.

11. API/auth test'leri.
    - `verify_api_key` enable/disable senaryoları.
    - X-API-Key missing/invalid yanıtları.

12. TypeScript tip toparlama (O5).
    - `ProfileData`, `Workspace`, `ScoringRun`, `TaskResult`, `RelevanceResult` ortak tipler.
    - `Channels.tsx` duplicate `ScoringRun` interface'i kaldır.
    - `any` sayısını azalt.

13. Orphan dosya temizliği (O2, O3, N2).
    - `ValidationLog.tsx`, `GoogleAds.css`, `google_ads_probe` — sil veya `tools/`'a taşı.

14. Dead/legacy task ve endpoint (O10, N7).
    - `scoring_tasks.py` orphan task'lar.
    - Legacy single generation endpoint'leri.

15. Embedding cache + fallback davranışı (O8).
    - Redis veya DB tabanlı cache, key = workspace + normalized text + model + task type.
    - Failure fallback `1.0` yerine `None` veya açık "relevance unavailable" durumu.

16. Tooling.
    - Frontend ESLint/Prettier, `lint/format/test` script'leri.
    - Pre-commit / GitHub Actions: status lint guard + raw fetch guard + unit testler.

17. Dashboard metric tanımları + URL seed cache davranış kuralı (E5, E7).
    - Dashboard kart scope'ları net yazılsın, tooltip veya CLAUDE.md'de belgelensin.
    - URL seed cache key formülü, TTL, force refresh kuralı, cache hit mesajı koşulu yazılsın.

## 3. İlk Uygulama Sırası (P0-P4 paketlerine göre)

Strikt sıra: bir paket bitmeden sonrakine geçilmez. Her paket kendi PR'ı olarak merge edilir.

### P0 sırası (Veri Güvenliği Hotfix — 5 commit halinde)

**C1**:
1. Docker-Postgres test çerçevesini ayağa kaldır (`docker-compose.test.yml` + pytest fixture + truncate).
2. Empty-DB migration smoke testi (`tests/integration/test_migration_chain.py`) — chain kırıksa burada onarılır.

**C2**:
3. `verify_scoring_run(..., mutating=False)` helper (`app/core/workspace.py`) + net 400/404 mesajları.
4. `scoring.py` guard'ları (mutating zorunlu, read warning'li opsiyonel).
5. `channels.py` guard'ları.

**C3**:
6. `keywords.py` legacy yolların kapanışı (DELETE /all, DELETE /{id}, list, import için workspace zorunlu; `crud.delete_keyword` global çağrısı kaldırılır; "legacy behavior" docstring'leri tamamen silinir).

**C4**:
7. `tasks.py` guard'ları (read için bile workspace zorunlu — task leak kritik).
8. `export.py` minimal güvenlik hotfix'i (workspace guard + background task içinde yeni `SessionLocal()` + dict key workspace-scoped).

**C5**:
9. `tests/integration/test_workspace_isolation.py` cross-workspace leak + DELETE 404 + 400 mesaj içeriği senaryoları.
10. P0 merge.

**Önemli:** C3'ten itibaren Keywords/Tasks/Export sayfaları frontend'de 400 alır. P1'in karşılık gelen `keywordsApi`/`tasksApi`/`exportApi` wrapper güncellemeleri **C3 sonrası paralel olarak başlatılabilir** — P1'in tamamen P0 bittikten sonra başlamasını beklemeye gerek yok. P0 strict sıra içeride; P1 dışarıda ve C3-C4 ile el sıkışabilir.

### P1 sırası (Frontend Workspace Wiring — P0'dan sonra)
10. `services/api.ts` parametre genişletmeleri (scoring/channels/tasks/keywords/export).
11. 14 raw fetch çağrısının wrapper'a taşınması.
12. `tsc --noEmit` / `npm run build` typecheck kapısı.
13. Sayfa wiring (Dashboard, Tasks öncelikli; sonra Scoring/Channels/Relevance/Generation/Export).
14. `socialStore` workspace reset mekanizması.

### P2 sırası (Export Altyapı Refactor)
15. `ExportJob` modeli + migration.
16. `app/tasks/export_tasks.py` Celery task.
17. Export endpoint'leri DB-backed status'a geçir.
18. UI progress entegrasyonu.

### P3 sırası (Migration / Production Bootstrap — hafifletildi)
Migration chain testi P0/C1'e taşındı; bu paket büyük ölçüde "prod komut değişimi"ne indirgendi.
19. (Gerekiyorsa) Migration chain onarımı (P0/C1'de zaten yapılmış olmalı).
20. CI'a empty-DB smoke test'inin pipeline'a bağlanması.
21. Geliştirme DB'sini drop + recreate ile baseline'a getir (değerli veri yok varsayımı).
22. `docker-compose.prod.yml` komutunu `alembic upgrade head && uvicorn ...` yap; `init_db + stamp head` kaldır.
23. Lifespan'deki `init_db()` çağrısını DEBUG-only'ye sınırla.
24. Rollback planı (önceki komutun tag/revert hazırlığı).

### P4 sırası (UX / Logging / Tech Debt)
25. Logging tek source-of-truth.
26. Mojibake taraması + düzeltmesi.
27. Axios interceptor + standart error UI.
28. Google Ads error UX (normalize + helper + invalid_argument çevirisi).
29. Generation workspace guard'ı + Ads RSA async.
30. `BrandProfile.scoring_run_id` fallback temizliği.
31. `verify_scoring_run` opsiyonel mod kaldırılması.
32. Export stale matrisi netleştirme.
33. CLAUDE.md test bilgisi düzeltmesi.
34. TypeScript tip toparlama.
35. Orphan dosya temizliği.
36. Dead/legacy task ve endpoint temizliği.
37. Embedding cache + fallback davranışı.
38. ESLint/Prettier + pre-commit/CI guard'lar.
39. Dashboard metric tanımları + URL seed cache davranış kuralı dokümantasyonu.

Bu sıra, en yüksek veri sızıntısı ve veri kaybı riskini (export hotfix, keywords global delete, scoring/channels/tasks leak) **P0'da bir hafta içinde** kapatır; frontend ve büyük refactor işleri sonraki paketlere yayılır. P0'ın frontend dokunmadan tamamlanması bilinçli bir karardır: backend guard'ı yokken frontend wiring boşa düşer.
