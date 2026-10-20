# Kullanıcı ve Yetki Yapısı v2 — Ajans Satışı İçin

**Durum:** TASLAK rev.4 — 11 Ekim 2026. Kod yok, commit yok. Onay bekleyen kararlar §9.
**Önceki belge:** `kullanici-yapisi-plan.md` (06.10, sunucu sorumlusu; araştırma ağacının `algoritma-kilitleri` dalında, bu depoda yok). Bu belge onun yerine geçer.
**Durum (12.10):** patron kararıyla ERTELENDİ; ileride uygulanacak. Başlarken: K1–K11 → F0 → F1. Koddaki satır referansları ve dal commit farkları 11.10 tarihlidir, uygulama günü yeniden doğrulanır.
**İnceleme izi:** Codex 1–5. turlar Ek A–E'de madde madde hükme bağlandı. rev.4 = Claude'un sıfırdan öz-incelemesi + Codex 4. tur. **Codex 5. tur (rev.4 üzerine): "uygulama tabanı olarak kullanılabilir; plan düzeyinde uygulamayı durduran itiraz kalmadı"** + F2 için üç uygulama ayrıntısı (§4 "F2 uygulama kuralları", Ek E). Kodun güvenliği ve bütçe garantisi uygulama testleriyle doğrulanacak. K1–K11 teknik incelemeden ayrı ÜRÜN kararlarıdır (patron).

---

## 0. Bağlam ve kabuller

**Canlıda olan (sunucu `deploy/auth-lean-merge`, 11.10):** tek `users` tablosu (argon2id, ilk girişte parola değişimi, `session_version` ile parola değişince diğer oturumlar düşer), Redis oturum (30 gün kayan), `LOGIN_ENABLED` açık, kaba kuvvet sınırı. Rol, kiracı, denetim kaydı YOK: giriş yapan herkes her workspace'te her şeyi yapar, Digitus'un global Google Ads bağlantısını ve AI bütçesini kullanır. Sunucuda CLI ile açılmış GERÇEK kullanıcılar var (sayısı ve kimlikleri sunucu sorumlusunda).

**Ürün çerçevesi (patron, 11.10):**
1. İlk alıcı reklam ajansları. Ajans platformu kendi müşterileri için kullanır.
2. Üyelik sonrası ön hazırlık ekranında ajans kendi Google Ads erişimini bağlar, kendi hesaplarıyla çalışır.
3. Ajans seçtiği çıktıyı müşterisine salt-okunur gösterir.

**Kavramlar:**
- **Kiracı (tenant)** = ajansın platformdaki hesabı. Digitus'un kendisi de bir kiracıdır ("Digitus"; mevcut veri ve mevcut kullanıcılar buraya bağlanır).
- **Workspace** = `brand_profiles` satırı = bir marka çalışması. Müşteri ≠ workspace; bir müşterinin birden çok markası olabilir. Ayrı "müşteri" tablosu YOK; yayım workspace başına yapılır.
- **Yayım (publication)** = ajansın müşterisine gösterdiği dondurulmuş çıktı seçkisi.

**Kapsam dışı (v2):** müşteri hesabı/girişi, kimliğe bürünme, beyaz etiket, SSO, oturumun DB'ye taşınması, workspace bazlı ekip ataması, ödeme/abonelik, açık kayıt, davet linki/e-posta akışı, kiracı başına Google Ads günlük kota (ölçülür, tavan konmaz), denetim kaydı TABLOSU (yapısal log yeter).

**Kabul edilen sınırlar:** dış ajans sahibi parolasını unutursa sıfırlama Digitus'un CLI'ıyla (kimlik doğrulaması telefon/e-posta ile elle; CLI `must_change_password` set eder ve `session_version` artırır → tüm oturumlar düşer). Bütçe bitince görev YARIDA durabilir (§4). Hesapsız yayım linki = linki olan herkes görür (§6).

---

## 1. Aktörler ve roller

| Rol | Kim | Görür | Yapar |
|---|---|---|---|
| `platform_admin` | Digitus'ta 1–2 kişi (K9) | Tüm kiracılar | Kiracı açar/askıya alır, tavan belirler, üye ekler. **Üyelik zorunluluğundan İSTİSNADIR:** herhangi bir aktif kiracıyı seçer, içeride `owner` yetkisiyle ve KENDİ kimliğiyle çalışır; her çapraz erişim yapısal loga gerekçeyle yazılır |
| `owner` | Ajans sahibi/yetkilisi | Kiracının tüm workspace'leri | Her şey + üye ekleme, Google Ads bağlantısı, kullanım görünümü |
| `editor` | Ajans uzmanı | Kiracının tüm workspace'leri (K2) | Workspace açar, import, koşu, içerik üretimi, export, yayım (K4). Üye/bağlantı/tavan yönetimi YOK |
| `viewer` | Ajans içi izleyici | Kiracının tüm workspace'leri | Yalnız okuma; mevcut export'u indirme (K3) |
| müşteri | Ajansın müşterisi, HESAPSIZ | Yalnız kendisine yayımlanan seçki | Linkle görüntüler; `allow_download` açıksa indirir |

Kurallar: Digitus personeli varsayılan olarak `platform_admin` DEĞİLDİR; destek personeli gerektiğinde ilgili kiracıya `viewer` üye yapılır. Mevcut sunucu kullanıcıları F1'de Digitus kiracısına `editor` olarak taşınır; K9'daki kişiler ek olarak `is_platform_admin=true` + Digitus `owner`. Düğme gizlemek kullanıcı deneyimidir; yetki yalnız backend'de denetlenir.

---

## 2. Veri modeli

Çapa `brand_profiles.tenant_id`. Diğer tablolara dokunulmaz; sahiplik `brand_profile_id`/`scoring_run_id` üzerinden türetilir.

```
tenants
  id, name, status (active|suspended), ai_cost_cap_usd_month NUMERIC NULL, created_at

users (mevcut) + is_platform_admin BOOL NOT NULL DEFAULT false

memberships
  id, user_id FK, tenant_id FK, role (owner|editor|viewer), created_at
  UNIQUE(user_id, tenant_id)

brand_profiles (mevcut) + tenant_id FK NOT NULL (backfill sonrası)
                        + google_ads_connection_id FK NULL
                        + google_ads_customer_id VARCHAR NULL   -- workspace ↔ reklam hesabı

google_ads_connections
  id, tenant_id, connected_by_user_id, google_account_email,
  login_customer_id VARCHAR NULL (MCC), refresh_token_enc BYTEA,
  status (active|revoked|error), last_error, last_ok_at, created_at, revoked_at
  -- kiracı başına birden çok bağlantı; Digitus'unki de BURADA (env'den tek seferlik seed)

tenant_budget_months
  tenant_id FK, ym CHAR(7), spent_usd NUMERIC, reserved_usd NUMERIC
  PRIMARY KEY (tenant_id, ym)

ai_call_reservations        -- mevcut AiCostReservation şekli (ceiling/actual/state) temel alınır
  id, tenant_id, ym CHAR(7), task_id NULL, provider, model, ceiling_usd, actual_usd NULL,
  state (reserved|in_flight|settled|unknown|released), created_at, sent_at, settled_at
  -- ym: ayrıldığı ay; kapanış HER ZAMAN bu ayın satırına (ay sınırı aşan çağrı)

publications
  id, brand_profile_id, scoring_run_id, created_by_user_id, title,
  snapshot JSONB, export_path VARCHAR NULL, allow_download BOOL,
  token_hash UNIQUE, expires_at, revoked_at, created_at
```

Kaldırılanlar (rev.3'e göre): `tenants.slug` (hiçbir URL kullanmıyor), `tenants.deleted_at` (status yeter), `google_ads_ops_cap_day` (ertelendi), `invitations` (ertelendi; §7 F5), `audit_log` (yapısal log; §3.7), `TaskResult.tenant_id` (görev argümanı; §3.3).

Oturum Redis'te KALIR. Şifreleme: `cryptography` (Fernet), anahtar `SECRETS_ENCRYPTION_KEY` env; kaybı = tüm bağlantılar yeniden bağlanır (kabul).

---

## 3. Erişim kapısı

**3.1 Bağlam (erişim nesnesi).** Her istekte, giriş doğrulamasından SONRA, `AccessContext{user_id, tenant_id, role, is_platform_admin}` kurulur.
- `X-Tenant-Id` yalnız bir SEÇİMDİR. Üye kullanıcı için sunucu her istekte `memberships`'ten aktif üyeliği ve kiracının `status=active` olduğunu DB'den doğrular (bugünkü `require_login` kullanıcıyı zaten her istekte yüklüyor; üyelik aynı sorguya eklenir). Üyelik kaldırılmış veya kiracı askıdaysa SONRAKİ istek 403. Tek üyelik → header'sız otomatik; çoklu üyelik → header zorunlu, yoksa 400; üye olmadığı kiracı → 403.
- `platform_admin`: header zorunlu; herhangi bir AKTİF kiracıyı seçebilir (üyelik aranmaz); bağlamda `role=owner`; her istek yapısal loga `admin_cross_tenant` olarak yazılır (K6).
- **Taşıma mekanizması:** mevcut `require_login` SENKRON `def` (`app/core/login.py:202`) → threadpool'da set edilen `ContextVar` endpoint'e TAŞINMAZ. Bağlam `async def` + `yield` dependency'si ile event-loop bağlamında kurulur (`token = ACCESS.set(ctx)`; `finally: ACCESS.reset(token)`), `api_router`'a router seviyesinde bağlanır. F1'de belirsizlik görülürse `request.state` + açık parametre geçişine dönülür; tercih güvenliktir.
- **Fail-closed:** `LOGIN_ENABLED=true` iken bağlam yoksa 401, kontrol ATLANMAZ. **`LOGIN_ENABLED=false` (yalnız dev/test):** bağlam SABİT = Digitus kiracısı, `role=owner`; böylece `tenant_id NOT NULL` ile oluşturma yolu çalışır, kiracı filtresi yoktur. Production guard'a (`app/config.py`) `LOGIN_ENABLED=true` zorunluluğu eklenir; izolasyon testleri bayrağı açık koşar.

**3.2 Tek kapı, değişmeyen imza.** `verify_workspace(db, brand_profile_id)` (`app/core/workspace.py`; API dışından çağrılmıyor, doğrulandı) bağlamı okur: `platform_admin` değilse ve `workspace.tenant_id != bağlam.tenant_id` ise **404** (varlık ifşa edilmez). `verify_scoring_run` / `verify_task_in_workspace` / `verify_export_in_workspace` üzerinden geçen ~100 çağrı noktası tek değişiklikle kapanır.

**3.3 Kapıdan geçmeyen yollar (envanter — F1'de dosya dosya, F0 sonrası birleşik kaynakta):**
- Workspace listeleme → kiracı filtresi; oluşturma → `tenant_id` bağlamdan.
- Global Google Ads bağlantısını kullanan yollar → F3'e kadar **yalnız Digitus kiracısının `platform_admin` üyeleri**; diğer kiracılar 403. Bilinen yollar: (i) `/google-ads/*` (9 rota); (ii) workspace keyword yenileme (`app/api/v1/brand_profile.py` ~2013–2023: `_get_service()` + `settings.GOOGLE_ADS_CUSTOMER_ID` + `svc.enrich_keywords`). Envanter yöntemi: `_get_service(`, `enrich_keywords(`, `generate_keyword_ideas(`, `settings.GOOGLE_ADS_`, `GoogleAdsService` için tüm `app/` (`enricher.enrich_keywords` AI ön-filtredir, Google Ads değil).
- `dashboard` (tek `Optional` `brand_profile_id`) → None iken yalnız boş durum, doğrulanır. `/keywords/{id}` → `WorkspaceKeyword` günceller, workspace doğrulamalı (doğrulandı).
- **Arka plan işleri izolasyon kapsamındadır:** (a) görev yalnız kapıdan geçmiş endpoint'ten, doğrulanmış run/workspace ile oluşturulur; (b) `tenant_id` görev ARGÜMANI olarak geçer (kolon yok); (c) worker ilk adımda run'ın `brand_profile.tenant_id`'sini argümanla karşılaştırır, eşleşmezse `failed`; (d) worker Google Ads bağlantısını ve maliyeti run'ın workspace'inin kiracısından çözer. Görev imzası değişir → worker restart.
- Site profil analizi (BackgroundTasks) → aynı (c)/(d).
- Auth uçları, `/health`, yayım public ucu → giriş kapısı dışında, kendi kapıları var.

**3.4 Rol kapısı.** Yetki HTTP metoduyla DEĞİL, işlem listesiyle tanımlanır (workspace GET'i `fail_if_stuck` ile takılı analizi yazar; sistem bakımıdır, viewer için de çalışır).
- `viewer`: okuma uçları, mevcut export'u indirme (K3), yayımlanmış seçki. YAPAMAZ: yeni export, import, koşu, üretim, analiz, yayım, silme.
- `editor` = viewer + workspace oluşturma/silme, import, koşu, üretim, export oluşturma, yayım (K4).
- `owner` = editor + üyeler, Google Ads bağlantıları, tavan görünümü.
- `require_role("editor"|"owner")` yazma uçlarına tek tek; F1 envanterinde her rota bir role eşlenir, eşlenmemiş yazma rotası testi düşürür.

**3.5 İzolasyon test paketi (F1 çıkış kapısı).** "404 geldi, güvenli" YETMEZ. Tek parametrik test: `app.routes` içindeki tüm `/api/v1/*` rotaları numaralandırılır; allowlist (auth, health, format-matrix, public yayım) dışındaki her rota için B kiracısında GERÇEK veri kurulur ve aynı GEÇERLİ istek B oturumuyla 200 (pozitif kontrol), A oturumuyla 404 döner; istek fabrikası olmayan rota testi düşürür. Ek senaryolar: viewer yazma rotasında 403; yetkisiz istekten sonra `task_results`'ta yeni satır YOK ve hedef satır değişmemiş; üyeliği kaldırılan kullanıcı 403; askıdaki kiracı 403; admin header'sız 400, header'la 200 ve log satırı var; F3'te önbellek yolu. Ret kodları: sahiplik → 404, rol → 403, bağlam yok → 401, kiracı seçimi eksik → 400.

**3.6 nginx `X-API-Key`.** Satır kalır (K10); iki kapı bağımsız ve ikisi de aktif.

**3.7 Yapısal log (denetim kaydı yerine).** Olaylar: admin çapraz erişim, üye ekleme/çıkarma/rol değişimi, bağlantı ekleme/iptal, tavan değişimi, yayım oluşturma/iptal. Loguru JSON satırı, **dosya sink'i rotasyonlu ve kalıcı** (konteyner stdout YETMEZ), sunucuda erişimi kısıtlı dizin, `grep` ile aranabilir. Uyum gereksinimi doğarsa tabloya taşınır.

---

## 4. Kiracı kullanım tavanı (dış kullanıcı üretim başlatmadan ÖNCE)

- `tenants.ai_cost_cap_usd_month` (K7); `NULL` = sınırsız (yalnız Digitus).
- **Tek kural:** Hiçbir AI çağrısı, azami maliyeti ATOMİK olarak ayrılmadan gönderilmez. Bütçe yetmiyorsa çağrı yapılmaz. **Gönderilmiş ve sonucu belirsiz çağrının parası ASLA geri alınmaz.**
- **Kanca noktaları — tek geçit YOKTUR (doğrulandı):** doğrudan sağlayıcı çağrısı 5 noktada/4 sınıfta: `GeminiService` ×2 (`ai_service.py:440,538`), `AnthropicService` (`anthropic_service.py:335`), screening sağlayıcısı (`screening/providers.py:286`), relevance embedding (`relevance_scorer.py:325`). Ortak `reserve_call()/settle_call()` yardımcısı bu beş noktaya bağlanır; F0 sonrası birleşik kaynakta sayı yeniden çıkarılır. Mevcut usage telemetri kaydı aynı noktalarda → kanca ona biner.
- **Mekanizma (zarf YOK, başlangıç rezervasyonu YOK → çift sayım yok):**
  1. **Ayır:** `UPDATE tenant_budget_months SET reserved_usd = reserved_usd + C WHERE tenant_id=? AND ym=? AND spent_usd + reserved_usd + C <= cap` (satır yoksa `INSERT … ON CONFLICT`); 0 satır → çağrı yapılmaz, görev `failed: tenant_cost_cap`, o ana kadarki sonuç korunur. C = azami maliyet (girdi token + max çıktı token × fiyat). `ai_call_reservations` satırı `state=reserved`.
  2. **Gönder:** hemen önce atomik geçiş `UPDATE … SET state='in_flight', sent_at=now() WHERE id=? AND state='reserved'`; 0 satır (janitor serbest bırakmış) → GÖNDERME, yeniden ayır.
  3. **Kapat:** yanıt geldi → `spent_usd += a, reserved_usd −= C`, satır `settled`. Yanıt BELİRSİZ (timeout, bağlantı kopması, istisna) → `spent_usd += C, reserved_usd −= C`, satır `unknown` (güvenli taraf: tavan kadar harcanmış sayılır).
  4. **Janitor:** yalnız `state=reserved` VE `created_at < now()−T` satırları `released` yapar (`reserved_usd −= C`). `in_flight` satırlara DOKUNMAZ; worker ölürse `in_flight` kalır ve ay sonuna kadar ayrılmış sayılır. Aylık mutabakat CLI'ı (`platform_admin`) sağlayıcı faturasıyla karşılaştırıp elle kapatır.
  Değişmez (test): `reserved_usd == Σ ceiling (state ∈ {reserved, in_flight})`.
- **F2 uygulama kuralları (Codex 5. tur, zorunlu):**
  1. **Tek transaction:** aylık sayacın güncellenmesi ile rezervasyon satırının oluşturulması/durum geçişi/kapatılması AYNI DB transaction'ında; biri olmadan diğeri commit edilmez.
  2. **Tekrara dayanıklı kapatma:** kapatma `UPDATE … WHERE id=? AND state='in_flight'` ile yapılır; 0 satır = zaten kapanmış → sayaç TEKRAR güncellenmez. Aynı yanıtın iki kez işlenmesi veya retry, aynı rezervasyonu iki kez harcanmış sayamaz; retry her zaman YENİ rezervasyon açar.
  3. **Ay bağlılığı:** rezervasyon `ym` taşır; ayın son günü ayrılan ve sonraki ayda biten çağrı, AYRILDIĞI ayın satırından kapatılır (`tenant_budget_months(tenant_id, ym)`), o günkü aydan değil.
  Mutabakat CLI'ı gerektiğinde ay içinde de koşabilir; worker ölümünde belirsiz tutarın bütçeyi tutması sert tavanın kabul edilen bedelidir.
- **Başlangıç ön-kontrolü BİLGİ amaçlıdır:** görev başında `tahmin > kalan` ise 409 döner, ama geçen görevin tamamlanacağını GARANTİ ETMEZ; bütçe sonraki çağrıda bitince görev yarıda durur. Pilot için kabul.
- Sıcak satır: kiracı-ay satırına çağrı başına 2 UPDATE; motor paralelliği 16 → önemsiz. Chunk'lı görevler ek yapı istemez.
- Google Ads: kiracı başına günlük kota ERTELENDİ, işlem sayısı kiracı başına ölçülür (log). **Mevcut `_call_with_retry` hız kontrolü ve retry KORUNUR** (planlama servisi 1 QPS; kota bol olsa da hız sınırı ayrı).
- Owner için "Kullanım" sayfası: ay içi harcama / tavan. `platform_admin` tavanı düzenler.
- **F2 eşzamanlılık testleri:** (i) bütçesi k çağrıya yeten aya N>k paralel ayırma → tam k geçer; (ii) değişmez her adımda; (iii) `reserved` satırı janitor bıraktıktan sonra gönderme geçişi 0 satır döner, çağrı yapılmaz; (iv) `in_flight` satır janitor'dan etkilenmez; (v) belirsiz yanıt `unknown` + `spent += C`; (vi) aynı rezervasyon iki kez kapatılınca sayaç BİR kez değişir; (vii) ay sınırı: `ym=2026-10`'da ayrılan, Kasım'da kapanan çağrı Ekim satırını günceller, Kasım satırı değişmez; (viii) sayaç güncellemesi başarısızsa rezervasyon satırı da commit edilmemiştir (transaction).

---

## 5. Google Ads bağlantısı (öz-hizmet)

**Kullanıcıdan anahtar İSTENMEZ.** Developer token ve OAuth client Digitus'un global env'inde kalır (uygulama kimliği); ajans yalnız Google izin ekranından geçer.

1. Owner "Google Ads hesabını bağla" → **`state`**: rastgele 32 bayt, Redis'te 10 dk TTL, başlatan oturuma + kiracıya bağlı, TEK kullanımlık (callback'te doğrulanır ve silinir; eşleşmezse 400). Google OAuth (adwords kapsamı) → callback refresh token'ı Fernet ile şifreler, `google_ads_connections`'a yazar.
2. Hesap listesi: `list_accessible_customers` + her yönetici (MCC) hesabı için `customer_client` sorgusuyla alt ağaç (bugünkü kod yalnız doğrudan erişilebilirleri listeler).
3. Owner/editor her workspace'i bir reklam hesabına eşler (`google_ads_connection_id` + `google_ads_customer_id`); `login_customer_id` bağlantıdan.
4. **Tek runtime yolu:** `GoogleAdsService` kimlik bilgisini YALNIZ bağlantı satırından alır. Digitus'un env'deki refresh token'ı F3'te tek seferlik CLI ile Digitus kiracısına bağlantı satırı olarak seed edilir; settings tabanlı kimlik bilgisi okuması runtime'dan KALDIRILIR.
5. `invalid_grant`/iptal → `status=error`, arayüzde "yeniden bağla"; çoklu bağlantı, biri iptal olunca diğerleri etkilenmez.
6. **Önbellek de kapıdan geçer:** bugün `gads:campaigns:{customer_id}` (`google_ads.py:272`). F3'te: okumadan ÖNCE kiracının bağlantıya ve `customer_id`'ye erişimi doğrulanır; anahtarlar `{connection_id}` kapsamına alınır; bağlantı iptalinde temizlenir.

**Ön hazırlık ekranı:** ilk girişte (parola değişiminden sonra) "Google Ads'i bağla / CSV ile devam et" kartı. Zorunlu DEĞİL; CSV kullanıcısı atlar.

**Dış ön koşullar (F0'da başlatılır):** Google Cloud projesinde OAuth izin ekranı doğrulaması (hassas kapsam, dış kullanıcı); erişim seviyesi kontrolü (Explorer 2.880 işlem/gün prod, Basic 15.000/gün, Standard sınırsız; seviye OAuth kimliğinin Cloud projesine bağlı) → mevcut seviye API Center'dan, ajans başına tahmin, gerekiyorsa başvuru (K8).

---

## 6. Müşteriye yayımlama

"Workspace'i paylaş" DEĞİL, **"teslimatı yayımla"**: ajans seçer, önizler, yayımlar; sonradan üretilen taslaklar görünmez.

- Seçim kaynakları: aktif ADS seti grupları/asset'leri, SEO içerikleri, sosyal fikir/içerikler (`is_selected`/aktif set ön işaretli).
- Yayımla → seçilenler dondurulmuş `snapshot` JSON; `allow_download` açıksa export üretilir, `export_path` saklanır. Rastgele 32 bayt token, DB'de hash.
- Public uç `GET /api/v1/public/publications/{token}` giriş kapısı DIŞINDA (ayrı router, `main.py`'de auth router gibi monte); kapısı: hash eşleşir, süresi dolmamış, iptal edilmemiş → snapshot; aksi 404. Frontend `/p/{token}` salt-okunur sayfa, korumalı rotaların DIŞINDA.
- Süre varsayılanı 30 gün, iptal anında geçersiz, aynı workspace'te birden çok yayım (K5). Snapshot öğe sınırı 200.
- Bilinen sınır: linki olan herkes görür; indirme düğmesini kapatmak kopyalamayı engellemez.
- Arayüz: "Müşteriye yayımla" modali (kanal başına onay kutuları + önizleme + link kopyala) ve "Yayınlar" listesi (iptal). Ayrı ÖZELLİK, F4'te ayrı tahminlenir; snapshot şeması ve sayfa tasarımı F4 başında yazılır.

---

## 7. Fazlar ve çıkış kapıları

| Faz | İş | Çıkış kapısı |
|---|---|---|
| **F0 Kaynak eşitleme** | `auth-lean-merge` → `lean-taslak` merge (K1); CLAUDE.md giriş bölümü; §3.3 ve §4 envanterleri birleşik kaynakta yeniden sayılır; Google doğrulama + erişim seviyesi başvuruları; prod Redis kalıcılığı kontrol; loguru dosya sink'i kalıcı mı kontrol | lean-taslak'ta login testleri yeşil; sunucu HEAD'i (13c636d) lean-taslak'tan ff ile ulaşılabilir |
| **F1 Kiracı + rol + izolasyon** | Migration'lar (idempotent kalıp): `tenants`, `memberships`, `users.is_platform_admin`, `brand_profiles.tenant_id`; backfill: tek "Digitus" kiracısı, TÜM mevcut workspace'ler ona, TÜM mevcut kullanıcılar Digitus `editor`, K9 listesi admin + `owner`; `tenant_id NOT NULL`; erişim bağlamı (§3.1, admin istisnası, dev sabit bağlamı); `verify_workspace` kapısı; liste/oluşturma; §3.3 envanteri + arka plan kuralları; `require_role` + rol eşleme; Google Ads yolları yalnız Digitus admin; production guard; §3.7 yapısal log; CLI `create_tenant` / `add_member` / `reset_password`; **frontend:** authStore rol+kiracı, admin için basit kiracı seçici (`/auth/me` kiracı listesi), viewer'da yazma düğmeleri gizli, 403 ekranı; §3.5 test paketi | §3.5 yeşil (pozitif kontrol dahil); iki kiracıyla elle çapraz deneme; mevcut kullanıcılar giriş yapıp Digitus verisini görüyor; mevcut 4.5k test yeşil; frontend lint+build+test; worker restart |
| **F2 Kiracı tavanı** | `tenant_budget_months`, `ai_call_reservations`, `reserve_call/settle_call` beş kanca noktasına, janitor (yalnız `reserved`), mutabakat CLI, başlangıç ön-kontrolü 409, Kullanım sayfası, Google Ads işlem ölçümü | §4 testleri (i)–(viii) yeşil (tek transaction, idempotent kapatma, ay bağlılığı dahil); değişmez; tavan NULL kiracı etkilenmez; beş kanca noktası işaretli; yarıda duran görev sonucunu koruyor |
| **F3 Google Ads bağlantısı** | OAuth + `state`, `google_ads_connections`, Fernet, MCC ağacı, workspace eşlemesi, tek runtime yolu + Digitus seed CLI, keyword yenileme yolu kiracı bağlantısına, önbellek kapsamı + okuma öncesi sahiplik, ön hazırlık kartı | İki kiracı farklı hesapla keyword çeker, birbirinin hesabını ne API ne önbellek yolundan listeleyebilir; yanlış/tekrar `state` 400; iptal edilen bağlantı "yeniden bağla" + önbellek temiz; settings tabanlı kimlik bilgisi okuması kodda yok |
| **F4 Yayımlama** | `publications`, snapshot şeması, public uç, `/p/{token}` sayfası, modal + liste | Yayım sonrası üretilen taslak görünmez; iptal/süre dolumu 404 |
| **F5 Owner üye ekleme** | Owner "üye ekle (e-posta + rol)": e-posta YOKSA geçici parolayla kullanıcı açılır, parola owner'a TEK kez gösterilir, ilk girişte değişim zorunlu; e-posta VARSA yalnız üyelik eklenir, kullanıcının parolasına DOKUNULMAZ (başka ajansın hesabı ele geçirilemez); üye çıkarma/rol değişimi; hepsi yapısal loga | Dış ajans sahibi üye ekler, yeni üye girer ve yalnız o kiracıyı görür; var olan e-posta eklenince parola değişmemiş |

Sıra: izolasyon + roller → tavan → Google Ads → yayım → üye yönetimi. **İlk ajans F1+F2 bitince, yalnız CSV/JSON ile çalışan KONTROLLÜ pilot** olarak CLI ile açılabilir; F3 bitene kadar global Google Ads yolları dış kiracılara 403. Her fazda `pg_dump` + migration `downgrade()`; Celery imzası değişen fazda worker restart. "F2 yarıya iner" türü süre iddiası YOK; tahmin dev-team sprint planında.

---

## 8. Riskler

| Risk | Önlem |
|---|---|
| Kapıdan kaçan uç | §3.5 parametrik test + allowlist; yeni rota = istek fabrikası |
| Kanca dışında kalan sağlayıcı çağrısı | §4 envanteri F0 sonrası yeniden; test: kanca olmadan sağlayıcı çağrısı yapan kod yolu (mock ile) yakalanır |
| Mevcut kullanıcı kilitlenmesi | F1 backfill + çıkış kapısında "mevcut kullanıcı giriş yapıyor" kontrolü |
| Çoklu üyelikte yanlış kiracı | Pilotta tek üyelik; çoklu için zorunlu header, yoksa 400 |
| Worker ölümü → `in_flight` yetim rezervasyon | Ay içinde ayrılmış sayılır (güvenli taraf); mutabakat CLI |
| Google doğrulama gecikmesi | F0'da başvuru; CSV akışı bağlantısız çalışır |
| Google hız sınırı | Mevcut retry/hız kontrolü korunur; kota ölçülür |
| Redis düşerse giriş 503 (fail-closed) | Prod Redis kalıcılığı + izleme; kabul |
| Şifreleme anahtarı kaybı | Yeniden bağlama; anahtar yedeği env yönetiminde |
| Log kaybı | Dosya sink'i rotasyonlu + kalıcı volume; F0'da kontrol |
| Frontend/backend uyumsuz dağıtım | F1 ve frontend aynı dağıtımda; `auth/me` kiracı+rol döner |

---

## 9. Onay bekleyen kararlar

| # | Karar | Öneri |
|---|---|---|
| K1 | Tek geliştirme tabanı. Yön commit kümesinden: `lean-taslak`'ta 7, `auth-lean-merge`'de 11 özel commit; sonuç 18'ini de içermeli, sunucu HEAD'i (13c636d) ff ile ulaşmalı | `auth-lean-merge` → `lean-taslak`, F0'da |
| K2 | Pilotta tüm kiracı üyeleri tüm workspace'leri görür | Evet |
| K3 | Viewer export indirebilir mi | Evet |
| K4 | Yayımlama yetkisi | editor+ |
| K5 | Link süresi / hesapsız link | 30 gün / evet |
| K6 | Çoklu üyelikte aktif kiracı; admin istisnası | Zorunlu `X-Tenant-Id`; admin her aktif kiracı, loglu |
| K7 | Tavan birimi | Aylık USD (Google Ads kotası ölçülür, tavan yok) |
| K8 | Google Ads hedef erişim seviyesi | Mevcut seviye + tahmin; Basic hedef |
| K9 | `platform_admin` kimler; mevcut kullanıcıların rolü | 1–2 kişi admin+owner; diğerleri Digitus `editor` |
| K10 | nginx `X-API-Key` satırı | Kalsın |
| K11 | Yapısal log kapsamı | §3.7 listesi; tablo yok |

---

## Ek A — Codex 1. tur (rev.1'e) hükümleri

| Codex maddesi | Hüküm | Yansıması |
|---|---|---|
| Bu kopyada giriş kodu görünmüyor | DOĞRU; kod `../Digitus-Engine-lean` `deploy/auth-lean-merge` | §0; F0 tek taban |
| Müşteri = tek workspace zorunluluğu koyma | DOĞRU | §0; yayım workspace başına |
| `verify_workspace`'e tek satır yetmez; bağlam + tüm yollar | KISMEN DOĞRU: tek kapı doğru, bağlam + envanter + test şart | §3.1–3.5 |
| Roller ve tavan sona bırakılmasın; eşzamanlı harcama | DOĞRU | F1–F2 öne; §4 |
| Her Digitus çalışanı admin olmasın; impersonation gereksiz | DOĞRU | §1 |
| OAuth bağlantısı kiracıya ait, çoklu, MCC ağacı, yeniden bağlama | DOĞRU | §5 |
| "Standard erişim şart" fazla kesin | DOĞRU | §5, K8 |
| Bağlantı ürüne giriş şartı olmasın | DOĞRU | §5 |
| Seçili teslimat yayımlama; "sıfır arayüz" değil | DOĞRU | §6 |
| Hesapsız link sınırı | DOĞRU, kabul | §6, K5 |
| Sıra ve Redis oturumu | KABUL | §7, §2 |

## Ek B — Codex 2. tur (rev.1 üzerine) hükümleri

| Codex maddesi | Hüküm | Yansıması |
|---|---|---|
| ContextVar tarifi güvenliği garanti etmiyor; senkron dependency'de set edilen değer taşınmaz | DOĞRU; `require_login` senkron (login.py:202) | §3.1 async yield dependency, fail-closed, prod guard |
| "Celery izolasyon sorunu yok" yanlış | DOĞRU | §3.3 (a)–(d) |
| Google Ads önbelleği yalnız customer_id | DOĞRU (google_ads.py:272) | §5 madde 6 |
| Rota testi "404 geldi" ile kalmasın | DOĞRU | §3.5 |
| Tahmin ≠ sert tavan; retry/yarım iş; 409 koşullu | DOĞRU; ilk pipeline bütçesiz (`ai_budget.py`) | §4 |
| Viewer yalnız GET değil; GET workspace yazıyor | DOĞRU (`fail_if_stuck`) | §3.4 |
| Pilot yalnız CSV/JSON; "platform_admin'e kapatılır" belirsiz | DOĞRU | §3.3, §7 |
| K1 yönü commit farkından | DOĞRU (7/11) | K1 |

## Ek C — Codex 3. tur (rev.2 üzerine) hükümleri

| Codex maddesi | Hüküm | Yansıması |
|---|---|---|
| Çağrı öncesi bakiye kontrolü sert tavan sağlamaz; atomik ayırma; çift sayım yok; janitor canlı worker'ı bırakmasın | DOĞRU | §4 (rev.3 zarf; rev.4'te sadeleşti, Ek D) |
| Global Google Ads'i yalnız `/google-ads/*` kullanıyor iddiası yanlış | DOĞRU; `brand_profile.py` ~2013–2023 | §3.3, F1, F3 |
| K1–K11 ürün kararı | KABUL | başlık |

## Ek D — Claude öz-incelemesi (rev.3) + Codex 4. tur hükümleri

| Madde | Codex hükmü | rev.4 |
|---|---|---|
| AI çağrıları tek geçitten geçmiyor (5 nokta/4 sınıf) | HAKLI; kesin sayı birleşik kaynakta | §4 kanca noktaları; F0 yeniden sayım; §8 mock testi |
| Mevcut kullanıcıların üyeliği yok | HAKLI; herkese admin verilmesin, rol açıkça | §1, F1 backfill: `editor`, K9 admin+owner |
| Giriş kapalıyken oluşturma yolu tanımsız | HAKLI; prod'da kapalı | §3.1 dev sabit bağlamı + prod guard |
| Admin kiracı seçimi çelişkili | HAKLI; kendi kimliğiyle loglu | §1, §3.1, K6 |
| Frontend F1'de eksik | HAKLI; gizleme UX, yetki backend | F1 frontend satırı; §1 |
| OAuth `state` eksik | HAKLI; rastgele, süreli, tek kullanımlık, oturuma bağlı | §5 madde 1 |
| Parola unutma akışı yok | HAKLI; oturum iptali + ilk giriş değişimi | §0 kabul edilen sınırlar; CLI `reset_password` |
| Zarf kaldırılıp aylık satıra doğrudan per-call rezervasyon | DESTEKLENİR | §4 |
| "Süresi dolan rezervasyonu serbest bırak" güvenli | **YANLIŞ** (gönderilmiş çağrı ücret doğurur; timeout kanıt değil) | §4 `reserved`/`in_flight` ayrımı; janitor yalnız `reserved`; belirsiz yanıt `unknown` = tavan harcanmış |
| Başlangıç rezervasyonu yoksa görev yarıda durabilir | DOĞRU, pilot için kabul | §0, §4 ön-kontrol bilgi amaçlı |
| Davet yerine geçici parola; mevcut e-posta ele geçirilemesin | KABUL + şart | F5 kuralı |
| `audit_log` yerine yapısal log; kalıcı, kısıtlı, aranabilir | KABUL + şart | §3.7, F0 sink kontrolü |
| Google Ads günlük kota ertelensin; hız sınırı KALSIN | KABUL + şart | §4 retry/hız korunur |
| `slug` kaldır; `TaskResult.tenant_id` yerine argüman + worker karşılaştırması; Digitus bağlantısı DB'ye | KABUL | §2, §3.3, §5 madde 4 |
| "F2 yarıya iner" kanıtsız | DOĞRU | iddia kaldırıldı |

## Ek E — Codex 5. tur (rev.4 üzerine) hükümleri

| Codex maddesi | Hüküm | Yansıması |
|---|---|---|
| rev.4 uygulama tabanı; `reserved`/`in_flight`/`unknown` ayrımı doğru; zarfı kaldırmak makul | ONAY | — |
| Aylık sayaç ile rezervasyon kaydı aynı transaction'da | DOĞRU | §4 uygulama kuralı 1; test (viii) |
| Kapatma tekrara dayanıklı; retry aynı rezervasyonu iki kez harcamasın | DOĞRU | §4 kuralı 2 (`WHERE state='in_flight'`, retry = yeni rezervasyon); test (vi) |
| Rezervasyon ayını taşısın; ay sınırını aşan çağrı ayrıldığı aydan kapansın | DOĞRU | §2 `ym` kolonu; §4 kuralı 3; test (vii) |
| Mutabakat ay içinde de koşabilir; worker ölümünde tutulan bütçe kabul edilen bedel | KABUL | §4 |
| "Teknik itiraz kalmadı" → "plan düzeyinde durduran itiraz kalmadı"; güvenlik uygulama testleriyle | KABUL | başlık notu |
