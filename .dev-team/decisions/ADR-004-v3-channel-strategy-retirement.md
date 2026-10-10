# ADR-004 — V3 Kanal Stratejisi Emekliliği ve Sözleşme Sadeleştirmesi

Date: 2026-09-22 · Status: **KABUL EDİLDİ** · Kapsam: **V3 Production Engine**

## 1. Bağlam (Context)

Digitus Engine v2.1 geçiş döneminde `BrandProfile` üzerine `channel_strategy` (`product_definition`, `content_strategy`, `social_mode`) ve buna bağlı `strategy_version` sürümleme/freshness mekanizması eklenmişti.

Ancak kilitli ve deterministik **v3 Production Engine** mimarisinde:
1. V3 motoru firma ve ürün bilgisini doğrudan ve tek kaynak olarak onaylı marka profili (`profile_data`: `products`, `services`, `company_name`, `sector`, `target_audience`) üzerinden alır.
2. V3 relevance ve firma bağlamı deterministik `firm_block` üzerinden oluşturulur ve `firm_block_sha256` ile izlenir.
3. V3 ADS, SEO ve SOCIAL algoritmaları kilitlenmiş olup, prompt veya motor düzeyinde `content_strategy` veya `social_mode` girdisi tüketmez.
4. Kanal Stratejisi UI formu ve onay adımı, gerçekte v3 algoritmasına etki etmeyen yapay bir ara katman ve kullanıcı sürtünmesi (onay butonu kilitlenmesi, gereksiz 409 hataları) oluşturmaktaydı.

## 2. Kararlar (Decisions)

### 2.1. Sözleşmenin Emekliye Ayrılması
- `product_definition`, `content_strategy` ve `social_mode` (`hype | authority`) aktif v3 sözleşmesinden tamamen çıkarılmıştır.
- Kanal Stratejisi kullanıcı arayüzünden (ProfileReviewCards, BrandProfile, StartAnalysisModal) tamamen kaldırılmıştır.
- `GET /workspaces/{id}/strategy` ve `PUT /workspaces/{id}/strategy` API endpoint'leri ve şemaları (`ChannelStrategyUpdateRequest`, `ChannelStrategyResponse`) emekliye ayrılmıştır (404).

### 2.2. V3 Akışında Strateji Bağımsızlığı
- V3 hiçbir aşamada (run oluşturma, yürütme, teslimat, finalize) onaylı kanal stratejisi aramaz, okumaz ve yazmaz.
- `channel_strategy=None` ve `strategy_version=0` olan marka profilleri v3 analizini eksiksiz tamamlar.
- Final teslimatında `run.channel_pool_strategy_version` yazılmaz; sonuç özetinde `strategy_version` alanı yer almaz.
- V3 `ChannelAssignmentAttempt` ve execution manifest kayıtları strateji sürümü veya snapshot'ı taşımaz.

### 2.3. Freshness Otoriteleri
- V3 için `strategy_stale` daima `False` olarak dondurulmuştur. Strateji kolonu değişiklikleri v3 havuzlarını bayatlatmaz.
- V3 havuz tazeliği (freshness) yalnız gerçek v3 otoritelerine dayanır:
  - **Relevance / Firma Tazeliği:** `firm_block_sha256` (profildeki ürün/hizmet ve firma alanlarının kanonik hash'i).
  - **Politika Tazeliği:** `policy_version` (onaylı dışlama temaları ve rakip kararları).
  - **Görev Durumu:** Celery `TaskResult` durumu.

### 2.4. Eski Authority Kapısının İptali (Sessiz Dönüşüm Değildir)
- Eski v2.1 sözleşmesindeki "authority sessizce hype'a çevrilmez; v3 + authority + social -> 409 SOCIAL_AUTHORITY_EXPERIMENTAL" kuralı iptal edilmiştir.
- **Bu bir `authority → hype` dönüşümü değildir:** V3 SOCIAL motorunda `authority` veya `hype` diye bir algoritmik ayrım veya girdi bulunmamaktadır. V3 SOCIAL tamamen kilitli `NormBounds` + V4 dört boyut + V5 niyet mimarisiyle çalışır.
- Dolayısıyla kaldırılan şey algoritmik bir dönüşüm değil, kullanılmayan bir mod seçimi ve buna bağlı yapay ret kapısıdır. Workspace üzerinde tarihsel bir `authority` kaydı olsa dahi v3 SOCIAL analizi engellenmeden başarıyla çalışır.

### 2.5. İçerik Grounding ve Firma Bilgisi Bütünlüğü
- ADS RSA ve SOCIAL içerik üreticileri ürün ve hizmet iddialarını (`load_product_definition`) onaylı marka profilinin `profile_data` (`products`, `services`, `brand_summary`) alanlarından almaya devam eder.
- Deterministik claim doğrulama (`find_ungrounded_claims`) ve onaylı firma gerçekleri (`product_facts`) tam korunmaktadır.

## 3. Sonuçlar ve Etkiler (Consequences)

1. **Kullanıcı Deneyimi:** Profil onayı tek HTTP isteğine (`approveProfile`) inmiş, gereksiz formlar ve kilitlenmeler ortadan kalkmıştır.
2. **Mimari Yalınlık:** V3 motoru ile legacy v2/v2_1 arasındaki sınır netleşmiş, v3 tamamen bağımsız ve deterministik hale gelmiştir.
3. **Geriye Dönük Uyumluluk:** Veritabanı kolonları (`channel_strategy`, `strategy_version`, `channel_pool_strategy_version`) tarihsel v2/v2_1 verilerinin korunması amacıyla fiziksel olarak silinmemiş; ancak v3 çalışma zamanında bu kolonlara bağımlılık sıfırlanmıştır. Fiziksel kolon temizliği ayrı bir migrasyon fazına bırakılmıştır.
