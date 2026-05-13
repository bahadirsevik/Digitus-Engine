import { useState, useEffect, useCallback } from 'react'
import { tasksApi } from '../services/api'

interface TaskStatus {
  task_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  result_data?: Record<string, unknown>
  error_message?: string
}

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

export const getWorkspaceTaskKey = (key: string, brand_profile_id?: number | null): string => {
  return brand_profile_id ? `${key}:${brand_profile_id}` : key
}

export function useTaskPolling(
  taskId: string | null,
  storageKey: string,
  intervalMs: number = 3000,
  brand_profile_id?: number
) {
  const [status, setStatus] = useState<TaskStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStatus = useCallback(
    async (id: string) => {
      if (!brand_profile_id) {
        setStatus(null)
        setLoading(false)
        return null
      }

      try {
        const response = await tasksApi.getStatus(id, brand_profile_id)
        const data = response.data as TaskStatus
        setStatus(data)

        // Task bittiyse storage'dan sil
        if (['completed', 'failed', 'cancelled'].includes(data.status)) {
          removeStoredTask(storageKey)
        }

        return data
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Polling error')
        return null
      }
    },
    [storageKey, brand_profile_id]
  )

  // taskId değiştiğinde storage'a kaydet
  useEffect(() => {
    if (taskId && brand_profile_id) {
      storeTask(storageKey, taskId)
      setLoading(true)
      fetchStatus(taskId).finally(() => setLoading(false))
    } else if (!brand_profile_id) {
      setStatus(null)
      setLoading(false)
    }
  }, [taskId, storageKey, brand_profile_id, fetchStatus])

  // Polling
  useEffect(() => {
    if (!taskId) return
    if (!brand_profile_id) return
    if (status && ['completed', 'failed', 'cancelled'].includes(status.status)) return

    const interval = setInterval(() => {
      fetchStatus(taskId)
    }, intervalMs)

    return () => clearInterval(interval)
  }, [taskId, status, intervalMs, brand_profile_id, fetchStatus])

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
