/**
 * TaskProgress Component
 * Task durumu ve progress bar
 */
import { Loader2, CheckCircle, XCircle, Clock } from 'lucide-react'
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
  errorMessage 
}: TaskProgressProps) {
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
      case 'pending': return 'Bekliyor...'
      case 'running': return 'Çalışıyor...'
      case 'completed': return 'Tamamlandı'
      case 'failed': return 'Hata'
      case 'cancelled': return 'İptal Edildi'
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
            <div 
              className="progress-fill" 
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="progress-text">{progress}%</span>
        </div>
      )}

      {message && (
        <p className="task-message">{message}</p>
      )}

      {status === 'failed' && errorMessage && (
        <div className="task-error">
          <strong>Hata:</strong> {errorMessage}
        </div>
      )}
    </div>
  )
}
