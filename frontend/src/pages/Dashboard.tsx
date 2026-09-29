import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dashboardApi, DashboardSummary } from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import DashboardCockpit from './DashboardCockpit'
import DashboardEmpty from './DashboardEmpty'
import './Dashboard.css'

const POLL_DEFAULT_MS = 30_000
const POLL_BLOCKING_MS = 10_000

export default function Dashboard() {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeRunId, setActiveRunId] = useState<number | undefined>(undefined)
  const isMountedRef = useRef(true)

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const fetchSummary = useCallback(
    async (opts: { refresh?: boolean } = {}) => {
      setLoading(true)
      setError(null)
      try {
        const res = await dashboardApi.workspaceSummary({
          brand_profile_id: activeWorkspace?.id,
          active_run_id: activeRunId,
          refresh: opts.refresh ?? false,
          refresh_health: opts.refresh ?? false,
        })
        if (isMountedRef.current) setSummary(res.data)
      } catch (err) {
        const message =
          err && typeof err === 'object' && 'message' in err
            ? String((err as { message?: unknown }).message)
            : 'Özet verisi alınamadı.'
        if (isMountedRef.current) setError(message)
      } finally {
        if (isMountedRef.current) setLoading(false)
      }
    },
    [activeWorkspace?.id, activeRunId]
  )

  useEffect(() => {
    fetchSummary({ refresh: true })
  }, [fetchSummary])

  const pollInterval = useMemo(() => {
    return summary?.blocking_task ? POLL_BLOCKING_MS : POLL_DEFAULT_MS
  }, [summary?.blocking_task])

  useEffect(() => {
    const id = window.setInterval(() => {
      fetchSummary({ refresh: false })
    }, pollInterval)
    return () => window.clearInterval(id)
  }, [pollInterval, fetchSummary])

  const handleChangeRun = useCallback((runId: number) => {
    setActiveRunId(runId)
  }, [])

  const handleManualRefresh = useCallback(() => {
    fetchSummary({ refresh: true })
  }, [fetchSummary])

  if (loading && !summary) {
    return (
      <div className="dashboard-page animate-fade-in">
        <p className="dashboard-loading">Yükleniyor...</p>
      </div>
    )
  }

  if (error && !summary) {
    return (
      <div className="dashboard-page animate-fade-in">
        <div className="dashboard-warning">{error}</div>
      </div>
    )
  }

  if (!summary) {
    return <DashboardEmpty nextAction={null} />
  }

  if (!summary.workspace) {
    return <DashboardEmpty nextAction={summary.next_action} />
  }

  return (
    <>
      {error && (
        <div className="dashboard-page">
          <div className="dashboard-warning">{error}</div>
        </div>
      )}
      <DashboardCockpit
        summary={summary}
        loading={loading}
        onChangeRun={handleChangeRun}
        onRefresh={handleManualRefresh}
      />
    </>
  )
}
