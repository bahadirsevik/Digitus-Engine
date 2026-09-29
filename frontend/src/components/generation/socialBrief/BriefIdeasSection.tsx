import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Lightbulb, RefreshCw, RotateCcw } from 'lucide-react'
import {
  apiErrorCode,
  socialBriefApi,
  type SocialGeneratedIdeaResponse,
  type SocialIdeasGenerateResponse,
} from '../../../services/api'
import type { BriefSectionContext } from './briefContext'
import {
  clearIdemKey,
  getOrCreateIdemKey,
  shouldReleaseIdemKey,
  type SocialIdemScope,
} from './idempotency'
import {
  ATTEMPT_STATUS_LABEL,
  isActiveStatus,
  isStaleError,
  reasonText,
  socialErrorText,
} from './labels'
import { useAliveRef, usePolling } from './usePolling'
import { MAX_CONTENT_SELECTION } from './briefRules'

interface Props {
  ctx: BriefSectionContext
  selectedCategoryIds: number[]
  selectedIdeaIds: number[]
  onToggleIdea: (id: number) => void
  onIdeasChange: (ideas: SocialGeneratedIdeaResponse[]) => void
  onOpenContents: () => void
}

// K4: her seçilen kategori en az bir fikir almalı. Boş kategori, ilk üretimin uyarılarından
// okunur (yeni kayıtlarda category_unfilled, eski kayıtlarda ai_failed) ve şu anki fikir
// kümesinde hâlâ fikri yoksa boş sayılır.
const CATEGORY_GAP_REASONS = new Set(['category_unfilled', 'ai_failed'])

function mergeIdeas(...lists: (SocialGeneratedIdeaResponse[] | undefined)[]) {
  const byId = new Map<number, SocialGeneratedIdeaResponse>()
  for (const list of lists) for (const idea of list ?? []) byId.set(idea.id, idea)
  return [...byId.values()].sort((a, b) => a.id - b.id)
}

export default function BriefIdeasSection({
  ctx,
  selectedCategoryIds,
  selectedIdeaIds,
  onToggleIdea,
  onIdeasChange,
  onOpenContents,
}: Props) {
  const { brief, workspaceId, state } = ctx
  const alive = useAliveRef()
  const ideasAttempt = state?.ideas_attempt ?? null
  const latestRetry = state?.idea_retry_attempts?.[0] ?? null
  const contentIdeaIds = useMemo(
    () => new Set(state?.content_idea_ids ?? []),
    [state?.content_idea_ids]
  )

  const [ideasRes, setIdeasRes] = useState<SocialIdeasGenerateResponse | null>(null)
  const [retryRes, setRetryRes] = useState<SocialIdeasGenerateResponse | null>(null)
  const [ideasPerCategory, setIdeasPerCategory] = useState(3)
  const [busy, setBusy] = useState<'generate' | 'retry' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [readError, setReadError] = useState<string | null>(null)

  const ideasId = ideasAttempt?.id ?? null
  const retryId = latestRetry?.id ?? null

  // Sunucudaki attempt'leri oku (sayfa yenileme / başka oturum sonrası da çalışır)
  const loadIdeas = useCallback(async () => {
    if (ideasId == null) return
    try {
      const res = await socialBriefApi.getIdeasAttempt(brief.id, ideasId, workspaceId)
      if (!alive.current) return
      setIdeasRes(res.data)
      setReadError(null)
      if (!isActiveStatus(res.data.attempt_status) && isActiveStatus(ideasAttempt?.status)) {
        await ctx.refreshState()
      }
    } catch (err) {
      if (alive.current) setReadError(socialErrorText(err, 'Fikirler okunamadı.'))
    }
  }, [brief.id, ideasId, workspaceId, alive, ctx, ideasAttempt?.status])

  const loadRetry = useCallback(async () => {
    if (retryId == null) return
    try {
      const res = await socialBriefApi.getIdeasRetryAttempt(brief.id, retryId, workspaceId)
      if (!alive.current) return
      setRetryRes(res.data)
      setReadError(null)
      if (!isActiveStatus(res.data.attempt_status) && isActiveStatus(latestRetry?.status)) {
        await ctx.refreshState()
        await loadIdeas()
      }
    } catch (err) {
      if (alive.current) setReadError(socialErrorText(err, 'Tekrar deneme sonucu okunamadı.'))
    }
  }, [brief.id, retryId, workspaceId, alive, ctx, latestRetry?.status, loadIdeas])

  useEffect(() => {
    setIdeasRes(null)
    void loadIdeas()
    // yalnız attempt kimliği değişince yeniden yükle
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ideasId])

  useEffect(() => {
    setRetryRes(null)
    void loadRetry()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryId])

  const ideasActive = isActiveStatus(ideasRes?.attempt_status ?? ideasAttempt?.status)
  const retryActive = isActiveStatus(retryRes?.attempt_status ?? latestRetry?.status)
  usePolling(ideasActive && ideasId != null, loadIdeas, ctx.pollIntervalMs)
  usePolling(retryActive && retryId != null, loadRetry, ctx.pollIntervalMs)

  // Güncel fikir kümesi: ilk üretim + tekrar denemeler; aynı fikir iki kez gösterilmez
  const ideas = useMemo(
    () =>
      mergeIdeas(ideasRes?.ideas, retryRes?.ideas).filter(
        (i) => i.brief_id === brief.id && !i.is_stale
      ),
    [ideasRes, retryRes, brief.id]
  )
  useEffect(() => {
    onIdeasChange(ideas)
  }, [ideas, onIdeasChange])

  const categoryName = useMemo(() => {
    const m = new Map((state?.categories ?? []).map((c) => [c.id, c.category_name]))
    return (id: number) => m.get(id) ?? `Kategori #${id}`
  }, [state?.categories])

  // Güncel kapsama: hedef başına planlanan (ilk üretim planı) vs şu anki fikir sayısı
  const coverageRows = useMemo(
    () =>
      brief.targets.map((t) => {
        const planned = ideasRes?.coverage.find((c) => c.target_id === t.id)?.requested ?? null
        const current = ideas.filter((i) => i.brief_target_id === t.id).length
        return { target: t, planned, current }
      }),
    [brief.targets, ideasRes, ideas]
  )
  const missingTargets = coverageRows.filter((r) => r.current === 0)
  const underQuota = coverageRows.filter(
    (r) => r.current > 0 && r.planned != null && r.current < r.planned
  )
  const emptyCategoryIds = useMemo(() => {
    const withIdeas = new Set(ideas.map((i) => i.category_id))
    const ids = new Set<number>()
    for (const w of ideasRes?.warnings ?? []) {
      if (
        w.category_id != null &&
        CATEGORY_GAP_REASONS.has(w.reason_code) &&
        !withIdeas.has(w.category_id)
      ) {
        ids.add(w.category_id)
      }
    }
    return [...ids].sort((a, b) => a - b)
  }, [ideasRes, ideas])

  const ideasStatus = ideasRes?.attempt_status ?? ideasAttempt?.status ?? null
  const terminalIdeas = ideasStatus != null && !isActiveStatus(ideasStatus)
  // Kapsama/retry kararı yalnız sunucudaki fikir sonucu okunduktan sonra verilir;
  // aksi halde yükleme anında tüm hedefler "eksik" görünür (yenileme sonrası).
  const ideasLoaded = ideasAttempt == null || ideasRes?.attempt_id === ideasAttempt.id
  const canGenerate =
    !brief.is_stale &&
    (state?.categories.length ?? 0) > 0 &&
    (ideasAttempt == null || (ideasLoaded && ideasStatus === 'failed' && ideas.length === 0))
  const canRetry =
    !brief.is_stale &&
    ideasAttempt != null &&
    ideasLoaded &&
    terminalIdeas &&
    ideasStatus !== 'failed' &&
    (missingTargets.length > 0 || emptyCategoryIds.length > 0) &&
    !retryActive

  const handleGenerate = async () => {
    if (busy || ctx.disabled || !canGenerate) return
    if (selectedCategoryIds.length < 1 || selectedCategoryIds.length > 6) {
      setError('Fikir üretmek için 1–6 kategori seçin.')
      return
    }
    const categoryIds = [...selectedCategoryIds].sort((a, b) => a - b)
    const scope: SocialIdemScope = { workspaceId, briefId: brief.id, stage: 'ideas' }
    const key = getOrCreateIdemKey(scope, `cats=${categoryIds.join(',')}|n=${ideasPerCategory}`)
    setBusy('generate')
    setError(null)
    try {
      const res = await socialBriefApi.generateIdeas(
        brief.id,
        { idempotency_key: key, category_ids: categoryIds, ideas_per_category: ideasPerCategory },
        workspaceId
      )
      if (!alive.current) return
      clearIdemKey(scope)
      setIdeasRes(res.data)
      await ctx.refreshState()
    } catch (err) {
      if (!alive.current) return
      if (shouldReleaseIdemKey(err)) clearIdemKey(scope)
      setError(socialErrorText(err, 'Fikir üretimi başlatılamadı.'))
      const code = apiErrorCode(err)
      if (code === 'IDEAS_ALREADY_GENERATED' || code === 'ATTEMPT_CONFLICT' || isStaleError(err)) {
        await ctx.refreshState()
      }
    } finally {
      if (alive.current) setBusy(null)
    }
  }

  const handleRetry = async () => {
    if (busy || ctx.disabled || !canRetry || ideasAttempt == null) return
    const scope: SocialIdemScope = { workspaceId, briefId: brief.id, stage: 'ideas_retry' }
    const missingIds = missingTargets.map((r) => r.target.id).join(',')
    const emptyCats = emptyCategoryIds.join(',')
    const key = getOrCreateIdemKey(
      scope,
      `src=${ideasAttempt.id}|missing=${missingIds}|cats=${emptyCats}`
    )
    setBusy('retry')
    setError(null)
    try {
      const res = await socialBriefApi.retryIdeas(
        brief.id,
        { idempotency_key: key, source_attempt_id: ideasAttempt.id },
        workspaceId
      )
      if (!alive.current) return
      clearIdemKey(scope)
      setRetryRes(res.data)
      await ctx.refreshState()
    } catch (err) {
      if (!alive.current) return
      if (shouldReleaseIdemKey(err)) clearIdemKey(scope)
      setError(socialErrorText(err, 'Eksik hedefler için tekrar deneme başlatılamadı.'))
      if (apiErrorCode(err) === 'ATTEMPT_CONFLICT' || isStaleError(err)) {
        await ctx.refreshState()
      }
    } finally {
      if (alive.current) setBusy(null)
    }
  }

  if ((state?.categories.length ?? 0) === 0 && ideasAttempt == null) return null

  const targetById = new Map(brief.targets.map((t) => [t.id, t]))
  const selectionFull = selectedIdeaIds.length >= MAX_CONTENT_SELECTION

  return (
    <section className="sb-stage" aria-labelledby={`sb-ideas-title-${brief.id}`}>
      <div className="sb-stage-head">
        <div>
          <span className="sb-stage-step">Adım 2</span>
          <h3 id={`sb-ideas-title-${brief.id}`} className="sb-stage-title">
            Fikirler
          </h3>
          <p className="sb-stage-desc">
            Fikirler yalnız brief'teki platform ve formatlar için üretilir; her hedef en az bir
            fikir almalıdır.
          </p>
        </div>
        {ideasStatus && (
          <span
            className={`sb-badge ${ideasStatus === 'completed' ? 'sb-badge-active' : ideasStatus === 'failed' ? 'sb-badge-stale' : 'sb-badge-neutral'}`}
          >
            İlk üretim: {ATTEMPT_STATUS_LABEL[ideasStatus] ?? ideasStatus}
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

      {(ideasActive || retryActive) && (
        <div className="socx-gen-banner" role="status" aria-live="polite">
          <span className="socx-gen-icon">
            <RefreshCw size={19} className="chx-spin" />
          </span>
          <div>
            <div className="socx-gen-title">
              {ideasActive ? 'Fikirler üretiliyor' : 'Eksik hedefler yeniden deneniyor'}
            </div>
            <div className="socx-gen-sub">
              {ideasActive
                ? `${ideasStatus === 'pending' ? 'Sırada bekliyor' : 'Yapay zekâ çalışıyor'}. Bu sayfadan ayrılabilirsiniz; sonuç kaydedilir.`
                : 'Sonuç hazır olunca otomatik gösterilecek.'}
            </div>
          </div>
        </div>
      )}

      {ideasAttempt && !ideasLoaded && !readError && (
        <div className="socx-gen-banner" role="status">
          <span className="socx-gen-icon">
            <RefreshCw size={19} className="chx-spin" />
          </span>
          <div>
            <div className="socx-gen-title">Fikirler yükleniyor...</div>
            <div className="socx-gen-sub">Kaydedilmiş sonuçlar sunucudan getiriliyor.</div>
          </div>
        </div>
      )}

      {ideasLoaded && ideasStatus === 'failed' && ideas.length === 0 && (
        <p className="sb-muted-note">
          <AlertTriangle size={13} /> Fikir üretimi başarısız oldu
          {ideasRes?.reason_code || ideasAttempt?.reason_code
            ? `: ${reasonText(ideasRes?.reason_code ?? ideasAttempt?.reason_code)}`
            : '.'}{' '}
          Sahte yedek fikir oluşturulmaz; aşağıdan yeniden başlatabilirsiniz.
        </p>
      )}

      {canGenerate && (
        <div className="sb-cat-gen-bar">
          <div className="sb-cat-gen-intro">
            <Lightbulb size={18} aria-hidden="true" />
            <div>
              <div className="sb-cat-gen-title">
                {selectedCategoryIds.length} kategori için fikir üret
              </div>
              <div className="sb-cat-gen-sub">
                Toplam en fazla 30 fikir. Sonuç arka planda üretilir.
              </div>
            </div>
          </div>
          <div className="sb-cat-gen-controls">
            <label htmlFor={`ideas-per-cat-${brief.id}`}>Kategori başına</label>
            <select
              id={`ideas-per-cat-${brief.id}`}
              className="sb-select sb-select-narrow"
              value={ideasPerCategory}
              onChange={(e) => setIdeasPerCategory(Number(e.target.value))}
              disabled={busy != null}
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="sb-btn sb-btn-primary"
              onClick={handleGenerate}
              disabled={busy != null || ctx.disabled || selectedCategoryIds.length === 0}
            >
              {busy === 'generate' ? (
                <RefreshCw size={14} className="chx-spin" />
              ) : (
                <Lightbulb size={14} />
              )}
              {ideasStatus === 'failed' ? 'Fikirleri Yeniden Üret' : 'Fikirleri Üret'}
            </button>
          </div>
        </div>
      )}

      {ideasAttempt && ideasLoaded && terminalIdeas && (
        <div className="sb-coverage" aria-label="Hedef kapsaması">
          <table className="sb-coverage-table">
            <caption>Güncel hedef kapsaması</caption>
            <thead>
              <tr>
                <th scope="col">Hedef</th>
                <th scope="col">Planlanan</th>
                <th scope="col">Mevcut fikir</th>
                <th scope="col">Durum</th>
              </tr>
            </thead>
            <tbody>
              {coverageRows.map(({ target, planned, current }) => (
                <tr key={target.id}>
                  <td>{ctx.targetLabel(target)}</td>
                  <td>{planned ?? '—'}</td>
                  <td>{current}</td>
                  <td>
                    {current === 0 ? (
                      <span className="sb-badge sb-badge-stale">Eksik hedef</span>
                    ) : planned != null && current < planned ? (
                      <span className="sb-badge sb-badge-neutral">Kota altında</span>
                    ) : (
                      <span className="sb-badge sb-badge-active">Tamam</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {missingTargets.length > 0 && (
            <div className="channels-alert sb-alert-warning sb-stage-alert" role="status">
              {missingTargets.length} hedef için henüz fikir yok:{' '}
              {missingTargets.map((r) => ctx.targetLabel(r.target)).join(', ')}.
              {canRetry && ' Yalnız bu hedefler için tekrar deneyebilirsiniz.'}
            </div>
          )}
          {emptyCategoryIds.length > 0 && (
            <div className="channels-alert sb-alert-warning sb-stage-alert" role="status">
              {emptyCategoryIds.length} kategori için henüz fikir yok:{' '}
              {emptyCategoryIds.map((id) => categoryName(id)).join(', ')}.
              {canRetry && ' Her kategorinin en az bir fikir alması için tekrar deneyebilirsiniz.'}
            </div>
          )}
          {missingTargets.length === 0 && underQuota.length > 0 && (
            <p className="sb-muted-note">
              Bazı hedefler planlanandan az fikir aldı, ancak her hedef en az bir fikre sahip.
            </p>
          )}
          {canRetry && (
            <button
              type="button"
              className="sb-btn sb-btn-secondary"
              onClick={handleRetry}
              disabled={busy != null || ctx.disabled}
            >
              {busy === 'retry' ? (
                <RefreshCw size={14} className="chx-spin" />
              ) : (
                <RotateCcw size={14} />
              )}
              {missingTargets.length > 0
                ? 'Eksik hedefleri tekrar dene'
                : 'Boş kategorileri tekrar dene'}
            </button>
          )}

          {(ideasRes?.warnings.length ?? 0) > 0 && (
            <details className="sb-history-details">
              <summary>İlk üretimde oluşan uyarılar ({ideasRes?.warnings.length})</summary>
              <ul className="sb-warning-list">
                {ideasRes?.warnings.map((w, i) => {
                  const t = targetById.get(w.target_id)
                  return (
                    <li key={i}>
                      <AlertTriangle size={12} />
                      {t ? ctx.targetLabel(t) : `Hedef #${w.target_id}`}
                      {w.category_id ? ` · ${categoryName(w.category_id)}` : ''}:{' '}
                      {reasonText(w.reason_code)}
                    </li>
                  )
                })}
              </ul>
            </details>
          )}
          {(state?.idea_retry_attempts.length ?? 0) > 0 && (
            <details className="sb-history-details">
              <summary>Tekrar denemeleri ({state?.idea_retry_attempts.length})</summary>
              <ul className="sb-attempt-log">
                {state?.idea_retry_attempts.map((a) => (
                  <li key={a.id}>
                    #{a.id} · {ATTEMPT_STATUS_LABEL[a.status] ?? a.status}
                    {a.reason_code ? ` · ${reasonText(a.reason_code)}` : ''}
                  </li>
                ))}
              </ul>
              {retryRes && retryRes.warnings.length > 0 && (
                <ul className="sb-warning-list">
                  {retryRes.warnings.map((w, i) => {
                    const t = targetById.get(w.target_id)
                    return (
                      <li key={i}>
                        <AlertTriangle size={12} />
                        Son deneme · {t ? ctx.targetLabel(t) : `Hedef #${w.target_id}`}:{' '}
                        {reasonText(w.reason_code)}
                      </li>
                    )
                  })}
                </ul>
              )}
            </details>
          )}
        </div>
      )}

      {ideas.length > 0 && (
        <>
          <p className="sb-muted-note">
            İçerik üretmek istediğiniz fikirleri seçin ({selectedIdeaIds.length}/
            {MAX_CONTENT_SELECTION}).
          </p>
          {brief.targets.map((target) => {
            const group = ideas.filter((i) => i.brief_target_id === target.id)
            if (group.length === 0) return null
            return (
              <div key={target.id} className="sb-idea-group">
                <h4 className="sb-idea-group-title">
                  {ctx.targetLabel(target)} <span className="sb-muted">({group.length})</span>
                </h4>
                <div className="sb-idea-grid">
                  {group.map((idea) => {
                    const hasContent = contentIdeaIds.has(idea.id)
                    const checked = selectedIdeaIds.includes(idea.id)
                    const inputId = `sb-idea-${idea.id}`
                    const disabled =
                      hasContent || brief.is_stale || ctx.disabled || (!checked && selectionFull)
                    return (
                      <div
                        key={idea.id}
                        className={`sb-idea-card ${checked ? 'is-selected' : ''} ${!checked ? 'is-unselected' : ''} ${hasContent ? 'has-content' : ''}`}
                      >
                        <label className={`sb-idea-select-area ${disabled ? 'is-disabled' : ''}`}>
                          <span className="sb-idea-card-head">
                            <input
                              id={inputId}
                              type="checkbox"
                              checked={checked}
                              disabled={disabled}
                              onChange={() => onToggleIdea(idea.id)}
                              aria-label={idea.idea_title}
                            />
                            <span className="sb-idea-title">{idea.idea_title}</span>
                          </span>
                          <span className="sb-idea-desc">{idea.idea_description}</span>
                          <span className="sb-chip-container">
                            <span className="sb-chip sb-chip-sm">
                              {categoryName(idea.category_id)}
                            </span>
                            <span className="sb-chip sb-chip-sm">
                              {ctx.keywordLabel(idea.keyword_id)}
                            </span>
                            <span className="sb-chip sb-chip-sm">
                              Trend uyumu %{Math.round(idea.trend_alignment * 100)}
                            </span>
                          </span>
                        </label>
                        {hasContent && (
                          <button
                            type="button"
                            className="sb-badge sb-badge-active sb-content-jump"
                            onClick={onOpenContents}
                          >
                            <CheckCircle2 size={11} /> İçerik var · Gör
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </>
      )}
    </section>
  )
}
