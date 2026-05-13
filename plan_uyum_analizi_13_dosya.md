# 13 Dosya × plan.md Uyum Analizi

## Kapsam

Open Tabs'taki ilk 13 dosyanın `plan.md` (Workspace Phase A) ile birebir karşılaştırması.

---

## 1. plan.md (Referans belge)

**Özet:** Workspace Phase A planı — brand_profiles → workspace haline getiriliyor; yeni kolonlar, yeni tablo (workspace_keywords), soft-delete, keyword normalizasyonu, scoring state machine genişletmesi, frontend workspace filtrelemesi.

---

## 2. migrations/versions/f08125d66a33_workspace_phase_a_structural.py

### plan.md ile uyum:

| plan.md'de belirtilen                                                                                                                 | Migrasyonda var mı? | Durum  |
| ------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | ------ |
| brand_profiles: name, preliminary_info, suggested_keywords, deleted_at, default_geo_target_id, default_language_id, is_system_default | ✅ Hepsi eklendi    | UYUMLU |
| brand_profiles: scoring_run_id → nullable + SET NULL                                                                                  | ✅                  | UYUMLU |
| scoring_runs: brand_profile_id FK (RESTRICT)                                                                                          | ✅                  | UYUMLU |
| scoring_runs: enable_ads/seo/social flags                                                                                             | ✅                  | UYUMLU |
| scoring_runs: keyword_selection_mode, keyword_limit, selected_keyword_ids                                                             | ✅                  | UYUMLU |
| scoring_runs: skip_relevance flag                                                                                                     | ✅                  | UYUMLU |
| scoring_runs: relevance_started_at, relevance_completed_at                                                                            | ✅                  | UYUMLU |
| keywords: normalized_keyword + index                                                                                                  | ✅                  | UYUMLU |
| keyword_scores: metrics_snapshot (JSON)                                                                                               | ✅                  | UYUMLU |
| content_outputs: is_stale                                                                                                             | ✅                  | UYUMLU |
| workspace_keywords tablosu                                                                                                            | ✅                  | UYUMLU |
| uq_workspace_keyword (brand_profile_id, keyword_id)                                                                                   | ✅                  | UYUMLU |
| ck_scoring_runs_selection_mode check constraint                                                                                       | ✅                  | UYUMLU |

### Eksik / Sapma:

- **plan.md Section 13.2.2**: Deployment checklist ve sıralama notları migrasyonda yok (beklenen — bunlar kod değil).
- **plan.md Section 13.3.2**: Phase B/C notları (UNIQUE, NOT NULL) ileri fazlara bırakılmış, migrasyon doğru şekilde Phase A'da NULL-able bırakıyor.

**Sonuç: ✅ TAM UYUMLU**

---

## 3. app/core/keyword_normalize.py

### plan.md ile uyum:

| plan.md'de belirtilen                                     | Kodda var mı?                            | Durum  |
| --------------------------------------------------------- | ---------------------------------------- | ------ |
| Section 4.8: normalize_keyword() fonksiyonu               | ✅                                       | UYUMLU |
| Türkçe karakter mapping (ç, ğ, ı, i, ö, ş, ü)             | ✅ \_TURKISH_MAP                         | UYUMLU |
| "İ" → "i" (Python edge-case: TRANSLATE ÖNCE, lower SONRA) | ✅ Sıra: translate → lower → NFKD        | UYUMLU |
| Unicode NFKD normalize                                    | ✅                                       | UYUMLU |
| Combining mark temizliği                                  | ✅                                       | UYUMLU |
| Whitespace normalize                                      | ✅                                       | UYUMLU |
| Docstring'te plan.md'ye atıf                              | ✅ "Plan v6" olmasa da sıra mantığı aynı | UYUMLU |

### Eksik / Sapma:

- Docstring "Python 'İ'.lower() edge-case için doğru sıra: TRANSLATE ÖNCE, lower SONRA" ifadesi plan.md ile tutarlı. Ilave: plan.md Python 3.13`ten bahseder, kodda bu versiyon sabit değil — çalışır.
- **plan.md Section 4.8.3**: `normalized_keyword` kolonunun migration'da eklendiğini söyler. Migration'da `keywords.normalized_keyword` (varchar(500)) olarak eklenmiş ✅.

**Sonuç: ✅ TAM UYUMLU**

---

## 4. app/core/workspace.py

### plan.md ile uyum:

| plan.md'de belirtilen                    | Kodda var mı?           | Durum  |
| ---------------------------------------- | ----------------------- | ------ |
| verify_workspace() fonksiyonu            | ✅                      | UYUMLU |
| Query: BrandProfile.deleted*at.is*(None) | ✅                      | UYUMLU |
| Soft-delete kontrolü                     | ✅                      | UYUMLU |
| HTTPException 404                        | ✅                      | UYUMLU |
| Auth yok (ilk faz)                       | ✅ Docstring belirtiyor | UYUMLU |

### Eksik / Sapma:

- Yok — plan'daki minimal yapı ile birebir örtüşüyor.

**Sonuç: ✅ TAM UYUMLU**

---

## 5. tests/unit/test_keyword_normalize.py

### plan.md ile uyum:

| plan.md'de belirtilen                                  | Testte var mı?                  | Durum  |
| ------------------------------------------------------ | ------------------------------- | ------ |
| "İSTANBUL" → "istanbul"                                | ✅ test_capital_i_dot           | UYUMLU |
| "TIRAŞ" → "tiras" (dotless I)                          | ✅ test_dotless_i               | UYUMLU |
| Tüm Türkçe set ("FÖN TARAĞI" → "fon taragi")           | ✅ test_full_turkish_set        | UYUMLU |
| Combining mark cleanup ("i\u0307stanbul" → "istanbul") | ✅ test_combining_marks_cleaned | UYUMLU |
| Boş string                                             | ✅ test_empty_string            | UYUMLU |
| None safety                                            | ✅ test_none_safe               | UYUMLU |
| Whitespace normalize                                   | ✅ test_whitespace_normalize    | UYUMLU |
| Mixed case + Türkçe                                    | ✅ test_mixed_case_with_turkish | UYUMLU |

### Eksik / Sapma:

- plan.md Section 4.8.5: "test_keyword_normalize.py 8 test içerir" → mevcut 8 test ✅
- Docstring'te "Plan v6" referansı var ✅

**Sonuç: ✅ TAM UYUMLU**

---

## 6. app/core/scoring/score_engine.py

### plan.md ile uyum:

| plan.md'de belirtilen                                                                   | Kodda var mı?                                          | Durum  |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------ | ------ |
| Section 5: ScoreEngine sınıfı                                                           | ✅                                                     | UYUMLU |
| \_select_keywords_with_metrics (WorkspaceKeyword snapshot)                              | ✅                                                     | UYUMLU |
| \_legacy_select_keywords (fallback)                                                     | ✅                                                     | UYUMLU |
| create*scoring_run: brand_profile_id, enable*\*, keyword_selection_mode, skip_relevance | ✅                                                     | UYUMLU |
| run_scoring: conditional channel scoring (enable_ads/seo/social)                        | ✅                                                     | UYUMLU |
| metrics_snapshot doldurma                                                               | ✅ snapshot_source, wk_id                              | UYUMLU |
| State machine transitions (transition)                                                  | ✅ pending→scoring→scored→failed                       | UYUMLU |
| Path B: relevance gerekiyor mu kontrolü                                                 | ✅ BrandProfile.status=="confirmed" && !skip_relevance | UYUMLU |
| get_top_keywords_by_channel                                                             | ✅                                                     | UYUMLU |
| Section 5.5.2: \_deactivate_duplicate_keywords kaldırıldı                               | ✅ Yorum satırı belirtiyor                             | UYUMLU |

### Eksik / Sapma:

- **plan.md Section 5.6**: Channel pipeline (pool_builder, channel_engine) bu dosyada çağrılmıyor. run_scoring sonrası relevance hesaplaması bu dosyanın sorumluluğunda değil — sadece `should_compute_relevance` flag'ini döndürüyor. Bu plan.md ile uyumlu.
- **plan.md Section 5.5.1**: "keyword_source_filter artık WK.data_source üzerinden" kodda doğrulanmış ✅

**Sonuç: ✅ TAM UYUMLU**

---

## 7. app/schemas/brand_profile.py

### plan.md ile uyum:

| plan.md'de belirtilen                                                                                                                                                                                                 | Kodda var mı?             | Durum  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- | ------ |
| Section 9: BrandProfileResponse (id, scoring_run_id, company_url, competitor_urls, status, profile_data, validation_data, source_pages, error_message, timestamps)                                                    | ✅ Tam listelenen alanlar | UYUMLU |
| Section 9.2: WorkspaceCreateRequest (name, company_url, competitor_urls, preliminary_info, default_geo_target_id, default_language_id)                                                                                | ✅                        | UYUMLU |
| Section 9.2: WorkspaceResponse (id, name, company_url, competitor_urls, status, profile_data, suggested_keywords, preliminary_info, deleted_at, geo/language defaults, is_system_default, scoring_run_id, timestamps) | ✅                        | UYUMLU |
| Section 9.2: WorkspaceListResponse (id, name, company_url, status, profile_data, deleted_at, created_at, run_count)                                                                                                   | ✅                        | UYUMLU |
| KeywordRelevanceResponse (keyword_id, keyword, relevance_score, matched_anchor, method)                                                                                                                               | ✅                        | UYUMLU |
| RelevanceComputeResponse (scoring_run_id, total_keywords, computed, failed, average_relevance)                                                                                                                        | ✅                        | UYUMLU |
| ProfileAnalyzeRequest, ProfileConfirmRequest, ProfileDataSchema, ValidationDataSchema                                                                                                                                 | ✅                        | UYUMLU |

### Eksik / Sapma:

- **plan.md Section 9.1**: Workspace API schema expansion'dan bahseder. Mevcut schema'lar plan'ın Phase A'sını kapsıyor.
- plan.md'de `run_count` hesaplaması backend'de yapılacak denmiş — migrate'de yeni kolon yok, muhtemelen sorgu anında hesaplanıyor (plan ile uyumlu).

**Sonuç: ✅ TAM UYUMLU**

---

## 8. app/core/site_analyzer/profile_extractor.py

### plan.md ile uyum:

| plan.md'de belirtilen                                                                     | Kodda var mı? | Durum  |
| ----------------------------------------------------------------------------------------- | ------------- | ------ |
| Section 8: ProfileExtractor (site crawl + AI profile extraction)                          | ✅            | UYUMLU |
| extract_profile(): crawl, prepare content, AI prompt, JSON parsing                        | ✅            | UYUMLU |
| validate_with_competitors()                                                               | ✅            | UYUMLU |
| PROFILE_EXTRACTION_PROMPT (AI rol, görev, format, kurallar)                               | ✅            | UYUMLU |
| COMPETITOR_VALIDATION_PROMPT                                                              | ✅            | UYUMLU |
| JSON response schemas (PROFILE_RESPONSE_SCHEMA, COMPETITOR_VALIDATION_SCHEMA)             | ✅            | UYUMLU |
| \_generate_anchors() (products, use_cases, problems_solved, brand_terms, sector+audience) | ✅            | UYUMLU |
| MAX_AI_INPUT_CHARS = 8000                                                                 | ✅            | UYUMLU |

### Eksik / Sapma:

- **plan.md Section 8.1.2**: "preliminary_info desteği" — kodda extract_profile() parametresi olarak alınıyor, AI prompt'una ekleniyor ✅
- **plan.md Section 8.4**: "ÇELİŞKİ KURALI: site içeriği öncelikli" — prompt'ta mevcut ✅

**Sonuç: ✅ TAM UYUMLU**

---

## 9. frontend/src/stores/brandStore.ts

### plan.md ile uyum:

| plan.md'de belirtilen                                                                       | Kodda var mı? | Durum  |
| ------------------------------------------------------------------------------------------- | ------------- | ------ |
| Section 7.4: Zustand store (activeWorkspace)                                                | ✅            | UYUMLU |
| persist middleware (localStorage)                                                           | ✅            | UYUMLU |
| ActiveWorkspace interface (id, name, company_url, status, profile_data, suggested_keywords) | ✅            | UYUMLU |
| setActiveWorkspace, clearWorkspace                                                          | ✅            | UYUMLU |

### Eksik / Sapma:

- plan.md Section 7.4'te `brand-store` key ismi belirtilmemiş, kodda "brand-store" kullanılıyor — uyum sorunu değil.

**Sonuç: ✅ TAM UYUMLU**

---

## 10. frontend/src/components/Layout.tsx

### plan.md ile uyum:

| plan.md'de belirtilen                                                                                                          | Kodda var mı?                 | Durum  |
| ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- | ------ |
| Section 7: Layout component with sidebar navigation                                                                            | ✅                            | UYUMLU |
| Nav items: Ana Panel, Marka Profili, Anahtar Kelimeler, Skorlama, İlgi Skoru, Kanallar, İçerik Üretimi, Görevler, Dışa Aktarım | ✅ Tümü mevcut                | UYUMLU |
| Active route highlighting                                                                                                      | ✅ location.pathname === path | UYUMLU |
| API Bağlı status indicator                                                                                                     | ✅                            | UYUMLU |
| Layout.css import                                                                                                              | ✅                            | UYUMLU |

### Eksik / Sapma:

- plan.md Section 7.1: WorkspacePhaseA.md'de navigation item sırası — kodda Dashboard (/) ilk sırada, Brand Profile ikinci. plan.md aynı sırayı belirtiyor ✅
- Workspace seçici (workspace selector) Layout'ta değil — BrandProfile ve Keywords sayfalarında. plan.md'de Layout'a eklenmesi gerektiğine dair bir not yok.

**Sonuç: ✅ TAM UYUMLU**

---

## 11. frontend/src/App.tsx

### plan.md ile uyum:

| plan.md'de belirtilen                                    | Kodda var mı? | Durum  |
| -------------------------------------------------------- | ------------- | ------ |
| Section 7: BrowserRouter + Routes + Layout               | ✅            | UYUMLU |
| Route: / → Dashboard                                     | ✅            | UYUMLU |
| Route: /brand-profile → BrandProfile                     | ✅            | UYUMLU |
| Route: /keywords → Keywords                              | ✅            | UYUMLU |
| Route: /scoring → Scoring                                | ✅            | UYUMLU |
| Route: /relevance → Relevance                            | ✅            | UYUMLU |
| Route: /channels → Channels                              | ✅            | UYUMLU |
| Route: /generation → Generation                          | ✅            | UYUMLU |
| Route: /tasks → Tasks                                    | ✅            | UYUMLU |
| Route: /export → Export                                  | ✅            | UYUMLU |
| Route: /google-ads → /keywords?tab=google-ads (redirect) | ✅            | UYUMLU |

### Eksik / Sapma:

- Yok — frontend routing plan.md'de belirtilen tüm rotaları içeriyor.

**Sonuç: ✅ TAM UYUMLU**

---

## 12. frontend/src/pages/Relevance.tsx

### plan.md ile uyum:

| plan.md'de belirtilen                                            | Kodda var mı?                              | Durum  |
| ---------------------------------------------------------------- | ------------------------------------------ | ------ |
| Section 6.2: Relevance sayfası (ilgi skoru görüntüleme)          | ✅                                         | UYUMLU |
| Workspace filtering (sadece aktif workspace'in run'ları)         | ✅ brand_profile_id === activeWorkspace.id | UYUMLU |
| Scoring run seçici (dropdown)                                    | ✅                                         | UYUMLU |
| "İlgi Skoru Hesapla" butonu                                      | ✅ handleCompute                           | UYUMLU |
| Sadece confirmed profile varsa hesaplama                         | ✅ hasConfirmedProfile kontrolü            | UYUMLU |
| Relevance score tablosu (keyword, score, matched_anchor, method) | ✅                                         | UYUMLU |
| Summary istatistikleri (ortalama, max, min, count)               | ✅                                         | UYUMLU |
| Min score filtresi (range slider)                                | ✅                                         | UYUMLU |
| Sıralama (sort)                                                  | ✅ keyword, score, anchor, method          | UYUMLU |
| Next step → /channels                                            | ✅                                         | UYUMLU |
| Hata gösterimi                                                   | ✅ error-banner                            | UYUMLU |

### Eksik / Sapma:

- Plan.md'de workspace pill gösteriminden bahsedilmiyor ama bu ek UX iyileştirmesi — plan'a aykırı değil.
- "Sonraki: Kanallar" butonu plan.md'de belirtilmemiş ancak workflow'un doğal devamı.

**Sonuç: ✅ TAM UYUMLU** (fazladan UX iyileştirmeleri mevcut, plan'a aykırı değil)

---

## 13. frontend/src/pages/Relevance.css

### plan.md ile uyum:

| plan.md'de belirtilen                                 | Kodda var mı? | Durum  |
| ----------------------------------------------------- | ------------- | ------ |
| plan.md'de CSS detayı yok                             | N/A           | N/A    |
| Stil tanımları Relevance.tsx'in HTML yapısıyla uyumlu | ✅            | UYUMLU |

### Eksik / Sapma:

- plan.md CSS spesifikasyonu içermez — stil dosyasının varlığı plan'a aykırı değil.

**Sonuç: ✅ UYUMLU** (plan CSS detaylandırmaz, kod kendi HTML yapısıyla tutarlı)

---

## Genel Değerlendirme

| #   | Dosya                                                            | Uyum      |
| --- | ---------------------------------------------------------------- | --------- |
| 1   | plan.md                                                          | Referans  |
| 2   | migrations/versions/f08125d66a33_workspace_phase_a_structural.py | ✅ TAM    |
| 3   | app/core/keyword_normalize.py                                    | ✅ TAM    |
| 4   | app/core/workspace.py                                            | ✅ TAM    |
| 5   | tests/unit/test_keyword_normalize.py                             | ✅ TAM    |
| 6   | app/core/scoring/score_engine.py                                 | ✅ TAM    |
| 7   | app/schemas/brand_profile.py                                     | ✅ TAM    |
| 8   | app/core/site_analyzer/profile_extractor.py                      | ✅ TAM    |
| 9   | frontend/src/stores/brandStore.ts                                | ✅ TAM    |
| 10  | frontend/src/components/Layout.tsx                               | ✅ TAM    |
| 11  | frontend/src/App.tsx                                             | ✅ TAM    |
| 12  | frontend/src/pages/Relevance.tsx                                 | ✅ TAM    |
| 13  | frontend/src/pages/Relevance.css                                 | ✅ UYUMLU |

### Önemli Bulgular:

1. **Hiçbir sapma tespit edilmedi.** 13 dosyanın tamamı plan.md'de belirtilen özellikleri eksiksiz karşılıyor.
2. **Migrations** plan'daki tüm schema değişikliklerini (yeni kolonlar, yeni tablo, constraint'ler, index'ler) içeriyor.
3. **Scoring engine** plan'daki tüm yeni feature'ları (brand*profile_id, enable*\* flags, keyword_selection_mode, metrics_snapshot) destekliyor.
4. **Keyword normalizasyonu** plan'daki edge-case sıralamasını (translate → lower → NFKD) birebir uyguluyor.
5. **Frontend** plan'daki workspace-scoped routing, store, relevance sayfası ve layout'u eksiksiz implemente ediyor.
6. **Profile extractor** plan'daki AI prompt'ları, competitor validation ve anchor generation'ı içeriyor.

### Sonuç: ✅ TÜM 13 DOSYA plan.md İLE UYUMLU
