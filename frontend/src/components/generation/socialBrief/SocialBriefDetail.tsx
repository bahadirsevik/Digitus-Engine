import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  Lock,
  Plus,
  RefreshCw,
  Tag,
} from 'lucide-react'
import {
  socialBriefApi,
  type SocialBriefResponse,
  type SocialBriefStateResponse,
  type SocialBriefTargetResponse,
  type SocialCategoriesGenerateResponse,
  type SocialGeneratedIdeaResponse,
} from '../../../services/api'
import type { BriefSectionContext } from './briefContext'
import BriefCategoriesSection from './BriefCategoriesSection'
import BriefIdeasSection from './BriefIdeasSection'
import { MAX_CONTENT_SELECTION } from './briefRules'
import BriefContentsSection from './BriefContentsSection'
import { formatSeconds, socialErrorText } from './labels'
import { useAliveRef } from './usePolling'

interface Props {
  brief: SocialBriefResponse
  workspaceId: number
  disabled: boolean
  pollIntervalMs: number
  platformLabel: (platform: string) => string
  formatLabel: (platform: string, format: string) => string
  onBack: () => void
  onNewBrief: () => void
  /** Brief kilitlendi/eskidi: üst listeyi tazele. */
  onBriefChanged: () => void
}

/**
 * Tek brief ekranı. Workspace/run/brief kapsamı üst bileşende `key` ile
 * bağlanır: kapsam değişince bu bileşen tamamen sökülür, geç gelen yanıtlar
 * `alive` korumasıyla atılır ve eski kapsamın verisi ekrana yazılmaz.
 */
export default function SocialBriefDetail({
  brief,
  workspaceId,
  disabled,
  pollIntervalMs,
  platformLabel,
  formatLabel,
  onBack,
  onNewBrief,
  onBriefChanged,
}: Props) {
  const alive = useAliveRef()
  const [state, setState] = useState<SocialBriefStateResponse | null>(null)
  const [stateError, setStateError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedCategoryIds, setSelectedCategoryIds] = useState<number[]>([])
  const [selectedIdeaIds, setSelectedIdeaIds] = useState<number[]>([])
  const [viewContentIdeaId, setViewContentIdeaId] = useState<number | null>(null)
  const [ideas, setIdeas] = useState<SocialGeneratedIdeaResponse[]>([])
  const [activeStep, setActiveStep] = useState<1 | 2 | 3 | null>(null)

  const refreshState = useCallback(async () => {
    try {
      const res = await socialBriefApi.getBriefState(brief.id, workspaceId)
      if (!alive.current) return
      setState(res.data)
      // Yenilemeden sonra en son ulaşılan aşamaya dön; kullanıcı geri gittiyse
      // sonraki yoklamalar onu zorla ileri taşımasın.
      setActiveStep(
        (current) =>
          current ??
          (res.data.content_attempts.length > 0 || res.data.content_idea_ids.length > 0
            ? 3
            : res.data.ideas_attempt || res.data.idea_retry_attempts.length > 0
              ? 2
              : 1)
      )
      setStateError(null)
      if (res.data.is_stale && !brief.is_stale) onBriefChanged()
    } catch (err) {
      if (alive.current) setStateError(socialErrorText(err, 'Brief durumu okunamadı.'))
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [brief.id, brief.is_stale, workspaceId, alive, onBriefChanged])

  useEffect(() => {
    void refreshState()
    // yalnız brief/workspace değişince (bileşen zaten key ile yeniden kurulur)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brief.id, workspaceId])

  // Kategoriler geldiğinde varsayılan olarak hepsi seçili
  const categoryIdsKey = (state?.categories ?? []).map((c) => c.id).join(',')
  useEffect(() => {
    setSelectedCategoryIds((state?.categories ?? []).map((c) => c.id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryIdsKey])

  // İçeriği oluşan fikir seçimden düşer (aynı fikre ikinci içerik istenmez)
  useEffect(() => {
    const has = new Set(state?.content_idea_ids ?? [])
    setSelectedIdeaIds((prev) => prev.filter((id) => !has.has(id)))
  }, [state?.content_idea_ids])

  const effectiveBrief = useMemo<SocialBriefResponse>(
    () => ({
      ...brief,
      is_stale: brief.is_stale || Boolean(state?.is_stale),
      locked_at: brief.locked_at ?? state?.locked_at ?? null,
    }),
    [brief, state?.is_stale, state?.locked_at]
  )

  const targetLabel = useCallback(
    (t: SocialBriefTargetResponse) => {
      const base = `${platformLabel(t.platform)} · ${formatLabel(t.platform, t.content_format)}`
      if (t.duration_min_sec != null && t.duration_max_sec != null) {
        return `${base} (${formatSeconds(t.duration_min_sec)}–${formatSeconds(t.duration_max_sec)})`
      }
      return base
    },
    [platformLabel, formatLabel]
  )
  const keywordLabel = useCallback(
    (id: number) => brief.keywords.find((k) => k.keyword_id === id)?.keyword_snapshot ?? `#${id}`,
    [brief.keywords]
  )

  const ctx: BriefSectionContext = {
    brief: effectiveBrief,
    workspaceId,
    state,
    disabled,
    pollIntervalMs,
    refreshState,
    platformLabel,
    formatLabel,
    targetLabel,
    keywordLabel,
  }

  const handleCategoriesResult = (res: SocialCategoriesGenerateResponse) => {
    setState((prev) =>
      prev
        ? {
            ...prev,
            categories: res.categories,
            category_attempt: prev.category_attempt
              ? { ...prev.category_attempt, id: res.attempt_id, status: 'completed' }
              : prev.category_attempt,
          }
        : prev
    )
  }

  const ideasStarted =
    state?.ideas_attempt != null && !(state.ideas_attempt.status === 'failed' && ideas.length === 0)

  const toggleCategory = (id: number) =>
    setSelectedCategoryIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(0, 6)
    )
  const toggleIdea = (id: number) => {
    setViewContentIdeaId(null)
    setSelectedIdeaIds((prev) =>
      prev.includes(id)
        ? prev.filter((x) => x !== id)
        : prev.length >= MAX_CONTENT_SELECTION
          ? prev
          : [...prev, id]
    )
  }
  const handleIdeasChange = useCallback((list: SocialGeneratedIdeaResponse[]) => setIdeas(list), [])
  const step = activeStep ?? 1
  const canOpenIdeas = (state?.categories.length ?? 0) > 0
  const canOpenContents = ideas.length > 0 || (state?.content_attempts.length ?? 0) > 0
  const stages = [
    { number: 1, title: 'Kategoriler', subtitle: 'Temaları seçin' },
    { number: 2, title: 'Fikirler', subtitle: 'Fikirleri seçin' },
    { number: 3, title: 'İçerikler', subtitle: 'Sonuçları görün' },
  ] as const

  return (
    <div className="sb-workspace animate-fade-in">
      <div className="sb-card">
        <div className="sb-card-head">
          <div className="sb-card-title-wrap">
            <button type="button" className="sb-btn sb-btn-ghost" onClick={onBack}>
              <ChevronLeft size={16} />
              Tüm Briefler
            </button>
            <div>
              <div className="sb-title-row">
                <h2 className="sb-card-title">Brief #{brief.id}</h2>
                {effectiveBrief.is_stale ? (
                  <span className="sb-badge sb-badge-stale">
                    <AlertTriangle size={12} />
                    Güncel değil
                  </span>
                ) : effectiveBrief.locked_at ? (
                  <span className="sb-badge sb-badge-locked">
                    <Lock size={12} />
                    Kilitli
                  </span>
                ) : (
                  <span className="sb-badge sb-badge-active">
                    <CheckCircle2 size={12} />
                    Hazır
                  </span>
                )}
              </div>
              <p className="sb-card-subtitle">
                {brief.brand_name_snapshot && <b>{brief.brand_name_snapshot} · </b>}
                {brief.created_at
                  ? new Date(brief.created_at).toLocaleString('tr-TR')
                  : 'Yeni oluşturuldu'}
              </p>
            </div>
          </div>
          <button type="button" className="sb-btn sb-btn-secondary" onClick={onNewBrief}>
            <Plus size={14} />
            Yeni Brief
          </button>
        </div>

        {effectiveBrief.is_stale && (
          <div className="channels-alert channels-alert-error sb-stage-alert" role="alert">
            Kanal havuzu bu brief oluşturulduktan sonra güncellendi. Bu brief üzerinde yeni
            kategori, fikir veya içerik üretilemez; mevcut sonuçlar yalnız incelenebilir. Güncel
            havuzla yeni bir brief oluşturun.
          </div>
        )}

        <div className="sb-brief-summary">
          <div>
            <span className="sb-brief-item-label">Kelimeler ({brief.keywords.length})</span>
            <div className="sb-chip-container">
              {brief.keywords.map((kw) => (
                <span key={kw.id} className="sb-chip">
                  <Tag size={11} />
                  {kw.keyword_snapshot}
                </span>
              ))}
            </div>
          </div>
          <div>
            <span className="sb-brief-item-label">Hedefler ({brief.targets.length})</span>
            <div className="sb-chip-container">
              {brief.targets.map((t) => (
                <span key={t.id} className="sb-chip">
                  {targetLabel(t)}
                </span>
              ))}
            </div>
          </div>
          {brief.brand_context_snapshot && (
            <div className="sb-brief-summary-wide">
              <span className="sb-brief-item-label">Marka notları</span>
              <p className="sb-prewrap sb-muted">{brief.brand_context_snapshot}</p>
            </div>
          )}
        </div>

        {stateError && (
          <div className="channels-alert channels-alert-error sb-stage-alert" role="alert">
            {stateError}{' '}
            <button type="button" className="sb-btn sb-btn-ghost sb-btn-xs" onClick={refreshState}>
              Tekrar dene
            </button>
          </div>
        )}

        {loading && !state ? (
          <div className="sb-progress-note" role="status">
            <RefreshCw size={14} className="chx-spin" /> Brief durumu yükleniyor...
          </div>
        ) : state ? (
          <>
            <nav className="sb-flow-steps" aria-label="Sosyal brief adımları">
              {stages.map((stage, index) => {
                const available =
                  stage.number === 1 || (stage.number === 2 ? canOpenIdeas : canOpenContents)
                const completed = stage.number < step
                return (
                  <div className="sb-flow-step-wrap" key={stage.number}>
                    <button
                      type="button"
                      className={`sb-flow-step ${stage.number === step ? 'is-active' : ''} ${completed ? 'is-done' : ''}`}
                      aria-current={stage.number === step ? 'step' : undefined}
                      disabled={!available}
                      onClick={() => {
                        setViewContentIdeaId(null)
                        setActiveStep(stage.number)
                      }}
                    >
                      <span className="sb-flow-step-circle">
                        {completed ? <Check size={16} /> : stage.number}
                      </span>
                      <span className="sb-flow-step-text">
                        <strong>{stage.title}</strong>
                        <small>{stage.subtitle}</small>
                      </span>
                    </button>
                    {index < stages.length - 1 && (
                      <span
                        className={`sb-flow-step-line ${completed ? 'is-done' : ''}`}
                        aria-hidden="true"
                      />
                    )}
                  </div>
                )
              })}
            </nav>
            <div className="sb-flow-body">
              <div hidden={step !== 1}>
                <BriefCategoriesSection
                  ctx={ctx}
                  selectable={!ideasStarted && !effectiveBrief.is_stale}
                  selectedCategoryIds={selectedCategoryIds}
                  onToggleCategory={toggleCategory}
                  onCategoriesResult={handleCategoriesResult}
                  onBriefLocked={onBriefChanged}
                />
              </div>
              <div hidden={step !== 2}>
                <BriefIdeasSection
                  ctx={ctx}
                  selectedCategoryIds={selectedCategoryIds}
                  selectedIdeaIds={selectedIdeaIds}
                  onToggleIdea={toggleIdea}
                  onIdeasChange={handleIdeasChange}
                  onOpenContents={(ideaId) => {
                    setViewContentIdeaId(ideaId)
                    setActiveStep(3)
                  }}
                />
              </div>
              <div hidden={step !== 3}>
                <BriefContentsSection
                  ctx={ctx}
                  ideas={ideas}
                  selectedIdeaIds={selectedIdeaIds}
                  viewIdeaId={viewContentIdeaId}
                  onClearSelection={() => {
                    setSelectedIdeaIds([])
                    setViewContentIdeaId(null)
                  }}
                />
              </div>
            </div>
            <div className="sb-flow-actions">
              {step > 1 && (
                <button
                  type="button"
                  className="sb-btn sb-btn-secondary"
                  onClick={() => setActiveStep(step === 3 ? 2 : 1)}
                >
                  <ArrowLeft size={15} /> Geri
                </button>
              )}
              {step < 3 && (
                <button
                  type="button"
                  className="sb-btn sb-btn-primary"
                  disabled={step === 1 ? !canOpenIdeas : !canOpenContents}
                  onClick={() => {
                    setViewContentIdeaId(null)
                    setActiveStep(step === 1 ? 2 : 3)
                  }}
                >
                  {step === 1 ? 'Fikirlere Geç' : 'İçeriklere Geç'} <ArrowRight size={15} />
                </button>
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
