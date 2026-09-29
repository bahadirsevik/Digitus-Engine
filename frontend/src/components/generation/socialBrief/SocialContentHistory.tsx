import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, History, RefreshCw, X } from 'lucide-react'
import { socialBriefApi, type SocialContentHistoryItemResponse } from '../../../services/api'
import SocialContentCard from './SocialContentCard'
import { fallbackFormatLabel, fallbackPlatformLabel, socialErrorText } from './labels'
import { useAliveRef } from './usePolling'

export const HISTORY_PAGE_SIZE = 20

function shortTitle(item: SocialContentHistoryItemResponse): string {
  const text =
    item.idea_title?.trim() ||
    item.caption
      .split('\n')
      .find((line) => line.trim())
      ?.trim()
  if (!text) return `İçerik #${item.id}`
  return text.length > 90 ? `${text.slice(0, 87).trimEnd()}…` : text
}

interface Props {
  workspaceId: number
  onClose: () => void
}

/**
 * Workspace geneli "Geçmiş Sosyal İçerikler" (plan K13). Yalnız görüntüleme:
 * farklı run ve brief'lerde (ve eski sihirbazda) üretilmiş içerikler en yeniden
 * eskiye, sayfa sayfa listelenir. Workspace değişince üst bileşen `key` ile
 * yeniden kurar; geç gelen yanıtlar `alive` korumasıyla atılır.
 */
export default function SocialContentHistory({ workspaceId, onClose }: Props) {
  const alive = useAliveRef()
  const [items, setItems] = useState<SocialContentHistoryItemResponse[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const load = useCallback(
    async (offset: number) => {
      if (offset === 0) setLoading(true)
      else setLoadingMore(true)
      setError(null)
      try {
        const res = await socialBriefApi.getContentHistory(workspaceId, {
          limit: HISTORY_PAGE_SIZE,
          offset,
        })
        if (!alive.current) return
        setItems((prev) => {
          const base = offset === 0 ? [] : prev
          const seen = new Set(base.map((i) => i.id))
          return [...base, ...res.data.items.filter((i) => !seen.has(i.id))]
        })
        setTotal(res.data.total)
        setHasMore(res.data.has_more)
        if (offset === 0) setExpandedId(null)
      } catch (err) {
        if (alive.current) setError(socialErrorText(err, 'Sosyal içerik geçmişi alınamadı.'))
      } finally {
        if (alive.current) {
          setLoading(false)
          setLoadingMore(false)
        }
      }
    },
    [workspaceId, alive]
  )

  useEffect(() => {
    void load(0)
  }, [load])

  return (
    <section className="sb-card sb-history" aria-labelledby="sb-history-title">
      <div className="sb-card-head">
        <div>
          <h2 id="sb-history-title" className="sb-card-title">
            <History size={18} aria-hidden="true" /> Geçmiş Sosyal İçerikler
          </h2>
          <p className="sb-card-subtitle">
            Bu marka çalışmasında farklı analizler ve brief'lerle üretilmiş tüm sosyal içerikler.
            {total > 0 && ` Toplam ${total} içerik.`}
          </p>
        </div>
        <button type="button" className="sb-btn sb-btn-ghost" onClick={onClose}>
          <X size={15} />
          Kapat
        </button>
      </div>

      {error && (
        <div className="channels-alert channels-alert-error sb-stage-alert" role="alert">
          {error}{' '}
          <button
            type="button"
            className="sb-btn sb-btn-ghost sb-btn-xs"
            onClick={() => load(items.length)}
          >
            Tekrar dene
          </button>
        </div>
      )}

      {loading ? (
        <div className="sb-empty-state" role="status">
          <RefreshCw size={22} className="chx-spin" />
          <p className="sb-empty-text">Geçmiş yükleniyor...</p>
        </div>
      ) : items.length === 0 && !error ? (
        <div className="sb-empty-state">
          <p className="sb-empty-text">Bu marka çalışmasında henüz sosyal içerik üretilmedi.</p>
        </div>
      ) : (
        <div className="sb-history-list">
          {items.map((c) => (
            <div key={c.id} className={`sb-history-entry ${expandedId === c.id ? 'is-open' : ''}`}>
              <button
                type="button"
                className="sb-history-toggle"
                aria-expanded={expandedId === c.id}
                aria-controls={`sb-history-content-${c.id}`}
                onClick={() => setExpandedId((current) => (current === c.id ? null : c.id))}
              >
                <span className="sb-history-row-main">
                  <span className="sb-history-row-title">{shortTitle(c)}</span>
                  <span className="sb-history-row-meta">
                    {fallbackPlatformLabel(c.platform)} · {fallbackFormatLabel(c.content_format)}
                    {c.created_at && ` · ${new Date(c.created_at).toLocaleDateString('tr-TR')}`}
                  </span>
                </span>
                {c.is_stale && <span className="sb-badge sb-badge-stale">Güncel değil</span>}
                <ChevronDown size={18} className="sb-history-chevron" aria-hidden="true" />
              </button>
              <div
                id={`sb-history-content-${c.id}`}
                className="sb-history-expanded"
                hidden={expandedId !== c.id}
              >
                {expandedId === c.id && (
                  <SocialContentCard
                    view={{
                      id: c.id,
                      ideaTitle: c.idea_title,
                      platformLabel: fallbackPlatformLabel(c.platform),
                      formatLabel: fallbackFormatLabel(c.content_format),
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
                      durationMinSec: c.duration_min_sec,
                      durationMaxSec: c.duration_max_sec,
                      validationWarnings: c.validation_warnings,
                      isStale: c.is_stale,
                      meta: (
                        <p className="sb-content-meta">
                          {c.brief_id != null ? `Brief #${c.brief_id}` : 'Eski (brief’siz) içerik'}
                          {' · '}
                          {c.run_name ? `Analiz: ${c.run_name}` : `Analiz #${c.scoring_run_id}`}
                          {c.created_at && ` · ${new Date(c.created_at).toLocaleString('tr-TR')}`}
                        </p>
                      ),
                    }}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {hasMore && !loading && (
        <div className="sb-history-more">
          <button
            type="button"
            className="sb-btn sb-btn-secondary"
            onClick={() => load(items.length)}
            disabled={loadingMore}
          >
            {loadingMore ? <RefreshCw size={14} className="chx-spin" /> : null}
            Daha fazla göster
          </button>
        </div>
      )}
    </section>
  )
}
