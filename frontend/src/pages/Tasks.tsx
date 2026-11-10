import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, Clock, CheckCircle, XCircle, Loader2, Trash2, Copy } from 'lucide-react'
import { apiErrorMessage, tasksApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import ErrorBanner from '../components/ErrorBanner'
import { summarizeTask } from './taskSummary'
import './Tasks.css'

interface Task {
  task_id: string
  task_type: string
  scoring_run_id?: number | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  result_data?: Record<string, unknown>
  error_message?: string
  created_at: string
  started_at?: string
  completed_at?: string
}

// Sunucu varsayılanı: en yeni 20 görev (filtre sunucuda uygulanır)
const TASK_LIST_LIMIT = 20

const FILTERS: { id: string; label: string }[] = [
  { id: 'all', label: 'Tümü' },
  { id: 'running', label: 'Çalışıyor' },
  { id: 'pending', label: 'Bekliyor' },
  { id: 'completed', label: 'Tamamlandı' },
  { id: 'failed', label: 'Hatalı' },
]

const STATUS_LABEL: Record<Task['status'], string> = {
  pending: 'Bekliyor',
  running: 'Çalışıyor',
  completed: 'Tamamlandı',
  failed: 'Hatalı',
  cancelled: 'İptal edildi',
}

const TASK_TYPE_LABEL: Record<string, string> = {
  seo_content: 'SEO+GEO içerik üretimi',
  ads: 'Google Ads reklam üretimi',
  social: 'Sosyal medya içerik üretimi',
  social_content: 'Sosyal medya içerik üretimi',
  export: 'Dışa aktarım',
  channel_assignment: 'Kanal ataması',
  corpus_screening: 'Ön tarama',
  competitor_discovery: 'Rakip keşfi',
  policy_preview: 'Rakip önizlemesi',
}

export default function Tasks() {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<string>('all')
  const [copiedId, setCopiedId] = useState<string | null>(null)
  // Workspace/filtre değişince uçuştaki eski yanıt yeni listeye yazılmaz
  const scopeRef = useRef('')
  scopeRef.current = `${activeWorkspace?.id ?? ''}:${filter}`

  const fetchTasks = async () => {
    const workspaceId = activeWorkspace?.id
    if (!workspaceId) {
      setTasks([])
      setLoading(false)
      return
    }
    const requestScope = scopeRef.current
    try {
      const res = await tasksApi.list({
        brand_profile_id: workspaceId,
        limit: TASK_LIST_LIMIT,
        status_filter: filter === 'all' ? undefined : filter,
      })
      if (scopeRef.current !== requestScope) return
      setTasks((res.data as { tasks?: Task[] }).tasks || [])
      setError(null)
    } catch (err) {
      if (scopeRef.current !== requestScope) return
      setError(`Görev listesi alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    } finally {
      if (scopeRef.current === requestScope) setLoading(false)
    }
  }

  useEffect(() => {
    setTasks([])
    setError(null)
    if (!activeWorkspace?.id) {
      setLoading(false)
      return
    }

    setLoading(true)
    fetchTasks()
    const interval = setInterval(fetchTasks, 5000)
    return () => clearInterval(interval)
  }, [activeWorkspace?.id, filter]) // eslint-disable-line react-hooks/exhaustive-deps

  const cancelTask = async (taskId: string) => {
    if (!activeWorkspace?.id) return

    try {
      await tasksApi.cancel(taskId, activeWorkspace.id)
      fetchTasks()
    } catch (err) {
      setError(`Görev iptal edilemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    }
  }

  const copyTaskId = async (taskId: string) => {
    try {
      await navigator.clipboard.writeText(taskId)
      setCopiedId(taskId)
      setTimeout(() => setCopiedId((cur) => (cur === taskId ? null : cur)), 2000)
    } catch {
      setError('Görev kimliği panoya kopyalanamadı.')
    }
  }

  const getStatusIcon = (status: Task['status']) => {
    switch (status) {
      case 'pending':
        return <Clock size={16} className="status-icon pending" />
      case 'running':
        return <Loader2 size={16} className="status-icon running animate-spin" />
      case 'completed':
        return <CheckCircle size={16} className="status-icon completed" />
      case 'failed':
      case 'cancelled':
        return <XCircle size={16} className="status-icon failed" />
    }
  }

  const emptyText =
    filter === 'all'
      ? 'Bu marka çalışmasında henüz görev yok.'
      : `Son ${TASK_LIST_LIMIT} görev içinde bu duruma uygun kayıt yok.`

  return (
    <div className="tasks-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Görevler</h1>
          <p>Arka plan görevlerinin durumunu takip edin</p>
        </div>
        <button className="btn btn-secondary" onClick={fetchTasks} disabled={loading}>
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          Yenile
        </button>
      </header>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {/* Filters */}
      <div className="task-filters">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={`filter-btn ${filter === f.id ? 'active' : ''}`}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {activeWorkspace?.id && (
        <p className="task-scope-note">En yeni {TASK_LIST_LIMIT} görev gösterilir.</p>
      )}

      {/* Task List */}
      <div className="task-list">
        {!activeWorkspace?.id ? (
          <div className="empty-state">Görevleri görmek için önce bir marka çalışması seçin.</div>
        ) : loading && tasks.length === 0 ? (
          <div className="loading-state">Yükleniyor...</div>
        ) : error && tasks.length === 0 ? null : tasks.length === 0 ? (
          <div className="empty-state">{emptyText}</div>
        ) : (
          tasks.map((task) => {
            const { summary, warnings, link } = summarizeTask(task)
            return (
              <div key={task.task_id} className={`task-card glass-card task-${task.status}`}>
                <div className="task-header">
                  {getStatusIcon(task.status)}
                  <span className="task-type">
                    {TASK_TYPE_LABEL[task.task_type] || 'Arka plan görevi'}
                    {task.scoring_run_id ? ` — Analiz #${task.scoring_run_id}` : ''}
                  </span>
                  <span className={`task-status-text task-status-${task.status}`}>
                    {STATUS_LABEL[task.status] || 'Durum bilinmiyor'}
                  </span>
                  <span className="task-date">
                    {new Date(task.created_at).toLocaleString('tr-TR')}
                  </span>
                  <button
                    type="button"
                    className="task-id"
                    title="Destek için görev kimliğini kopyala"
                    onClick={() => void copyTaskId(task.task_id)}
                  >
                    <Copy size={12} />
                    {copiedId === task.task_id ? 'Kopyalandı' : `#${task.task_id.slice(0, 8)}`}
                  </button>
                </div>

                {task.status === 'running' && (
                  <div className="progress-container">
                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${task.progress}%` }} />
                    </div>
                    <span className="progress-text">{task.progress}%</span>
                  </div>
                )}

                {summary && <p className="task-summary">{summary}</p>}

                {warnings.map((w) => (
                  <div key={w} className="task-error task-note">
                    <strong>Not:</strong> {w}
                  </div>
                ))}

                {task.error_message && (
                  <div
                    className={task.status === 'completed' ? 'task-error task-note' : 'task-error'}
                  >
                    <strong>{task.status === 'completed' ? 'Not:' : 'Hata:'}</strong>{' '}
                    {task.error_message}
                  </div>
                )}

                {(link || task.status === 'pending' || task.status === 'running') && (
                  <div className="task-actions">
                    {link && (
                      <Link to={link.to} className="btn btn-sm btn-secondary">
                        {link.label}
                      </Link>
                    )}
                    {(task.status === 'pending' || task.status === 'running') && (
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => cancelTask(task.task_id)}
                      >
                        <Trash2 size={14} />
                        İptal
                      </button>
                    )}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
