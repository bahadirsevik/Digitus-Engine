import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, FileText, RefreshCw, RotateCcw, Send } from 'lucide-react'
import {
  apiErrorCode,
  socialBriefApi,
  type SocialBriefContentsAttemptResponse,
  type SocialContentHistoryItemResponse,
  type SocialGeneratedIdeaResponse,
} from '../../../services/api'
import type { BriefSectionContext } from './briefContext'
import {
  clearIdemKey,
  getOrCreateIdemKey,
  shouldReleaseIdemKey,
  textFingerprint,
  type SocialIdemScope,
} from './idempotency'
import {
  ATTEMPT_STATUS_LABEL,
  isActiveStatus,
  isStaleError,
  reasonText,
  socialErrorText,
} from './labels'
import SocialContentCard from './SocialContentCard'
import { MAX_CONTENT_SELECTION } from './briefRules'
import { useAliveRef, usePolling } from './usePolling'

const USP_MAX = 5000

interface Props {
  ctx: BriefSectionContext
  ideas: SocialGeneratedIdeaResponse[]
  selectedIdeaIds: number[]
  onClearSelection: () => void
}

export default function BriefContentsSection({
  ctx,
  ideas,
  selectedIdeaIds,
  onClearSelection,
}: Props) {
  const { brief, workspaceId, state } = ctx
  const alive = useAliveRef()
  const latest = state?.content_attempts?.[0] ?? null
  const latestId = latest?.id ?? null

  const [attemptRes, setAttemptRes] = useState<SocialBriefContentsAttemptResponse | null>(null)
  const [contents, setContents] = useState<SocialContentHistoryItemResponse[]>([])
  const [contentsLoaded, setContentsLoaded] = useState(false)
  const [usp, setUsp] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [readError, setReadError] = useState<string | null>(null)

  const ideaById = useMemo(() => new Map(ideas.map((i) => [i.id, i])), [ideas])
  const contentIdeaIds = useMemo(
    () => new Set(state?.content_idea_ids ?? []),
    [state?.content_idea_ids]
  )

  // Brief'e ait kayıtlı içerikler: sunucudan (yenilemeye/başka oturuma dayanıklı)
  const loadContents = useCallback(async () => {
    try {
      const res = await socialBriefApi.getContentHistory(workspaceId, {
        brief_id: brief.id,
        limit: 50,
      })
      if (!alive.current) return
      setContents(res.data.items)
      setContentsLoaded(true)
    } catch (err) {
      if (alive.current) setReadError(socialErrorText(err, 'Kayıtlı içerikler okunamadı.'))
    }
  }, [workspaceId, brief.id, alive])

  const loadAttempt = useCallback(async () => {
    if (latestId == null) return
    try {
      const res = await socialBriefApi.getContentsAttempt(brief.id, latestId, workspaceId)
      if (!alive.current) return
      setAttemptRes(res.data)
      setReadError(null)
      if (!isActiveStatus(res.data.attempt_status) && isActiveStatus(latest?.status)) {
        await Promise.all([ctx.refreshState(), loadContents()])
      }
    } catch (err) {
      if (alive.current) setReadError(socialErrorText(err, 'İçerik üretim durumu okunamadı.'))
    }
  }, [brief.id, latestId, workspaceId, alive, latest?.status, ctx, loadContents])

  useEffect(() => {
    void loadContents()
  }, [loadContents])

  useEffect(() => {
    setAttemptRes(null)
    void loadAttempt()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestId])

  const status = attemptRes?.attempt_status ?? latest?.status ?? null
  const active = isActiveStatus(status)
  usePolling(active && latestId != null, loadAttempt, ctx.pollIntervalMs)

  const trimmedUsp = usp.trim()
  const retryableIds = useMemo(() => {
    if (!attemptRes || (status !== 'partial' && status !== 'failed')) return []
    return attemptRes.unresolved_idea_ids.filter(
      (id) => !contentIdeaIds.has(id) && ideaById.has(id)
    )
  }, [attemptRes, status, contentIdeaIds, ideaById])

  const dispatch = async (ideaIds: number[]) => {
    if (busy || ctx.disabled || brief.is_stale || active) return
    const ids = [...new Set(ideaIds)].sort((a, b) => a - b)
    if (ids.length < 1 || ids.length > MAX_CONTENT_SELECTION) {
      setError(`İçerik üretmek için 1–${MAX_CONTENT_SELECTION} fikir seçin.`)
      return
    }
    if (trimmedUsp.length > USP_MAX) {
      setError(`Marka USP metni en fazla ${USP_MAX} karakter olabilir.`)
      return
    }
    const scope: SocialIdemScope = { workspaceId, briefId: brief.id, stage: 'contents' }
    const fp = `ids=${ids.join(',')}|usp=${trimmedUsp ? textFingerprint(trimmedUsp) : '-'}`
    const key = getOrCreateIdemKey(scope, fp)
    setBusy(true)
    setError(null)
    try {
      const res = await socialBriefApi.generateContents(
        brief.id,
        {
          idempotency_key: key,
          idea_ids: ids,
          ...(trimmedUsp ? { trusted_brand_usp: trimmedUsp } : {}),
        },
        workspaceId
      )
      if (!alive.current) return
      clearIdemKey(scope)
      setAttemptRes(res.data)
      onClearSelection()
      await ctx.refreshState()
      if (!isActiveStatus(res.data.attempt_status)) await loadContents()
    } catch (err) {
      if (!alive.current) return
      if (shouldReleaseIdemKey(err)) clearIdemKey(scope)
      setError(socialErrorText(err, 'İçerik üretimi başlatılamadı.'))
      if (apiErrorCode(err) === 'ATTEMPT_CONFLICT' || isStaleError(err)) {
        await ctx.refreshState()
      }
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  const hasIdeas = ideas.length > 0
  if (!hasIdeas && contents.length === 0 && latest == null) return null

  const targetById = new Map(brief.targets.map((t) => [t.id, t]))
  const ideaTitle = (id: number) => ideaById.get(id)?.idea_title ?? `Fikir #${id}`

  return (
    <section className="sb-stage" aria-labelledby={`sb-contents-title-${brief.id}`}>
      <div className="sb-stage-head">
        <div>
          <span className="sb-stage-step">Adım 3</span>
          <h3 id={`sb-contents-title-${brief.id}`} className="sb-stage-title">
            İçerikler
          </h3>
          <p className="sb-stage-desc">
            Seçtiğiniz fikirler için formatına uygun içerik üretilir. Doğrulanamayan iddia içeren
            içerik kaydedilmez.
          </p>
        </div>
        {status && (
          <span
            className={`sb-badge ${status === 'completed' ? 'sb-badge-active' : status === 'failed' ? 'sb-badge-stale' : 'sb-badge-neutral'}`}
          >
            Son üretim: {ATTEMPT_STATUS_LABEL[status] ?? status}
          </span>
        )}
      </div>

      {error && (
        <div className="channels-alert channels-alert-error sb-stage-alert" role="alert">
          {error}
        </div>
      )}
      {readError && (
        <div className="channels-alert channels-alert-error sb-stage-alert" role="alert">
          {readError}
        </div>
      )}

      {hasIdeas && !brief.is_stale && (
        <div className="sb-content-dispatch">
          <label htmlFor={`sb-usp-${brief.id}`} className="sb-field-label">
            Güvenilir marka USP'si (opsiyonel)
          </label>
          <p className="sb-form-desc">
            Yalnız doğruladığınız bilgileri yazın. Buradaki ifadeler içerikte iddia olarak
            kullanılabilir; genel bir varsayılan eklenmez.
          </p>
          <textarea
            id={`sb-usp-${brief.id}`}
            className="sb-textarea"
            rows={2}
            maxLength={USP_MAX}
            value={usp}
            onChange={(e) => setUsp(e.target.value)}
            placeholder="Örn: 2012'den beri hizmet veriyoruz; ISO 9001 sertifikalıyız."
          />
          <div className="sb-content-dispatch-row">
            <span className="sb-muted">
              {selectedIdeaIds.length} fikir seçili · {trimmedUsp.length}/{USP_MAX} karakter
            </span>
            <button
              type="button"
              className="sb-btn sb-btn-primary"
              onClick={() => dispatch(selectedIdeaIds)}
              disabled={busy || active || ctx.disabled || selectedIdeaIds.length === 0}
            >
              {busy ? <RefreshCw size={14} className="chx-spin" /> : <Send size={14} />}
              Seçilen Fikirlerden İçerik Üret
            </button>
          </div>
          {active && (
            <p className="sb-muted-note">
              Bu brief'te bir içerik üretimi sürüyor; bitince yeni üretim başlatabilirsiniz.
            </p>
          )}
        </div>
      )}

      {active && (
        <div className="sb-progress-note" role="status" aria-live="polite">
          <RefreshCw size={14} className="chx-spin" />
          {status === 'pending' ? 'İçerik üretimi sırada bekliyor' : 'İçerikler üretiliyor'}
          {attemptRes ? ` (${attemptRes.requested_idea_ids.length} fikir)` : ''}. Bu sayfadan
          ayrılabilirsiniz; sonuç kaydedilir.
        </div>
      )}

      {attemptRes && !active && (
        <div className="sb-attempt-summary" role="status">
          <span>
            İstenen <b>{attemptRes.requested_idea_ids.length}</b> · Üretilen{' '}
            <b>{attemptRes.successful_idea_ids.length}</b> · Üretilemeyen{' '}
            <b>{attemptRes.unresolved_idea_ids.length}</b>
          </span>
          {attemptRes.reason_code && (
            <span className="sb-muted">{reasonText(attemptRes.reason_code)}</span>
          )}
          {attemptRes.warnings.length > 0 && (
            <ul className="sb-warning-list">
              {attemptRes.warnings.map((w) => (
                <li key={w.idea_id}>
                  <AlertTriangle size={12} />
                  <span>
                    <b>{ideaTitle(w.idea_id)}</b>: {reasonText(w.reason_code)}
                    {w.claims.length > 0 && (
                      <span className="sb-claims">
                        {' '}
                        Reddedilen ifadeler: {w.claims.map((c) => `“${c}”`).join(', ')}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {retryableIds.length > 0 && !brief.is_stale && (
            <button
              type="button"
              className="sb-btn sb-btn-secondary"
              onClick={() => dispatch(retryableIds)}
              disabled={busy || ctx.disabled}
            >
              <RotateCcw size={14} />
              Üretilemeyen {retryableIds.length} fikir için yeniden dene
            </button>
          )}
        </div>
      )}

      {contentsLoaded && contents.length === 0 && !active && hasIdeas && (
        <div className="sb-empty-inline">
          <FileText size={16} /> Bu brief için henüz içerik üretilmedi.
        </div>
      )}

      {contents.length > 0 && (
        <div className="sb-content-list">
          {contents.map((c) => {
            const idea = ideaById.get(c.idea_id)
            const target = idea ? targetById.get(idea.brief_target_id) : undefined
            return (
              <div key={c.id} id={`sb-content-idea-${c.idea_id}`}>
                <SocialContentCard
                  view={{
                    id: c.id,
                    ideaTitle: c.idea_title,
                    platformLabel: c.platform ? ctx.platformLabel(c.platform) : '—',
                    formatLabel:
                      c.platform && c.content_format
                        ? ctx.formatLabel(c.platform, c.content_format)
                        : '—',
                    keyword: c.keyword,
                    categoryName: c.category_name,
                    hooks: c.hooks,
                    caption: c.caption,
                    ctaText: c.cta_text,
                    hashtags: c.hashtags,
                    visualSuggestion: c.visual_suggestion,
                    videoConcept: c.video_concept,
                    platformNotes: c.platform_notes,
                    postingSuggestion: c.industry_posting_suggestion,
                    scenario: c.scenario,
                    formatPayload: c.format_payload,
                    durationStatus: c.duration_status,
                    actualDurationSec: c.actual_duration_sec,
                    durationMinSec: c.duration_min_sec ?? target?.duration_min_sec,
                    durationMaxSec: c.duration_max_sec ?? target?.duration_max_sec,
                    validationWarnings: c.validation_warnings,
                    isStale: c.is_stale,
                  }}
                />
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
