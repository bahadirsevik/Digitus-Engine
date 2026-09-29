/**
 * TaskProgress Component
 * Task durumu ve progress bar
 */
import { Loader2, CheckCircle, XCircle, Clock } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import './TaskProgress.css'

interface TaskProgressProps {
  taskId: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message?: string
  errorMessage?: string
}

export default function TaskProgress({
  taskId,
  status,
  progress,
  message,
  errorMessage,
}: TaskProgressProps) {
  const [displayProgress, setDisplayProgress] = useState(progress || 0)
  const hasRealProgressRef = useRef((progress || 0) > 0)

  useEffect(() => {
    hasRealProgressRef.current = (progress || 0) > 0
    setDisplayProgress(progress || 0)
  }, [taskId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (['completed', 'failed', 'cancelled'].includes(status)) {
      setDisplayProgress(status === 'completed' ? 100 : Math.max(displayProgress, progress || 0))
      return
    }
    if ((progress || 0) > 0) {
      hasRealProgressRef.current = true
    }
    setDisplayProgress((prev) => Math.max(prev, progress || 0))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress, status])

  useEffect(() => {
    if (status !== 'running' || hasRealProgressRef.current) return
    const interval = window.setInterval(() => {
      setDisplayProgress((prev) => {
        if (hasRealProgressRef.current) return prev
        return Math.min(12, prev + 1)
      })
    }, 1200)
    return () => window.clearInterval(interval)
  }, [status])

  const getStatusIcon = () => {
    switch (status) {
      case 'pending':
        return <Clock size={20} className="status-icon pending" />
      case 'running':
        return <Loader2 size={20} className="status-icon running animate-spin" />
      case 'completed':
        return <CheckCircle size={20} className="status-icon completed" />
      case 'failed':
      case 'cancelled':
        return <XCircle size={20} className="status-icon failed" />
    }
  }

  const getStatusText = () => {
    switch (status) {
      case 'pending':
        return 'Bekliyor...'
      case 'running':
        return 'Çalışıyor...'
      case 'completed':
        return 'Tamamlandı'
      case 'failed':
        return 'Hata'
      case 'cancelled':
        return 'İptal Edildi'
    }
  }

  return (
    <div className={`task-progress task-${status}`}>
      <div className="task-header">
        {getStatusIcon()}
        <div className="task-info">
          <span className="task-status-text">{getStatusText()}</span>
          <span className="task-id">Gorev ID: {taskId}</span>
        </div>
      </div>

      {status === 'running' && (
        <div className="progress-container">
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${displayProgress}%` }} />
          </div>
          <span className="progress-text">{displayProgress}%</span>
        </div>
      )}

      {message && <p className="task-message">{message}</p>}

      {status === 'failed' && errorMessage && (
        <div className="task-error">
          <strong>Hata:</strong> {errorMessage}
        </div>
      )}
    </div>
  )
}
