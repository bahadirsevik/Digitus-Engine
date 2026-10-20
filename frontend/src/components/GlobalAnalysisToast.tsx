import { useNavigate } from 'react-router-dom'
import { AnalysisToast, ChainStage } from './AnalysisProgress'
import { getWorkspaceTaskKey, useTaskPolling } from '../hooks/useTaskPolling'
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
