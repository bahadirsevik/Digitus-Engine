import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Play,
  Eye,
  TrendingUp,
  Award,
  Trash2,
  Download,
  ArrowRight,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
} from 'lucide-react'
import { apiErrorMessage, scoringApi, ScoringRunCreate, ScoringSortBy } from '../services/api'
import type { ScoringRun } from '../types/models'
import { DEFAULT_SCORE_SORT, nextScoreSortState, ScoreSortState } from './scoringSort'
import { useBrandStore } from '../stores/brandStore'
import './Scoring.css'

interface KeywordScore {
  keyword_id: number
  keyword: string
  ads_score: number | null
  seo_score: number | null
  social_score: number | null
  ads_rank?: number | null
  exclusions?: Array<{
    reason: 'competitor' | 'topic' | 'price'
    channels: string[]
    matched_term?: string | null
  }>
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

export default function Scoring() {
  const navigate = useNavigate()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)

  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const [scores, setScores] = useState<KeywordScore[]>([])
  const [totalScored, setTotalScored] = useState(0)
  const [scoreSort, setScoreSort] = useState<ScoreSortState>(DEFAULT_SCORE_SORT)
  const [isLoadingScores, setIsLoadingScores] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [engineV3Enabled, setEngineV3Enabled] = useState(false)

  const [newRun, setNewRun] = useState<
    ScoringRunCreate & {
      enable_ads: boolean
      enable_seo: boolean
      enable_social: boolean
      keyword_selection_mode: 'all' | 'top_n' | 'specific'
      keyword_limit: number
      skip_relevance: boolean
    }
  >({
    run_name: '',
    brand_profile_id: undefined,
    ads_capacity: 20,
    seo_capacity: 30,
    social_capacity: 25,
    default_relevance_coefficient: 1.0,
    keyword_source_filter: null,
    enable_ads: true,
    enable_seo: true,
    enable_social: true,
    keyword_selection_mode: 'top_n',
    keyword_limit: 200,
    selected_keyword_ids: undefined,
    skip_relevance: false,
    auto_assign_channels: true,
    algorithm_version: 'v3',
  })

  useEffect(() => {
    fetchRuns()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id])

  useEffect(() => {
    if (activeWorkspace?.id) {
      setNewRun((prev) => ({ ...prev, brand_profile_id: activeWorkspace.id }))
    }
  }, [activeWorkspace?.id])

  useEffect(() => {
    let cancelled = false
    scoringApi
      .getCapabilities()
      .then((res) => {
        if (!cancelled) {
          const v3 = Boolean(res.data?.engine_v3_enabled)
          setEngineV3Enabled(v3)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEngineV3Enabled(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const fetchRuns = async () => {
    if (!activeWorkspace?.id) {
      setRuns([])
      return
    }
    try {
      const res = await scoringApi.listRuns({ brand_profile_id: activeWorkspace.id })
      const data = res.data || []
      setRuns(data)
    } catch (error) {
      console.error('Error fetching runs:', error)
    }
  }

  const createRun = async () => {
    if (!activeWorkspace?.id) {
      setErrorMsg('Önce Marka Çalışması seçin')
      return
    }
    if (!newRun.enable_ads && !newRun.enable_seo && !newRun.enable_social) {
      setErrorMsg('En az bir kanal seçilmelidir')
      return
    }
    if (!engineV3Enabled) {
      setErrorMsg('Motor v3 sunucuda kapalı veya doğrulanamadı. Çalışma oluşturulmadı.')
      return
    }
    setErrorMsg(null)
    try {
      const res = await scoringApi.createRun({
        ...newRun,
        brand_profile_id: activeWorkspace.id,
      })
      setShowModal(false)
      setNewRun((prev) => ({
        ...prev,
        run_name: '',
        brand_profile_id: activeWorkspace?.id,
        ads_capacity: 20,
        seo_capacity: 30,
        social_capacity: 25,
        default_relevance_coefficient: 1.0,
        keyword_source_filter: null,
        enable_ads: true,
        enable_seo: true,
        enable_social: true,
        keyword_selection_mode: 'top_n',
        keyword_limit: 200,
        selected_keyword_ids: undefined,
        skip_relevance: false,
        auto_assign_channels: true,
        algorithm_version: 'v3',
      }))
      fetchRuns()
      setSelectedRun(res.data.id)
    } catch (error) {
      console.error('Error creating run:', error)
      // v2.1 kapıları tipli 409 döner (V21_DISABLED / STRATEGY_REQUIRED /
      // SOCIAL_AUTHORITY_EXPERIMENTAL) — mesaj kullanıcıya gösterilir
      setErrorMsg(apiErrorMessage(error, 'Çalışma oluşturulamadı'))
    }
  }

  const executeRun = async (runId: number) => {
    if (!activeWorkspace?.id) return
    setLoading(true)
    try {
      const res = await scoringApi.executeRun(runId, activeWorkspace.id)
      const data = res.data as { channel_assignment_task_id?: string; status?: string }
      if (data?.channel_assignment_task_id) {
        navigate(`/channels?run_id=${runId}&task_id=${data.channel_assignment_task_id}`)
        return
      }
      fetchRuns()
      viewScores(runId)
    } catch (error) {
      console.error('Error executing run:', error)
    }
    setLoading(false)
  }

  const fetchScores = async ({
    runId,
    offset = 0,
    sort = scoreSort,
    append = false,
  }: {
    runId: number
    offset?: number
    sort?: ScoreSortState
    append?: boolean
  }) => {
    if (!activeWorkspace?.id) return
    if (append) setIsLoadingMore(true)
    else setIsLoadingScores(true)
    setErrorMsg(null)
    try {
      const res = await scoringApi.getScores({
        runId,
        brand_profile_id: activeWorkspace.id,
        limit: SCORE_PAGE_SIZE,
        offset,
        sort_by: sort.sort_by,
        sort_dir: sort.sort_dir,
      })
      const nextScores = res.data.scores || []
      setScores((prev) => (append ? [...prev, ...nextScores] : nextScores))
      setTotalScored(Number(res.data.total_scored || 0))
    } catch (error) {
      console.error('Error fetching scores:', error)
      setErrorMsg(append ? 'Daha fazla skor yüklenemedi.' : 'Skorlar yüklenemedi.')
    } finally {
      if (append) setIsLoadingMore(false)
      else setIsLoadingScores(false)
    }
  }

  const viewScores = async (runId: number) => {
    const nextSort = DEFAULT_SCORE_SORT
    setSelectedRun(runId)
    setScoreSort(nextSort)
    setScores([])
    setTotalScored(0)
    await fetchScores({ runId, offset: 0, sort: nextSort, append: false })
  }

  const sortScores = async (column: ScoringSortBy) => {
    if (!selectedRun) return
    const nextSort = nextScoreSortState(scoreSort, column)
    setScoreSort(nextSort)
    // Keep existing rows visible during fetch — no scroll jump, no flash
    await fetchScores({ runId: selectedRun, offset: 0, sort: nextSort, append: false })
  }

  const loadMoreScores = async () => {
    if (!selectedRun || isLoadingMore || scores.length >= totalScored) return
    await fetchScores({
      runId: selectedRun,
      offset: scores.length,
      sort: scoreSort,
      append: true,
    })
  }

  // v2 skorları insan ölçeğinde (~-15..70) — 2 hane yeterli
  const formatScore = (value: number | string | null | undefined) => {
    if (value === null || value === undefined || value === '') return '-'
    return Number(value).toFixed(2)
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
    const priority = channel === 'social' ? score.social_priority : null
    return (
      <div className="v3-channel-result">
        <strong>{formatScore(values.score)}</strong>
        <span>{values.rank ? `Algoritma #${values.rank}` : 'Aday değil'}</span>
        {values.finalRank ? (
          <span className="v3-delivered">Teslim #{values.finalRank}</span>
        ) : values.reason ? (
          <span className="v3-excluded">Elendi: {values.reason}</span>
        ) : null}
        {(values.poolClass || priority) && (
          <span className="v3-meta">
            {[values.poolClass, priority].filter(Boolean).join(' · ')}
          </span>
        )}
      </div>
    )
  }

  const hasMoreScores = selectedRun !== null && scores.length < totalScored

  return (
    <div className="scoring-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Skorlama</h1>
          <p>Anahtar kelime skorlama işlemleri</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>
          <TrendingUp size={18} />
          Yeni Skorlama
        </button>
      </header>

      {activeWorkspace && (
        <div className="workspace-pill">
          <span className="pill-label">Aktif Marka:</span>
          <span className="pill-value">{activeWorkspace.name}</span>
        </div>
      )}

      {!activeWorkspace && (
        <div className="no-workspace">
          <AlertCircle size={20} />
          <span>Önce Marka Çalışması seçin</span>
        </div>
      )}

      {!showModal && errorMsg && <div className="error-banner">{errorMsg}</div>}

      <div className="runs-section">
        <h2>Skorlama Çalışmaları</h2>
        <div className="runs-grid">
          {runs.length === 0 ? (
            <p className="empty-text">Henüz skorlama çalışması yok</p>
          ) : (
            runs.map((run) => (
              <div
                key={run.id}
                className={`run-card glass-card ${selectedRun === run.id ? 'active' : ''}`}
              >
                <div className="run-header">
                  <span className="run-name">
                    {run.run_name || `Çalışma #${run.id}`}
                    {run.keyword_source_filter && (
                      <span
                        className={`source-badge source-badge--${run.keyword_source_filter === 'google_ads_api' ? 'ads' : 'csv'}`}
                      >
                        {run.keyword_source_filter === 'google_ads_api' ? 'Google Ads' : 'CSV'}
                      </span>
                    )}
                    {run.algorithm_version === 'v2_1' && (
                      <span
                        className="source-badge source-badge--ads"
                        title="v2.1 deneysel algoritma koşusu"
                      >
                        v2.1
                      </span>
                    )}
                  </span>
                  <span className={`status status-${run.status}`}>{run.status}</span>
                </div>
                <div className="run-capacities">
                  <span
                    className={`badge badge-ads ${run.enable_ads === false ? 'badge-muted' : ''}`}
                  >
                    ADS: {run.ads_capacity}
                  </span>
                  <span
                    className={`badge badge-seo ${run.enable_seo === false ? 'badge-muted' : ''}`}
                  >
                    SEO: {run.seo_capacity}
                  </span>
                  <span
                    className={`badge badge-social ${run.enable_social === false ? 'badge-muted' : ''}`}
                  >
                    SOCIAL: {run.social_capacity}
                  </span>
                  <span className="badge badge-social">
                    Katsayı:{' '}
                    {typeof run.default_relevance_coefficient === 'number'
                      ? run.default_relevance_coefficient.toFixed(2)
                      : Number(run.default_relevance_coefficient || 1).toFixed(2)}
                  </span>
                </div>
                <div className="run-actions">
                  {run.status === 'pending' && (
                    <button
                      className="btn btn-success"
                      onClick={() => executeRun(run.id)}
                      disabled={loading}
                    >
                      <Play size={16} />
                      {loading ? 'Çalışıyor...' : 'Çalıştır'}
                    </button>
                  )}

                  {[
                    'scored',
                    'relevance_computing',
                    'relevance_computed',
                    // Kanal ataması SÜRERKEN de skorlar hazır ve görüntülenebilir;
                    // bu statü eksik olduğu için aktif run atama bitene dek görünmüyordu.
                    'channel_assigning',
                    'channel_assigned',
                    'completed',
                  ].includes(run.status) && (
                    <>
                      <button className="btn btn-secondary" onClick={() => viewScores(run.id)}>
                        <Eye size={16} />
                        Görüntüle
                      </button>
                      <button
                        className="btn btn-success"
                        onClick={async () => {
                          try {
                            const res = await scoringApi.exportXlsx(run.id, activeWorkspace?.id)
                            const url = window.URL.createObjectURL(new Blob([res.data]))
                            const link = document.createElement('a')
                            link.href = url
                            link.setAttribute('download', `scoring_run_${run.id}.xlsx`)
                            document.body.appendChild(link)
                            link.click()
                            link.remove()
                          } catch (err) {
                            console.error('Export error:', err)
                          }
                        }}
                      >
                        <Download size={16} />
                        Excel
                      </button>
                      <button
                        className="btn btn-primary"
                        onClick={() => {
                          if (run.algorithm_version === 'v3') {
                            navigate(`/channels?run_id=${run.id}`)
                          } else {
                            const hasConfirmedProfile = activeWorkspace?.status === 'confirmed'
                            if (run.skip_relevance) {
                              navigate(`/channels?run_id=${run.id}`)
                            } else if (hasConfirmedProfile) {
                              navigate(`/relevance?run_id=${run.id}`)
                            } else {
                              navigate(`/brand-profile?run_id=${run.id}`)
                            }
                          }
                        }}
                      >
                        Sonraki
                        <ArrowRight size={16} />
                      </button>
                    </>
                  )}

                  <button
                    className="btn btn-danger"
                    onClick={async () => {
                      if (!confirm('Bu skorlama çalışmasını silmek istediğinize emin misiniz?'))
                        return
                      try {
                        await scoringApi.deleteRun(run.id, activeWorkspace?.id)
                        fetchRuns()
                        if (selectedRun === run.id) {
                          setSelectedRun(null)
                          setScores([])
                        }
                      } catch (err) {
                        console.error('Delete error:', err)
                      }
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {selectedRun &&
        (() => {
          const selectedRunObj = runs.find((r) => r.id === selectedRun)
          const isV3Run = selectedRunObj?.algorithm_version === 'v3'
          return (
            <div className="scores-section">
              <div className="scores-heading">
                <div>
                  <h2>Skorlar - Çalışma #{selectedRun}</h2>
                  <p>
                    {totalScored > 0
                      ? `${scores.length}/${totalScored} kelime gösteriliyor`
                      : 'Skor kaydı bulunamadı'}
                  </p>
                </div>
              </div>
              <div
                className={`table-container ${isLoadingScores && scores.length > 0 ? 'table-sorting' : ''}`}
              >
                <table className="table">
                  <thead>
                    <tr>
                      <th>Anahtar Kelime</th>
                      {!isV3Run && (
                        <>
                          <th>{renderSortableHeader('ads_score', 'ADS Skor')}</th>
                          <th>{renderSortableHeader('seo_score', 'SEO Skor')}</th>
                          <th>{renderSortableHeader('social_score', 'SOCIAL Skor')}</th>
                          <th>{renderSortableHeader('ads_rank', 'ADS Sıra')}</th>
                          <th>{renderSortableHeader('seo_rank', 'SEO Sıra')}</th>
                          <th>{renderSortableHeader('social_rank', 'SOCIAL Sıra')}</th>
                        </>
                      )}
                      {isV3Run && (
                        <>
                          <th>Aile</th>
                          <th>{renderSortableHeader('ads_score', 'ADS')}</th>
                          <th>{renderSortableHeader('seo_score', 'SEO')}</th>
                          <th>{renderSortableHeader('social_score', 'SOCIAL')}</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {isLoadingScores && scores.length === 0 ? (
                      <tr>
                        <td colSpan={isV3Run ? 5 : 7} className="scores-empty-cell">
                          Skorlar yükleniyor...
                        </td>
                      </tr>
                    ) : scores.length === 0 ? (
                      <tr>
                        <td colSpan={isV3Run ? 5 : 7} className="scores-empty-cell">
                          Skor kaydı bulunamadı.
                        </td>
                      </tr>
                    ) : (
                      scores.map((score) => (
                        <tr key={score.keyword_id}>
                          <td>
                            <strong>{score.keyword}</strong>
                            {(score.exclusions || []).map((ex, exIdx) => (
                              <span
                                key={exIdx}
                                className={`scx-excluded-badge is-${ex.reason}`}
                                title={
                                  ex.reason === 'topic'
                                    ? `SOCIAL içerik üretiminde engelli${ex.matched_term ? ` (${ex.matched_term})` : ''}`
                                    : `${ex.channels.join('/')} kanalında engelli${ex.matched_term ? ` (${ex.matched_term})` : ''}`
                                }
                              >
                                {ex.reason === 'competitor'
                                  ? `Rakip: ${ex.channels.join('/')}`
                                  : ex.reason === 'topic'
                                    ? 'Konu: SOCIAL'
                                    : 'Fiyat: SEO'}
                              </span>
                            ))}
                          </td>
                          {!isV3Run && (
                            <>
                              <td>{formatScore(score.ads_score)}</td>
                              <td>{formatScore(score.seo_score)}</td>
                              <td>{formatScore(score.social_score)}</td>
                              <td>
                                {score.ads_rank && (
                                  <span className={score.ads_rank <= 3 ? 'top-rank' : ''}>
                                    {score.ads_rank <= 3 && <Award size={14} />}#{score.ads_rank}
                                  </span>
                                )}
                              </td>
                              <td>
                                {score.seo_rank && (
                                  <span className={score.seo_rank <= 3 ? 'top-rank' : ''}>
                                    {score.seo_rank <= 3 && <Award size={14} />}#{score.seo_rank}
                                  </span>
                                )}
                              </td>
                              <td>
                                {score.social_rank && (
                                  <span className={score.social_rank <= 3 ? 'top-rank' : ''}>
                                    {score.social_rank <= 3 && <Award size={14} />}#
                                    {score.social_rank}
                                  </span>
                                )}
                              </td>
                            </>
                          )}
                          {isV3Run && (
                            <>
                              <td>
                                <div className="v3-family">
                                  <strong>{score.family_name || score.family_id || '—'}</strong>
                                  {score.family_name && score.family_id && (
                                    <span>{score.family_id}</span>
                                  )}
                                </div>
                              </td>
                              <td>{renderV3Channel(score, 'ads')}</td>
                              <td>{renderV3Channel(score, 'seo')}</td>
                              <td>{renderV3Channel(score, 'social')}</td>
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
        })()}

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal glass-card" onClick={(e) => e.stopPropagation()}>
            <h2>Yeni Skorlama Çalışması</h2>

            <div className="form-group">
              <label>Skorlanacak Kanallar</label>
              <div className="channel-toggle-grid">
                {[
                  ['enable_ads', 'ADS'],
                  ['enable_seo', 'SEO'],
                  ['enable_social', 'SOCIAL'],
                ].map(([key, label]) => (
                  <label
                    key={key}
                    className={`channel-toggle ${newRun[key as 'enable_ads' | 'enable_seo' | 'enable_social'] ? 'active' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={newRun[key as 'enable_ads' | 'enable_seo' | 'enable_social']}
                      onChange={(e) =>
                        setNewRun({
                          ...newRun,
                          [key]: e.target.checked,
                        })
                      }
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label>Çalışma Adı</label>
              <input
                className="input"
                value={newRun.run_name}
                onChange={(e) => setNewRun({ ...newRun, run_name: e.target.value })}
                placeholder="Mart 2026 Skorlama"
              />
            </div>

            <div className="form-group">
              <label>ADS Kapasitesi</label>
              <input
                type="number"
                className="input"
                title="ADS için İstenen Keyword Sayısı"
                placeholder="20"
                value={newRun.ads_capacity}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    ads_capacity: parseInt(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>SEO Kapasitesi</label>
              <input
                type="number"
                className="input"
                title="SEO için İstenen Keyword Sayısı"
                placeholder="30"
                value={newRun.seo_capacity}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    seo_capacity: parseInt(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>SOCIAL Kapasitesi</label>
              <input
                type="number"
                className="input"
                title="SOCIAL için İstenen Keyword Sayısı"
                placeholder="25"
                value={newRun.social_capacity}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    social_capacity: parseInt(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>Varsayılan İlgi Katsayısı (0.1 - 3.0)</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="3"
                className="input"
                title="İlgi skoru ile kanal skorunu birleştirmede kullanılır"
                placeholder="1.0"
                value={newRun.default_relevance_coefficient ?? 1.0}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    default_relevance_coefficient: parseFloat(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>Keyword Kaynağı</label>
              <select
                className="input"
                title="Keyword Kaynağı"
                value={newRun.keyword_source_filter ?? ''}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    keyword_source_filter:
                      e.target.value === '' ? null : (e.target.value as 'csv' | 'google_ads_api'),
                  })
                }
              >
                <option value="">Tüm Kaynaklar (CSV + Google Ads)</option>
                <option value="csv">Sadece CSV</option>
                <option value="google_ads_api">Sadece Google Ads API</option>
              </select>
            </div>

            <div className="form-group">
              <label>Algoritma Sürümü</label>
              <div className="input" aria-label="Analiz motoru">
                v3 — kilitli üretim motoru
              </div>
              <p className="form-hint">
                ADS/SEO/SOCIAL motorlarını tek koşuda çalıştırır ve kanal atamasını otomatik yapar.
              </p>
              {!engineV3Enabled && (
                <p className="form-hint error-text">
                  Motor v3 sunucuda kapalı veya doğrulanamadı; çalışma oluşturulamaz.
                </p>
              )}
            </div>

            {errorMsg && <div className="modal-error">{errorMsg}</div>}
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setShowModal(false)}>
                İptal
              </button>
              <button className="btn btn-primary" onClick={createRun} disabled={!engineV3Enabled}>
                Oluştur
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
