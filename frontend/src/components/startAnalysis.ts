import { ScoringRunCreate } from '../services/api'

// "Analizi başlat" modalının saf payload dönüştürücüsü (vitest edilir).
// Amaç seçimi enable_* bayraklarına gider; kapasiteler her kanal için gerekli
// (backend şeması >0 ister, kapalı kanal için de).
//
// Analiz motoru kullanici secimi degildir. Yeni urun kosulari kilitli v3
// motoruyla ve otomatik kanal atamasiyla baslar. Legacy run'lar yalniz
// goruntuleme/rozet uyumlulugu icin desteklenir.
// Sağlayıcı / model / ücret / tarama modu KULLANICIYA GÖSTERİLMEZ — bunlar
// sunucu kararıdır (bkz. app/core/screening/auto_trigger.py).

// Lokasyon filtresi kapısı (plan_v3_lokasyon_filtresi.md §5.7): mod `none`
// iken hiç gönderilmez — StartAnalysisModal bu alanı yalnız aktif moddaki
// taze bir `/policy/location-preview` yanıtından doldurur.
export interface StartAnalysisLocationGate {
  location_universe_fingerprint: string
  location_policy_fingerprint: string
  location_preview_is_saved_policy: boolean
}

export interface StartAnalysisOptions {
  brandProfileId: number
  runName?: string
  ads: boolean
  seo: boolean
  social: boolean
  adsCapacity: number
  seoCapacity: number
  socialCapacity: number
  locationGate?: StartAnalysisLocationGate
}

export const DEFAULT_CAPACITIES = { ads: 30, seo: 50, social: 20 }

export const SOCIAL_BASELINE_LABEL = 'Mevcut SOCIAL — henüz optimize edilmedi'

export function buildStartAnalysisPayload(opts: StartAnalysisOptions): ScoringRunCreate {
  const invalid = (capacity: number) => !Number.isSafeInteger(capacity) || capacity < 1
  if (
    (opts.ads && invalid(opts.adsCapacity)) ||
    (opts.seo && invalid(opts.seoCapacity)) ||
    (opts.social && invalid(opts.socialCapacity))
  ) {
    throw new RangeError('Seçili kanalların kelime sayısı pozitif tam sayı olmalı.')
  }

  return {
    run_name: opts.runName?.trim() || undefined,
    brand_profile_id: opts.brandProfileId,
    ads_capacity: Math.max(1, opts.adsCapacity || DEFAULT_CAPACITIES.ads),
    seo_capacity: Math.max(1, opts.seoCapacity || DEFAULT_CAPACITIES.seo),
    social_capacity: Math.max(1, opts.socialCapacity || DEFAULT_CAPACITIES.social),
    enable_ads: opts.ads,
    enable_seo: opts.seo,
    enable_social: opts.social,
    keyword_selection_mode: 'all',
    auto_assign_channels: true,
    algorithm_version: 'v3',
    ...(opts.locationGate ? { ...opts.locationGate } : {}),
  }
}

export function hasAtLeastOnePurpose(opts: Pick<StartAnalysisOptions, 'ads' | 'seo' | 'social'>) {
  return opts.ads || opts.seo || opts.social
}

/** Koşu SOCIAL-only mi? (mevcut baseline SOCIAL sunumu) */
export function isSocialOnly(opts: Pick<StartAnalysisOptions, 'ads' | 'seo' | 'social'>) {
  return opts.social && !opts.ads && !opts.seo
}

export function validateStartAnalysis(opts: {
  ads: boolean
  seo: boolean
  social: boolean
}): { code: string; message: string } | null {
  if (!hasAtLeastOnePurpose(opts)) {
    return { code: 'NO_PURPOSE', message: 'En az bir amaç seçin.' }
  }
  return null
}

/** Koşu adı: sonuç listesinde hangi koşu olduğu ADINDAN anlaşılmalı. */
export function defaultRunName(opts: {
  ads: boolean
  seo: boolean
  social: boolean
  today?: string
}): string {
  const stamp = opts.today ?? new Date().toLocaleDateString('tr')
  const channels = [opts.ads && 'ADS', opts.seo && 'SEO', opts.social && 'SOCIAL']
    .filter(Boolean)
    .join('/')
  return `v3 ${channels} analizi — ${stamp}`
}

/** Sonuç listesindeki koşu rozeti (mevcut baseline SOCIAL açıkça işaretlenir). */
export function runResultBadge(run: {
  algorithm_version?: string | null
  enable_ads?: boolean | null
  enable_seo?: boolean | null
  enable_social?: boolean | null
}): { label: string; tone: 'baseline' | 'experimental' } | null {
  const social = run.enable_social !== false
  const ads = run.enable_ads !== false
  const seo = run.enable_seo !== false
  if (social && !ads && !seo && (run.algorithm_version ?? 'v2') === 'v2') {
    return { label: SOCIAL_BASELINE_LABEL, tone: 'baseline' }
  }
  if ((run.algorithm_version ?? 'v2') === 'v2_1') {
    return { label: 'v2.1 motoru', tone: 'experimental' }
  }
  return null
}
