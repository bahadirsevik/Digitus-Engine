// Plan v4 (export dağıtımı): ExportJob yaşam döngüsü — create → poll → download.
// Export.tsx'teki mantığın ortak hook'a çıkarılmış hali; ExportMenu kullanır.
// Codex post-review: geçmişteki pending/processing job devralınıp poll'lanır;
// kullanıcının bu oturumda başlattığı job tamamlanınca OTOMATİK indirilir.
import { useCallback, useEffect, useRef, useState } from 'react'
import { MAX_POLL_FAILURES } from './useTaskPolling'
import {
  apiErrorMessage,
  downloadBlobResponse,
  exportApi,
  ExportRequest,
  ExportStatusResponse,
  policyStaleFromError,
} from '../services/api'

const POLL_INTERVAL_MS = 2000
export const EXPORT_POLL_FAILED_MESSAGE =
  'Dışa aktarımın durumu alınamıyor. İşlem arka planda devam ediyor olabilir.'
const HISTORY_LIMIT = 5

function isFinal(status: ExportStatusResponse['status']) {
  return status === 'completed' || status === 'failed'
}

function isRunning(status: ExportStatusResponse['status']) {
  return status === 'pending' || status === 'processing'
}

export function useExportJob(runId: number | null, workspaceId: number | undefined) {
  const [activeJob, setActiveJob] = useState<ExportStatusResponse | null>(null)
  const [history, setHistory] = useState<ExportStatusResponse[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Yalnız KULLANICININ başlattığı job biter bitmez otomatik indirilir;
  // geçmişten devralınan job'da sürpriz indirme olmaz.
  const autoDownloadRef = useRef(false)
  // Üst üste MAX_POLL_FAILURES durum hatası: export'un durumu bilinmiyor (başarısız DEĞİL)
  const [pollFailed, setPollFailed] = useState(false)
  const failuresRef = useRef(0)
  const activeIdRef = useRef<string | null>(null)

  const downloadJob = useCallback(
    async (job: ExportStatusResponse) => {
      if (!workspaceId || job.status !== 'completed') return
      try {
        const res = await exportApi.download(job.export_id, workspaceId)
        downloadBlobResponse(res, job.file_name || `digitus_rapor_${job.export_id.slice(0, 8)}`)
      } catch (err) {
        setError(apiErrorMessage(err, 'Dosya indirilemedi'))
      }
    },
    [workspaceId]
  )

  const refreshHistory = useCallback(async () => {
    if (!runId || !workspaceId) {
      setHistory([])
      return
    }
    try {
      const res = await exportApi.listForRun(runId, workspaceId, HISTORY_LIMIT)
      const jobs = (res.data?.exports || []) as ExportStatusResponse[]
      setHistory(jobs)
      // Devral: sayfaya dönüşte süren job varsa poll'a alınır — sonsuza
      // kadar eski ilerlemede görünmez (codex post-review #2)
      const running = jobs.find((job) => isRunning(job.status))
      if (running) {
        setActiveJob((prev) => (prev && !isFinal(prev.status) ? prev : running))
        setBusy(true)
      }
    } catch (err) {
      console.error('Export history failed:', err)
    }
  }, [runId, workspaceId])

  useEffect(() => {
    setActiveJob(null)
    setError(null)
    setBusy(false)
    setPollFailed(false)
    failuresRef.current = 0
    autoDownloadRef.current = false
    void refreshHistory()
  }, [refreshHistory])

  const pollOnce = useCallback(
    async (exportId: string): Promise<void> => {
      if (!workspaceId) return
      try {
        const res = await exportApi.status(exportId, workspaceId)
        // Kapsam değiştiyse (başka job/workspace) geç yanıt yazılmaz
        if (activeIdRef.current !== exportId) return
        const next = res.data as ExportStatusResponse
        failuresRef.current = 0
        setPollFailed(false)
        setActiveJob(next)
        setHistory((prev) => [next, ...prev.filter((job) => job.export_id !== next.export_id)])
        if (isFinal(next.status)) {
          setBusy(false)
          if (next.status === 'failed') {
            setError(next.error_message || 'Dışa aktarım başarısız oldu.')
          } else if (autoDownloadRef.current) {
            // Kullanıcının başlattığı job hazır — beklettirmeden indir
            autoDownloadRef.current = false
            void downloadJob(next)
          }
        }
      } catch {
        if (activeIdRef.current !== exportId) return
        failuresRef.current += 1
        // Durum BİLİNMİYOR (job başarısız sayılmaz): busy/takip korunur, kullanıcı yeniden kontrol eder
        if (failuresRef.current >= MAX_POLL_FAILURES) setPollFailed(true)
      }
    },
    [workspaceId, downloadJob]
  )

  // Aktif job'ı final duruma kadar sıralı poll'la (istekler çakışmaz)
  const activeId = activeJob && !isFinal(activeJob.status) ? activeJob.export_id : null
  activeIdRef.current = activeId
  useEffect(() => {
    if (!activeId || !workspaceId || pollFailed) return
    let cancelled = false
    let timer: number | undefined
    const loop = async () => {
      await pollOnce(activeId)
      if (!cancelled) timer = window.setTimeout(loop, POLL_INTERVAL_MS)
    }
    timer = window.setTimeout(loop, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [activeId, workspaceId, pollFailed, pollOnce])

  // "Durumu tekrar kontrol et": sayaç sıfırlanır, poll sürer
  const recheck = useCallback(() => {
    failuresRef.current = 0
    setPollFailed(false)
    if (activeIdRef.current) void pollOnce(activeIdRef.current)
  }, [pollOnce])

  const startExport = useCallback(
    async (req: ExportRequest) => {
      if (!workspaceId || busy) return
      setBusy(true)
      setError(null)
      setPollFailed(false)
      failuresRef.current = 0
      setActiveJob(null)
      autoDownloadRef.current = true
      try {
        const res = await exportApi.create(req, workspaceId)
        const job = res.data as ExportStatusResponse
        setActiveJob(job)
        setHistory((prev) => [job, ...prev.filter((item) => item.export_id !== job.export_id)])
        if (isFinal(job.status)) {
          setBusy(false)
          autoDownloadRef.current = false
          if (job.status === 'failed') {
            setError(job.error_message || 'Dışa aktarım başarısız oldu.')
          } else {
            void downloadJob(job)
          }
        }
      } catch (err) {
        // 409 bayat havuz kendi mesajıyla; 422 NO_CONTENT detail.message'ı apiErrorMessage getirir
        const stale = policyStaleFromError(err)
        setError(stale ? stale.message : apiErrorMessage(err, 'Dışa aktarım başlatılamadı'))
        setBusy(false)
        autoDownloadRef.current = false
      }
    },
    [workspaceId, busy, downloadJob]
  )

  return {
    activeJob,
    history,
    error,
    busy,
    pollFailed,
    recheck,
    startExport,
    downloadJob,
    refreshHistory,
    setError,
  }
}
