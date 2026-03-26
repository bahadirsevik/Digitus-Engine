/**
 * Generation Page
 * Tab yapısında: SEO+GEO, Google Ads, Social Media
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Sparkles, FileText, Megaphone, Users, CheckCircle } from 'lucide-react'
import { useTaskPolling, getStoredTaskId } from '../hooks/useTaskPolling'
import TaskProgress from '../components/TaskProgress'
import ErrorBanner from '../components/ErrorBanner'
import SocialStepper from '../components/SocialStepper'
import { useSocialStore } from '../stores/socialStore'
import './Generation.css'

type TabType = 'seo' | 'ads' | 'social'

interface ScoringRun {
  id: number
  run_name?: string
  status: string
  created_at: string
}

export default function Generation() {
  const [searchParams] = useSearchParams()
  const socialRunId = useSocialStore((state) => state.scoringRunId)
  const setSocialFormData = useSocialStore((state) => state.setFormData)

  const [activeTab, setActiveTab] = useState<TabType>('seo')
  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // SEO Form State
  const [seoRunId, setSeoRunId] = useState<number | ''>('')
  const [seoLimit, setSeoLimit] = useState(10)
  const [seoTaskId, setSeoTaskId] = useState<string | null>(getStoredTaskId('seo_task'))
  const [seoResults, setSeoResults] = useState<any[]>([])

  // Ads Form State
  const [adsRunId, setAdsRunId] = useState<number | ''>('')
  const [brandName, setBrandName] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')
  const [usps, setUsps] = useState('')
  const [adsResults, setAdsResults] = useState<any | null>(null)

  // Task polling
  const seoPolling = useTaskPolling(seoTaskId, 'seo_task')

  const requestedRunId = useMemo(() => {
    const raw = searchParams.get('run_id')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isNaN(parsed) || parsed <= 0 ? null : parsed
  }, [searchParams])

  // Auto-fill Ads form context from brand profile when ADS run changes
  useEffect(() => {
    if (!adsRunId) return
    const fetchProfile = async () => {
      try {
        const res = await fetch(`/api/v1/brand-profile/runs/${adsRunId}/profile`)
        if (res.ok) {
          const profile = await res.json()
          if (!websiteUrl && profile.company_url) setWebsiteUrl(profile.company_url)
          if (profile.status === 'confirmed' && profile.profile_data) {
            const pd = profile.profile_data
            if (!brandName && pd.company_name) setBrandName(pd.company_name)
            if (!usps) {
              const products = (pd.products || []).slice(0, 3).join(', ')
              const sector = pd.sector || ''
              if (products && sector) setUsps(`${products} -- ${sector}`)
              else if (products) setUsps(products)
            }
          }
        }
      } catch { /* profile not found — ok */ }
    }
    fetchProfile()
  }, [adsRunId]) // eslint-disable-line react-hooks/exhaustive-deps

  const fetchSeoResults = useCallback(async (runId: number) => {
    try {
      const res = await fetch(`/api/v1/generation/seo-geo/list/${runId}?limit=100`)
      if (res.ok) {
        const data = await res.json()
        setSeoResults(data.items || [])
      }
    } catch (e) {
      console.error('Failed to fetch SEO results:', e)
    }
  }, [])

  useEffect(() => {
    fetchRuns()
  }, [])

  useEffect(() => {
    if (!requestedRunId || runs.length === 0) return
    if (!runs.some((run) => run.id === requestedRunId)) return

    setSeoRunId((prev) => (prev === '' ? requestedRunId : prev))
    setAdsRunId((prev) => (prev === '' ? requestedRunId : prev))

    if (socialRunId !== requestedRunId) {
      setSocialFormData({ scoringRunId: requestedRunId })
    }
  }, [requestedRunId, runs, socialRunId, setSocialFormData])

  // Auto-fetch SEO results when task completes
  useEffect(() => {
    if (seoPolling.isCompleted && seoRunId) {
      fetchSeoResults(Number(seoRunId))
    }
  }, [seoPolling.isCompleted, seoRunId, fetchSeoResults])

  // Fetch results when run is selected
  useEffect(() => {
    if (seoRunId) {
      fetchSeoResults(Number(seoRunId))
    } else {
      setSeoResults([])
    }
  }, [seoRunId, fetchSeoResults])

  const fetchRuns = async () => {
    try {
      const res = await fetch('/api/v1/scoring/runs')
      if (res.ok) {
        const data = await res.json()
        setRuns(data)
      }
    } catch (e) {
      console.error('Failed to fetch runs:', e)
    }
  }

  const startSeoGeneration = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/generation/seo-geo/bulk/${seoRunId}?limit=${seoLimit}`, {
        method: 'POST',
      })

      if (!res.ok) {
        const errData = await res.json()
        const detail = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail)
        throw new Error(detail || 'SEO üretimi başarısız')
      }

      const data = await res.json()
      setSeoTaskId(data.task_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Bir hata oluştu')
    } finally {
      setLoading(false)
    }
  }

  const startAdsGeneration = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/v1/generation/ads/rsa', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scoring_run_id: adsRunId,
          brand_name: brandName,
          website_url: websiteUrl ? websiteUrl.trim() : undefined,
          brand_usp: usps ? usps.split(',').map(s => s.trim()).join(', ') : undefined,
        })
      })

      if (!res.ok) {
        const errData = await res.json()
        const detail = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail)
        throw new Error(detail || 'Ads üretimi başarısız')
      }

      const data = await res.json()
      setAdsResults(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Bir hata oluştu')
    } finally {
      setLoading(false)
    }
  }

  const tabs = [
    { id: 'seo' as TabType, icon: FileText, label: 'SEO+GEO' },
    { id: 'ads' as TabType, icon: Megaphone, label: 'Google Ads' },
    { id: 'social' as TabType, icon: Users, label: 'Sosyal Medya' },
  ]

  return (
    <div className="generation-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>İçerik Üretimi</h1>
          <p>AI destekli içerik üretimi</p>
        </div>
      </header>

      {/* Tabs */}
      <div className="tabs">
        {tabs.map(tab => (
          <button
            key={tab.id}
            className={`tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <tab.icon size={18} />
            {tab.label}
          </button>
        ))}
      </div>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} onRetry={() => setError(null)} />}

      {/* SEO+GEO Tab */}
      {activeTab === 'seo' && (
        <div className="tab-content glass-card">
          <h2>SEO+GEO İçerik Üretimi</h2>
          <p className="tab-desc">Seçili skorlama çalışmasının SEO havuzundaki kelimeler için blog içerikleri üretir.</p>

          {seoTaskId && seoPolling.status && (
            <TaskProgress
              taskId={seoTaskId}
              status={seoPolling.status.status}
              progress={seoPolling.progress}
              errorMessage={seoPolling.errorMessage}
            />
          )}

          <div className="form-section">
            <div className="form-group">
              <label>Skorlama Çalışması</label>
              <select
                value={seoRunId}
                onChange={e => setSeoRunId(Number(e.target.value))}
              >
                <option value="">Seçin...</option>
                {runs.map(run => (
                  <option key={run.id} value={run.id}>
                    {run.run_name || `Çalışma #${run.id}`}{run.created_at ? ` — ${new Date(run.created_at).toLocaleDateString('tr-TR')}` : ''}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Limit</label>
              <input
                type="number"
                value={seoLimit}
                onChange={e => setSeoLimit(Number(e.target.value))}
                min={1}
                max={100}
              />
            </div>

            <button
              className="btn btn-primary"
              onClick={startSeoGeneration}
              disabled={loading || !seoRunId || seoPolling.isActive}
            >
              {loading || seoPolling.isActive ? 'Üretiliyor...' : 'SEO İçerik Üret'}
              <Sparkles size={18} />
            </button>
          </div>

          {/* SEO Results */}
          {seoResults.length > 0 && (
            <div className="results-section" style={{ marginTop: 'var(--space-lg)' }}>
              <h3 style={{ marginBottom: 'var(--space-md)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <CheckCircle size={18} color="var(--success)" />
                Üretilen İçerikler ({seoResults.length})
              </h3>
              <div className="seo-results-grid">
                {seoResults.map((item: any) => (
                  <div key={item.id} className="seo-result-card glass-card">
                    <div className="seo-result-title">{item.title || item.keyword}</div>
                    <div className="seo-result-keyword">{item.keyword}</div>
                    <div className="seo-result-scores">
                      <span className="score-badge seo">SEO: {(item.seo_score * 100).toFixed(0)}%</span>
                      <span className="score-badge geo">GEO: {(item.geo_score * 100).toFixed(0)}%</span>
                      <span className="score-badge combined">Kombine: {(item.combined_score * 100).toFixed(0)}%</span>
                    </div>
                    <div className="seo-result-meta">
                      {item.word_count} kelime
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Google Ads Tab */}
      {activeTab === 'ads' && (
        <div className="tab-content glass-card">
          <h2>Google Ads RSA Üretimi</h2>
          <p className="tab-desc">ADS havuzundaki kelimeler için reklam grupları ve başlıklar üretir.</p>

          <div className="form-section">
            <div className="form-group">
              <label>Skorlama Çalışması *</label>
              <select
                value={adsRunId}
                onChange={e => setAdsRunId(Number(e.target.value))}
              >
                <option value="">Seçin...</option>
                {runs.map(run => (
                  <option key={run.id} value={run.id}>
                    {run.run_name || `Çalışma #${run.id}`}{run.created_at ? ` — ${new Date(run.created_at).toLocaleDateString('tr-TR')}` : ''}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Marka Adı</label>
              <input
                type="text"
                value={brandName}
                onChange={e => setBrandName(e.target.value)}
                placeholder="Profilden otomatik dolar veya manuel girin"
              />
            </div>

            <div className="form-group">
              <label>Web Sitesi URL</label>
              <input
                type="url"
                value={websiteUrl}
                onChange={e => setWebsiteUrl(e.target.value)}
                placeholder="https://example.com"
              />
            </div>

            <div className="form-group">
              <label>USP'ler (virgülle ayırın)</label>
              <input
                type="text"
                value={usps}
                onChange={e => setUsps(e.target.value)}
                placeholder="Ücretsiz kargo, 30 gün iade, 7/24 destek"
              />
            </div>

            <button
              className="btn btn-primary"
              onClick={startAdsGeneration}
              disabled={loading || !adsRunId}
            >
              {loading ? 'Üretiliyor...' : 'Reklam Üret'}
              <Sparkles size={18} />
            </button>
          </div>

          {/* Ads Results */}
          {adsResults && (
            <div className="results-section" style={{ marginTop: 'var(--space-lg)' }}>
              <h3 style={{ marginBottom: 'var(--space-md)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <CheckCircle size={18} color="var(--success)" />
                RSA Üretimi Tamamlandı
              </h3>
              <div className="ads-summary-grid">
                <div className="ads-stat glass-card">
                  <span className="ads-stat-value">{adsResults.total_groups}</span>
                  <span className="ads-stat-label">Reklam Grubu</span>
                </div>
                <div className="ads-stat glass-card">
                  <span className="ads-stat-value">{adsResults.total_headlines}</span>
                  <span className="ads-stat-label">Başlık</span>
                </div>
                <div className="ads-stat glass-card">
                  <span className="ads-stat-value">{adsResults.total_descriptions}</span>
                  <span className="ads-stat-label">Açıklama</span>
                </div>
                <div className="ads-stat glass-card">
                  <span className="ads-stat-value">{adsResults.total_negative_keywords}</span>
                  <span className="ads-stat-label">Negatif Kelime</span>
                </div>
              </div>

              {adsResults.validation_summary && (
                <div className="validation-summary glass-card" style={{ marginTop: 'var(--space-md)', padding: 'var(--space-md)' }}>
                  <h4 style={{ marginBottom: 'var(--space-sm)' }}>Doğrulama Özeti</h4>
                  <div className="validation-stats">
                    <span>✅ Korunan: {adsResults.validation_summary.headlines_kept}</span>
                    <span>✂️ Kısaltılan: {adsResults.validation_summary.headlines_shortened}</span>
                    <span>🔄 Yeniden üretilen: {adsResults.validation_summary.headlines_regenerated}</span>
                    <span>❌ Elenen: {adsResults.validation_summary.headlines_eliminated}</span>
                    <span>🔀 DKI dönüştürülen: {adsResults.validation_summary.dki_converted_to_plain}</span>
                  </div>
                </div>
              )}

              {/* Ad Groups Detail */}
              <div className="seo-results-grid" style={{ marginTop: 'var(--space-md)' }}>
                {adsResults.ad_groups?.map((group: any, idx: number) => (
                  <div key={idx} className="seo-result-card glass-card">
                    <div className="seo-result-title">{group.name}</div>
                    <div className="seo-result-keyword">{group.theme}</div>
                    <div className="seo-result-scores">
                      <span className="score-badge seo">{group.headlines?.length || 0} Başlık</span>
                      <span className="score-badge geo">{group.descriptions?.length || 0} Açıklama</span>
                      <span className="score-badge combined">{group.negative_keywords?.length || 0} Negatif</span>
                    </div>
                    <div className="seo-result-meta">
                      {group.keywords?.join(', ')}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Social Media Tab */}
      {activeTab === 'social' && (
        <div className="tab-content">
          <SocialStepper />
        </div>
      )}
    </div>
  )
}
