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
    
    // Auto-detect separator from multiple lines (not just first line)
    // Google KP exports may have metadata rows before the header
    const detectSep = (sampleLines: string[]): string => {
      const seps = ['\t', ';', ',']
      for (const s of seps) {
        const counts = sampleLines.map(l => l.split(s).length)
        const maxCols = Math.max(...counts)
        if (maxCols >= 4 && counts.filter(c => c >= 4).length >= Math.min(2, sampleLines.length)) {
          return s
        }
      }
      return ','
    }
    let sep = detectSep(lines.slice(0, Math.min(10, lines.length)))

    // Find header row by checking for known column names (Turkish + English)
    const headerPatterns = [
      'keyword', 'avg. monthly searches', 'monthly searches',
      'competition', 'üç aylık', 'yıldan yıla', 'three month change',
      'year over year', 'rekabet'
    ]
    let headerIndex = -1
    for (let i = 0; i < Math.min(10, lines.length); i++) {
      const lower = lines[i]?.toLowerCase() || ''
      const matchCount = headerPatterns.filter(p => lower.includes(p)).length
      if (matchCount >= 2) {
        headerIndex = i
        // Re-detect separator from header row specifically
        sep = detectSep([lines[i]])
        break
      }
    }

    if (headerIndex === -1) {
      // Fallback: use line 0 if no header found
      headerIndex = 0
    }
    
    // Parse header to find column indices dynamically
    const headerCols = lines[headerIndex].split(sep).map((h: string) => h.trim().toLowerCase().replace(/"/g, ''))
    
    // Map column names to indices
    const colMap: Record<string, number> = {}
    headerCols.forEach((col: string, idx: number) => {
      if (col.includes('keyword') && !col.includes('negative')) colMap.keyword = idx
      if (col.includes('avg') && col.includes('search')) colMap.volume = idx
      if (col === 'competition' || col === 'rekabet') colMap.competition_text = idx
      if (col.includes('competition') && col.includes('indexed') || col.includes('rekabet') && col.includes('endeks')) colMap.competition_idx = idx
      if (col.includes('üç aylık') || col.includes('three month')) colMap.trend3m = idx
      if (col.includes('yıldan yıla') || col.includes('year over year') || col.includes('yildan yila')) colMap.trend12m = idx
    })
    
    // Fallback for simple CSV format: keyword, sector, volume, trend3m, trend12m, competition
    const isSimpleFormat = colMap.keyword === undefined
    
    // Parse helper: handle comma decimal (Turkish locale "0,53" → 0.53)
    const parseNum = (val: string | undefined): number => {
      if (!val) return 0
      // Remove quotes, whitespace
      val = val.trim().replace(/"/g, '')
      // Replace comma with dot for decimal
      val = val.replace(',', '.')
      // Remove non-numeric except dot and minus
      val = val.replace(/[^0-9.\-]/g, '')
      const n = parseFloat(val)
      return isNaN(n) ? 0 : n
    }
    
    // Competition text to numeric score (0-1)
    const parseCompetition = (text: string | undefined, indexed: string | undefined): number => {
      // Try indexed value first (0-100)
      if (indexed) {
        const val = parseNum(indexed)
        if (val > 0) return val / 100
      }
      // Fallback: text labels
      if (!text) return 0.5
      const lower = text.trim().toLowerCase().replace(/"/g, '')
      if (lower === 'düşük' || lower === 'dusuk' || lower === 'low') return 0.20
      if (lower === 'orta' || lower === 'medium') return 0.50
      if (lower === 'yüksek' || lower === 'yuksek' || lower === 'high') return 0.80
      // Numeric string
      const num = parseNum(text)
      if (num > 1) return num / 100  // 0-100 range
      if (num > 0) return num
      return 0.5
    }
    
    // Helper to parse trend values, handling infinity and extreme values
    const parseTrend = (val: string | undefined): number => {
      if (!val) return 0
      val = val.trim().replace(/"/g, '')
      if (val.includes('∞') || val.toLowerCase().includes('inf')) return 0
      // Handle percentage format: "22%" or "-18%"
      const num = parseNum(val)
      if (Math.abs(num) > 9999) return 0
      return num
    }
    
    const dataLines = lines.slice(headerIndex + 1)
    
    const allParsed = dataLines.map((line: string) => {
      const parts = line.split(sep)
      
      // Skip empty rows (only separators)
      const hasContent = parts.some((p: string) => p.trim().replace(/"/g, ''))
      if (!hasContent) return null
      
      if (isSimpleFormat) {
        // Simple format: keyword, sector, volume, trend3m, trend12m, competition
        const rawComp = parseNum(parts[5])
        return {
          keyword: parts[0]?.trim().replace(/^"|"$/g, '') || '',
          sector: parts[1]?.trim().replace(/^"|"$/g, '') || undefined,
          monthly_volume: (() => { const v = parseInt(parts[2]?.replace(/[^0-9]/g, '')); return isNaN(v) ? 1 : v; })(),
          trend_3m: parseTrend(parts[3]),
          trend_12m: parseTrend(parts[4]),
          competition_score: rawComp > 1 ? rawComp / 100 : rawComp,
          _valid: true,
        }
      }
      
      // Google Keyword Planner format
      const kw = parts[colMap.keyword ?? 0]?.trim().replace(/^"|"$/g, '') || ''
      const volume = parseInt((parts[colMap.volume ?? 1] || '0').replace(/[^0-9]/g, ''))
      const trend3m = parseTrend(parts[colMap.trend3m ?? -1])
      const trend12m = parseTrend(parts[colMap.trend12m ?? -1])
      const compScore = parseCompetition(
        parts[colMap.competition_text ?? -1],
        parts[colMap.competition_idx ?? -1]
      )
      
      return {
        keyword: kw,
        sector: undefined,
        monthly_volume: isNaN(volume) || volume === 0 ? 1 : volume,
        trend_3m: trend3m,
        trend_12m: trend12m,
        competition_score: compScore,
        _valid: true,
      }
    }).filter(Boolean)
    
    const totalParsed = allParsed.filter((kw: any) => kw?.keyword).length
    const keywordsToImport = allParsed
      .filter((kw: any) => kw?.keyword)
      .map(({ _valid, ...rest }: any) => ({
        ...rest,
        // Clamp competition_score to 0-1 range (safety net)
        competition_score: Math.min(1, Math.max(0, rest.competition_score > 1 ? rest.competition_score / 100 : rest.competition_score)),
        // Ensure monthly_volume is positive integer
        monthly_volume: Math.max(1, Math.round(rest.monthly_volume || 1)),
      }))
    
    const skippedCount = totalParsed - keywordsToImport.length
    
    try {
      // Send to backend API
      await keywordsApi.import(keywordsToImport)
      setUploadStatus(`✅ ${keywordsToImport.length} keyword kaydedildi.${skippedCount > 0 ? ` (${skippedCount} satır geçersiz veri nedeniyle atlandı)` : ''}`)
      
      // Refresh from API
      fetchKeywords()
    } catch (err) {
      console.error('API import failed, adding locally:', err)
      // Fallback: add locally if API fails
      const localKeywords: Keyword[] = keywordsToImport.map((kw: any, idx: number) => ({
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
    if (!confirm('Bu anahtar kelime silinecek. Emin misiniz?')) return
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
          <h1>Anahtar Kelimeler</h1>
          <p>Anahtar kelime yönetimi</p>
        </div>
        <div className="header-actions">
          <button className="btn btn-secondary" onClick={fetchKeywords} title="Yenile">
            <RefreshCw size={18} className={loading ? 'spin' : ''} />
          </button>
          <button 
            className="btn btn-danger" 
            onClick={async () => {
              if (!confirm('TÜM ANAHTAR KELİMELERİ SİLMEK İSTEDİĞİNİZE EMİN MİSİNİZ? Bu işlem geri alınamaz!')) return
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
          placeholder="Anahtar kelime veya sektör ara..."
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
              <th>Anahtar Kelime</th>
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
                  <p>Anahtar kelime bulunamadı</p>
                  <button className="btn btn-primary" onClick={() => setShowModal(true)}>
                    <Plus size={16} /> İlk Anahtar Kelimeyi Ekle
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
              <h2>Yeni Anahtar Kelime Ekle</h2>
              <button className="btn btn-icon" onClick={() => setShowModal(false)}>
                <X size={20} />
              </button>
            </div>
            
            <div className="form-group">
              <label>Anahtar Kelime *</label>
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


