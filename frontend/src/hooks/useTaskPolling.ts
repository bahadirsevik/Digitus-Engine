import { useState, useEffect, useCallback, useRef } from 'react'
import { tasksApi } from '../services/api'
import type { TaskStatusInfo as TaskStatus } from '../services/api'

const STORAGE_KEY = 'digitus_active_tasks'

// localStorage helpers
const getStoredTasks = (): Record<string, string> => {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
}

const storeTask = (key: string, taskId: string) => {
  const tasks = getStoredTasks()
  tasks[key] = taskId
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks))
}

const removeStoredTask = (key: string) => {
  const tasks = getStoredTasks()
  delete tasks[key]
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks))
}

export const getStoredTaskId = (key: string): string | null => {
  return getStoredTasks()[key] || null
}

/** Görev kimliğini doğrudan (hook dışından) kaydeder — örn. başka run'a ait geç yanıt. */
export const setStoredTaskId = (key: string, taskId: string): void => {
  storeTask(key, taskId)
}

/**
 * Görev saklama anahtarı. `runId` verilirse anahtar run'a da bağlanır
 * (`key:ws:runId`); run henüz bilinmiyorsa `null` verilir (`key:ws:none`).
 * `runId` hiç verilmezse eski workspace-only biçim döner.
 */
export const getWorkspaceTaskKey = (
  key: string,
  brand_profile_id?: number | null,
  runId?: number | null
): string => {
  if (!brand_profile_id) return key
  if (runId === undefined) return `${key}:${brand_profile_id}`
  return `${key}:${brand_profile_id}:${runId ?? 'none'}`
}

/**
 * Saklama anahtarına bağlı görev kimliği state'i. Anahtar (workspace/run) değişince
 * eski anahtara ait kimlik aynı render'da bırakılır ve yeni anahtarın kaydı okunur —
 * böylece A run'ının görevi B'nin anahtarı altında yoklanmaz/yazılmaz.
 */
export function useScopedTaskId(
  storageKey: string
): [string | null, (taskId: string | null) => void] {
  const [entry, setEntry] = useState<{ key: string; id: string | null }>(() => ({
    key: storageKey,
    id: getStoredTaskId(storageKey),
  }))
  const taskId = entry.key === storageKey ? entry.id : getStoredTaskId(storageKey)
  const setTaskId = useCallback(
    (id: string | null) => setEntry({ key: storageKey, id }),
    [storageKey]
  )
  return [taskId, setTaskId]
}

export function useTaskPolling(
  taskId: string | null,
  storageKey: string,
  intervalMs: number = 3000,
  brand_profile_id?: number,
  runId?: number | null
) {
  const [status, setStatus] = useState<TaskStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Backend görevin başka bir run'a ait olduğunu bildirdi — bu görev "bizim" değil
  const [foreign, setForeign] = useState(false)

  // Geç gelen yanıt, kapsam (görev/anahtar) değiştiyse state'e yazılmaz
  const scopeRef = useRef({ taskId, storageKey })
  scopeRef.current = { taskId, storageKey }

  const fetchStatus = useCallback(
    async (id: string) => {
      if (!brand_profile_id) {
        setStatus(null)
        setLoading(false)
        return null
      }

      const isCurrent = () =>
        scopeRef.current.taskId === id && scopeRef.current.storageKey === storageKey

      try {
        const response = await tasksApi.getStatus(id, brand_profile_id)
        if (!isCurrent()) return null
        const data = response.data as TaskStatus

        if (
          data.scoring_run_id != null &&
          runId != null &&
          Number(data.scoring_run_id) !== Number(runId)
        ) {
          setStatus(null)
          setForeign(true)
          removeStoredTask(storageKey)
          return null
        }

        setStatus(data)

        // Task bittiyse storage'dan sil
        if (['completed', 'failed', 'cancelled'].includes(data.status)) {
          removeStoredTask(storageKey)
        }

        return data
      } catch (err) {
        if (!isCurrent()) return null
        setError(err instanceof Error ? err.message : 'Polling error')
        return null
      }
    },
    [storageKey, brand_profile_id, runId]
  )

  // taskId / anahtar değiştiğinde önceki görev durumu sıfırlanır, yenisi storage'a kaydedilir
  useEffect(() => {
    setStatus(null)
    setError(null)
    setForeign(false)
    if (taskId && brand_profile_id) {
      storeTask(storageKey, taskId)
      setLoading(true)
      fetchStatus(taskId).finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [taskId, storageKey, brand_profile_id, fetchStatus])

  // Polling
  useEffect(() => {
    if (!taskId) return
    if (!brand_profile_id) return
    if (foreign) return
    if (status && ['completed', 'failed', 'cancelled'].includes(status.status)) return

    const interval = setInterval(() => {
      fetchStatus(taskId)
    }, intervalMs)

    return () => clearInterval(interval)
  }, [taskId, status, foreign, intervalMs, brand_profile_id, fetchStatus])

  const isActive = status?.status === 'pending' || status?.status === 'running'
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'

  return {
    status,
    loading,
    error,
    isActive,
    isCompleted,
    isFailed,
    progress: status?.progress || 0,
    resultData: status?.result_data,
    errorMessage: status?.error_message,
  }
}
