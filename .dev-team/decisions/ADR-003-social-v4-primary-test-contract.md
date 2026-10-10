# ADR-003 — Social V4 Primary çekirdeği ölçüm sözleşmesi

Date: 2026-09-10 · Status: **v3.5.1 DONDURULDU** · Ücretli çağrı: **0**
Kanonik metin: `plan_social_v4_test.md` (656 satır)
Ham inceleme çıktıları: `benchmark/council/`

## Karar

Patronun `algoritma/Optimice_Social_Media_Icerik_Motoru_Nihai_Algoritma_Akis.docx`
dokümanındaki geometrik SOCIAL modelini, üç firma (Optimice ws21 / Dijital ws29 /
GR-7 ws30) üzerinde, **kayıtlı patron tercihini yakalama** metriğiyle ölçen ön-kayıtlı
bir test. Ölçtüğü şey "V4 sosyal olarak doğru mu" DEĞİL.

Asıl baraj **hacim-only** kontrolü. SEO V3 iki gün önce aynı barajda düştü.

## Onda karar verilen, kolay unutulacak şeyler

- **Primary kapısı `BC ≥ 60`** (kaynak §7 tablosu otorite; §11/§15 yalnız `<50` diyor —
  kaynak kendi içinde tutarsız, seçim kayıtlı). 50–59 yalnız Secondary/Review.
- **Relevance 0–1 saklanıyor** (`models.py:973`), `×100` çevrilip `<40` uygulanır.
  Ham değeri `<40` ile karşılaştırmak **her satırı SKIP eder**.
- **Çapa bir kapı değildir** — sentetik kontrol satırı, ayrılmış negatif id, hiçbir
  paydaya girmez, yalnız drift teşhisi. Afin düzeltme yok.
- **İki ayrı çıktı:** `r1/r2/r3` (hüküm + Jaccard) ve `consensus` (dört ham boyutun
  medyanından **yeniden hesaplanan** liste). Consensus ≠ tekrar skorlarının medyanı.
- **Ağırlık vektörü açık:** 1–10. sıra ×4, 11–15 ×3, 16–20 ×2, 21–30 ×1.
- **Kırpma yok** (kaynak istemiyor) — ama nötr değil: T12'de 20 satır, TrendChange'te
  107 satır `[-1,+3]` dışında (maks 25,0 / min −24,47). Tek açık onay maddesi **O8** budur.
- **`norm_bounds` AI'dan önce tek sefer sabitlenir**, K0–K4'ün tamamı aynısını kullanır.
- **Seed donmuş:** `SHA256(f"{ws}:{repeat}:{batch}", UTF-8)[0:8]` → işaretsiz big-endian.
- **İki aşamalı mühür:** duman öncesi protokol, duman sonrası prompt/config.
- **Dondurma ölçütü:** iki bağımsız deterministik uygulama, aynı fixture + aynı saklanmış
  ham AI çıktısından aynı sonucu üretmeli. Canlı LLM çağrısı kapsam dışı.

## Süreç — on inceleme turu, ne işe yaradı

| tur | kim | ne buldu |
|---|---|---|
| 1 | konsey (3 koltuk) | tasarım kusurları (çapa, ablasyon, ±0,5 eşiği) |
| 2 | bağımsız incelemeci | 3 bloklayıcı — en önemlisi `BC≥60` sadakat ihlali |
| 3 | konsey (1/3 koltuk) | aile/eşleşme, Evergreen, normalizasyon sırası |
| 4 | bağımsız incelemeci | ifade daraltmaları (AUC 0,779, kapsam adı) |
| 5 | **QA** | 5 bloklayıcı — EK-A'nın hiç yazılmamış olması dahil |
| 6 | QA (kısa) | kapatma sırasında açılan yeni delik (`norm_bounds` sırası) |
| 7 | konsey `codex` | metni göremedi; **jenerik kontrol listesi** yine 4 açık buldu |
| 8 | konsey `deepseek` | 8 sözleşme deliği — ağırlık vektörünün tanımsızlığı dahil |
| 9 | bağımsız incelemeci | **SADELEŞTİRME** — 2 onay kapısı + 1 iptal kuralı kaldırıldı |
| 10 | bağımsız incelemeci | metin içi tutarlılık artıkları |

**Öğrenilen üç şey, kaydı tutulmaya değer:**

1. **Bir düzeltme yeni delik açabilir.** 6. ve 9. turların bulguları, 5. ve 8. turların
   düzeltmelerinin yan etkisiydi. Revizyon sonrası ikinci tur şart.
2. **Düzeltmeler planı büyütür.** 9. tura kadar her tur mekanizma ve onay kapısı ekledi;
   630 satıra çıktı. Sadeleştirme turu, dokuzuncu inceleme turundan daha değerliydi.
3. **Konsey koltuğunun içeriği görmesi şart değil.** 7. turda `codex` paketi okuyamadı
   ama "uygulanabilir bir sözleşme neyi pinlemeli" listesi dört gerçek açık buldu.

## Yapılan hatalar (kayıt için)

- Kaynak tablosu `head -50` ile kesildi, `BC ≥ 60` satırı hiç görülmedi → plan patronun
  algoritmasını test etmek yerine **değiştiriyordu**.
- Konseye "kaynak doküman tartışmaya kapalı" denince plan–kaynak sadakat kontrolü
  yasaklanmış oldu; üç koltuk da o yüzden ihlali göremedi.
- "Kırpma penceresini aşan 0 satır" iddiası yanlıştı — üç alandan yalnız biri kontrol
  edilmişti.
- **Taban oranı ihmali:** "pozitiflerin %77'si trendsiz → test anlamsız" sonucuna varıldı;
  evrenin %85'i zaten trendsizdi ve bu sayı aynı çıktının içindeydi. Karar geri çekildi.
- QA koltuğu bütçe kaygısıyla atlanmıştı; üç bloklayıcının üçü de sözleşme kusuruydu,
  yani tam olarak QA'nın işi.

## Sıradaki

Aşama 1 (ücretsiz, etiketsiz): kod + birim testler + mühür üretimi + iki bağımsız
deterministik uygulama kontrolü. Sonra Aşama 1 verisiyle **yalnız O8** patrona sunulur.
Ücretli aşama için ayrıca O1/O2 onayı gerekir.
