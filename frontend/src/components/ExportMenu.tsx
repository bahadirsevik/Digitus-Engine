// Plan v4 (export dağıtımı): kanal sayfası header'ındaki "İndir" menüsü.
// Kanal havuzu (Excel, senkron) + kanal içerikleri + tam rapor (ExportJob) +
// son dışa aktarımlar (tür etiketli). Dışa Aktarım sayfasının yerini alır.
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Download, RefreshCw } from 'lucide-react'
import {
  apiErrorMessage,
  channelsApi,
  downloadBlobResponse,
  policyStaleFromError,
} from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import { useExportJob } from '../hooks/useExportJob'
import { FORMAT_LABEL, jobTypeLabel } from './exportLabels'
import './ExportMenu.css'

type ChannelName = 'ADS' | 'SEO' | 'SOCIAL'
type JobFormat = 'excel' | 'docx' | 'pdf'

// Kanal → ExportJob section eşlemesi (backend sözleşmesi)
const CHANNEL_SECTION: Record<ChannelName, string> = {
  ADS: 'ads',
  SEO: 'seo_content',
  SOCIAL: 'social',
}

// Menü etiketi: ADS'te görüntülenen taslak olsa bile AKTİF set iner — açık yazılır
const CONTENT_LABEL: Record<ChannelName, string> = {
  ADS: 'İçerikler — aktif reklam seti',
  SEO: 'İçerikler — SEO+GEO yazıları',
  SOCIAL: 'İçerikler — sosyal paketler',
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'kuyrukta',
  processing: 'hazırlanıyor',
  completed: 'hazır',
  failed: 'hata',
}

interface Props {
  channel: ChannelName
  runId: number | null
  disabled?: boolean
}

export default function ExportMenu({ channel, runId, disabled }: Props) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const [open, setOpen] = useState(false)
  const [poolError, setPoolError] = useState<string | null>(null)
  const [poolBusy, setPoolBusy] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  const { activeJob, history, error, busy, startExport, downloadJob, refreshHistory, setError } =
    useExportJob(runId, activeWorkspace?.id)

  // Menü dışına tıklayınca kapan
  useEffect(() => {
    if (!open) return
    const onClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const handlePoolDownload = async () => {
    if (!runId || !activeWorkspace?.id || poolBusy) return
    setPoolBusy(true)
    setPoolError(null)
    try {
      const res = await channelsApi.exportPoolXlsx(runId, channel, activeWorkspace.id)
      downloadBlobResponse(res, `kanal_havuzu_${channel.toLowerCase()}_run${runId}.xlsx`)
    } catch (err) {
      // Blob response'ta hata gövdesi JSON olarak gelmeyebilir — 409 için ortak mesaj
      const stale = policyStaleFromError(err)
      setPoolError(
        stale
          ? stale.message
          : apiErrorMessage(err, 'Kanal havuzu indirilemedi — havuz hazır olmayabilir.')
      )
    } finally {
      setPoolBusy(false)
    }
  }

  const handleJobStart = (sections: string[], format: JobFormat) => {
    if (!runId) return
    setPoolError(null)
    void startExport({
      scoring_run_id: runId,
      format,
      sections,
      include_compliance_details: true,
      include_stale_content: false,
    })
  }

  const isDisabled = disabled || !runId || !activeWorkspace?.id
  const message = poolError || error

  return (
    <div className="exm-root" ref={rootRef}>
      <button
        type="button"
        className="chx-btn exm-trigger"
        onClick={() => {
          const opening = !open
          setOpen(opening)
          setPoolError(null)
          setError(null)
          // Kapat-aç geçmişi listeden geri getirir + süren job devralınır
          if (opening) void refreshHistory()
        }}
        disabled={isDisabled}
        title={isDisabled ? 'İndirme için kanal ataması tamamlanmış bir analiz seçin' : undefined}
      >
        <Download size={14} />
        İndir
        <ChevronDown size={13} className={`exm-chev${open ? ' is-open' : ''}`} />
      </button>

      {open && !isDisabled && (
        <div className="exm-panel">
          <button
            type="button"
            className="exm-row exm-row-btn"
            onClick={() => void handlePoolDownload()}
            disabled={poolBusy}
          >
            <span>Kanal havuzu</span>
            <span className="exm-format-chip">{poolBusy ? '...' : 'Excel'}</span>
          </button>

          <div className="exm-row">
            <span className="exm-row-label">{CONTENT_LABEL[channel]}</span>
            <span className="exm-format-group">
              {(['excel', 'docx', 'pdf'] as JobFormat[]).map((format) => (
                <button
                  key={format}
                  type="button"
                  className="exm-format-chip exm-format-btn"
                  onClick={() => handleJobStart([CHANNEL_SECTION[channel]], format)}
                  disabled={busy}
                >
                  {FORMAT_LABEL[format]}
                </button>
              ))}
            </span>
          </div>

          <div className="exm-row">
            <span className="exm-row-label">Tam rapor</span>
            <span className="exm-format-group">
              {(['excel', 'docx', 'pdf'] as JobFormat[]).map((format) => (
                <button
                  key={format}
                  type="button"
                  className="exm-format-chip exm-format-btn"
                  onClick={() => handleJobStart(['all'], format)}
                  disabled={busy}
                >
                  {FORMAT_LABEL[format]}
                </button>
              ))}
            </span>
          </div>

          {busy && activeJob && (
            <div className="exm-progress">
              <RefreshCw size={13} className="animate-spin" />
              <span>
                {jobTypeLabel(activeJob)} hazırlanıyor... %{activeJob.progress ?? 0}
              </span>
            </div>
          )}

          {message && <div className="exm-error">{message}</div>}

          {history.length > 0 && (
            <div className="exm-history">
              <div className="exm-history-title">Son dışa aktarımlar</div>
              {history.map((job) => (
                <div key={job.export_id} className="exm-history-row">
                  <div className="exm-history-info">
                    <span className="exm-history-label">{jobTypeLabel(job)}</span>
                    <span className={`exm-status exm-status-${job.status}`}>
                      {STATUS_LABEL[job.status] || job.status}
                      {job.status === 'processing' || job.status === 'pending'
                        ? ` %${job.progress ?? 0}`
                        : ''}
                    </span>
                    {job.policy_outdated && (
                      <span
                        className="exm-outdated"
                        title="Bu rapor üretiminden sonra politika/profil değişti — güncel rapor için yeniden export alın"
                      >
                        eski politika
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="exm-format-chip exm-format-btn"
                    onClick={() => void downloadJob(job)}
                    disabled={job.status !== 'completed'}
                  >
                    İndir
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
