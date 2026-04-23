import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  googleAdsApi,
  CustomerIdItem,
  CustomerDetailOut,
  EnrichResponse,
  ImportResponse,
  CampaignInfo,
  CampaignKeywordOut,
} from '../services/api'
import './GoogleAds.css'

interface HealthStatus {
  status: 'ok' | 'not_configured' | 'error'
  auth_ok?: boolean
  permission_ok?: boolean
  accessible_customers_count?: number
  error?: string
}

const TABLE_PAGE_SIZE = 30

export default function GoogleAds() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)
  const [customers, setCustomers] = useState<CustomerIdItem[]>([])
  const [selectedCustomer, setSelectedCustomer] = useState<string>('')
  const [customerDetail, setCustomerDetail] = useState<CustomerDetailOut | null>(null)
  const [seeds, setSeeds] = useState<string>('')
  const [maxResults, setMaxResults] = useState<number>(300)
  const [minVolume, setMinVolume] = useState<number>(0)
  const [languageId, setLanguageId] = useState<string>('')
  const [geoTargetId, setGeoTargetId] = useState<string>('')
  const [sector, setSector] = useState<string>('')
  const [targetMarket, setTargetMarket] = useState<string>('')
  const [previewData, setPreviewData] = useState<EnrichResponse | null>(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [loadingImport, setLoadingImport] = useState(false)
  const [importResult, setImportResult] = useState<ImportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [campaigns, setCampaigns] = useState<CampaignInfo[]>([])
  const [selectedCampaign, setSelectedCampaign] = useState<string>('')
  const [minImpressions, setMinImpressions] = useState<number>(0)
  const [dateRange, setDateRange] = useState<string>('ALL_TIME')
  const [campaignLimit, setCampaignLimit] = useState<number>(500)
  const [campaignKeywords, setCampaignKeywords] = useState<CampaignKeywordOut[]>([])
  const [campaignKwCount, setCampaignKwCount] = useState<number>(0)
  const [campaignSector, setCampaignSector] = useState<string>('')
  const [campaignTargetMarket, setCampaignTargetMarket] = useState<string>('')
  const [campaignImportResult, setCampaignImportResult] = useState<ImportResponse | null>(null)
  const [loadingCampaigns, setLoadingCampaigns] = useState(false)
  const [loadingCampaignKw, setLoadingCampaignKw] = useState(false)
  const [loadingCampaignImport, setLoadingCampaignImport] = useState(false)
  const [previewVisibleCount, setPreviewVisibleCount] = useState<number>(TABLE_PAGE_SIZE)
  const [campaignVisibleCount, setCampaignVisibleCount] = useState<number>(TABLE_PAGE_SIZE)

  useEffect(() => {
    checkHealth()
  }, [])

  const checkHealth = async () => {
    setHealthLoading(true)
    try {
      const res = await googleAdsApi.health()
      setHealth(res.data as HealthStatus)
      if (res.data.status === 'ok') {
        fetchCustomers()
      }
    } catch (err: any) {
      setHealth({ status: 'error', error: err?.response?.data?.detail || 'Bağlantı hatası' })
    } finally {
      setHealthLoading(false)
    }
  }

  const fetchCustomers = async () => {
    try {
      const res = await googleAdsApi.listCustomers()
      setCustomers(res.data || [])
    } catch (err: any) {
      setError('Müşteri listesi alınamadı: ' + (err?.response?.data?.detail || err.message))
    }
  }

  const handleCustomerChange = async (customerId: string) => {
    setSelectedCustomer(customerId)
    setCustomerDetail(null)
    setCampaigns([])
    setSelectedCampaign('')
    setCampaignKeywords([])
    setCampaignVisibleCount(TABLE_PAGE_SIZE)
    setCampaignKwCount(0)
    setCampaignImportResult(null)
    if (!customerId) return
    try {
      const res = await googleAdsApi.getCustomer(customerId)
      setCustomerDetail(res.data)
    } catch {
      // detail fetch is best-effort
    }
    setLoadingCampaigns(true)
    try {
      const campaignRes = await googleAdsApi.listCampaigns(customerId)
      setCampaigns(campaignRes.data || [])
    } catch {
      // campaign list is best-effort
    } finally {
      setLoadingCampaigns(false)
    }
  }

  const getSeedList = (): string[] =>
    seeds
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean)
      .slice(0, 20)

  const hasSeedKeywords = getSeedList().length > 0

  const handlePreview = async () => {
    setError(null)
    setPreviewData(null)
    setPreviewVisibleCount(TABLE_PAGE_SIZE)
    const seedList = getSeedList()
    if (!selectedCustomer) { setError('Lütfen bir müşteri hesabı seçin.'); return }
    if (seedList.length === 0) { setError('En az bir seed kelime girin.'); return }
    setLoadingPreview(true)
    try {
      const res = await googleAdsApi.enrich({
        customer_id: selectedCustomer,
        seeds: seedList,
        max_results: maxResults,
        min_volume: minVolume,
        language_id: languageId || undefined,
        geo_target_id: geoTargetId || undefined,
      })
      setPreviewData(res.data)
    } catch (err: any) {
      setError('Önizleme hatası: ' + (err?.response?.data?.detail || err.message))
    } finally {
      setLoadingPreview(false)
    }
  }

  const handleImport = async () => {
    setError(null)
    setImportResult(null)
    const seedList = getSeedList()
    if (!selectedCustomer) { setError('Lütfen bir müşteri hesabı seçin.'); return }
    if (seedList.length === 0) { setError('En az bir seed kelime girin.'); return }
    setLoadingImport(true)
    try {
      const res = await googleAdsApi.import({
        customer_id: selectedCustomer,
        seeds: seedList,
        max_results: maxResults,
        min_volume: minVolume,
        sector: sector || undefined,
        target_market: targetMarket || undefined,
      })
      setImportResult(res.data)
    } catch (err: any) {
      setError('Import hatası: ' + (err?.response?.data?.detail || err.message))
    } finally {
      setLoadingImport(false)
    }
  }

  const handleCampaignKeywordList = async () => {
    setError(null)
    setCampaignKeywords([])
    setCampaignVisibleCount(TABLE_PAGE_SIZE)
    setCampaignKwCount(0)
    setCampaignImportResult(null)
    if (!selectedCustomer) { setError('Lutfen bir musteri hesabi secin.'); return }
    setLoadingCampaignKw(true)
    try {
      const res = await googleAdsApi.listCampaignKeywords({
        customer_id: selectedCustomer,
        campaign_id: selectedCampaign || undefined,
        min_impressions: minImpressions,
        date_range: dateRange,
        limit: campaignLimit,
      })
      setCampaignKeywords(res.data.keywords || [])
      setCampaignKwCount(res.data.count || 0)
    } catch (err: any) {
      setError('Kampanya keywordleri alinamadi: ' + (err?.response?.data?.detail || err.message))
    } finally {
      setLoadingCampaignKw(false)
    }
  }

  const handleCampaignImport = async () => {
    setError(null)
    setCampaignImportResult(null)
    if (!selectedCustomer) { setError('Lutfen bir musteri hesabi secin.'); return }
    setLoadingCampaignImport(true)
    try {
      const res = await googleAdsApi.importCampaignKeywords({
        customer_id: selectedCustomer,
        campaign_id: selectedCampaign || undefined,
        min_impressions: minImpressions,
        date_range: dateRange,
        limit: campaignLimit,
        sector: campaignSector || undefined,
        target_market: campaignTargetMarket || undefined,
      })
      setCampaignImportResult(res.data)
    } catch (err: any) {
      setError('Kampanya import hatasi: ' + (err?.response?.data?.detail || err.message))
    } finally {
      setLoadingCampaignImport(false)
    }
  }

  const healthBannerClass = () => {
    if (!health) return ''
    if (health.status === 'ok') return 'health-banner health-banner--ok'
    if (health.status === 'not_configured') return 'health-banner health-banner--warning'
    return 'health-banner health-banner--error'
  }

  const healthBannerText = () => {
    if (!health) return ''
    if (health.status === 'ok') {
      const count = health.accessible_customers_count
      return `Bağlı${count !== undefined ? ` · ${count} hesap` : ''}`
    }
    if (health.status === 'not_configured') {
      return 'Credentials yapılandırılmamış (.env kontrol et)'
    }
    return `Hata: ${health.error || 'Bilinmeyen hata'}`
  }

  return (
    <div className="google-ads-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Google Ads</h1>
          <p>Keyword fikirleri çek ve veritabanına aktar</p>
        </div>
      </header>

      {/* Health Banner */}
      {healthLoading ? (
        <div className="health-banner health-banner--loading">Bağlantı kontrol ediliyor...</div>
      ) : health && (
        <div className={healthBannerClass()}>{healthBannerText()}</div>
      )}

      {error && (
        <div className="health-banner health-banner--error">{error}</div>
      )}

      {/* Customer Selection */}
      {health?.status === 'ok' && (
        <section className="glass-card customer-selector">
          <h2>Hesap Seçimi</h2>
          {customers.length === 0 ? (
            <p className="empty-text">Erişilebilir hesap bulunamadı</p>
          ) : (
            <>
              <select
                className="input"
                value={selectedCustomer}
                onChange={e => handleCustomerChange(e.target.value)}
              >
                <option value="">— Müşteri seçin —</option>
                {customers.map(c => (
                  <option key={c.customer_id} value={c.customer_id}>
                    {c.customer_id}
                  </option>
                ))}
              </select>
              {customerDetail && (
                <p className="customer-detail">
                  {customerDetail.name} · {customerDetail.currency_code} · {customerDetail.time_zone}
                </p>
              )}
            </>
          )}
        </section>
      )}

      {/* Campaign Keywords Section */}
      {health?.status === 'ok' && (
        <section className="glass-card campaign-section">
          <h2>Kampanya Keywordleri</h2>
          <div className="enrich-form">
            <div className="form-row">
              <div className="form-group">
                <label>Kampanya (opsiyonel)</label>
                <select
                  className="input"
                  value={selectedCampaign}
                  onChange={e => setSelectedCampaign(e.target.value)}
                  disabled={!selectedCustomer || loadingCampaigns}
                >
                  <option value="">Tum kampanyalar</option>
                  {campaigns.map(c => (
                    <option key={c.campaign_id} value={c.campaign_id}>
                      {c.campaign_name} ({c.campaign_id})
                    </option>
                  ))}
                </select>
                {loadingCampaigns && <p className="preview-summary">Kampanyalar yukleniyor...</p>}
              </div>
              <div className="form-group">
                <label>Tarih Araligi</label>
                <select
                  className="input"
                  value={dateRange}
                  onChange={e => setDateRange(e.target.value)}
                >
                  <option value="ALL_TIME">Tum zamanlar</option>
                  <option value="LAST_7_DAYS">Son 7 gun</option>
                  <option value="LAST_14_DAYS">Son 14 gun</option>
                  <option value="LAST_30_DAYS">Son 30 gun</option>
                </select>
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>Min. Gosterim</label>
                <input
                  type="number"
                  className="input"
                  min={0}
                  value={minImpressions}
                  onChange={e => setMinImpressions(parseInt(e.target.value) || 0)}
                />
              </div>
              <div className="form-group">
                <label>Limit (1-2000)</label>
                <input
                  type="number"
                  className="input"
                  min={1}
                  max={2000}
                  value={campaignLimit}
                  onChange={e => {
                    const next = parseInt(e.target.value) || 500
                    setCampaignLimit(Math.min(Math.max(next, 1), 2000))
                  }}
                />
              </div>
            </div>
            <button
              className="btn btn-primary"
              onClick={handleCampaignKeywordList}
              disabled={loadingCampaignKw || loadingCampaignImport || !selectedCustomer}
            >
              {loadingCampaignKw ? 'Yukleniyor...' : 'Kampanya Keywordlerini Listele'}
            </button>
          </div>

          {campaignKwCount > 0 && (
            <p className="preview-summary">{campaignKwCount} keyword bulundu.</p>
          )}
          {campaignKwCount === 0 && selectedCustomer && !loadingCampaignKw && (
            <p className="preview-summary warning-text">
              Bu filtrede keyword bulunamadi. Bu kampanyada aktif keyword olmayabilir; "Tum kampanyalar" secimiyle tekrar deneyin.
            </p>
          )}
          {campaignKeywords.length > 0 && (
            <>
              <div className="table-container">
                <table className="preview-table table">
                  <thead>
                    <tr>
                      <th>Keyword</th>
                      <th>Esleme</th>
                      <th>Kampanya</th>
                      <th>Ad Group</th>
                      <th>Gosterim</th>
                      <th>Tiklama</th>
                      <th>Ort. CPC</th>
                      <th>CTR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {campaignKeywords.slice(0, campaignVisibleCount).map((kw, idx) => (
                      <tr key={`${kw.keyword}-${idx}`}>
                        <td>{kw.keyword}</td>
                        <td>{kw.match_type}</td>
                        <td>{kw.campaign_name}</td>
                        <td>{kw.ad_group_name}</td>
                        <td>{kw.impressions.toLocaleString()}</td>
                        <td>{kw.clicks.toLocaleString()}</td>
                        <td>{kw.avg_cpc.toFixed(2)}</td>
                        <td>{(kw.ctr * 100).toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {campaignVisibleCount < campaignKeywords.length && (
                <button
                  className="btn btn-secondary"
                  onClick={() => setCampaignVisibleCount(prev => prev + TABLE_PAGE_SIZE)}
                  type="button"
                >
                  Daha fazla goster ({campaignKeywords.length - campaignVisibleCount} kalan)
                </button>
              )}
            </>
          )}

          <div className="enrich-form campaign-import-form">
            <div className="form-row">
              <div className="form-group">
                <label>Sektor (opsiyonel)</label>
                <input
                  className="input"
                  value={campaignSector}
                  onChange={e => setCampaignSector(e.target.value)}
                  placeholder="Dijital Pazarlama"
                />
              </div>
              <div className="form-group">
                <label>Hedef Pazar (opsiyonel)</label>
                <input
                  className="input"
                  value={campaignTargetMarket}
                  onChange={e => setCampaignTargetMarket(e.target.value)}
                  placeholder="Turkiye"
                />
              </div>
            </div>
            <button
              className="btn btn-success"
              onClick={handleCampaignImport}
              disabled={loadingCampaignImport || loadingCampaignKw || !selectedCustomer}
            >
              {loadingCampaignImport ? 'Aktariliyor...' : "Kampanya Keywordlerini DB'ye Aktar"}
            </button>
          </div>

          {campaignImportResult && (
            <div className="import-result">
              <strong>
                {campaignImportResult.created} olusturuldu · {campaignImportResult.already_existing} mevcuttu · {campaignImportResult.skipped_fuzzy} benzer atlandi
              </strong>
            </div>
          )}
        </section>
      )}

      {/* Enrich / Preview Form */}
      {health?.status === 'ok' && (
        <section className="glass-card">
          <h2>Keyword Önizleme</h2>
          <div className="enrich-form">
            <div className="form-group">
              <label>Seed Kelimeler (her satıra bir kelime, max 20)</label>
              <textarea
                className="input seeds-textarea"
                value={seeds}
                onChange={e => setSeeds(e.target.value)}
                placeholder={'dijital pazarlama\nseo ajansı'}
              />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>Maks. Sonuç (1-5000)</label>
                <input
                  type="number"
                  className="input"
                  min={1}
                  max={5000}
                  value={maxResults}
                  onChange={e => {
                    const next = parseInt(e.target.value) || 300
                    setMaxResults(Math.min(Math.max(next, 1), 5000))
                  }}
                />
              </div>
              <div className="form-group">
                <label>Min. Hacim</label>
                <input
                  type="number"
                  className="input"
                  min={0}
                  value={minVolume}
                  onChange={e => setMinVolume(parseInt(e.target.value) || 0)}
                />
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>Language ID (opsiyonel)</label>
                <input
                  className="input"
                  value={languageId}
                  onChange={e => setLanguageId(e.target.value)}
                  placeholder="1011 (Türkçe)"
                />
              </div>
              <div className="form-group">
                <label>Geo Target ID (opsiyonel)</label>
                <input
                  className="input"
                  value={geoTargetId}
                  onChange={e => setGeoTargetId(e.target.value)}
                  placeholder="2792 (Türkiye)"
                />
              </div>
            </div>
            <button
              className="btn btn-primary"
              onClick={handlePreview}
              disabled={loadingPreview || loadingImport || !selectedCustomer || !hasSeedKeywords}
            >
              {loadingPreview ? 'Yükleniyor...' : 'Önizle'}
            </button>
          </div>

          {previewData && (
            <>
              {previewData.truncated && (
                <p className="preview-summary warning-text">
                  İlk {previewData.truncated_at ?? previewData.count} sonuç gösteriliyor (max_results'a ulaşıldı)
                </p>
              )}
              <p className="preview-summary">
                {previewData.count} kelime · {previewData.keywords.filter(k => k.trend_3m !== 0).length} kelime trend hesaplandı
              </p>
              {previewData.keywords.length === 0 ? (
                <p className="empty-text">Verilen seed'ler için öneri bulunamadı</p>
              ) : (
                <>
                  <div className="table-container">
                    <table className="preview-table table">
                      <thead>
                        <tr>
                          <th>Kelime</th>
                          <th>Aylık Hacim</th>
                          <th>Rekabet</th>
                          <th>Trend 3m</th>
                          <th>Trend 12m</th>
                          <th>CPC Min</th>
                        </tr>
                      </thead>
                      <tbody>
                        {previewData.keywords.slice(0, previewVisibleCount).map((kw, i) => (
                          <tr key={i}>
                            <td>{kw.keyword}</td>
                            <td>{kw.avg_monthly_searches.toLocaleString()}</td>
                            <td>{kw.competition_score.toFixed(2)}</td>
                            <td>{kw.trend_3m.toFixed(2)}%</td>
                            <td>{kw.trend_12m.toFixed(2)}%</td>
                            <td>{kw.cpc_low.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {previewVisibleCount < previewData.keywords.length && (
                    <button
                      className="btn btn-secondary"
                      onClick={() => setPreviewVisibleCount(prev => prev + TABLE_PAGE_SIZE)}
                      type="button"
                    >
                      Daha fazla goster ({previewData.keywords.length - previewVisibleCount} kalan)
                    </button>
                  )}
                </>
              )}
            </>
          )}
        </section>
      )}

      {/* Import Section */}
      {health?.status === 'ok' && (
        <section className="glass-card import-section">
          <h2>DB'ye Aktar</h2>
          <p className="preview-summary">Yukarıdaki seed/max_results/min_volume ayarları kullanılır.</p>
          <div className="enrich-form">
            <div className="form-row">
              <div className="form-group">
                <label>Sektör (opsiyonel)</label>
                <input
                  className="input"
                  value={sector}
                  onChange={e => setSector(e.target.value)}
                  placeholder="Dijital Pazarlama"
                />
              </div>
              <div className="form-group">
                <label>Hedef Pazar (opsiyonel)</label>
                <input
                  className="input"
                  value={targetMarket}
                  onChange={e => setTargetMarket(e.target.value)}
                  placeholder="Türkiye"
                />
              </div>
            </div>
            <button
              className="btn btn-success"
              onClick={handleImport}
              disabled={loadingImport || loadingPreview || !selectedCustomer || !hasSeedKeywords}
            >
              {loadingImport ? 'Aktarılıyor...' : "DB'ye Aktar"}
            </button>
          </div>

          {importResult && (
            <div className="import-result">
              <strong>
                {importResult.created} oluşturuldu · {importResult.already_existing} mevcuttu · {importResult.skipped_fuzzy} benzer atlandı
              </strong>
              {importResult.truncated && (
                <p className="preview-summary">{importResult.truncated_reason}</p>
              )}
              <p style={{ marginTop: '0.75rem' }}>
                <Link to="/keywords" className="btn btn-secondary" style={{ display: 'inline-flex' }}>
                  Anahtar Kelimeler sayfasına git
                </Link>
              </p>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
