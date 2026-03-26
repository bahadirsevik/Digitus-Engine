/**
 * Task Polling Hook
 * F5 persistence ile task durumu takibi
 */
import { useState, useEffect, useCallback } from 'react'

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

export function useTaskPolling(
  taskId: string | null,
  storageKey: string,
  intervalMs: number = 3000
) {
  const [status, setStatus] = useState<TaskStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStatus = useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/v1/tasks/${id}`)
      if (!response.ok) throw new Error('Task not found')
      const data = await response.json()
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
  }, [storageKey])

  // taskId değiştiğinde storage'a kaydet
  useEffect(() => {
    if (taskId) {
      storeTask(storageKey, taskId)
      setLoading(true)
      fetchStatus(taskId).finally(() => setLoading(false))
    }
  }, [taskId, storageKey, fetchStatus])

  // Polling
  useEffect(() => {
    if (!taskId) return
    if (status && ['completed', 'failed', 'cancelled'].includes(status.status)) return

    const interval = setInterval(() => {
      fetchStatus(taskId)
    }, intervalMs)

    return () => clearInterval(interval)
  }, [taskId, status, intervalMs, fetchStatus])

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
