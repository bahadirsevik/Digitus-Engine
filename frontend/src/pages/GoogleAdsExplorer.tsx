import { useEffect, useMemo, useState } from 'react'
import { Briefcase, CreditCard, Eye, Filter, RefreshCw, Search } from 'lucide-react'
import {
  ApiError,
  CampaignInfo,
  CampaignKeywordOut,
  CustomerDetailOut,
  CustomerIdItem,
  googleAdsApi,
} from '../services/api'
import './GoogleAdsExplorer.css'

type DateRange = 'LAST_7_DAYS' | 'LAST_14_DAYS' | 'LAST_30_DAYS' | 'ALL_TIME'

const moneyFormatter = new Intl.NumberFormat('tr-TR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

function formatNumber(value: number): string {
  return new Intl.NumberFormat('tr-TR').format(value)
}

function formatMoney(value: number): string {
  return moneyFormatter.format(value)
}

const HEALTH_LABEL: Record<string, string> = {
  'kontrol ediliyor': 'Bağlantı kontrol ediliyor',
  ok: 'Bağlantı çalışıyor',
  not_configured: 'Bağlantı kurulmamış',
  error: 'Bağlantı hatası',
}

const CAMPAIGN_STATUS_LABEL: Record<string, string> = {
  ENABLED: 'Etkin',
  PAUSED: 'Duraklatıldı',
  REMOVED: 'Kaldırıldı',
}

const MATCH_TYPE_LABEL: Record<string, string> = {
  EXACT: 'Tam eşleme',
  PHRASE: 'Sıralı eşleme',
  BROAD: 'Geniş eşleme',
}

function apiErrorMessage(err: unknown, fallback: string): string {
  const friendly = (message: string) =>
    message.includes('invalid_grant')
      ? 'Google Ads bağlantısının yetkisi geçersiz veya süresi dolmuş. Bağlantının yenilenmesi için sistem yöneticinize başvurun.'
      : message

  if (err && typeof err === 'object') {
    const apiErr = err as Partial<ApiError>
    if (typeof apiErr.detail === 'string') return friendly(apiErr.detail)
    if (typeof apiErr.message === 'string') return friendly(apiErr.message)
  }
  return fallback
}

export default function GoogleAdsExplorer() {
  const [customers, setCustomers] = useState<CustomerIdItem[]>([])
  const [selectedCustomer, setSelectedCustomer] = useState('')
  const [customerDetail, setCustomerDetail] = useState<CustomerDetailOut | null>(null)
  const [campaigns, setCampaigns] = useState<CampaignInfo[]>([])
  const [selectedCampaign, setSelectedCampaign] = useState('')
  const [keywords, setKeywords] = useState<CampaignKeywordOut[]>([])
  const [dateRange, setDateRange] = useState<DateRange>('LAST_30_DAYS')
  const [minImpressions, setMinImpressions] = useState(0)
  const [limit, setLimit] = useState(500)
  const [loading, setLoading] = useState(false)
  const [keywordLoading, setKeywordLoading] = useState(false)
  const [error, setError] = useState('')
  const [health, setHealth] = useState('kontrol ediliyor')

  const loadCustomers = async (refresh = false) => {
    setLoading(true)
    setError('')
    try {
      const healthRes = await googleAdsApi.health()
      const status = healthRes.data?.status || 'unknown'
      setHealth(status)
      if (status !== 'ok') {
        setError(
          status === 'not_configured'
            ? 'Google Ads bağlantısı kurulmamış. Kurulum için sistem yöneticinize başvurun.'
            : apiErrorMessage(
                { message: healthRes.data?.error },
                'Google Ads bağlantısı şu an kullanılamıyor. Biraz sonra Yenile ile tekrar deneyin; sürerse sistem yöneticinize başvurun.'
              )
        )
        setCustomers([])
        return
      }
      const customerRes = await googleAdsApi.listCustomers()
      const items = customerRes.data || []
      setCustomers(items)
      if (!selectedCustomer && items[0]) {
        // Varsayılan hesap: 956 ile biten; yoksa ilk hesap
        setSelectedCustomer(
          (items.find((c) => c.customer_id.endsWith('956')) ?? items[0]).customer_id
        )
      }
      if (refresh && selectedCustomer) {
        await loadCustomerContext(selectedCustomer, true)
      }
    } catch (err) {
      setHealth((current) => (current === 'kontrol ediliyor' ? 'error' : current))
      setError(apiErrorMessage(err, 'Google Ads bilgileri alınamadı'))
    } finally {
      setLoading(false)
    }
  }

  const loadCustomerContext = async (customerId: string, refresh = false) => {
    if (!customerId) return
    setLoading(true)
    setError('')
    try {
      const [detailRes, campaignsRes] = await Promise.all([
        googleAdsApi.getCustomer(customerId),
        googleAdsApi.listCampaigns(customerId, refresh),
      ])
      setCustomerDetail(detailRes.data)
      setCampaigns(campaignsRes.data || [])
      setSelectedCampaign((current) => {
        if (!current) return ''
        return campaignsRes.data?.some((campaign) => campaign.campaign_id === current)
          ? current
          : ''
      })
    } catch (err) {
      setError(apiErrorMessage(err, 'Kampanya bilgileri alınamadı'))
      setCampaigns([])
    } finally {
      setLoading(false)
    }
  }

  const loadKeywords = async (refresh = false) => {
    if (!selectedCustomer) return
    setKeywordLoading(true)
    setError('')
    try {
      const res = await googleAdsApi.listCampaignKeywords({
        customer_id: selectedCustomer,
        campaign_id: selectedCampaign || undefined,
        date_range: dateRange,
        min_impressions: minImpressions,
        limit,
        refresh,
      })
      setKeywords(res.data.keywords || [])
    } catch (err) {
      setError(apiErrorMessage(err, 'Kampanya anahtar kelime metrikleri alınamadı'))
      setKeywords([])
    } finally {
      setKeywordLoading(false)
    }
  }

  useEffect(() => {
    loadCustomers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (selectedCustomer) {
      loadCustomerContext(selectedCustomer)
    }
  }, [selectedCustomer])

  const totals = useMemo(() => {
    const impressions = keywords.reduce((sum, item) => sum + item.impressions, 0)
    const clicks = keywords.reduce((sum, item) => sum + item.clicks, 0)
    const cost = keywords.reduce((sum, item) => sum + item.cost, 0)
    return {
      impressions,
      clicks,
      cost,
      ctr: impressions > 0 ? (clicks / impressions) * 100 : 0,
    }
  }, [keywords])

  return (
    <div className="google-ads-explorer animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Google Ads Gezgini</h1>
          <p>Kampanya ve anahtar kelime metriklerini görüntüleyin (yalnızca okuma)</p>
        </div>
        <button
          className="btn btn-secondary"
          onClick={() => loadCustomers(true)}
          disabled={loading}
        >
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          Yenile
        </button>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="gads-toolbar">
        <label>
          <span>Google Ads hesabı</span>
          <select value={selectedCustomer} onChange={(e) => setSelectedCustomer(e.target.value)}>
            <option value="">Hesap seçin</option>
            {customers.map((customer) => (
              <option key={customer.customer_id} value={customer.customer_id}>
                {customer.customer_id}
              </option>
            ))}
          </select>
        </label>
        <div className={`gads-health gads-health-${health}`}>
          <Eye size={16} />
          {HEALTH_LABEL[health] || 'Bağlantı durumu bilinmiyor'}
        </div>
      </section>

      {customerDetail && (
        <section className="gads-account-band">
          <div>
            <span>Hesap</span>
            <strong>{customerDetail.name || customerDetail.customer_id}</strong>
          </div>
          <div>
            <span>Para birimi</span>
            <strong>{customerDetail.currency_code || '-'}</strong>
          </div>
          <div>
            <span>Zaman dilimi</span>
            <strong>{customerDetail.time_zone || '-'}</strong>
          </div>
          <div className="readonly-pill">Salt okunur</div>
        </section>
      )}

      <section className="gads-main-grid">
        <div className="gads-panel">
          <div className="panel-title">
            <Briefcase size={18} />
            Kampanyalar
          </div>
          <div className="campaign-list">
            <button
              className={`campaign-row ${selectedCampaign === '' ? 'active' : ''}`}
              onClick={() => setSelectedCampaign('')}
            >
              <span>Tüm kampanyalar</span>
              <small>{campaigns.length} kampanya</small>
            </button>
            {campaigns.map((campaign) => (
              <button
                key={campaign.campaign_id}
                className={`campaign-row ${
                  selectedCampaign === campaign.campaign_id ? 'active' : ''
                }`}
                onClick={() => setSelectedCampaign(campaign.campaign_id)}
              >
                <span>{campaign.campaign_name}</span>
                <small>
                  {CAMPAIGN_STATUS_LABEL[campaign.status] || campaign.status} ·{' '}
                  {campaign.campaign_id}
                </small>
              </button>
            ))}
            {!loading && campaigns.length === 0 && (
              <div className="gads-empty">Bu hesapta kampanya bulunamadı.</div>
            )}
          </div>
        </div>

        <div className="gads-panel">
          <div className="panel-title">
            <Filter size={18} />
            Anahtar Kelime Metrikleri
          </div>
          <div className="metric-filters">
            <label>
              <span>Tarih</span>
              <select value={dateRange} onChange={(e) => setDateRange(e.target.value as DateRange)}>
                <option value="LAST_7_DAYS">Son 7 gün</option>
                <option value="LAST_14_DAYS">Son 14 gün</option>
                <option value="LAST_30_DAYS">Son 30 gün</option>
                <option value="ALL_TIME">Tüm zamanlar</option>
              </select>
            </label>
            <label>
              <span>En az gösterim</span>
              <input
                type="number"
                min={0}
                value={minImpressions}
                onChange={(e) => setMinImpressions(Number(e.target.value))}
              />
            </label>
            <label>
              <span>En fazla satır</span>
              <input
                type="number"
                min={1}
                max={2000}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </label>
            <button
              className="btn btn-primary"
              onClick={() => loadKeywords(false)}
              disabled={!selectedCustomer || keywordLoading}
            >
              <Search size={16} />
              Getir
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => loadKeywords(true)}
              disabled={!selectedCustomer || keywordLoading}
            >
              <RefreshCw size={16} className={keywordLoading ? 'animate-spin' : ''} />
              Google Ads'ten yeniden çek
            </button>
          </div>

          <div className="gads-summary-grid">
            <div>
              <span>Anahtar kelime</span>
              <strong>{formatNumber(keywords.length)}</strong>
            </div>
            <div>
              <span>Gösterim</span>
              <strong>{formatNumber(totals.impressions)}</strong>
            </div>
            <div>
              <span>Tıklama</span>
              <strong>{formatNumber(totals.clicks)}</strong>
            </div>
            <div>
              <span>Maliyet</span>
              <strong>{formatMoney(totals.cost)}</strong>
            </div>
            <div>
              <span>CTR</span>
              <strong>{totals.ctr.toFixed(2)}%</strong>
            </div>
          </div>

          <div className="table-container gads-table">
            <table className="table">
              <thead>
                <tr>
                  <th>Anahtar kelime</th>
                  <th>Eşleme</th>
                  <th>Kampanya</th>
                  <th>Reklam grubu</th>
                  <th>Gösterim</th>
                  <th>Tıklama</th>
                  <th>Maliyet</th>
                  <th>CPC</th>
                  <th>CTR</th>
                </tr>
              </thead>
              <tbody>
                {keywords.map((keyword, index) => (
                  <tr
                    key={`${keyword.campaign_id}-${keyword.ad_group_name}-${keyword.keyword}-${index}`}
                  >
                    <td>{keyword.keyword}</td>
                    <td>{MATCH_TYPE_LABEL[keyword.match_type] || keyword.match_type}</td>
                    <td>{keyword.campaign_name}</td>
                    <td>{keyword.ad_group_name}</td>
                    <td>{formatNumber(keyword.impressions)}</td>
                    <td>{formatNumber(keyword.clicks)}</td>
                    <td>{formatMoney(keyword.cost)}</td>
                    <td>{formatMoney(keyword.avg_cpc)}</td>
                    <td>{(keyword.ctr * 100).toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!keywordLoading && keywords.length === 0 && (
              <div className="gads-empty">Filtrelere uyan anahtar kelime metriği yok.</div>
            )}
            {keywordLoading && <div className="gads-empty">Metrikler yükleniyor...</div>}
          </div>
        </div>
      </section>
      <div className="gads-note">
        <CreditCard size={16} />
        Bu ekran yalnızca kampanya performansını gösterir; anahtar kelime havuzunuza veri eklemez.
      </div>
    </div>
  )
}
