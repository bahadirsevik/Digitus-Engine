import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, CheckCircle2, ChevronDown, RefreshCw, X } from 'lucide-react'
import { channelsApi, scoringApi, tasksApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import type { ScoringRun } from '../types/models'
import TaskProgress from './TaskProgress'
import ExportMenu from './ExportMenu'
import SeoGeoPanel from './generation/SeoGeoPanel'
import AdsPanel from './generation/AdsPanel'
import SocialPanel from './generation/SocialPanel'
import { useBriefKeywordSelection } from './generation/socialBrief/useBriefKeywordSelection'
import { getStoredTaskId, getWorkspaceTaskKey, useTaskPolling } from '../hooks/useTaskPolling'
import '../pages/Channels.css'

type ChannelName = 'ADS' | 'SEO' | 'SOCIAL'

export interface PoolKeyword {
  id: number
  keyword_id: number
  keyword: string
  rank?: number
  final_rank?: number
  is_strategic?: boolean
  pool_label?: string | null
  volume?: number
  score?: number
  adjusted_score?: number | null
}

interface RunTask {
  task_id: string
  task_type?: string | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress?: number
  error_message?: string | null
}

const CHANNEL_LABELS: Record<ChannelName, string> = {
  ADS: 'ADS',
  SEO: 'SEO+GEO',
  SOCIAL: 'Social',
}

// Tasarım: havuz tablosu kapalıyken gösterilen satır sayısı
const COLLAPSED_COUNT = 8

function isActiveTask(task?: RunTask | null) {
  return task?.status === 'pending' || task?.status === 'running'
}

function formatNumber(value?: number | null) {
  if (value == null) return '-'
  return Number(value).toLocaleString('tr-TR')
}

export default function ChannelWorkspaceView({ channel }: { channel: ChannelName }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const assignTaskStorageKey = getWorkspaceTaskKey('channel_assign', activeWorkspace?.id)

  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [pool, setPool] = useState<PoolKeyword[]>([])
  const [loading, setLoading] = useState(false)
  const [poolError, setPoolError] = useState<string | null>(null)
  const poolRequestRef = useRef(0)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [assignTaskId, setAssignTaskId] = useState<string | null>(
    getStoredTaskId(assignTaskStorageKey)
  )
  const [staleNotice, setStaleNotice] = useState(false)
  const [poolExpanded, setPoolExpanded] = useState(false)

  const assignPolling = useTaskPolling(
    assignTaskId,
    assignTaskStorageKey,
    3000,
    activeWorkspace?.id
  )

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) || null,
    [runs, selectedRunId]
  )

  // Sosyal brief kelime seçimi: üst havuz kartları + brief formu AYNI durumu kullanır.
  // Kapsam (workspace+run) değişince seçim render anında boşalır.
  const briefSelection = useBriefKeywordSelection(
    `${activeWorkspace?.id ?? 'none'}:${selectedRunId ?? 'none'}`
  )

  const fetchRuns = useCallback(async () => {
    if (!activeWorkspace?.id) {
      setRuns([])
      setSelectedRunId(null)
      setPool([])
      return
    }

    try {
      const res = await scoringApi.listRuns({ brand_profile_id: activeWorkspace.id })
      const allRuns = (Array.isArray(res.data) ? res.data : []) as ScoringRun[]
      const visibleRuns = allRuns.filter((run) =>
        ['channel_assigning', 'channel_assigned', 'completed'].includes(run.status)
      )
      setRuns(visibleRuns)

      const requested = Number(searchParams.get('run_id') || '')
      const requestedRun = visibleRuns.find((run) => run.id === requested)
      const defaultRun =
        requestedRun ||
        visibleRuns.find((run) => ['channel_assigned', 'completed'].includes(run.status)) ||
        visibleRuns[0]
      setSelectedRunId((current) =>
        current && visibleRuns.some((run) => run.id === current) ? current : defaultRun?.id || null
      )
      if (requested && !requestedRun) {
        setInfo('Bu analiz aktif marka çalışmasına ait değil veya kanal havuzu hazır değil.')
      }
    } catch {
      setError('Analizler yüklenemedi.')
    }
  }, [activeWorkspace?.id, searchParams])

  const fetchPool = useCallback(
    async (runId: number) => {
      if (!activeWorkspace?.id) return
      // Hızlı run/workspace değişiminde eski yanıt yeni havuzun üzerine yazılmaz;
      // yükleme durumu yalnız EN SON isteğin sonunda kapanır.
      const requestId = ++poolRequestRef.current
      setLoading(true)
      setPoolError(null)
      try {
        const res = await channelsApi.getPools(runId, activeWorkspace.id)
        if (poolRequestRef.current !== requestId) return
        setPool((res.data.channels?.[channel] || []) as PoolKeyword[])
        setError('')
      } catch {
        if (poolRequestRef.current !== requestId) return
        setPool([])
        setPoolError('Kanal havuzu alınamadı.')
      } finally {
        if (poolRequestRef.current === requestId) setLoading(false)
      }
    },
    [activeWorkspace?.id, channel]
  )

  const discoverAssignmentTask = useCallback(
    async (runId: number) => {
      if (!activeWorkspace?.id) return
      const res = await tasksApi.listByRun(runId, activeWorkspace.id)
      const tasks = (Array.isArray(res.data) ? res.data : []) as RunTask[]
      const task = tasks.find(
        (item) => item.task_type === 'channel_assignment' && isActiveTask(item)
      )
      if (task) setAssignTaskId(task.task_id)
    },
    [activeWorkspace?.id]
  )

  useEffect(() => {
    void fetchRuns()
  }, [fetchRuns])

  useEffect(() => {
    setAssignTaskId(getStoredTaskId(assignTaskStorageKey))
  }, [assignTaskStorageKey])

  // Workspace değişince önceki workspace'in havuzu ekranda kalmaz
  useEffect(() => {
    poolRequestRef.current += 1
    setPool([])
    setPoolError(null)
    setLoading(false)
  }, [activeWorkspace?.id])

  useEffect(() => {
    if (!selectedRunId) return
    setSearchParams({ run_id: String(selectedRunId) })
    setPoolExpanded(false)
    void fetchPool(selectedRunId)
    void discoverAssignmentTask(selectedRunId)
  }, [selectedRunId, fetchPool, discoverAssignmentTask, setSearchParams])

  useEffect(() => {
    if (assignPolling.isCompleted && selectedRunId) {
      void fetchRuns()
      void fetchPool(selectedRunId)
    }
  }, [assignPolling.isCompleted, selectedRunId, fetchRuns, fetchPool])

  const handleRemove = async (item: PoolKeyword) => {
    if (!activeWorkspace?.id || !selectedRunId) return
    const ok = window.confirm(
      'Bu kelime yalnızca bu çalışmanın kanal havuzundan çıkarılır. Yeniden kanal ataması yapılırsa geri gelebilir. Kalıcı dışlama için marka profilindeki dışlanacak temaları kullanın.'
    )
    if (!ok) return

    try {
      await channelsApi.removePoolItem(selectedRunId, item.id, activeWorkspace.id)
      setPool((current) => current.filter((row) => row.id !== item.id))
      briefSelection.remove(item.keyword_id)
      setStaleNotice(true)
      setError('')
    } catch (removeError) {
      const err = removeError as { message?: string }
      setError(err.message || 'Kelime havuzdan kaldırılamadı.')
    }
  }

  const canEditPool =
    selectedRun?.status === 'channel_assigned' || selectedRun?.status === 'completed'

  const maxScore = useMemo(
    () => Math.max(...pool.map((item) => item.adjusted_score ?? item.score ?? 0), 1),
    [pool]
  )
  const shownPool = poolExpanded ? pool : pool.slice(0, COLLAPSED_COUNT)
  const sideBySide = channel === 'ADS' || channel === 'SEO'

  const poolCard = (
    <section className="chx-card chx-pool-card">
      <div className="chx-card-head">
        <h2>{CHANNEL_LABELS[channel]} Havuzu</h2>
        <span className="chx-card-count">{pool.length} kelime</span>
      </div>

      {loading ? (
        <div className="loading-state">
          <RefreshCw size={24} className="animate-spin" />
          <p>Havuz yükleniyor...</p>
        </div>
      ) : poolError ? (
        <div className="channels-alert channels-alert-error chx-pool-error" role="alert">
          {poolError}{' '}
          <button
            type="button"
            className="chx-retry-btn"
            onClick={() => selectedRunId && fetchPool(selectedRunId)}
          >
            <RefreshCw size={13} />
            Tekrar dene
          </button>
        </div>
      ) : pool.length === 0 ? (
        <p className="empty-text chx-empty">Bu kanal için havuz henüz hazır değil.</p>
      ) : channel === 'SOCIAL' ? (
        // Tasarım (Social.html): tablo yerine kompakt satır-kart gridi
        <>
          {briefSelection.composing && (
            <p className="chx-pool-pick-hint" role="note">
              Brief için kelime seçmek üzere kartlara tıklayın ({briefSelection.selectedIds.length}/
              {briefSelection.maxKeywords}).
            </p>
          )}
          <div className="chx-poolgrid">
            {shownPool.map((item, index) => {
              const score = item.adjusted_score ?? item.score ?? 0
              const picking = briefSelection.composing
              const picked = picking && briefSelection.isSelected(item.keyword_id)
              return (
                <div
                  key={item.id}
                  className={`chx-poolrow${picking ? ' is-pickable' : ''}${picked ? ' is-picked' : ''}`}
                >
                  {picking && (
                    // Satırın tamamını kaplayan seçim düğmesi; silme düğmesi ÜSTÜNDE ve ayrı
                    <button
                      type="button"
                      className="chx-poolrow-pick"
                      aria-pressed={picked}
                      aria-label={`${picked ? 'Brief seçiminden çıkar' : "Brief'e ekle"}: ${item.keyword}`}
                      onClick={() => briefSelection.toggle(item.keyword_id)}
                    />
                  )}
                  <span className="chx-poolrow-rank chx-mono">
                    {item.final_rank || item.rank || index + 1}
                  </span>
                  <div className="chx-poolrow-main">
                    <div className="chx-poolrow-kw">
                      {item.keyword}
                      {item.pool_label === 'rising_opportunity' && (
                        <span className="chx-kw-badge chx-kw-badge-rising">Yükselen Fırsat</span>
                      )}
                    </div>
                    <div className="chx-poolrow-meta">
                      <span className="chx-score-bar">
                        <span
                          className="chx-score-fill"
                          style={{ width: `${Math.max((score / maxScore) * 100, 5)}%` }}
                        />
                      </span>
                      <span className="chx-poolrow-vol chx-mono">
                        {formatNumber(item.volume)} hacim
                      </span>
                    </div>
                  </div>
                  {picked && (
                    <span className="chx-poolrow-check" aria-hidden="true">
                      <CheckCircle2 size={15} />
                    </span>
                  )}
                  <span className="chx-poolrow-score chx-mono">{formatNumber(score)}</span>
                  <button
                    type="button"
                    className="chx-remove-btn"
                    onClick={(event) => {
                      event.stopPropagation()
                      void handleRemove(item)
                    }}
                    disabled={!canEditPool}
                    title={
                      canEditPool
                        ? 'Bu çalışmanın havuzundan kaldır'
                        : 'Kanal ataması sürerken havuz düzenlenemez'
                    }
                  >
                    <X size={12} />
                  </button>
                </div>
              )
            })}
          </div>
          {pool.length > COLLAPSED_COUNT && (
            <button
              type="button"
              className="chx-expander"
              onClick={() => setPoolExpanded((v) => !v)}
            >
              {poolExpanded
                ? 'Daha az göster'
                : `Daha fazla kelime gör (${pool.length - COLLAPSED_COUNT})`}
              <span className={`chx-expander-chev${poolExpanded ? ' is-open' : ''}`}>
                <ChevronDown size={15} />
              </span>
            </button>
          )}
        </>
      ) : (
        <>
          <div className="chx-table-wrap">
            <table className="chx-table">
              <thead>
                <tr>
                  <th className="left rank">#</th>
                  <th className="left">KEYWORD</th>
                  <th>HACİM</th>
                  <th>SKOR</th>
                  <th className="action" aria-label="İşlem"></th>
                </tr>
              </thead>
              <tbody>
                {shownPool.map((item, index) => {
                  const score = item.adjusted_score ?? item.score ?? 0
                  return (
                    <tr key={item.id}>
                      <td className="left rank chx-mono">
                        {item.final_rank || item.rank || index + 1}
                      </td>
                      <td className="left keyword">
                        {item.keyword}
                        {item.is_strategic && (
                          <span className="chx-kw-badge chx-kw-badge-strategic">Stratejik</span>
                        )}
                      </td>
                      <td className="chx-mono muted">{formatNumber(item.volume)}</td>
                      <td>
                        <span className="chx-score">
                          <span className="chx-score-bar">
                            <span
                              className="chx-score-fill"
                              style={{ width: `${Math.max((score / maxScore) * 100, 4)}%` }}
                            />
                          </span>
                          <span className="chx-mono chx-score-value">{formatNumber(score)}</span>
                        </span>
                      </td>
                      <td className="action">
                        <button
                          type="button"
                          className="chx-remove-btn"
                          onClick={() => handleRemove(item)}
                          disabled={!canEditPool}
                          title={
                            canEditPool
                              ? 'Bu çalışmanın havuzundan kaldır'
                              : 'Kanal ataması sürerken havuz düzenlenemez'
                          }
                        >
                          <X size={13} />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {pool.length > COLLAPSED_COUNT && (
            <button
              type="button"
              className="chx-expander"
              onClick={() => setPoolExpanded((v) => !v)}
            >
              {poolExpanded
                ? 'Daha az göster'
                : `Daha fazla kelime gör (${pool.length - COLLAPSED_COUNT})`}
              <span className={`chx-expander-chev${poolExpanded ? ' is-open' : ''}`}>
                <ChevronDown size={15} />
              </span>
            </button>
          )}
        </>
      )}
    </section>
  )

  const generationPanel = selectedRunId ? (
    <>
      {channel === 'SEO' && <SeoGeoPanel runId={selectedRunId} run={selectedRun} />}
      {channel === 'ADS' && <AdsPanel runId={selectedRunId} run={selectedRun} />}
      {channel === 'SOCIAL' && (
        <SocialPanel
          runId={selectedRunId}
          run={selectedRun}
          pool={pool}
          poolLoading={loading}
          poolError={poolError}
          onRetryPool={() => fetchPool(selectedRunId)}
          keywordSelection={briefSelection}
        />
      )}
    </>
  ) : null

  return (
    <div className="channels-page animate-fade-in">
      {/* Tasarım: başlık + inline analiz seçici + aktif marka pili + yenile */}
      <header className="chx-header">
        <div className="chx-header-left">
          <div>
            <h1>{CHANNEL_LABELS[channel]}</h1>
            <p>Kanal havuzu ve içerik üretimi</p>
          </div>
          {runs.length > 0 && (
            <div className="chx-run-pill">
              <span className="chx-run-label">ANALİZ</span>
              <div className="chx-run-select">
                <select
                  title="Analiz seç"
                  value={selectedRunId || ''}
                  onChange={(event) => setSelectedRunId(Number(event.target.value) || null)}
                >
                  {runs.map((run) => (
                    <option key={run.id} value={run.id}>
                      #{run.id} {run.run_name || 'Analiz'} — {run.status}
                    </option>
                  ))}
                </select>
                <ChevronDown size={14} className="chx-run-chev" />
              </div>
              {selectedRun && (
                <span
                  className={`chx-status-pill${
                    selectedRun.status === 'channel_assigning' ? ' is-busy' : ''
                  }`}
                >
                  {selectedRun.status}
                </span>
              )}
            </div>
          )}
        </div>
        {activeWorkspace && (
          <span className="chx-brand-pill">
            <span className="muted">Aktif Marka</span>
            <b>{activeWorkspace.name}</b>
          </span>
        )}
        <ExportMenu channel={channel} runId={selectedRunId} disabled={!canEditPool} />
        <button type="button" className="chx-btn" onClick={() => void fetchRuns()}>
          <RefreshCw size={14} />
          Yenile
        </button>
      </header>

      {!activeWorkspace && (
        <div className="no-workspace">
          <AlertCircle size={20} />
          <span>Önce Marka Çalışması seçin</span>
        </div>
      )}

      {error && <div className="channels-alert channels-alert-error">{error}</div>}
      {info && <div className="channels-alert channels-alert-info">{info}</div>}
      {staleNotice && (
        <div className="channels-alert channels-alert-info">
          Üretilmiş içerikler güncelliğini yitirmiş olabilir. İçerik üretimini yeniden çalıştırın.
        </div>
      )}

      {activeWorkspace && runs.length === 0 && (
        <p className="empty-text">Kanal ataması tamamlanmış analiz bulunamadı.</p>
      )}

      {assignTaskId && assignPolling.status && (
        <div className="chx-card chx-assign-progress">
          <TaskProgress
            taskId={assignTaskId}
            status={assignPolling.status.status}
            progress={assignPolling.progress}
            message="Kanal ataması yapılıyor..."
            errorMessage={assignPolling.errorMessage}
          />
        </div>
      )}

      {sideBySide ? (
        // Tasarım: havuz + üretim formu yan yana; banner/sonuçlar tam genişlik
        <div className="chx-grid">
          {poolCard}
          {generationPanel}
        </div>
      ) : (
        <>
          {poolCard}
          {generationPanel && (
            <section
              className={`channel-generation-section${channel === 'SOCIAL' ? ' channel-generation-section-social' : ''}`}
            >
              {generationPanel}
            </section>
          )}
        </>
      )}
    </div>
  )
}
