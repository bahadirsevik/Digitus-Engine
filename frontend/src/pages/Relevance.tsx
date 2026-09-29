import { useState, useEffect, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertCircle, Calculator, ArrowRight, ChevronUp, ChevronDown } from 'lucide-react'
import { useBrandStore } from '../stores/brandStore'
import {
  brandProfileApi,
  scoringApi,
  KeywordRelevanceResponse,
  RelevanceComputeResponse,
} from '../services/api'
import type { ScoringRun } from '../types/models'
import './Relevance.css'

type SortField = 'keyword' | 'relevance_score' | 'matched_anchor' | 'method'
type SortDirection = 'asc' | 'desc'

export default function Relevance() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)

  const [scoringRuns, setScoringRuns] = useState<ScoringRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [relevanceData, setRelevanceData] = useState<KeywordRelevanceResponse[]>([])
  const [summary, setSummary] = useState<{
    average: number
    max: number
    min: number
    count: number
  } | null>(null)
  const [minScore, setMinScore] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sortField, setSortField] = useState<SortField>('relevance_score')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')

  const requestedRunId = useMemo(() => {
    const raw = searchParams.get('run_id')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isNaN(parsed) || parsed <= 0 ? null : parsed
  }, [searchParams])

  // Load workspace-scoped scoring runs
  useEffect(() => {
    if (!activeWorkspace?.id) {
      setScoringRuns([])
      setSelectedRunId(null)
      setRelevanceData([])
      setSummary(null)
      return
    }
    scoringApi
      .listRuns({ limit: 100, brand_profile_id: activeWorkspace.id })
      .then((res) => {
        const runs = ((res.data as ScoringRun[]) || []).filter((r) =>
          [
            'scored',
            'relevance_computing',
            'relevance_computed',
            // Atama sürerken relevance zaten hesaplanmış durumda — görüntülenebilir.
            'channel_assigning',
            'channel_assigned',
            'completed',
          ].includes(r.status)
        )
        setScoringRuns(runs)
      })
      .catch(() => {})
  }, [activeWorkspace?.id])

  // Auto-select run from query param once the run list is loaded
  useEffect(() => {
    if (!requestedRunId || scoringRuns.length === 0) return
    if (!scoringRuns.some((r) => r.id === requestedRunId)) return
    setSelectedRunId(requestedRunId)
  }, [requestedRunId, scoringRuns])

  // Load relevance when run selected
  useEffect(() => {
    if (!selectedRunId || !activeWorkspace?.id) {
      setRelevanceData([])
      setSummary(null)
      return
    }
    brandProfileApi
      .getRelevance(selectedRunId, 0, activeWorkspace.id)
      .then((res) => {
        const data = (res.data as KeywordRelevanceResponse[]) || []
        setRelevanceData(data)
        if (data.length > 0) {
          const scores = data.map((d) => d.relevance_score as number)
          setSummary({
            average: scores.reduce((a: number, b: number) => a + b, 0) / scores.length,
            max: Math.max(...scores),
            min: Math.min(...scores),
            count: scores.length,
          })
        } else {
          setSummary(null)
        }
      })
      .catch(() => setRelevanceData([]))
  }, [selectedRunId, activeWorkspace?.id])

  const handleCompute = async () => {
    if (!selectedRunId || !activeWorkspace?.id) return
    setLoading(true)
    setError(null)
    try {
      const res = await brandProfileApi.computeRelevance(selectedRunId, activeWorkspace.id)
      void (res.data as RelevanceComputeResponse)
      // Reload data
      const reloadRes = await brandProfileApi.getRelevance(selectedRunId, 0, activeWorkspace.id)
      const data = (reloadRes.data as KeywordRelevanceResponse[]) || []
      setRelevanceData(data)
      if (data.length > 0) {
        const scores = data.map((d) => d.relevance_score as number)
        setSummary({
          average: scores.reduce((a: number, b: number) => a + b, 0) / scores.length,
          max: Math.max(...scores),
          min: Math.min(...scores),
          count: scores.length,
        })
      }
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setError(e?.response?.data?.detail || 'İlgi skoru hesaplanırken hata')
    } finally {
      setLoading(false)
    }
  }

  const hasRelevanceData = relevanceData.length > 0

  const filteredData = useMemo(() => {
    return relevanceData.filter((d) => d.relevance_score >= minScore)
  }, [relevanceData, minScore])

  const sortedData = useMemo(() => {
    if (!sortField) return filteredData
    return [...filteredData].sort((a, b) => {
      const av = a[sortField]
      const bv = b[sortField]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (sortDirection === 'asc') {
        return av < bv ? -1 : av > bv ? 1 : 0
      } else {
        return av > bv ? -1 : av < bv ? 1 : 0
      }
    })
  }, [filteredData, sortField, sortDirection])

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null
    return sortDirection === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />
  }

  const hasConfirmedProfile = activeWorkspace?.status === 'confirmed'
  const goToChannels = () => {
    if (!selectedRunId) return
    navigate(`/channels?run_id=${selectedRunId}`)
  }

  return (
    <div className="relevance-page">
      <div className="relevance-header">
        <h1>İlgi Skoru</h1>
        <p className="relevance-subtitle">
          Skorlama sonrası, marka profili ile keyword'lerin alaka düzeyini hesaplar
        </p>
      </div>

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

      <div className="relevance-controls">
        <div className="control-row">
          <div className="control-group">
            <label>Scoring Run</label>
            <select
              value={selectedRunId || ''}
              onChange={(e) => setSelectedRunId(Number(e.target.value) || null)}
            >
              <option value="">Seçiniz...</option>
              {scoringRuns.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.run_name || `Run #${r.id}`} — {r.status}
                </option>
              ))}
            </select>
          </div>

          <div className="compute-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleCompute}
              disabled={!selectedRunId || loading || !hasConfirmedProfile}
              title={!hasConfirmedProfile ? 'Aktif çalışmanın onaylanmış profili yok' : ''}
            >
              <Calculator size={16} />
              {loading
                ? 'Hesaplanıyor...'
                : relevanceData.length > 0
                  ? 'Yeniden Hesapla'
                  : 'İlgi Skoru Hesapla'}
            </button>
            {selectedRunId && (
              <span
                className={`relevance-state-label ${relevanceData.length > 0 ? 'state-computed' : 'state-pending'}`}
              >
                {relevanceData.length > 0 ? 'Otomatik hesaplandı' : 'Henüz hesaplanmadı'}
              </span>
            )}
          </div>
        </div>

        {selectedRunId && (
          <div className="next-step">
            <button className="btn btn-primary" onClick={goToChannels}>
              Sonraki: Kanallar <ArrowRight size={16} />
            </button>
          </div>
        )}

        {!hasConfirmedProfile && activeWorkspace && (
          <div className="disabled-hint">
            <AlertCircle size={14} />
            Aktif çalışmanın onaylanmış profili yok. Önce Marka Profili sayfasından profili
            onaylayın.
          </div>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}

      {hasRelevanceData && (
        <>
          <div className="relevance-summary">
            <div className="summary-item">
              <span className="summary-label">Hesaplanan</span>
              <span className="summary-value">{summary?.count}</span>
            </div>
            <div className="summary-item">
              <span className="summary-label">Ortalama</span>
              <span className="summary-value">{summary?.average.toFixed(3)}</span>
            </div>
            <div className="summary-item">
              <span className="summary-label">En Yüksek</span>
              <span className="summary-value">{summary?.max.toFixed(3)}</span>
            </div>
            <div className="summary-item">
              <span className="summary-label">En Düşük</span>
              <span className="summary-value">{summary?.min.toFixed(3)}</span>
            </div>
          </div>

          <div className="filter-row">
            <label>
              Min İlgi Skoru: {minScore.toFixed(2)}
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
              />
            </label>
            <span className="filter-count">
              Gösterilen: {sortedData.length} / {relevanceData.length}
            </span>
          </div>

          <div className="relevance-table">
            <table>
              <thead>
                <tr>
                  <th onClick={() => toggleSort('keyword')}>
                    Keyword <SortIcon field="keyword" />
                  </th>
                  <th onClick={() => toggleSort('relevance_score')}>
                    Relevance Score <SortIcon field="relevance_score" />
                  </th>
                  <th onClick={() => toggleSort('matched_anchor')}>
                    Matched Anchor <SortIcon field="matched_anchor" />
                  </th>
                  <th onClick={() => toggleSort('method')}>
                    Method <SortIcon field="method" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedData.length > 0 ? (
                  sortedData.map((item) => (
                    <tr key={item.keyword_id}>
                      <td className="kw-cell">{item.keyword}</td>
                      <td className="score-cell">{item.relevance_score.toFixed(3)}</td>
                      <td className="anchor-cell">{item.matched_anchor || '—'}</td>
                      <td>{item.method}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="empty-filter-cell">
                      Bu eşikte gösterilecek keyword yok. Min ilgi skorunu düşürerek tekrar
                      görüntüleyebilirsin.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
