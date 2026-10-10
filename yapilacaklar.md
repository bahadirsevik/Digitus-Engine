# Yapılacaklar

Yalnız V3 motoruna (app/core/engine) ve bugünkü ürüne uyan açık işler. Eski v2
hattına ait maddeler (intent/prefilter parse, embedding relevance formatı ve
fallback'i, v2 skor formülleri, hot_sale/GT tanımı, kanal stratejisi, eski run
korumasının artıkları, run-15 doğrulamaları) 09.10'da listeden çıkarıldı.

**Ayrıntılı çözüm planı: `plan_yapilacaklar.md`** (faz, kanıt, adım, efor, kararlar).
09.10 incelemesinde ayrıca bulunanlar: V3 run'larında boşa embedding harcaması
(§3.2), Türkçe run adında Excel indirmesinde olası 500 (§1.2), görev takibinin
run'lar arasında karışması (§1.3).

## Güvenlik (öncelikli, temizlik değil)

- **S-1:** Parola değişimi eski oturumları kesin olarak kesmiyor.
  - Redis yokken iptal sessizce atlanıyor.
  - Oturum TTL'i her kullanımda uzuyor; eski oturum süresiz yaşayabilir.
  - Ayrıntı ve öneri: `plan_yapilacaklar.md` "Güvenlik" bölümü.
  - Durum: auth dalı (`deploy/auth-lean-merge`), onay bekliyor.

## Kodda doğrulanmış açıklar (Paket 1–4 ile çoğu kapandı; güncel durum planda)

- **Google Ads API importunda çöp/GTIN filtresi yok**: `_is_junk_keyword`
  yalnız CSV yolunda (`app/core/csv_import/google_ads_parser.py`).
- **Reklam grubunda negatif ∩ hedef kelime kontrolü yok**: ADS üretimi hâlâ
  `KeywordGrouper` ile grupluyor; bir grup kendi hedef kelimesini negatife
  alabiliyor (Sensodyn örneği).
- **Profil analizinde tek-uçuş kilidi yok**: 15 dk'yı aşan analiz tekrar
  denenirse iki koşu birbirini ezebilir (ADR-002 artık riski).
- **Altyapı**: CI yok, backend linter (ruff) / mypy yok, ESLint 8 EOL.

## Temizlik (v2'den kalan ölü kod)

- Route'suz eski `Scoring` / `Channels` / `Relevance` sayfaları (~1.570 satır)
  ve kullanılmayan CSS.
- `AnalysisProgress` hâlâ v2 aşamalarını (ilgi skoru, screening) anlatıyor;
  V3 aşamalarına uyarlanmalı.
- Corpus screening V3'te kullanılmıyor (ayrı screening worker'ı da var):
  kaldırılsın mı, karar gerekli.

## Doğrulanmadı (notlardan)

- Marka profili P2.15: onaylı profildeki iki "Dışlanacak Temalar" kutusunun
  etiketi/UX'i kafa karıştırıyor (`exclude_themes` V3 prompt bağlamına giriyor).
- `profile/confirm` ucunda workspace doğrulaması eksik.
- Export'ta excel/xlsx adlandırma tutarsızlığı; bazı task anahtarları run
  bazlı değil.
- Migration `20260918_002` downgrade'i v3 satırında ham CHECK hatası veriyor.
