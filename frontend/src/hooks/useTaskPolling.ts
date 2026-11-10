import { useState, useEffect, useCallback, useRef } from 'react'
import { apiErrorMessage, apiErrorStatus, tasksApi } from '../services/api'
import type { TaskStatusInfo as TaskStatus } from '../services/api'

const STORAGE_KEY = 'digitus_active_tasks'

/** Üst üste bu kadar yoklama hatasından sonra yoklama durur (görev başarısız SAYILMAZ). */
export const MAX_POLL_FAILURES = 3
export const POLL_FAILED_MESSAGE =
  'Görevin durumu alınamıyor. İşlem arka planda devam ediyor olabilir.'
export const POLL_RECHECK_LABEL = 'Durumu tekrar kontrol et'

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
  let taskId = entry.id
  if (entry.key !== storageKey) {
    // Anahtar değişti: önbellekteki kayıt (bu anahtarın önceki ziyaretinden kalma olabilir)
    // kullanılmaz; yeni anahtarın kaydı depodan taze okunur ve aynı render'da state'e alınır.
    taskId = getStoredTaskId(storageKey)
    setEntry({ key: storageKey, id: taskId })
  }
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
  // Üst üste MAX_POLL_FAILURES yoklama hatası: durum BİLİNMİYOR (görev başarısız değil)
  const [pollFailed, setPollFailed] = useState(false)
  const failuresRef = useRef(0)
  const inFlightRef = useRef<string | null>(null)
  // Görev "bizim" değil (başka run'a ait) ya da backend'de yok (404): yoklama durur, boşta durum
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

      // Aynı görev için ikinci istek başlatılmaz (yoklamalar çakışmaz)
      const flightKey = `${storageKey}|${id}`
      if (inFlightRef.current === flightKey) return null
      inFlightRef.current = flightKey

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

        failuresRef.current = 0
        setPollFailed(false)
        setError(null)
        setStatus(data)

        // Task bittiyse storage'dan sil
        if (['completed', 'failed', 'cancelled'].includes(data.status)) {
          removeStoredTask(storageKey)
        }

        return data
      } catch (err) {
        if (!isCurrent()) return null
        // 404: görev kaydı yok (silinmiş/eski depo kaydı) — "durum bilinmiyor" DEĞİL, boşta durum.
        // Depo kaydı silinir, yoklama durur ve pollFailed set edilmez (üretim butonu kilitlenmesin).
        if (apiErrorStatus(err) === 404) {
          setStatus(null)
          setForeign(true)
          removeStoredTask(storageKey)
          return null
        }
        failuresRef.current += 1
        setError(apiErrorMessage(err, 'Görev durumu alınamadı'))
        if (failuresRef.current >= MAX_POLL_FAILURES) setPollFailed(true)
        return null
      } finally {
        if (inFlightRef.current === flightKey) inFlightRef.current = null
      }
    },
    [storageKey, brand_profile_id, runId]
  )

  // taskId / anahtar değiştiğinde önceki görev durumu sıfırlanır, yenisi storage'a kaydedilir
  useEffect(() => {
    setStatus(null)
    setError(null)
    setPollFailed(false)
    failuresRef.current = 0
    setForeign(false)
    if (taskId && brand_profile_id) {
      storeTask(storageKey, taskId)
      setLoading(true)
      fetchStatus(taskId).finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [taskId, storageKey, brand_profile_id, fetchStatus])

  const finished = status != null && ['completed', 'failed', 'cancelled'].includes(status.status)

  // Sıralı yoklama: bir sonraki istek, öncekinin BİTMESİNDEN sonra planlanır
  useEffect(() => {
    if (!taskId || !brand_profile_id || foreign || finished || pollFailed) return
    let cancelled = false
    let timer: number | undefined
    const loop = async () => {
      await fetchStatus(taskId)
      if (!cancelled) timer = window.setTimeout(loop, intervalMs)
    }
    timer = window.setTimeout(loop, intervalMs)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [taskId, foreign, finished, pollFailed, intervalMs, brand_profile_id, fetchStatus])

  // "Durumu tekrar kontrol et": sayaç sıfırlanır, yoklama kaldığı yerden sürer (görev/depo kaydı korunur)
  const recheck = useCallback(() => {
    failuresRef.current = 0
    setPollFailed(false)
    setError(null)
    const id = scopeRef.current.taskId
    if (id) void fetchStatus(id)
  }, [fetchStatus])

  const isActive = status?.status === 'pending' || status?.status === 'running'
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'

  return {
    status,
    loading,
    error,
    pollFailed,
    recheck,
    isActive,
    isCompleted,
    isFailed,
    progress: status?.progress || 0,
    resultData: status?.result_data,
    errorMessage: status?.error_message,
  }
}
