import { useState, useEffect, useRef } from 'react'
import { Plus, Upload, Trash2, Search, RefreshCw, FileSpreadsheet, X, FileUp, CheckCircle } from 'lucide-react'
import { keywordsApi, KeywordCreate } from '../services/api'
import './Keywords.css'

interface Keyword {
  id: number
  keyword: string
  sector?: string
  target_market?: string
  monthly_volume?: number
  trend_12m?: number
  trend_3m?: number
  competition_score?: number
  is_active: boolean
}

export default function Keywords() {
  const [keywords, setKeywords] = useState<Keyword[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [showModal, setShowModal] = useState(false)
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [newKeyword, setNewKeyword] = useState<KeywordCreate>({
    keyword: '',
    sector: '',
    monthly_volume: 1000,
    trend_12m: 10,
    trend_3m: 15,
    competition_score: 0.5
  })
  
  useEffect(() => {
    fetchKeywords()
  }, [])
  
  const fetchKeywords = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await keywordsApi.list({ limit: 2000 })
      setKeywords(res.data.items || [])
    } catch (err: any) {
      console.error('Error fetching keywords:', err)
      setError('API bağlantısı kurulamadı. Backend çalışıyor mu?')
      // Demo data for testing without backend
      setKeywords([
        { id: 1, keyword: 'laptop satın al', sector: 'teknoloji', monthly_volume: 12000, trend_12m: 15, trend_3m: 22, competition_score: 0.72, is_active: true },
        { id: 2, keyword: 'en iyi telefon 2024', sector: 'teknoloji', monthly_volume: 8500, trend_12m: 45, trend_3m: 80, competition_score: 0.65, is_active: true },
        { id: 3, keyword: 'python öğren', sector: 'eğitim', monthly_volume: 5200, trend_12m: 30, trend_3m: 25, competition_score: 0.45, is_active: true },
      ])
    }
    setLoading(false)
  }
  
  const handleCreate = async () => {
    if (!newKeyword.keyword.trim()) return
    try {
      await keywordsApi.create(newKeyword)
      setShowModal(false)
      setNewKeyword({ keyword: '', sector: '', monthly_volume: 1000, trend_12m: 10, trend_3m: 15, competition_score: 0.5 })
      fetchKeywords()
    } catch (err) {
      // Add locally for demo
      const newItem: Keyword = {
        id: Date.now(),
        ...newKeyword,
        is_active: true
      }
      setKeywords([...keywords, newItem])
      setShowModal(false)
    }
  }
  
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      setUploadedFile(file)
      setUploadStatus(null)
    }
  }
  
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }
  
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)
    const file = e.dataTransfer.files?.[0]
    if (file && (file.name.endsWith('.csv') || file.name.endsWith('.txt'))) {
      setUploadedFile(file)
      setUploadStatus(null)
    }
  }
  
  const handleBulkImport = async () => {
    if (!uploadedFile) return
    
    setUploadStatus('Dosya işleniyor...')
    
    const text = await uploadedFile.text()
    const lines = text.split('\n').filter((line: string) => line.trim())
    
    // Find header row - Google Ads CSVs have metadata rows before header
    // Header row contains "Keyword" and "Currency" columns
    let startIndex = 0
    for (let i = 0; i < Math.min(10, lines.length); i++) {
      const lowerLine = lines[i]?.toLowerCase() || ''
      if (lowerLine.includes('keyword') && lowerLine.includes('currency')) {
        startIndex = i + 1  // Skip header, start from next row
        break
      }
    }
    
    const allParsed = lines.slice(startIndex).map((line: string) => {
      const parts = line.split('\t').length > 1 ? line.split('\t') : line.split(',')
      const rawCompetition = parseFloat(parts[6]?.replace(/[^0-9.]/g, ''))
      
      // Helper to parse trend values, handling infinity (∞) and extreme values
      const parseTrend = (val: string | undefined): number => {
        if (!val) return 0
        // Check for infinity symbol or text
        if (val.includes('∞') || val.toLowerCase().includes('inf')) return 0
        const num = parseFloat(val.replace(/[^0-9.-]/g, ''))
        // Cap extreme values to prevent database overflow
        if (isNaN(num) || !isFinite(num) || Math.abs(num) > 9999) return 0
        return num
      }
      
      return {
        keyword: parts[0]?.trim().replace(/^"|"$/g, '') || '',
        sector: parts[1]?.trim().replace(/^"|"$/g, '') || undefined,
        monthly_volume: (() => { const v = parseInt(parts[2]?.replace(/[^0-9]/g, '')); return isNaN(v) ? 1000 : v; })(),
        trend_3m: parseTrend(parts[3]),
        trend_12m: parseTrend(parts[4]),
        competition_score: rawCompetition / 100,
        _rawCompetition: rawCompetition,
      }
    })
    
    const totalParsed = allParsed.filter(kw => kw.keyword).length
    const keywordsToImport = allParsed
      .filter(kw => kw.keyword && !isNaN(kw._rawCompetition) && kw._rawCompetition > 0)
      .map(({ _rawCompetition, ...rest }) => rest)
    
    const skippedCount = totalParsed - keywordsToImport.length
    
    try {
      // Send to backend API
      await keywordsApi.import(keywordsToImport)
      setUploadStatus(`✅ ${keywordsToImport.length} keyword kaydedildi. (${skippedCount} satır rekabet verisi eksik/0 olduğu için atlandı)`)
      
      // Refresh from API
      fetchKeywords()
    } catch (err) {
      console.error('API import failed, adding locally:', err)
      // Fallback: add locally if API fails
      const localKeywords: Keyword[] = keywordsToImport.map((kw, idx) => ({
        id: Date.now() + idx,
        ...kw,
        is_active: true
      }))
      setKeywords([...keywords, ...localKeywords])
      setUploadStatus(`${keywordsToImport.length} keyword eklendi (yerel mod)`)
    }
    
    setUploadedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
    
    setTimeout(() => {
      setShowUploadModal(false)
      setUploadStatus(null)
    }, 2000)
  }
  
  const handleDelete = async (id: number) => {
    if (!confirm('Bu keyword silinecek. Emin misiniz?')) return
    try {
      await keywordsApi.delete(id)
      fetchKeywords()
    } catch (err) {
      // Remove locally for demo
      setKeywords(keywords.filter(kw => kw.id !== id))
    }
  }
  
  const filteredKeywords = keywords.filter(kw => 
    kw.keyword.toLowerCase().includes(search.toLowerCase()) ||
    (kw.sector && kw.sector.toLowerCase().includes(search.toLowerCase()))
  )
  
  return (
    <div className="keywords-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Keywords</h1>
          <p>Anahtar kelime yönetimi</p>
        </div>
        <div className="header-actions">
          <button className="btn btn-secondary" onClick={fetchKeywords} title="Yenile">
            <RefreshCw size={18} className={loading ? 'spin' : ''} />
          </button>
          <button 
            className="btn btn-danger" 
            onClick={async () => {
              if (!confirm('TÜM KEYWORDLERİ SİLMEK İSTEDİĞİNİZE EMİN MİSİNİZ? Bu işlem geri alınamaz!')) return
              try {
                await keywordsApi.deleteAll()
                fetchKeywords()
              } catch (err) {
                setKeywords([])
              }
            }}
            title="Tümünü Sil"
          >
            <Trash2 size={18} />
            Tümünü Sil
          </button>
          <button className="btn btn-secondary" onClick={() => setShowUploadModal(true)}>
            <Upload size={18} />
            İçe Aktar
          </button>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            <Plus size={18} />
            Ekle
          </button>
        </div>
      </header>
      
      {/* Error Banner */}
      {error && (
        <div className="error-banner">
          <span>⚠️ {error}</span>
          <span className="demo-badge">Demo Modu</span>
        </div>
      )}
      
      {/* Search */}
      <div className="search-bar">
        <Search size={20} />
        <input 
          type="text" 
          className="input" 
          placeholder="Keyword veya sektör ara..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      
      {/* Stats */}
      <div className="stats-row">
        <div className="stat-item">
          <span className="stat-value">{keywords.length}</span>
          <span className="stat-label">Toplam</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{filteredKeywords.length}</span>
          <span className="stat-label">Filtrelenen</span>
        </div>
      </div>
      
      {/* Keywords Table */}
      <div className="table-container">
        <table className="table">
          <thead>
            <tr>
              <th>Keyword</th>
              <th>Sektör</th>
              <th>Hacim</th>
              <th>Trend 12M</th>
              <th>Trend 3M</th>
              <th>Rekabet</th>
              <th>İşlemler</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="loading-cell">
                  <RefreshCw size={24} className="spin" />
                  Yükleniyor...
                </td>
              </tr>
            ) : filteredKeywords.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-cell">
                  <FileSpreadsheet size={48} />
                  <p>Keyword bulunamadı</p>
                  <button className="btn btn-primary" onClick={() => setShowModal(true)}>
                    <Plus size={16} /> İlk Keyword'ü Ekle
                  </button>
                </td>
              </tr>
            ) : (
              filteredKeywords.map(kw => (
                <tr key={kw.id}>
                  <td><strong>{kw.keyword}</strong></td>
                  <td><span className="sector-badge">{kw.sector || '-'}</span></td>
                  <td>{kw.monthly_volume?.toLocaleString() || '-'}</td>
                  <td className={kw.trend_12m && kw.trend_12m > 0 ? 'positive' : 'negative'}>
                    {kw.trend_12m ? `${kw.trend_12m > 0 ? '+' : ''}${kw.trend_12m}%` : '-'}
                  </td>
                  <td className={kw.trend_3m && kw.trend_3m > 0 ? 'positive' : 'negative'}>
                    {kw.trend_3m ? `${kw.trend_3m > 0 ? '+' : ''}${kw.trend_3m}%` : '-'}
                  </td>
                  <td>
                    <div className="competition-bar">
                      <div 
                        className="competition-fill" 
                        style={{ width: `${(Number(kw.competition_score) || 0) * 100}%` }}
                      ></div>
                      <span>{typeof kw.competition_score === 'number' ? kw.competition_score.toFixed(2) : (parseFloat(String(kw.competition_score)) || 0).toFixed(2)}</span>
                    </div>
                  </td>
                  <td>
                    <button 
                      className="btn btn-icon btn-danger" 
                      onClick={() => handleDelete(kw.id)}
                      title="Sil"
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      
      {/* Add Modal */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal glass-card" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Yeni Keyword Ekle</h2>
              <button className="btn btn-icon" onClick={() => setShowModal(false)}>
                <X size={20} />
              </button>
            </div>
            
            <div className="form-group">
              <label>Keyword *</label>
              <input 
                className="input" 
                value={newKeyword.keyword}
                onChange={e => setNewKeyword({...newKeyword, keyword: e.target.value})}
                placeholder="laptop satın al"
              />
            </div>
            
            <div className="form-row">
              <div className="form-group">
                <label>Sektör</label>
                <input 
                  className="input" 
                  value={newKeyword.sector}
                  onChange={e => setNewKeyword({...newKeyword, sector: e.target.value})}
                  placeholder="teknoloji"
                />
              </div>
              <div className="form-group">
                <label>Aylık Hacim</label>
                <input 
                  type="number"
                  className="input" 
                  value={newKeyword.monthly_volume}
                  onChange={e => setNewKeyword({...newKeyword, monthly_volume: parseInt(e.target.value)})}
                />
              </div>
            </div>
            
            <div className="form-row">
              <div className="form-group">
                <label>Trend 12M (%)</label>
                <input 
                  type="number"
                  className="input" 
                  value={newKeyword.trend_12m}
                  onChange={e => setNewKeyword({...newKeyword, trend_12m: parseFloat(e.target.value)})}
                />
              </div>
              <div className="form-group">
                <label>Trend 3M (%)</label>
                <input 
                  type="number"
                  className="input" 
                  value={newKeyword.trend_3m}
                  onChange={e => setNewKeyword({...newKeyword, trend_3m: parseFloat(e.target.value)})}
                />
              </div>
            </div>
            
            <div className="form-group">
              <label>Rekabet Skoru (0-1)</label>
              <input 
                type="number"
                step="0.01"
                min="0"
                max="1"
                className="input" 
                value={newKeyword.competition_score}
                onChange={e => setNewKeyword({...newKeyword, competition_score: parseFloat(e.target.value)})}
              />
            </div>
            
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setShowModal(false)}>
                İptal
              </button>
              <button className="btn btn-primary" onClick={handleCreate}>
                Ekle
              </button>
            </div>
          </div>
        </div>
      )}
      
      {/* Upload Modal */}
      {showUploadModal && (
        <div className="modal-overlay" onClick={() => setShowUploadModal(false)}>
          <div className="modal glass-card modal-large" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>CSV Dosyası Yükle</h2>
              <button className="btn btn-icon" onClick={() => setShowUploadModal(false)}>
                <X size={20} />
              </button>
            </div>
            
            <div className="upload-info">
              <p><strong>CSV Formatı:</strong></p>
              <code>keyword, sektör, hacim, trend12m, trend3m, rekabet</code>
              <p style={{marginTop: '8px', fontSize: '0.8rem', color: 'var(--text-muted)'}}>
                İlk satır başlık ise otomatik atlanır
              </p>
            </div>
            
            <div 
              className={`drop-zone ${dragActive ? 'active' : ''} ${uploadedFile ? 'has-file' : ''}`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input 
                type="file" 
                ref={fileInputRef}
                onChange={handleFileSelect}
                accept=".csv,.txt"
                style={{display: 'none'}}
              />
              {uploadedFile ? (
                <div className="file-selected">
                  <FileUp size={32} />
                  <span className="file-name">{uploadedFile.name}</span>
                  <span className="file-size">({(uploadedFile.size / 1024).toFixed(1)} KB)</span>
                </div>
              ) : (
                <div className="drop-content">
                  <Upload size={48} />
                  <p>CSV dosyasını buraya sürükleyin</p>
                  <span>veya tıklayarak seçin</span>
                </div>
              )}
            </div>
            
            {uploadStatus && (
              <div className="upload-success">
                <CheckCircle size={20} />
                {uploadStatus}
              </div>
            )}
            
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => {
                setShowUploadModal(false)
                setUploadedFile(null)
              }}>
                İptal
              </button>
              <button 
                className="btn btn-primary" 
                onClick={handleBulkImport}
                disabled={!uploadedFile}
              >
                <Upload size={16} />
                Yükle
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
