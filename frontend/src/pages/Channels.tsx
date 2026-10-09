import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import {
  ChevronDown,
  ChevronUp,
  MapPin,
  RefreshCw,
  ArrowRight,
  AlertCircle,
  Zap,
} from 'lucide-react'
import {
  scoringApi,
  channelsApi,
  brandProfileApi,
  PoolFreshness,
  poolFreshnessLabel,
  LocationAuditResponse,
} from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import type { ScoringRun } from '../types/models'
import {
  useTaskPolling,
  getStoredTaskId,
  getWorkspaceTaskKey,
  useScopedTaskId,
} from '../hooks/useTaskPolling'
import TaskProgress from '../components/TaskProgress'
import { locationModeLabel, locationReasonLabel } from '../services/locationPolicy'
import './Channels.css'

const LOCATION_AUDIT_PAGE_SIZE = 50
const LOCATION_AUDIT_ELIGIBLE_STATUSES = new Set(['channel_assigned', 'completed'])

interface PoolKeyword {
  keyword_id: number
  keyword: string
  rank: number
  final_rank?: number
  is_strategic: boolean
  volume?: number
  score?: number
  relevance_score?: number | null
  adjusted_score?: number | null
  algorithm_rank?: number | null
  pool_class?: string | null
}

type ChannelCapacities = { [key: string]: number | undefined }
type ExpansionSummary = {
  capacity?: number
  final_count_after_expansion?: number
  final_count_before_expansion?: number
  brand_excluded?: number
  prefilter_eliminated?: number
  rounds_run?: number
  stop_reason?: string
}
type ExpansionSummaries = Record<string, ExpansionSummary | undefined>

function normalizeCoefficient(value: number): number {
  if (Number.isNaN(value)) return 1.0
  return Math.min(3, Math.max(0.1, value))
}

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    return error.message
  }
  return 'Beklenmeyen hata'
}

export default function Channels() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)

  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const assignTaskStorageKey = getWorkspaceTaskKey(
    'channel_assign',
    activeWorkspace?.id,
    selectedRun
  )
  const [pools, setPools] = useState<{ [key: string]: PoolKeyword[] }>({})
  const [capacities, setCapacities] = useState<ChannelCapacities>({})
  const [unfilledCounts, setUnfilledCounts] = useState<{ [key: string]: number }>({})
  const [poolFreshness, setPoolFreshness] = useState<PoolFreshness | null>(null)
  const [loading, setLoading] = useState(false)
  const poolRequestRef = useRef(0)
  const [assigning, setAssigning] = useState(false)
  const [assignTaskId, setAssignTaskId] = useScopedTaskId(assignTaskStorageKey)
  const [error, setError] = useState<string>('')
  const [info, setInfo] = useState<string>('')
  const [relevanceCoefficient, setRelevanceCoefficient] = useState<number>(1.0)

  // Lokasyon denetimi (plan_v3_lokasyon_filtresi.md §5.6) — "bu keyword neden
  // havuzda değil?" sorusu için TAMAMLANMIŞ bir run'ın mühürlü lokasyon
  // kararlarını gösteren, isteğe bağlı açılır panel. Yeni bir sayfa DEĞİL —
  // mevcut kanal sonuçları yüzeyine gömülü.
  const [locationAuditOpen, setLocationAuditOpen] = useState(false)
  const [locationAuditOnlyExcluded, setLocationAuditOnlyExcluded] = useState(false)
  const [locationAuditOffset, setLocationAuditOffset] = useState(0)
  const [locationAuditData, setLocationAuditData] = useState<LocationAuditResponse | null>(null)
  const [locationAuditLoading, setLocationAuditLoading] = useState(false)
  const [locationAuditError, setLocationAuditError] = useState('')

  const requestedRunId = useMemo(() => {
    const raw = searchParams.get('run_id')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isNaN(parsed) || parsed <= 0 ? null : parsed
  }, [searchParams])

  const selectedRunData = useMemo(
    () => runs.find((run) => run.id === selectedRun) || null,
    [runs, selectedRun]
  )

  const totalPoolKeywords = useMemo(
    () => Object.values(pools).reduce((sum, list) => sum + list.length, 0),
    [pools]
  )

  const hasExistingPools = totalPoolKeywords > 0

  // Task polling for channel assignment
  const assignPolling = useTaskPolling(
    assignTaskId,
    assignTaskStorageKey,
    3000,
    activeWorkspace?.id,
    selectedRun
  )

  const expansionSummaries = useMemo(() => {
    const resultData = assignPolling.resultData as
      | { current_message?: string; steps?: { expansion_rounds?: ExpansionSummaries } }
      | undefined
    return resultData?.steps?.expansion_rounds || {}
  }, [assignPolling.resultData])

  const assignmentMessage = useMemo(() => {
    const resultData = assignPolling.resultData as { current_message?: string } | undefined
    return resultData?.current_message
  }, [assignPolling.resultData])

  // URL'deki task_id yalnız yönlendirildiği run için ve yalnız bir kez uygulanır;
  // başka bir run'ın anahtarı altına asla yazılmaz.
  const appliedUrlTaskRef = useRef<string | null>(null)
  useEffect(() => {
    const taskIdFromUrl = searchParams.get('task_id')
    const urlScope = taskIdFromUrl ? `${requestedRunId}:${taskIdFromUrl}` : null
    if (
      taskIdFromUrl &&
      requestedRunId !== null &&
      requestedRunId === selectedRun &&
      appliedUrlTaskRef.current !== urlScope
    ) {
      appliedUrlTaskRef.current = urlScope
      setAssignTaskId(taskIdFromUrl)
    } else {
      setAssignTaskId(getStoredTaskId(assignTaskStorageKey))
    }
  }, [searchParams, requestedRunId, selectedRun, assignTaskStorageKey, setAssignTaskId])

  const fetchRuns = useCallback(async () => {
    if (!activeWorkspace?.id) {
      setRuns([])
      setSelectedRun(null)
      setPools({})
      setCapacities({})
      return
    }
    try {
      const res = await scoringApi.listRuns({ brand_profile_id: activeWorkspace.id })
      let data = (Array.isArray(res.data) ? res.data : []) as ScoringRun[]
      const eligibleStatuses = [
        'scored',
        'relevance_computed',
        'channel_assigning',
        'channel_assigned',
        'completed',
      ]
      data = data.filter((r) => eligibleStatuses.includes(r.status))
      setRuns(data)
    } catch (fetchError) {
      setError(`Çalışmalar yüklenemedi: ${extractErrorMessage(fetchError)}`)
    }
  }, [activeWorkspace?.id])

  const fetchPools = useCallback(
    async (runId: number) => {
      if (!activeWorkspace?.id) return
      // Hızlı run değişiminde eski yanıt yeni havuzun üzerine yazılmaz
      const requestId = ++poolRequestRef.current
      setLoading(true)
      try {
        const poolsRes = await channelsApi.getPools(runId, activeWorkspace.id)
        if (poolRequestRef.current !== requestId) return
        const responseData = poolsRes.data as {
          channels?: Record<string, PoolKeyword[]>
          capacities?: Record<string, number>
          unfilled_counts?: Record<string, number>
        } & Partial<PoolFreshness>
        setPools(responseData.channels || {})
        setCapacities(responseData.capacities || {})
        setUnfilledCounts(responseData.unfilled_counts || {})
        // Plan v13: havuz bayatlık sinyali — band + "yeniden ata" CTA'sı
        const freshData = responseData
        setPoolFreshness(
          typeof freshData.channel_pool_stale === 'boolean'
            ? {
                channel_pool_stale: freshData.channel_pool_stale,
                policy_stale: Boolean(freshData.policy_stale),
                relevance_stale: Boolean(freshData.relevance_stale),
                // v2.1 Faz C (Codex): alan kopyalanmazsa banner koşulu asla çalışmaz
                strategy_stale: Boolean(freshData.strategy_stale),
              }
            : null
        )
        setError('')
      } catch (fetchError) {
        if (poolRequestRef.current !== requestId) return
        setPools({})
        setCapacities({})
        setUnfilledCounts({})
        setPoolFreshness(null)
        setError(`Havuzlar alınamadı: ${extractErrorMessage(fetchError)}`)
      } finally {
        if (poolRequestRef.current === requestId) setLoading(false)
      }
    },
    [activeWorkspace?.id]
  )

  const fetchLocationAudit = useCallback(
    async (offset: number, onlyExcluded: boolean) => {
      if (!selectedRun || !activeWorkspace?.id) return
      setLocationAuditLoading(true)
      setLocationAuditError('')
      try {
        const res = await brandProfileApi.getLocationAudit(selectedRun, {
          brand_profile_id: activeWorkspace.id,
          limit: LOCATION_AUDIT_PAGE_SIZE,
          offset,
          only_excluded: onlyExcluded,
        })
        setLocationAuditData(res.data)
        setLocationAuditOffset(offset)
      } catch (fetchError) {
        setLocationAuditData(null)
        setLocationAuditError(extractErrorMessage(fetchError))
      } finally {
        setLocationAuditLoading(false)
      }
    },
    [selectedRun, activeWorkspace?.id]
  )

  // Run değişince (veya kapanınca) denetim paneli sıfırlanır — bir run'ın
  // sonucu başka bir run'da görünmesin.
  useEffect(() => {
    setLocationAuditOpen(false)
    setLocationAuditData(null)
    setLocationAuditError('')
    setLocationAuditOffset(0)
    setLocationAuditOnlyExcluded(false)
  }, [selectedRun])

  const locationAuditEligible = Boolean(
    selectedRunData && LOCATION_AUDIT_ELIGIBLE_STATUSES.has(selectedRunData.status)
  )

  const toggleLocationAudit = () => {
    const next = !locationAuditOpen
    setLocationAuditOpen(next)
    if (next && !locationAuditData && !locationAuditLoading) {
      void fetchLocationAudit(0, locationAuditOnlyExcluded)
    }
  }

  const toggleLocationAuditOnlyExcluded = () => {
    const next = !locationAuditOnlyExcluded
    setLocationAuditOnlyExcluded(next)
    void fetchLocationAudit(0, next)
  }

  useEffect(() => {
    void fetchRuns()
  }, [fetchRuns])

  useEffect(() => {
    if (!requestedRunId || runs.length === 0) return
    if (!runs.some((run) => run.id === requestedRunId)) return
    setSelectedRun(requestedRunId)
  }, [requestedRunId, runs])

  useEffect(() => {
    if (!selectedRunData) return
    const defaultCoef = Number(selectedRunData.default_relevance_coefficient ?? 1)
    setRelevanceCoefficient(normalizeCoefficient(defaultCoef))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunData?.id])

  useEffect(() => {
    if (!selectedRun) {
      setPools({})
      return
    }
    void fetchPools(selectedRun)
  }, [selectedRun, fetchPools])

  // When assignment task completes, refresh pools and runs
  useEffect(() => {
    if (assignPolling.isCompleted && selectedRun) {
      void fetchRuns()
      void fetchPools(selectedRun)
      setAssigning(false)
      setInfo('Kanal ataması tamamlandı. Havuzlar yenilendi.')
      setError('')
    }
    if (assignPolling.isFailed) {
      void fetchRuns()
      setAssigning(false)
      setError(assignPolling.errorMessage || 'Kanal ataması başarısız.')
    }
  }, [
    assignPolling.isCompleted,
    assignPolling.isFailed,
    assignPolling.errorMessage,
    selectedRun,
    fetchRuns,
    fetchPools,
  ])

  const startAssignment = (runId: number) => {
    void runAssignment(runId)
  }

  const runAssignment = async (runId: number) => {
    if (!activeWorkspace?.id) {
      setError('Önce Marka Çalışması seçin')
      return
    }
    if (hasExistingPools) {
      const ok = window.confirm(
        'Mevcut içerikler güncel olmayacak şekilde işaretlenecek (stale). Devam etmek istiyor musunuz?'
      )
      if (!ok) return
    }

    setAssigning(true)
    setError('')
    setInfo('')

    try {
      const coefficient = normalizeCoefficient(relevanceCoefficient)
      const res = await channelsApi.assign(runId, coefficient, activeWorkspace.id)
      const taskId = res.data.task_id
      setAssignTaskId(taskId)
      setInfo(
        `Atama başlatıldı. Kullanılan etkili ilgi katsayısı: ${Number(res.data.effective_relevance_coefficient ?? coefficient).toFixed(2)}`
      )
    } catch (assignError) {
      setAssigning(false)
      setError(extractErrorMessage(assignError))
    }
  }

  const canAssign = (run: ScoringRun): boolean => {
    if (run.status === 'channel_assigning') return false
    if (run.algorithm_version === 'v3') {
      return (
        run.status === 'scored' || run.status === 'channel_assigned' || run.status === 'completed'
      )
    }
    if (run.skip_relevance) return run.status === 'scored'
    return (
      run.status === 'relevance_computed' ||
      run.status === 'channel_assigned' ||
      run.status === 'completed'
    )
  }

  const getButtonLabel = (run: ScoringRun): string => {
    if (run.status === 'channel_assigning' || assigning || assignPolling.isActive) {
      return 'Atama Sürüyor...'
    }
    if (!canAssign(run) && run.status === 'scored') return 'Önce İlgi Skoru Hesaplayın'
    return 'Kanal Ataması Başlat'
  }

  const channels = ['ADS', 'SEO', 'SOCIAL']
  const channelColors = {
    ADS: 'var(--ch-ads)',
    SEO: 'var(--ch-seo)',
    SOCIAL: 'var(--ch-social)',
  }

  const isAssignActive = assigning || assignPolling.isActive

  const poolCountLabel = (channel: string): string => {
    const count = (pools[channel] || []).length
    const capacity = capacities[channel]
    return capacity ? `${count} / ${capacity} kelime` : `${count} kelime`
  }
  const stopReasonText = (reason?: string): string => {
    switch (reason) {
      case 'capacity_reached':
        return 'Hedef kapasite yakalandı'
      case 'max_rounds_reached':
        return 'Maksimum ek tur tamamlandı'
      case 'no_more_candidates':
        return 'Aday kalmadı'
      case 'budget_reached':
        return 'AI bütçe sınırı'
      case 'channel_disabled':
        return 'Kanal kapalı'
      default:
        return reason || 'Rapor yok'
    }
  }

  const emptyPoolText = (channel: string): string => {
    const summary = expansionSummaries[channel]
    if (summary && (summary.rounds_run || 0) > 0) {
      return 'Havuz boş. Marka dışlama ve kanal filtreleri sonrası uygun keyword bulunamadı. Ek aday turları da yeterli sonuç üretmedi.'
    }
    return 'Havuz boş. Marka dışlama ve kanal filtreleri sonrası uygun keyword bulunamadı.'
  }

  return (
    <div className="channels-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Kanal Ataması</h1>
          <p>Yapay zeka destekli kanal atama paneli</p>
        </div>
        <button className="btn btn-secondary" onClick={() => void fetchRuns()}>
          <RefreshCw size={16} />
          Çalışmaları Yenile
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

      {error && <div className="channels-alert channels-alert-error">{error}</div>}
      {info && <div className="channels-alert channels-alert-info">{info}</div>}

      {poolFreshness?.channel_pool_stale && (
        <div className="channels-alert channels-alert-error" data-testid="pool-stale-banner">
          <AlertCircle size={16} /> {poolFreshnessLabel(poolFreshness)} — bu havuz güncel değil;
          içerik üretimi ve export engellendi. Aşağıdan kanal atamasını yeniden çalıştırın.
        </div>
      )}

      <div className="run-selection glass-card">
        <h3>Tamamlanan Skorlama Çalışmasını Seç</h3>
        <div className="run-buttons">
          {runs.length === 0 ? (
            <p className="empty-text">Tamamlanmış skorlama çalışması bulunamadı.</p>
          ) : (
            runs.map((run) => (
              <button
                key={run.id}
                className={`btn ${selectedRun === run.id ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setSelectedRun(run.id)}
              >
                {run.run_name || `Çalışma #${run.id}`}
              </button>
            ))
          )}
        </div>

        {selectedRun && (
          <div className="channels-controls">
            <div className="channels-coef-input">
              <label className="brand-label">İlgi Katsayısı (0.1 - 3.0)</label>
              <input
                className="input"
                type="number"
                step="0.1"
                min="0.1"
                max="3"
                title="İlgi skoru ile kanal skorunu birleştirmede kullanılır"
                placeholder="1.0"
                value={relevanceCoefficient}
                onChange={(e) =>
                  setRelevanceCoefficient(normalizeCoefficient(Number(e.target.value)))
                }
              />
            </div>
            {selectedRunData &&
              !canAssign(selectedRunData) &&
              selectedRunData.status === 'scored' && (
                <div className="disabled-hint">
                  <AlertCircle size={14} />
                  Önce İlgi Skoru sayfasından ilgi skoru hesaplayın
                </div>
              )}
            {selectedRunData && (
              <button
                className="btn btn-success"
                onClick={() => startAssignment(selectedRun)}
                disabled={isAssignActive || !canAssign(selectedRunData)}
              >
                <Zap size={18} />
                {getButtonLabel(selectedRunData)}
              </button>
            )}
          </div>
        )}
      </div>

      {assignTaskId && assignPolling.status && (
        <div className="glass-card" style={{ marginTop: 'var(--space-md)' }}>
          <TaskProgress
            taskId={assignTaskId}
            status={assignPolling.status.status}
            progress={assignPolling.progress}
            message={assignmentMessage}
            errorMessage={assignPolling.errorMessage}
          />
        </div>
      )}

      {selectedRun && !loading && (
        <div className="pools-grid">
          {channels.map((channel) => (
            <div key={channel} className="pool-card glass-card">
              <div
                className="pool-header"
                style={{
                  borderColor: channelColors[channel as keyof typeof channelColors],
                }}
              >
                <h3>{channel}</h3>
                <span className="pool-count">
                  {poolCountLabel(channel)}
                  {selectedRunData?.algorithm_version === 'v3' &&
                    unfilledCounts[channel] !== undefined && (
                      <span style={{ marginLeft: '0.4rem', opacity: 0.85, fontSize: '0.8rem' }}>
                        (Eksik: {unfilledCounts[channel]})
                      </span>
                    )}
                </span>
              </div>
              <div className="pool-list">
                {(pools[channel] || []).length === 0 ? (
                  <p className="empty-text">{emptyPoolText(channel)}</p>
                ) : (
                  (pools[channel] || []).map((kw, idx) => (
                    <div key={kw.keyword_id} className="pool-item">
                      <span className="pool-rank">#{kw.final_rank || kw.rank || idx + 1}</span>
                      <span className="pool-keyword">{kw.keyword}</span>
                      {selectedRunData?.algorithm_version === 'v3' && (
                        <span
                          style={{
                            marginLeft: 'auto',
                            display: 'flex',
                            gap: '0.35rem',
                            alignItems: 'center',
                            fontSize: '0.75rem',
                          }}
                        >
                          {kw.algorithm_rank && (
                            <span className="badge badge-secondary" title="Algoritma Sırası">
                              alg:#{kw.algorithm_rank}
                            </span>
                          )}
                          {kw.pool_class && (
                            <span className="badge badge-info" title="Havuz Sınıfı">
                              {kw.pool_class}
                            </span>
                          )}
                        </span>
                      )}
                    </div>
                  ))
                )}
              </div>
              {selectedRunData?.algorithm_version === 'v3' && (
                <div className="pool-debug">
                  <span>
                    Kapasite: {capacities[channel] || 0} | Havuz: {(pools[channel] || []).length} |
                    Eksik:{' '}
                    {unfilledCounts[channel] ??
                      Math.max(0, (capacities[channel] || 0) - (pools[channel] || []).length)}
                  </span>
                </div>
              )}
              {expansionSummaries[channel] && (
                <div className="pool-debug">
                  <span>
                    {expansionSummaries[channel]?.capacity || capacities[channel] || 0} hedeflendi,{' '}
                    {(pools[channel] || []).length} bulundu
                  </span>
                  <span>{expansionSummaries[channel]?.brand_excluded || 0} marka dışı elendi</span>
                  <span>
                    {expansionSummaries[channel]?.prefilter_eliminated || 0} kanal filtresinde
                    elendi
                  </span>
                  <span>{expansionSummaries[channel]?.rounds_run || 0} ek aday turu çalıştı</span>
                  <span>
                    Durdurma nedeni: {stopReasonText(expansionSummaries[channel]?.stop_reason)}
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {loading && (
        <div className="loading-state">
          <RefreshCw size={32} className="animate-spin" />
          <p>Havuzlar yükleniyor...</p>
        </div>
      )}

      {locationAuditEligible && (
        <div className="glass-card location-audit" style={{ marginTop: 'var(--space-md)' }}>
          <button
            type="button"
            className="location-audit-toggle"
            onClick={toggleLocationAudit}
            aria-expanded={locationAuditOpen}
          >
            <MapPin size={16} />
            <span>Lokasyon Denetimi — bu keyword neden havuzda değil?</span>
            {locationAuditOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>

          {locationAuditOpen && (
            <div className="location-audit-body">
              {locationAuditLoading && !locationAuditData && (
                <p className="empty-text">Lokasyon denetimi yükleniyor…</p>
              )}
              {locationAuditError && (
                <div className="channels-alert channels-alert-error">{locationAuditError}</div>
              )}
              {locationAuditData && (
                <>
                  <p className="location-audit-summary">
                    Mod: <b>{locationModeLabel(locationAuditData.mode)}</b> · Toplam:{' '}
                    {locationAuditData.total_rows} · Tutulan: {locationAuditData.kept_count} ·
                    Elenen: {locationAuditData.excluded_count}
                  </p>
                  <label className="location-audit-filter">
                    <input
                      type="checkbox"
                      checked={locationAuditOnlyExcluded}
                      onChange={toggleLocationAuditOnlyExcluded}
                    />
                    Yalnız lokasyon nedeniyle elenenleri göster
                  </label>

                  {locationAuditData.rows.length === 0 ? (
                    <p className="empty-text">
                      {locationAuditOnlyExcluded
                        ? 'Bu run’da lokasyon nedeniyle elenen keyword yok.'
                        : 'Gösterilecek satır yok.'}
                    </p>
                  ) : (
                    <div className="location-audit-table-wrap">
                      <table className="location-audit-table">
                        <thead>
                          <tr>
                            <th>Keyword</th>
                            <th>Karar</th>
                            <th>Neden</th>
                            <th>Eşleşen şehir</th>
                            <th>Muafiyet</th>
                          </tr>
                        </thead>
                        <tbody>
                          {locationAuditData.rows.map((row) => (
                            <tr key={row.keyword_id}>
                              <td>{row.keyword}</td>
                              <td>
                                <span
                                  className={`badge ${row.is_kept ? 'badge-success' : 'badge-danger'}`}
                                >
                                  {row.is_kept ? 'Tutuldu' : 'Elendi'}
                                </span>
                              </td>
                              <td>{locationReasonLabel(row.reason_code) || '—'}</td>
                              <td>{row.matched_city || '—'}</td>
                              <td>{row.matched_exempt_term || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <div className="location-audit-pagination">
                    <button
                      type="button"
                      className="btn btn-secondary"
                      disabled={locationAuditLoading || locationAuditOffset === 0}
                      onClick={() =>
                        void fetchLocationAudit(
                          Math.max(0, locationAuditOffset - LOCATION_AUDIT_PAGE_SIZE),
                          locationAuditOnlyExcluded
                        )
                      }
                    >
                      Önceki
                    </button>
                    <span>
                      {locationAuditOffset + 1}–
                      {locationAuditOffset + locationAuditData.rows.length} /{' '}
                      {locationAuditData.total_rows}
                    </span>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      disabled={
                        locationAuditLoading ||
                        locationAuditOffset + locationAuditData.rows.length >=
                          locationAuditData.total_rows
                      }
                      onClick={() =>
                        void fetchLocationAudit(
                          locationAuditOffset + LOCATION_AUDIT_PAGE_SIZE,
                          locationAuditOnlyExcluded
                        )
                      }
                    >
                      Sonraki
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {selectedRun && hasExistingPools && (
        <div className="channels-next">
          <button
            className="btn btn-primary"
            onClick={() => navigate(`/generation?run_id=${selectedRun}`)}
          >
            Sonraki: İçerik Üretimi
            <ArrowRight size={16} />
          </button>
        </div>
      )}
    </div>
  )
}
