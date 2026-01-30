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
      console.error('Error:', error)
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
        channels,
        include_scores: true
      }
      
      const res = await exportApi.create(req)
      console.log('Export created:', res.data)
      
      // Extract filename from path
      // Handle both Windows (\) and Unix (/) paths
      const fullPath = res.data.filepath
      const filename = fullPath.split(/[/\\]/).pop()
      
      if (filename) {
        // Trigger download
        const downloadRes = await exportApi.download(filename)
        
        // Create blob link to download
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
      }
      
    } catch (error) {
      console.error('Export error:', error)
      alert('Export sırasında bir hata oluştu.')
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
          <h1>Export</h1>
          <p>Verileri dışa aktarın</p>
        </div>
      </header>
      
      <div className="export-form glass-card">
        {/* Run Selection */}
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
                  {run.run_name || `Run #${run.id}`}
                </button>
              ))
            )}
          </div>
        </div>
        
        {/* Format Selection */}
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
        
        {/* Channel Selection */}
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
        
        {/* Export Button */}
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
              Export başarıyla oluşturuldu!
            </div>
          )}
        </div>
      </div>
      
      {/* Export Info */}
      <div className="export-info glass-card">
        <h3>Export Bilgisi</h3>
        <ul>
          <li><strong>Excel:</strong> Her kanal için ayrı sayfa, tam veri</li>
          <li><strong>Word:</strong> Tablo formatında özet rapor</li>
          <li><strong>PDF:</strong> Yazdırılabilir profesyonel rapor</li>
        </ul>
      </div>
    </div>
  )
}
