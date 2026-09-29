/**
 * Lokasyon (şehir) filtresi — paylaşılan tipler ve istemci-tarafı yardımcılar.
 *
 * plan_v3_lokasyon_filtresi.md §3/§6. Tek doğruluk kaynağı backend'dir
 * (`app/core/policy/location_policy.py`); buradaki eşleşme SADECE arayüz
 * uyarıları (marka-şehir çakışması) için bir YAKLAŞIKLAMADIR — ek varyantı
 * (ör. "ankarada") üretmez, yalnız düz il adı token eşleşmesi yapar. Gerçek
 * kabul/eleme kararı ve sayılar HER ZAMAN preview/audit API'lerinden gelir.
 */

export type LocationFilterMode = 'none' | 'exclude_all' | 'focus_only'

export const LOCATION_MODE_OPTIONS: { value: LocationFilterMode; label: string }[] = [
  { value: 'none', label: 'Şehir filtresi yok' },
  { value: 'exclude_all', label: "Tüm şehirli keyword'leri hariç tut" },
  { value: 'focus_only', label: 'Yalnız seçtiğim şehirler kalsın' },
]

export function locationModeLabel(mode: string): string {
  return LOCATION_MODE_OPTIONS.find((opt) => opt.value === mode)?.label || mode
}

export function isActiveLocationMode(mode: string): mode is 'exclude_all' | 'focus_only' {
  return mode === 'exclude_all' || mode === 'focus_only'
}

/** Alan hiç yoksa (eski profil) veya tanınmayan bir değer taşıyorsa 'none'. */
export function readLocationFilterMode(
  profileData: Record<string, unknown> | null | undefined
): LocationFilterMode {
  const raw =
    profileData && typeof profileData === 'object' ? profileData.location_filter_mode : undefined
  return raw === 'exclude_all' || raw === 'focus_only' ? raw : 'none'
}

export function readFocusCities(profileData: Record<string, unknown> | null | undefined): string[] {
  const raw = profileData && typeof profileData === 'object' ? profileData.focus_cities : undefined
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : []
}

export function readLocationExemptTerms(
  profileData: Record<string, unknown> | null | undefined
): string[] {
  const raw =
    profileData && typeof profileData === 'object' ? profileData.location_exempt_terms : undefined
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : []
}

// 81 resmi il — `app/core/policy/location_policy.py::TURKISH_PROVINCES` ile
// AYNI kanonik liste (halk arası kısaltmalar bilinçli olarak eklenmez).
export const TURKISH_PROVINCES: string[] = [
  'Adana',
  'Adıyaman',
  'Afyonkarahisar',
  'Ağrı',
  'Aksaray',
  'Amasya',
  'Ankara',
  'Antalya',
  'Ardahan',
  'Artvin',
  'Aydın',
  'Balıkesir',
  'Bartın',
  'Batman',
  'Bayburt',
  'Bilecik',
  'Bingöl',
  'Bitlis',
  'Bolu',
  'Burdur',
  'Bursa',
  'Çanakkale',
  'Çankırı',
  'Çorum',
  'Denizli',
  'Diyarbakır',
  'Düzce',
  'Edirne',
  'Elazığ',
  'Erzincan',
  'Erzurum',
  'Eskişehir',
  'Gaziantep',
  'Giresun',
  'Gümüşhane',
  'Hakkâri',
  'Hatay',
  'Iğdır',
  'Isparta',
  'İstanbul',
  'İzmir',
  'Kahramanmaraş',
  'Karabük',
  'Karaman',
  'Kars',
  'Kastamonu',
  'Kayseri',
  'Kırıkkale',
  'Kırklareli',
  'Kırşehir',
  'Kilis',
  'Kocaeli',
  'Konya',
  'Kütahya',
  'Malatya',
  'Manisa',
  'Mardin',
  'Mersin',
  'Muğla',
  'Muş',
  'Nevşehir',
  'Niğde',
  'Ordu',
  'Osmaniye',
  'Rize',
  'Sakarya',
  'Samsun',
  'Siirt',
  'Sinop',
  'Sivas',
  'Şanlıurfa',
  'Şırnak',
  'Tekirdağ',
  'Tokat',
  'Trabzon',
  'Tunceli',
  'Uşak',
  'Van',
  'Yalova',
  'Yozgat',
  'Zonguldak',
]

// Başka anlamı/yaygın ürün kullanımı da olan il adları (plan §3) — yalnız
// arayüz uyarısı vurgusu için, eşleşme kuralını DEĞİŞTİRMEZ.
export const AMBIGUOUS_PROVINCES = new Set([
  'Ordu',
  'Van',
  'Batman',
  'Rize',
  'Uşak',
  'Mersin',
  'Aksaray',
  'Osmaniye',
])

const PRE_FOLD: Record<string, string> = {
  İ: 'i',
  I: 'ı',
  Â: 'A',
  â: 'a',
  Î: 'İ',
  î: 'i',
  Û: 'U',
  û: 'u',
}
const CHAR_MAP: Record<string, string> = {
  ı: 'i',
  ğ: 'g',
  ü: 'u',
  ş: 's',
  ö: 'o',
  ç: 'c',
}

/** `app/core/site_analyzer/turkish_normalizer.py::normalize_turkish` +
 * `location_policy.py`'nin `İ`/`I` ön-katlamasının istemci tarafı eşdeğeri.
 * Ek varyantı (hâl ekleri) ÜRETMEZ — yalnız düz token eşleşmesi için. */
export function normalizeLocationText(text: string): string {
  if (!text) return ''
  let out = text.replace(/[İIÂâÎîÛû]/g, (ch) => PRE_FOLD[ch] ?? ch).toLowerCase()
  out = out.replace(/[ığüşöç]/g, (ch) => CHAR_MAP[ch] ?? ch)
  out = out.replace(/[^\w\s-]/g, ' ')
  out = out.replace(/\s+/g, ' ').trim()
  return out
}

const PROVINCE_LOOKUP: Map<string, string> = new Map(
  TURKISH_PROVINCES.map((p) => [normalizeLocationText(p), p])
)

/** Metin içindeki (tam token olarak geçen) tüm kanonik il adları, tekil. */
export function findProvinceMatches(text: string): string[] {
  const found: string[] = []
  const tokens = normalizeLocationText(text).split(' ').filter(Boolean)
  for (const token of tokens) {
    const city = PROVINCE_LOOKUP.get(token)
    if (city && !found.includes(city)) found.push(city)
  }
  return found
}

/** Marka adı + marka terimlerinde geçen il adları (uyarı amaçlı). */
export function findBrandCityMatches(companyName: string, brandTerms: string[]): string[] {
  const found: string[] = []
  for (const city of [
    ...findProvinceMatches(companyName),
    ...brandTerms.flatMap(findProvinceMatches),
  ]) {
    if (!found.includes(city)) found.push(city)
  }
  return found
}

export const LOCATION_REASON_LABELS: Record<string, string> = {
  LOCATION_CITY_FILTER: "Şehir filtresi (tüm şehirli keyword'ler hariç)",
  LOCATION_NON_FOCUS_CITY: 'Odak dışı şehir',
}

export function locationReasonLabel(reasonCode?: string | null): string {
  if (!reasonCode) return ''
  return LOCATION_REASON_LABELS[reasonCode] || reasonCode
}
