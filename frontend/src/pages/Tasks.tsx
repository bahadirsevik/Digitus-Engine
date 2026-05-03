/**
 * Tasks Page
 * Celery task listesi ve durumu
 */
import { useState, useEffect } from 'react'
import { RefreshCw, Clock, CheckCircle, XCircle, Loader2, Trash2 } from 'lucide-react'
import ErrorBanner from '../components/ErrorBanner'
import './Tasks.css'

interface Task {
  task_id: string
  task_type: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  result_data?: Record<string, unknown>
  error_message?: string
  created_at: string
  started_at?: string
  completed_at?: string
}

export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<string>('all')

  useEffect(() => {
    fetchTasks()
    const interval = setInterval(fetchTasks, 5000) // Auto-refresh
    return () => clearInterval(interval)
  }, [])

  const fetchTasks = async () => {
    try {
      const res = await fetch('/api/v1/tasks/')
      if (!res.ok) throw new Error('Task listesi alınamadı')
      const data = await res.json()
      setTasks(data.tasks || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Hata')
    } finally {
      setLoading(false)
    }
  }

  const cancelTask = async (taskId: string) => {
    try {
      const res = await fetch(`/api/v1/tasks/${taskId}/cancel`, {
        method: 'POST'
      })
      if (!res.ok) throw new Error('İptal başarısız')
      fetchTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'İptal hatası')
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

  const getTaskTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      seo_content: 'SEO+GEO',
      ads: 'Google Ads',
      social: 'Sosyal Medya',
      export: 'Dışa Aktarım',
    }
    return labels[type] || type
  }

  const filteredTasks = filter === 'all'
    ? tasks
    : tasks.filter(t => t.status === filter)

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
        {['all', 'running', 'pending', 'completed', 'failed'].map(f => (
          <button
            key={f}
            className={`filter-btn ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f === 'all' ? 'Tümü' : 
             f === 'running' ? 'Çalışıyor' :
             f === 'pending' ? 'Bekliyor' :
             f === 'completed' ? 'Tamamlandı' : 'Hatalı'}
          </button>
        ))}
      </div>

      {/* Task List */}
      <div className="task-list">
        {loading && tasks.length === 0 ? (
          <div className="loading-state">Yükleniyor...</div>
        ) : filteredTasks.length === 0 ? (
          <div className="empty-state">Görev bulunamadı</div>
        ) : (
          filteredTasks.map(task => (
            <div key={task.task_id} className={`task-card glass-card task-${task.status}`}>
              <div className="task-header">
                {getStatusIcon(task.status)}
                <span className="task-type">{getTaskTypeLabel(task.task_type)}</span>
                <span className="task-id">#{task.task_id.slice(0, 8)}</span>
                <span className="task-date">
                  {new Date(task.created_at).toLocaleString('tr-TR')}
                </span>
              </div>

              {task.status === 'running' && (
                <div className="progress-container">
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${task.progress}%` }} />
                  </div>
                  <span className="progress-text">{task.progress}%</span>
                </div>
              )}

              {task.error_message && (
                <div className="task-error">
                  <strong>Hata:</strong> {task.error_message}
                </div>
              )}

              {task.result_data && Object.keys(task.result_data).length > 0 && (
                <div className="task-result">
                  <pre>{JSON.stringify(task.result_data, null, 2)}</pre>
                </div>
              )}

              {(task.status === 'pending' || task.status === 'running') && (
                <div className="task-actions">
                  <button
                    className="btn btn-sm btn-danger"
                    onClick={() => cancelTask(task.task_id)}
                  >
                    <Trash2 size={14} />
                    İptal
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
