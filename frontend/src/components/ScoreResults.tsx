import { useEffect, useState } from 'react'
import { Award, ChevronDown, ChevronUp, ChevronsUpDown } from 'lucide-react'
import { scoringApi, ScoringSortBy } from '../services/api'
import { DEFAULT_SCORE_SORT, nextScoreSortState, ScoreSortState } from '../pages/scoringSort'
import type { AnalysisChannels } from './AnalysisProgress'
import '../pages/Scoring.css'

interface KeywordScore {
  keyword_id: number
  keyword: string
  ads_score: number | null
  seo_score: number | null
  social_score: number | null
  ads_rank?: number | null
  seo_rank?: number | null
  social_rank?: number | null
  family_id?: string | null
  family_name?: string | null
  ads_final_rank?: number | null
  seo_final_rank?: number | null
  social_final_rank?: number | null
  ads_exclude_reason?: string | null
  seo_exclude_reason?: string | null
  social_exclude_reason?: string | null
  ads_pool_class?: string | null
  seo_pool_class?: string | null
  social_pool_class?: string | null
  social_priority?: string | null
}

const SCORE_PAGE_SIZE = 100

export default function ScoreResults({
  runId,
  workspaceId,
  channels,
}: {
  runId: number | null
  workspaceId?: number | null
  /** Run'da kapalı kanalların sütunları gizlenir (undefined = göster). */
  channels?: AnalysisChannels
}) {
  const show = {
    ads: channels?.ads !== false,
    seo: channels?.seo !== false,
    social: channels?.social !== false,
  }
  const shown = Object.values(show).filter(Boolean).length
  // Varsayılan sıralama ilk açık kanalın skoru (ADS kapalı run'da boş ADS'e göre sıralanmasın)
  const defaultSort: ScoreSortState = show.ads
    ? DEFAULT_SCORE_SORT
    : { sort_by: show.seo ? 'seo_score' : 'social_score', sort_dir: 'desc' }
  const [scores, setScores] = useState<KeywordScore[]>([])
  const [totalScored, setTotalScored] = useState(0)
  const [algorithmVersion, setAlgorithmVersion] = useState<string | null>(null)
  const [scoreSort, setScoreSort] = useState<ScoreSortState>(defaultSort)
  const [isLoadingScores, setIsLoadingScores] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const fetchScores = async ({
    offset = 0,
    sort = scoreSort,
    append = false,
  }: {
    offset?: number
    sort?: ScoreSortState
    append?: boolean
  }) => {
    if (!workspaceId || !runId) return
    if (append) setIsLoadingMore(true)
    else setIsLoadingScores(true)
    setErrorMsg(null)
    try {
      const res = await scoringApi.getScores({
        runId,
        brand_profile_id: workspaceId,
        limit: SCORE_PAGE_SIZE,
        offset,
        sort_by: sort.sort_by,
        sort_dir: sort.sort_dir,
      })
      const nextScores = res.data.scores || []
      setScores((prev) => (append ? [...prev, ...nextScores] : nextScores))
      setTotalScored(Number(res.data.total_scored || 0))
      setAlgorithmVersion(res.data.algorithm_version || null)
    } catch {
      setErrorMsg(append ? 'Daha fazla skor yüklenemedi.' : 'Skorlar yüklenemedi.')
    } finally {
      if (append) setIsLoadingMore(false)
      else setIsLoadingScores(false)
    }
  }

  useEffect(() => {
    setScores([])
    setTotalScored(0)
    setAlgorithmVersion(null)
    setScoreSort(defaultSort)
    if (runId && workspaceId) {
      void fetchScores({ offset: 0, sort: defaultSort, append: false })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, workspaceId, defaultSort.sort_by])

  const sortScores = async (column: ScoringSortBy) => {
    if (!runId) return
    const nextSort = nextScoreSortState(scoreSort, column)
    setScoreSort(nextSort)
    await fetchScores({ offset: 0, sort: nextSort, append: false })
  }

  const loadMoreScores = async () => {
    if (!runId || isLoadingMore || scores.length >= totalScored) return
    await fetchScores({
      offset: scores.length,
      sort: scoreSort,
      append: true,
    })
  }

  const formatScore = (value: number | string | null | undefined) => {
    if (value === null || value === undefined || value === '') return '-'
    return Number(value).toFixed(4)
  }

  const renderSortableHeader = (column: ScoringSortBy, label: string) => {
    const active = scoreSort.sort_by === column
    const Icon = active ? (scoreSort.sort_dir === 'desc' ? ChevronDown : ChevronUp) : ChevronsUpDown
    return (
      <button
        type="button"
        className={`score-sort-button ${active ? 'active' : ''}`}
        onClick={() => sortScores(column)}
      >
        {label}
        <Icon size={14} />
      </button>
    )
  }

  const renderV3Channel = (score: KeywordScore, channel: 'ads' | 'seo' | 'social') => {
    const values = {
      ads: {
        score: score.ads_score,
        rank: score.ads_rank,
        finalRank: score.ads_final_rank,
        reason: score.ads_exclude_reason,
        poolClass: score.ads_pool_class,
      },
      seo: {
        score: score.seo_score,
        rank: score.seo_rank,
        finalRank: score.seo_final_rank,
        reason: score.seo_exclude_reason,
        poolClass: score.seo_pool_class,
      },
      social: {
        score: score.social_score,
        rank: score.social_rank,
        finalRank: score.social_final_rank,
        reason: score.social_exclude_reason,
        poolClass: score.social_pool_class,
      },
    }[channel]
    return (
      <div className="v3-channel-result">
        <strong>{formatScore(values.score)}</strong>
        <span>{values.rank ? `Algoritma #${values.rank}` : 'Aday değil'}</span>
        {values.finalRank ? (
          <span className="v3-delivered">Teslim #{values.finalRank}</span>
        ) : values.reason ? (
          <span className="v3-excluded">Elendi: {values.reason}</span>
        ) : null}
        {(values.poolClass || (channel === 'social' && score.social_priority)) && (
          <span className="v3-meta">
            {[values.poolClass, channel === 'social' ? score.social_priority : null]
              .filter(Boolean)
              .join(' · ')}
          </span>
        )}
      </div>
    )
  }

  if (!runId) {
    return <div className="empty-text">Skorlama çalışması seçin.</div>
  }

  const hasMoreScores = scores.length < totalScored
  const isV3Run = algorithmVersion === 'v3'

  return (
    <div className="scores-section">
      <div className="scores-heading">
        <div>
          <h2>Skorlar - Çalışma #{runId}</h2>
          <p>
            {totalScored > 0
              ? `${scores.length}/${totalScored} skor gösteriliyor`
              : 'Skor kaydı bulunamadı'}
          </p>
        </div>
      </div>
      {errorMsg && <div className="error-banner">{errorMsg}</div>}
      <div
        className={`table-container ${isLoadingScores && scores.length > 0 ? 'table-sorting' : ''}`}
      >
        <table className="table">
          <thead>
            <tr>
              <th>Anahtar Kelime</th>
              {isV3Run ? (
                <>
                  <th>Aile</th>
                  {show.ads && <th>{renderSortableHeader('ads_score', 'ADS')}</th>}
                  {show.seo && <th>{renderSortableHeader('seo_score', 'SEO')}</th>}
                  {show.social && <th>{renderSortableHeader('social_score', 'SOCIAL')}</th>}
                </>
              ) : (
                <>
                  {show.ads && <th>{renderSortableHeader('ads_score', 'ADS Skor')}</th>}
                  {show.seo && <th>{renderSortableHeader('seo_score', 'SEO Skor')}</th>}
                  {show.social && <th>{renderSortableHeader('social_score', 'SOCIAL Skor')}</th>}
                  {show.ads && <th>{renderSortableHeader('ads_rank', 'ADS Sıra')}</th>}
                  {show.seo && <th>{renderSortableHeader('seo_rank', 'SEO Sıra')}</th>}
                  {show.social && <th>{renderSortableHeader('social_rank', 'SOCIAL Sıra')}</th>}
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {isLoadingScores && scores.length === 0 ? (
              <tr>
                <td colSpan={isV3Run ? 2 + shown : 1 + 2 * shown} className="scores-empty-cell">
                  Skorlar yükleniyor...
                </td>
              </tr>
            ) : scores.length === 0 ? (
              <tr>
                <td colSpan={isV3Run ? 2 + shown : 1 + 2 * shown} className="scores-empty-cell">
                  Skor kaydı bulunamadı.
                </td>
              </tr>
            ) : (
              scores.map((score) => (
                <tr key={score.keyword_id}>
                  <td>
                    <strong>{score.keyword}</strong>
                  </td>
                  {isV3Run ? (
                    <>
                      <td>
                        <div className="v3-family">
                          <strong>{score.family_name || score.family_id || '—'}</strong>
                          {score.family_name && score.family_id && <span>{score.family_id}</span>}
                        </div>
                      </td>
                      {show.ads && <td>{renderV3Channel(score, 'ads')}</td>}
                      {show.seo && <td>{renderV3Channel(score, 'seo')}</td>}
                      {show.social && <td>{renderV3Channel(score, 'social')}</td>}
                    </>
                  ) : (
                    <>
                      {show.ads && <td>{formatScore(score.ads_score)}</td>}
                      {show.seo && <td>{formatScore(score.seo_score)}</td>}
                      {show.social && <td>{formatScore(score.social_score)}</td>}
                      {show.ads && (
                        <td>
                          {score.ads_rank && (
                            <span className={score.ads_rank <= 3 ? 'top-rank' : ''}>
                              {score.ads_rank <= 3 && <Award size={14} />}#{score.ads_rank}
                            </span>
                          )}
                        </td>
                      )}
                      {show.seo && (
                        <td>
                          {score.seo_rank && (
                            <span className={score.seo_rank <= 3 ? 'top-rank' : ''}>
                              {score.seo_rank <= 3 && <Award size={14} />}#{score.seo_rank}
                            </span>
                          )}
                        </td>
                      )}
                      {show.social && (
                        <td>
                          {score.social_rank && (
                            <span className={score.social_rank <= 3 ? 'top-rank' : ''}>
                              {score.social_rank <= 3 && <Award size={14} />}#{score.social_rank}
                            </span>
                          )}
                        </td>
                      )}
                    </>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {hasMoreScores && (
        <div className="scores-load-more">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={loadMoreScores}
            disabled={isLoadingMore}
          >
            {isLoadingMore ? 'Yükleniyor...' : 'Daha fazla göster'}
          </button>
        </div>
      )}
    </div>
  )
}
