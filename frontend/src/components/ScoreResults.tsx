import { useEffect, useRef, useState } from 'react'
import { Award, ChevronDown, ChevronUp, ChevronsUpDown } from 'lucide-react'
import { apiErrorMessage, scoringApi, ScoringSortBy } from '../services/api'
import ErrorBanner from './ErrorBanner'
import TablePager from './TablePager'
import { poolClassLabel, selectionReasonLabel } from './statusLabels'
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

const SCORE_PAGE_SIZE = 25

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
  const [page, setPage] = useState(0)
  const [isLoadingScores, setIsLoadingScores] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  // Run/workspace değişince uçuştaki eski yanıt state'e yazılmaz
  const scopeRef = useRef(`${workspaceId}:${runId}`)
  scopeRef.current = `${workspaceId}:${runId}`
  // Aynı run içinde art arda sıralama/sayfa isteğinde son istek kazanır
  const requestSeqRef = useRef(0)

  // Sıralama sunucuda tüm sonuçlar üzerinde yapılır; ekranda yalnız bir sayfa durur.
  const fetchScores = async ({
    pageIndex = 0,
    sort = scoreSort,
  }: {
    pageIndex?: number
    sort?: ScoreSortState
  }) => {
    if (!workspaceId || !runId) return
    const requestScope = scopeRef.current
    const requestSeq = ++requestSeqRef.current
    const isCurrent = () =>
      scopeRef.current === requestScope && requestSeqRef.current === requestSeq
    setIsLoadingScores(true)
    setErrorMsg(null)
    try {
      const res = await scoringApi.getScores({
        runId,
        brand_profile_id: workspaceId,
        limit: SCORE_PAGE_SIZE,
        offset: pageIndex * SCORE_PAGE_SIZE,
        sort_by: sort.sort_by,
        sort_dir: sort.sort_dir,
      })
      if (!isCurrent()) return
      setScores(res.data.scores || [])
      setPage(pageIndex)
      setTotalScored(Number(res.data.total_scored || 0))
      setAlgorithmVersion(res.data.algorithm_version || null)
    } catch (err) {
      if (!isCurrent()) return
      setErrorMsg(`Skorlar yüklenemedi. ${apiErrorMessage(err, '')}`.trim())
    } finally {
      if (isCurrent()) setIsLoadingScores(false)
    }
  }

  useEffect(() => {
    setErrorMsg(null)
    setScores([])
    setTotalScored(0)
    setAlgorithmVersion(null)
    setPage(0)
    setScoreSort(defaultSort)
    if (runId && workspaceId) {
      void fetchScores({ pageIndex: 0, sort: defaultSort })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, workspaceId, defaultSort.sort_by])

  const sortScores = async (column: ScoringSortBy) => {
    if (!runId) return
    const nextSort = nextScoreSortState(scoreSort, column)
    setScoreSort(nextSort)
    await fetchScores({ pageIndex: 0, sort: nextSort })
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
    if (!values.rank && !values.finalRank && !values.reason) {
      return (
        <div className="v3-channel-result">
          <strong>{formatScore(values.score)}</strong>
          <span>Aday değil</span>
        </div>
      )
    }
    const reason = values.finalRank || !values.reason ? null : selectionReasonLabel(values.reason)
    const poolLabel = poolClassLabel(
      channel,
      values.poolClass ?? (channel === 'social' ? score.social_priority : null)
    )
    const meta = [values.rank ? `Aday sırası ${values.rank}` : null, poolLabel]
      .filter(Boolean)
      .join(' · ')
    return (
      <div className="v3-channel-result">
        <strong>{formatScore(values.score)}</strong>
        {values.finalRank ? (
          <span className="v3-delivered">Havuzda · {values.finalRank}. sıra</span>
        ) : reason ? (
          <span className={reason.neutral ? 'v3-skipped' : 'v3-excluded'}>{reason.label}</span>
        ) : null}
        {meta && <span className="v3-meta">{meta}</span>}
      </div>
    )
  }

  if (!runId) {
    return <div className="empty-text">Bir analiz seçin.</div>
  }

  const isV3Run = algorithmVersion === 'v3'

  return (
    <div className="scores-section">
      <div className="scores-heading">
        <div>
          <h2>Skorlar - Çalışma #{runId}</h2>
          <p>
            {totalScored > 0
              ? `${totalScored} anahtar kelime skorlandı`
              : errorMsg
                ? 'Skorlar yüklenemedi'
                : 'Skor kaydı bulunamadı'}
          </p>
        </div>
      </div>
      {errorMsg && (
        <ErrorBanner
          error={errorMsg}
          onRetry={() => void fetchScores({ pageIndex: page, sort: scoreSort })}
          retryLabel="Skorları yeniden yükle"
        />
      )}
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
                  {show.social && <th>{renderSortableHeader('social_score', 'Sosyal Medya')}</th>}
                </>
              ) : (
                <>
                  {show.ads && <th>{renderSortableHeader('ads_score', 'ADS Skor')}</th>}
                  {show.seo && <th>{renderSortableHeader('seo_score', 'SEO Skor')}</th>}
                  {show.social && (
                    <th>{renderSortableHeader('social_score', 'Sosyal Medya Skor')}</th>
                  )}
                  {show.ads && <th>{renderSortableHeader('ads_rank', 'ADS Sıra')}</th>}
                  {show.seo && <th>{renderSortableHeader('seo_rank', 'SEO Sıra')}</th>}
                  {show.social && (
                    <th>{renderSortableHeader('social_rank', 'Sosyal Medya Sıra')}</th>
                  )}
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
                  {errorMsg ? 'Skorlar yüklenemedi.' : 'Skor kaydı bulunamadı.'}
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
      <TablePager
        page={page}
        pageSize={SCORE_PAGE_SIZE}
        total={totalScored}
        unit="kelime"
        disabled={isLoadingScores}
        onPage={(next) => void fetchScores({ pageIndex: next, sort: scoreSort })}
      />
    </div>
  )
}
