import type { VisibilityKeywordRow, AuditFinding, AuditStatus, AuditTarget } from '../services/api'

export const SECTION_LABEL: Record<string, string> = {
  site: 'Web sitesi',
  seo: 'SEO',
  tracking: 'Ölçümleme ve etiketler',
  contact: 'İletişim kanalları',
  ecommerce: 'E-ticaret',
}
export const SECTION_ORDER = ['site', 'seo', 'tracking', 'contact', 'ecommerce']

export const AUDIT_STATUS_LABEL: Record<AuditStatus, string> = {
  pending: 'Sırada',
  running: 'Çalışıyor',
  completed: 'Tamamlandı',
  partial: 'Kısmen tamamlandı',
  failed: 'Başarısız',
}

export const TARGET_STATUS_LABEL: Record<AuditTarget['status'], string> = {
  pending: 'Bekliyor',
  completed: 'Denetlendi',
  blocked: 'Site erişimi engelledi',
  failed: 'Denetlenemedi',
}

export interface Chip {
  text: string
  tone: 'ok' | 'warning' | 'problem' | 'neutral' | 'muted'
}

/**
 * Ölçüm durumu ile değerlendirme ayrı okunur: engellenen ya da okunamayan
 * kontrol asla "yok" ya da "sorun" gibi gösterilmez.
 */
export function findingChip(f: AuditFinding | undefined): Chip {
  // Bu hedef için kontrol hiç çalıştırılmadı (ör. rakiplerde masaüstü hız ölçümü yapılmaz)
  if (!f) return { text: 'Ölçülmedi', tone: 'muted' }
  if (f.state === 'blocked') return { text: 'Engellendi', tone: 'muted' }
  if (f.state === 'unknown') return { text: 'Bilinmiyor', tone: 'muted' }
  if (f.state === 'error') return { text: 'Hata', tone: 'problem' }
  if (f.state === 'not_observed') {
    return { text: 'Görülmedi', tone: f.assessment === 'warning' ? 'warning' : 'neutral' }
  }
  if (f.assessment === 'ok') return { text: 'İyi', tone: 'ok' }
  if (f.assessment === 'warning') return { text: 'Uyarı', tone: 'warning' }
  if (f.assessment === 'problem') return { text: 'Sorun', tone: 'problem' }
  return { text: 'Görüldü', tone: 'neutral' }
}

const PAGE_TYPE_LABEL: Record<string, string> = {
  iletisim: 'İletişim',
  hakkimizda: 'Hakkımızda',
  hizmet: 'Hizmetler',
  blog: 'Blog',
  kvkk: 'KVKK',
  randevu: 'Randevu',
  fiyat: 'Fiyat',
  sss: 'SSS',
}

const SOCIAL_LABEL: Record<string, string> = {
  instagram: 'Instagram',
  facebook: 'Facebook',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  linkedin: 'LinkedIn',
  x: 'X',
}

function list(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String) : []
}

function counts(parts: [string, unknown][]): string {
  const shown = parts.filter(([, n]) => typeof n === 'number' && n > 0)
  return shown.length ? shown.map(([label, n]) => `${n} ${label}`).join(', ') : 'Eksik yok'
}

/** Bulgunun değerinden kısa, okunur özet. Değer yoksa boş döner. */
export function findingSummary(f: AuditFinding | undefined): string {
  const v = f?.value
  if (!f || !v) return ''
  switch (f.check) {
    case 'site.reachable':
      return v.final_url ? String(v.final_url) : v.http_status ? `HTTP ${v.http_status}` : ''
    case 'site.ssl':
      return v.expires ? `Bitiş ${v.expires} (${v.days_left} gün)` : ''
    case 'site.domain':
      return [v.expiration && `Bitiş ${v.expiration}`, v.registrar].filter(Boolean).join(' · ')
    case 'site.speed_mobile':
    case 'site.speed_desktop': {
      const scores = (v.scores || {}) as Record<string, number>
      const lab = (v.lab || {}) as Record<string, string>
      return [
        `Performans ${scores.performance}`,
        lab['largest-contentful-paint'] && `En büyük öğe ${lab['largest-contentful-paint']}`,
      ]
        .filter(Boolean)
        .join(' · ')
    }
    case 'site.structure':
      return Object.keys((v.page_types || {}) as object)
        .map((t) => PAGE_TYPE_LABEL[t] || t)
        .join(', ')
    case 'site.cta':
      return (
        [v.phone && 'Telefon', v.whatsapp && 'WhatsApp', (v.forms as number) > 0 && 'Form']
          .filter(Boolean)
          .join(', ') || ''
      )
    case 'site.broken_links':
      return `${list(v.broken).length} kırık / ${v.checked} bağlantı`
    case 'seo.titles':
      return counts([
        ['eksik', v.missing],
        ['tekrar', v.duplicate],
        ['uzun', v.too_long],
      ])
    case 'seo.meta_descriptions':
      return counts([
        ['eksik', v.missing],
        ['tekrar', v.duplicate],
      ])
    case 'seo.h1':
      return counts([
        ['eksik', v.missing],
        ['birden fazla', v.multiple],
      ])
    case 'seo.noindex': {
      // Liste en fazla 5 örnek adres; toplam `count`ta. Eski kayıtta tavan "en az" diye yazılır.
      const sample = list(v.noindex_pages).length
      if (!sample) return ''
      return `${v.count ?? (sample >= 5 ? `en az ${sample}` : sample)} sayfa kapalı`
    }
    case 'seo.structured_data':
      return list(v.types).slice(0, 4).join(', ')
    case 'seo.image_alt':
      return `${v.missing_alt} / ${v.total} görselde alt metni eksik`
    case 'seo.blog':
      return v.newest_date ? `En yeni yazı ${v.newest_date}` : ''
    case 'seo.sitemap':
      return v.urls ? `${v.urls}${v.capped ? '+' : ''} adres` : ''
    case 'tracking.gtm':
    case 'tracking.ga4':
    case 'tracking.google_ads':
      return list(v.ids).join(', ')
    case 'tracking.other_pixels':
      return list(v.found).join(', ')
    case 'tracking.cookie_banner':
      return list(v.tools).join(', ')
    case 'contact.phone':
      return list(v.links)
        .map((l) => l.replace('tel:', ''))
        .join(', ')
    case 'contact.email':
      return list(v.emails).slice(0, 2).join(', ')
    case 'contact.forms':
      return [
        (v.contact_forms as number) > 0 && `${v.contact_forms} iletişim formu`,
        v.newsletter_form && 'abonelik formu',
      ]
        .filter(Boolean)
        .join(', ')
    case 'contact.social_links':
      return Object.keys((v.accounts || {}) as object)
        .map((n) => SOCIAL_LABEL[n] || n)
        .join(', ')
    case 'ecommerce.platform':
      return list(v.platforms).join(', ')
    default:
      return ''
  }
}

export interface AuditRow {
  check: string
  label: string
  cells: (AuditFinding | undefined)[]
  limits: string[]
}

/** Hedefleri sütun, kontrolleri satır yapan kıyas tablosu (bölüm bazında). */
export function buildSections(targets: AuditTarget[]): { section: string; rows: AuditRow[] }[] {
  const order: string[] = []
  const labels: Record<string, string> = {}
  const sections: Record<string, string> = {}
  for (const t of targets) {
    for (const f of t.findings || []) {
      if (!(f.check in labels)) order.push(f.check)
      labels[f.check] = f.label
      sections[f.check] = f.section
    }
  }
  return SECTION_ORDER.map((section) => ({
    section,
    rows: order
      .filter((check) => sections[check] === section)
      .map((check) => {
        const cells = targets.map((t) => (t.findings || []).find((f) => f.check === check))
        const limits = cells.find((c) => c?.limits.length)?.limits || []
        return { check, label: labels[check], cells, limits }
      }),
  })).filter((s) => s.rows.length > 0)
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** "Gözlenmedi" ölçülen derinlikle birlikte yazılır; ölçülemeyen sorgu "yok" gibi görünmez. */
export function rankText(row: VisibilityKeywordRow, site: string): string {
  if (row.error) return 'Ölçülmedi'
  const position = row.positions[site]
  return position ? `${position}. sıra` : `İlk ${row.checked_top}'de ${unseenText(row.unresolved)}`
}

/** Adresi okunamayan sonuç varsa "gözlenmedi" kesin yokluk gibi yazılmaz. */
export function unseenText(unresolved?: number): string {
  return unresolved ? `eşleşmedi (${unresolved} sonucun adresi okunamadı)` : 'gözlenmedi'
}

export function adsText(domains: string[] = [], unresolved?: number): string {
  return [...domains, ...(unresolved ? [`${unresolved} reklamın adresi okunamadı`] : [])].join(', ')
}
