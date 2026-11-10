/**
 * Sosyal brief akışı için Türkçe durum, gerekçe ve hata metinleri.
 * Backend kararlarını yeniden üretmez; yalnız kodları okunur metne çevirir.
 */
import { apiErrorCode, apiErrorMessage, apiErrorStatus } from '../../../services/api'
import type { SocialAttemptStatus } from '../../../services/api'

export const ATTEMPT_STATUS_LABEL: Record<SocialAttemptStatus, string> = {
  pending: 'Sırada',
  running: 'Çalışıyor',
  completed: 'Tamamlandı',
  partial: 'Kısmen tamamlandı',
  failed: 'Başarısız',
}

export function isActiveStatus(status: string | null | undefined): boolean {
  return status === 'pending' || status === 'running'
}

// Deneme / uyarı gerekçe kodları → kullanıcı metni. Bilinmeyen kod ekrana basılmaz.
const REASON_TEXT: Record<string, string> = {
  target_unfilled: 'Bu hedef için geçerli fikir üretilemedi.',
  category_unfilled: 'Bu kategori için geçerli fikir üretilemedi.',
  ai_failed: 'Yapay zekâ bu hedef için yanıt üretemedi.',
  idea_input_invalid: 'Bu brief ile fikir üretilemedi. Yeni bir brief oluşturup tekrar deneyin.',
  idea_provider_error: 'Yapay zekâ servisi hata verdi. Yeniden deneyebilirsiniz.',
  idea_output_invalid: 'Yapay zekâ çıktısı brief kurallarına uymadı.',
  idea_heartbeat_failed:
    'Fikir üretimi yarıda kesildi; sunucu yanıt vermeyi bıraktı. Yeniden deneyebilirsiniz.',
  idea_persistence_failed: 'Fikirler kaydedilemedi.',
  content_rejected:
    'İçerik, doğrulanmamış iddia (sayı, üstünlük, garanti) içerdiği için kaydedilmedi.',
  content_input_invalid:
    'Brief verisi içerik üretimine uygun değil (ör. marka notları çok uzun). Notları kısaltıp yeni bir brief oluşturun.',
  content_provider_error: 'Yapay zekâ servisi hata verdi. Yeniden deneyebilirsiniz.',
  content_output_invalid: 'Yapay zekâ çıktısı format kurallarına uymadı.',
  content_quality_invalid: 'İçerik kalite kontrolünden geçemedi.',
  content_repair_input_invalid: 'İçerik otomatik olarak düzeltilemedi.',
  content_repair_provider_error: 'Düzeltme sırasında yapay zekâ servisi hata verdi.',
  content_repair_output_invalid: 'Düzeltme çıktısı format kurallarına uymadı.',
  content_execution_inconsistent: 'Üretim tamamlanamadı; sonuç kaydedilmedi.',
  content_heartbeat_failed:
    'İçerik üretimi yarıda kesildi; sunucu yanıt vermeyi bıraktı. Yeniden deneyebilirsiniz.',
  content_persistence_failed: 'İçerik kaydedilemedi.',
  content_generation_failed: 'İçerik üretilemedi.',
  content_partial: 'Bazı fikirler için içerik üretilemedi.',
  worker_lost:
    'Üretim yarıda kesildi; sunucu yanıt vermeyi bıraktı. Yeniden deneyin; sürerse sistem yöneticinize bildirin.',
  brief_stale: 'Kanal ataması değiştiği için sonuç kaydedilmedi.',
  assignment_changed: 'Kanal ataması değiştiği için sonuç kaydedilmedi.',
  worker_bootstrap_failed: 'Üretim başlatılamadı. Yeniden deneyebilirsiniz.',
  dispatch_failed: 'Üretim başlatılamadı. Yeniden deneyebilirsiniz.',
  voiceover_duration_mismatch:
    'Seslendirme metninin uzunluğu seçilen süreyle kabaca uyuşmuyor (uyarı, içerik kaydedildi).',
}

export function reasonText(code: string | null | undefined): string {
  if (!code) return ''
  if (code.startsWith('claim_risk:'))
    return `Risk — yayın öncesi düzelt: ${code.slice('claim_risk:'.length)}`
  if (code.startsWith('claim_unverified:'))
    return `Profilde doğrulanamadı — kontrol gerekli: ${code.slice('claim_unverified:'.length)}`
  return REASON_TEXT[code] ?? 'Bu adım tamamlanamadı.'
}

// Onarım öncesi/sonrası metin: claim_review:<json> kaydındaki `current.changed` (yoksa `applied`)
export interface ClaimRepairItem {
  field: string
  original: string
  suggestion: string
}

const CLAIM_FIELD_LABEL: Record<string, string> = {
  caption: 'Açıklama',
  cta_text: 'CTA',
  visual_suggestion: 'Görsel',
  scenario: 'Senaryo',
  video_concept: 'Video konsepti',
  platform_notes: 'Platform notları',
  industry_posting_suggestion: 'Paylaşım önerisi',
}
const CLAIM_INDEXED_LABEL: Record<string, string> = {
  hook: 'Kanca',
  hashtag: 'Etiket',
  payload: 'Format metni',
}

export function claimFieldLabel(field: string): string {
  const m = /^(\w+)\[(\d+)\]$/.exec(field)
  if (m && CLAIM_INDEXED_LABEL[m[1]]) {
    const n = Number(m[2]) + 1
    return `${CLAIM_INDEXED_LABEL[m[1]]} ${n}`
  }
  return CLAIM_FIELD_LABEL[field] ?? field
}

export function parseClaimRepairs(warnings: string[]): ClaimRepairItem[] {
  const raw = warnings.find((w) => w.startsWith('claim_review:'))
  if (!raw) return []
  try {
    const current = (JSON.parse(raw.slice('claim_review:'.length)) as { current?: unknown })
      .current as
      | {
          changed?: Partial<ClaimRepairItem>[]
          applied?: (Partial<ClaimRepairItem> & { span?: string })[]
        }
      | undefined
    const rows = current?.changed?.length
      ? current.changed
      : (current?.applied ?? []).map((a) => ({ ...a, field: a.span ?? '' }))
    return rows
      .filter((r) => r.original || r.suggestion)
      .map((r) => ({
        field: r.field ?? '',
        original: r.original ?? '',
        suggestion: r.suggestion ?? '',
      }))
  } catch {
    return []
  }
}

// Tipli hata kodları → açıklayıcı Türkçe metin
const ERROR_TEXT: Record<string, string> = {
  FEATURE_DISABLED: 'Sosyal brief akışı bu ortamda kapalı.',
  BRIEF_NOT_FOUND: 'Brief bulunamadı veya bu marka çalışmasına ait değil.',
  BRIEF_STALE: 'Kanal havuzu değiştiği için bu brief güncel değil. Yeni bir brief oluşturun.',
  CONTENT_BRIEF_STALE:
    'Kanal havuzu değiştiği için bu brief güncel değil. Yeni bir brief oluşturun.',
  ASSIGNMENT_CHANGED:
    'Kanal ataması bu brief oluşturulduktan sonra değişti. Yeni bir brief oluşturun.',
  CONTENT_ASSIGNMENT_CHANGED:
    'Kanal ataması bu brief oluşturulduktan sonra değişti. Yeni bir brief oluşturun.',
  CATEGORIES_ALREADY_GENERATED: 'Bu brief için kategoriler zaten üretilmiş.',
  CATEGORY_ATTEMPT_TERMINAL:
    'Önceki kategori üretimi başarısız oldu. Yeniden başlatarak tekrar deneyebilirsiniz.',
  CATEGORIES_NOT_READY: 'Önce kategori üretimi tamamlanmalı.',
  IDEAS_ALREADY_GENERATED: 'Bu brief için fikirler zaten üretilmiş.',
  IDEA_ATTEMPT_TERMINAL:
    'Önceki fikir üretimi sonuçlandı. Yeniden başlatarak tekrar deneyebilirsiniz.',
  IDEA_ATTEMPT_REQUEST_MISMATCH:
    'Bu istek daha önce farklı seçimlerle gönderilmiş. Sayfayı yenileyip seçiminizi kontrol ederek yeniden başlatın.',
  IDEA_RETRY_NOT_NEEDED:
    'Tüm hedefler ve kategoriler en az bir fikir aldı; tekrar denemeye gerek yok.',
  IDEA_RETRY_SOURCE_NOT_TERMINAL: 'Fikir üretimi bitmeden eksik hedefler tekrar denenemez.',
  IDEA_RETRY_SOURCE_NOT_FOUND: 'Tekrar denenecek fikir üretimi bulunamadı.',
  IDEA_RETRY_ATTEMPT_TERMINAL:
    'Önceki tekrar deneme sonuçlandı. Kalan eksik hedefler için yeniden deneyebilirsiniz.',
  ATTEMPT_CONFLICT: 'Bu brief için şu anda başka bir üretim çalışıyor. Bitmesini bekleyin.',
  CONTENT_ATTEMPT_TERMINAL:
    'Önceki içerik üretimi sonuçlandı. Kalan fikirler için yeniden başlatabilirsiniz.',
  CONTENT_ATTEMPT_REQUEST_MISMATCH:
    'Bu istek daha önce farklı seçimlerle gönderilmiş. Sayfayı yenileyip seçiminizi kontrol ederek yeniden başlatın.',
  CONTENT_IDEA_NOT_ELIGIBLE: 'Seçilen fikirlerden biri artık içerik üretimine uygun değil.',
  CONTENT_IDEAS_INVALID: 'Fikir seçimi geçersiz. 1 ile 30 arasında fikir seçin.',
  CONTENT_DISPATCH_FAILED: 'İçerik üretimi başlatılamadı. Tekrar deneyin.',
  IDEA_DISPATCH_FAILED: 'Fikir üretimi başlatılamadı. Tekrar deneyin.',
  IDEA_RETRY_DISPATCH_FAILED: 'Eksik fikirlerin üretimi başlatılamadı. Tekrar deneyin.',
  CATEGORY_PROVIDER_ERROR: 'Yapay zekâ servisi hata verdi. Yeniden deneyebilirsiniz.',
  CATEGORY_OUTPUT_INVALID:
    'Yapay zekâ çıktısı kategori kurallarına uymadı. Yeniden deneyebilirsiniz.',
}

/** Tipli API hatasını kullanıcıya gösterilecek Türkçe metne çevirir. */
export function socialErrorText(err: unknown, fallback: string): string {
  const code = apiErrorCode(err)
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  const status = apiErrorStatus(err)
  if (status === 0) {
    return 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edip aynı işlemi güvenle tekrar deneyebilirsiniz.'
  }
  if (status >= 500) {
    // Backend'in güvenli Türkçe metni varsa onu göster; yoksa genel metin
    const detail = (err as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) return detail
    return 'Sunucu hatası oluştu. Aynı işlemi güvenle tekrar deneyebilirsiniz.'
  }
  return apiErrorMessage(err, fallback)
}

export function isStaleError(err: unknown): boolean {
  const code = apiErrorCode(err)
  return (
    code === 'BRIEF_STALE' ||
    code === 'CONTENT_BRIEF_STALE' ||
    code === 'ASSIGNMENT_CHANGED' ||
    code === 'CONTENT_ASSIGNMENT_CHANGED'
  )
}

export const DURATION_STATUS_TEXT: Record<string, string> = {
  valid: 'Süre seçilen aralıkta',
  mismatch: 'Süre seçilen aralığın dışında',
  unparseable: 'Süre hesaplanamadı',
  not_applicable: '',
}

// Format matrisi yüklenmemiş ekranlar (ör. geçmiş) için yedek etiketler
const PLATFORM_FALLBACK: Record<string, string> = {
  instagram: 'Instagram',
  tiktok: 'TikTok',
  twitter: 'X',
  linkedin: 'LinkedIn',
  youtube: 'YouTube',
}
// Backend format matrisiyle (app/generators/social/format_matrix.py) AYNI
// etiketler — brief ekranı matrisi, geçmiş bu yedeği kullanır; ikisi ayrışmasın.
const FORMAT_FALLBACK: Record<string, string> = {
  post: 'Post',
  carousel: 'Carousel',
  reels: 'Reels',
  story: 'Story (statik)',
  short: 'Short',
  video: 'Video',
  thread: 'Thread',
}

export function fallbackPlatformLabel(id: string | null | undefined): string {
  if (!id) return 'Platform bilinmiyor'
  return PLATFORM_FALLBACK[id] ?? id
}

export function fallbackFormatLabel(id: string | null | undefined): string {
  if (!id) return 'Format bilinmiyor'
  return FORMAT_FALLBACK[id] ?? id
}

// Backend CANONICAL_CATEGORY_TYPES (app/core/social/category_contract.py)
const CATEGORY_TYPE_LABEL: Record<string, string> = {
  educational: 'Eğitici',
  product_benefit: 'Ürün faydası',
  social_proof: 'Sosyal kanıt',
  brand_story: 'Marka hikâyesi',
  community: 'Topluluk',
  trending: 'Gündem',
}

export function categoryTypeLabel(id: string | null | undefined): string {
  if (!id) return ''
  return CATEGORY_TYPE_LABEL[id] ?? id
}

// Backend ALLOWED_HOOK_STYLES (app/core/social/content_contract.py)
const HOOK_STYLE_LABEL: Record<string, string> = {
  question: 'Soru',
  shocking: 'Şaşırtıcı',
  relatable: 'Özdeşleşme',
  curiosity: 'Merak',
}

export function hookStyleLabel(id: string | null | undefined): string {
  if (!id) return ''
  return HOOK_STYLE_LABEL[id] ?? id
}

export function formatSeconds(sec: number): string {
  if (sec < 60) return `${sec} sn`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return s ? `${m} dk ${s} sn` : `${m} dk`
}
