import { useState, useEffect } from 'react'
import { Download, FileText, Table, FileSpreadsheet, CheckCircle } from 'lucide-react'
import { scoringApi, exportApi, ExportRequest } from '../services/api'
import './Export.css'

export default function Export() {
  const [runs, setRuns] = useState<any[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const [format, setFormat] = useState<'docx' | 'pdf' | 'excel'>('excel')
  const [channels, setChannels] = useState<string[]>(['ADS', 'SEO', 'SOCIAL'])
  const [exporting, setExporting] = useState(false)
  const [success, setSuccess] = useState(false)

  useEffect(() => {
    fetchRuns()
  }, [])

  const fetchRuns = async () => {
    try {
      const res = await scoringApi.listRuns()
      setRuns(res.data.filter((r: any) => r.status === 'completed') || [])
    } catch (error) {
      console.error('Dışa aktarım çalışmaları yüklenemedi:', error)
    }
  }

  const toggleChannel = (channel: string) => {
    if (channels.includes(channel)) {
      setChannels(channels.filter(c => c !== channel))
    } else {
      setChannels([...channels, channel])
    }
  }

  const handleExport = async () => {
    if (!selectedRun) return

    setExporting(true)
    setSuccess(false)

    try {
      const req: ExportRequest = {
        scoring_run_id: selectedRun,
        format,
        sections: ['all'],
        include_compliance_details: true
      }

      const res = await exportApi.create(req)
      const exportId = res.data.export_id

      let attempts = 0
      const maxAttempts = 60

      while (attempts < maxAttempts) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        const statusRes = await exportApi.status(exportId)
        const status = statusRes.data.status

        if (status === 'completed') {
          const downloadRes = await exportApi.download(exportId)

          const contentDisposition = downloadRes.headers['content-disposition']
          let filename = `digitus_rapor.${format === 'excel' ? 'xlsx' : format}`
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
          break
        } else if (status === 'failed') {
          const errMsg = statusRes.data.error_message || 'Dışa aktarım başarısız'
          alert(`Dışa aktarım hatası: ${errMsg}`)
          break
        }

        attempts++
      }

      if (attempts >= maxAttempts) {
        alert('Dışa aktarım zaman aşımına uğradı.')
      }

    } catch (error: any) {
      console.error('Dışa aktarım hatası:', error)
      const msg = error?.response?.data?.detail || error?.message || 'Bilinmeyen hata'
      alert(`Dışa aktarım sırasında hata oluştu: ${msg}`)
    }

    setExporting(false)
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
              runs.map(run => (
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
            {formatOptions.map(opt => (
              <button
                key={opt.value}
                className={`format-btn ${format === opt.value ? 'active' : ''}`}
                onClick={() => setFormat(opt.value as typeof format)}
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
            {['ADS', 'SEO', 'SOCIAL'].map(channel => (
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
        </div>
      </div>

      <div className="export-info glass-card">
        <h3>Dışa Aktarım Bilgisi</h3>
        <ul>
          <li><strong>Excel:</strong> Kanal bazlı tam veri tablosu</li>
          <li><strong>Word:</strong> Özet rapor ve tablolar</li>
          <li><strong>PDF:</strong> Yazdırılabilir rapor çıkışı</li>
        </ul>
      </div>
    </div>
  )
}
