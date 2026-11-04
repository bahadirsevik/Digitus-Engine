import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { AnalysisToast, ChainStage } from './AnalysisProgress'
import {
  getWorkspaceTaskKey,
  POLL_FAILED_MESSAGE,
  POLL_RECHECK_LABEL,
  useTaskPolling,
} from '../hooks/useTaskPolling'
import { useAnalysisToastStore } from '../stores/analysisToastStore'
import { useBrandStore } from '../stores/brandStore'

/** Sayfa değişiminden etkilenmeyen, Layout seviyesinde kanal atama ilerleme toast'ı. */
export default function GlobalAnalysisToast() {
  const navigate = useNavigate()
  const workspaceId = useBrandStore((s) => s.activeWorkspace?.id)
  const { task, dismissedTaskId, dismiss } = useAnalysisToastStore()
  const mine = task && workspaceId === task.workspaceId ? task : null
  const polling = useTaskPolling(
    mine?.taskId ?? null,
    getWorkspaceTaskKey('channel_assign', mine?.workspaceId, mine?.runId),
    3000,
    mine?.workspaceId,
    mine?.runId
  )

  // Yoklama hatası: görev BAŞARISIZ değil, durumu bilinmiyor — yeniden başlatma değil, yeniden kontrol sunulur
  if (mine && polling.pollFailed && dismissedTaskId !== mine.taskId) {
    return (
      <div className="kwx-toast" role="alert">
        <div className="kwx-toast-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="kwx-toast-title">Analiz durumu alınamıyor</div>
            <div className="kwx-toast-sub">{POLL_FAILED_MESSAGE}</div>
          </div>
          <button type="button" className="kwx-toast-close" onClick={dismiss} title="Kapat">
            <X size={13} />
          </button>
        </div>
        <div className="kwx-toast-body">
          <button type="button" className="btn btn-sm btn-secondary" onClick={polling.recheck}>
            {POLL_RECHECK_LABEL}
          </button>
        </div>
      </div>
    )
  }

  const taskStatus = polling.status?.status
  if (!mine || !taskStatus || dismissedTaskId === mine.taskId) return null
  const stage: ChainStage =
    taskStatus === 'completed'
      ? 'done'
      : taskStatus === 'failed' || taskStatus === 'cancelled'
        ? 'failed'
        : 'assigning'
  const message =
    typeof polling.resultData?.current_message === 'string'
      ? polling.resultData.current_message
      : null

  return (
    <AnalysisToast
      stage={stage}
      taskProgress={polling.progress || 0}
      channels={mine.channels}
      message={message}
      errorMessage={taskStatus === 'cancelled' ? 'Analiz iptal edildi' : polling.errorMessage}
      onDismiss={dismiss}
      onOpen={() => navigate(`/keywords?view=scores&run_id=${mine.runId}`)}
    />
  )
}
