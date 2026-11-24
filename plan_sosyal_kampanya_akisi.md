# Sosyal Kampanya Akışı — Uygulama Planı (rev.2.1, 17.11.2026 — Codex rev.2 incelemesi: "düzeltmelerden sonra uygulamaya geçilebilir"; 5 düzeltme işlendi, §10b)

Kaynak: ajans istekleri — "yüzde indirim mi bundle mı, ne tür CTA kullan", "reklamda şuna
odaklan", "kampanya fikirleri", "görsel oluşturacaksa prompt üretsin", "sosyal medyadan
beslensin"; Codex rev.1 incelemesi (7 bulgu, §10'da tek tek). Kod değişikliği henüz yok.
Kod tabanı: `Digitus-Engine-lean` / `deploy/lean-taslak`.

**Kapsam cümlesi:** Yeni bir akış YAZILMAZ. Mevcut sosyal brief akışı bir "kampanya modu"
kazanır. Kelimeler yalnız SOCIAL havuzundan gelir (`brief_eligibility.py` zaten zorluyor).
Sistem teklif **türü** önerir; teklif **koşullarını** yalnız kullanıcı verir.

```
SOCIAL kelimeleri → kampanya brief'i → kampanya açıları → alternatif kampanya fikirleri
→ fikir seçimi + TEKLİF KOŞULLARINI NETLEŞTİRME → içerik + görsel prompt'u
```

## 0. Kararlar

| # | Karar | Durum |
|---|-------|-------|
| K1 | Kampanya ayrı tablo/sihirbaz değil; `SocialBrief.campaign_spec` (NULL = bugünkü organik brief, davranış birebir aynı) | Önerilen |
| K2 | İki giriş yolu: **"Kampanyam belli"** (kullanıcı teklifi girer) ve **"Kampanya öner"** (sistem alternatif sunar, kullanıcı seçer). İkisi de aynı tek mekanizmaya bağlanır: K3 | Codex #1 — kabul |
| K3 | Teklif koşulları **içerik üretim isteğinde** kesinleşir (bugün `trusted_brand_usp`'nin geldiği yer). "Belli" yolunda brief'teki taslak ön-doldurulur; "öner" yolunda kullanıcı seçtiği fikrin koşullarını o an girer veya **"teklifsiz devam et"** der | Codex #2 — kabul |
| K4 | Son içerikte yer tutucu (`[indirim oranı]`) **YOK**. Koşul eksikse içerik üretimi başlamaz (400) ya da teklifsiz üretilir | Codex #2 — kabul |
| K5 | Teklif koşulları yapılandırılmış taşınır (tür, değer, **kapsam**, şartlar, bitiş); genel marka USP'sinden AYRI alan. `trusted_brand_usp`'ye karıştırılmaz | Codex #3 — kabul |
| K6 | Kampanyaya özel **teklif tutarlılık kontrolü** (§5) — mevcut genel iddia denetimine bırakılmaz | Codex #3 — kabul (rev.1 varsayımım yanlıştı) |
| K7 | Teklif türü önerisi ürün bağlamına dayanır; veri yoksa gerekçe **koşullu** yazılır; tür sayısı zorlanmaz (1 de olabilir) | Codex #4 — kabul |
| K8 | Brief'te kullanım yeri: **ücretli reklam / organik gönderi** (tek seçim). "İkisi" ilk sürümde yok — gerekçe §10 | Codex #5 — kısmen |
| K9 | CTA seçenekleri **hedef kanaldan türetilir** (WhatsApp → "satın al" sunulmaz); serbest enum değil | Codex #5 — kabul |
| K10 | `image_prompt` iddia denetiminden muaf; **rakam yasağı kaldırıldı**. Oranı kod ekler | Codex #6 — kabul |
| K11 | Ek AI çağrısı yok; deneme başı tavanlar değişmez. Bayrak: `ENABLE_SOCIAL_CAMPAIGN_BRIEF` | Önerilen |

## 1. Brief girdisi

"Kampanya" bölümü (kapalıysa bugünkü akış):

| Alan | Değer | Not |
|------|-------|-----|
| `mode` | `defined` / `suggest` | K2 |
| `usage` | `paid` / `organic` | K8; metin tonu ve uzunluğu buna göre |
| `promoted_item` | serbest metin ≤200, zorunlu | Tanıtılan ürün/hizmet/paket |
| `audience` | serbest metin ≤200, opsiyonel | |
| `objective` | satış / talep toplama / bilinirlik | |
| `destination` | site / form / WhatsApp / telefon / mağaza / profil | K9: CTA listesi bundan türer |
| `disallowed_offer_types` | çoklu seçim | "İndirim yapamayız" — bu türde fikir üretilmez |
| `offer_draft` | §2 yapısı, yalnız `mode=defined` | İçerik adımında ön-doldurulur, orada onaylanır |
| `focus_note` | serbest metin ≤300 | "Reklamda şuna odaklan"; güvenilir girdi SAYILMAZ |

## 2. Teklif koşulları (`offer_terms`) — içerik isteğinde kesinleşir

```
type        : percent_discount | bundle | free_shipping | gift | none
value       : "%20" / "2 al 1 öde" / "ücretsiz kargo"      (type≠none ise zorunlu)
applies_to  : "Başlangıç paketi" / "tüm ürünler"            (zorunlu — kapsam)
conditions  : "500 TL üzeri", "kod: YAZ20"                  (opsiyonel)
ends        : tarih                                         (opsiyonel; geçmiş tarih 400)
```

- İçerik isteği (`contents/async`) `offer_terms` taşır. Bir istekte **tek teklif**; `type≠none`
  ise seçilen fikirlerin teklif türü `offer_terms.type` ile aynı olmalı (farklıysa 400, açık
  mesaj). Arayüz fikirleri türe göre gruplar. Farklı türler ardışık isteklerle üretilir.
- **`type=none` açık istisnadır** ("teklifsiz devam et"): tür eşleşmesi aranmaz, her fikirle
  seçilebilir. Fikrin `offer_type` / `offer_rationale` alanları içerik prompt'una **hiç
  girmez** (devre dışı; "eski teklif + teklifsiz" birlikte verilmez); fikrin yalnız
  başlığı/açıklaması/CTA'sı kullanılır. İçerikte hiçbir teklif dili geçemez.
- **Bitiş tarihi**: verilmişse metne **açık tarih** olarak yazılır ("30 Kasım'a kadar").
  Göreli aciliyet ("bugün son", "son saatler", "son gün") tarih olsa da olmasa da YASAK.
  Geçmiş tarihli teklifle üretim, yeniden üretim ve revizyon başlamaz (400 / `offer_expired`).
- Koşullar, `trusted_brand_usp`'nin worker'a taşındığı aynı yoldan (attempt girdi snapshot'ı)
  gider ve içerik satırına **snapshot** olarak yazılır — repair, revizyon ve export buradan okur.

## 3. Veri modeli (tek migration, idempotent — `20260712_001` kalıbı)

- `social_briefs.campaign_spec` JSON NULL — §1 alanları + `spec_version`.
- `social_ideas.campaign_payload` JSON NULL — `{offer_type, offer_rationale, cta_type}`.
- `social_contents.campaign_payload` JSON NULL — `{offer_terms, cta_type, usage}` (üretimde kullanılan snapshot).
- `social_contents.image_prompt` TEXT NULL.

Yeni tablo yok. Fikir satırları insert-only kalır (kesinleşen koşul fikre değil içeriğe yazılır).
Legacy sınırı: bu kolonlar yalnız `/social/briefs/*` akışından yazılır.

## 4. Aşamalar

**Kategori = kampanya açısı.** `category_contract.py` DEĞİŞMEZ; `category_type` kapalı listesi
(educational, product_benefit, social_proof, brand_story, community, trending) korunur.
Beklenen eşleme: fiyat/değer avantajı → product_benefit; müşteri deneyimi → social_proof;
sezon/gündem → trending; "nasıl seçilir/kullanılır" → educational. **Faz 2 başında 5–10
gerçek brief ile doğrulanır**; açılar listeye oturmuyorsa sözleşme o zaman genişletilir
(şimdiden değil).

**Fikir = kampanya fikri.** Her fikir `offer_type` + `offer_rationale` + `cta_type` taşır.
- Fikir metninde **sayı/oran yok**, yer tutucu da yok — yalnız mekanizma ("paket alana ek ürün").
- `mode=defined`: tüm fikirler `offer_draft.type`'a uyar; uymayan bugünkü gibi tek tek atılır.
- `mode=suggest`: yalnız `disallowed_offer_types` dışındaki, ürün bağlamına uyan türler.
  Tür sayısı zorlanmaz. Gerekçe koşullu yazılır: marj/sepet verisi olmadığından "daha çok
  kazandırır" gibi sonuç iddiası YASAK; "birlikte kullanılan ürünler varsa değerlendirilebilir"
  kalıbı. Bu kural prompt + `idea_contract.py`'de (gerekçede kazanç/garanti dili → fikir atılır).
- `cta_type` yalnız `destination`'ın izin verdiği kümeden (kod doğrular).
- **Fikir retry** (`idea_retry_*`): `campaign_spec` brief'ten okunur; retry fikirleri aynı
  kurallarla üretilir (retry snapshot + worker input'a alan eklenir).

**İçerik = metin + görsel prompt'u.**
- Prompt'a `offer_terms` bloğu "yalnız bu koşulları, aynen bu kapsamla kullan" talimatıyla girer.
- `cta_text` fikrin `cta_type`'ına ve `destination`'a uyar.
- `usage=paid`: daha kısa, teklif ve CTA öne çekilmiş metin. Reklam paneli alan limitleri yok (§8).
- `image_prompt`: bkz. §6.
- **Repair** (`content_item_execution.py`, tek birleşik onarım): aynı `offer_terms` repair
  prompt'una da girer; §5 bulguları repair gerekçesine eklenir. Çağrı tavanı (öge başı 2) değişmez.

## 5. Teklif tutarlılık kontrolü (yeni, saf modül: `app/core/social/offer_consistency.py`)

Neden ayrı: `evaluate_social_content_quality` (rev.4) doğrulanamayan iddiayı **reddetmez**,
uyarıyla kabul eder; repair'i yalnız "risk" sınıfı açar. Uydurma "%30" bu yüzden geçer.

Deterministik, görünür metinlerin tamamında (hook, caption, CTA, hashtag, format_payload):

**Yalnız teklif bağlamındaki ifadeler değerlendirilir** — metindeki her sayı kampanya koşulu
değildir. Teklif ifadesi = teklif kalıbına bağlı sayı/ibare: "% … indirim", "… TL indirim",
"N al M öde", "ücretsiz/bedava kargo", "hediye", "… üzeri" eşikleri, kupon kodu. Çıplak ürün
fiyatı ("Ürün 1.000 TL") teklif ifadesi DEĞİLDİR; o genel iddia denetiminin işidir.
Karşılaştırma dayanağı: `value` + `conditions` + onaylı `product_facts`.

| Bulgu | Tespit | Sonuç |
|-------|--------|-------|
| Koşulda olmayan teklif ifadesi | teklif kalıbı `value`/`conditions`'ta yok (Türkçe sayı normalizasyonu: mevcut `validators.py` yardımcıları) | **sert** |
| Zorunlu koşul düşmüş | metin teklifi (`value`) anıyor ama `conditions`'taki eşik ("500 TL üzeri") veya kod ("YAZ20") metinde yok | **sert** |
| `type=none` iken herhangi bir teklif kalıbı | aynı kalıplar | **sert** |
| Göreli aciliyet dili | sabit liste ("bugün son", "son gün", "son saatler") — tarihten bağımsız | **sert** |
| Açık kapsam çelişkisi | genelleme kalıbı ("tüm ürünlerde", "her şeyde", "bütün …") var VE `applies_to` genel değil | **sert** |
| Belirsiz kapsam | teklif anılıyor ama `applies_to` metni içerikte geçmiyor, genelleme kalıbı da yok | kabul + `offer_scope_review` uyarısı |

**Sert** = tek birleşik repair'e girer; repair sonrası sürüyorsa kabul edilmiş `SocialContent`
olarak **KAYDEDİLMEZ**. Deneme kaydı, `reason_code=offer_mismatch` ve ihlal ayrıntısı
`warnings`'te korunur; aynı batch'teki diğer başarılı içerikler etkilenmez (mevcut `partial`
davranışı). Bu, rev.4'ün "belirsiz içerik kaybolmaz" ilkesinden bilinçli sapmadır: yanlış
teklif belirsiz iddia değil, kullanıcının verdiği veriyle çelişen somut hatadır. Yalnız
kampanya içeriklerinde geçerli. (Karar verildi — Codex rev.2 de aynı yönde.)

**Genel iddia denetimiyle bağ:** kesinleşen `offer_terms` (value + conditions + ends),
`SocialContentGroundingContext`'e ayrı alan olarak eklenir ve whitelist'e girer; böylece
kullanıcının onayladığı "%20" genel denetimde `claim_unverified` uyarısı ALMAZ. USP ile
karışmaz (ayrı alan). Aynı snapshot (içerik satırındaki `campaign_payload`) ilk üretim,
repair ve revizyonda kullanılır.

Sınırları (plana yazılı, gizlenmiyor): "yarı fiyatına" gibi dolaylı ifadeler kalıp dışı
kalabilir — prompt kuralı + ajans incelemesi kalan savunmadır. Kusursuz anlamsal denetim
hedeflenmiyor.

## 6. Görsel prompt'u

`visual_suggestion` (mevcut) = insana yönelik kısa stil notu. `image_prompt` (yeni) =
**görsel üretim aracına kopyalanacak** ayrıntılı talimat. İçerir: konu/ürün, kompozisyon,
ortam, ışık, marka stili, **metin bindirilecek boş alan**. İngilizce.
- Görselin içinde yazı yok (Türkçe yazı modellerde bozuk çıkıyor; metin tasarımda eklenir).
- Gerçek kişi adı, rakip marka/logo yok.
- Oran kodla eklenir: post 4:5, story/reels/short 9:16, video kapağı 16:9; carousel: kapak + stil satırı.
- Denetim: yalnız uzunluk + boş olmama. İddia/rakam denetimi YOK (teknik talimat, reklam metni değil).

## 7. Dokunulacak yollar (rev.1'de eksikti)

| Yol | Değişiklik |
|-----|-----------|
| Brief oluşturma | `schemas/social_brief.py`, `brief_validation.py` (saf: enum, uzunluk, mode↔offer_draft, destination↔CTA), `brief_service.py` |
| Kategori | `brief_category_prompt.py` (yalnız prompt) |
| Fikir + retry | `brief_idea_prompt.py`, `idea_contract.py`, `idea_persistence.py`, `idea_worker_input.py`, `idea_retry_snapshot.py`, `idea_retry_worker_input.py`, `idea_retry_persistence.py` |
| İçerik + repair | `brief_content_prompt.py`, `content_contract.py`, `content_worker_input.py`, `content_item_execution.py`, `content_persistence.py`, yeni `offer_consistency.py`; istek şeması `SocialBriefContentsGenerateRequest.offer_terms` |
| Revizyon | `content_revision.py`: `image_prompt` → `production` kapsamına eklenir (yalnız o kapsam seçilirse değişir; metin revizyonunda **korunur** — mevcut kapsam-dışı karşılaştırması bunu zorlar). `campaign_payload` hiçbir kapsamda değişmez. Revizyon önerisi de §5'ten geçer (snapshot içerik satırından); ihlalli öneri `revision_rejected` |
| Okuma | `brief_state_read.py`, `idea_read.py`, `idea_retry_read.py`, `content_read.py`, `content_history_read.py` |
| Export | `exporters/data_collector.py`, `social_format.py`, dört biçim (DOCX/PDF/XLSX/CSV) + `/social/briefs/{id}/export` |
| Frontend | brief formu (iki yol), fikir kartı (tür + CTA + gerekçe; türe göre gruplama), **içerik öncesi teklif netleştirme paneli**, `SocialContentCard` (görsel prompt'u + kopyala; teklif özeti), `SocialBriefDetail` |

## 8. Bilinçli olarak YOK

Gerçek görsel üretimi · sosyal hesap bağlantısı · reklam yayınlama · reklam paneli alan/karakter
eşlemesi · `usage=ikisi` · carousel'de slayt başına prompt · bütçe/hedefleme önerisi ·
hukuki denetim (yalnız sabit "kampanya koşullarını ve indirim öncesi fiyatı doğrulayın" notu;
metin ajansa teyit ettirilecek).

## 9. Fazlar ve kabul testleri

| Faz | İçerik |
|-----|--------|
| 1 | Migration + brief oluşturma/doğrulama + okuma + bayrak |
| 2 | Kategori eşleme doğrulaması (elle) → fikir + retry |
| 3 | `offer_consistency.py` + içerik + repair + revizyon |
| 4 | Frontend |
| 5 | Export + geçmiş |

Kabul testleri (her biri otomatik):
1. **Organik akış korunur**: `campaign_spec` yokken prompt girdisi ve çıktı alanları değişmez.
2. **Yalnız SOCIAL havuzu**: kampanya brief'i havuz dışı kelimeyle 400 (mevcut eligibility, regresyon).
3. **Verilmeyen koşul eklenmez**: `%20` verildi, içerik `%30` diyor → repair; hâlâ varsa kaydedilmez,
   batch'teki diğer içerikler kaydedilir. `type=none` + teklif kalıbı → aynı.
   - Yanlış pozitif yok: `value="100 TL indirim"`, `conditions="500 TL üzeri"`, içerikte
     "Ürün 1.000 TL", "500 TL üzeri", "100 TL indirim" → temiz.
   - Koşul düşmesi: `conditions="kod: YAZ20"`, içerik `%20`'yi anıyor ama kodu yazmıyor → sert.
   - Onaylı `%20` genel denetimde `claim_unverified` üretmez.
4. **Kapsam korunur**: `applies_to="Başlangıç paketi"`, içerik "tüm ürünlerde %20" → repair;
   sürerse kaydedilmez. Kapsam metni hiç geçmiyor ama genelleme de yok → `offer_scope_review`.
5. **Koşul eksikse üretim başlamaz**: `offer_terms`'siz kampanya içerik isteği 400; son içerikte yer tutucu olamaz.
   - `type=none` indirim fikriyle kabul edilir; prompt girdisinde fikrin `offer_type`/`offer_rationale`'ı YOK.
   - `type≠none` ve fikir türü farklı → 400.
   - Tarih: `ends` verildiyse içerikte açık tarih var; "bugün son" → sert. Geçmiş `ends` ile
     üretim ve revizyon başlamaz.
6. **Retry**: retry fikirleri `campaign_payload` taşır, `disallowed_offer_types`'a uyar.
7. **Repair**: repair prompt girdisinde aynı `offer_terms` var.
8. **Revizyon**: `text` kapsamı → `image_prompt` ve `campaign_payload` bayt bayt aynı; ihlalli öneri reddedilir.
9. **Export**: dört biçimde teklif özeti + görsel prompt'u görünür; organik brief çıktısı değişmez.
10. **CTA**: `destination=whatsapp` iken "satın al" türü fikir atılır.
11. **Görsel prompt'u**: "two products", "4:5" içeren prompt kabul edilir.

Operasyon: Faz 2 ve 3 sonrası `celery_worker` restart (brief task kodu). Faz 3'te gerçek
modelle 5–10 brief elle denenir — çıktı büyüdüğü için JSON kesilmesi riski.

## 10. Codex rev.1 bulguları — değerlendirme

| # | Bulgu | Kodda doğrulama | Sonuç |
|---|-------|-----------------|-------|
| 1 | Kararı kullanıcıya baştan seçtirme; iki giriş yolu | — (ürün kararı) | Kabul → K2 |
| 2 | Öneri ile içerik arasında netleştirme adımı; son içerikte yer tutucu olmasın | İçerik isteği zaten fikir kimlikleri + `trusted_brand_usp` alıyor; doğal yeri orası | Kabul → K3, K4 |
| 3 | İddia denetimi uydurma indirimi reddetmez; teklif USP'den ayrı ve kapsamıyla taşınmalı | **Doğru.** `content_quality.py:392-463`: repair yalnız risk sınıfında; repair sonrası her zaman accept. Rev.1'deki "mevcut denetim keser" cümlem yanlıştı (CLAUDE.md'deki eski tarife dayanmıştım) | Kabul → K5, K6, §5 |
| 4 | Öneri ürün bağlamına dayanmalı; koşullu gerekçe; iki tür zorlanmasın | — | Kabul → K7, §1 |
| 5 | Ücretli/organik ayrımı; CTA yalnız enum ile doğrulanmasın | — | Ayrım + kanal-CTA kuralı kabul (K8, K9). **"İkisi" reddedildi**: `uq_social_content_idea_brief` fikir başına tek içerik zorluyor; "ikisi" ya bu kısıtı ya içerik sözleşmesini değiştirir ve çıktıyı ikiye katlar. İki ayrı brief aynı sonucu sıfır yeni kodla verir |
| 6 | Görsel prompt'unda rakam yasağı yanlış; oran ekleme kararıyla çelişiyor | **Doğru**, plan içi çelişki | Kabul → K10, §6 |
| 7 | Kapsam üç kolondan geniş: retry, repair, revizyon, export; revizyonda prompt korunmalı; kategori tipleri kapalı liste | **Doğru.** `content_revision.py:67-76` revizyon kapsam-bazlı; `category_contract.py:29` kapalı liste | Kabul → §4, §7; kolon sayısı 3 → 4 |

## 10b. Codex rev.2 bulguları — değerlendirme (hepsi plan-içi mantık; kabul)

| # | Bulgu | Sonuç |
|---|-------|-------|
| 1 | "Teklifsiz devam et" tür-eşleşme kuralıyla çelişiyor | `none` açık istisna; fikrin teklif mekanizması prompt'a girmez → §2 |
| 2 | Her sayı kampanya koşulu değil; koşulun düşmesi de teklifi değiştirir | Yalnız teklif bağlamı; dayanak `value`+`conditions`+`product_facts`; "zorunlu koşul düşmüş" satırı → §5 |
| 3 | "Kapsam değişmez" testi yalnız uyarı bekliyordu | Açık çelişki sert; belirsiz eşleşme uyarı → §5, test 4 |
| 4 | Bitiş tarihinin varlığı aciliyeti doğrulamaz; geçmiş tarih | Açık tarih yazdırılır, göreli aciliyet her durumda yasak, geçmiş tarih engellenir → §2 |
| 5 | Kesinleşen teklif genel iddia denetimine de ulaşmalı | Grounding bağlamına ayrı alan → §5 |

"Kaydedilmez" kararı kesinleşti. "Sosyal medyadan beslensin" = SOCIAL havuzu (kullanıcının
ilk mesajında açık); açık soru olmaktan çıktı.

## 10c. Codex rev.2.1 kapanış notları — plan incelemesi KAPANDI, uygulamaya geçilebilir

1. **Teklifsiz devam**: `offer_type`/`offer_rationale`'ı çıkarmak yetmeyebilir — fikir
   başlığı/açıklamasında da "bundle"/"indirim" geçebilir. Savunma zaten §5'te
   (`type=none` + teklif kalıbı → sert). Test: başlığında "indirim" geçen fikir + `type=none`
   → son içerik teklif kalıbı içermez (içerirse repair, sürerse kaydedilmez).
2. **Kalıp denetimi**: doğruluk yalnız model denemesine bırakılmaz. Faz 3'te
   `offer_consistency.py` için **sabit Türkçe test kümesi** (doğru / yanlış / koşulu eksik
   metinler; ek almış biçimler dahil: "%20'lik", "indirimli", "kargo bedava") yazılır;
   model denemesi bunu tamamlar.

## 11. Açık sorular

Yok. Teklif türü listesi ilk sürüm için yeterli: "ilk siparişe özel" ayrı tür değil,
`conditions` alanında ifade edilir; sadakat puanı sonraya.
