/**
 * Marka Çalışmaları — Claude Design "Marka Profili" uygulaması.
 *
 * Yapı (tasarım d5a30c21 / marka-profili.jsx):
 *  - Kart grid (bpx-card): ad + statü pili, teal URL, sektör, footer'da
 *    analiz sayısı/tarih + "Bu Çalışmaya Geç".
 *  - Sağ panel yerine ORTALANMIŞ modal (bpx-modal). Aynı modal hem "Yeni
 *    Çalışma" sihirbazını (url → analiz → 5 kart → üretim → keyword+odak)
 *    hem mevcut kartın faz içeriğini taşır — kullanıcı kapatıp kart
 *    üzerinden aynı faza geri dönebilir.
 *  - Legacy (keyword-önce) çalışmalar eski editör/formlarıyla aynı modal
 *    içinde çalışmaya devam eder.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Check,
  CheckCircle2,
  Globe,
  ArrowRight,
  Plus,
  RotateCcw,
  AlertCircle,
  Trash2,
  Loader2,
  X,
  Search,
} from 'lucide-react'
import { useBrandStore } from '../stores/brandStore'
import { workspaceApi, ProfileConfirmRequest, WorkspaceResponse } from '../services/api'
import {
  extractErrorMessage,
  getWorkspacePhase,
  listToTextarea,
  splitNewlineItems,
  statusLabel,
  toActiveWorkspace as workspaceToActiveWorkspace,
  WorkspaceListRow,
  workspaceResponseToListRow,
  WorkspacePhase,
} from './brandProfileState'
import PolicyPanel from '../components/PolicyPanel'
import ProfileReviewCards from './brandProfile/ProfileReviewCards'
import PolicyReviewSection from './brandProfile/PolicyReviewSection'
import KeywordAnchorReview from './brandProfile/KeywordAnchorReview'
import LoadingStep from './brandProfile/LoadingStep'
import AiCompetitorDiscovery from './brandProfile/AiCompetitorDiscovery'
import LocationPolicyControl from '../components/LocationPolicyControl'
import {
  LocationFilterMode,
  readFocusCities,
  readLocationExemptTerms,
  readLocationFilterMode,
} from '../services/locationPolicy'
import './BrandProfile.css'

interface ProfileFormState {
  company_name: string
  sector: string
  target_audience: string
  products: string
  services: string
  use_cases: string
  problems_solved: string
  brand_terms: string
  protected_themes: string
  exclude_themes: string
  location_filter_mode: LocationFilterMode
  focus_cities: string[]
  location_exempt_terms: string[]
}

const LIST_FIELD_CONFIG = [
  { key: 'products', label: 'Ürünler' },
  { key: 'services', label: 'Hizmetler' },
  { key: 'use_cases', label: 'Kullanım Alanları' },
  { key: 'problems_solved', label: 'Çözülen Problemler' },
  { key: 'brand_terms', label: 'Marka Terimleri' },
  // Korunacak temalar dışlanacak temalardan ÖNCE gösterilir: çakışmada
  // korumanın kazandığını okuma sırası da anlatsın.
  { key: 'protected_themes', label: 'Korunacak Temalar' },
  { key: 'exclude_themes', label: 'Dışlanacak Temalar' },
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
    protected_themes: '',
    exclude_themes: '',
    location_filter_mode: 'none',
    focus_cities: [],
    location_exempt_terms: [],
  }
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
    protected_themes: listToTextarea(profileData.protected_themes),
    exclude_themes: listToTextarea(profileData.exclude_themes),
    location_filter_mode: readLocationFilterMode(profileData),
    focus_cities: readFocusCities(profileData),
    location_exempt_terms: readLocationExemptTerms(profileData),
  }
}

function buildProfilePayload(form: ProfileFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  LIST_FIELD_CONFIG.forEach(({ key }) => {
    payload[key] = splitNewlineItems(form[key])
  })
  payload.location_filter_mode = form.location_filter_mode
  payload.focus_cities = form.focus_cities
  payload.location_exempt_terms = form.location_exempt_terms
  return payload
}

function hasGeneratedProfile(workspace: {
  profile_data: Record<string, unknown> | null
  status: string
}) {
  const profile = workspace.profile_data
  if (!profile || typeof profile !== 'object') return false
  const anchors = Array.isArray(profile.anchor_texts)
    ? profile.anchor_texts.filter((item: unknown) => String(item || '').trim())
    : []
  return (workspace.status === 'draft' || workspace.status === 'confirmed') && anchors.length > 0
}

// Statü pili rengi (tasarımdaki green/amber/teal/red eşlemesi)
function pillTone(phase: WorkspacePhase): 'green' | 'amber' | 'teal' | 'red' {
  if (phase === 'confirmed') return 'green'
  if (phase === 'failed') return 'red'
  if (
    phase === 'keyword_anchor_review' ||
    phase === 'profile_cards_review' ||
    phase === 'competitor_review' ||
    phase === 'keywords_review' ||
    phase === 'profile_review'
  )
    return 'amber'
  return 'teal'
}

const ANALYZE_STEPS = [
  'Site içeriği taranıyor',
  'Ürün ve hizmetler ayıklanıyor',
  'Hedef kitle belirleniyor',
  'Marka profili hazırlanıyor',
]
const GENERATE_STEPS = [
  'Profil analiz ediliyor',
  'Keyword havuzu oluşturuluyor',
  'Marka odakları eşleştiriliyor',
]

const MODAL_POLL_MS = 3000
const LOADING_PHASES: WorkspacePhase[] = [
  'analyzing_site',
  'generating_keywords',
  'analyzing_keywords',
  'generating_profile',
]

// ─── Tek modal: yeni çalışma sihirbazı + mevcut kart detayı ───────────

function WorkspaceModal({
  workspace: initialWorkspace,
  onClose,
  onWorkspaceUpdated,
  onFinished,
  onRestore,
}: {
  workspace: WorkspaceListRow | null // null = "Yeni Çalışma" (url adımı)
  onClose: () => void
  onWorkspaceUpdated: (workspace: WorkspaceResponse) => void
  onFinished: (workspace: WorkspaceResponse) => void
  onRestore: (id: number) => void
}) {
  const [ws, setWs] = useState<WorkspaceListRow | null>(initialWorkspace)
  const [companyUrl, setCompanyUrl] = useState('')
  const [competitors, setCompetitors] = useState(['', '', ''])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  // Legacy formlar (keyword-önce çalışmalar + confirmed düzenleme)
  const [profileForm, setProfileForm] = useState<ProfileFormState>(emptyProfileForm())
  const [keywordDraft, setKeywordDraft] = useState<string[]>(
    initialWorkspace?.suggested_keywords || []
  )
  const [approvingKeywords, setApprovingKeywords] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [competitorDiscoveryActive, setCompetitorDiscoveryActive] = useState(false)
  const [startingKeywords, setStartingKeywords] = useState(false)

  const navigate = useNavigate()
  const setActiveWorkspace = useBrandStore((s) => s.setActiveWorkspace)

  useEffect(() => {
    setWs(initialWorkspace)
    setKeywordDraft(initialWorkspace?.suggested_keywords || [])
    setCompetitorDiscoveryActive(false)
  }, [initialWorkspace?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ws?.profile_data) setProfileForm(profileToForm(ws.profile_data))
  }, [ws?.profile_data])

  useEffect(() => {
    setKeywordDraft(ws?.suggested_keywords || [])
  }, [ws?.suggested_keywords])

  // ESC ile kapat
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const phase: WorkspacePhase | 'url' = ws ? getWorkspacePhase(ws) : 'url'

  // Yükleme fazlarında modal kendi poll'unu yapar (sihirbaz akışı modal
  // içinde ilerler; kullanıcı kapatırsa sayfa polling'i zaten devam eder)
  useEffect(() => {
    if (!ws || !LOADING_PHASES.includes(phase as WorkspacePhase)) return
    const timer = window.setInterval(async () => {
      try {
        const res = await workspaceApi.get(ws.id)
        const fresh = workspaceResponseToListRow(res.data as WorkspaceResponse, ws)
        setWs(fresh)
        onWorkspaceUpdated(res.data as WorkspaceResponse)
      } catch {
        /* poll hatası sessiz — sonraki tikte tekrar dener */
      }
    }, MODAL_POLL_MS)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws?.id, phase])

  const handleCreate = async () => {
    if (!companyUrl.trim()) return
    setCreating(true)
    setError('')
    try {
      const competitorUrls = competitors.map((c) => c.trim()).filter(Boolean)
      const res = await workspaceApi.create({
        company_url: companyUrl.trim(),
        competitor_urls: competitorUrls.length > 0 ? competitorUrls : undefined,
        flow_version: 'profile_first',
      })
      const created = res.data as WorkspaceResponse
      setWs(workspaceResponseToListRow(created))
      onWorkspaceUpdated(created)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setCreating(false)
    }
  }

  const handleModalWorkspaceUpdated = (updated: WorkspaceResponse) => {
    setWs((prev) => workspaceResponseToListRow(updated, prev || undefined))
    onWorkspaceUpdated(updated)
  }

  const handleFinished = (confirmed: WorkspaceResponse) => {
    onFinished(confirmed)
  }

  const continueToKeywordGeneration = async () => {
    if (!ws) return
    setStartingKeywords(true)
    setError('')
    try {
      const response = await workspaceApi.approveProfile(ws.id, { rerun_keywords: true })
      setCompetitorDiscoveryActive(false)
      handleModalWorkspaceUpdated(response.data)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setStartingKeywords(false)
    }
  }

  // ── Legacy: keyword onayı (keyword-önce akış) ──
  const handleApproveKeywordsLegacy = async () => {
    if (!ws) return
    setApprovingKeywords(true)
    setError('')
    try {
      const keywords = keywordDraft.map((kw) => kw.trim()).filter(Boolean)
      const res = await workspaceApi.approveKeywords(ws.id, { keywords })
      handleModalWorkspaceUpdated(res.data as WorkspaceResponse)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setApprovingKeywords(false)
    }
  }

  // ── Legacy: profil onayı / confirmed düzenleme ──
  const handleConfirm = async () => {
    if (!ws) return
    setConfirming(true)
    setError('')
    try {
      const payload = buildProfilePayload(profileForm)
      const res = await workspaceApi.confirm(ws.id, {
        profile_data: payload,
      } as ProfileConfirmRequest)
      const confirmed = res.data as WorkspaceResponse
      if (confirmed.id !== ws.id) {
        setError('Onay cevabı beklenen çalışma ile eşleşmedi. Listeyi yenileyip tekrar deneyin.')
        return
      }
      handleFinished(confirmed)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setConfirming(false)
    }
  }

  const handleGoKeywords = () => {
    if (!ws) return
    setActiveWorkspace(workspaceToActiveWorkspace(ws))
    navigate('/keywords?tab=google-ads')
  }

  const isArchived = !!ws?.deleted_at
  const isDraft = ws?.status === 'draft'
  const isConfirmed = ws?.status === 'confirmed'
  const profileReady = ws ? hasGeneratedProfile(ws) : false
  const canContinueToKeywords = isConfirmed && profileReady
  const canEditKeywordsLegacy =
    phase === 'keywords_review' ||
    (phase === 'failed' &&
      !!ws?.suggested_keywords?.length &&
      ws?.onboarding_flow !== 'profile_first')

  const urlValid = /\./.test(companyUrl)
  const isLoadingPhase = LOADING_PHASES.includes(phase as WorkspacePhase)

  const renderHeader = () => {
    if (phase === 'url') {
      return (
        <div className="bpx-modal-header">
          <div className="bpx-modal-header-main">
            <div className="bpx-modal-title">Yeni Çalışma Oluştur</div>
          </div>
          <button type="button" className="bpx-close" onClick={onClose} aria-label="Kapat">
            <X size={17} />
          </button>
        </div>
      )
    }
    if (isLoadingPhase || !ws) return null
    const tone = pillTone(phase as WorkspacePhase)
    return (
      <div className="bpx-modal-header">
        <div className="bpx-modal-header-main">
          <div className="bpx-modal-title-row">
            <span className="bpx-modal-title">{ws.name}</span>
            <span className={`bpx-pill is-${tone}`}>{statusLabel(ws)}</span>
          </div>
          <a className="bpx-modal-url" href={ws.company_url} onClick={(e) => e.preventDefault()}>
            <Globe size={14} /> {ws.company_url}
          </a>
        </div>
        {canContinueToKeywords && !isArchived && (
          <button type="button" className="bpx-btn-primary" onClick={handleGoKeywords}>
            Anahtar Kelimelere Geç <ArrowRight size={15} strokeWidth={2.2} />
          </button>
        )}
        {isArchived && (
          <button type="button" className="bpx-btn-raised" onClick={() => onRestore(ws.id)}>
            <RotateCcw size={14} /> Arşivden Çıkar
          </button>
        )}
        <button type="button" className="bpx-close" onClick={onClose} aria-label="Kapat">
          <X size={17} />
        </button>
      </div>
    )
  }

  const renderBody = () => {
    if (ws && phase === 'competitor_review' && !competitorDiscoveryActive) {
      return (
        <div className="bpx-fade acd-offer">
          <div className="acd-offer-icon">
            <Search size={22} />
          </div>
          <div className="bpx-loading-title">Rakiplerinizi bulalım mı?</div>
          <div className="bpx-loading-sub">
            AI, Google araması ile markanızın gerçek rakiplerini bulup doğrular. Onayladığınız rakip
            markalar kelime çalışmalarınızda otomatik olarak engellenir.
          </div>
          <div className="acd-offer-actions">
            <button
              type="button"
              className="bpx-btn-ghost"
              onClick={() => void continueToKeywordGeneration()}
              disabled={startingKeywords}
            >
              {startingKeywords ? <Loader2 size={15} className="spin-icon" /> : null}
              Şimdi değil, keywordleri üret
            </button>
            <button
              type="button"
              className="bpx-btn-primary"
              onClick={() => setCompetitorDiscoveryActive(true)}
              disabled={startingKeywords}
            >
              <Search size={15} /> Evet, rakipleri bul
            </button>
          </div>
        </div>
      )
    }
    if (ws && phase === 'competitor_review' && competitorDiscoveryActive) {
      return (
        <div className="bpx-fade">
          <AiCompetitorDiscovery
            workspaceId={ws.id}
            confirmed
            variant="wizard"
            autoStart
            onFinish={() => void continueToKeywordGeneration()}
          />
        </div>
      )
    }
    if (phase === 'url') {
      return (
        <div className="bpx-fade">
          <p className="bpx-step-intro">
            Marka URL'nizi girin; sistem sitenizi inceleyip marka profilinizi çıkarır. Sonraki
            adımda profili kontrol edip onaylayacaksınız.
          </p>
          <label className="bpx-label">
            Marka URL <span className="req">*</span>
          </label>
          <input
            className="bpx-input"
            type="text"
            placeholder="https://..."
            value={companyUrl}
            autoFocus
            onChange={(e) => setCompanyUrl(e.target.value)}
          />
          <div className="bpx-field-gap" />
          <label className="bpx-label">
            Rakip URL'leri <span className="opt">(opsiyonel, en fazla 3)</span>
          </label>
          <div className="bpx-stack">
            {competitors.map((value, idx) => (
              <input
                key={idx}
                className="bpx-input"
                type="text"
                placeholder={`Rakip ${idx + 1}`}
                value={value}
                onChange={(e) =>
                  setCompetitors((prev) => prev.map((c, i) => (i === idx ? e.target.value : c)))
                }
              />
            ))}
          </div>
          {error && <div className="error-banner">{error}</div>}
          <div className="bpx-modal-footer is-end bpx-inline-footer">
            <button type="button" className="bpx-btn-ghost" onClick={onClose} disabled={creating}>
              İptal
            </button>
            <button
              type="button"
              className="bpx-btn-primary"
              onClick={handleCreate}
              disabled={creating || !urlValid}
            >
              {creating ? (
                <Loader2 size={15} className="spin-icon" />
              ) : (
                <Search size={15} strokeWidth={2.2} />
              )}
              Analizi Başlat
            </button>
          </div>
        </div>
      )
    }

    if (!ws) return null

    if (phase === 'analyzing_site') {
      return (
        <LoadingStep
          title="Siteniz inceleniyor…"
          sub="Marka profilinizi çıkarıyoruz. Bu birkaç dakika sürebilir; pencereyi kapatsanız da işlem arka planda devam eder."
          steps={ANALYZE_STEPS}
        />
      )
    }
    if (phase === 'generating_keywords') {
      return (
        <LoadingStep
          title="Keyword önerileri hazırlanıyor…"
          sub="Onayladığınız profile göre size özel anahtar kelimeler ve marka odakları üretiliyor."
          steps={GENERATE_STEPS}
        />
      )
    }
    if (phase === 'profile_cards_review') {
      return <ProfileReviewCards workspace={ws} onApproved={handleModalWorkspaceUpdated} />
    }
    if (phase === 'keyword_anchor_review') {
      return (
        <KeywordAnchorReview
          workspace={ws}
          onWorkspaceUpdated={handleModalWorkspaceUpdated}
          onApproved={handleFinished}
        />
      )
    }

    // ── Legacy fazlar + failed + confirmed düzenleme ──
    return (
      <div className="bpx-fade">
        {phase === 'failed' && (
          <div className="error-banner">
            Analiz başarısız oldu. Yeni bir çalışma oluşturmayı deneyebilir veya bu çalışmayı
            arşivleyebilirsiniz.
          </div>
        )}

        {(phase === 'analyzing_keywords' || phase === 'generating_profile') && (
          <LoadingStep
            title={phase === 'analyzing_keywords' ? 'Site okunuyor…' : 'Marka profili üretiliyor…'}
            sub={
              phase === 'analyzing_keywords'
                ? 'İlk 10 keyword önerisi hazırlanıyor.'
                : 'Onaylanan keywordlere göre marka profili üretiliyor.'
            }
            steps={phase === 'analyzing_keywords' ? ANALYZE_STEPS : GENERATE_STEPS}
          />
        )}

        {canEditKeywordsLegacy && (
          <div className="suggested-keywords">
            <div className="section-title-row">
              <h4>Onerilen Keywordler</h4>
              <button
                type="button"
                className="bpx-btn-raised"
                onClick={() =>
                  setKeywordDraft((items) => (items.length >= 20 ? items : [...items, '']))
                }
                disabled={keywordDraft.length >= 20 || approvingKeywords}
              >
                <Plus size={14} /> Ekle
              </button>
            </div>
            <div className="keyword-editor">
              {keywordDraft.map((kw, idx) => (
                <div className="keyword-editor-row" key={idx}>
                  <input
                    type="text"
                    value={kw}
                    placeholder={`Keyword ${idx + 1}`}
                    onChange={(e) =>
                      setKeywordDraft((items) =>
                        items.map((item, i) => (i === idx ? e.target.value : item))
                      )
                    }
                    disabled={approvingKeywords}
                  />
                  <button
                    type="button"
                    className="card-icon-action danger"
                    title="Sil"
                    aria-label="Sil"
                    onClick={() => setKeywordDraft((items) => items.filter((_, i) => i !== idx))}
                    disabled={approvingKeywords || keywordDraft.length <= 1}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="bpx-btn-primary"
                onClick={handleApproveKeywordsLegacy}
                disabled={approvingKeywords || keywordDraft.every((kw) => !kw.trim())}
              >
                {approvingKeywords ? (
                  <Loader2 size={14} className="spin-icon" />
                ) : (
                  <CheckCircle2 size={14} />
                )}
                {approvingKeywords ? 'Profil Uretiliyor...' : 'Keywordleri Onayla'}
              </button>
            </div>
          </div>
        )}

        {(isDraft || isConfirmed) && ws.profile_data && (
          <div className="profile-form">
            <div className="form-group">
              <label>Firma Adı</label>
              <input className="bpx-input" type="text" value={profileForm.company_name} readOnly />
            </div>
            <div className="form-group">
              <label>Sektör</label>
              <input className="bpx-input" type="text" value={profileForm.sector} readOnly />
            </div>
            <div className="form-group">
              <label>Hedef Kitle</label>
              <input
                className="bpx-input"
                type="text"
                value={profileForm.target_audience}
                readOnly
              />
            </div>

            <LocationPolicyControl
              workspaceId={ws.id}
              companyName={profileForm.company_name}
              brandTerms={splitNewlineItems(profileForm.brand_terms)}
              mode={profileForm.location_filter_mode}
              focusCities={profileForm.focus_cities}
              exemptTerms={profileForm.location_exempt_terms}
              onModeChange={(mode) => setProfileForm((f) => ({ ...f, location_filter_mode: mode }))}
              onFocusCitiesChange={(cities) =>
                setProfileForm((f) => ({ ...f, focus_cities: cities }))
              }
              onExemptTermsChange={(terms) =>
                setProfileForm((f) => ({ ...f, location_exempt_terms: terms }))
              }
              disabled={confirming}
            />

            {LIST_FIELD_CONFIG.map(({ key, label }) => (
              <div className="form-group" key={key}>
                <label>{label}</label>
                <textarea
                  className="bpx-textarea"
                  rows={3}
                  value={profileForm[key as keyof ProfileFormState] as string}
                  onChange={(e) =>
                    setProfileForm((f) => ({
                      ...f,
                      [key]: e.target.value,
                    }))
                  }
                />
              </div>
            ))}

            <div className="bpx-modal-footer is-end bpx-inline-footer">
              <button
                type="button"
                className="bpx-btn-primary"
                onClick={handleConfirm}
                disabled={confirming}
              >
                {confirming ? (
                  <Loader2 size={16} className="spin-icon" />
                ) : (
                  <Check size={16} strokeWidth={2.4} />
                )}
                {isDraft ? 'Profili Onayla' : 'Değişiklikleri Kaydet'}
              </button>
            </div>

            {/* Plan v13: rakip kararları + konu dışlamaları onay SONRASI da
                düzenlenebilir (legacy dahil) — yeni policy/review yüzeyi */}
            <PolicyReviewSection
              workspace={ws}
              onSaved={() => {
                void workspaceApi.get(ws.id).then((res) => {
                  const fresh = workspaceResponseToListRow(res.data as WorkspaceResponse, ws)
                  setWs(fresh)
                  onWorkspaceUpdated(res.data as WorkspaceResponse)
                })
              }}
            />
          </div>
        )}

        {error && <div className="error-banner">{error}</div>}
      </div>
    )
  }

  return (
    <div className="bpx-overlay" onClick={onClose}>
      <div className="bpx-modal" onClick={(e) => e.stopPropagation()}>
        {renderHeader()}
        <div className={`bpx-modal-body${isLoadingPhase ? ' is-loading' : ''}`}>{renderBody()}</div>
      </div>
    </div>
  )
}

// ─── Kart ─────────────────────────────────────────────────────────────

function BrandCard({
  ws,
  isActive,
  onOpen,
  onActivate,
  onArchive,
  onRestore,
}: {
  ws: WorkspaceListRow
  isActive: boolean
  onOpen: () => void
  onActivate: () => void
  onArchive: () => void
  onRestore: () => void
}) {
  const phase = getWorkspacePhase(ws)
  const tone = pillTone(phase)
  const busy =
    phase === 'analyzing_site' ||
    phase === 'generating_keywords' ||
    phase === 'analyzing_keywords' ||
    phase === 'generating_profile'

  return (
    <div
      className={`bpx-card${isActive ? ' is-active' : ''}${ws.deleted_at ? ' is-archived' : ''}`}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onOpen()
      }}
    >
      <div className="bpx-card-top">
        <div className="bpx-card-name">{ws.name}</div>
        <div className="bpx-card-actions">
          <span className={`bpx-pill is-${tone}`}>
            {busy && <Loader2 size={9} className="spin-icon" />} {statusLabel(ws)}
          </span>
          {!ws.deleted_at && (
            <button
              type="button"
              className="bpx-icon-btn"
              title="Arşivle"
              aria-label="Arşivle"
              onClick={(e) => {
                e.stopPropagation()
                onArchive()
              }}
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>
      <div className="bpx-card-url">
        <Globe size={14} /> <span>{ws.company_url}</span>
      </div>
      <div className="bpx-card-desc">
        {(ws.profile_data?.sector as string) || 'Sektör bilgisi yok'}
      </div>
      <div className="bpx-card-footer">
        <div>
          <div className="bpx-card-meta-line">{ws.run_count} analiz</div>
          <div className="bpx-card-date">{new Date(ws.created_at).toLocaleDateString('tr')}</div>
        </div>
        {ws.deleted_at ? (
          <button
            type="button"
            className="bpx-btn-raised"
            onClick={(e) => {
              e.stopPropagation()
              onRestore()
            }}
          >
            <RotateCcw size={14} /> Geri Getir
          </button>
        ) : isActive ? (
          <span className="bpx-card-active-note">Aktif Çalışma</span>
        ) : (
          <button
            type="button"
            className="bpx-btn-raised"
            onClick={(e) => {
              e.stopPropagation()
              onActivate()
            }}
          >
            Bu Çalışmaya Geç
          </button>
        )}
      </div>
    </div>
  )
}

const POLLING_STATUSES = new Set(['pending', 'running'])
const POLL_INTERVAL_MS = 3000

// ─── Ana sayfa ────────────────────────────────────────────────────────

export default function BrandProfile() {
  const [searchParams, setSearchParams] = useSearchParams()
  const workspaceIdParam = searchParams.get('workspace_id')
  const [workspaces, setWorkspaces] = useState<WorkspaceListRow[]>([])
  const [includeArchived, setIncludeArchived] = useState(false)
  const [archivedCount, setArchivedCount] = useState(0)
  const [selectedWs, setSelectedWs] = useState<WorkspaceListRow | null>(null)
  const [modalOpen, setModalOpen] = useState(false) // selectedWs=null + modalOpen → yeni çalışma
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const setActiveWorkspace = useBrandStore((s) => s.setActiveWorkspace)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchWorkspaces = useCallback(async () => {
    setLoading(true)
    try {
      const res = await workspaceApi.list(true)
      const allWorkspaces = res.data || []
      setArchivedCount(allWorkspaces.filter((ws: WorkspaceListRow) => !!ws.deleted_at).length)
      setWorkspaces(
        includeArchived
          ? allWorkspaces
          : allWorkspaces.filter((ws: WorkspaceListRow) => !ws.deleted_at)
      )
      setSelectedWs((prev) => {
        if (!prev) return prev
        const fresh = allWorkspaces.find((ws: WorkspaceListRow) => ws.id === prev.id)
        return fresh ? { ...prev, ...fresh } : prev
      })
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [includeArchived])

  useEffect(() => {
    fetchWorkspaces()
  }, [fetchWorkspaces])

  useEffect(() => {
    if (!workspaceIdParam) return
    const workspaceId = Number(workspaceIdParam)
    if (!Number.isInteger(workspaceId) || workspaceId <= 0) {
      setError('Gecersiz marka calismasi id parametresi.')
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.delete('workspace_id')
          return next
        },
        { replace: true }
      )
      return
    }

    let cancelled = false
    workspaceApi
      .get(workspaceId)
      .then((res) => {
        if (cancelled) return
        const workspace = res.data as WorkspaceResponse
        if (workspace.deleted_at) {
          setSelectedWs(null)
          setModalOpen(false)
          setError('Bu marka calismasi arsivlenmis. Panel acilmadi.')
          setSearchParams(
            (prev) => {
              const next = new URLSearchParams(prev)
              next.delete('workspace_id')
              return next
            },
            { replace: true }
          )
          return
        }
        setSelectedWs(workspaceResponseToListRow(workspace))
        setModalOpen(true)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setSelectedWs(null)
        setModalOpen(false)
        setError(`Marka calismasi acilamadi: ${extractErrorMessage(err)}`)
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev)
            next.delete('workspace_id')
            return next
          },
          { replace: true }
        )
      })

    return () => {
      cancelled = true
    }
  }, [workspaceIdParam, setSearchParams])

  // Analizi süren çalışmaları listede canlı tut
  useEffect(() => {
    const pollingIds = workspaces
      .filter((ws) => !ws.deleted_at && POLLING_STATUSES.has(ws.status))
      .map((ws) => ws.id)

    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }

    if (pollingIds.length === 0) return

    const tick = async () => {
      try {
        const results = await Promise.all(pollingIds.map((id) => workspaceApi.get(id)))
        const updated = results.map((r) => r.data as WorkspaceListRow)
        setWorkspaces((prev) =>
          prev.map((ws) => {
            const fresh = updated.find((u) => u.id === ws.id)
            return fresh ? { ...ws, ...fresh } : ws
          })
        )
        setSelectedWs((prev) => {
          if (!prev) return prev
          const fresh = updated.find((u) => u.id === prev.id)
          return fresh ? workspaceResponseToListRow(fresh as WorkspaceResponse, prev) : prev
        })
        updated.forEach((fresh) => {
          if (activeWorkspace?.id === fresh.id && fresh.status !== activeWorkspace.status) {
            setActiveWorkspace(workspaceToActiveWorkspace(fresh))
          }
        })
      } catch {
        /* sessiz — poll devam eder */
      }
      pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS)
    }

    pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS)

    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    }
  }, [workspaces, activeWorkspace, setActiveWorkspace])

  const handleArchive = async (id: number) => {
    try {
      await workspaceApi.archive(id)
      if (activeWorkspace?.id === id) {
        setActiveWorkspace(null)
      }
      if (selectedWs?.id === id) {
        setSelectedWs(null)
        setModalOpen(false)
      }
      fetchWorkspaces()
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    }
  }

  const handleRestore = async (id: number) => {
    try {
      await workspaceApi.restore(id)
      fetchWorkspaces()
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    }
  }

  const handleOpen = async (id: number) => {
    try {
      const res = await workspaceApi.get(id)
      setSelectedWs({ ...(res.data as WorkspaceResponse), run_count: 0 } as WorkspaceListRow)
      setModalOpen(true)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    }
  }

  const handleActivateWorkspace = (ws: WorkspaceListRow) => {
    if (ws.deleted_at) return
    setActiveWorkspace(workspaceToActiveWorkspace(ws))
  }

  const syncWorkspaceResponse = (workspace: WorkspaceResponse) => {
    setWorkspaces((prev) => {
      const exists = prev.some((ws) => ws.id === workspace.id)
      if (!exists) return [workspaceResponseToListRow(workspace), ...prev]
      return prev.map((ws) =>
        ws.id === workspace.id ? workspaceResponseToListRow(workspace, ws) : ws
      )
    })
    if (activeWorkspace?.id === workspace.id) {
      setActiveWorkspace(workspaceToActiveWorkspace(workspace))
    }
  }

  const handleWorkspaceUpdated = (workspace: WorkspaceResponse) => {
    setSelectedWs((prev) =>
      prev && prev.id !== workspace.id
        ? prev
        : workspaceResponseToListRow(workspace, prev || undefined)
    )
    syncWorkspaceResponse(workspace)
  }

  // Sihirbaz bitti (Onayla ve analize geç): aktifleştir + kelimelere yönlendir
  const handleFinished = (workspace: WorkspaceResponse) => {
    syncWorkspaceResponse(workspace)
    setActiveWorkspace(workspaceToActiveWorkspace(workspace))
    setSelectedWs(null)
    setModalOpen(false)
    fetchWorkspaces()
    navigate('/keywords?tab=google-ads')
  }

  const closeModal = () => {
    setSelectedWs(null)
    setModalOpen(false)
    fetchWorkspaces()
  }

  return (
    <div className="brand-profile-page">
      <h1 className="bpx-page-title">Marka Çalışmaları</h1>

      <div className="bpx-toolbar">
        <button
          type="button"
          className="bpx-btn-primary"
          onClick={() => {
            setSelectedWs(null)
            setModalOpen(true)
          }}
        >
          <Plus size={16} strokeWidth={2.4} /> Yeni Çalışma Oluştur
        </button>
        <label className={`archive-toggle ${includeArchived ? 'active' : ''}`}>
          <input
            type="checkbox"
            checked={includeArchived}
            disabled={archivedCount === 0}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          <span className="archive-toggle-track" />
          <span>Arşivlenmiş Çalışmaları Göster</span>
          <strong>{archivedCount}</strong>
        </label>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {loading && workspaces.length === 0 && <div className="loading-indicator">Yükleniyor...</div>}

      {!loading && workspaces.length === 0 && (
        <div className="empty-state">
          <AlertCircle size={24} />
          <span>Henüz marka çalışması yok. Yeni bir çalışma oluşturun.</span>
        </div>
      )}

      <div className="bpx-grid">
        {workspaces.map((ws) => (
          <BrandCard
            key={ws.id}
            ws={ws}
            isActive={activeWorkspace?.id === ws.id}
            onOpen={() => handleOpen(ws.id)}
            onActivate={() => handleActivateWorkspace(ws)}
            onArchive={() => handleArchive(ws.id)}
            onRestore={() => handleRestore(ws.id)}
          />
        ))}
      </div>

      {/* Plan v13: Gelişmiş ayarlar (conquest istisnaları + manuel terimler).
          Başlıkta workspace adı — yanlış markanın politikasını düzenleme
          riskine karşı bağlam açıkça görünür. */}
      {activeWorkspace?.id && (
        <PolicyPanel workspaceId={activeWorkspace.id} workspaceName={activeWorkspace.name} />
      )}

      {modalOpen && (
        <WorkspaceModal
          workspace={selectedWs}
          onClose={closeModal}
          onWorkspaceUpdated={handleWorkspaceUpdated}
          onFinished={handleFinished}
          onRestore={handleRestore}
        />
      )}
    </div>
  )
}
