import { useState, useEffect, useMemo, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { Zap, RefreshCw, ArrowRight } from 'lucide-react'
import { scoringApi, channelsApi } from '../services/api'
import { useTaskPolling, getStoredTaskId } from '../hooks/useTaskPolling'
import TaskProgress from '../components/TaskProgress'
import './Channels.css'

interface PoolKeyword {
  keyword_id: number
  keyword: string
  rank: number
  is_strategic: boolean
  volume?: number
  score?: number
  relevance_score?: number | null
  adjusted_score?: number | null
}

interface ScoringRun {
  id: number
  run_name?: string
  status: string
  default_relevance_coefficient?: number
}

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

  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const [pools, setPools] = useState<{ [key: string]: PoolKeyword[] }>({})
  const [loading, setLoading] = useState(false)
  const [assigning, setAssigning] = useState(false)
  const [assignTaskId, setAssignTaskId] = useState<string | null>(getStoredTaskId('channel_assign'))
  const [error, setError] = useState<string>('')
  const [info, setInfo] = useState<string>('')
  const [relevanceCoefficient, setRelevanceCoefficient] = useState<number>(1.0)

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
  const assignPolling = useTaskPolling(assignTaskId, 'channel_assign')

  const fetchRuns = useCallback(async () => {
    try {
      const res = await scoringApi.listRuns()
      const completedRuns = (Array.isArray(res.data) ? res.data : []).filter((r: any) => r.status === 'completed')
      setRuns(completedRuns)
    } catch (fetchError) {
      setError(`Çalışmalar yüklenemedi: ${extractErrorMessage(fetchError)}`)
    }
  }, [])

  const fetchPools = useCallback(async (runId: number) => {
    setLoading(true)
    try {
      const poolsRes = await channelsApi.getPools(runId)
      setPools(poolsRes.data.channels || {})
      setError('')
    } catch (fetchError) {
      setPools({})
      setError(`Havuzlar alınamadı: ${extractErrorMessage(fetchError)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchRuns()
  }, [fetchRuns])

  useEffect(() => {
    if (!requestedRunId) return
    setSelectedRun((prev) => prev || requestedRunId)
  }, [requestedRunId])

  useEffect(() => {
    if (!selectedRunData) return
    const defaultCoef = Number(selectedRunData.default_relevance_coefficient ?? 1)
    setRelevanceCoefficient(normalizeCoefficient(defaultCoef))
  }, [selectedRunData?.id])

  useEffect(() => {
    if (!selectedRun) {
      setPools({})
      return
    }
    void fetchPools(selectedRun)
  }, [selectedRun, fetchPools])

  // When assignment task completes, refresh pools
  useEffect(() => {
    if (assignPolling.isCompleted && selectedRun) {
      void fetchPools(selectedRun)
      setAssigning(false)
      setInfo('Kanal ataması tamamlandı. Havuzlar yenilendi.')
      setError('')
    }
    if (assignPolling.isFailed) {
      setAssigning(false)
      setError(assignPolling.errorMessage || 'Kanal ataması başarısız.')
    }
  }, [assignPolling.isCompleted, assignPolling.isFailed, assignPolling.errorMessage, selectedRun, fetchPools])

  const runAssignment = async (runId: number) => {
    if (hasExistingPools) {
      const ok = window.confirm(
        'Havuzlar yeniden oluşturulacak. Mevcut üretilmiş içerikler silinmez, ancak yeni havuzla uyumsuz kalabilir. Devam etmek istiyor musunuz?'
      )
      if (!ok) return
    }

    setAssigning(true)
    setError('')
    setInfo('')

    try {
      const coefficient = normalizeCoefficient(relevanceCoefficient)
      const res = await channelsApi.assign(runId, coefficient)
      const taskId = res.data.task_id
      setAssignTaskId(taskId)
      setInfo(`Atama başlatıldı. Kullanılan etkili ilgi katsayısı: ${Number(res.data.effective_relevance_coefficient ?? coefficient).toFixed(2)}`)
    } catch (assignError) {
      setAssigning(false)
      setError(extractErrorMessage(assignError))
    }
  }

  const channels = ['ADS', 'SEO', 'SOCIAL']
  const channelColors = {
    ADS: '#ef4444',
    SEO: '#10b981',
    SOCIAL: '#3b82f6'
  }

  const isAssignActive = assigning || assignPolling.isActive

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

      {error && <div className="channels-alert channels-alert-error">{error}</div>}
      {info && <div className="channels-alert channels-alert-info">{info}</div>}

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
                value={relevanceCoefficient}
                onChange={(e) => setRelevanceCoefficient(normalizeCoefficient(Number(e.target.value)))}
              />
            </div>
            <button
              className="btn btn-success"
              onClick={() => runAssignment(selectedRun)}
              disabled={isAssignActive}
            >
              <Zap size={18} />
              {isAssignActive ? 'Atama Sürüyor...' : 'Kanal Ataması Yap'}
            </button>
          </div>
        )}
      </div>

      {assignTaskId && assignPolling.status && (
        <div className="glass-card" style={{ marginTop: 'var(--space-md)' }}>
          <TaskProgress
            taskId={assignTaskId}
            status={assignPolling.status.status}
            progress={assignPolling.progress}
            errorMessage={assignPolling.errorMessage}
          />
        </div>
      )}

      {selectedRun && !loading && (
        <div className="pools-grid">
          {channels.map((channel) => (
            <div key={channel} className="pool-card glass-card">
              <div className="pool-header" style={{ borderColor: channelColors[channel as keyof typeof channelColors] }}>
                <h3>{channel}</h3>
                <span className="pool-count">{(pools[channel] || []).length} kelime</span>
              </div>
              <div className="pool-list">
                {(pools[channel] || []).length === 0 ? (
                  <p className="empty-text">Havuz boş</p>
                ) : (
                  (pools[channel] || []).map((kw, idx) => (
                    <div key={kw.keyword_id} className="pool-item">
                      <span className="pool-rank">#{idx + 1}</span>
                      <span className="pool-keyword">{kw.keyword}</span>
                    </div>
                  ))
                )}
              </div>
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
