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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import {
  workspaceApi,
  FocusSettingsApplyResponse,
  FocusSettingsPreviewResponse,
  ProfileConfirmRequest,
  WorkspaceResponse,
} from '../services/api'
import {
  canHaveFocuses,
  clearPendingFocusUrl,
  editableExcludeThemes,
  extractErrorMessage,
  getWorkspacePhase,
  HARD_EXCLUDE_CHIP_NOTE,
  isFocusWorkspace,
  listToTextarea,
  orderWithFocuses,
  parseExcludedInfo,
  readPendingFocusUrl,
  rememberPendingFocusUrl,
  SOFT_EXCLUDE_HELP,
  SOFT_EXCLUDE_LABEL,
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

type ListFieldKey =
  | 'products'
  | 'services'
  | 'use_cases'
  | 'problems_solved'
  | 'brand_terms'
  | 'protected_themes'
  | 'exclude_themes'

const LIST_FIELD_CONFIG: ReadonlyArray<{ key: ListFieldKey; label: string; help?: string }> = [
  { key: 'products', label: 'Ürünler' },
  { key: 'services', label: 'Hizmetler' },
  { key: 'use_cases', label: 'Kullanım Alanları' },
  { key: 'problems_solved', label: 'Çözülen Problemler' },
  { key: 'brand_terms', label: 'Marka Terimleri' },
  // Korunacak temalar dışlanacak temalardan ÖNCE gösterilir: çakışmada
  // korumanın kazandığını okuma sırası da anlatsın.
  { key: 'protected_themes', label: 'Korunacak Temalar' },
  // Kutu A: yumuşak (eleme yapmaz). Kesin dışlama terimleri (kutu B) burada
  // düzenlenemez; aşağıda salt-okunur çip olarak gösterilir.
  { key: 'exclude_themes', label: SOFT_EXCLUDE_LABEL, help: SOFT_EXCLUDE_HELP },
]

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

function profileToForm(
  profileData?: Record<string, unknown>,
  excludedInfo?: string | null
): ProfileFormState {
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
    // Yalnız kullanıcının düzenleyebildiği A terimleri; B'den gelenler çip olarak ayrı
    exclude_themes: listToTextarea(editableExcludeThemes(profileData.exclude_themes, excludedInfo)),
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

function parseCompanyUrl(raw: string): URL | null {
  try {
    return new URL(raw.includes('//') ? raw : `https://${raw}`)
  } catch {
    return null
  }
}

const isRootPath = (u: URL) => u.pathname === '' || u.pathname === '/'

// ─── Alt sayfalar kartı (onaylı marka) ────────────────────────────────

function FocusesCard({
  parent,
  focuses,
  onCreated,
  onOpen,
  onReload,
}: {
  parent: WorkspaceListRow
  focuses: WorkspaceListRow[]
  onCreated: (focus: WorkspaceResponse) => void
  onOpen: (id: number) => void
  onReload: () => void
}) {
  // Faz 2 diyaloğunda ana sayfayla oluşturulan markanın özgün alt sayfa URL'si
  const [url, setUrl] = useState(() => readPendingFocusUrl(parent.id))
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')
  // Marka ayarlarını alt sayfalara uygula: önizleme -> uygula (Faz 3d)
  const [preview, setPreview] = useState<FocusSettingsPreviewResponse['focuses'] | null>(null)
  const [applyResult, setApplyResult] = useState<FocusSettingsApplyResponse | null>(null)
  const [settingsBusy, setSettingsBusy] = useState(false)

  const openPreview = async () => {
    setSettingsBusy(true)
    setError('')
    setApplyResult(null)
    try {
      const res = await workspaceApi.previewFocusSettings(parent.id)
      setPreview(res.data.focuses)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setSettingsBusy(false)
    }
  }

  const applySettings = async () => {
    setSettingsBusy(true)
    setError('')
    try {
      const res = await workspaceApi.applyFocusSettings(parent.id)
      setApplyResult(res.data)
      setPreview(null)
      onReload()
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setSettingsBusy(false)
    }
  }

  const add = async () => {
    setAdding(true)
    setError('')
    try {
      const res = await workspaceApi.createFocus(parent.id, url.trim())
      clearPendingFocusUrl(parent.id)
      setUrl('')
      onCreated(res.data)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="bpx-section bpx-focuses">
      <div className="bpx-section-title">Alt sayfalar</div>
      {focuses.length === 0 ? (
        <p className="bpx-section-hint">Henüz alt sayfa yok.</p>
      ) : (
        <ul className="bpx-focus-list">
          {focuses.map((f) => (
            <li key={f.id}>
              <button type="button" className="bpx-focus-link" onClick={() => onOpen(f.id)}>
                {f.name}
              </button>
              <span className="bpx-focus-status">{statusLabel(f)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="bpx-focus-add">
        <input
          className="bpx-input"
          type="text"
          aria-label="Alt sayfa URL"
          placeholder="https://… (markanın sitesindeki bir alt sayfa)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={adding}
        />
        <button
          type="button"
          className="bpx-btn-raised"
          onClick={() => void add()}
          disabled={adding || !url.trim()}
        >
          {adding ? <Loader2 size={14} className="spin-icon" /> : <Plus size={14} />}
          Alt sayfa ekle
        </button>
      </div>
      {focuses.length > 0 && !preview && (
        <button
          type="button"
          className="bpx-btn-raised"
          onClick={() => void openPreview()}
          disabled={settingsBusy}
        >
          {settingsBusy && <Loader2 size={14} className="spin-icon" />}
          Marka ayarlarını alt sayfalara uygula
        </button>
      )}
      {preview && (
        <div className="bpx-focus-preview">
          <div className="bpx-section-title">Önizleme</div>
          <ul className="bpx-focus-list">
            {preview.map((f) => (
              <li key={f.id}>
                <strong>{f.name}</strong>{' '}
                {f.busy
                  ? 'İşlem sürüyor — atlanacak'
                  : f.changes.length === 0
                    ? 'Değişiklik yok'
                    : `Değişecek: ${f.changes.map((c) => c.label).join(', ')}`}
              </li>
            ))}
          </ul>
          {preview.some((f) => !f.busy && f.changes.length > 0) && (
            <p className="bpx-section-hint">
              Bu alt sayfadaki yerel düzenlemeler marka ayarlarıyla değiştirilecek.
            </p>
          )}
          <button
            type="button"
            className="bpx-btn-primary"
            onClick={() => void applySettings()}
            disabled={settingsBusy}
          >
            Uygula
          </button>
          <button
            type="button"
            className="bpx-btn-raised"
            onClick={() => setPreview(null)}
            disabled={settingsBusy}
          >
            Vazgeç
          </button>
        </div>
      )}
      {applyResult && (
        <div className="bpx-section-hint">
          <p>{applyResult.applied.length} alt sayfaya uygulandı.</p>
          {applyResult.skipped.map((s) => (
            <p key={s.id}>
              {s.name}: atlandı ({s.reason})
            </p>
          ))}
        </div>
      )}
      {error && <div className="error-banner">{error}</div>}
    </div>
  )
}

// ─── Tek modal: yeni çalışma sihirbazı + mevcut kart detayı ───────────

function WorkspaceModal({
  workspace: initialWorkspace,
  onClose,
  onWorkspaceUpdated,
  onFinished,
  onRestore,
  onOpenWorkspace,
  onReloadList,
  allWorkspaces,
}: {
  workspace: WorkspaceListRow | null // null = "Yeni Çalışma" (url adımı)
  onClose: () => void
  onWorkspaceUpdated: (workspace: WorkspaceResponse) => void
  onFinished: (workspace: WorkspaceResponse) => void
  onRestore: (id: number) => Promise<string | null>
  onOpenWorkspace: (id: number) => void
  onReloadList: () => void
  allWorkspaces: WorkspaceListRow[]
}) {
  const [ws, setWs] = useState<WorkspaceListRow | null>(initialWorkspace)
  // Sayfa hata bandı modal örtüsünün arkasında kalır; geri getirme hatası modalda gösterilir
  const [restoreError, setRestoreError] = useState('')
  const [companyUrl, setCompanyUrl] = useState('')
  const [competitors, setCompetitors] = useState(['', '', ''])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  // Alt sayfa URL'si için kapsam sorusu; 'site' seçilince tekrar sorulmaz
  const [askScope, setAskScope] = useState(false)
  const [scopeChoice, setScopeChoice] = useState<'site' | null>(null)
  // "Ana sayfayla oluştur" denince özgün alt sayfa URL'si (marka onaylanınca "Alt sayfa ekle" önerisi)
  const subpageUrlRef = useRef('')

  // Legacy formlar (keyword-önce çalışmalar + confirmed düzenleme)
  const [profileForm, setProfileForm] = useState<ProfileFormState>(emptyProfileForm())
  // Kaydedilmemiş A/profil düzenlemesi var mı: kirliyken sunucudan yeniden
  // yükleme (örn. kutu B kaydı sonrası) formu SIFIRLAMAZ.
  const profileDirtyRef = useRef(false)
  const editProfileForm = (updater: (form: ProfileFormState) => ProfileFormState) => {
    profileDirtyRef.current = true
    setProfileForm(updater)
  }
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
    profileDirtyRef.current = false
  }, [initialWorkspace?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ws?.profile_data && !profileDirtyRef.current) {
      setProfileForm(profileToForm(ws.profile_data, ws.excluded_info))
    }
  }, [ws?.profile_data, ws?.excluded_info])

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

  const createWorkspace = async (crawlScope: 'site' | 'page') => {
    setAskScope(false)
    setCreating(true)
    setError('')
    try {
      const competitorUrls = competitors.map((c) => c.trim()).filter(Boolean)
      const res = await workspaceApi.create({
        company_url: companyUrl.trim(),
        competitor_urls: competitorUrls.length > 0 ? competitorUrls : undefined,
        flow_version: 'profile_first',
        crawl_scope: crawlScope,
      })
      const created = res.data as WorkspaceResponse
      if (crawlScope === 'site' && subpageUrlRef.current) {
        rememberPendingFocusUrl(created.id, subpageUrlRef.current)
      }
      setWs(workspaceResponseToListRow(created))
      onWorkspaceUpdated(created)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setCreating(false)
    }
  }

  const handleCreate = () => {
    const raw = companyUrl.trim()
    if (!raw) return
    const parsed = parseCompanyUrl(raw)
    // Ayrıştırılamayan URL'yi backend doğrular; kök veya 'site' seçimi sormadan devam eder
    if (!parsed || scopeChoice === 'site' || isRootPath(parsed)) {
      void createWorkspace('site')
      return
    }
    setAskScope(true)
  }

  const chooseSiteScope = () => {
    const parsed = parseCompanyUrl(companyUrl.trim())
    subpageUrlRef.current = companyUrl.trim()
    if (parsed) setCompanyUrl(parsed.origin)
    setScopeChoice('site')
    setAskScope(false)
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
      profileDirtyRef.current = false
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
            {isFocusWorkspace(ws) && <span className="bpx-focus-tag">Alt sayfa</span>}
          </div>
          {ws.parent_brand_profile_id != null && (
            <button
              type="button"
              className="bpx-focus-link"
              onClick={() => onOpenWorkspace(ws.parent_brand_profile_id as number)}
            >
              Marka: {allWorkspaces.find((w) => w.id === ws.parent_brand_profile_id)?.name ?? '—'}
            </button>
          )}
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
          <button
            type="button"
            className="bpx-btn-raised"
            onClick={async () => setRestoreError((await onRestore(ws.id)) || '')}
          >
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
          {askScope && (
            <div className="bpx-scope-ask" role="dialog" aria-labelledby="bpx-scope-ask-title">
              <div id="bpx-scope-ask-title" className="bpx-scope-ask-title">
                Bu adres bir alt sayfa
              </div>
              <p className="bpx-step-intro">
                Girdiğiniz adres sitenin ana sayfası değil. Nasıl devam edelim?
              </p>
              <button type="button" className="bpx-btn-primary" onClick={chooseSiteScope} autoFocus>
                Markayı ana sayfayla oluştur
              </button>
              <p className="bpx-scope-ask-help">
                Sitenin tamamı okunur. Adres alanına ana sayfa yazılır; isterseniz
                değiştirebilirsiniz.
              </p>
              <button
                type="button"
                className="bpx-btn-ghost"
                onClick={() => void createWorkspace('page')}
              >
                Yalnız bu alt sayfa ile devam
              </button>
              <p className="bpx-scope-ask-help">
                Yalnız bu sayfa okunur; profil bu sayfanın konusuna göre çıkarılır.
              </p>
              <button type="button" className="bpx-btn-ghost" onClick={() => setAskScope(false)}>
                Vazgeç
              </button>
            </div>
          )}
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

        {isConfirmed && !isArchived && canHaveFocuses(ws) && (
          <FocusesCard
            key={ws.id}
            parent={ws}
            focuses={allWorkspaces.filter(
              (w) => w.parent_brand_profile_id === ws.id && !w.deleted_at
            )}
            onCreated={(focus) => {
              onWorkspaceUpdated(focus)
              onOpenWorkspace(focus.id)
            }}
            onOpen={onOpenWorkspace}
            onReload={onReloadList}
          />
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
              onModeChange={(mode) =>
                editProfileForm((f) => ({ ...f, location_filter_mode: mode }))
              }
              onFocusCitiesChange={(cities) =>
                editProfileForm((f) => ({ ...f, focus_cities: cities }))
              }
              onExemptTermsChange={(terms) =>
                editProfileForm((f) => ({ ...f, location_exempt_terms: terms }))
              }
              disabled={confirming}
            />

            {LIST_FIELD_CONFIG.map(({ key, label, help }) => {
              const helpId = help ? `profile-field-help-${key}` : undefined
              // Kesin dışlama (kutu B) terimleri: A'nın düzenlenebilir listesinden
              // AYRI, salt-okunur. Kayıtta gönderilmez; sunucu korur.
              const hardTerms = key === 'exclude_themes' ? parseExcludedInfo(ws.excluded_info) : []
              return (
                <div className="form-group" key={key}>
                  <label htmlFor={`profile-field-${key}`}>{label}</label>
                  {help && (
                    <p className="bpx-section-hint" id={helpId}>
                      {help}
                    </p>
                  )}
                  <textarea
                    id={`profile-field-${key}`}
                    className="bpx-textarea"
                    rows={3}
                    aria-describedby={helpId}
                    value={profileForm[key]}
                    onChange={(e) => editProfileForm((f) => ({ ...f, [key]: e.target.value }))}
                  />
                  {hardTerms.length > 0 && (
                    <div className="bpx-chiprow" data-testid="hard-exclude-chips">
                      {hardTerms.map((term) => (
                        <span
                          key={term}
                          className="bpx-chip is-readonly"
                          title="Silmek için aşağıdaki Kesin dışlama kutusunu kullanın"
                        >
                          {term}
                          <span className="bpx-chip-note">{HARD_EXCLUDE_CHIP_NOTE}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}

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
        {restoreError && <div className="error-banner">{restoreError}</div>}
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
  nested,
}: {
  ws: WorkspaceListRow
  nested: boolean
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
      className={`bpx-card${isActive ? ' is-active' : ''}${ws.deleted_at ? ' is-archived' : ''}${
        nested ? ' is-focus-nested' : ''
      }`}
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onOpen()
      }}
    >
      <div className="bpx-card-top">
        <div className="bpx-card-name">
          {ws.name}
          {isFocusWorkspace(ws) && <span className="bpx-focus-tag">Alt sayfa</span>}
        </div>
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

// Türkçe duyarsız arama: "İstanbul" / "istanbul" / "ISTANBUL" ve "akıllı" / "akilli" eşleşir
function normalizeSearchText(value: string): string {
  return value
    .toLocaleLowerCase('tr')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/ı/g, 'i')
    .trim()
}

function workspaceMatchesSearch(ws: WorkspaceListRow, tokens: string[]): boolean {
  if (tokens.length === 0) return true
  const haystack = normalizeSearchText(
    [
      ws.name,
      ws.company_url,
      ws.profile_data?.company_name as string | undefined,
      ws.profile_data?.sector as string | undefined,
    ]
      .filter(Boolean)
      .join(' ')
  )
  return tokens.every((token) => haystack.includes(token))
}

function searchTokens(query: string): string[] {
  return normalizeSearchText(query).split(/\s+/).filter(Boolean)
}

// ─── Ana sayfa ────────────────────────────────────────────────────────

export default function BrandProfile() {
  const [searchParams, setSearchParams] = useSearchParams()
  const workspaceIdParam = searchParams.get('workspace_id')
  const [workspaces, setWorkspaces] = useState<WorkspaceListRow[]>([])
  const [includeArchived, setIncludeArchived] = useState(false)
  const [archivedRows, setArchivedRows] = useState<WorkspaceListRow[]>([])
  const [searchQuery, setSearchQuery] = useState('')
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
      setArchivedRows(allWorkspaces.filter((ws: WorkspaceListRow) => !!ws.deleted_at))
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
    // Arşivde onay yoktu; yalnız aktif alt sayfası olan markada, etkisini bildirmek için sorulur
    const focusIds = workspaces
      .filter((w) => w.parent_brand_profile_id === id && !w.deleted_at)
      .map((w) => w.id)
    const focusCount = focusIds.length
    // Marka arşivlenince alt sayfaları da arşivlenir: seçili alt sayfa da temizlenmeli
    const archivedIds = new Set([id, ...focusIds])
    if (
      focusCount > 0 &&
      !window.confirm(
        `Marka arşivlensin mi? Bu markanın ${focusCount} alt sayfası da arşivlenecek.`
      )
    ) {
      return
    }
    try {
      await workspaceApi.archive(id)
      if (activeWorkspace && archivedIds.has(activeWorkspace.id)) {
        setActiveWorkspace(null)
      }
      if (selectedWs && archivedIds.has(selectedWs.id)) {
        setSelectedWs(null)
        setModalOpen(false)
      }
      fetchWorkspaces()
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    }
  }

  const handleRestore = async (id: number): Promise<string | null> => {
    try {
      await workspaceApi.restore(id)
      fetchWorkspaces()
      return null
    } catch (err: unknown) {
      const message = extractErrorMessage(err)
      setError(message)
      return message
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

  const archivedCount = archivedRows.length
  const tokens = useMemo(() => searchTokens(searchQuery), [searchQuery])
  const visibleWorkspaces = useMemo(
    () => orderWithFocuses(workspaces.filter((ws) => workspaceMatchesSearch(ws, tokens))),
    [workspaces, tokens]
  )
  const visibleIds = useMemo(
    () => new Set(visibleWorkspaces.map((ws) => ws.id)),
    [visibleWorkspaces]
  )
  const knownWorkspaces = useMemo(
    () => [...workspaces, ...archivedRows],
    [workspaces, archivedRows]
  )
  // Arşiv gizliyken aramaya uyan arşivlenmiş çalışmalar — kullanıcıya ipucu olarak gösterilir
  const hiddenArchivedMatches = useMemo(
    () =>
      !includeArchived && tokens.length > 0
        ? archivedRows.filter((ws) => workspaceMatchesSearch(ws, tokens)).length
        : 0,
    [includeArchived, tokens, archivedRows]
  )

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
        <div className="bpx-search">
          <Search size={15} className="bpx-search-icon" aria-hidden="true" />
          <input
            type="search"
            className="bpx-search-input"
            placeholder="Marka, site veya sektör ara..."
            aria-label="Marka çalışması ara"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setSearchQuery('')
            }}
          />
          {searchQuery && (
            <button
              type="button"
              className="bpx-search-clear"
              title="Aramayı temizle"
              aria-label="Aramayı temizle"
              onClick={() => setSearchQuery('')}
            >
              <X size={14} />
            </button>
          )}
        </div>
        {tokens.length > 0 && (
          <span className="bpx-search-count">
            {visibleWorkspaces.length} / {workspaces.length} çalışma
          </span>
        )}
      </div>

      {hiddenArchivedMatches > 0 && (
        <div className="bpx-search-hint">
          Arşivde aramaya uyan {hiddenArchivedMatches} çalışma daha var.{' '}
          <button type="button" onClick={() => setIncludeArchived(true)}>
            Arşivlenmişleri de göster
          </button>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {loading && workspaces.length === 0 && <div className="loading-indicator">Yükleniyor...</div>}

      {!loading && workspaces.length === 0 && (
        <div className="empty-state">
          <AlertCircle size={24} />
          <span>Henüz marka çalışması yok. Yeni bir çalışma oluşturun.</span>
        </div>
      )}

      {workspaces.length > 0 && visibleWorkspaces.length === 0 && (
        <div className="empty-state">
          <Search size={24} />
          <span>“{searchQuery.trim()}” ile eşleşen marka çalışması bulunamadı.</span>
        </div>
      )}

      <div className="bpx-grid">
        {visibleWorkspaces.map((ws) => (
          <BrandCard
            key={ws.id}
            ws={ws}
            nested={
              ws.parent_brand_profile_id != null && visibleIds.has(ws.parent_brand_profile_id)
            }
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
          onOpenWorkspace={(id) => void handleOpen(id)}
          onReloadList={() => void fetchWorkspaces()}
          allWorkspaces={knownWorkspaces}
        />
      )}
    </div>
  )
}
