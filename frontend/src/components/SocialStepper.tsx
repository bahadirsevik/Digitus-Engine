import { useState, useEffect, useRef } from 'react'
import {
  ArrowLeft,
  Check,
  Clapperboard,
  FileText,
  Plus,
  RefreshCw,
  Sparkles,
  Tag,
  Target,
  X,
} from 'lucide-react'
import { useSocialStore } from '../stores/socialStore'
import { useBrandStore } from '../stores/brandStore'
import {
  apiErrorMessage as sharedApiErrorMessage,
  brandProfileApi,
  generationApi,
  policyStaleFromError,
  scoringApi,
} from '../services/api'
import { getStoredTaskId, setStoredTaskId, useTaskPolling } from '../hooks/useTaskPolling'
import type { ScoringRun } from '../types/models'
import type { Idea } from '../stores/socialStore'
import ErrorBanner from './ErrorBanner'
import './SocialStepper.css'

// Grounding reddi uyarısı (Faz F) — task result_data.warnings sözleşmesi
interface ClaimWarning {
  idea_id: number
  reason_code: string
  claims: string[]
}

// Topic-policy reddi (plan B) — tipli sözleşme
interface PolicyWarning {
  entity_type: string
  entity_id?: number | null
  client_ref?: string | null
  display_name: string
  reason_code: string
  matched_term?: string | null
}

interface SocialStepperProps {
  disabled?: boolean
  // Kanal sayfasında run seçimi sayfanın TEK seçicisinden gelir (SocialPanel
  // store'a senkronlar); stepper'ın kendi "Skorlama Çalışması" seçicisi
  // gizlenir ki iki seçici çelişmesin.
  hideRunSelector?: boolean
}

export default function SocialStepper({
  disabled = false,
  hideRunSelector = false,
}: SocialStepperProps) {
  const store = useSocialStore()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<ScoringRun[]>([])
  // Marka bağlamı chip editörü (tasarım) — backend'e satır-birleştirilmiş
  // tek string gider (mevcut brand_context alanı, şema değişikliği yok)
  const [ctxAdding, setCtxAdding] = useState(false)
  const [ctxDraft, setCtxDraft] = useState('')
  // Faz F: desteklenmeyen iddia nedeniyle KAYDEDİLMEYEN içerik uyarıları
  const [claimWarnings, setClaimWarnings] = useState<ClaimWarning[]>([])
  const [policyWarnings, setPolicyWarnings] = useState<PolicyWarning[]>([])
  const ctxInputRef = useRef<HTMLInputElement>(null)
  const currentWorkspaceId = activeWorkspace?.id ?? null

  // P7: içerik üretimi Celery'de — task key RUN bazlı (workspace-key sızıntısı
  // yeni akışa taşınmaz; run değişince eski task bandı görünmez)
  const contentsTaskKey =
    currentWorkspaceId && store.scoringRunId
      ? `social_content:${currentWorkspaceId}:${store.scoringRunId}`
      : 'social_content'
  const contentsPolling = useTaskPolling(
    store.taskId,
    contentsTaskKey,
    3000,
    currentWorkspaceId ?? undefined
  )

  // Sayfaya dönüşte süren içerik task'ını key'den keşfet
  useEffect(() => {
    const stored = getStoredTaskId(contentsTaskKey)
    if (stored && !useSocialStore.getState().taskId) {
      useSocialStore.getState().setTaskId(stored)
    }
  }, [contentsTaskKey])

  // Task bitince: yalnız YENİ üretilen içerikleri (result_data.content_ids)
  // çekip 4. adıma geç — run'ın eski içerikleri karışmaz (sync davranış korunur)
  useEffect(() => {
    if (!contentsPolling.isCompleted || !store.taskId) return
    const runId = store.scoringRunId
    const contentIds = ((contentsPolling.resultData?.content_ids as number[]) || []).filter(
      (id) => typeof id === 'number'
    )
    // Grounding reddi uyarıları (Faz F): kaydedilmeyen içerikler
    setClaimWarnings((contentsPolling.resultData?.warnings as ClaimWarning[]) || [])
    setPolicyWarnings((contentsPolling.resultData?.policy_warnings as PolicyWarning[]) || [])
    if (!runId || !currentWorkspaceId) return
    if (!contentIds.length) {
      setError('Üretilen içerik kimlikleri alınamadı; Görevler sayfasını kontrol edin.')
      store.setTaskId(null)
      return
    }
    generationApi
      .getSocialResults(runId, currentWorkspaceId)
      .then((res) => {
        const all = ((res.data as { contents?: unknown[] }).contents || []) as { id: number }[]
        const fresh = all.filter((c) => contentIds.includes(c.id))
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        store.setContents(fresh as any[])
        store.setStep(4)
      })
      .catch(() => setError('Üretilen içerikler alınamadı; Görevler sayfasını kontrol edin.'))
      .finally(() => store.setTaskId(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentsPolling.isCompleted, store.taskId])

  useEffect(() => {
    if (contentsPolling.isFailed && store.taskId) {
      setError(contentsPolling.errorMessage || 'İçerik üretimi başarısız oldu.')
      // Tüm içerikler claim nedeniyle reddedildiyse task failed olur ama
      // warnings result_data'da korunur — kullanıcıya gösterilir (Faz F)
      setClaimWarnings((contentsPolling.resultData?.warnings as ClaimWarning[]) || [])
      setPolicyWarnings((contentsPolling.resultData?.policy_warnings as PolicyWarning[]) || [])
      store.setTaskId(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentsPolling.isFailed, store.taskId])

  useEffect(() => {
    if (store.workspaceId !== currentWorkspaceId) {
      store.reset()
      if (currentWorkspaceId) {
        store.setFormData({ workspaceId: currentWorkspaceId })
      }
      setError(null)
    }
  }, [currentWorkspaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!activeWorkspace?.id) {
      setRuns([])
      return
    }
    scoringApi
      .listRuns({ brand_profile_id: activeWorkspace.id })
      .then((res) => setRuns(Array.isArray(res.data) ? res.data : []))
      .catch((e) => console.error('Failed to fetch runs:', e))
  }, [activeWorkspace?.id])

  // Auto-fill brand info from confirmed profile when scoring run changes
  useEffect(() => {
    if (!store.scoringRunId || !activeWorkspace?.id) return
    brandProfileApi
      .getProfile(store.scoringRunId, activeWorkspace.id)
      .then((res) => {
        const profile = res.data as { status?: string; profile_data?: Record<string, unknown> }
        if (profile.status === 'confirmed' && profile.profile_data) {
          const pd = profile.profile_data
          const updates: Record<string, unknown> = {}
          if (!store.brandName && pd.company_name) updates.brandName = pd.company_name
          if (!store.brandContext) {
            const parts: string[] = []
            if (pd.company_name) parts.push(`Marka: ${pd.company_name}`)
            if ((pd.products as string[] | undefined)?.length)
              parts.push(`Ürünler: ${(pd.products as string[]).join(', ')}`)
            if ((pd.use_cases as string[] | undefined)?.length)
              parts.push(`Kullanım: ${(pd.use_cases as string[]).join(', ')}`)
            if (parts.length) updates.brandContext = parts.join('\n')
          }
          if (Object.keys(updates).length) store.setFormData(updates)
        }
      })
      .catch(() => {
        /* profile not found — ok */
      })
  }, [store.scoringRunId, activeWorkspace?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const steps = [
    { num: 1, title: 'Marka Bilgisi', desc: 'Marka ve bağlam girin' },
    { num: 2, title: 'Kategori Seçimi', desc: 'Üretilen kategorileri seçin' },
    { num: 3, title: 'Fikir Seçimi', desc: 'İçerik üretilecek fikirleri seçin' },
  ]

  // Backend şema sınırları (app/schemas/social.py) — iş kararı, UI uyar
  const CATEGORY_MIN = 4
  const CATEGORY_MAX = 10
  const IDEAS_MIN = 3
  const IDEAS_MAX = 10

  // 422 Pydantic detayı merkezi interceptor'da okunur mesaja çevriliyor.
  // Plan v13: bayat havuz 409'u (POLICY_STALE nesne detayı) kendi mesajıyla
  // gösterilir — genel "Request failed" mesajına indirilmez.
  const apiErrorMessage = (err: unknown, fallback: string): string =>
    policyStaleFromError(err)?.message ?? sharedApiErrorMessage(err, fallback)

  const generateCategories = async () => {
    if (!activeWorkspace?.id) {
      setError('Marka çalışması seçin')
      return
    }
    if (store.maxCategories < CATEGORY_MIN || store.maxCategories > CATEGORY_MAX) {
      setError(`Kategori sayısı ${CATEGORY_MIN}–${CATEGORY_MAX} arasında olmalı.`)
      return
    }
    if (store.ideasPerCategory < IDEAS_MIN || store.ideasPerCategory > IDEAS_MAX) {
      setError(`Kategori başına fikir sayısı ${IDEAS_MIN}–${IDEAS_MAX} arasında olmalı.`)
      return
    }

    setLoading(true)
    setError(null)
    try {
      const res = await generationApi.createSocialCategories(
        {
          scoring_run_id: store.scoringRunId!,
          brand_name: store.brandName,
          brand_context: store.brandContext,
          max_categories: store.maxCategories,
        },
        activeWorkspace.id
      )
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      store.setCategories(((res.data as { categories?: unknown[] }).categories || []) as any[])
      store.setStep(2)
    } catch (err) {
      setError(apiErrorMessage(err, 'Kategori üretimi başarısız'))
    } finally {
      setLoading(false)
    }
  }

  const generateIdeas = async () => {
    if (!activeWorkspace?.id) {
      setError('Marka çalışması seçin')
      return
    }
    if (store.ideasPerCategory < IDEAS_MIN || store.ideasPerCategory > IDEAS_MAX) {
      setError(`Kategori başına fikir sayısı ${IDEAS_MIN}–${IDEAS_MAX} arasında olmalı.`)
      return
    }

    setLoading(true)
    setError(null)
    try {
      const res = await generationApi.createSocialIdeas(
        { category_ids: store.selectedCategoryIds, ideas_per_category: store.ideasPerCategory },
        store.brandName,
        activeWorkspace.id
      )
      const data = res.data as { ideas?: unknown[] } | Array<{ ideas?: unknown[] }>
      const allIdeas = Array.isArray(data)
        ? data.flatMap((cat) => cat.ideas || [])
        : data.ideas || []
      store.setIdeas(allIdeas as Idea[])
      store.setStep(3)
    } catch (err) {
      setError(apiErrorMessage(err, 'Fikir üretimi başarısız'))
    } finally {
      setLoading(false)
    }
  }

  const generateContents = async () => {
    if (!activeWorkspace?.id) {
      setError('Marka çalışması seçin')
      return
    }

    setLoading(true)
    setError(null)
    setClaimWarnings([])
    setPolicyWarnings([])
    // Başlatıldığı run'ın saklama anahtarı istek anında yakalanır
    const startKey = contentsTaskKey
    try {
      // P7: içerik üretimi Celery task'ı olarak koşar; polling tamamlanınca
      // yalnız yeni content_ids çekilip 4. adıma geçilir
      const res = await generationApi.createSocialContentsAsync(
        {
          idea_ids: store.selectedIdeaIds,
          brand_name: store.brandName,
        },
        activeWorkspace.id
      )
      const startedTaskId = (res.data as { task_id: string }).task_id
      // Görev kimliği her zaman başlatıldığı run'ın anahtarına yazılır (unmount/run değişimi
      // olsa bile kaybolmaz); global store yalnız kapsam hâlâ aynıysa güncellenir.
      setStoredTaskId(startKey, startedTaskId)
      const live = useSocialStore.getState()
      const liveWorkspaceId = useBrandStore.getState().activeWorkspace?.id ?? null
      const liveKey =
        liveWorkspaceId && live.scoringRunId
          ? `social_content:${liveWorkspaceId}:${live.scoringRunId}`
          : 'social_content'
      if (liveKey === startKey) store.setTaskId(startedTaskId)
    } catch (err) {
      setError(apiErrorMessage(err, 'İçerik üretimi başlatılamadı'))
    } finally {
      setLoading(false)
    }
  }

  const goBack = () => {
    if (store.step > 1) {
      store.setStep(store.step - 1)
    }
  }

  // --- chip editörü yardımcıları ---
  const contextItems = store.brandContext.split('\n').filter((line) => line.trim())
  const setContextItems = (items: string[]) => store.setFormData({ brandContext: items.join('\n') })
  const addContextItem = () => {
    const v = ctxDraft.trim()
    if (v && !contextItems.includes(v)) setContextItems([...contextItems, v])
    setCtxDraft('')
    ctxInputRef.current?.focus()
  }
  useEffect(() => {
    if (ctxAdding) ctxInputRef.current?.focus()
  }, [ctxAdding])

  const platformColor = (platform?: string) => {
    const p = (platform || '').toLowerCase()
    if (p.includes('insta')) return '#e1568c'
    if (p.includes('tiktok')) return '#5eead4'
    if (p.includes('linked') || p.includes('twitter') || p === 'x') return '#5b9be8'
    if (p.includes('youtube')) return '#f87171'
    return '#2dd4bf'
  }

  // Senaryo düz string gelir; "0-5sn: ..." benzeri satırları zaman+metin
  // olarak ayrıştır (tasarımdaki timeline görünümü), eşleşmeyen satır düz kalır
  const scenarioLines = (scenario: string) =>
    scenario
      .split('\n')
      .filter((line) => line.trim())
      .map((line) => {
        const m = line.match(/^\s*[([]?\s*([\d]+\s*[-–—]\s*[\d]+\s*sn)\s*[)\]]?\s*[:\-–]?\s*(.+)$/i)
        return m ? { time: m[1].replace(/\s+/g, ''), text: m[2] } : { time: null, text: line }
      })

  // İçerik fazı artık async task; banner senkron istek VE task sürerken görünür
  const contentsGenerating = Boolean(store.taskId) && !contentsPolling.isFailed
  const busy = loading || contentsGenerating
  const genLabel =
    store.step === 1
      ? 'Kategoriler üretiliyor'
      : store.step === 2
        ? 'Fikirler üretiliyor'
        : 'İçerikler üretiliyor'

  return (
    <div className="socx-root">
      {/* Stepper kartı (tasarım) */}
      <div className="socx-stepper chx-card">
        {steps.map((s, i) => {
          const state = store.step > s.num ? 'done' : store.step === s.num ? 'active' : 'todo'
          return (
            <div key={s.num} className="socx-step-wrap">
              <div className={`socx-step is-${state}`}>
                <span className="socx-step-circle">
                  {state === 'done' ? <Check size={16} strokeWidth={3} /> : s.num}
                </span>
                <div className="socx-step-text">
                  <div className="socx-step-title">{s.title}</div>
                  <div className="socx-step-sub">{s.desc}</div>
                </div>
              </div>
              {i < steps.length - 1 && (
                <div className={`socx-step-line${store.step > s.num ? ' is-done' : ''}`} />
              )}
            </div>
          )
        })}
      </div>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {/* Plan B: topic-policy redleri — hangi öğe hangi terimle düştü */}
      {policyWarnings.length > 0 && (
        <div className="socx-claim-warnings">
          <b>{policyWarnings.length} öğe politika nedeniyle üretilmedi/kaydedilmedi.</b>
          <ul>
            {policyWarnings.map((w, wi) => (
              <li key={wi}>
                <b>{w.display_name}</b>
                {' — '}
                {w.reason_code === 'stale_parent'
                  ? 'kanal ataması yenilendi (bayat)'
                  : `dışlanan konu${w.matched_term ? `: ${w.matched_term}` : ''}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Faz F: task failed olsa da (tümü reddedildi) uyarılar görünür */}
      {claimWarnings.length > 0 && store.step !== 4 && (
        <div className="socx-claim-warnings">
          <b>{claimWarnings.length} içerik desteklenmeyen iddia nedeniyle kaydedilmedi.</b> İlgili
          fikirleri yeniden üretebilirsiniz.
          <ul>
            {claimWarnings.map((w, wi) => {
              const idea = store.ideas.find((i) => i.id === w.idea_id)
              return (
                <li key={wi}>
                  <b>{idea?.idea_title || `Fikir #${w.idea_id}`}</b>
                  {w.claims.length > 0 && <> — tespit edilen iddia: {w.claims.join(', ')}</>}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* Adım gövdesi */}
      <div className="socx-body chx-card">
        {store.step === 1 && (
          <div className="socx-brand-form">
            {!hideRunSelector && (
              <label className="kwx-field">
                <span className="kwx-field-label">Skorlama Çalışması *</span>
                <select
                  className="kwx-input"
                  value={store.scoringRunId || ''}
                  onChange={(e) => store.setScoringRun(Number(e.target.value) || null)}
                >
                  <option value="">Seçin...</option>
                  {runs.map((run) => (
                    <option key={run.id} value={run.id}>
                      {run.run_name || `Çalışma #${run.id}`}
                      {run.created_at
                        ? ` — ${new Date(run.created_at).toLocaleDateString('tr-TR')}`
                        : ''}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <label className="kwx-field">
              <span className="kwx-field-label">Marka Adı</span>
              <input
                className="kwx-input"
                type="text"
                value={store.brandName}
                onChange={(e) => store.setFormData({ brandName: e.target.value })}
                placeholder="Profilden otomatik dolar veya manuel girin"
              />
            </label>

            {/* Marka Bağlamı — chip editörü (tasarım) */}
            <div>
              <div className="socx-ctx-head">
                <span className="kwx-field-label">Marka Bağlamı ({contextItems.length})</span>
                <button
                  type="button"
                  className={`kwx-add-toggle${ctxAdding ? ' active' : ''}`}
                  onClick={() => setCtxAdding((a) => !a)}
                >
                  <Plus size={14} strokeWidth={2.2} /> Bağlam Ekle
                </button>
              </div>
              <div className="kwx-chips">
                {contextItems.map((item) => (
                  <span key={item} className="kwx-chip">
                    {item}
                    <button
                      type="button"
                      className="kwx-chip-x"
                      title="Sil"
                      onClick={() => setContextItems(contextItems.filter((x) => x !== item))}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
                {ctxAdding && (
                  <span className="kwx-chip-add">
                    <input
                      ref={ctxInputRef}
                      value={ctxDraft}
                      onChange={(e) => setCtxDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          addContextItem()
                        }
                        if (e.key === 'Escape') {
                          setCtxDraft('')
                          setCtxAdding(false)
                        }
                      }}
                      onBlur={() => {
                        if (!ctxDraft.trim()) setCtxAdding(false)
                      }}
                      placeholder="Bağlam yaz…"
                      size={Math.max(ctxDraft.length, 14)}
                    />
                    <button
                      type="button"
                      className="kwx-chip-commit"
                      title="Ekle"
                      onMouseDown={(e) => {
                        e.preventDefault()
                        addContextItem()
                      }}
                    >
                      <Plus size={13} strokeWidth={2.6} />
                    </button>
                  </span>
                )}
                {contextItems.length === 0 && !ctxAdding && (
                  <span className="kwx-chips-empty">
                    Henüz bağlam yok — "Bağlam Ekle" ile başlayın.
                  </span>
                )}
              </div>
            </div>

            <div className="socx-grid-2">
              <label className="kwx-field">
                <span className="kwx-field-label">Maks Kategori (4–10)</span>
                <input
                  className="kwx-input"
                  type="number"
                  min={CATEGORY_MIN}
                  max={CATEGORY_MAX}
                  value={store.maxCategories}
                  onChange={(e) => store.setFormData({ maxCategories: Number(e.target.value) })}
                />
              </label>
              <label className="kwx-field">
                <span className="kwx-field-label">Kategori Başına Fikir (3–10)</span>
                <input
                  className="kwx-input"
                  type="number"
                  min={IDEAS_MIN}
                  max={IDEAS_MAX}
                  value={store.ideasPerCategory}
                  onChange={(e) => store.setFormData({ ideasPerCategory: Number(e.target.value) })}
                />
              </label>
            </div>
          </div>
        )}

        {store.step === 2 && (
          <div>
            <h3 className="socx-body-title">Fikir üretmek istediğiniz kategorileri seçin:</h3>
            {store.categories.length === 0 ? (
              <p className="empty-state">Henüz kategori üretilmedi</p>
            ) : (
              <div className="socx-select-list">
                {store.categories.map((cat) => {
                  const on = store.selectedCategoryIds.includes(cat.id)
                  return (
                    <button
                      type="button"
                      key={cat.id}
                      className={`socx-select-row${on ? ' is-on' : ''}`}
                      onClick={() => store.toggleCategory(cat.id)}
                    >
                      <span className="socx-checkbox">
                        {on && <Check size={12} strokeWidth={3} />}
                      </span>
                      <span className="socx-row-main">
                        <span className="socx-row-title">{cat.name}</span>
                        <span className="socx-row-desc">{cat.description}</span>
                      </span>
                      <span className="socx-count-pill">
                        <Tag size={11} /> {cat.keyword_count} kelime
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {store.step === 3 && (
          <div>
            <h3 className="socx-body-title">İçerik üretmek istediğiniz fikirleri seçin:</h3>
            {store.ideas.length === 0 ? (
              <p className="empty-state">Henüz fikir üretilmedi</p>
            ) : (
              <div className="socx-select-list">
                {store.ideas.map((idea) => {
                  const on = store.selectedIdeaIds.includes(idea.id)
                  return (
                    <button
                      type="button"
                      key={idea.id}
                      className={`socx-select-row is-idea${on ? ' is-on' : ''}`}
                      onClick={() => store.toggleIdea(idea.id)}
                    >
                      <span className="socx-checkbox">
                        {on && <Check size={12} strokeWidth={3} />}
                      </span>
                      <span className="socx-row-main">
                        <span className="socx-row-title lg">{idea.idea_title}</span>
                        <span className="socx-row-desc">{idea.idea_description}</span>
                      </span>
                      <span className="socx-idea-meta">
                        <span className="socx-idea-score">
                          <span
                            className="socx-platform-dot"
                            style={{ background: platformColor(idea.target_platform) }}
                          />
                          <span className="chx-mono">
                            {(idea.trend_alignment * 100).toFixed(0)}%
                          </span>
                        </span>
                        <span className="socx-idea-platform">
                          {(idea.target_platform || '').toUpperCase()} •{' '}
                          {(idea.content_format || '').toUpperCase()}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {store.step === 4 && (
          <div>
            <div className="socx-result-head">
              <Check size={18} strokeWidth={2.2} />
              <h2>{store.contents.length} İçerik Üretildi!</h2>
            </div>
            {claimWarnings.length > 0 && (
              <div className="socx-claim-warnings">
                <b>{claimWarnings.length} içerik desteklenmeyen iddia nedeniyle kaydedilmedi.</b>{' '}
                İlgili fikirleri 3. adımdan yeniden üretebilirsiniz.
                <ul>
                  {claimWarnings.map((w, wi) => {
                    const idea = store.ideas.find((i) => i.id === w.idea_id)
                    return (
                      <li key={wi}>
                        <b>{idea?.idea_title || `Fikir #${w.idea_id}`}</b>
                        {w.claims.length > 0 && <> — tespit edilen iddia: {w.claims.join(', ')}</>}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
            {store.contents.length === 0 ? (
              <p className="empty-state">İçerik üretilemedi</p>
            ) : (
              store.contents.map((content, idx) => (
                <div key={content.id || idx} className="socx-content-card">
                  <div className="socx-content-no">İçerik #{idx + 1}</div>

                  {content.hooks && content.hooks.length > 0 && (
                    <div className="socx-block">
                      <div className="socx-block-title">
                        <Sparkles size={14} /> Hook'lar
                      </div>
                      <div className="socx-hook-list">
                        {content.hooks.map((hook, hi) => (
                          <div key={hi} className="socx-hook">
                            <span className="socx-hook-tag chx-mono">[{hook.style}]</span>
                            <span>{hook.text}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {content.caption && (
                    <div className="socx-block">
                      <div className="socx-block-title">
                        <FileText size={14} /> Caption
                      </div>
                      <div className="socx-text-box">
                        {content.caption
                          .split('\n')
                          .filter((p) => p.trim())
                          .map((p, pi) => (
                            <p key={pi}>{p}</p>
                          ))}
                      </div>
                    </div>
                  )}

                  {content.scenario && (
                    <div className="socx-block">
                      <div className="socx-block-title">
                        <Clapperboard size={14} /> Senaryo
                      </div>
                      <div className="socx-text-box">
                        {scenarioLines(content.scenario).map((line, li) => (
                          <div key={li} className="socx-scene">
                            {line.time && (
                              <span className="socx-scene-time chx-mono">{line.time}</span>
                            )}
                            <span className="socx-scene-text">{line.text}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {content.cta_text && (
                    <div className="socx-block">
                      <div className="socx-block-title">
                        <Target size={14} /> CTA
                      </div>
                      <div className="socx-cta">{content.cta_text}</div>
                    </div>
                  )}

                  {content.hashtags && content.hashtags.length > 0 && (
                    <div className="socx-hashtags">
                      {content.hashtags.map((tag, ti) => (
                        <span key={ti} className="socx-hashtag">
                          {tag.startsWith('#') ? tag : `#${tag}`}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}

            <div className="socx-done-banner">
              <Check size={18} strokeWidth={2.2} />
              <span>
                İçerik üretimi tamamlandı! Sayfanın üstündeki İndir menüsünden indirebilirsiniz.
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Üretim banner'ı — senkron istek (kategori/fikir) veya içerik task'ı sürerken */}
      {busy && (
        <div className="socx-gen-banner">
          <span className="socx-gen-icon">
            <span className="chx-spin">
              <RefreshCw size={19} strokeWidth={2.4} />
            </span>
          </span>
          <div>
            <div className="socx-gen-title">
              {contentsGenerating ? 'İçerikler üretiliyor' : genLabel}
            </div>
            <div className="socx-gen-sub">
              {contentsGenerating ? (
                <>
                  İçerikler arka planda üretiliyor{' '}
                  {contentsPolling.progress > 0 && <b>(%{contentsPolling.progress})</b>} — bu ekranı
                  kapatabilirsiniz, durumu <b>Görevler</b>'den de izleyebilirsiniz.
                </>
              ) : (
                'Yapay zekâ marka bağlamınızı analiz ediyor. Bu bir dakikaya kadar sürebilir.'
              )}
            </div>
          </div>
        </div>
      )}

      {/* Alt aksiyonlar */}
      {!busy && (
        <div className={`socx-actions${store.step === 1 ? ' is-end' : ''}`}>
          {store.step > 1 && (
            <button type="button" className="socx-ghost-btn" onClick={goBack}>
              <ArrowLeft size={15} /> Geri
            </button>
          )}
          {store.step === 1 && (
            <button
              type="button"
              className="kwx-run-btn"
              onClick={generateCategories}
              disabled={!store.scoringRunId || disabled}
              title={disabled ? 'Bu çalışmada SOCIAL kanalı aktif değil' : undefined}
            >
              Kategorileri Üret <Sparkles size={15} strokeWidth={2.2} />
            </button>
          )}
          {store.step === 2 && (
            <button
              type="button"
              className="kwx-run-btn"
              onClick={generateIdeas}
              disabled={store.selectedCategoryIds.length === 0 || disabled}
              title={disabled ? 'Bu çalışmada SOCIAL kanalı aktif değil' : undefined}
            >
              Fikirleri Üret <Sparkles size={15} strokeWidth={2.2} />
            </button>
          )}
          {store.step === 3 && (
            <button
              type="button"
              className="kwx-run-btn"
              onClick={generateContents}
              disabled={store.selectedIdeaIds.length === 0 || disabled}
              title={disabled ? 'Bu çalışmada SOCIAL kanalı aktif değil' : undefined}
            >
              İçerikleri Üret <Sparkles size={15} strokeWidth={2.2} />
            </button>
          )}
          {store.step === 4 && (
            <button type="button" className="kwx-run-btn" onClick={() => store.reset()}>
              Yeni Üretim <Sparkles size={15} strokeWidth={2.2} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}
