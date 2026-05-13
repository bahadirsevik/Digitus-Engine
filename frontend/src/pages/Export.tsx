import { useEffect, useState } from 'react'
import { Download, FileSpreadsheet, FileText, Table, CheckCircle } from 'lucide-react'
import TaskProgress from '../components/TaskProgress'
import { scoringApi, exportApi, ExportRequest } from '../services/api'
import type { ScoringRun } from '../types/models'
import { useBrandStore } from '../stores/brandStore'
import './Export.css'

type ExportFormat = 'docx' | 'pdf' | 'excel'
type ExportStatus = 'pending' | 'processing' | 'completed' | 'failed'

interface ExportJobStatus {
  export_id: string
  status: ExportStatus
  progress: number
  file_name?: string | null
  error_message?: string | null
  created_at?: string | null
}

export default function Export() {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const [format, setFormat] = useState<ExportFormat>('excel')
  const [channels, setChannels] = useState<string[]>(['ADS', 'SEO', 'SOCIAL'])
  const [includeStaleContent, setIncludeStaleContent] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [success, setSuccess] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeExport, setActiveExport] = useState<ExportJobStatus | null>(null)
  const [recentExports, setRecentExports] = useState<ExportJobStatus[]>([])

  useEffect(() => {
    fetchRuns()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id])

  useEffect(() => {
    if (!selectedRun || !activeWorkspace?.id) {
      setRecentExports([])
      return
    }

    exportApi
      .listForRun(selectedRun, activeWorkspace.id)
      .then((res) => setRecentExports(res.data.exports || []))
      .catch((err) => console.error('Export history failed:', err))
  }, [selectedRun, activeWorkspace?.id])

  useEffect(() => {
    if (!activeExport || !activeWorkspace?.id) return
    if (activeExport.status === 'completed' || activeExport.status === 'failed') return

    const interval = window.setInterval(async () => {
      try {
        const statusRes = await exportApi.status(activeExport.export_id, activeWorkspace.id)
        const nextStatus = statusRes.data as ExportJobStatus
        setActiveExport(nextStatus)
        setRecentExports((prev) => {
          const others = prev.filter((job) => job.export_id !== nextStatus.export_id)
          return [nextStatus, ...others]
        })
        if (nextStatus.status === 'completed' || nextStatus.status === 'failed') {
          setExporting(false)
        }
      } catch (err: unknown) {
        const e = err as { response?: { data?: { detail?: string } }; message?: string }
        setError(e?.response?.data?.detail || e?.message || 'Export durumu alınamadı')
        setExporting(false)
      }
    }, 2000)

    return () => window.clearInterval(interval)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeExport?.export_id, activeExport?.status, activeWorkspace?.id])

  const fetchRuns = async () => {
    if (!activeWorkspace?.id) {
      setRuns([])
      setSelectedRun(null)
      setRecentExports([])
      return
    }

    try {
      const res = await scoringApi.listRuns({ brand_profile_id: activeWorkspace.id })
      setRuns(((res.data as ScoringRun[]) || []).filter((r) => r.status === 'completed'))
    } catch (err) {
      console.error('Export runs failed:', err)
    }
  }

  const toggleChannel = (channel: string) => {
    setChannels((prev) =>
      prev.includes(channel) ? prev.filter((c) => c !== channel) : [...prev, channel]
    )
  }

  const downloadExport = async (job: ExportJobStatus) => {
    if (!activeWorkspace?.id || job.status !== 'completed') return

    try {
      const downloadRes = await exportApi.download(job.export_id, activeWorkspace.id)
      const contentDisposition = downloadRes.headers['content-disposition']
      let filename = job.file_name || `digitus_rapor.${format === 'excel' ? 'xlsx' : format}`
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="?([^";\n]+)"?/)
        if (match) filename = match[1]
      }

      const url = window.URL.createObjectURL(new Blob([downloadRes.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', filename)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)

      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail || e?.message || 'Dosya indirilemedi')
    }
  }

  const handleExport = async () => {
    if (!selectedRun || !activeWorkspace?.id) return

    setExporting(true)
    setSuccess(false)
    setError(null)
    setActiveExport(null)

    try {
      const req: ExportRequest = {
        scoring_run_id: selectedRun,
        format,
        sections: ['all'],
        include_compliance_details: true,
        include_stale_content: includeStaleContent,
      }

      const res = await exportApi.create(req, activeWorkspace.id)
      const job = res.data as ExportJobStatus
      setActiveExport(job)
      setRecentExports((prev) => [job, ...prev.filter((item) => item.export_id !== job.export_id)])
      if (job.status === 'failed') setExporting(false)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      console.error('Export failed:', err)
      const msg = e?.response?.data?.detail || e?.message || 'Bilinmeyen hata'
      setError(`Dışa aktarım sırasında hata oluştu: ${msg}`)
      setExporting(false)
    }
  }

  const formatOptions = [
    { value: 'excel', label: 'Excel (.xlsx)', icon: Table },
    { value: 'docx', label: 'Word (.docx)', icon: FileText },
    { value: 'pdf', label: 'PDF (.pdf)', icon: FileSpreadsheet },
  ]

  return (
    <div className="export-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Dışa Aktarım</h1>
          <p>Üretilen verileri dosya olarak indirin</p>
        </div>
      </header>

      <div className="export-form glass-card">
        <div className="form-section">
          <h3>1. Skorlama Çalışması</h3>
          <div className="run-options">
            {runs.length === 0 ? (
              <p className="empty-text">Tamamlanmış skorlama çalışması yok</p>
            ) : (
              runs.map((run) => (
                <button
                  key={run.id}
                  className={`option-btn ${selectedRun === run.id ? 'active' : ''}`}
                  onClick={() => setSelectedRun(run.id)}
                >
                  {run.run_name || `Çalışma #${run.id}`}
                </button>
              ))
            )}
          </div>
        </div>

        <div className="form-section">
          <h3>2. Dosya Formatı</h3>
          <div className="format-options">
            {formatOptions.map((opt) => (
              <button
                key={opt.value}
                className={`format-btn ${format === opt.value ? 'active' : ''}`}
                onClick={() => setFormat(opt.value as ExportFormat)}
              >
                <opt.icon size={24} />
                <span>{opt.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="form-section">
          <h3>3. Kanallar</h3>
          <div className="channel-options">
            {['ADS', 'SEO', 'SOCIAL'].map((channel) => (
              <label key={channel} className="channel-checkbox">
                <input
                  type="checkbox"
                  checked={channels.includes(channel)}
                  onChange={() => toggleChannel(channel)}
                />
                <span className={`badge badge-${channel.toLowerCase()}`}>{channel}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="form-section">
          <label className="channel-checkbox">
            <input
              type="checkbox"
              checked={includeStaleContent}
              onChange={(e) => setIncludeStaleContent(e.target.checked)}
            />
            <span>Güncel değil işaretlenen içerikleri dahil et</span>
          </label>
        </div>

        <div className="export-action">
          <button
            className="btn btn-primary btn-large"
            onClick={handleExport}
            disabled={!selectedRun || channels.length === 0 || exporting}
          >
            <Download size={20} />
            {exporting ? 'Oluşturuluyor...' : 'Dışa Aktar'}
          </button>

          {success && (
            <div className="success-message">
              <CheckCircle size={20} />
              Dışa aktarım başarıyla tamamlandı!
            </div>
          )}

          {error && <div className="export-error">{error}</div>}
        </div>

        {activeExport && (
          <div className="export-progress">
            <TaskProgress
              taskId={activeExport.export_id}
              status={activeExport.status === 'processing' ? 'running' : activeExport.status}
              progress={activeExport.progress}
              errorMessage={activeExport.error_message || undefined}
            />
            {activeExport.status === 'completed' && (
              <button className="btn btn-secondary" onClick={() => downloadExport(activeExport)}>
                <Download size={18} />
                İndir
              </button>
            )}
          </div>
        )}
      </div>

      {recentExports.length > 0 && (
        <div className="export-history glass-card">
          <h3>Export Geçmişi</h3>
          <div className="export-history-list">
            {recentExports.map((job) => (
              <div key={job.export_id} className="export-history-row">
                <div>
                  <span className={`export-status export-status-${job.status}`}>{job.status}</span>
                  <span className="export-file">{job.file_name || job.export_id.slice(0, 8)}</span>
                </div>
                <span className="export-progress-value">{job.progress}%</span>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={() => downloadExport(job)}
                  disabled={job.status !== 'completed'}
                >
                  <Download size={14} />
                  İndir
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="export-info glass-card">
        <h3>Dışa Aktarım Bilgisi</h3>
        <ul>
          <li>
            <strong>Excel:</strong> Kanal bazlı tam veri tablosu
          </li>
          <li>
            <strong>Word:</strong> Özet rapor ve tablolar
          </li>
          <li>
            <strong>PDF:</strong> Yazdırılabilir rapor çıkışı
          </li>
        </ul>
      </div>
    </div>
  )
}
