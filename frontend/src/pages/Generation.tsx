/**
 * KALICI REDIRECT WRAPPER (Faz 4 — Generation panel extraction).
 *
 * Üretim panelleri kanal sayfalarına taşındı:
 *   SEO+GEO → components/generation/SeoGeoPanel (sayfa: /seo-geo)
 *   Ads     → components/generation/AdsPanel    (sayfa: /ads)
 *   Social  → components/generation/SocialPanel (sayfa: /social)
 *
 * Eski /generation?tab=...&run_id=... linkleri GenerationRedirect ile ilgili
 * kanal sayfasına, run_id korunarak yönlendirilir (bookmark/geri-tuşu güvenli).
 * Generation.css panellerce kullanılmaya devam eder — SİLME.
 */
export { default } from '../components/GenerationRedirect'
