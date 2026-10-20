# Faz 1 çıktı paketi — Gemini ↔ Claude (Hissefy ws21, Digitus ws29)

Tarih: 11–12 Ekim 2026. Lean klon `deploy/lean-taslak`, plan `plan_claude_gemini_karsilastirma.md` rev.6
(patron kapsam kararı 11.10: karşılaştırma ve doğruluk değerlendirmesi patronda; bu paket yalnız sistem
çıktılarıdır). Hiçbir değerlendirme/hüküm içermez; kalite kıyasını siz yapacaksınız.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `ozet.md` / `ozet.xlsx` | Koşu başına istek sayısı, liste-fiyatı maliyet (Gemini / Claude), faz süreleri, içerik sayıları |
| `ws21_havuzlar.xlsx`, `ws29_havuzlar.xlsx` | Kanal başına (ADS/SEO/SOCIAL) üç konfigürasyonun final anahtar kelime havuzları yan yana + "Ortak" sayfaları (hangi kelime hangi konfigde var) |
| `ws21_icerikler.md`, `ws29_icerikler.md` | Aynı anahtar/kelime için üç konfigürasyonun ürettiği içerik alt alta: RSA grupları (başlıklar, açıklamalar, negatifler), blog (başlık, meta, metin), sosyal (hook, metin, hashtag). Sert kural ihlali varsa listelenir |
| `ws21_icerikler.xlsx`, `ws29_icerikler.xlsx` | Aynı içerikler tablo hâlinde (anahtar, konfig, alan, metin, kural durumu) |
| `profiller.md` | Site profil çıkarımı: aynı dondurulmuş içerikle Gemini 3.8 Flash, Claude Sonnet 5.5, Claude Haiku 5.5 |

Konfigürasyonlar: **G/G/G** = her aşama Gemini (üretimdeki hâl). **Aday** = seçim aşamalarının hacimli
olanları Claude Haiku 5.5, akıl yürütme aşamaları ve içerik Claude Sonnet 5.5. **G/G/C** = seçim Gemini,
yalnız içerik üretimi Claude Sonnet 5.5. Kapasiteler her koşuda aynı: ADS 60 / SEO 30 / SOCIAL 30.
Faz 1 kuralı gereği prompt'lar bayt-aynı (Claude için idiomatik prompt yazılmadı; bu Gemini lehine bir yanlılıktır).

## Koşular (scoring_run_id)

| Firma | G/G/G | Aday | G/G/C |
|---|---|---|---|
| Hissefy (ws21) | 76 | 79 | 82 |
| Digitus (ws29) | 78 | 81 (80 başarısız, bkz. bulgular) | 83 |

Ham artefaktlar: `benchmark/faz1/ws*_*.json` (mühürlü), profil: `benchmark/faz1/profile_compare.json`,
harcama defteri: `benchmark/faz1_budget_ledger.json`, dondurulmuş profil girdileri: `benchmark/profile_inputs/`.

## Maliyet (liste fiyatı; konsol faturasıyla mutabakat yapılmadı)

| Firma | G/G/G (Gemini) | Aday (Claude + Gemini) | G/G/C (Claude + Gemini) |
|---|---|---|---|
| Hissefy | 2,28 $ | 5,71 + 0,41 = 6,12 $ | 3,27 + 1,45 = 4,72 $ |
| Digitus | 2,81 $ | 6,69 + 0,46 = 7,15 $ | 3,42 + 1,83 = 5,24 $ |

Toplam harcama (başarısız iki koşu dahil): Claude 19,18 $ (tavan 50), Gemini 10,21 $ (tavan 20), DeepSeek 0.
Not: Aday'daki Haiku aşamaları ucuzdur; Claude maliyetinin büyük kısmı Sonnet içerik üretimi ve O aşamalarıdır.

## Süre (saniye, duvar saati)

| Koşu | Seçim (orkestrasyon) | ADS içerik | SEO blog (25–30 kelime) | Sosyal |
|---|---|---|---|---|
| ws21 G/G/G | 170 | 105 | 1278 | 95 |
| ws21 Aday | 467 | 121 | 2126 | 197 |
| ws21 G/G/C | 448 | 131 | 1771 | 168 |
| ws29 G/G/G | 332 | 137 | 1539 | 103 |
| ws29 Aday | 558 | 119 | 2361 | 259 |
| ws29 G/G/C | 231 | 151 | 1795 | 126 |

**Uyarı:** koşular ikişer paralel yürütüldü ve Gemini hız limitlerini paylaştı; ws21 G/G/C'nin seçim süresi
(448 s) bu yüzden ws21 G/G/G'den (170 s) çok farklı çıkmıştır. Süreleri "aynı eşzamanlılıkta" kıyas için
tek başına koşulması gerekir (plan G6b). Claude Sonnet blog üretimi Gemini'den yaklaşık 1,5× uzun sürdü.

## Teknik bulgular (hüküm değil, gözlem)

1. **Haiku kimlik uydurdu.** ws29 Aday ilk denemede (run 80) `family_a2` aşamasında Haiku, istenen listede
   olmayan bir kelime kimliği (11011, çalışma alanında hiç yok) döndürdü; iki denemede de aynı hata → motor
   üretimdeki kuralla fail-closed durdu (0,04 $). Tekrar koşu (run 81) sorunsuz tamamlandı. Gemini koşularında
   bu tür yapısal hata görülmedi.
2. **Tekrar (retry) sayısı:** Claude çağrılarında koşu başına 3–7 retry nedeni kaydedildi; Gemini'de 0–2.
   Kırpılma (max_tokens) hiçbir koşuda yok.
3. **Sosyal içerik kaydı:** fikir sayısı benzer (9–12) ama kaydedilen içerik Claude konfiglerinde daha az
   (Hissefy: G/G/G 9, Aday 5, G/G/C 2; Digitus: 7 / 6 / 3). Kaydedilmeyenler ürünün iddia/format doğrulamasından
   geçememiştir (`unresolved_idea_ids` artefaktta). Bu, kalite değil ürün sözleşmesine uyum farkıdır; içerikleri
   okuyarak yorumlanmalı.
4. **Blog sayısı** konfigler arası fark (25–30) SEO havuz büyüklüğünden gelir, üretim hatasından değil
   (`failed` boş).
5. **Profil çıkarımı:** üç model de 11 alanın hepsini doldurdu; aynı metin Claude'da ~2× daha fazla girdi
   tokeni (tokenizer farkı). Metinler `profiller.md`'de.
6. **Telemetri notu:** Gemini çağrılarında `ai_usage_events.provider` kolonu boş kalıyor (yalnız Claude
   dolduruyor); maliyetler model adından hesaplanır, etkilenmez. Küçük takip işi.

## Yapılmayanlar (kapsam kararı gereği)

DeepSeek hakem, kör patron kartları, patron etiketleriyle recall, G1–G7 kapı hükümleri, 1A aşama replay'i.
Altyapı hazır; istenirse koşulur. Faz 2 (Claude-idiomatik prompt) bu turda yok.
