import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import {
  Brain,
  CheckCircle2,
  Globe,
  RefreshCw,
  ArrowRight,
  Settings2
} from 'lucide-react'
import {
  brandProfileApi,
  BrandProfileResponse,
  KeywordRelevanceResponse,
  RelevanceComputeResponse,
  scoringApi
} from '../services/api'
import './BrandProfile.css'

interface ScoringRunStatus {
  id: number
  run_name?: string
  status: string
  total_keywords: number
  started_at?: string | null
  completed_at?: string | null
}

interface ProfileFormState {
  company_name: string
  sector: string
  target_audience: string
  products: string
  services: string
  use_cases: string
  problems_solved: string
  brand_terms: string
  exclude_themes: string
  anchor_texts: string
}

const RUN_POLLABLE_STATUSES = new Set(['pending', 'running'])
const ADVANCED_ANCHOR_MODE_KEY = 'brand_profile_anchor_advanced'

const LIST_FIELD_CONFIG = [
  { key: 'products', label: 'Ürünler' },
  { key: 'services', label: 'Hizmetler' },
  { key: 'use_cases', label: 'Kullanım Alanları' },
  { key: 'problems_solved', label: 'Çözülen Problemler' },
  { key: 'brand_terms', label: 'Marka Terimleri' },
  { key: 'exclude_themes', label: 'Dışlanacak Temalar' }
] as const

function emptyProfileForm(): ProfileFormState {
  return {
    company_name: '',
    sector: '',
    target_audience: '',
    products: '',
    services: '',
    use_cases: '',
    problems_solved: '',
    brand_terms: '',
    exclude_themes: '',
    anchor_texts: ''
  }
}

function normalizeRuns(data: unknown): ScoringRunStatus[] {
  if (Array.isArray(data)) return data as ScoringRunStatus[]

  if (data && typeof data === 'object') {
    const wrapped = data as { value?: unknown }
    if (Array.isArray(wrapped.value)) {
      return wrapped.value as ScoringRunStatus[]
    }
  }

  return []
}

function parseCompetitors(raw: string): string[] {
  const parts = raw
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)

  const unique: string[] = []
  for (const item of parts) {
    if (!unique.includes(item)) unique.push(item)
    if (unique.length >= 3) break
  }
  return unique
}

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === 'string') return item
          if (item && typeof item === 'object' && 'msg' in item) {
            return String(item.msg)
          }
          return JSON.stringify(item)
        })
        .join(' | ')
    }
    return error.message
  }
  return 'Beklenmeyen hata'
}

function listToTextarea(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean)
    .join('\n')
}

function splitNewlineItems(raw: string): string[] {
  return raw
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function profileToForm(profileData?: Record<string, unknown>): ProfileFormState {
  const form = emptyProfileForm()
  if (!profileData) return form

  const readString = (key: string): string => {
    const value = profileData[key]
    return typeof value === 'string' ? value : ''
  }

  return {
    company_name: readString('company_name'),
    sector: readString('sector'),
    target_audience: readString('target_audience'),
    products: listToTextarea(profileData.products),
    services: listToTextarea(profileData.services),
    use_cases: listToTextarea(profileData.use_cases),
    problems_solved: listToTextarea(profileData.problems_solved),
    brand_terms: listToTextarea(profileData.brand_terms),
    exclude_themes: listToTextarea(profileData.exclude_themes),
    anchor_texts: listToTextarea(profileData.anchor_texts)
  }
}

function buildProfilePayload(form: ProfileFormState, includeAnchorOverride: boolean): Record<string, unknown> {
  const payload: Record<string, unknown> = {}

  LIST_FIELD_CONFIG.forEach(({ key }) => {
    payload[key] = splitNewlineItems(form[key])
  })

  if (includeAnchorOverride) {
    payload.anchor_texts = splitNewlineItems(form.anchor_texts)
  }

  return payload
}

export default function BrandProfile() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [runs, setRuns] = useState<ScoringRunStatus[]>([])
  const [loadingRuns, setLoadingRuns] = useState(false)

  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [companyUrl, setCompanyUrl] = useState('')
  const [competitorInput, setCompetitorInput] = useState('')

  const [profile, setProfile] = useState<BrandProfileResponse | null>(null)
  const [profileForm, setProfileForm] = useState<ProfileFormState>(emptyProfileForm())
  const [advancedAnchorMode, setAdvancedAnchorMode] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem(ADVANCED_ANCHOR_MODE_KEY) === 'true'
  })

  const [relevance, setRelevance] = useState<KeywordRelevanceResponse[]>([])
  const [relevanceSummary, setRelevanceSummary] = useState<RelevanceComputeResponse | null>(null)

  const [minScore, setMinScore] = useState(0.4)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const [loadingProfile, setLoadingProfile] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [loadingRelevance, setLoadingRelevance] = useState(false)
  const [computingRelevance, setComputingRelevance] = useState(false)

  const requestedRunId = useMemo(() => {
    const raw = searchParams.get('run_id')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isNaN(parsed) || parsed <= 0 ? null : parsed
  }, [searchParams])

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) || null,
    [runs, selectedRunId]
  )

  const isProfileConfirmed = profile?.status === 'confirmed'
  const hasAnchors = splitNewlineItems(profileForm.anchor_texts).length > 0

  const fetchRuns = useCallback(async () => {
    setLoadingRuns(true)
    try {
      const response = await scoringApi.listRuns({ limit: 100 })
      setRuns(normalizeRuns(response.data))
    } catch (err) {
      setError(`Skorlama çalışmaları yüklenemedi: ${extractErrorMessage(err)}`)
    } finally {
      setLoadingRuns(false)
    }
  }, [])

  const fetchProfile = useCallback(async (runId: number, withSpinner = true) => {
    if (withSpinner) setLoadingProfile(true)
    try {
      const response = await brandProfileApi.getProfile(runId)
      const data = response.data as BrandProfileResponse
      setProfile(data)
      setProfileForm(profileToForm(data.profile_data))
      setCompanyUrl((prev) => (prev.trim() ? prev : data.company_url || ''))
      setCompetitorInput((prev) => {
        if (prev.trim()) return prev
        return (data.competitor_urls || []).join('\n')
      })
      setError('')
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        setProfile(null)
        setProfileForm(emptyProfileForm())
      } else {
        setError(`Profil alınamadı: ${extractErrorMessage(err)}`)
      }
    } finally {
      if (withSpinner) setLoadingProfile(false)
    }
  }, [])

  const fetchRelevance = useCallback(async (runId: number, threshold: number) => {
    setLoadingRelevance(true)
    try {
      const response = await brandProfileApi.getRelevance(runId, threshold)
      setRelevance(Array.isArray(response.data) ? (response.data as KeywordRelevanceResponse[]) : [])
      setError('')
    } catch (err) {
      setRelevance([])
      if (!(axios.isAxiosError(err) && err.response?.status === 404)) {
        setError(`İlgi skorları alınamadı: ${extractErrorMessage(err)}`)
      }
    } finally {
      setLoadingRelevance(false)
    }
  }, [])

  useEffect(() => {
    void fetchRuns()
  }, [fetchRuns])

  useEffect(() => {
    if (!requestedRunId) return
    setSelectedRunId((prev) => prev || requestedRunId)
  }, [requestedRunId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(ADVANCED_ANCHOR_MODE_KEY, String(advancedAnchorMode))
  }, [advancedAnchorMode])

  useEffect(() => {
    if (!selectedRunId) return
    setInfo('')
    setError('')
    setRelevanceSummary(null)
    setProfile(null)
    setProfileForm(emptyProfileForm())
    setCompanyUrl('')
    setCompetitorInput('')
    setRelevance([])
    void fetchProfile(selectedRunId, true)
  }, [selectedRunId, fetchProfile])

  useEffect(() => {
    if (!selectedRunId) return
    void fetchRelevance(selectedRunId, minScore)
  }, [selectedRunId, minScore, fetchRelevance])

  useEffect(() => {
    if (!selectedRunId || !profile || !RUN_POLLABLE_STATUSES.has(profile.status)) return
    const timer = window.setInterval(() => {
      void fetchProfile(selectedRunId, false)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [selectedRunId, profile, fetchProfile])

  const handleAnalyze = async () => {
    if (!selectedRunId) {
      setError('Önce bir skorlama çalışması seçin.')
      return
    }
    if (!companyUrl.trim()) {
      setError('Şirket URL alanı zorunludur.')
      return
    }

    setAnalyzing(true)
    setError('')
    setInfo('')

    try {
      const competitors = parseCompetitors(competitorInput)
      const payload: { company_url: string; competitor_urls?: string[] } = {
        company_url: companyUrl.trim()
      }
      if (competitors.length > 0) payload.competitor_urls = competitors

      const response = await brandProfileApi.analyzeProfile(selectedRunId, payload)
      const data = response.data as BrandProfileResponse
      setProfile(data)
      setProfileForm(profileToForm(data.profile_data))
      setInfo('Profil analizi başlatıldı. Durum otomatik olarak güncellenecek.')
      await fetchProfile(selectedRunId, false)
    } catch (err) {
      setError(`Analiz başarısız: ${extractErrorMessage(err)}`)
    } finally {
      setAnalyzing(false)
    }
  }

  const handleConfirm = async () => {
    if (!selectedRunId) {
      setError('Önce bir skorlama çalışması seçin.')
      return
    }

    setConfirming(true)
    setError('')
    setInfo('')

    try {
      const response = await brandProfileApi.confirmProfile(selectedRunId, {
        profile_data: buildProfilePayload(profileForm, advancedAnchorMode)
      })

      const data = response.data as BrandProfileResponse
      setProfile(data)
      setProfileForm(profileToForm(data.profile_data))
      setInfo('Profil onaylandı. Anchor listesi varsa ilgi skoru hesabı otomatik başlar.')
      await fetchRelevance(selectedRunId, minScore)
    } catch (err) {
      setError(`Onay başarısız: ${extractErrorMessage(err)}`)
    } finally {
      setConfirming(false)
    }
  }

  const handleComputeRelevance = async () => {
    if (!selectedRunId) {
      setError('Önce bir skorlama çalışması seçin.')
      return
    }

    setComputingRelevance(true)
    setError('')
    setInfo('')
    try {
      const response = await brandProfileApi.computeRelevance(selectedRunId)
      setRelevanceSummary(response.data as RelevanceComputeResponse)
      await fetchRelevance(selectedRunId, minScore)
      setInfo('İlgi skoru hesaplaması tamamlandı.')
    } catch (err) {
      setError(`İlgi skoru hesaplaması başarısız: ${extractErrorMessage(err)}`)
    } finally {
      setComputingRelevance(false)
    }
  }

  return (
    <div className="brand-profile-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Marka Profili</h1>
          <p>Web sitesi analizi, profil düzenleme ve ilgi skoru kontrol paneli</p>
        </div>
        <button className="btn btn-secondary" onClick={() => void fetchRuns()} disabled={loadingRuns}>
          <RefreshCw size={18} className={loadingRuns ? 'animate-spin' : ''} />
          Çalışmaları Yenile
        </button>
      </header>

      {error && <div className="brand-alert brand-alert-error">{error}</div>}
      {info && <div className="brand-alert brand-alert-info">{info}</div>}

      <section className="glass-card brand-section">
        <h3>1) Skorlama Çalışması Seç</h3>
        <div className="brand-run-controls">
          <select
            className="input"
            value={selectedRunId || ''}
            onChange={(event) => {
              const value = Number(event.target.value)
              setSelectedRunId(Number.isNaN(value) || value === 0 ? null : value)
            }}
          >
            <option value="">Bir çalışma seçin...</option>
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                #{run.id} - {run.run_name || 'Adsız'} ({run.status})
              </option>
            ))}
          </select>
        </div>
        {selectedRun && (
          <div className="brand-run-meta">
            <span className={`status status-${selectedRun.status}`}>{selectedRun.status}</span>
            <span>Toplam Kelime: {selectedRun.total_keywords}</span>
          </div>
        )}
      </section>

      <section className="glass-card brand-section">
        <h3>2) Web Sitesini Analiz Et</h3>
        <div className="brand-grid brand-grid-two">
          <div>
            <label className="brand-label">Şirket URL</label>
            <input
              className="input"
              placeholder="https://vepafirca.com.tr/"
              value={companyUrl}
              onChange={(event) => setCompanyUrl(event.target.value)}
            />
          </div>
          <div>
            <label className="brand-label">Rakip URL'leri (opsiyonel, en fazla 3)</label>
            <textarea
              className="input brand-textarea"
              placeholder="https://competitor1.com\nhttps://competitor2.com"
              value={competitorInput}
              onChange={(event) => setCompetitorInput(event.target.value)}
            />
          </div>
        </div>
        <div className="brand-actions">
          <button
            className="btn btn-primary"
            disabled={!selectedRunId || analyzing}
            onClick={() => void handleAnalyze()}
          >
            <Globe size={16} />
            {analyzing ? 'Analiz Ediliyor...' : 'Analizi Başlat'}
          </button>
          {selectedRunId && (
            <button
              className="btn btn-secondary"
              disabled={loadingProfile}
              onClick={() => void fetchProfile(selectedRunId)}
            >
              <RefreshCw size={16} className={loadingProfile ? 'animate-spin' : ''} />
              Profili Yenile
            </button>
          )}
        </div>
        {profile && (
          <div className="brand-status-row">
            <span className={`status status-${profile.status}`}>{profile.status}</span>
            <span>Profil ID: {profile.id}</span>
            {profile.error_message && <span className="brand-error-inline">{profile.error_message}</span>}
          </div>
        )}
      </section>

      <section className="glass-card brand-section">
        <h3>3) Profili Gözden Geçir ve Onayla</h3>
        {!profile ? (
          <p className="empty-text">Seçili çalışma için henüz profil bulunamadı.</p>
        ) : (
          <>
            <div className="brand-grid brand-grid-three">
              <div>
                <label className="brand-label">Şirket Adı (Kilitli)</label>
                <input className="input" value={profileForm.company_name} disabled />
              </div>
              <div>
                <label className="brand-label">Sektör (Kilitli)</label>
                <input className="input" value={profileForm.sector} disabled />
              </div>
              <div>
                <label className="brand-label">Hedef Kitle (Kilitli)</label>
                <input className="input" value={profileForm.target_audience} disabled />
              </div>
            </div>

            <div className="brand-grid brand-grid-two brand-editable-grid">
              {LIST_FIELD_CONFIG.map(({ key, label }) => (
                <div key={key}>
                  <label className="brand-label">{label}</label>
                  <textarea
                    className="input brand-textarea"
                    placeholder="Her satıra bir öge girin. Virgül ayıraç değildir, içeriğin parçası olarak korunur."
                    value={profileForm[key]}
                    onChange={(event) => {
                      const next = event.target.value
                      setProfileForm((prev) => ({ ...prev, [key]: next }))
                    }}
                  />
                </div>
              ))}
            </div>

            <div className="brand-advanced-row">
              <label className="brand-advanced-toggle">
                <input
                  type="checkbox"
                  checked={advancedAnchorMode}
                  onChange={(event) => setAdvancedAnchorMode(event.target.checked)}
                />
                <span>
                  <Settings2 size={15} />
                  Gelişmiş Mod: Anchor Metni Override
                </span>
              </label>
              <p className="brand-hint">
                Kapalıysa anchor_texts backend tarafında onay anında otomatik üretilir. Açıksa girdiğiniz liste override olarak gönderilir.
              </p>
            </div>

            {advancedAnchorMode && (
              <div className="brand-editor">
                <label className="brand-label">Anchor Metinleri (Gelişmiş)</label>
                <textarea
                  className="input brand-textarea brand-anchor-editor"
                  placeholder="Her satira bir oge girin. Virgul ayirac degildir, icerigin parcasi olarak korunur."
                  value={profileForm.anchor_texts}
                  onChange={(event) => setProfileForm((prev) => ({ ...prev, anchor_texts: event.target.value }))}
                />
              </div>
            )}

            {profile.source_pages && profile.source_pages.length > 0 && (
              <div className="brand-source-pages">
                <h4>Kaynak Sayfalar</h4>
                <div className="table-container">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Durum</th>
                        <th>Başlık</th>
                        <th>URL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {profile.source_pages.map((page) => (
                        <tr key={`${page.url}-${page.status}`}>
                          <td>{page.status}</td>
                          <td>{page.title}</td>
                          <td className="brand-url-cell">{page.url}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="brand-actions">
              <button
                className="btn btn-success"
                disabled={!selectedRunId || confirming}
                onClick={() => void handleConfirm()}
              >
                <CheckCircle2 size={16} />
                {confirming ? 'Onaylanıyor...' : 'Profili Onayla'}
              </button>
              <button
                className="btn btn-primary"
                disabled={!selectedRunId || !isProfileConfirmed}
                title={!isProfileConfirmed ? 'Önce profili onaylayın' : 'Kanallara git'}
                onClick={() => {
                  if (!selectedRunId) return
                  navigate(`/channels?run_id=${selectedRunId}`)
                }}
              >
                Sonraki: Kanallar
                <ArrowRight size={16} />
              </button>
            </div>

            {!advancedAnchorMode && !hasAnchors && (
              <p className="brand-hint" style={{ marginTop: 8 }}>
                Anchor listesi şu an ön izleme ekranında boş görünüyor; onay anında yeniden üretilecektir.
              </p>
            )}
          </>
        )}
      </section>

      <section className="glass-card brand-section">
        <h3>4) İlgi Skoru</h3>
        <div className="brand-actions">
          <div className="brand-score-filter">
            <label className="brand-label">Minimum Skor</label>
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              className="input"
              value={minScore}
              onChange={(event) => setMinScore(Number(event.target.value))}
            />
            <p className="brand-hint">
              Minimum skor sadece bu tabloyu filtreler. Kanal atamasında hesaplanan tüm ilgi skorlarının tamamı kullanılır.
            </p>
          </div>
          <button
            className="btn btn-secondary"
            disabled={!selectedRunId || loadingRelevance}
            onClick={() => {
              if (!selectedRunId) return
              void fetchRelevance(selectedRunId, minScore)
            }}
          >
            <RefreshCw size={16} className={loadingRelevance ? 'animate-spin' : ''} />
            İlgi Skorlarını Yenile
          </button>
          <button
            className="btn btn-primary"
            disabled={!selectedRunId || computingRelevance}
            onClick={() => void handleComputeRelevance()}
          >
            <Brain size={16} />
            {computingRelevance ? 'Hesaplanıyor...' : 'İlgi Skoru Hesapla'}
          </button>
        </div>

        {relevanceSummary && (
          <div className="brand-summary">
            <span>Hesaplanan: {relevanceSummary.computed}/{relevanceSummary.total_keywords}</span>
            <span>Başarısız: {relevanceSummary.failed}</span>
            <span>Ortalama: {relevanceSummary.average_relevance}</span>
          </div>
        )}

        {relevance.length === 0 ? (
          <p className="empty-text">Seçili filtre/çalışma için henüz ilgi skoru satırı yok.</p>
        ) : (
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Anahtar Kelime</th>
                  <th>Skor</th>
                  <th>Yöntem</th>
                  <th>Eşleşen Anchor</th>
                </tr>
              </thead>
              <tbody>
                {relevance.slice(0, 60).map((row) => (
                  <tr key={row.keyword_id}>
                    <td>{row.keyword}</td>
                    <td>{row.relevance_score.toFixed(3)}</td>
                    <td>{row.method}</td>
                    <td className="brand-anchor-cell">{row.matched_anchor || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
