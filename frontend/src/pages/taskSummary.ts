// Görev kartı: ham result_data yerine kısa özet + sonuca bağlantı.
// Yalnız kayıtta gerçekten bulunan alanlardan özet üretilir; eski kayıtta
// alan yoksa özet boş kalır (tahmin edilmez).

export interface TaskLike {
  task_type: string
  status: string
  scoring_run_id?: number | null
  result_data?: Record<string, unknown> | null
}

export interface TaskSummary {
  summary: string | null
  warnings: string[]
  link: { to: string; label: string } | null
}

const num = (v: unknown): number | null => (typeof v === 'number' && isFinite(v) ? v : null)
const arrLen = (v: unknown): number | null => (Array.isArray(v) ? v.length : null)
const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null

function channelCounts(rd: Record<string, unknown>): {
  summary: string | null
  unfilled: string[]
} {
  // delivery_summary bazı kayıtlarda bir kat daha iç içe
  const outer = obj(rd.delivery_summary)
  const channels = obj(obj(outer?.delivery_summary)?.channels) || obj(outer?.channels)
  if (!channels) return { summary: null, unfilled: [] }
  const parts: string[] = []
  const unfilled: string[] = []
  for (const [name, raw] of Object.entries(channels)) {
    const c = obj(raw)
    const kept = num(c?.kept_count)
    if (kept === null) continue
    parts.push(`${name} ${kept}`)
    if ((num(c?.unfilled_count) ?? 0) > 0) unfilled.push(name)
  }
  return {
    summary: parts.length ? `Kanal havuzları: ${parts.join(' · ')} kelime.` : null,
    unfilled,
  }
}

export function summarizeTask(task: TaskLike): TaskSummary {
  const empty: TaskSummary = { summary: null, warnings: [], link: null }
  if (task.status !== 'completed') return empty
  const rd = task.result_data || {}
  const runId = task.scoring_run_id
  const run = runId ? `run_id=${runId}` : ''

  switch (task.task_type) {
    case 'seo_content': {
      const total = num(rd.total)
      const requested = num(rd.requested_count)
      const blocked = num(rd.claim_blocked_count) ?? 0
      // failed_keywords listesi sunucuda kırpılır; eksik sayısı istenen − üretilen
      const missing = total !== null && requested !== null ? requested - total : 0
      return {
        summary:
          total === null
            ? null
            : missing > 0
              ? `${requested} kelimeden ${total} tanesi için SEO içeriği üretildi.`
              : `${total} SEO içeriği üretildi.`,
        warnings: [
          missing > 0 ? `${missing} kelime için içerik üretilemedi.` : null,
          blocked > 0
            ? `${blocked} içerik, iddia kontrolü nedeniyle yayın öncesi inceleme gerektiriyor.`
            : null,
        ].filter((w): w is string => w !== null),
        link: runId ? { to: `/seo-geo?${run}`, label: 'İçerikleri görüntüle' } : null,
      }
    }
    case 'ads': {
      const groups = num(rd.ad_group_count)
      const version = num(rd.version_number)
      const failed = num(rd.failed_groups) ?? 0
      const setId = num(rd.generation_set_id)
      return {
        summary:
          groups !== null
            ? `${groups} reklam grubu üretildi${version !== null ? ` (sürüm ${version})` : ''}.`
            : null,
        warnings: failed > 0 ? [`${failed} reklam grubu üretilemedi.`] : [],
        // Geçmiş görev, o an aktif olan sete değil kendi ürettiği sete götürür
        link: runId
          ? {
              to: `/ads?${run}${setId !== null ? `&set_id=${setId}` : ''}`,
              label: 'Reklam setini görüntüle',
            }
          : null,
      }
    }
    case 'social_content': {
      const total = num(rd.total)
      const unsaved = arrLen(rd.warnings) ?? 0
      return {
        summary: total !== null ? `${total} sosyal içerik üretildi.` : null,
        warnings:
          unsaved > 0 ? [`${unsaved} içerik doğrulanamayan iddia nedeniyle kaydedilmedi.`] : [],
        link: runId ? { to: `/social?${run}`, label: 'Sosyal içerikleri görüntüle' } : null,
      }
    }
    case 'channel_assignment': {
      const { summary, unfilled } = channelCounts(rd)
      return {
        summary,
        warnings: unfilled.length
          ? [
              `${unfilled.join(', ')} kanalında yeterli uygun kelime bulunamadı; havuz kapasitesi dolmadı.`,
            ]
          : [],
        link: runId ? { to: `/keywords?view=scores&${run}`, label: 'Skorları görüntüle' } : null,
      }
    }
    case 'corpus_screening':
      return {
        ...empty,
        link: runId ? { to: `/keywords?view=scores&${run}`, label: 'Skorları görüntüle' } : null,
      }
    case 'competitor_discovery': {
      const found = arrLen(rd.candidates)
      return {
        summary: found !== null ? `${found} rakip adayı bulundu.` : null,
        warnings: [],
        link: { to: '/brand-profile', label: 'Marka profiline git' },
      }
    }
    case 'policy_preview':
      return { ...empty, link: { to: '/brand-profile', label: 'Marka profiline git' } }
    default:
      return empty
  }
}
