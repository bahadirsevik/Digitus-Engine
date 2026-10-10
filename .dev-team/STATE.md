# Current Status

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
  - Sıradaki (Codex önerisi): birleşmiş migration zincirinin doğrulanması + kısa görsel
    kontrol → dağıtım. Paket 5 dağıtımın ön koşulu DEĞİL.
  - Celery: generation_tasks gövdesi değişti; deploy'da celery_worker restart (önce
    görev kontrolü).
  - Sıradaki: Paket 2, kullanıcı sonuçları gördükten sonra.
