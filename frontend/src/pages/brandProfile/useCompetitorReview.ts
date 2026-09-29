/**
 * Rakipler kartının paylaşılan state hook'u + kanonik URL yardımcıları
 * (plan v13). Bileşenden ayrı dosyada — react-refresh yalnız component
 * export eden dosyalarla çalışır.
 *
 * Karar state'i kanonik URL anahtarına bağlıdır (dizi index'ine DEĞİL) —
 * URL değişince önceki URL'nin marka adı/kararı yeni URL'ye TAŞINMAZ.
 * Varsayılan türetme önceliği: kaydedilmiş kullanıcı kararı (authoritative)
 * → validation/preview detected_name (AI veya backend domain-fallback'i)
 * → boş manuel giriş. Varsayılan karar HER satırda 'block'.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  brandProfileApi,
  CompetitorDecisionPayload,
  CompetitorPreviewEntry,
  CompetitorUrlDecision,
} from '../../services/api'

// Backend anahtarı registrable domain (tldextract). TS tarafında PSL yok —
// yaygın ikinci-seviye TLD'ler için 3 etiket, aksi halde 2 etiket alınır.
// (Yalnız UI eşleştirmesi; sunucu kendi kanonik anahtarını kendisi üretir.)
const SECOND_LEVEL = new Set(['com', 'net', 'org', 'gov', 'edu', 'gen', 'co', 'web', 'av', 'bel'])

export function normalizeCompetitorUrl(url: string): string {
  const raw = (url || '').trim().toLowerCase()
  if (!raw) return ''
  let host = ''
  try {
    host = new URL(raw.includes('//') ? raw : `https://${raw}`).hostname
  } catch {
    return ''
  }
  if (host.startsWith('www.')) host = host.slice(4)
  const labels = host.split('.').filter(Boolean)
  if (labels.length < 2) return ''
  const takeThree =
    labels.length >= 3 &&
    SECOND_LEVEL.has(labels[labels.length - 2]) &&
    labels[labels.length - 1].length === 2
  return labels.slice(takeThree ? -3 : -2).join('.')
}

export function domainFallbackName(url: string): string {
  const key = normalizeCompetitorUrl(url)
  if (!key) return ''
  const label = key.split('.')[0]
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : ''
}

export interface CompetitorRowState {
  term: string
  decision: 'block' | 'not_competitor'
  detecting?: boolean
}

export interface ValidationCompetitor {
  url?: string
  status?: string
  summary?: string
  detected_name?: string
}

function isSavedDecision(value: unknown): value is CompetitorUrlDecision {
  // Type-guard (plan v13): sunucudan gelen serbest JSON — bozuk/eski kayıt
  // UI'ı çökertmez, satır AI-tespiti/boş-manuel yoluna düşer.
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.url === 'string' &&
    typeof v.term === 'string' &&
    (v.decision === 'block' || v.decision === 'not_competitor')
  )
}

export interface UseCompetitorReview {
  urls: string[]
  setUrl: (index: number, value: string) => void
  rowFor: (url: string) => CompetitorRowState | null
  setTerm: (url: string, term: string) => void
  setDecision: (url: string, decision: 'block' | 'not_competitor') => void
  validationFor: (url: string) => ValidationCompetitor | null
  duplicateError: string | null
  emptyTermError: boolean
  isValid: boolean
  detecting: boolean
  runDetection: () => void
  buildPayload: () => {
    competitor_urls: string[]
    competitor_decisions: CompetitorDecisionPayload[]
  }
}

export function useCompetitorReview(workspace: {
  id: number
  competitor_urls?: string[] | null
  competitor_url_decisions?: Record<string, unknown> | null
  validation_data?: Record<string, unknown> | null
}): UseCompetitorReview {
  const initialUrls = workspace.competitor_urls || []
  const [urls, setUrls] = useState<string[]>([
    initialUrls[0] || '',
    initialUrls[1] || '',
    initialUrls[2] || '',
  ])
  const [rows, setRows] = useState<Record<string, CompetitorRowState>>({})
  const [detecting, setDetecting] = useState(false)
  const [previewNames, setPreviewNames] = useState<Record<string, string>>({})
  const pollRef = useRef<number | null>(null)

  // Workspace verisi SONRADAN gelirse/değişirse (modal reuse, poll refresh,
  // liste yanıtının gecikmesi) URL slotları ve dokunulmuş kararlar yeniden
  // kurulur — aksi halde boş başlayan kart, tam-replacement kaydında mevcut
  // URL/kararları SİLERDİ (Codex blocker'ı).
  const urlsSignature = (workspace.competitor_urls || []).join('\n')
  useEffect(() => {
    const fresh = workspace.competitor_urls || []
    setUrls([fresh[0] || '', fresh[1] || '', fresh[2] || ''])
    setRows({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id, urlsSignature])

  const validation = (workspace.validation_data || null) as {
    competitors?: ValidationCompetitor[]
  } | null

  const savedDecisions = ((): Record<string, CompetitorUrlDecision> => {
    // Kalıcı kararlar workspace yanıtındaki competitor_url_decisions
    // kolonundan gelir (validation_data saf AI verisidir).
    const raw = workspace.competitor_url_decisions
    if (!raw || typeof raw !== 'object') return {}
    const out: Record<string, CompetitorUrlDecision> = {}
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      if (isSavedDecision(value)) out[key] = value
    }
    return out
  })()

  const validationFor = useCallback(
    (url: string): ValidationCompetitor | null => {
      const key = normalizeCompetitorUrl(url)
      if (!key) return null
      for (const comp of validation?.competitors || []) {
        if (normalizeCompetitorUrl(comp.url || '') === key) return comp
      }
      return null
    },
    [validation]
  )

  const defaultRowFor = useCallback(
    (url: string): CompetitorRowState => {
      const key = normalizeCompetitorUrl(url)
      const saved = key ? savedDecisions[key] : undefined
      if (saved) {
        // Kaydedilmiş kullanıcı kararı AUTHORITATIVE — AI tespiti ezilmez
        return { term: saved.term, decision: saved.decision }
      }
      const comp = validationFor(url)
      const preview = key ? previewNames[key] : undefined
      const name = preview || (comp?.detected_name || '').trim() || domainFallbackName(url)
      return { term: name, decision: 'block' }
    },
    // savedDecisions her render'da yeniden türetilir ama içeriği workspace'e bağlı
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [validationFor, previewNames, workspace.id]
  )

  const rowFor = useCallback(
    (url: string): CompetitorRowState | null => {
      const key = normalizeCompetitorUrl(url)
      if (!key) return null
      return rows[key] || defaultRowFor(url)
    },
    [rows, defaultRowFor]
  )

  const setUrl = (index: number, value: string) => {
    setUrls((prev) => prev.map((u, i) => (i === index ? value : u)))
    // Eski URL anahtarındaki state bellekte kalabilir — submit yalnız o anki
    // dolu URL'leri gezdiği için payload'a sızmaz.
  }

  const setTerm = (url: string, term: string) => {
    const key = normalizeCompetitorUrl(url)
    if (!key) return
    setRows((prev) => ({ ...prev, [key]: { ...(prev[key] || defaultRowFor(url)), term } }))
  }

  const setDecision = (url: string, decision: 'block' | 'not_competitor') => {
    const key = normalizeCompetitorUrl(url)
    if (!key) return
    setRows((prev) => ({ ...prev, [key]: { ...(prev[key] || defaultRowFor(url)), decision } }))
  }

  const filledUrls = urls.map((u) => u.trim()).filter(Boolean)
  const keys = filledUrls.map(normalizeCompetitorUrl)
  const duplicateError = keys.some((k) => !k)
    ? 'Geçersiz rakip URL girdiniz'
    : new Set(keys).size !== keys.length
      ? 'Aynı rakip URL birden fazla kez girilemez'
      : null
  const emptyTermError = filledUrls.some((url) => {
    const row = rowFor(url)
    return row?.decision === 'block' && !row.term.trim()
  })
  const isValid = !duplicateError && !emptyTermError

  const runDetection = useCallback(() => {
    const targets = urls.map((u) => u.trim()).filter(Boolean)
    if (!targets.length || detecting) return
    setDetecting(true)
    brandProfileApi
      .startCompetitorPreview(workspace.id, targets)
      .then((res) => {
        const taskId = res.data.task_id
        let tries = 0
        const poll = () => {
          tries += 1
          brandProfileApi
            .getCompetitorPreview(workspace.id, taskId)
            .then((statusRes) => {
              const { status, result } = statusRes.data
              if (status === 'completed' && Array.isArray(result)) {
                const names: Record<string, string> = {}
                for (const entry of result as CompetitorPreviewEntry[]) {
                  const key = normalizeCompetitorUrl(entry.url)
                  if (key && entry.detected_name) names[key] = entry.detected_name
                }
                setPreviewNames((prev) => ({ ...prev, ...names }))
                setDetecting(false)
              } else if (status === 'failed' || tries > 15) {
                // Fail-open: domain-fallback zaten görünüyor
                setDetecting(false)
              } else {
                pollRef.current = window.setTimeout(poll, 2000)
              }
            })
            .catch(() => setDetecting(false))
        }
        pollRef.current = window.setTimeout(poll, 2000)
      })
      .catch(() => setDetecting(false))
  }, [urls, detecting, workspace.id])

  useEffect(
    () => () => {
      if (pollRef.current) window.clearTimeout(pollRef.current)
    },
    []
  )

  const buildPayload = () => {
    const currentUrls = urls.map((u) => u.trim()).filter(Boolean)
    const decisions: CompetitorDecisionPayload[] = currentUrls.map((url) => {
      const row = rowFor(url) || { term: '', decision: 'block' as const }
      return { url, term: row.term.trim(), decision: row.decision }
    })
    return { competitor_urls: currentUrls, competitor_decisions: decisions }
  }

  return {
    urls,
    setUrl,
    rowFor,
    setTerm,
    setDecision,
    validationFor,
    duplicateError,
    emptyTermError,
    isValid,
    detecting,
    runDetection,
    buildPayload,
  }
}
