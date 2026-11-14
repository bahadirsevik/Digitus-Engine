# Dijital Varlık Analizi Modülü: Yapılabilirlik ve Plan (v3)

Kaynak istek: `istekler/dijital_varlik_analizi.md`
Bu sürüm, Codex incelemelerindeki doğrulanan düzeltmeleri ve Faz 0 pilotunun sonuçlarını içerir (14.11.2026). Pilot raporu: `istekler/faz0_ornek_rapor.md`. Pilotun plana etkisi 10. bölümde.

## 1. Kısa sonuç

- Modül yapılabilir. Vaat edilecek şey "Similarweb'in veri kapsamı" değil, **kanıt gösteren dijital denetim + kaynaklı sektör/hedef kitle araştırması + reklam ve kreatif briefleri**.
- Üç ayrı çıktı var ve raporda birbirine karıştırılmaz:
  1. **Denetim:** sitede ne gözlendi, ne gözlenemedi.
  2. **Araştırma:** hangi yayın ve arama verisi bulundu.
  3. **Fikir:** bulgulardan çıkan hedef kitle ve kampanya hipotezleri. Bunlar ölçüm değil, öneridir.
- İlk sürüm yeni bir veri aboneliği satın almadan çıkar (sunucu ve yapay zekâ maliyeti yine vardır). Sıralama, işletme profili ve itibar aramaları sonradan ücretli servislerle eklenir.
- "Yapılamaz" ile "müşteri yetki verirse yapılır" ayrıdır. Sosyal medya, İşletme Profili ve ölçümlemedeki pek çok madde müşterinin **kendi** hesabı için yetkiyle alınabilir; **rakipler** için alınamaz.
- Kendi imkânımızla hiç üretilemeyenler: rakip site trafiği (Similarweb verisi) ve backlink. Yalnızca lisanslı servisten satın alınır.

## 2. Karar etiketleri

| Etiket | Anlamı |
|---|---|
| **Otomatik** | Erişilebilen veride otomatik gözlem. Eksiksiz ya da hatasız demek değildir |
| **Servis** | Ücretli bir dış servis gerekir |
| **Yetki** | Müşteri kendi hesabına erişim verirse yapılır; rakipte yapılamaz |
| **Beyan** | Dışarıdan görülmez; müşteriye sorulur ya da analist elle girer |
| **Yorum** | Yapay zekâ değerlendirmesi; ölçüm değil |
| **Hayır** | Hiçbir yolla yapılamaz |

Her bulgu ayrıca "gözlendi / gözlenmedi / bilinmiyor / engellendi" durumlarından birini taşır. "Gözlenmedi" ile "yok" aynı şey değildir: etiket, reklam ya da hesap bulunamaması, olmadığını kanıtlamaz.

## 3. Madde madde değerlendirme

### 3.1 Web sitesi

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Site açılıyor mu, SSL | Otomatik | Durum kodu, yönlendirme, sertifika geçerliliği. Bot engeli, DNS hatası ve gerçek kapalılık ayrı raporlanır; tek başarısız istekten "site kapalı" denmez |
| Mobil uyum | Otomatik + Yorum | Sayfa kodundaki işaret (viewport) ve ekran görüntüsü yorumu. Pilotta PageSpeed'in mobil uyum denetimi boş geldi; ona dayanılmaz. Menünün ve formların telefonda gerçekten kullanılabildiğini kanıtlamaz |
| Açılış hızı, Core Web Vitals | Otomatik | PageSpeed laboratuvar skoru. **Gerçek kullanıcı verisi ayrı bir kaynaktır** (CrUX); Google bunu PageSpeed'den kaldıracağını duyurdu, ayrıca çekilmesi gerekir. Az ziyaretli sitede hiç bulunmaz; bu durumda skor uydurulmaz |
| Tasarımın güncelliği, marka uyumu | Yorum | Ekran görüntüsü üzerinden okunabilirlik, tutarlılık, hiyerarşi bulguları. Tek bir "eski/yeni" notu verilmez |
| Menü ve sayfa yapısı | Otomatik | Taranan örnek sayfalar için. JavaScript ile çizilen menüler görülemeyebilir |
| CTA, form, telefon/WhatsApp | Otomatik | Butonun varlığı görülür; formun gerçekten teslim edildiği test edilmez (form gönderilmez) |
| Kırık link, hata sayfaları | Otomatik | Sınırlı sayıda link yoklanır, taranan kapsam yazılır. 403/429 kırık link sayılmaz |
| Hosting ve alan adı | Otomatik + Beyan | Bitiş tarihi ve kayıt firması kayıtlardan (uzantıya ve gizliliğe bağlı; `.tr`'de gelmeyebilir). IP çoğu zaman Cloudflare gibi bir aracıyı gösterir, gerçek hosting'i değil. Hesabın kimde olduğu beyan |

### 3.2 SEO

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| `site:` indeksli sayfa | Servis (güvenilmez) | Pilotta kullanılan arama servisi 4 sorgunun 4'ünde sonuç döndürmedi. Kesin toplam indeks sayısı Search Console API'sinden de alınamaz; ilk sürümde yok |
| Marka aramasında çıkanlar | Servis | SERP servisi. Yapay zekâ araması sıralama verisi yerine geçmez |
| Title, meta description | Otomatik | Örneklenen sayfalarda eksik/tekrar/uzunluk. "Tüm site tarandı" denmez |
| Sitemap, robots.txt | Otomatik | Varlık ve okunabilirlik. Dosyanın var olması doğru indeksleme demek değildir |
| Blog ve güncellenme sıklığı | Otomatik | Yazı sayfalarındaki tarihlerden. Sitemap tarihleri kullanılmaz: pilotta bir sitede 667 sayfanın 632'si "son 12 ayda güncellendi" görünüyordu. Tarih yayımlanmıyorsa bilinmiyor |
| Ana kelimelerde sıralama | Servis | Kelime × konum × cihaz için anlık ölçüm. İlk 20'de yoksa "ilk 20'de gözlenmedi" yazılır |
| Backlink | Servis (ayrı) | Kendi imkânımızla üretilemez; lisanslı servis. Servisler arasında sayılar farklıdır |
| Search Console bağlı mı | Beyan / Yetki | Sitede doğrulama etiketi olmaması bağlı değil demek değildir |

### 3.3 Google İşletme Profili

İki ayrı Google ürünü var: herkese açık veri için **Places API** (ücretli), yönetilen profil için **Business Profile API** (müşteri yetkisi).

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Profil var mı | Servis | Places eşleşmesi + kullanıcı teyidi. Aynı adlı işletmeler ve şubeler karışabilir |
| Sahiplenilmiş mi | Yetki | Herkese açık veride yok. Müşteri yetki verirse doğrulama durumu okunur. Rakipte bilinmiyor |
| Ad, adres, telefon, saatler | Servis | Places ile site ve müşteri beyanı karşılaştırılır; "doğru" değil "tutarlı/tutarsız" denir |
| Kategori ve açıklama | Servis / Yetki | Kategori Places'ten. Açıklama alanı pilotta 4 işletmenin hiçbirinde gelmedi; işletmenin kendi yazdığı açıklama yalnızca yetkiyle |
| Fotoğraf sayısı ve kalitesi | Servis (sınırlı) | En fazla 10 fotoğraf döner; toplam sayı öğrenilemez |
| Yorum sayısı, puan, dönüş | Servis / Yetki | Puan ve sayı Places'ten. Places en fazla 5 yorum verir ve yorumlarda işletme cevabı alanı yok (pilotta 4 işletmede doğrulandı). Tüm yorumlar ve cevaplar yalnızca yetkiyle |
| Güncel paylaşım | Yetki | Rakipte elle kontrol |
| Harita sıralaması | Servis | Arama noktası, sorgu ve tarih yazılır; tek bir şehir geneli sıra yoktur |

**Önemli kısıt (Google'ın kullanım kuralları, doğrulandı):** Places verisini almak, onu saklamak ve rapora koymakla aynı izin değil. Kurallar işletme adı, adres ve yorumların kopyalanıp saklanmamasını, Google içeriğinden türev içerik üretilmemesini ve yorum gösterilirken yazarın ve kaynağın belirtilmesini istiyor. Süresiz saklanabilen tek alan işletme kimliği (`place_id`). Sonuçları:
Faz 0'da netleşene kadar Places verisi için üç kural birlikte geçerlidir:
- **Saklama:** Veritabanına yalnızca işletme kimliği yazılır; diğer alanlar ekran açıldığında taze çekilir.
- **Yapay zekâ:** Places içeriği (yorum metni, fotoğraf, puan) yapay zekâya girdi olarak gönderilmez.
- **Dışa aktarım:** DOCX/PDF raporda yalnızca Google Haritalar bağlantısı yer alır; ilgili rapor alanı kapalı kalır.

Aynı verinin analist tarafından elle kopyalanması bu sorunu çözmez; kaynak ve kullanım biçimi aynıdır. Puanlardan hesaplanan ortalamalar (3.9, 3.11) da türev içerik olduğundan aynı kurala tabidir.

### 3.4 Sosyal medya

Platform × hesap sahipliği matrisi:

| Platform | Rakip (herkese açık) | Müşterinin kendi hesabı (yetkiyle) | İlk sürüm |
|---|---|---|---|
| YouTube | Kanal ve video verisi, görünen sayaçlar (abone sayısı yuvarlanmış gelir) | Ek performans ve geçmiş | Otomatik (herkese açık kısım) |
| Instagram | İşletme hesaplarında sınırlı veri; Meta uygulama onayı gerekir | İçerik, istatistik, mesajlaşma | Elle giriş; otomasyon küçük bir denemeyle doğrulanır |
| Facebook | Onaya bağlı sınırlı erişim | Sayfa, içerik, istatistik | Elle giriş |
| TikTok | Ticari kullanım için açık yol yok | Profil, sayaç, videolar | Elle giriş |
| LinkedIn | Kapsamlı erişim yok | Takipçi ve içerik istatistikleri | Elle giriş |

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Hesap var mı, resmi mi, doğrulanmış mı | Otomatik + Beyan | Siteden link keşfi + müşteri teyidi. Mavi tik ayrı alan, elle |
| Kullanıcı adı tutarlılığı | Otomatik | Farklı ad otomatik kusur sayılmaz |
| Profil görseli, bio, link | Matrise göre | Erişim yoksa elle kayıt ya da ekran görüntüsü |
| Takipçi ve büyüme | Matrise göre | Rakipte geçmiş yok: bizim kaydetmeye başladığımız tarihten sonrası. Müşteride platformun verdiği kadar geçmiş (Yetki) |
| Paylaşım sıklığı, son paylaşım | Matrise göre | Tarih aralığı ve çekilen içerik sayısıyla birlikte |
| İçerik türleri | Matrise göre | Rakibin story'leri izlenemez; süresi dolmuş story'lerin geçmişi kimse için vaat edilmez |
| Ortalama etkileşim | Matrise göre | Formül açık yazılır (ör. son 20 gönderide ortalama beğeni+yorum). İzlenme ile beğeni tek sayıda karıştırılmaz |
| Görsel kimlik ve dil | Yorum | Örnek içerik ya da yüklenen ekran görüntüsü üzerinden |
| Yorum/DM'lere dönüş | Beyan (ileride Yetki) | **Rakibin DM'leri: Hayır.** Müşteri hesabında yetkiyle ölçülebilir, ilk sürüm dışı |

Üçüncü taraf kazıma servisleri teknik olarak var; platform şartlarına aykırı veriye dayandığı için önermiyorum. İş kararıdır.

### 3.5 Dijital reklam

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Meta Reklam Kütüphanesi | Beyan (linkli) | Meta'nın API'si yalnızca AB'ye ulaşan reklamları ve siyasi reklamları veriyor (doğrulandı). Türkiye için hazır link + analistin tarihli gözlemi |
| Google Ads aktif mi | Servis | "Bu sorguda reklam görüldü/görülmedi". Görülmemesi kampanya kapalı demek değildir. Müşteride hesap yetkisiyle kesinleşir |
| Pixel/etiket | Otomatik | Sayfa kodunda ve Tag Manager dosyasında iz aranır. İz bulunamaması kesin "yok" değildir (onay penceresi, sunucu taraflı ölçüm) |
| Kreatif kalitesi | Yorum | Yüklenen kreatifler üzerinden görsel, mesaj, CTA. Performans verisi olmadan "iyi dönüştürür" denmez |
| Remarketing | Beyan / Yetki | Pixel varlığı remarketing kanıtı değildir |

### 3.6 Ölçümleme

Dört ayrı kanıt düzeyi kullanılır: **iz bulundu** (kodda) → **istek gözlendi** (tarayıcıda) → **hesapta doğrulandı** (yetkiyle) → **bilinmiyor**. İlk sürüm yalnızca birinci düzeyi verir.

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| GA4 kurulu mu | Otomatik (iz) | "İz bulundu" denir; verinin düzgün geldiği kanıtlanmaz |
| Tag Manager | Otomatik (iz) | Aynı |
| Dönüşüm takibi | Beyan / Yetki | Hesap içi bilgi. Hesapta tanımlı olması bile uçtan uca çalıştığını kanıtlamaz |
| Çerez bildirimi, KVKK | Otomatik | Var/yok. Hukuki uygunluk kararı verilmez |

### 3.7 İletişim kanalları

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| WhatsApp Business | Otomatik + Beyan | Link görülür; hattın "Business" olduğu linkten anlaşılmaz |
| E-posta alan adına bağlı mı | Otomatik | Sitedeki adresin alan adı. Posta kutusunun çalıştığını kanıtlamaz |
| E-bülten | Otomatik + Beyan | Abonelik formu görülür (gönderilmez); listenin kullanımı beyan |
| CRM / talep takibi | Beyan | |

### 3.8 E-ticaret

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Kendi mağazası / pazaryeri | Otomatik + Beyan | Altyapı ve linkler koddan. Bulunamayan mağaza "yok" değildir |
| Ürün sayfası kalitesi | Otomatik + Yorum | Örnek ürünlerde görsel, açıklama, fiyat, yapısal veri |
| Ödeme ve sepet akışı | Beyan (kontrol listesi) | Sipariş oluşturmayan gözlem + elle kontrol. Tam otomatik test yalnızca müşterinin vereceği test ortamında, ayrı iş olarak |
| Pazaryeri mağaza puanı | Beyan | Tarihli elle kayıt. Pazaryeri bazında erişim imkânı sonra araştırılır |

### 3.9 Online itibar

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Marka + yorum/şikayet aramaları | Servis ya da aramalı yapay zekâ | Bulunan sayfalar ve tarih. Tüm internetin sayımı değildir |
| Şikayetvar ve benzerleri | Kısmen | Arama sonuçlarındaki sayfalar linklenir. Toplam sayı/çözüm oranı yalnızca doğrulanabilir kaynak varsa |
| Haber ve bahsedilmeler | Otomatik | Kaynaklı arama. Aynı haberin kopyaları ayrı kanıt sayılmaz |
| Yorum puanları | Servis / Beyan | Google: Places (3.3'teki kısıtla). Diğerleri elle; farklı ölçekler ayrı gösterilir |

### 3.10 Rakip kıyası

Aynı kontroller en fazla 3 rakipte, **aynı yöntem ve örneklemle** çalışır (birinde 5, diğerinde 50 sayfa taranmışsa notlar yarıştırılmaz). Rakip listesi kullanıcı onayıyla sabitlenir. Yetki gerektiren hiçbir veri rakipte alınamaz. Kelime sıralaması için ek sorgu gerekmez: aynı sonuç sayfasından okunur.

### 3.11 Sektör analizi

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| Tanım, alt kategoriler, ürün/hizmetler | Otomatik | Onaylı profil + rakip siteleri + kaynaklı arama |
| Büyüklük, büyüme yönü | Kaynak varsa | TÜİK, bakanlık, birlik, faaliyet raporu gibi kaynaklar. Kaynak yoksa "veri bulunamadı"; rakam uydurulmaz |
| Mevsimsellik | Otomatik (yeni iş) | Google Ads aylık hacim serisi. **Seri bugün saklanmıyor**, analiz kendi kopyasını almalı. Arama talebidir, satış mevsimselliği değildir |
| Ana kelimeler, hacim, trend | Otomatik | Mevcut özellik. Google Trends ayrı bir ölçümdür (ilgi, hacim değil); resmi API'si sınırlı erişimde, ek kaynak olarak kalır |
| Kullanılan kanallar | Otomatik (sınırlı) | İncelenen rakiplerden. 3 rakipten sektör yüzdesi çıkarılmaz |
| Sektör ortalamaları | Sınırlı | "İncelenen N işletme" olarak, ortanca ve aralıkla. "Türkiye sektör ortalaması" denmez. Google puanı/yorum ortalaması 3.3'teki Places kuralı netleşene kadar kapalı |
| Reklam maliyet seviyesi | Otomatik | Google Ads teklif aralıkları (servis çekiyor, saklanmıyor). Beklenen tıklama maliyeti ya da Meta maliyeti değildir |
| Beklenen site özellikleri | Otomatik | Rakip özellik tablosu. Rakipte olan her şey zorunlu değildir |
| Yasal/reklam kısıtları, dizinler | Yorum (kaynaklı) | Tarihli özet. Hukuki görüş değildir |
| Rakip hizmet/fiyat/konumlandırma | Otomatik (sınırlı) | Fiyat yalnızca yayımlanmışsa; para birimi, vergi, tarih ile |
| Güncel gelişmeler | Otomatik | Kaynak tarihiyle. Eski rapor güncel gibi sunulmaz |
| Yorumlardaki şikayet/beklentiler | Sınırlı | Kullanım izni olan yorumlardan temalar. Birkaç yorumdan yüzde üretilmez |
| Fırsat ve farklılaşma | Yorum | Bulgulara bağlı, denenebilir hipotez |

### 3.12 Hedef kitle analizi

| Madde | Karar | Nasıl / sınır |
|---|---|---|
| İhtiyaç ve problemler | Otomatik + Yorum | Soru biçimli kelimeler, erişilebilir yorumlar, kaynaklı arama |
| Sık sorulan sorular | Otomatik | Hacmi olan soru kelimeleri gerçek veridir. "Kullanıcılar bunları da sordu" kutusu ayrı kaynak, sıklık sıralaması değildir |
| Tereddüt, şikayet, güven beklentisi | Yorum | Gözlenen örnekler ile çıkarılan hipotezler ayrı gösterilir |
| Olası segmentler | Yorum | İhtiyaç ve satın alma aşaması temelli segment kartı; kanıt ve doğrulanacak varsayımla. Gerçek demografi yalnızca Yetki ile |
| Değer teklifi ve mesajlar | Yorum | Onaylı ürün bilgilerine bağlı; kanıtsız sayı/garanti yok |
| Google/Meta hedef kitle önerileri | Yorum | Google: kelime grupları, negatifler, segment önerisi. Meta ilgi alanları serbest metin önerisidir; platformda doğrulanmadan "uygulamaya hazır" denmez |

### 3.13 Reklam ve kreatif fikir

Akış: araştırma → ihtiyaç temaları → olası segmentler → **kullanıcı segment ve kanal seçer** → segment başına 2-3 brief.
- **Reklam fikri:** segment, içgörü, teklif, kanal, mesaj, CTA, test hipotezi.
- **Kreatif fikir:** açılış cümlesi, görsel yön, çekim listesi, metin, oran/süre, gereken gerçek kanıt, A/B varyantı.
- Çıktı önce brieftir. Kullanıcı isterse mevcut reklam/sosyal üretim akışına girdi olarak aktarılır. Görsel/video dosyası üretimi kapsam dışı.

## 4. Kesin yapılamayanlar

| Ne | Neden | Alternatif |
|---|---|---|
| Rakip site trafiği (Similarweb verisi) | Temsil edici veri setimiz yok | Lisanslı servis; kendi sitede GA4 yetkisi |
| Backlink (kendi imkânımızla) | İnternet çapında indeks gerekir | Lisanslı servis |
| Rakibin DM, CRM, dönüşüm, remarketing verisi | Yetkisiz erişim yok | Yalnızca müşterinin kendi hesabı |
| Rakibin geçmiş takipçi eğrisi | Bugünkü sayaç geçmişi içermez | Kayıt başlangıcından itibaren takip |
| Türkiye Meta ticari reklamlarının otomatik tam listesi | API kapsamı | Linkli elle gözlem |
| Kesin sektör büyüklüğü, gerçek demografi, sektör ortalaması | Her sektörde temsil edici kaynak yok | Kaynaklı yayın, sınırlı örneklem ya da "veri bulunamadı" |
| Kesin "etiket yok / reklam yok / hesap yok" | Dinamik yükleme, engel, örnekleme | Gözlem koşullarıyla "gözlenmedi" |
| Her sitede otomatik ödeme testi | Siteye özel, sahte sipariş riski | Elle kontrol; test ortamında ayrı iş |

## 5. Teknik tasarım

Kodda doğrulanan başlangıç noktası:

| Mevcut parça | Kullanılabilir | Eksik |
|---|---|---|
| `SiteCrawler` | Güvenli istek altyapısı, sitemap keşfi | En fazla 5 sayfa tarıyor, ham sayfa kodunu atıyor ve marka profili için sayfa seçiyor. Denetim için ayrı sayfa seçimi (iletişim, KVKK, ürün) ve koddan sinyal çıkarma gerekir |
| Aramalı yapay zekâ çağrısı | Kaynak adresleri ve metin-kaynak eşlemesi | Yeni araştırma aşamaları, şemalar, arama bütçesi |
| Google Ads servisi | Aylık seri, teklif aralığı çekiliyor | İkisi de saklanmıyor |
| Celery + görev takibi | Aynen | Yeni görev tipi |
| Dışa aktarım | Dosya üretim altyapısı | İstek skorlama çalışması zorunlu tutuyor; denetim için bağımsız hedef gerekir |

Tasarım:

- **Skorlamadan bağımsız.** Analiz workspace'e bağlanır, skorlama çalışması gerektirmez. Mevcut ADS/SEO/SOCIAL motorlarına dokunulmaz.
- **Modül:** `app/core/digital_audit/` (toplayıcılar ve sentez), dış servis adaptörleri `app/integrations/` altında, uçlar `app/api/v1/digital_audit.py`, görev `app/tasks/digital_audit_tasks.py`.
- **İki tablo:** `digital_audits` (workspace, durum, kapsam, başlangıçta dondurulan profil ve kelime kopyası, form cevapları, özet rapor) ve `digital_audit_targets` (kendi/rakip, alan adı, bölüm durumları, bulgular JSON). Bir marka + üç rakip ve bölüm bölüm kaydetme için bu ayrım gerekli; kontrol başına tablo açılmaz.
- **Bulgu biçimi:** `{kontrol, ölçüm durumu, değerlendirme, değer, kanıt türü, kaynak adresi, tarih, kapsam, sınırlar}`. Ölçüm durumu (gözlendi/gözlenmedi/bilinmiyor/engellendi/hata) ile değerlendirme (iyi/uyarı/sorun/bilgi) ayrı alanlardır; böylece servis hatası ile gerçek eksik aynı hücreye düşmez.
- **Otomatik bulgu ile analist girdisi ayrı durur.** Elle girilen bilgi ya da müşteri beyanı, otomatik ölçümün üzerine yazılmaz; aynı kontrolün yanında ayrı bir kayıt olarak, kaynağı "beyan" ya da "elle" işaretiyle tutulur. Tam değişiklik geçmişi ilk sürümde yok.
- **Kaynak kuralları kodda açık yazılır**, genel bir izin sistemi kurulmaz. Places için saklama, yapay zekâ ve dışa aktarım kuralları 3.3'te. Ham sayfa kodu ve görseller veritabanına yazılmaz. Soru formuna parola ya da erişim anahtarı yazılmaz.
- **Tarama davranışı:** Denetim tarayıcısı sitelerin robots.txt kurallarına uyar ve istekleri yavaş atar. Mevcut tarayıcı robots.txt'yi yalnızca sitemap bulmak için okuyor; kurala uyma yeni iş. Özellikle rakip sitelerde gerekli.
- **Çalıştırma:** Başlangıçta kapsam dondurulur. Aynı başlatma isteği tekrar gelirse çalışan görev döner. Bölümler tamamlandıkça kaydedilir; biri hata verirse diğerleri sürer. Sonuç durumları: tamamlandı / kısmi / başarısız. Tekrar deneme yalnızca geçici hatalarda ve sınırlı.
- **Takılı görev:** İşçi süreç kapanırsa analiz sonsuza kadar "çalışıyor" kalmamalı. Projede bunun hazır kalıbı var (sosyal içerik üretimindeki süreli kilit: süre dolunca görev başarısız sayılır); aynısı kullanılır, o ana kadar kaydedilen bölümler "kısmi" sonuç olarak korunur.
- **Yapay zekâ:** Toplama ve sentez ayrı aşamalar; sayıları ve durum kararlarını kod üretir. Sayısal iddiada kaynak adresi yetmez: rakamın o kaynakta, o ülke ve yıl için geçtiği kontrol edilir. Kaynak verisi dönmeyen aramalı çağrı başarısız sayılır. Dış sayfa metinleri güvenilmeyen veridir; içlerindeki talimatlar izlenmez. Rakibin iddiası müşterinin ürün bilgisine dönüşmez.
- **Maliyet tavanı:** Analiz başına en fazla ücretli çağrı ve tekrar sayısı; aynı anda çalışan analiz sayısı sınırlı; analiz sonunda kaynak başına çağrı sayısı görünür. Aramalı yapay zekâ tek çağrıda birden çok arama yapabildiği için bu "kesin parasal tavan" olarak sunulmaz; ilk pilotlarda gerçek faturayla karşılaştırılır.
- **Arayüz:** Tek sayfa. Bölüm bazında bulgular, öncelik ve ölçüm kapsamı. **Tek bir "dijital başarı puanı" yok**: erişilemeyen veriler sıfır puan gibi görünür ve yanıltır.
- **Dışa aktarım:** Bağımsız denetim raporu (DOCX/PDF). Gözlem/beyan/yorum işaretleri, kaynak linki ve tarih dosyada da korunur.

İlk sürümde bilinçli olarak olmayanlar: tarayıcı (Chromium) işçisi, workspace başına günlük harcama sınırı, elle düzeltmelerin geçmiş kaydı. İhtiyaç pilotta görülürse eklenir.

## 6. Fazlar

| Faz | Kapsam | Kabul ölçütü |
|---|---|---|
| **0: Teknik deneme** | Üç site (klasik, JavaScript ağırlıklı, bot engelli). PageSpeed, bir SERP sorgusu, Places alanları ve kullanım kuralı, aramalı yapay zekâ çıktısı. Ürün kodu değil, atılabilir betik | **Gerçek bir marka + 3 rakip için örnek rapor çıkar** ve aşağıdaki beş soruyu cevaplar. Sonraki fazlar buna göre daraltılır |
| **1: Denetim çekirdeği** | Tablolar, görev, uçlar, sayfa. Marka + 3 rakip. Web sitesi, SEO sinyalleri, iz tespiti, iletişim, e-ticaret, PageSpeed. Müşteri soru formu ve elle kayıtlar | Engellenen ya da eksik veri "yok" diye işaretlenmez. Kaynak ve kapsam görünür. Dört odaklı test geçer: çift başlatma tek analiz üretir; işçi kesilince analiz "çalışıyor" kalmaz; kısmi sonuç korunur; başka workspace'in analizine erişilemez |
| **2: Araştırma ve fikir** | Kaynaklı sektör ve hedef kitle raporu, aylık seri kopyası, segment kartları, reklam ve kreatif briefler, bağımsız DOCX/PDF | Kaynaksız rakam yok. Hipotezler işaretli. Skorlama çalışması olmadan çıktı alınır |
| **3: Veri zenginleştirme** | SERP (sıralama, marka, reklam, itibar), Places (uygun kullanım modeliyle), YouTube, kıyas ekranı | Konum/cihaz/tarih açık. Servis kesilse çekirdek çalışır. Tavan uygulanır |
| **4: Seçenekler** | Müşteri yetkisiyle GA4, Search Console, İşletme Profili, sosyal hesaplar. Backlink ve trafik lisansı. Tarayıcı işçisi | Bağlantı başına ayrı keşif ve tahmin |

**Faz 0 raporunun cevaplaması gereken sorular:**
1. İstenen kontrollerin hangileri gerçekten ölçülebildi?
2. JavaScript ağırlıklı ve bot engelli sitelerde ne kadarı "bilinmiyor" kaldı?
3. Araştırmadaki sayısal iddialar kaynakta doğrulanabildi mi?
4. Bir marka ve üç rakip için süre ve maliyet ne oldu?
5. Çıktı, ajans çalışanının müşteriye sunabileceği kadar yararlı mı? (Bu soruyu şirketteki istek sahipleri cevaplar.)

**Faz 0 için gerekenler:** PageSpeed ve Places için Google API anahtarı, bir SERP servisi anahtarı (projede SerpApi ayarları deney amaçlı duruyor), denenecek gerçek bir müşteri sitesi ve üç rakibi. Places kullanım kuralı için gerekirse Google'ın koşullarını okuyacak biri (hukuk ya da hesap yöneticisi).

**En büyük belirsizlik** tablo ve uç geliştirmesi değil; farklı sitelerden yeterli veri çıkarabilmek ve kaynaklı araştırmayı tutarlı kalitede rapora dönüştürmek. Faz 0 bu ikisini ölçer.

**Sıra neden böyle:** Sektör/hedef kitle analizi ve reklam/kreatif fikir asıl istenen şey; ücretli servis kararını beklemeden Faz 2'de çıkar. SERP ve Places olmadan eksik kalan alanlar raporda açıkça "veri yok" görünür. YouTube ve araştırma, Places bütçesi reddedilse de düşmez.

**Efor:** Codex'in ön tahmini Faz 0+1 için 15-23, Faz 0+1+2 için 25-38, Faz 3 dahil 33-52 kişi-gün (tek geliştirici, kararlar gecikmeden). Büyüklük sırası olarak makul buluyorum; kendi başıma doğrulamadım. Faz 0 bittikten sonra yenilenmeli; platform onayı ve sözleşme bekleme süreleri dahil değil.

**Başlangıç sınırları (öneri):** 1 marka + en fazla 3 rakip; hedef başına en fazla 10 sayfa ve 50 link; marka için mobil + masaüstü PageSpeed, rakiplerde yalnızca ana sayfa mobil; 10 kelime × 1 konum × 1 cihaz.

## 7. Şirketten gereken kararlar

Faz 0 ve 1'i başlatmak için hepsi gerekmez.

1. Araç ajans içi analist için mi, müşterinin doğrudan kullanacağı bir ürün mü? İkincisi kimlik ve yetki işini ciddi büyütür. Önerim: önce ajans içi.
2. İlk hedef kaliteli bir ön analiz raporu mu, sürekli takip mi? Önerim: önce rapor.
3. Analiz başına kaç rakip, sayfa, kelime ve ne kadar harcama kabul ediliyor?
4. Rakip trafik ve backlink kesin isteniyor mu? İsteniyorsa ayrı lisans bütçesi gerekir.
5. Müşterilerden hesap yetkisi istenecek mi, yoksa ilk sürüm yalnızca açık veri ve beyanla mı çalışacak?
6. Kreatifte fikir/brief yeterli mi, görsel/video üretimi de isteniyor mu?

## 8. Riskler

- **Yapay zekânın rakam uydurması.** Kaynakta doğrulanmayan rakam rapora girmez.
- **Anlık ölçümün kesin gibi okunması.** Sıralama, reklam ve harita sonuçları konum ve tarihle birlikte yazılır.
- **"Gözlenmedi"nin "yok" diye satılması.** En çok etiket, reklam ve sosyal hesaplarda.
- **Places kullanım kuralları.** Saklama ve rapora koyma izni netleşmeden bu veri üzerine özellik kurulmaz.
- **Maliyet.** Rakip ve kelime sayısı ücretli sorguyu çarpar; aramalı yapay zekânın arama ücreti token sayacında görünmez.
- **JavaScript ile çizilen ve bot engelleyen siteler.** Bazı kontroller "bilinmiyor" döner; Faz 0 bunun ne sıklıkta olduğunu gösterir.

## 9. v1'e göre ne değişti

Codex'in düzeltmelerinden kodda ya da resmi belgede doğruladıklarım:

| Konu | v1'de ne demiştim | Doğrusu |
|---|---|---|
| Mevsimsellik | "Projede zaten çekiliyor, eğri buradan çıkar" | Servis seriyi çekiyor ama workspace kayıtlarında yalnızca 3 ve 12 aylık trend yüzdesi saklanıyor. Yeni iş |
| Dışa aktarım | "Mevcut dışa aktarıma yeni bölüm" | İstek şeması skorlama çalışmasını zorunlu tutuyor. Bağımsız hedef gerekir |
| Site tarayıcısı | "Genişletilerek çıkar" | 5 sayfa sınırı var, ham kodu atıyor, sayfaları profil için seçiyor. Ayrı denetim modu gerekir |
| Places | "Bulgular JSON'a yazılır" | Kullanım kuralları ad, adres ve yorumların saklanmasını ve türev içerik üretimini kısıtlıyor |
| Core Web Vitals | "PageSpeed verir" | Laboratuvar skoru verir; Google gerçek kullanıcı verisini PageSpeed'den kaldıracağını duyurdu |
| Sosyal medya, İşletme Profili | Çoğuna "yapılamaz" | Müşterinin kendi hesabında yetkiyle yapılabilir; yalnızca rakipte yapılamaz |
| Etiket tespiti | "GA4 kurulu mu: Evet" | Yalnızca iz bulunur; çalıştığı kanıtlanmaz |
| Search Console | "Kesin indeks sayısını verir" | API'si toplam indeks sayısını vermez |
| Hosting | "IP'den bulunur" | IP çoğu zaman aracı servisi gösterir |
| Faz sırası | Araştırma ve fikir, ücretli servislerden sonra | Öne alındı |
| Veri modeli | Tek tablo | Analiz + hedef olarak iki tablo |

İlk sürüme alınmayanlar (Codex ile mutabık): workspace başına günlük harcama sınırı, elle düzeltmelerin tam geçmişi, genel bir kaynak izin sistemi. Yerlerine küçük karşılıkları kondu: analiz başına tavan ve eşzamanlı çalışma sınırı, otomatik bulgu ile analist girdisinin ayrı tutulması, Places için kodda üç açık kural.

Codex'in ikinci incelemesinden (14.11.2026) eklenenler: Places için elle kayıt alternatifi kaldırıldı (3.3); takılı görev ve dört dayanıklılık testi (5, 6); Faz 0'ın örnek rapor üretmesi ve beş soru (6). Kendi incelememde eklediğim: robots.txt kurallarına uyma (5), Places puanlarından ortalama hesaplamanın da aynı kısıta girmesi (3.11).

Doğrulamadıklarım: Codex'in sosyal platform API'leri (Instagram, Facebook, TikTok, LinkedIn) ve Business Profile API hakkındaki bağlantıları. Bildiklerimle tutarlı, ama sayfaları tek tek açmadım; Faz 0 ve Faz 4 keşiflerinde gerçek hesapla denenmeden taahhüt verilmemeli.

## 10. Faz 0 pilotunun plana etkisi

Pilot bir müşteri ve üç rakip (diş klinikleri) ile, ayrıca iki bot engelli ve iki JavaScript ağırlıklı siteyle yapıldı. Ayrıntı ve kanıtlar: `istekler/faz0_ornek_rapor.md`, `istekler/faz0_pilot/`.

### Faz 1 için bağlayıcı kurallar

| Kural | Pilottaki dayanağı |
|---|---|
| **Engel sayfası gerçek içerik sayılmaz.** 403/429/503 ya da doğrulama sayfası gelirse hedef "engellendi" olur; başlık, açıklama ve diğer sayfa içi bulgular üretilmez | Bir sitede engel sayfasının başlığı ("Just a moment...") site başlığı gibi geldi |
| **Okunamayan sayfadan "yok" sonucu çıkmaz.** Engellenen ya da içeriği JavaScript ile çizilen sayfada kontroller "bilinmiyor" döner | Bot engelli iki sitede sayfa içi kontrollerin tamamı, bir JavaScript sitesinde neredeyse tamamı okunamadı |
| **Sayfa türü yalnızca adres kalıbından çıkarılmaz.** Menü bağlantısı, bağlantı metni ve sayfa içeriği birlikte kullanılır; denetlenecek sayfalar sitemap'ten değil ana sayfanın bağlantılarından seçilir | Bir rakipte sitemap'teki kumar sayfaları adres kalıbı yüzünden "KVKK" ve "Hakkımızda" sanıldı |
| **Karakter kodlaması sunucu başlığına bırakılmaz** | Bir sitede sunucu kodlama bildirmiyordu, Türkçe karakterler bozuldu |
| **Tag Manager dosyasından dönüşüm çıkarımı yapılmaz** | "Dönüşüm" kelimesi her sitede kütüphanenin kendi kodunda geçiyordu |
| **Gerçek kullanıcı hız verisi çoğu sitede yoktur;** yokluğu sorun sayılmaz, laboratuvar skorundan ayrı gösterilir | 4 siteden yalnızca 1'inde vardı |

### Sonraki fazlar için notlar

- **Kaynaklı araştırma (Faz 2):** kaynak doğrulaması zorunlu, PDF okuma kapsamda. Otomatik kontrol yalnızca aday bulur (rakam kaynakta geçiyor mu); rapora girmeden önce bağlam cümlesi gösterilir. Doğrulanamayan bir dayanak elenirse ona bağlı öneri de yeniden değerlendirilir. Pilotta 19 sayısal iddianın 9'u kaynakta bulundu; hedef kitle örnek ifadelerinin 9'da 3'ü birebir geçiyordu.
- **Sıralama (Faz 3):** sorgu başına 9-10 sonuç geliyor ("ilk 20" için 2 sorgu). Google sonuç bağlantıları şifreli; site eşleştirmesi görünen adres satırından yapılır ve bu satır bazen adres içermez. Pilot sonuçları, üç sorguluk elle karşılaştırma yapılana kadar ön bulgudur.
- **Puan ve yorum sayısı:** arama sonucundaki harita kutusunda da görünüyor. Places yerine kullanılıp kullanılmayacağı ayrı karar (eşleşme ada göre ve daha zayıf; kullanım koşulları ayrıca değerlendirilmeli).
- **Sağlık sektörü:** 12.11.2025 tarihli yönetmelik sağlık hizmetlerinde reklamı yasaklıyor ve ücretli öne çıkmaya izin vermiyor. Reklam fikri üretimi (Faz 2) sektör kısıtını girdi almalı; sağlık müşterilerinde ücretli reklam önerileri uzman değerlendirmesi olmadan "yayıma hazır" sayılmaz.
- **Maliyet:** pilotta çağrı sayıları ve süre ölçüldü (yaklaşık 12 dakika; 17 arama, 5 hız ölçümü, 4 işletme profili, 7 yapay zekâ çağrısı). Para tutarı henüz bütçe verisi değil.

### Faz 1 kapsamı (kesinleşen)

Dahil: tablolar, görev, uçlar, sayfa; web sitesi, sayfa içi SEO, etiket izleri, iletişim, e-ticaret tespiti, PageSpeed laboratuvar ölçümü; marka + en fazla 3 rakip; müşteri soru formu; engel tanıma ve "bilinmiyor" durumları; çift başlatma, işçi kesintisi, kısmi sonuç ve workspace izolasyonu testleri.

Dahil değil: sosyal medya otomasyonu, sıralama ve arama görünürlüğü, Places, müşteri hesabı bağlantıları, yapay zekâ raporları, tasarım yorumu, dışa aktarım, tarayıcı işçisi.

## 11. Faz 1 durumu (14.11.2026)

Çekirdek geliştirildi ve geliştirme ortamında gerçek sitelerle çalıştırıldı. Henüz commit edilmedi.

**Yapılanlar**
- Tablolar: `digital_audits`, `digital_audit_targets` (migration `20261114_001`). Skorlama çalışması gerektirmez.
- Görev: `digital_audit.run`. Her sitenin sonucu bitince kaydedilir; workspace başına tek aktif denetim veritabanı indeksiyle garanti edilir; süreli kilit dolunca okuma anında "kısmi" ya da "başarısız" olarak kapatılır.
- Uçlar: `POST/GET /api/v1/digital-audit/audits`, `GET /audits/{id}`, `PUT /audits/{id}/answers`.
- Toplayıcılar: 31 kontrol (web sitesi, SEO, etiket izleri, iletişim, e-ticaret, mobil ve masaüstü hız).
- Arayüz: "Dijital Denetim" sayfası (kıyas tablosu, durum etiketleri, sınır notları, müşteri soru formu, geçmiş).
- Testler: 42 backend, 10 frontend. Çift başlatma, işçi kesintisi, kısmi sonuç, geç kalan işçinin yazamaması, workspace izolasyonu dahil.

**Bilinen sınırlar**
- Bot engelini aşmaz; JavaScript ile çizilen sitelerde çoğu kontrol "bilinmiyor" döner (tarayıcı işçisi yok). Metni çok kısa olup script yükleyen her sayfa böyle sayılır.
- Site başına en fazla 10 sayfa ve 40 site içi bağlantı (başka sitelere giden bağlantılar ve robots.txt ile yasaklanan adresler denetlenmez); sayfalar ana sayfanın bağlantılarından seçilir. Ana sayfadan bağlantı verilmeyen bir sayfa (ör. yalnızca alt sayfada görünen KVKK metni) "görülmedi" çıkabilir.
- Alan adı kaydı `.tr` uzantılarında büyük olasılıkla okunamaz ("bilinmiyor" döner); denenmedi.
- Hız ölçümü laboratuvar ölçümüdür; aynı site için ölçümler arasında 10-15 puan fark görüldü (89, 76, 74).
- Tasarım yorumu, ürün sayfası kalite örneklemesi, dışa aktarım ve önceki denetimle karşılaştırma yok.
- Denetimi iptal etme düğmesi yok; süren denetim bitene ya da kilit süresi (10 dk) dolana kadar yenisi başlatılamaz.
