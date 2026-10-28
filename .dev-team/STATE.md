# Current Status

Last updated: 2026-10-28
Branch: `deploy/lean-taslak`
Active phase: **Marka alt sayfa + SPA crawler (plan_marka_alt_sayfa.md rev.2.1) — FAZ 1 commit 7fd5dec; FAZ 2 KOD TAMAM (28.10) commit onayi bekliyor; Faz 3 sonra. Onceki: ADS 15 KW standardi push'lu, sunucuya dagitildi (20.10)**
Kaynak plan: main repo kokunde `plan_marka_alt_sayfa.md` rev.2.1 (Codex 3 inceleme).
Onceki aktif is (beklemede): Gemini→Claude karsilastirma Faz 1 degerlendirmesi PATRONDA (asagida).

## Just completed (28.10, FAZ 2 — dev-team Backend Dev + Frontend Dev Sonnet paralel; Architect/Konsey KOSMADI, karar planda yazili)

- BrandProfile.crawl_scope (String(10) NOT NULL server_default 'site') + idempotent migration 20261028_001 (down 20261017_001). Create: verilmezse URL yolu kok -> site, degilse page.
- 4 arka plan gorevi scope'u KILITLI SATIRDAN okur; resolve_site_content crawl_scope ZORUNLU kw-only (varsayilan yok) -> bozuk cache yeniden crawl'i ayni kapsamla. CrawlContentUnusable mesaji DEBUG kapaliyken de kullaniciya gider.
- FE: alt sayfa diyalogu (Markayi ana sayfayla olustur / Yalniz bu alt sayfa ile devam / Vazgec); crawl_scope her zaman acik gonderilir.
- Testler: tam paket 5199 gecti / 2 bilinen basarisizlik (test_hybrid_run_db KeyError) / 112 atlandi; FE lint+format+build temiz, 282/282. Canli/tarayici kontrolu YOK.
- Acik: legacy keyword-once akista page prompt kurali yok; dagitimda sunucu 20261020_001 merge'u icin ek merge + worker restart.

## Just completed (28.10, plan_marka_alt_sayfa.md rev.2.1 FAZ 1 — dev-team Backend Dev Sonnet+high; Architect/Konsey KOSMADI, plan Codex onayli)

- url_guard: `safe_fetch` (HTML/XML/text, FetchBudget 12 istek/45 sn redirect dahil, timeout kalan sureyle + govde okurken sure kontrolu), HttpStatusError/TlsVerificationError/FetchBudgetExceeded; `safe_fetch_html` sozlesmesi aynen.
- crawler: tum indirmeler safe_fetch (verify=True), scope site|page, meta+JSON-LD+h1-h3 + satir tekrari ayiklama, sitemap origin+robots+sitemapindex 1 seviye, www esdegerligi, kapi kaynak metinde, Turkce `kod: mesaj` hatalari, ic linklerde sondaki `/` korunur.
- profile_extractor: prepared_source_text (4 kapi noktasi), crawl_scope parametresi, missing_service_info. Prompt: yalniz page kapsaminda ek kural (asagida). Faz 2-3 YOK.
- Testler: hedefli 10 dosya 205 passed; TAM PAKET (izole, alembic upgrade head dahil) 5173 passed / 2 failed / 112 skipped — 2 kirmizi onceden bilinen test_hybrid_run_db KeyError 'deepseek'/'anthropic' (degisen modulleri import etmiyor).
- Canli AI'siz: imecemobil alt sayfa 0 sayfa -> site 3 sayfa / page 388 krk; 6 calisan sitede gerileme yok.
- Codex 3. tur sonrasi (28.10): PAGE_SCOPE_RULES yalniz page'de prompt SONUNA (site prompt'u bayt-ayni, testli); build_profile_prompt + missing_service_info hem extract_profile hem extract_profile_from_site_content (cache yolu CrawlContentUnusable); LOW_PRIORITY_SEGMENTS (kurumsal/yasal sayfalar -10, noksel urun sayfalarini kaciriyordu); butce YUMUSAK sinir olarak belgelendi (45+8 sn+DNS). Hedefli 205 passed. Canli 4 Gemini cagrisi (n=1): imecemobil dogrudan+cache yalniz 'Toprak Analizi Hizmeti'; yalniz kunye sayfasi iki yolda da profile_missing_service. Noksel: spiral/boyuna kaynakli boru + celik profil sayfalari secildi. resolve_site_content scope'u Faz 2'de baglanacak.

## Onceki:  (17.10, dev-team 1 Backend Dev gorevi Sonnet+high; Architect/Konsey KOSMADI — plan Codex onayli, bilinen kalip)

- ADS gruplama: havuz kirpmasi (`max_groups*5`) silindi, `max_groups` yalniz grup sayisini sinirlar;
  `G = max(1, n//15)`, dengeli hedef boyutlar; AI'ya tam G grup + boyutlar; deterministik `_repair_groups`
  (fazla/eksik grup, unutulan/tekrarlanan/bilinmeyen id) her zaman tam G grup x tam boyut, her KW bir kez;
  tasinan/yerlestirilen KW `KeywordGroupSchema.fill_keyword_ids` (yalniz bellekte; DB Faz 4);
  "Diger Kelimeler" kaldirildi; fallback ayni boyutlar; Turkce-dogru baslik (`_turkish_title`, İstanbul).
- Testler (docker-compose.test.yml, hedefli): yeni `tests/unit/test_ads_keyword_grouping.py` (18) +
  mevcut ADS testleri = 84 passed; grouper'a dokunan test_usage_telemetry + test_ai_parse_hardening = 51 passed.
  TAM PAKET KOSMADI (commit oncesi Faz 5'te).
- dev-team `references/model-selection-guide.md` 5.5 nesline guncellendi (Haiku 5.5 1M + $0.10/$0.50;
  oranlar 100x/40x/20x/1x).
- Faz 2 (17.10): `validators.keyword_in_text` + `keyword_coverage` (site_analyzer normalize_turkish,
  kelime sinirli ardisik ifade, DKI varsayilani `DKIValidator.VALID_PATTERN` ile; ek toleransi YOK);
  `tests/unit/test_ads_keyword_coverage.py` (13). Hedefli ADS paketi 97 passed. Henuz hicbir yere bagli degil (Faz 3).
- Kullanici duzeltmeleri (17.10): 15 -> `app/core/constants.py` `ADS_KEYWORDS_PER_GROUP`; G==1'de AI cagrisi
  yok ONAYLANDI; bolunen grubun ikinci yarisi da `fill_keyword_ids`'e yazilir (test eklendi). Hedefli 149 passed.
- COMMIT AYRIMI (kullanici, Faz 5'te): model rehberi (`.claude/skills/dev-team/references/model-selection-guide.md`)
  ADS degisikliklerinden AYRI commit. Commit + push yalniz Faz 5'te.
- Faz 3 (17.10, Backend Dev Sonnet+high): `rsa_generator` aday havuzu (18-20 baslik / 5-6 aciklama), [:15]/[:4] kesmeleri
  kalkti, grounding SONRASI dedup + KW kontrolu, TEK tamamlama (strict retry'in yerine; yalniz sayi eksikse), acgozlu
  kapsam secimi, sert minimum fallback degismedi, eksik `missing_headlines`/`missing_kw_descriptions` (exclude=True) +
  task result_data `standard_shortfalls`. MUHURLU `ADS_RSA_GENERATION_PROMPT` (benchmark/idiomatic_prompts manifest SHA)
  DOKUNULMADI; uretim turetilmis `ADS_RSA_CANDIDATES_PROMPT` kullanir (import aninda 3 tam-metin degisim, kayarsa RuntimeError).
  ADS ile ilgili unit+integration (-k) 564 passed / 32 skipped / 0 failed. Degisen eski testler: test_ads_negative_conflict
  (strict retry prompt testi -> aday prompt), test_claim_grounding CannedAI (2. cagri bos RSA).
- Maliyet notu: tamamlama artik SIK (temiz baslik<15 veya KW'li aciklama<4); baslik AI yeniden yazimi aday basina sinirsiz
  (eskisi gibi); task time_limit=900 — Faz 5 canli denemede usage + sure olc. '%30' yalniz primary cikti TAHMINI.
- Faz 4 (17.10, Backend+Frontend paralel, Sonnet): `AdGroup.fill_keyword_ids` (JSON nullable) + idempotent migration
  `20261017_001` (down 20261011_001); ilk kayit/klon/regenerate/_model_to_schema/export'ta tasinir; okuma-ani
  `validators.build_standard_report` -> `AdGroupDBSchema.standard_report`; export 4 formatta yalniz olgu+liste
  (`app/exporters/ads_standard_format.py`); AdsPanel ozet + standart-alti rozeti + 4 liste. Fallback duzeltmesi `_top_up`:
  temiz adaylar korunur, eksik tip yalniz sert minimuma kadar tamamlanir (hic temiz yoksa fallback 3/2 verir, eskiden 5 baslik).
  Testler: ADS/export/migration -k 685 passed / 32 skipped / 0 failed (test DB alembic 20261017_001 uygulandi);
  frontend lint+format+build OK, vitest 264 passed. TAM PAKET KOSMADI.
- 17.10 dogrulama: TAM PAKET 5038 passed / 112 skipped / 2 failed — ikisi de test_hybrid_run_db (budget_summary'de
  'deepseek'/'anthropic' anahtari yok); temiz HEAD worktree'sinde AYNI iki test AYNI KeyError ile dusuyor -> ONCEDEN VAR
  (Faz 1 karsilastirma isi c1f5a53 civari), ADS'den bagimsiz. Bizim etkimiz: test_aday'daki 'pools[ADS] > 3 -> ads_grouping'
  kosulu `>= 2*ADS_KEYWORDS_PER_GROUP` yapildi.
- Tarayici (Playwright, gecici vite :5180, API_KEY container env'den): ws29 /ads 13 grubun 13'unde rapor blogu dogru;
  mobil 390px yatay kaydirma ONCEDEN VAR (eski kodla da). Docker frontend :3000 HMR tuzagi: eski AdsPanel'i sunuyor ->
  `docker-compose restart frontend` gerekir (yapilmadi). YEREL DB'ye (kullanici onayi) alembic 20261017_001 UYGULANDI;
  celery_worker YENIDEN BASLATILMADI (eski kod) -> yerel canli denemeden once restart sart.
- Model rehberi resmi sayfalarla dogrulandi (models/overview + pricing, 17.10); harness alias cozumu dogrulanmadi. AYRI commit.
- CANLI DENEME 17.10 (yerel, Gemini 3.8-flash; once task_results/ad_sets/export bos + celery inspect bos, sonra yalniz
  celery_worker restart): run 83, 60 ADS KW -> 4 grup x 15 KW, her grup 15 baslik + 4 KW'li aciklama, standard_shortfalls=[],
  fill=[] (AI tam boyut verdi), tamamlama cagrisi GEREKMEDI. AI aday: 20/20/19/19 baslik, 0 elenen. KW baslikta 11/14/13/12
  (50/60), yalniz aciklamada 5, hic 5 (biri 'en iyi seo ajansi' -> iddia kurali; 'seo ajansi istanbul' ~ 'Istanbul SEO Ajansi'
  K7 siralama katiligi). Sure 51 sn (eski Gemini 12-13 grup: 113/139 sn). Usage 6 istek (grouping 1, rsa 4, micro_shorten 1),
  11.570 in / 6.716 out, ~0,034 $. RSA cagri basina in +11%, out +2% (run 78 Gemini ortalamasina gore; '%30' tahmini ASILDI
  degil, altinda); RSA toplam token ~%67 az (13 yerine 4 cagri). Aktif set 26 (v1) DEGISMEDI; yeni set 27 v2 DRAFT.
  Gozlem: basliklarin cogu KW-only (CTA/fayda az) — K6 sonucu; SEO grubu web tasarim KW'leriyle genisletilmis.
- KALITE DUZELTMELERI 17.10 (kullanici onayi 1-4, K6 DEGISMEDI): tum baslik/aciklama position='any' (tek nokta, generate_rsa sonu);
  prompt 'yazildigi gibi' kalkti -> dogal Turkce oncelikli; `_dedup` DKI-acik `validators.kw_tokens` ile; gruplama prompt'lari
  ad/tema TUM KW'yi kapsasin. Odakli: 689 passed + bilinen 2 hybrid KeyError. 2. CANLI: set 28 v3 DRAFT, 63 sn, 7 istek,
  12.484 in / 7.633 out / 765 thought, ~0,041 $; aktif set 26 DEGISMEDI.
  KARSILASTIRMA 27->28: baslik KW kapsami 50/60 -> 30/60 (katı K7; dogal varyantlar sayilmiyor); konum position_1 32 -> 0;
  DKI/duz tekrar 2 -> 0; CTA 0 -> 6, keyword-tipi baslik 45 -> 25; sorgu-kalipli basliklar ('...Fiyat', '...Verme(k)',
  'Ankara Pazarlama') 28'de YOK; ACIKLAMALARDA bozuk KW yerlestirme SURUYOR ('dijital pazarlama ajanslari istanbul
  cozumlerini', 'dijital reklam ajans desteğiyle'); grup adlari 3/4 genisledi, SEO grubu (6 web KW) HALA yalniz SEO adli;
  Dijital grubunda tema-disi marka hizmeti basliklari (Web Tasarim, E-Ticaret). n=1 her set; gruplama ikisinde AYNI.
- PROMPT TUR 3 (17.10, kullanici onayi): turetilmis prompt'ta 'oncelikle kullan' (mutlaka DEGIL), aciklamada 'cumleye dogal
  yerlesen EN AZ BIR KW, digerlerini zorla doldurma' (yasak listesi YOK), 'basliklar BU GRUBUN KW + temasindan; marka bilgisi
  yalniz temayla ilgiliyse'; tamamlama aciklama talimati ayni. Muhurlu prompt dokunulmadi. HEDEFLI DENEME (grup 228+229,
  DB'ye YAZILMADI: collector.flush no-op + rollback; once/sonra sayim ayni): TOPLAM 2 cagri (yalniz ads_rsa; kisaltma/iddia/
  tamamlama 0), 5.026 in / 2.576 out, ~0,013 $. Aciklamalar 8'de 7 dogal; tema kaymasi yok; baslikta KW 8 ve 6.
  YENI BULGU: brand_usp bossa `_generate_raw_rsa` 'Kaliteli urun ve hizmet' varsayilanini prompt'a koyuyor -> metne siziyor
  (2 baslik + 1 aciklama). Onceden var; duzeltme ONAY BEKLIYOR. Odakli 689 passed + bilinen 2 hybrid KeyError.
  Set 28 (draft) grup 226 adi/temasi SQL ile 'SEO ve Web Tasarim Hizmetleri' yapildi; set 26 aktif DOKUNULMADI.
  NOT: set 28 metinleri eski prompt'la; yeni prompt yalniz sonraki uretimlerde.
- USP DUZELTMESI 17.10: bos USP'de jenerik 'Kaliteli urun ve hizmet' kaldirildi (api generation.py iki uc -> trusted or
  profil USP or ''; rsa_generator prompt '—'; fallback aciklama on eki yalniz USP varsa). Testler: unit 2 + integration 1;
  odakli 701 passed + bilinen 2 hybrid. Benchmark hybrid_run.py DOKUNULMADI.
- 3. TAM URETIM (son kod): set 29 v4 DRAFT, 50 sn, 5 istek (grouping 1 + rsa 4), 12.451 in / 6.635 out, ~0,034 $;
  eksik yok; baslikta KW 28/60; konum hep any; DKI/duz tekrar 0; 'Kaliteli' 0. Aktif 26 + draft 27/28 metinleri DEGISMEDI.
  BULGU: gercek akista display USP = profil urunleri ('Web Tasarim, Web Yazilim, E-Ticaret Sistemleri -- Dijital Pazarlama
  ve Yazilim Ajansi') -> her grup prompt'una giriyor -> tema kaymasinin buyuk olasi kaynagi; hedefli deneme USP='—' ile
  kosulmustu (temsili DEGILDI). SEO grup adi 3 koşuda da dar kaldi. `{KeyWord:Sosyal Medya }` (sonda bosluk) DKI kusuru.
- DKI BOSLUK DUZELTMESI 17.10: kaynak HeadlineValidator._apply_shortening_rules ('Hizmetleri' -> '' kurali
  '{KeyWord:Sosyal Medya }' birakiyordu); cift bosluk + '}' onu bosluk temizlenir; test_dki_validator +2 test.
- SON KONTROLLER: tam paket 5052 passed / 112 skipped / 2 failed (bilinen hybrid KeyError, HEAD'de de);
  frontend lint+format+build OK, vitest 264. git diff --check temiz, CRLF yok.
- COMMIT HAZIR (YAPILMADI): 36 dosya (28 M + 8 yeni), mesaj scratchpad ads_commit_msg.txt; HARIC: model rehberi (ayri),
  benchmark/faz1/export xlsx, ponytail_audit (bizim degil). Commit / push / dagitim AYRI onaylarla.
- DEPLOY (kullanici: dagitim asamasinda): mevcut DB'ye `alembic upgrade head` + celery_worker restart (once running task kontrolu).
- Worker restart deploy'da sart.

## Just completed (11.10, dev-team 6 Backend Dev gorevi, Architect/Konsey KOSMADI — mimari planda kayitli)

- **Dalga 1 `4ede981`:** `AnthropicService` (anthropic==1.13.0/httpx2, max_retries=0, adaptive thinking +
  effort tablosu, allowance-dahil max_tokens, structured output `output_config.format`, refusal/429/5xx/4xx
  eslemesi, Gemini-sekilli usage namespace: prompt_token_count = input + cache_read + cache_creation);
  `to_anthropic_response_schema` (additionalProperties:false, optional alanlar OLDUGU GIBI);
  `RoutedAIService` "anthropic" slotu + mock; ledger `reserve(provider=)`, cache-farkinda `_usage_totals`
  (cache write > 0 -> TAVAN, shortcut), `_anthropic` backend taramasi; PRICE_TABLE claude-haiku/sonnet/opus-5-5
  (resmi sayfa 11.10); `AiUsageEvent` provider/cache/queue_wait_ms/http_attempts + idempotent migration
  `20261011_001`; screening provider CHECK genisledi. 228 ilgili test yesil.
- **Dalga 2 `1422fd4`:** `engine/routing.resolve_stage_route`; orchestrator rotayi MUHURDEN ONCE cozer,
  runner'lar `stage_route(run, stage)` ile MUHURDEN okur (kosu ortasinda route degisikligi etkisiz);
  `StageContext.provider`; manifest schema 3 (providers + adapter kimlik alanlari) yalniz gemini-disi route
  varken, aksi halde schema 2 BAYT-AYNI (golden test); eski manifest gemini varsayimiyla resume; "refusal"
  fatal mark; benchmark `CharacterizationBudget` provider-farkinda (anthropic zarfi, allowance cift sayilmaz,
  provider->butce eslemesi, cagri-basi fiyat); `profile_inputs` (crawl dondurma); `claude_spike`
  (≤30 sentetik cagri, kuru kosu tavani 0,33 $, muhurlu JSON).
- **Tam paket: 4690 passed / 112 skipped / 0 failed** (temel 4527; +163 yeni), docker-compose.test.yml,
  override ile `digitus_lean_test_*` konteyner adlari (eski `dg-auth-merge` test_db ile ad cakismasi).
- Bulgu: ledger eszamanlilik testi ilk yazimda yanlis tasarlanmisti (thread icinde settle taahhudu
  dusuruyordu) -> iki fazli yapildi; ledger kilidi dogru calisiyor (5 ok / 3 BudgetExceeded).
- Bulgu (T4): gemini/deepseek route'lari artik motor asamasinda ETKILI (eskiden runner sabiti eziyordu);
  route bos -> bayt-ayni. Uretim .env'de AI_STAGE_ROUTES yok.

- **Codex Faz 0 kod incelemesi (`inceleme_claude_gemini_faz0_kod_codex.md`) -> 3 P1 DOGRU, duzeltildi (4. commit):**
  (1) `_scoped` provider'i gemini'de de ACIK gecer (eski hali global route'u yeniden okutuyordu ->
  muhurlu (gemini, model) -> (anthropic, gemini-model) riski); (2) `routes_for_seal`: muhurlu run'da
  rota MUHURDEN, resume ayar degisiminden etkilenmez; (3) `to_anthropic_response_schema` v2:
  maxItems / minItems∉{0,1} / min-maxLength / sayisal sinirlar / bilinmeyen format DUSER,
  description'a tasinir (SOCIAL hashtags 5-20 regresyon testi); spike plani gercek SOCIAL semasiyla
  2 cagri ekledi (kuru kosu 0,32 $). Tam paket **4696 / 112 skipped / 0**.

- **SPIKE KOSULDU (11.10 12:52 UTC, `benchmark/claude_spike.json`, muhurlu):** 29 cagri (20 Haiku, 8 Sonnet,
  1 header probu), settled **0,0837 $** (tavan 0,50, kuru tavan 0,32 -> rezervasyon zarflari gercegin ~3,8 kati,
  §9 yeniden muhurde daraltilabilir). **14/14 sema KABUL** (optional alan eksik birakilabiliyor, nullable calisiyor,
  gercek SOCIAL semasi iki modelde de kabul) -> `[T,"null"]` yedegi GEREKMEZ. max_tokens kirpilmasi 0.
  Dusunme payi kucuk: low p99 489 tok (n=25), medium p99 288 (n=3) -> allowance 2000/4000 cok genis, Codex: n kucuk (25/3), gercek benchmark
  verisi gelene kadar 2000/4000 KORUNUR; settled 0,0837 $ yerel fiyat tablosuyla hesap (probe haric), konsol karsilastirmasi yapilmadan 'faturayla dogrulandi' denmez; Faz 1 butcesi gercek asama girdi/cikti/cagri sayisiyla hesaplanir, spike yalniz kalibre eder. Tokenizer ~0,71 tok/bayt (Turkce) ->
  bayt tabanli girdi tavani ~%40 muhafazakar. Gecikme p50 3,8 s / p90 12 s (uzun ciktilar 14-24 s).
  Rate limit (tier): 1000 RPM, 2,4M TPM, 2M ITPM, 400K OTPM -> Faz 1 icin bol. anthropic 1.13.0, git a696d50.
  Konsol faturasi ile 0,08 $ karsilastirmasi KULLANICIDA (fiyat tablosu dogrulamasi).

- **Dalga 3 (Faz 0 madde 6/8/9/10 + Gemini retry):** arsiv benchmark modulleri lean'e tasindi
  (bakeoff_comparison/gates, patron_compare/dataset, annotation_io/excel + 5 test, 132 yesil; routing_harness'tan
  2 saf fonksiyon teste inline) + fixture'lar (reference_characterization_gemini-3.5-flash.json, plan, annotation_sheet*);
  `GeminiService(retry_attempts=None)`: google-genai SDK varsayilani 5 deneme (408/429/5xx) -> benchmark 1 gecer,
  uretim aynen (test); `claude_bakeoff_plan.py` -> `benchmark/claude_bakeoff_plan.json` MUHURLU (K3/K7/K8/K12/K16/K18/K19,
  §7.5, §8 kapilar, §9 butce 23/50, §3.2 N_max, adapter kimlikleri, spike sha; `faz1_blockers()` =
  [K12_document_reference, spike_console_reconciled, workspace_ids]); Faz 2 idiomatik prompt'lar
  `benchmark/idiomatic_prompts/{seo_generation,ads_rsa,social_brief_contents}.md` + manifest.json muhurlu
  (K16 tek atis; kaynak sablon sha'lari baglandi; is kurallari birebir, yalniz XML yapi). Tam paket **4855 / 112 / 0**.

- **Faz 1 altyapisi (dalga A+B, 11.10, Codex Faz 0 hukmu: altyapi GO / ucretli kosu HENUZ DEGIL):**
  A1 `stage_capture` (ai_runner CAPTURE_SINK hook'u, kapali iken bayt-ayni) + `stage_replay` (ayni prompt/sema/retry
  butcesiyle baska saglayiciya replay, `compare_stage_outputs` asama karari uyumu); A2 `hybrid_run` (G/G/G, Aday, G/G/C
  konfiglari planin K3 modellerinden; `StageRoutePinnedAI` icerik asamalarina provider enjeksiyonu; `GeminiService(
  retry_attempts=1)` gercekten kullaniliyor; provider basina tavan Claude 50 / Gemini 20 / DeepSeek 5; `faz1_blockers`
  ILK satirda, dry-run bile kilitli) + `BudgetGuardedAI` settle'i paralellikte `logical_request` ile dogru; A3
  `claude_gates` (G1-G7 esikleri MUHURLU plandan regex ile, eksik sayi -> hata; kumeli bootstrap, Wilson, kappa, pilot
  etiketi, rapor) + `patron_labels` (optimice/gr7/dijital v2 etiketleri bayt-ayni, muhurlu index; gr7 SEO 72/14);
  B1 `content_pairs` (ortak katman, kor A/B kartlari, gizli esleme ayri dosya, sert kurallar gercek validator'larla) +
  `pairwise_judge` (DeepSeek V4 Pro, pozisyon takasi, S formulu K4, berabere bandi 0,05, `pairwise_judge` asamasi
  AI_STAGES'e eklendi); B2 `profile_compare` (donmus girdi, 3 saglayici, bloklu-rastgele sira, kor kartlar, G3 girdisi);
  B3 QA: `tests/integration/test_hybrid_run_db.py` 9 DB'li uctan uca test (Aday/G/G/G/G/G/C, kilit, enjekte hatalar,
  tavan) ILK KOSUDA YESIL; QA bulgusu: `claim_rewrite`/`micro_shorten` ic ice scoped() guard'i atliyordu -> `_GuardedScoped.for_stage`
  ebeveynden gecer (KAPANDI). Dalga A tam paket 4906/0; dalga B hedefli 95+23 yesil; **dalga B sonrasi TAM PAKET
  bellek yetersizligi nedeniyle sistem tarafindan DURDURULDU, yeniden kosulmali** (kullanici izniyle).
- Bilinen bosluklar: `content_pairs.extract_items` SQL'i gercek DB'de denenmedi; sosyal ciftler pozisyonel eslesir;
  Optimice "ADS 16" kaynagi belirsiz (artefakt 123); hybrid_run API'nin `require_run_allowed`/deneme-ws kapisini
  atlar (ws38/39'a yoneltme); `AI_STAGE_ROUTES` global -> ayni process'te es zamanli iki run_hybrid yasak.

- **Codex Faz 1 altyapi incelemesi (`inceleme_claude_gemini_faz1_altyapi_codex.md`) -> 3 P1 + 1 test DOGRU, duzeltildi:**
  (1) `faz1_ledger.Faz1Ledger` (benchmark/faz1_budget_ledger.json, muhurlu): K7 tavanlari DENEY TOPLAMI; hybrid_run ve
  profile_compare tavani defterden KALAN alir, bitiste taahhudu (settle edilmemis tavan yakimi dahil) isler; tukenmis
  tavan -> AI/DB kurulmadan hata; (2) replay hedefli tekrari KAPATIR (`run_batch(targeted_retries=0, allow_missing=True)`,
  uretim varsayilani degismez): alt kume prompt'u uretimle birebir kurulamaz -> kiyas iki tarafta ILK DENEME ile simetrik,
  `missing_ids` kaydedilir; `has_response_parser` yakalanir, parser verilmeden replay BASLAMAZ; (3) `assert_workspace_allowed`
  (1A/1B/profile kapsami, ad->id eslemesinden): run_hybrid ve profile_compare hedef ws'i DB/AI oncesi dogrular; (4) ic ice
  scoped() regresyon testi xfail -> gercek assert. Hedefli 37+85 yesil; DB e2e 9 yesil. TAM PAKET henuz yeniden kosulmadi
  (bellek kesintisi; kullanici izniyle).

- **Codex Faz 1 2. tur -> 2 bulgu DOGRU, duzeltildi:** (1) defter IKI FAZLI: `reserve()` cagri ONCESI kalan tavani
  dosyaya KALICI yazar (cokmede acik kalir, sonraki komutlar harcanmis sayar, `reconcile()` ile insan kapatir),
  `settle()` kapanista (finally) taahhudu isler — hybrid_run ve profile_compare buna bagli; (2) replay
  `max_attempts=1`: parse/saglayici hatasinda da TEK deneme (uretim MAX_ATTEMPTS=2 degismez) -> iki taraf ILK CEVAP
  ile simetrik. Hedefli 140 yesil (DB e2e 9 dahil). TAM PAKET henuz yeniden kosulmadi.

- **B4 QA:** `tests/integration/test_content_pairs_db.py` — gercek DB'de G/G/G + Aday kosusu sonrasi `extract_items`
  (rsa/blog/social), `common_layer`, kor kartlar round-trip ve false-green (dayanaksiz iddia -> hard_rule_ok False) YESIL.
  Not: `ad_headlines.headline_text` varchar(30) — 31 karakter DB'de zaten yazilamaz.

- **FAZ 1 KOSULARI (11-12.10, patron karari: Hissefy+Digitus, degerlendirme patronda):** plan `amend_faz1_plan.py` ile
  yeniden muhurlendi (K12 sozlu, kredi 99,92, ws 21/29, kapasite 60/30/30). 6 hibrit kosu + profil kiyasi basarili:
  run 76/79/82 (ws21 GGG/Aday/GGC), 78/81/83 (ws29). Harcama (liste): Claude 19,18 $, Gemini 10,21 $. Basarisiz: run 77
  (ws29 GGG, kosu tavani hatasi — tavan mantigi duzeltildi), run 80 (ws29 Aday: Haiku family_a2'de uydurma ID 11011,
  2 denemede de -> fail-closed; tekrar 81 OK). Paket: benchmark/faz1/export/{README,ozet,ws*_havuzlar.xlsx,
  ws*_icerikler.md/xlsx,profiller.md}. Kod: faz1_export (yeni), faz1_ledger kilit+reload+total_caps, hybrid_run
  --caps + yalniz kullanilan saglayici rezervasyonu, ai_usage_events migration canli yerel DB'ye uygulandi.
  Kosu mekanigi: test imaji konteyneri lean aginda, --env-file lean/.env + ayri anthropic.env (anahtar ana agac
  .env'de), DATABASE_URL digitus_app'ten; ~30-40 dk/kosu (SEO blog dominant); ikiser paralel.
  Gozlemler README'de (Haiku ID uydurma, Claude retry 3-7 vs Gemini 0-2, sosyal kaydedilen icerik Claude'da az,
  Gemini provider kolonu NULL).

## Open / next

0b. Takip: GeminiService collector.record(provider="gemini"); 1A replay / hakem / kapilar istege bagli; konsol mutabakati.
0. **TAM PAKET 11.10 (izole docker-compose.test.yml, iki parca): unit 2566 passed/38 skipped + integration 2394 passed/74 skipped = 4960 / 0 hata.** Faz 1 oncesi kalan: 1A icin referans G/G/G kosusunu
   `StageCapture` ile sarmalayan sunucu adimi (hybrid_run'a `capture=True`); Faz 1 kosu sirasi/CLI betigi.
0b. **Eski not — Faz 1 wiring (kod):** bakeoff_comparison provider/model pinleri gemini-3.5/deepseek -> Claude adayi icin sozlesme
   genisletmesi; 1A istek yakalama/replay surucusu (K14) + 1B hibrit kosu CLI'i lean'de YOK (arsivde scripts/); profil
   kiyasi icin `profile_inputs.freeze` cagrisi; DeepSeek hakem + patron kart uretimi (annotation_io). Idiomatik: SEO
   revision/expansion, CLAIM_REWRITE, social repair prompt'lari Gemini metniyle kalir (karar: Faz 2 oncesi).
1. ~~Spike~~ TAMAM (yukarida). **Madde 8 butce yeniden muhuru**: spike p90 zarflari ile §9 hucreleri; allowance karari — `python -m app.core.benchmark.claude_spike
   --out benchmark/claude_spike.json` (tavan 0,50 $; kuru kosu 29 cagri / 0,33 $). Sonra allowance/effort
   kalibrasyonu, optional-alan kabulu, rate-limit basliklari, tokenizer orani muhurlenir.
2. Faz 0 madde 6 (arsiv benchmark moduller: bakeoff_*/patron_*/annotation_io -> lean), 8 (butce yeniden
   muhuru, spike p90), 9 (`claude_bakeoff_plan.json`), 10 (Faz 2 idiomatik prompt SHA) — spike SONRASI.
3. Gemini tarafi: `thoughts_tokens` kontrolu + genai ic retry hata enjeksiyonu (placeholder "not run").
4. Konsol: net kredi/devir/kapsam/Batch API (manuel). K12 belge referansi Faz 1 oncesi.
5. PUSH YAPILMADI — iki commit yerel. Sunucu feat/auth-login'den calisiyor; push karari kullanicinin.

## Previous status (2026-09-17)

Last updated: 2026-09-17
Branch: `algoritma-kilitleri`
Active phase: **Kilitli algoritmalarin uretime entegrasyonu — Faz 5 KAPANDI; sirada Faz 6 (post-policy + ChannelPool teslimi)**
Commit'ler: Faz 1 `803ab95` · Faz 2 `265bdd2` · Faz 3 `824cc9e` · Faz 4 `88b7d28` · Faz 5 `579bc91`

## Just completed

- **`plan_algoritma_entegrasyonu.md` yazildi ve onaylandi** (`ENTEGRASYON-2026-09-17-v2`,
  + patron duzeltmeleri v3). Kapsam: kilitli ADS (`nihai_niche_v1`), SEO
  (`seo_v31_kati2_v2`) ve SOCIAL (`social_v5_uretim_v1`) algoritmalari `algorithm_version="v3"`
  olarak uretim hattina baglanir. **Entegrasyon planidir, legacy sokumu DEGILDIR.**
  Patron kararlari: policy seciM SONRASI deterministik kapi (algorithm_rank / final_rank
  iki katman, geri doldurma yok, `unfilled_count`), tek kosu + tek Family V2, ucret UI'i yok.
  Codex duzeltmeleri: 2 tablo (engine_run YOK), test sayisi hedefi yok, freshness otoritesi
  `firm_block_sha256`, `auto_assign_channels` akisi, her LLM asamasi TEK GECIS,
  tek tavan anahtari, v3 once opt-in sonra varsayilan.
- **Faz 0 GECTI (G0):** uc kilit `--verify` yesil — 45 test
  (`test_ads_nihai_niche_lock`, `test_seo_v31_kati2_lock` + `_v2`, `test_social_v5_uretim_lock`).
- **Faz 1 uretim kodu yazildi:** `app/core/engine/context.py` (kilitli `firm_block`'un
  uretim kopyasi + kapali beyaz liste + evren A13/A15 + rekabet olcek koruma),
  `app/core/engine/persistence.py` (stage yazma/resume FAIL-CLOSED), `engine_stage_results`
  + `engine_selections` modelleri, idempotent migration `20260918_001`, config anahtarlari
  `ENABLE_ENGINE_V3` + `ENGINE_V3_HARD_CAP_USD`.
  Gate KAPALI degerlendirildi (kararlar planda kayitli) -> Architect ve Konsey KOSMADI.
- **Faz 1 TAMAM (G1 gecti): 93 test yesil** (tek temiz kosu, es zamanli kosu yok):
  `test_engine_context` 30 (firm_block PARITY dahil: uretim kopyasi <-> `scripts/ads_v3_ai.firm_block`
  7 profil varyantinda birebir string) + `test_engine_persistence` 19 + `test_engine_migration` 12
  + `test_engine_v3_acceptance` 17 + `test_v21_phase_e` 15 (regresyon).
- **Faz 1'de cikan ZORUNLU bulgu:** `20260723_001`'in ekledigi `ck_scoring_runs_algorithm_version`
  CHECK kisiti yalniz `('v2','v2_1')` iziniyle v3 run'i INSERT asamasinda engelliyordu
  (uygulama katmanindaki 409 kapilarindan BAGIMSIZ). Mevcut migration'a DOKUNULMADAN yeni
  idempotent migration `20260918_002` ile cozuldu.
- v3 kabul kapilari: `ENGINE_V3_DISABLED`, `ENGINE_V3_NOT_IMPLEMENTED`,
  `ENGINE_V3_SCREENING_NOT_SUPPORTED` (tipli 409) + K16 icin mevcut
  `SOCIAL_AUTHORITY_EXPERIMENTAL` yeniden kullanildi. v3 execute/assign SESSIZCE v2 motoruna
  DUSMUYOR (monkeypatch tuzagiyla test edildi).
- **Codex incelemesi -> 5 sozlesme acigi kapatildi, Faz 1 YENIDEN dogrulandi:**
  (1) evren artik RUN'a ait — `select_run_keywords` + `freeze_universe_snapshot`
  (`KeywordScore.metrics_snapshot`, kanal skorlari NULL) + `load_universe(scoring_run_id)`;
  motor canli `workspace_keywords` OKUMAZ, dondurmadan sonraki degisiklik kosuyu etkilemez.
  (2) `execution_manifest.engine_v3` TAM DEGISMEZ: firma hash + algorithm_versions + models
  + prompt_shas birlikte muhurlenir, ikinci muhur yalniz BIREBIR ayniysa kabul; her stage
  yazimi/okumasi `verify_stage_context` ile GERCEK manifeste karsi dogrulanir.
  (3) `engine_selections` kisiti XOR oldu (`ck_engine_selections_selected_xor_excluded`).
  (4) rekabet: sonlu olmayan / <0 / >1 -> `EngineInputError`.
  (5) `ENGINE_V3_HARD_CAP_USD` pozitif+sonlu validator; v3'te `auto_assign_channels=false`
  -> 409 `ENGINE_V3_SINGLE_RUN_REQUIRED` (tek kosu sozlesmesi).
  QA bir GERCEK kusur buldu ve duzeltildi: `top_n` modunda `.limit()` sonrasi `.order_by()`
  SQLAlchemy'de `InvalidRequestError` uretiyordu (her top_n run'i coker).
- **Codex 2. tur — snapshot butunlugu:** `freeze_universe_snapshot` artik YALNIZ
  `algorithm_version="v3"` kabul ediyor; IKI FAZLI (once bellekte dogrula, sonra tek
  `add_all`+`flush`) — gec satir hatasinda session'da pending `KeywordScore` KALMIYOR
  (alakasiz commit sonrasi bile sifir yazim testli); TEK YAZIM: ikinci freeze ayni evren+
  icerikte idempotent, farkta tipli `SnapshotMismatchError` (workspace degistikten sonra
  eski snapshot DEGISMIYOR — hacim/rekabet degisimi, yeni keyword, pasiflesen keyword
  vakalari testli).
- **Yanlis-yesil avi:** surum kapisi eklenince 4 test yanlis nedenle geciyordu (genis
  `pytest.raises(EngineInputError)` hem surum kapisini hem rekabet dogrulamasini yutuyordu).
  23 iddianin 22'si `match=` ile daraltildi, snapshot vakalari dar sinifa baglandi;
  daraltma sonrasi kirmizi cikmadi.
  **Faz 1 KAPANIS: 152 test yesil** (context 62 + persistence 30 + config 10 +
  migration 13 + v3 kabul 22 + v21 regresyon 15); kilitler yeniden dogrulandi: **45 yesil**;
  `scripts/` ve `algoritma/` bu oturumda DEGISMEDI.
- **Faz 1 COMMIT'LI: `803ab95`** — yalniz v3 kapsami. Uc kirli dosya (`config.py`,
  `scoring.py`, `assignment_dispatcher.py`) HEAD + YALNIZ v3 bloklari olarak yeniden
  kurulup stage edildi; authority SOCIAL deneyi, allowlist validator'i ve screening
  degisiklikleri commit'e GIRMEDI (calisma agacinda duruyor). v3 authority reddi artik
  `authority_social` deney modulune BAGLI DEGIL — 409 dogrudan uretiliyor.
- **FAZ 3 COMMIT'LI: `824cc9e`** — ADS Niche motoru. GOLDEN PARITY gercek uretim
  fonksiyonlariyla BIREBIR: 3 firma x 3 tekrar = 9 hucre, ilk-60 id, tam sira SHA'si,
  Selection 1e-9. Prompt parity de birebir (funnel + Intent v2->v3->v4, iki sema,
  bantlar, apply_band 36 kombinasyon). Temiz worktree: ADS 110 + Faz1/2 220(+3 skip)
  + kilitler 45 yesil.
  Codex duzeltmeleri: (1) `run_ads_stage` basinda `validated_family_map` — anahtarlar
  int'e normalize (JSON string ID kabul), kume donmus evrenle BIREBIR, bos/UNMATCHED
  red; hata vakalarinda AI cagrisi 0 ve stage yazimi 0 (DB ile kanitli).
  (2) funnel prompt kimligi KILITLI `canonical_render_sha256` degerine baglandi — eski
  `build_funnel_prompt({}, [])` hash'i kilitle ilgisizdi ve prompt degisse bile ayni
  kalabilirdi (sahte muhur).
  UCUNCU FIRMA BLOGU tuzagi: intent'in firm_block'u funnel ve aileninkinden de FARKLI.
  Kilitten tek bilincli fark: bos havuzda sessiz donus yerine ACIK hata (onaylandi).
- **FAZ 2 COMMIT'LI: `265bdd2`** — temiz worktree sinavi GECTI (220 passed + 3 skipped;
  skip'ler yalnizca gitignore'daki `benchmark/private/` golden'i, ACIK gerekceyle).
  Codex incelemesi uc tur surdu; UC GERCEK KUSUR uretim kodumda bulundu ve duzeltildi:
  (1) A3 kilitli davranistan UC noktada sapmisti (confidence kayboluyordu, A3 yalniz
  `if unmatched:` icinde kosuyordu, UNMATCHED A3'e gidiyordu, adaylar butun sozluktu);
  (2) `ai_runner._parse` `AIJsonParseError`'i yakalamiyordu -> kirpik cevap tekrar
  sozlesmesinden kaciyordu; (3) `BudgetExceeded` korumasi NO-OP'tu — `_complete`
  icindeki `except BudgetExceeded: raise`, cagiranin genis `except Exception`'i
  tarafindan zaten yakalandigi icin TAVAN ASIMINDA IKINCI CAGRI deneniyordu.
  Ders: koruma, cagiranin except ZINCIRINDE olmali.
- **FAZ 2 (Family V2) ayrintilari:** `app/core/engine/family/`
  (prompts.py birebir port — `frozen_contract`/`RULES_RENDERED`/4 sema kilitli kaynakla AYNI;
  rules.py; runner.py A1->A2->A2B->SOZLUK DONAR->A2C->A3->kapanis).
  A2B sozlesmesi patron karariyla semaya uygun hale getirildi: uc-verdict soyutlamasi
  URETIMDEN KALDIRILDI (kanonik STAGE2B_SCHEMA kelime bazli hukum uretmiyor); destek =
  `examples` degerlerinin A2B'ye gonderilen UNMATCHED metinlerle BIREBIR eslesen GERCEK
  keyword ID'leri; destek >= 2 -> sozluge eklenir; A2B ATAMA URETMEZ. Kaynak atfi
  LOCKED.json + kod haritasi 5b (plan K7 DEGIL).
  `load_stage_results` artik `scope_type` ZORUNLU.
  **202 test yesil** (Faz 1 + Faz 2), kilitler 45 yesil, `scripts/` DEGISMEDI, ucretli cagri 0.
  GOLDEN PARITY: `dict_sha` uc firmanin dondurulmus `dictionary_sha256_FINAL` degerini
  BIREBIR uretiyor; tracked golden uzerinden aile atamasi replay'i de yesil.
  **Kusur kaydi:** A2B duzenlemesi sirasinda `FamilyStageError`/`dict_sha`/
  `validate_families`/`assert_unmatched_ceiling` kazara silinmisti; `compile()` calisma-ani
  isimlerini denetlemedigi icin kacti, QA yakaladi, kilitli kaynaktaki haliyle geri kondu.
- **TEMIZ KLON SINAVI (git worktree) GERCEK KUSUR YAKALADI:** v3 erken-red blogu,
  anchor olarak kullanilan import satiri dosyada IKI KEZ gectigi icin yanlis fonksiyona
  (`republish_stale_deferred_parents`) yerlesmisti -> temiz agacta 3 test
  `NameError: name 'run' is not defined`. Blok `enqueue_channel_assignment` icine
  tasindi, commit amend edildi. **Temiz worktree sonucu: 151 yesil + 45 kilit yesil.**
- **Bilinen, v3 disi:** `tests/integration/test_a1_executor.py` 18 hata —
  `SourceSealError: price_snapshot`; kaynagi bu oturumdan ONCE degismis
  `app/core/telemetry/usage.py`. Motor v3 ile ilgisi yok, bu kapsamda duzeltilmedi.

## Earlier

- **Patronun yeni kaynak dokumani metne cevrildi ve olcum plani yazildi.**
  `algoritma/Optimice_Social_Media_Engine_Final_Social_Intent_Classifier.md`
  (birebir cikarim + SHA256 muhru). Plan: `plan_social_v5_intent_test.md` v0.1 TASLAK,
  **ucretli cagri 0, kod yazilmadi.** Gate ACIK (yeni katman sozlesmesi) -> Architect
  (Frontier, kor) + Konsey (3 koltuk, kor mod) kosuldu; ikisi de plan §9'a islendi.
- **Aşama 0'in ana bulgusu ucretsiz olculdu ve bagimsiz dogrulandi:** patronun kendi
  SOCIAL pozitiflerinin Hissefy'de %74'u (48/64-65), GR-7'de %55'i (47/85) ayni anda
  **ADS** etiketli; Dijital'de yalniz %5 (1/20). Dokumandaki "COMMERCIAL_SEARCH ->
  Social disina alinir" kurali harfiyen uygulanirsa patronun kendi secimlerinin
  cogunlugunu silme adayi. Kaynak: `patron_labels_optimice_v2.json` (`raw_labels`),
  `trial_workspaces/patron_labels_{dijital,gr7}_v2.json` (`current_positive`).
- **Kaynak deltasinda sessiz geri adim yakalandi:** eski dokumanin §7 tablosundaki
  `BrandContentability >= 60` Primary kapisi ve 50-59 Secondary/Review bandi yeni
  dokumanda YOK. V4 sozlesmesi bunu otorite kabul etmisti -> plan S3 (SOR-PATRONA).
- **Konsey ayrismasi kayitli (plan §9.2):** `deepseek` birlesik prompt, `codex` +
  `antigravity` + Architect ayri cagri. Plan ayri cagriyi secti (A0 = muhurlu V4'un
  bedava ve degismez kalmasi icin), `deepseek`'in curutme testini plana aldi.
- **Codex plan incelemesi uygulandi -> v0.2 (sadelestirme).** Cikarilanlar: A2 deterministik
  kol, kapida elenen %10 orneklem, sentetik accuracy capalari, uc-yapilandirmali esik
  taramasi ve KAYNAKSIZ TUM ESIKLER (%20 FER vetosu, %70 baglayicilik, 0,80 dengeli
  dogruluk, 0,92 Kendall, 5 puan capa sapmasi) -> hepsi HAM DEGER olarak raporlanir.
  COMMERCIAL_SEARCH -> EXCLUDE artik KOSULSUZ uygulaniyor, etkisi FER ile olculuyor;
  ADS ortusmesi yalniz risk baglami, vekil etiket DEGIL. Kontrol kolu duzeltildi: eski V4
  Primary listesi tek kontrol olmaktan cikti, kesinlesmis yeni kapilarla ortak K-SCORE
  (score-only) kolu kuruldu, K-INTENT ayni aday kumesini kullaniyor. Patron sorusu 8 -> 3.
- **Son sadelestirme -> v0.3 ve plan DONDURULDU.** M5 skor->intent tahmin analizi
  kaldirildi (gercek etiket degil, modelin kendi ciktisini tahmin ediyordu, karar
  uretmiyor); "iki bagimsiz deterministik uygulama" sarti kaldirildi (tek uygulama +
  odakli birim testleri); FER tek oran olmaktan cikip IKI AYRI olcume bolundu
  (Gate kaybi = yeni kapilarda elenen tarihsel pozitif / tum evrendeki tarihsel pozitif;
  Commercial kaybi = COMMERCIAL_SEARCH nedeniyle elenen / kapilari gecen) -- BIRLESTIRILMEZ.
  Yeni dokuman GUNCEL OTORITE: BC>=60 Primary kapisi ve 50-59 bandi UYGULANMIYOR
  (degisiklik kaydi, bloklayici soru degil). SocialIntentType tek secimli enum, patron
  sorusu acilmiyor. **Patrona kalan tek soru: yuksek/orta Social Score sayisal tanimi --
  ve o bile ucretli kosuyu BLOKLAMIYOR** (sonuclar esiksiz saklanir, esik gelince
  ucretsiz hesaplanir).
- **v0.4: patron karar paketi KALDIRILDI** (istenmemisti; dosya silindi) ve plandaki
  "Asama 0 patron paketi" adimi cikarildi. Eksik esik yalniz `UNDEFINED-IN-SOURCE` kaydi.
  **Iki konu ayrildi:** (1) yuksek/orta sinirlari = dokumanin spesifikasyon eksikligi,
  (2) ucretli kosu izni = KULLANICININ operasyonel butce onayi (patron karari degil).
  Intent siniflandirmasi esikten BAGIMSIZ kosar: SocialIntentType/IntentConfidence/
  IntentReason saklanir, Gate kaybi + Commercial kaybi olculur, sinif dagilimi ve tekrar
  kararliligi raporlanir; PRIMARY/SECONDARY nihai atamasi ve V5 genel basari hukmu
  VERILMEZ. Esik gerektiginde dokuman sahibine sorulacak TEK cumle plan §5.2'de;
  secenek/ornek esik/onay paketi uretilmez.
- **ASAMA 1 TAMAMLANDI (14.09, ucretli cagri 0).** Modüller: `scripts/social_v5_engine.py`
  (yeni kapilar Rel>=40/RF>=50/BC>=50, BC>=60 UYGULANMIYOR + Priority esleme +
  IKI AYRI kayip metrigi), `social_v5_intent_prompts.py` (tek-secimli enum, YAPISAL
  korluk), `social_v5_seal.py` (havuz + batch plani + muhur), `social_v5_runner.py`
  (dry-run; V4 kosucusunu parser=/budget_stage= ile YENIDEN KULLANIR, ikinci kosucu
  YAZILMADI), `social_v5_transport.py`, `social_v5_measure.py` (esikten bagimsiz olcumler).
  `scripts/social_v4_runner.py`'ye GERIYE-UYUMLU iki kwarg eklendi (parser, budget_stage).
- **Aday havuzu:** Hissefy 530/540 · Dijital 554/671 · GR-7 306/892 (RF<50 en cok elen).
  Dort ham skor YENIDEN URETILMEDI -- 10.09'daki odenmis kosunun 360 ham ciktisi okundu.
- **Maliyet kaniti** (`benchmark/social_v5_dryrun.json`, saglayici cagrisi 0): 282 cagri,
  gercekci **$1,42**, worst-case rezerve **$3,55**, tam-retry ust siniri $7,10.
  **worst-case $3,00 tavani ASIYOR** -> O2'de uc secenek operasyonel butce onayina sunuldu.
- **GERCEK KOSU HATASI BULUNDU VE DUZELTILDI (kullanici tespiti):** `v5intent:` oneki
  yalniz V4/V5'i ayiriyordu, V5 DUMAN ile V5 TAM kosusunu AYIRMIYORDU -- ayri raw dizini
  ve checkpoint YETMEZ, ledger request_id uzayi ortak kalirsa duman settled olduktan
  sonra tam kosu B1/B2 invaryantiyla durur. Faz artik request_id'nin KENDISINDE:
  `v5intent:smoke:...` / `v5intent:full:...`. Iki faz AYNI ledger ve AYNI sert tavani
  paylasmaya devam eder. `run_paid(phase=)` faz dogrular + raw/ckpt'yi fazdan turetir;
  olcum yalniz `v5intent:full:*` okur (duman final olcume GIRMEZ).
  Kanit: `tests/integration/test_social_v5_smoke_full_ledger.py` (7 test, gercek Postgres
  + gercek AiCostLedger, saglayici cagrisi 0).
- **Cagri hesabi duzeltildi:** duman artik tam kosunun ICINDE sayilmiyor ->
  **288 cagri = 282 tam + 6 duman**; gercekci **$1,45**, worst-case rezerve **$3,63**,
  tam-retry ust siniri $7,25. **Onerilen tek sert tavan: $4,00.**
- **test_social_v4_seal.py'deki iki eski test GUNCEL GERCEGE gore duzeltildi**
  (uretim verisi/muhur DEGISTIRILMEDI): GR-7'nin 4 relevance degeri 10.09'da
  tamamlandigi icin provisional=False ve affected_keyword_count=0 bekleniyor;
  `final` hala False ama ARTIK BASKA sebeple (complete=False, pending'de post_smoke
  muhru) -- test bu ayrimi acikca dogruluyor.
- **Testler:** tum unit paketi + V5 entegrasyonu **2192 passed, 0 failed**.
- **UCRETLI KOSU TAMAMLANDI (14.09, kullanici O2 onayi: tek ortak $4,00 tavan).**
  Duman 6/6 settled $0,027742 -> yapisal kontrol TEMIZ (dejenere batch yok, dort
  sinif da uretildi, guven 75-95) -> tam kosu 282/282 settled $1,333045.
  **TOPLAM 288 cagri / $1,360787 (tavan $4,00, kalan $2,639213), 0 retry, 0 ceiling,
  0 eksik.** Attempt id=35 (run 25); V4'un attempt'ine ve $5 defterine DOKUNULMADI.
- **OLCUM SONUCLARI** (`SOCIAL_V5_OLCUM_RAPORU.md`, `benchmark/social_v5_sonuc.json`):
  * Gate kaybi: Hissefy 1/64 · Dijital 2/20 · GR-7 21/91 (%23,1)
  * **Commercial kaybi: Hissefy 0/63 · Dijital 0/18 · GR-7 28/70 = %40** -- ticari
    kural GR-7'de kapilari GECEN patron pozitiflerinin %40'ini siliyor (uc tekrarda
    da 23-25). Iki metrik AYRI raporlandi, BIRLESTIRILMEDI.
  * Sinif kararliligi (oybirligi): 0,677 / 0,823 / 0,637. COMMERCIAL kumesi Jaccard
    Hissefy 0,57-0,67 · GR-7 0,63-0,69 (Dijital 0,89-0,93) -> iki firmada hangi
    kelimenin sosyalden cikarilacagi kosudan kosuya DEGISIYOR.
  * K-SCORE vs K-VOL agirlikli: C +1/+1/+4 ama P -12/-4/-22 -> V4'un imzasi UC
    FIRMADA DA ayni yonde (V4'te Dijital P'de karisikti).
  * Dijital'de havuzun %51'i COMMERCIAL_SEARCH ama tek patron pozitifi kaybi yok.
- **OLCUM KATMANI DUZELTILDI -> v2 KANONIK BIRIM cozunurlugu (kullanici tespiti,
  UCRETSIZ, ham artifact/prompt/muhur/ledger DEGISMEDI).** v1'de payda olarak eslesen
  DB satiri sayisi kullaniliyordu (GR-7: 91); plan A7'nin olcum birimi 85 KANONIK
  tercihtir. Yeni fonksiyonlar: gate_loss_units / commercial_loss_units /
  exact_hit_units_at_n / weighted_exact_utility_units / gate_stage_for_unit.
  Kurallar: birim, eslesen satirlarindan EN AZ BIRI kapiyi geciyorsa hayatta;
  evrende yok AYRI raporlanir (kapi sebebi DEGIL); commercial kaybi ancak kapiyi
  gecen TUM eslesmeler ticari ise; P birimi BIR KEZ (en iyi sirasiyla) sayar.
  **GR-7 selalesi V4'un dogrulanmis selalesiyle BIREBIR: 85 -> 2 evrende yok ->
  relevance 5 -> RF 11 -> 67 hayatta.**
  Eski->yeni: gate 21/91 -> 16/83 · commercial 28/70 -> 27/67 · C ve P HIC DEGISMEDI.
  Hissefy/Dijital HIC DEGISMEDI (birim=satir 1:1). v1 artifact superseded olarak
  saklandi: benchmark/social_v5_sonuc_v1_row_resolution_superseded_20260914.json.
  Policy-negatif de ayrildi: GR-7 24 kanonik birim / 28 eslesen satir.
  Testler: 2204 passed (12 yeni birim-cozunurlugu regresyon testi dahil).
- **PROVENANCE AYRIMI NETLESTIRILDI (ucretsiz, hesap YOK):** ucretli kosu **v0.4**
  muhurlu sozlesmesiyle yapildi ve o muhur DEGISTIRILMEDI (plan_version v0.4,
  seal_sha256 4e941c6e...). **v0.5 yalnizca ucretsiz olcum duzeltmesidir.**
  Sonuc artifact'i artik AYRI alanlar tasiyor: execution_plan_version /
  execution_seal_sha256 / measurement_amendment_version / measurement_resolution /
  supersedes. Belirsiz tek `plan_version` alani KALDIRILDI. Plan basligi ve
  degisiklik gecmisi (v0.5 satiri) + rapor girisi guncellendi; "kod yazilmadi"
  ifadesi kaldirildi. `datasets` ve `sonuc_sha256` DEGISMEDI (3ee89ec7...).
  Yeni test dosyasi: tests/unit/test_social_v5_provenance.py (19 test) --
  muhrun sonradan v0.5 yapilmasini, sayilarin sessizce degismesini ve
  Hissefy/Dijital'in duzeltmeden etkilenmesini yakalar.
  Testler: **2223 passed, 0 failed**. Ham artifact 282+6, ledger 288 cagri /
  $1,360787 -- dokunulmadi.
- **PATRON NIHAI PRIORITY KARARI UYGULANDI -> v0.6 ucretsiz son-islem (15.09).**
  high_min=70, mid_min=40, low_below=40 (source=patron_decision, artifact'ta
  `priority_specification`). `PENDING_THRESHOLD` KALDIRILDI; SS<40 non-commercial
  -> EXCLUDE(low_social_score) -- ticari kural dusuk-skordan ONCE geldigi icin
  GR-7 commercial kaybi 27/67 KARISMADI (test ile dogrulandi).
  * Priority (consensus): Hissefy 1/67/411/61 · Dijital 0/9/225/437 · GR-7 1/5/187/699
    (PRIMARY/TREND/SECONDARY/EXCLUDE). **PRIMARY uc firmada toplam IKI kelime** --
    SS>=70 bu evrenlerde cok nadir.
  * **GENEL HUKUM (donmus kural, plan §6.2): uc firmaya birden genellenebilecek yon YOK.**
    K-INTENT vs K-SCORE: Hissefy karisik · **Dijital DAHA KOTU (C ve P 3/3 negatif)** ·
    GR-7 karisik (P'de 3/3 pozitif +4/+11/+12, C sonucsuz). Hicbir firmada "daha iyi" YOK.
    K-INTENT vs K-VOL: uc firmada da karisik; **P'de hacim-only'ye yeniliyor (-11..-18)**.
  * K-INTENT Top-N Jaccard: 0,37-0,50 (Hissefy) · 0,55-0,70 (Dijital) · 0,37-0,44 (GR-7).
  * GR-7'nin tek PRIMARY'si kaynak dokumanin KENDI CONTENT_NATIVE ornegi
    ("sac beyazlamasina cozum") -- ornek sizintisi notu gecerli.
  * Esikten bagimsiz TUM metrikler birebir KORUNDU (test ile dogrulandi); v0.4 muhru ve
    v0.5 kanonik duzeltmesi DEGISMEDI; **yeni AI cagrisi YOK** (ledger hala 288/$1,360787).
  * Testler: **2244 passed, 0 failed**.
- **DURUM/PROVENANCE CELISKILERI TEMIZLENDI (ucretsiz, hesap YOK).**
  `threshold_status` -> `RESOLVED_BY_PATRON_DECISION` (high 70 / mid 40 / low <40,
  decided_at 2026-09-15); tarih SILINMEDI, kaynaktaki tanimsizlik GECMIS DURUM olarak
  isaretli kaldi. Plandaki bayat metinler (esik bekleniyor, hukum verilmez, butce onayi
  bekliyor, $3 tavan, acik O1/O2) temizlendi; tarihce satirlari *(gecmis)* damgasi aldi.
  `datasets` ve hesaplanan metrik/siralama/hukum alanlarina DOKUNULMADI.
- **PATRON SUNUMU URETILDI: `SOCIAL_V5_PATRON_SUNUMU.xlsx`** (11 sayfa, 2347 satir,
  232 KB): Yonetici_Ozeti · Firma_Metrikleri · Karsilastirmalar · Kayip_Analizi ·
  Kararlilik · Teslim_Listeleri · Kelimeler_{Hissefy,Dijital,GR7} (540/671/892 =
  TAM evren, 21 sutun: dort intent alani + SocialScore + nihai Priority + EXCLUDE
  nedeni + r1/r2/r3 + consensus) · Maliyet_Provenance · Dokumana_Uyum.
  GR-7 metriklerinde YALNIZ canonical-unit; superseded row-resolution artifact'i
  sunuma HIC girmedi (`scripts/social_v5_patron_excel.py`, saglayici cagrisi 0).
- Testler: **2249 passed, 0 failed**. Ledger 288 cagri / $1,360787 -- dokunulmadi.
- **ARTIK BEKLEYEN KALEM YOK.** Onceki bekletilenler: PRIMARY/SECONDARY atamasi (PENDING 422/257/191) ve V5 genel
  basari hukmu -- "yuksek"/"orta" sinirlari kaynakta TANIMSIZ, sayi UYDURULMADI.
  Sinir gelince saklanmis sonuclardan UCRETSIZ hesaplanir.
- **YENI INCELEME TURU ACILMAYACAK.** Yeni konsey, alternatif algoritma, yeni esik veya
  ek onay mekanizmasi eklenmeyecek.
- Konsey ham ciktilari: `benchmark/council/social_v5_intent_*_20260914.md` (MANIFEST guncel).

### Earlier


- **First real task through the adapted team — stuck profile recovery (ADR-002).**
  `CLAUDE.md`'s "restart leaves profiles stuck" note turned out to be stale: a startup
  janitor already existed. The real gap was that it runs **once at startup** and skips
  rows younger than 15 min — so a profile `running` at the instant of the restart is
  skipped and never revisited. Fixed with read-time (lazy) staleness on `get_profile`
  and `get_workspace`, the pattern the repo already uses for preview/discovery.
  Gate CLOSED (known pattern) → Architect and Council did not run. Seats: Backend Dev,
  QA. Zero paid AI calls. Full suite: **3131 passed, 18 failed**, all 18 pre-existing
  in `tests/integration/test_a1_executor.py` (`price_snapshot` seal drift, no import
  path to the changed modules). QA raised a residual risk — a live analysis running
  past 15 min now shows as `failed` on the next poll, and retry can race the still-alive
  run because `analyze_profile` has no single-flight guard. **User ruled Option A on
  2026-09-08: ship as-is, risk accepted.** Not committed.
- **agy (Antigravity) reconnected.** The binary lives outside PATH
  (`AppData\Local\agy\bin\agy.exe`), so the council saw only 2 seats. A shim at
  `~/bin/agy` (already first on PATH) restores it — council is back to 3 live seats:
  `antigravity`, `codex` (gpt-5.6-sol), `deepseek` (deepseek-v4-pro).

### Earlier

- **Council installed and calibrated** (`~/.claude/skills/claude-council/`). Three live
  seats: `codex` (gpt-5.6-sol, medium), `antigravity` (gemini-3.8-flash-high), `deepseek`
  (deepseek-v4-pro). Two are subscription-funded; DeepSeek is the only cash seat.
- **dev-team adapted to this stack** (`.claude/skills/dev-team/`). Four role files rewritten
  (frontend, backend, qa, ui-ux), Architect rewritten around `CLAUDE.md`, Plan-Check commands
  switched to `docker-compose.test.yml` + `npm run lint/format/build/test`, `allowed-tools`
  switched off Flutter, council integration added.
- **`.dev-team/` skeleton created** (this folder).

## Open decisions — the user's, not the team's

- **`.env` exposure, durable fix.** The council's CLI seats are now guarded (no tools, no
  file reads), so nothing reads `.env` today. But Codex's `-s read-only` sandbox blocks
  *writes*, not reads — so the guard is the only thing standing between a seat and the file.
  The structural fix (move `.env` out of the repo tree, update 5 `env_file` references in
  `docker-compose.yml` and `app/config.py:323`) was proposed and **not** approved.
- **Success criteria and roadmap sequencing** — `PROJECT.md` and `ROADMAP.md` are
  deliberately blank pending the user.

## Known blockers, carried from earlier work

- Google Ads refresh token is dead (`invalid_grant`); production enrich / keyword-ideas
  endpoints do not work.
- Site-profile analysis runs in FastAPI `BackgroundTasks`; a restart leaves profiles stuck
  in `pending`/`running`.

## Technical debt

See `CLAUDE.md` §10. Nothing new added by this workstream.

## Calisma kurallari (bu repoya ozgu)

- `docker-compose.test.yml` uzerinde **ayni anda yalniz bir pytest kosusu** calistirilir.
  Tek bir paylasilan `test_db` var; paralel kosular birbirinin tablolarini truncate eder.
  Yeni kosudan once `docker ps --filter name=test_app-run` kontrol edilir.
- Uzun test kosulari **ajana birakilmaz** — ajan arka plan kosusunu beklerken takilabilir
  (10.09'da 37 dk takildi ve uc oksuz container birakti).

## Next steps

1. **Aşama 1 (ücretsiz)** — Social V4 sözleşmesinin kodu, birim testleri, mühür üretimi.
   §1.12'nin sınavı: iki bağımsız deterministik uygulama, aynı fixture ve aynı saklanmış
   ham AI çıktısından aynı sonucu üretmeli.
2. **`CLAUDE.md` §4/§10 güncellendi** (ADR-002) — takılı profil sorunu çözülmüş olarak
   işaretlendi, Celery'ye taşıma bilinçli olarak reddedildi.
3. `tests/integration/test_a1_executor.py` — 18 test `price_snapshot` mühür kayması
   yüzünden kırık, bu iş hattıyla ilgisiz ama paket yeşil değil.
4. **Konsey altyapısı:** `codex` koltuğunun büyük artifact'lara katılabilmesi için ya
   prompt'un dosya/stdin ile geçmesi ya da CLI koltuk korumasının gevşetilmesi gerekiyor —
   ikincisi aşağıdaki `.env` kararına bağlı.
5. Volume-matched AUC re-analysis — ücretsiz, konseyin önerisi, hâlâ koşulmadı.
6. `.env` yapısal düzeltmesi.

## Uncommitted

`.claude/` is gitignored, so the adapted skill is **not** version-controlled. `.dev-team/`
is not ignored and will show as untracked. Neither has been committed; no commit has been
made in this workstream.

## 2026-09-23 — Yeni iş hattı: Sosyal brief akışı (PLAN, kod yok)

- `plan_social_brief_akisi.md` yazıldı. Akış: 3–5 kelime → platform başına format + video süre aralığı → kategori → fikir → içerik. Yalnız seçilen kanallar için fikir üretilir.
- Kapı AÇIK: veri modeli ve katmanlar arası sözleşme değişiyor. Mimar (Opus, medium) ve konsey (Codex, Gemini, DeepSeek) birbirini görmeden yanıtladı.
- Bugünkü kodda doğrulanan açıklar:
  - Platform filtresi üretimden sonra uygulanıyor; prompt izinli kanalları görmüyor.
  - Bilinmeyen platform instagram'a çevriliyor.
  - Yeniden üretim prompt'u platform değiştirmeye izin veriyor.
  - Yedek fikir sabit instagram/post.
  - `SocialIdea.keyword_id` hiç yazılmıyor.
- Tek gerçek ayrışma: versiyon yaşam döngüsü (K6). Mimar hafif `is_stale` modelini, Codex tam `SocialGenerationSet` modelini öneriyor.
- Sıradaki adım: patron kararları K1–K9 (planın §0'ı), ardından F1.
- Konsey altyapısı: Codex büyük prompt'ta Windows argüman sınırına takılıyor; bağlam yaklaşık 12 KB'ın altında tutulmalı.
- 23.09 patron kararları: K1=1–5 kelime, K4=her çift en az 1 fikir (tüm kategoriler genelinde), K6=hafif model (eski içerik birikir), K7=sahte yedek yok. Sıradaki adım: F1.
- 23.09 rev.2: Codex incelemesi (NO-GO) işlendi. K4 çelişkisi düzeltildi. Eklenenler: idempotency/attempts tablosu, içerik kopya koruması (unique kısıtı canlı denetimden sonra), kalıcı süre durumu + scenario_segments, primary_keyword_id, K10 (içerikli fikre yeniden üretim 409), K11 (brief kategori sonrası kilitli), K12 (/social/bulk flag → 410). Sıradaki: K10–K12 teyidi, sonra F1.
- 23.09 rev.3: Codex rev.2'ye GO verdi, K10–K12 onaylandı. Eklenenler: SocialContent.brief_id + UNIQUE(idea_id) WHERE brief_id IS NOT NULL, deneme lease/heartbeat + okuma anında worker_lost uzlaştırması, format_payload (segments/slides/posts), çakışmasız süre aralıkları, statik Story. Sıradaki: F1.
- 06.10 ADS export kelime bazlı görünüm: toplantı geri bildirimi (açıklamalar kelimeyle eşleşmiyordu). Excel'e 'Kelime Bazlı Reklamlar' sayfası + kelime_bazli_reklamlar.csv; reklam_gruplari.csv [:3] kırpması kaldırıldı. Kapı KAPALI (bilinen kalıp). Plan Codex 2 tur GO. tests/unit/test_export_ads_keyword_view.py; export testleri 56 passed. Commit YOK.
- 09.10 Paket 1 (plan_yapilacaklar.md) TAMAM. Kapı KAPALI (bilinen kalıplar; şema değişikliği yok).
  - Commit'ler: 60f4d40 (run-düzeyi profil uçları silindi, kullanıcı onaylı), 6215c6a
    (Content-Disposition yardımcısı, Türkçe adda 500), f69e041 (görev takibi run'a bağlı +
    `scoring_run_id`), 11cd062 (V3'te relevance compute 409 + otomatik tetik no-op).
  - QA bulgularından sonra: d5932e5 (run'a geri dönüşte görev + URL task_id), 5ab6874 (dashboard
    İlgi Skoru V3'te "skipped", ölü import).
  - Tam paket: backend 4402 passed / 112 skip / 0 kırmızı; frontend 224 passed, lint, format ve build temiz.
  - Bilinçli atlananlar:
    - Channels.tsx `runAssignment` geç yanıt koruması (route'suz sayfa, Paket 5.2'de silinecek).
    - Keywords `selectScoreRun` önbelleği (keşif yoklaması kendiliğinden düzeltiyor).
  - Paket 5.3'e not: `_run_relevance_computation` + `scoring_tasks` uyumluluk sarmalayıcısı +
    `scoring.py` v2 execute dalı erişilemez durumda; v2 relevance zinciri birlikte emekliye
    ayrılabilir.
  - 10.10 Codex incelemesi sonrası 6c13a42: başlatma isteği sürerken sayfadan çıkılırsa görev
    kimliği kayboluyordu. Kimlik artık başlatıldığı run'ın anahtarına her zaman yazılıyor.
    Kapsam: ADS generate ve grup regenerate, SEO bulk. Unmount testleri eski kodda kırmızı;
    frontend 227 passed. Backend değişmedi, en son tam paket 4402.
    - SocialStepper ve Channels.tsx'e de aynı düzeltme yapıldı, ama ikisi de canlı değil:
      `/social` ekranı SocialBriefWorkspace kullanıyor (SocialStepper yalnız `ds-entry.ts`'te);
      Channels.tsx route'suz.
    - Canlı brief akışı ekranı sunucudan geri yüklüyor (`getBriefState`), localStorage'a bağlı
      değil; bu hatadan etkilenmiyordu (Codex notu, doğrulandı).
  - Paket 1 KAPANDI (Codex: engelleyici bulgu yok).
- 10.10 Paket 2 TAMAM.
  - Kapı: veri modeli değiştiği için açıktı. Ancak tasarım planda kayıtlıydı, Codex iki
    turda inceledi ve kullanıcı onayladı; bu yüzden Architect/Council yeniden koşulmadı.
  - Commit'ler:
    - 7728838 (2.1): negatif ∩ hedef. Yalnız açık metinsel broad/phrase/exact; önek/fuzzy
      yok. Tamamlama güvenli olmayan kelime eklemez. Atılanlar
      AdGenerationSet.warnings'e yazılır; arayüz yok.
    - 0286f26 (2.2): `analysis_attempt_id` + migration 20261010_001 (idempotent). Dört
      `_run_*` task'ının running/başarı/hata yazımları token'a koşullu; janitor koşullu
      UPDATE + token döndürme; ölü `_run_workspace_profile_analysis` silindi; ADR-002
      güncellendi.
    - 4a3c007: QA bulgusu S1. Janitor'ın token ve yaş koşullarını AYRI ayrı ayırt eden 4
      test.
  - Tam paket: backend 4478 passed / 112 skip / 0 kırmızı (4a3c007 öncesi). Guard dosyası
    56 passed. Frontend değişmedi (227).
  - Mutasyon testi YAPILAMADI: production kodunu geçici bozma düzenlemesi izin
    politikasınca engellendi. Ayırt edicilik QA'nın gerekçeli mutasyon tablosuna dayanıyor.
  - Dev DB 20261010_001'e yükseltildi.
  - **DAĞITIM NOTU:** sunucudaki feat/auth-login dalında lean'de olmayan migration'lar var
    (20261006_001/002). Merge sonrası alembic iki head görür; açılıştaki
    `alembic upgrade head` hata verir. Merge'de `alembic merge` revizyonu gerekir.
    Merge'den sonra birleşmiş dalın migration zinciri ayrıca doğrulanmalı: boş DB'den
    `upgrade head` + test_migration_chain. Lean'de geçen testler birleşmiş dalı garanti
    etmez (Codex notu).
  - Codex Paket 2 incelemesi: engelleyici bulgu yok. Codex 163 ilgili testi bağımsız koştu,
    hepsi geçti. Mutasyon testi yokluğunu engelleyici saymadı. Paket 2 KAPANDI.
- 10.10 Paket 3 TAMAM. Kapı KAPALI (bilinen kalıplar, şema yok).
  - 61906df (3.1): etiketler "Kaçınılacak temalar" / "Kesin dışlama" (kullanıcı onaylı).
    - B terimleri A'da salt-okunur çip, düzenlenebilir listeden ayrı.
    - Sunucu `_preserve_hard_exclusions`: confirm, profile/approve ve anchors/preview'da
      exclude_themes = sert terimler + A.
    - Değişmeyen kayıt sürüm artırmaz. Kirli form yeniden yüklemede korunur.
  - b1f9542 (3.2): AnalysisProgress V3 adımları (orchestrator 15/40/65/85/92/100, kapalı
    kanal adımı yok); `current_message` gösteriliyor. Screening yoklaması ve
    `getScreeningStatus` istemci fonksiyonu kaldırıldı (backend ucu duruyor).
  - 1b4d9a3 (3.3): `app/core/keyword_junk.py` (empty / symbol_only / numeric_only; `+` ve `/`
    sayısal sayılır).
    - Uygulandığı yollar: crud.create_keywords_bulk (workspace + legacy), POST /keywords/
      400, build_import_plan, workspace refresh added_rows, CSV parser.
    - Barkodlu metin ve model numaraları kalır. İki Google Ads import ucuna ilk testler
      eklendi.
  - Kontroller:
    - Tam paket: backend 4525 passed / 112 skip / 0 kırmızı; frontend 249 passed; lint,
      format, build temiz.
    - QA: 3 commit PASS, yalnız nit.
    - Frontend konteyneri yeniden başlatıldı; yeni metinler sunuluyor.
    - Tarayıcıda görsel kontrol YAPILMADI.
  - Açık nitler (düzeltilmedi):
    - Keywords `selectScoreRun` `chainRunStatus`'u sıfırlamıyor (≤3 sn eski durum).
    - Dirty A + B'den terim ekle-çıkar zinciri soft terimi geri ekleyebilir.
    - Refresh'te atılan çöp sayısı raporlanmıyor.
  - Codex incelemesi sonrası (doğrulandı):
    - b77bdda: başarısız analiz bildirimi "çalışıyor" + spinner + %100 + 7/6 adım gösteriyordu,
      bantta tüm adımlar "done" görünüyordu (`stageInfo` failed'i done sayıyordu). Artık
      durduğu adım `is-failed`, yüzde korunuyor, toast hatayı gösteriyor; 3 test eklendi.
    - dd267ef: kararsız test bulundu ve düzeltildi. BrandProfile.exclusion testleri
      textarea DOM'a girer girmez okuyordu; form effect'i doldurmadan önce boştu. Tam
      pakette 6 koşunun 2'sinde kırmızıydı, düzeltme sonrası 6/6 yeşil (252 passed). Ürün
      hatası değil, test yarışı.
  - Paket 3 KAPANDI.
- 10.10 Paket 4 (Codex'in daralttığı kapsam).
  - Kapı: yeni araç, ama kapsam kayıtlı ve onaylı → Architect/Council koşulmadı.
  - 734311d: ruff==0.6.9, `ruff.toml` select E9/F63/F7/F82 (yalnız hata sınıfları,
    formatlama yok), `requirements-dev.txt`. 7 F821 düzeltildi:
    - typing importları,
    - ölü + kırık `order_rows_volume_only` silindi (`volume_order_key` tanımsızdı),
    - iki test importu.
    Motor kaynaklarını hash'leyen mühür testi yok (kontrol edildi).
  - 6e9a3bc: `.github/workflows/ci.yml`.
    - Tetik: push (deploy/lean-taslak) + pull_request.
    - İşler: ruff, docker-compose.test.yml ile izole backend paketi, frontend lint +
      format + build + test.
    - Secret/AI anahtarı ve dağıtım adımı YOK.
  - Yerel tam paket 4525 passed / 0 kırmızı. Push edildi.
  - GitHub ilk CI koşusu YEŞİL (Actions run 38005849005, Codex teyidi): backend 4525 passed /
    112 skip, migration adımı başarılı; frontend 252 passed + lint + format + build; ruff 0.
    Paket 4 KAPANDI.
  - CI yalnızca lean dalını doğrular; henüz birleşmemiş auth dalının migration zincirini
    doğrulamaz.
- 10.10 kullanıcının görsel testinde bulunan hata (dfc6f3b).
  - Analiz bandı ara adımları hiç göstermiyordu. `GET /tasks/run/{id}` `{tasks,total}`
    döndürüyor; Keywords ve ChannelWorkspaceView ise dizi bekliyordu, bu yüzden görev
    HİÇ bulunmuyordu. Kanıt: app loglarında `/tasks/{id}` isteği yok.
  - Hata lean'in ilk ağacından (0e45fed) beri vardı. Testler dizi mock'ladığı için yeşildi
    (3.2'de gerçek yanıt biçimi kontrol edilmedi — ders: mock'lar gerçek API şeklini
    kullanmalı).
  - Ortak `tasksFromListResponse` eklendi; frontend 255 passed (3/3 koşu).
  - Kullanıcı notu: "Kesin dışlama" kutusu "Değişiklikleri Kaydet"in ALTINDA, rakip
    listesinin altında. Kullanıcı ilk bakışta bulamadı → yakınlık/yönlendirme UX'i açık soru.
  - Kullanıcı görsel testi geçti: adım çubuğu 0 → %85 SOCIAL → %100 (yalnız SOCIAL koşusu).
- 10.10 KULLANICI KARARI — sıra:
  1. migration birleşimini doğrula
  2. görsel kontrol
  3. dağıtım
  4. ihtiyaç oldukça küçük temizlikler

  Paket 5 tek büyük silme işi olarak YAPILMAYACAK.
  - Ertelenen küçükler:
    - kesin dışlama ayırıcıları (`;`, `. `) + yardım/yönlendirme notu
    - uzun AI dışlama cümlelerinin eşleşmeme riski
    - QA nitleri
  - Sıradaki adım: auth dalı GitHub'a gelince migration birleşimi.
- 10.10 MIGRATION BİRLEŞİMİ TAMAM (dağıtım hazır, yapılmadı).
  - Auth dalının GitHub'a gelişi:
    - Sunucunun deploy anahtarı salt okunur olduğu için git bundle + scp ile alındı.
    - Gizli bilgi taraması temiz.
    - `feat/auth-login` (db90e94) değiştirilmeden GitHub'a push edildi.
  - Birleşik dal `deploy/auth-lean-merge` (worktree `../dg-auth-merge`, GitHub'da, c001ea2):
    - 7a353b4: lean merge, çakışmasız.
    - 0a6c7b5: alembic merge revizyonu 20261010_002.
      - Revises: 20261006_002 + 20261010_001.
      - Boş DB → tek head; sunucu yolu 20261006_002 → head doğrulandı.
    - 9793504: auth frontend'inde 3 dosyaya prettier.
    - c001ea2: login kapısı LOGIN_ENABLED=false iken get_db ve Redis zincirine girmiyor.
      - Önceden `Depends(resolve_current_user → get_db)` her istekte oturum açıyordu ve
        test_23 kırmızıydı.
      - 15 odaklı API testi eklendi: kapalı, açık, must_change_password. Düzeltme geri
        alınınca kırmızı.
  - Birleşik dal: backend 4540 passed / 0 kırmızı; frontend 255; lint, format, build, ruff
    temiz.
  - S-1 (12b76b8) + login yarışı (911aaba) eklendi.
    - Birleşik dal tam paket 4567 passed.
    - Codex: dağıtıma engel YOK (51 ilgili test bağımsız geçti).
    - Yarışta login yanıtı 200 dönebilir, ama üretilen eski sürümlü oturum korunan uçta
      401 alır.
  - Runbook: plan_yapilacaklar.md "Dağıtım runbook'u"; beklenen head 20261010_003.
- 11.10 ~02:26 (+03) DAĞITIM TAMAM. Sunucu `feat/auth-login` yerel dalı 911aaba'ya
  fast-forward.
  - Yedek: ~/yedek_20261011_0220.sql (1,7 MB).
  - Migration'lar: 20261010_001 → 002 (merge) → 003, hatasız.
  - Servisler: app (2 uvicorn worker), celery worker, beat ayakta. DB ve Redis yeniden
    başlatılmadı; oturumlar korundu.
  - Sunucu notları:
    - Klon tek-dal; `git fetch origin <dal>` uzak ref oluşturmaz, `FETCH_HEAD`
      kullanılır.
    - Sunucuda artık dev `docker-compose.yml` da var; her komutta
      `-f docker-compose.prod.yml` ŞART.
  - Sıradaki: tarayıcı kontrolü (giriş, test kullanıcısıyla parola değişimi, küçük
    analiz).
  - Dağıtım sonrası kullanıcı testi:
    - Tam run (ws6, 33 kelime, 3 kanal) temiz, 1,5 dk. Süre sabit 16 AI aşamasından geliyor
      → kanal paralelleştirmesi aday.
    - Yeni çalışmada proteinim.com profil analizi "no page passed quality gate" ile düştü.
      Sebep dağıtım DEĞİL: crawler ham HTML'i 50K'da kesiyordu (0b5d148, Mayıs'tan beri),
      `<main>` 75.683'te; kalite kapısı (61c28c6, 23.09) bunu görünür yaptı.
      Yereldeki ws46 profili elle yazılıp doğrudan DB'ye girildiği için hata yerelde
      görülmemişti.
    - Düzeltme 0cecc25: metin 50K, HTML 2M sınırı + regresyon testi. Birleşik dal 13c636d:
      4569 passed.
    - Sunucuya 11.10 ~03:18 alındı (migration yok). Proteinim analizi tamamlandı (ws7).
  - Ders: tam pytest koşusundan önce HER ZAMAN `docker ps --filter name=test_app-run`. Boş
    çıktı "durdu" demek değildir; iki eşzamanlı koşu ~30 dk boşa gitti.
  - Öneri: lean dalı auth'u içermiyor. İleride iki dal ayrışmasın diye
    `deploy/auth-lean-merge` lean'e birleştirilmeli (karar kullanıcıda).
  - Auth'ta not edilen, değiştirilmeyenler:
    - change-password, oturum deposu yokken eski oturumları iptal edemiyor (TTL'e kadar
      geçerli).
    - conftest `app.database.connection.get_db`'yi override ediyor; uçlar
      `app.dependencies.get_db` kullanıyor (farklı nesne) → bazı testler test oturumu yerine
      SessionLocal ile koşuyor.
  - Sıradaki (Codex önerisi): birleşmiş migration zincirinin doğrulanması + kısa görsel
    kontrol → dağıtım. Paket 5 dağıtımın ön koşulu DEĞİL.
  - Celery: generation_tasks gövdesi değişti; deploy'da celery_worker restart (önce
    görev kontrolü).
  - Sıradaki: Paket 2, kullanıcı sonuçları gördükten sonra.
