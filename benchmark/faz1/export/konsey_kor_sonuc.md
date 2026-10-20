# Kör konsey kıyası — sonuç (eşleme açıldı)

Tarih: 12.10.2026. Üç dış hakem, hangi sistemin hangi yapay zeka olduğunu bilmeden, A/B/C harfli
çıktıları plan §7.3 rubriğiyle değerlendirdi. Girdi dosyaları `benchmark/council_in/faz1_kor_*.md`
(sağlayıcı adları temizlenmiş; firma adları da gizlendi), ham hakem metinleri `benchmark/faz1/konsey_*.md`.

**Hakemler ve bağımsızlık:**
- **Codex (OpenAI gpt-5.6-sol)** — tarafsız üçüncü taraf. Konsey yolunda dosya argüman sınırına takıldı; aynı
  kör dosya **stdin** ile doğrudan verildi (`konsey_codex_*.md`).
- **DeepSeek V4 Pro** — tarafsız; planın asıl hakem adayı.
- **Antigravity (Gemini 3.8 Flash)** — **çıkar çatışmalı**: kıyaslanan sistemlerden biri Gemini'nin kendisidir
  (plan K4: "Claude ve Gemini hakem olamaz"). Hükmü bilgi amaçlı verildi, ağırlıklandırılmadı.

Gizli eşleme (hakemler görmedi): **Hissefy ws21:** A = G/G/C, B = Aday, C = G/G/G.
**Digitus ws29:** A = Aday, B = G/G/G, C = G/G/C.
(G/G/G = her aşama Gemini; Aday = seçim Haiku 5.5 + akıl yürütme/içerik Sonnet 5.5; G/G/C = seçim Gemini, içerik Sonnet 5.5.)

## Genel hüküm (eşleme açılmış)

| Firma | Codex (tarafsız) | DeepSeek (tarafsız) | Antigravity/Gemini (çatışmalı) |
|---|---|---|---|
| Hissefy | **Aday** > G/G/C > G/G/G | **Aday ≈ G/G/C** (ayırt edilemez) > G/G/G | **Aday** > G/G/G > G/G/C |
| Digitus | **G/G/C** > Aday > G/G/G | **G/G/C** > Aday > G/G/G | **G/G/G** > G/G/C > Aday |

İki tarafsız hakem iki firmada da **Claude içerikli bir konfigürasyonu** birinci, **yalnız-Gemini (G/G/G)**
konfigürasyonunu sonuncu sıraya koydu. Gemini koltuğu Hissefy'de Aday'ı birinci seçti; Digitus'ta kendi
sistemini birinci, Aday'ı sonuncu seçti.

## Bölüm başına en iyi (eşleme açılmış)

| Bölüm | Hissefy — Codex / DeepSeek / Gemini | Digitus — Codex / DeepSeek / Gemini |
|---|---|---|
| ADS havuzu | G/G/C / G/G/C≈G/G/G / G/G/G | G/G/C / G/G/C / G/G/C |
| SEO havuzu | **G/G/C** / **Aday** / **Aday** | **Aday** / **Aday** / **Aday** |
| SOCIAL havuzu | Aday / Aday / Aday | G/G/C / G/G/C≈Aday / G/G/C |
| RSA (ayrı tur, tüm gruplar) | **Aday** / **Aday** / G/G/G | **G/G/C** / **G/G/C** / **G/G/C** |
| Blog | Aday / Aday≈G/G/C / Aday | G/G/C / G/G/C / G/G/G |
| Sosyal medya içeriği | Aday / Aday / G/G/G | Aday / Aday / G/G/G |

Okuma notları:
- **SEO havuzu:** Aday (Haiku seçim) iki firmada 6 hükmün 5'inde birinci. Tek istisna Hissefy'de Codex'in G/G/C'yi seçmesi.
- **Blog (Sonnet içerik):** iki tarafsız hakem iki firmada da Claude içerikli konfigürasyonu birinci koydu; Gemini
  koltuğu Digitus'ta kendi blogunu birinci seçti.
- **Sosyal medya içeriği:** tarafsız hakemler iki firmada da Aday'ı birinci koydu, buna karşın Claude
  konfigürasyonlarında ürünün iddia/format doğrulamasını geçip kaydedilen içerik sayısı daha azdı (Hissefy 9/5/2,
  Digitus 7/6/3); hakemler eksik içerikleri puan kırarak değerlendirdi. Gemini koltuğu "eksiksiz üretim"i öne
  alıp G/G/G'yi seçti.
- **RSA:** Hissefy'de tarafsız hakemler Aday'ı, Digitus'ta üç hakem de G/G/C'yi birinci koydu. İlk turda RSA
  bölümü anahtar eşleşmesi yüzünden boş/eksik çıktığı için ("A'da 2/3 eksik" yorumu bir yapaylıktı) RSA ayrı,
  tüm gruplarla ikinci tur yapıldı; tablo o turu gösterir.
- **ADS havuzu** Digitus'ta 3/3 G/G/C (Gemini seçim) → seçim aşamasında Gemini'nin ADS havuzu daha iyi bulundu;
  Hissefy'de karışık.

## Hakemlerin alıntıladığı somut kusurlar (seçme)

- Hissefy: "bist 100 investing", "invest bist" (üçüncü taraf marka) havuzlarda; "Hissefy ile Hisse Satın Al"
  yanıltıcı ürün vaadi (G/G/G RSA, Antigravity); başlık kalıplarının tekrarı (G/G/C RSA, DeepSeek).
- Digitus: "en iyi seo ajansı" süperlatif anahtar kelime (Aday ADS havuzu); "Araştırmalara göre ... ölçülebilir
  artış" dayanaksız genelleme (G/G/G blog); "sssürecin" yazım hatası (Aday blog).

## Sınırlar

- Üç hakem de LLM; insan (patron) tercihi alınmadı. Plan G2 kapısı için insan çifti gerekir.
- Blog metinleri 2600 karakterle kesildi; havuzlar ilk 40 kelimeyle sınırlı; sosyal içerik tüm ortak anahtarlar.
- Tek koşu, tek firma çifti; istatistiksel güç yok. Bu bir **yön bilgisidir**, kalite kanıtı değil.
- Maliyet ve süre bu kıyasın dışındadır (bkz. `README.md`): Claude konfigürasyonları Gemini'den 2–2,5× pahalı ve
  daha yavaş; Haiku bir koşuda kimlik uydurma hatasıyla düştü.
