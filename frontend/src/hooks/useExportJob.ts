// Plan v4 (export dağıtımı): ExportJob yaşam döngüsü — create → poll → download.
// Export.tsx'teki mantığın ortak hook'a çıkarılmış hali; ExportMenu kullanır.
// Codex post-review: geçmişteki pending/processing job devralınıp poll'lanır;
// kullanıcının bu oturumda başlattığı job tamamlanınca OTOMATİK indirilir.
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  apiErrorMessage,
  downloadBlobResponse,
  exportApi,
  ExportRequest,
  ExportStatusResponse,
  policyStaleFromError,
} from '../services/api'

const POLL_INTERVAL_MS = 2000
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
    autoDownloadRef.current = false
    void refreshHistory()
  }, [refreshHistory])

  // Aktif job'ı final duruma kadar poll'la; geçmiş listesini de tazele
  useEffect(() => {
    if (!activeJob || !workspaceId || isFinal(activeJob.status)) return

    const interval = window.setInterval(async () => {
      try {
        const res = await exportApi.status(activeJob.export_id, workspaceId)
        const next = res.data as ExportStatusResponse
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
      } catch (err) {
        setError(apiErrorMessage(err, 'Export durumu alınamadı'))
        setBusy(false)
      }
    }, POLL_INTERVAL_MS)

    return () => window.clearInterval(interval)
  }, [activeJob, workspaceId, downloadJob])

  const startExport = useCallback(
    async (req: ExportRequest) => {
      if (!workspaceId || busy) return
      setBusy(true)
      setError(null)
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

  return { activeJob, history, error, busy, startExport, downloadJob, refreshHistory, setError }
}
