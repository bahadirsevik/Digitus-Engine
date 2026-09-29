import { useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw, Sparkles } from 'lucide-react'
import {
  apiErrorCode,
  socialBriefApi,
  type SocialCategoriesGenerateResponse,
} from '../../../services/api'
import type { BriefSectionContext } from './briefContext'
import {
  clearIdemKey,
  getOrCreateIdemKey,
  hasPendingIdemKey,
  shouldReleaseIdemKey,
  type SocialIdemScope,
} from './idempotency'
import {
  ATTEMPT_STATUS_LABEL,
  categoryTypeLabel,
  isActiveStatus,
  reasonText,
  socialErrorText,
} from './labels'
import { useAliveRef, usePolling } from './usePolling'

interface Props {
  ctx: BriefSectionContext
  /** Fikir üretimi henüz başlamadıysa kategoriler seçilebilir. */
  selectable: boolean
  selectedCategoryIds: number[]
  onToggleCategory: (id: number) => void
  /** POST yanıtı geldiğinde state okunmadan sonucu hemen göstermek için. */
  onCategoriesResult: (res: SocialCategoriesGenerateResponse) => void
  onBriefLocked: () => void
}

export default function BriefCategoriesSection({
  ctx,
  selectable,
  selectedCategoryIds,
  onToggleCategory,
  onCategoriesResult,
  onBriefLocked,
}: Props) {
  const { brief, workspaceId, state } = ctx
  const alive = useAliveRef()
  const [maxCategories, setMaxCategories] = useState(4)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const scope: SocialIdemScope = { workspaceId, briefId: brief.id, stage: 'categories' }
  const categories = state?.categories ?? []
  const attempt = state?.category_attempt ?? null
  const serverActive = isActiveStatus(attempt?.status) && !attempt?.lease_expired
  const pendingIntent = !generating && categories.length === 0 && hasPendingIdemKey(scope)

  // Başka sekmede / kaybolan yanıtta başlamış üretim: sunucu durumunu yokla
  usePolling(serverActive && !generating, ctx.refreshState, ctx.pollIntervalMs)

  const handleGenerate = async () => {
    if (generating || ctx.disabled || brief.is_stale) return
    const key = getOrCreateIdemKey(scope, `max=${maxCategories}`)
    setGenerating(true)
    setError(null)
    try {
      const res = await socialBriefApi.generateCategories(
        brief.id,
        { idempotency_key: key, max_categories: maxCategories },
        workspaceId
      )
      if (!alive.current) return
      if (isActiveStatus(res.data.attempt_status)) {
        // Aynı anahtarla daha önce başlamış deneme sürüyor: anahtar korunur, yoklanır
        await ctx.refreshState()
        return
      }
      clearIdemKey(scope)
      onCategoriesResult(res.data)
      onBriefLocked()
      await ctx.refreshState()
    } catch (err) {
      if (!alive.current) return
      if (shouldReleaseIdemKey(err)) clearIdemKey(scope)
      setError(socialErrorText(err, 'Kategori üretimi başarısız oldu.'))
      const code = apiErrorCode(err)
      if (
        code === 'CATEGORIES_ALREADY_GENERATED' ||
        code === 'ATTEMPT_CONFLICT' ||
        code === 'BRIEF_STALE' ||
        code === 'ASSIGNMENT_CHANGED'
      ) {
        await ctx.refreshState()
        onBriefLocked()
      }
    } finally {
      if (alive.current) setGenerating(false)
    }
  }

  const showGenerator = categories.length === 0 && !serverActive

  return (
    <section className="sb-stage" aria-labelledby={`sb-cat-title-${brief.id}`}>
      <div className="sb-stage-head">
        <div>
          <span className="sb-stage-step">Adım 1</span>
          <h3 id={`sb-cat-title-${brief.id}`} className="sb-stage-title">
            İçerik Kategorileri
          </h3>
          <p className="sb-stage-desc">
            Brief'teki kelimeler ve hedeflere göre yapay zekâ içerik temaları önerir.
          </p>
        </div>
        {attempt && (
          <span
            className={`sb-badge ${attempt.status === 'completed' ? 'sb-badge-active' : 'sb-badge-neutral'}`}
          >
            {ATTEMPT_STATUS_LABEL[attempt.status] ?? attempt.status}
          </span>
        )}
      </div>

      {error && (
        <div className="channels-alert channels-alert-error sb-stage-alert" role="alert">
          {error}
        </div>
      )}

      {serverActive && (
        <div className="sb-progress-note" role="status" aria-live="polite">
          <RefreshCw size={14} className="chx-spin" />
          Kategori üretimi sürüyor. Sonuç hazır olunca burada görünecek.
        </div>
      )}

      {attempt?.status === 'failed' && categories.length === 0 && !generating && (
        <p className="sb-muted-note">
          <AlertTriangle size={13} /> Son deneme başarısız oldu
          {attempt.reason_code ? `: ${reasonText(attempt.reason_code)}` : '.'} Yeniden
          başlatabilirsiniz.
        </p>
      )}

      {attempt && isActiveStatus(attempt.status) && attempt.lease_expired && (
        <p className="sb-muted-note">
          <AlertTriangle size={13} /> Önceki üretim yanıt vermiyor. Yeniden başlatabilirsiniz.
        </p>
      )}

      {pendingIntent && (
        <p className="sb-muted-note">
          Önceki isteğin yanıtı alınamadı. "Kategorileri Üret" aynı işlemi güvenle tekrar dener;
          kopya üretim oluşmaz.
        </p>
      )}

      {showGenerator && (
        <div className="sb-cat-gen-bar">
          <div className="sb-cat-gen-intro">
            <Sparkles size={18} aria-hidden="true" />
            <div>
              <div className="sb-cat-gen-title">Kategori üretimini başlat</div>
              <div className="sb-cat-gen-sub">
                Yapay zekâ en az 2, en fazla seçtiğiniz sayıda kategori önerir. Kategoriler
                oluşturulunca brief kilitlenir ve değiştirilemez.
              </div>
            </div>
          </div>
          <div className="sb-cat-gen-controls">
            <label htmlFor={`max-categories-${brief.id}`}>En fazla kategori</label>
            <select
              id={`max-categories-${brief.id}`}
              className="sb-select sb-select-narrow"
              value={maxCategories}
              onChange={(e) => setMaxCategories(Number(e.target.value))}
              disabled={generating || brief.is_stale}
            >
              {[2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="sb-btn sb-btn-primary"
              onClick={handleGenerate}
              disabled={generating || brief.is_stale || ctx.disabled}
            >
              {generating ? (
                <>
                  <RefreshCw size={14} className="chx-spin" />
                  Üretiliyor...
                </>
              ) : (
                <>
                  <Sparkles size={14} />
                  Kategorileri Üret
                </>
              )}
            </button>
          </div>
        </div>
      )}

      {categories.length > 0 && (
        <>
          {selectable && (
            <p className="sb-muted-note">
              Fikir üretilecek kategorileri seçin ({selectedCategoryIds.length} seçili).
            </p>
          )}
          <div className="sb-category-grid">
            {categories.map((cat) => {
              const checked = selectedCategoryIds.includes(cat.id)
              const inputId = `sb-cat-${brief.id}-${cat.id}`
              const Card = selectable ? 'label' : 'div'
              return (
                <Card
                  key={cat.id}
                  className={`sb-category-card ${selectable ? 'is-selectable' : ''} ${checked ? 'is-selected' : 'is-unselected'}`}
                >
                  <div className="sb-category-head">
                    {selectable ? (
                      <span className="sb-check-label">
                        <input
                          id={inputId}
                          type="checkbox"
                          checked={checked}
                          onChange={() => onToggleCategory(cat.id)}
                          aria-label={cat.category_name}
                        />
                        <span className="sb-category-name">{cat.category_name}</span>
                      </span>
                    ) : (
                      <h4 className="sb-category-name">
                        <CheckCircle2 size={13} aria-hidden="true" /> {cat.category_name}
                      </h4>
                    )}
                    <span className="sb-category-type-pill">
                      {categoryTypeLabel(cat.category_type)}
                    </span>
                  </div>
                  <p className="sb-category-desc">{cat.description}</p>
                  <div className="sb-category-score-row">
                    <span>Uygunluk</span>
                    <span className="sb-category-score-val">
                      %{Math.round(cat.relevance_score * 100)}
                    </span>
                  </div>
                  {cat.suggested_keyword_ids.length > 0 && (
                    <div className="sb-chip-container">
                      {cat.suggested_keyword_ids.map((id) => (
                        <span key={id} className="sb-chip sb-chip-sm">
                          {ctx.keywordLabel(id)}
                        </span>
                      ))}
                    </div>
                  )}
                </Card>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
