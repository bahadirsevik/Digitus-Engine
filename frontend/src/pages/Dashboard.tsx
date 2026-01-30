import { useState, useEffect } from 'react'
import { 
  Key, 
  BarChart3, 
  Layers, 
  Star,
  TrendingUp,
  RefreshCw
} from 'lucide-react'
import { keywordsApi, scoringApi, healthApi } from '../services/api'
import './Dashboard.css'

interface Stats {
  keywords: number
  scoringRuns: number
  apiStatus: string
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats>({ keywords: 0, scoringRuns: 0, apiStatus: 'checking...' })
  const [loading, setLoading] = useState(true)
  
  useEffect(() => {
    fetchStats()
  }, [])
  
  const fetchStats = async () => {
    setLoading(true)
    try {
      const [keywordsRes, runsRes, healthRes] = await Promise.all([
        keywordsApi.list({ limit: 1 }),
        scoringApi.listRuns({ limit: 1 }),
        healthApi.check()
      ])
      
      setStats({
        keywords: keywordsRes.data.total || 0,
        scoringRuns: runsRes.data.length || 0,
        apiStatus: healthRes.data.status || 'online'
      })
    } catch (error) {
      setStats(prev => ({ ...prev, apiStatus: 'offline' }))
    }
    setLoading(false)
  }
  
  const statCards = [
    { 
      icon: Key, 
      label: 'Toplam Keyword', 
      value: stats.keywords,
      color: 'var(--accent-primary)'
    },
    { 
      icon: BarChart3, 
      label: 'Skorlama Çalışmaları', 
      value: stats.scoringRuns,
      color: 'var(--success)'
    },
    { 
      icon: Layers, 
      label: 'Kanal Sayısı', 
      value: 3,
      color: 'var(--warning)'
    },
    { 
      icon: Star, 
      label: 'API Durumu', 
      value: stats.apiStatus,
      color: stats.apiStatus === 'offline' ? 'var(--error)' : 'var(--success)'
    },
  ]
  
  return (
    <div className="dashboard animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>DIGITUS ENGINE V2 kontrol paneli</p>
        </div>
        <button className="btn btn-secondary" onClick={fetchStats} disabled={loading}>
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          Yenile
        </button>
      </header>
      
      {/* Stats Grid */}
      <div className="stats-grid">
        {statCards.map((card, i) => (
          <div key={i} className="stat-card glass-card">
            <div className="stat-icon" style={{ background: card.color }}>
              <card.icon size={24} />
            </div>
            <div className="stat-content">
              <span className="stat-label">{card.label}</span>
              <span className="stat-value">{card.value}</span>
            </div>
          </div>
        ))}
      </div>
      
      {/* Quick Actions */}
      <section className="quick-actions">
        <h2>Hızlı İşlemler</h2>
        <div className="action-grid">
          <a href="/keywords" className="action-card glass-card">
            <Key size={32} />
            <h3>Keyword Ekle</h3>
            <p>Yeni anahtar kelimeler ekleyin</p>
          </a>
          <a href="/scoring" className="action-card glass-card">
            <TrendingUp size={32} />
            <h3>Skorlama Başlat</h3>
            <p>Keywordları skorlayın</p>
          </a>
          <a href="/channels" className="action-card glass-card">
            <Layers size={32} />
            <h3>Kanal Ataması</h3>
            <p>AI destekli kanal ataması</p>
          </a>
        </div>
      </section>
      
      {/* Workflow */}
      <section className="workflow-section">
        <h2>İş Akışı</h2>
        <div className="workflow-steps">
          <div className="workflow-step">
            <div className="step-number">1</div>
            <div className="step-content">
              <h4>Keyword Yükle</h4>
              <p>Anahtar kelimeleri içe aktarın veya manuel ekleyin</p>
            </div>
          </div>
          <div className="workflow-arrow">→</div>
          <div className="workflow-step">
            <div className="step-number">2</div>
            <div className="step-content">
              <h4>Skorlama</h4>
              <p>ADS, SEO, SOCIAL skorları hesaplayın</p>
            </div>
          </div>
          <div className="workflow-arrow">→</div>
          <div className="workflow-step">
            <div className="step-number">3</div>
            <div className="step-content">
              <h4>Kanal Ataması</h4>
              <p>AI ile niyet analizi ve kanal ataması</p>
            </div>
          </div>
          <div className="workflow-arrow">→</div>
          <div className="workflow-step">
            <div className="step-number">4</div>
            <div className="step-content">
              <h4>Export</h4>
              <p>DOCX, PDF veya Excel olarak dışa aktarın</p>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
