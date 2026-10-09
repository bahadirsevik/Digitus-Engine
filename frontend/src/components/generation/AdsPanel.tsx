/**
 * Google Ads RSA üretim paneli — Claude Design "ADS" tasarımı.
 * Kanal sayfası (ADS) içinde inline çalışır; run seçimi dışarıdan gelir.
 * Kök eleman display:contents — form kartı ChannelWorkspaceView'daki iki
 * kolonlu gridin sağ hücresine, banner/sonuçlar tam genişliğe oturur.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle, RefreshCw, Sparkles, X } from 'lucide-react'
import {
  useTaskPolling,
  useScopedTaskId,
  getWorkspaceTaskKey,
  setStoredTaskId,
} from '../../hooks/useTaskPolling'
import ErrorBanner from '../ErrorBanner'
import { useBrandStore } from '../../stores/brandStore'
import {
  apiErrorMessage,
  apiErrorStatus,
  brandProfileApi,
  generationApi,
  policyStaleFromError,
} from '../../services/api'
import type {
  AdGenerationSetSummary,
  AdGroup,
  AdsResult,
  AdsSetListResult,
  ScoringRun,
} from '../../types/models'
import '../../pages/Generation.css'

// Set durum rozetleri (Faz E versiyonlama)
const SET_STATUS_LABELS: Record<AdGenerationSetSummary['status'], string> = {
  generating: 'Üretiliyor',
  active: 'Aktif',
  draft: 'Taslak',
  archived: 'Arşiv',
  failed: 'Hatalı',
}

const canActivateSet = (s: AdGenerationSetSummary) =>
  !s.is_stale && (s.status === 'draft' || s.status === 'archived') && (s.groups_count ?? 0) > 0

const canRegenerateFromSet = (s: AdGenerationSetSummary | null | undefined) =>
  !!s && !s.is_stale && (s.status === 'active' || s.status === 'draft' || s.status === 'archived')

const adGroupName = (group: AdGroup) => group.group_name || group.name || 'Reklam Grubu'
const adGroupTheme = (group: AdGroup) => group.group_theme || group.theme || ''
const adGroupKeywords = (group: AdGroup) => group.target_keywords || group.keywords || []
const displayUrlFrom = (url: string) => {
  if (!url) return 'example.com'
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return (
      url
        .replace(/^https?:\/\//, '')
        .replace(/^www\./, '')
        .split('/')[0] || url
    )
  }
}

// Tasarımdaki üretim adımları; gerçek task progress eşiklerinden türetilir
const RSA_STEPS = [
  'Havuz kelimeleri kümeleniyor',
  'Reklam grupları oluşturuluyor',
  'Yapay zekâ · başlık üretimi',
  'Yapay zekâ · açıklama üretimi',
  'Negatif kelime listesi çıkarılıyor',
  'Sonuçlar derleniyor',
]

function rsaStepFromProgress(progress: number): number {
  if (progress < 15) return 0
  if (progress < 30) return 1
  if (progress < 60) return 2
  if (progress < 80) return 3
  if (progress < 92) return 4
  return 5
}

function GenBanner({ progress }: { progress: number }) {
  const step = rsaStepFromProgress(progress)
  const pct = Math.max(progress, 3)
  return (
    <div className="adsx-banner">
      <div className="adsx-banner-top">
        <span className="adsx-banner-icon">
          <span className="chx-spin">
            <RefreshCw size={19} strokeWidth={2.4} />
          </span>
        </span>
        <div className="adsx-banner-copy">
          <div className="adsx-banner-title">Reklamlar üretiliyor</div>
          <div className="adsx-banner-sub">
            Havuzdaki kelimeler için RSA reklam grupları yapay zekâ ile oluşturuluyor.{' '}
            <b className="warn">Bu işlem 3–5 dakika sürebilir.</b> Bu ekranı kapatabilirsiniz —
            işlem arka planda sürer, durumu <b className="strong">Görevler</b>'den de
            izleyebilirsiniz.
          </div>
        </div>
      </div>
      <div className="adsx-progressbar">
        <div className="adsx-progressbar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="adsx-steps">
        {RSA_STEPS.map((label, i) => {
          const state = i < step ? 'done' : i === step ? 'active' : 'todo'
          return (
            <div key={i} className={`adsx-step is-${state}`}>
              <span className="adsx-step-dot">
                {state === 'done' ? (
                  <CheckCircle size={11} strokeWidth={2.6} />
                ) : state === 'active' ? (
                  <span className="chx-spin">
                    <RefreshCw size={11} strokeWidth={2.6} />
                  </span>
                ) : null}
              </span>
              {label}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function GenToast({ progress, onDismiss }: { progress: number; onDismiss: () => void }) {
  const step = rsaStepFromProgress(progress)
  const pct = Math.max(progress, 3)
  return (
    <div className="adsx-toast">
      <div className="adsx-toast-head">
        <span className="adsx-toast-icon">
          <span className="chx-spin">
            <RefreshCw size={16} strokeWidth={2.4} />
          </span>
        </span>
        <div className="adsx-toast-copy">
          <div className="adsx-toast-title">Reklamlar üretiliyor</div>
          <div className="adsx-toast-sub">{RSA_STEPS[Math.min(step, RSA_STEPS.length - 1)]}</div>
        </div>
        <button type="button" className="adsx-toast-close" onClick={onDismiss} title="Kapat">
          <X size={13} />
        </button>
      </div>
      <div className="adsx-toast-body">
        <div className="adsx-toast-meta">
          <span>
            {step}/{RSA_STEPS.length} adım
          </span>
          <span className="chx-mono">{pct}%</span>
        </div>
        <div className="adsx-toast-bar">
          <div className="adsx-progressbar-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="adsx-toast-warn">Bu işlem 3–5 dakika sürebilir.</div>
      </div>
    </div>
  )
}

// Tasarım: reklam grubu kartı — intent kutusu, keyword chip'leri, önizleme,
// Başlıklar/Açıklama/Negatif sekmeleri
function AdGroupCard({
  group,
  previewUrl,
  onRegenerate,
  regenerateDisabled,
}: {
  group: AdGroup
  previewUrl: string
  onRegenerate?: (groupId: number) => void
  regenerateDisabled?: boolean
}) {
  const [tab, setTab] = useState<'heads' | 'descs' | 'neg'>('heads')
  const headlines = group.headlines || []
  const descriptions = group.descriptions || []
  const negatives = group.negative_keywords || []

  return (
    <div className="adsx-group-card">
      <div className="adsx-group-head">
        <span className="adsx-group-icon">
          <Sparkles size={15} />
        </span>
        <h3>{adGroupName(group)}</h3>
        {onRegenerate && group.id != null && (
          <button
            type="button"
            className="adsx-regen-btn"
            onClick={() => onRegenerate(group.id!)}
            disabled={regenerateDisabled}
            title="Bu grubu yeniden üret (yeni taslak set oluşturur)"
          >
            <RefreshCw size={12} strokeWidth={2.4} />
            Yeniden Üret
          </button>
        )}
      </div>
      {adGroupTheme(group) && <div className="adsx-group-intent">{adGroupTheme(group)}</div>}
      {adGroupKeywords(group).length > 0 && (
        <div className="adsx-chip-row">
          {adGroupKeywords(group).map((kw, i) => (
            <span key={i} className="adsx-chip">
              {kw}
            </span>
          ))}
        </div>
      )}

      <div className="adsx-preview">
        <div className="adsx-preview-label">REKLAM METNİ ÖNİZLEMESİ</div>
        <div className="adsx-preview-url">{previewUrl}</div>
        <div className="adsx-preview-title">
          {headlines
            .slice(0, 3)
            .map((headline) => headline.text)
            .join(' | ')}
        </div>
        <div className="adsx-preview-desc">
          {descriptions
            .slice(0, 2)
            .map((description) => description.text)
            .join(' ')}
        </div>
      </div>

      <div className="adsx-tabs">
        <button
          type="button"
          className={tab === 'heads' ? 'active' : ''}
          onClick={() => setTab('heads')}
        >
          Başlıklar {headlines.length}
        </button>
        <button
          type="button"
          className={tab === 'descs' ? 'active' : ''}
          onClick={() => setTab('descs')}
        >
          Açıklama {descriptions.length}
        </button>
        <button
          type="button"
          className={tab === 'neg' ? 'active' : ''}
          onClick={() => setTab('neg')}
        >
          Negatif {negatives.length}
        </button>
      </div>

      <div className="adsx-tab-body">
        {tab === 'heads' &&
          headlines.map((headline, hIdx) => (
            <div key={headline.id ?? hIdx} className="adsx-line">
              {headline.text}
              {headline.validation_action && <small>{headline.validation_action}</small>}
            </div>
          ))}
        {tab === 'descs' &&
          descriptions.map((description, dIdx) => (
            <div key={description.id ?? dIdx} className="adsx-line is-desc">
              {description.text}
            </div>
          ))}
        {tab === 'neg' && (
          <div className="adsx-chip-row">
            {negatives.map((negative, nIdx) => (
              <span key={negative.id ?? nIdx} className="adsx-chip is-neg">
                {negative.keyword}
                {negative.match_type ? ` (${negative.match_type})` : ''}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function AdsPanel({ runId, run }: { runId: number; run?: ScoringRun | null }) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const taskStorageKey = getWorkspaceTaskKey('ads_task', activeWorkspace?.id, runId)
  // Run/workspace kapsamı: await sonrası dönen yanıt, kapsam değiştiyse state'e yazılmaz
  const scopeKey = `${activeWorkspace?.id ?? 'none'}:${runId}`
  const scopeRef = useRef(scopeKey)
  scopeRef.current = scopeKey
  // Unmount sonrası dönen başlatma yanıtı UI state'ine/yan etkiye dokunmaz
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const [brandName, setBrandName] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')
  const [usps, setUsps] = useState('')
  const [taskId, setTaskId] = useScopedTaskId(taskStorageKey)
  const [results, setResults] = useState<AdsResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toastOpen, setToastOpen] = useState(true)
  // Set versiyonlama (Faz E): geçmiş + onay akışı
  const [sets, setSets] = useState<AdGenerationSetSummary[]>([])
  const [activeSetId, setActiveSetId] = useState<number | null>(null)
  // null = varsayılan görünüm (aktif non-stale set)
  const [selectedSetId, setSelectedSetId] = useState<number | null>(null)
  // Async üretim/regenerate bitince otomatik geçilecek set
  const [pendingSetId, setPendingSetId] = useState<number | null>(null)
  const [activating, setActivating] = useState(false)

  const polling = useTaskPolling(taskId, taskStorageKey, 3000, activeWorkspace?.id, runId)
  const channelDisabled = run?.enable_ads === false

  // Run/workspace değişince eski marka bağlamı TAŞINMAZ ve autofill state
  // okumadan KOŞULSUZ doldurur — reset+autofill ayrı effect'lerdeyken autofill
  // aynı render closure'ındaki eski (dolu) değerleri görüp doldurmayı
  // atlıyordu (alanlar boş kalıyordu). Tek effect + yerel değişkenlerle çözülür.
  useEffect(() => {
    setResults(null)
    setError(null)
    setLoading(false)
    setActivating(false)
    setBrandName('')
    setWebsiteUrl('')
    setUsps('')
    setSets([])
    setActiveSetId(null)
    setSelectedSetId(null)
    setPendingSetId(null)
    if (!runId || !activeWorkspace?.id) return

    let cancelled = false

    const uspsFrom = (pd: Record<string, unknown>) => {
      const products = ((pd.products as string[] | undefined) || []).slice(0, 3).join(', ')
      const sector = (pd.sector as string) || ''
      if (products && sector) return `${products} -- ${sector}`
      return products
    }

    const applyWorkspaceFallback = () => {
      if (cancelled) return
      const pd = activeWorkspace.profile_data
      if (!pd) return
      if (activeWorkspace.company_url) setWebsiteUrl(activeWorkspace.company_url)
      if (pd.company_name) setBrandName(pd.company_name as string)
      const usp = uspsFrom(pd)
      if (usp) setUsps(usp)
    }

    brandProfileApi
      .getProfile(runId, activeWorkspace.id)
      .then((res) => {
        if (cancelled) return
        const profile = res.data as {
          company_url?: string
          status: string
          profile_data?: Record<string, unknown>
        }
        if (profile.status === 'confirmed' && profile.profile_data) {
          const pd = profile.profile_data
          if (profile.company_url) setWebsiteUrl(profile.company_url)
          if (pd.company_name) setBrandName(pd.company_name as string)
          const usp = uspsFrom(pd)
          if (usp) setUsps(usp)
        } else {
          if (profile.company_url) setWebsiteUrl(profile.company_url)
          applyWorkspaceFallback()
        }
      })
      .catch(() => {
        applyWorkspaceFallback()
      })

    return () => {
      cancelled = true
    }
  }, [runId, activeWorkspace?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const fetchResults = useCallback(async () => {
    if (!activeWorkspace?.id || !runId) return
    const requestScope = scopeRef.current
    try {
      const res = await generationApi.getAdsRsa(
        runId,
        activeWorkspace.id,
        selectedSetId ?? undefined
      )
      if (scopeRef.current !== requestScope) return
      const data = res.data as AdsResult
      setResults(data.total_groups > 0 ? data : null)
    } catch (e) {
      console.error('Failed to fetch Ads results:', e)
    }
  }, [activeWorkspace?.id, runId, selectedSetId])

  const fetchSets = useCallback(async () => {
    if (!activeWorkspace?.id || !runId) return
    const requestScope = scopeRef.current
    try {
      const res = await generationApi.getAdsSets(runId, activeWorkspace.id)
      if (scopeRef.current !== requestScope) return
      const data = res.data as AdsSetListResult
      setSets(data.sets || [])
      setActiveSetId(data.active_set_id ?? null)
    } catch (e) {
      console.error('Failed to fetch Ads sets:', e)
    }
  }, [activeWorkspace?.id, runId])

  useEffect(() => {
    void fetchResults()
  }, [fetchResults])

  useEffect(() => {
    void fetchSets()
  }, [fetchSets])

  useEffect(() => {
    if (polling.isCompleted) {
      void fetchSets()
      // Üretim yeni bir set doğurdu (ilkse aktif, değilse taslak) —
      // kullanıcı sonucu görsün ve gerekirse onaylasın diye o sete geç.
      if (pendingSetId != null) {
        setSelectedSetId(pendingSetId)
        setPendingSetId(null)
      } else {
        void fetchResults()
      }
    }
  }, [polling.isCompleted, fetchResults, fetchSets, pendingSetId])

  const activateSet = async (setId: number) => {
    if (!activeWorkspace?.id) return
    const requestScope = scopeRef.current
    setActivating(true)
    setError(null)
    try {
      await generationApi.activateAdsSet(setId, activeWorkspace.id)
      if (scopeRef.current !== requestScope) return
      setSelectedSetId(null) // varsayılan görünüm = yeni aktif set
      await fetchSets()
    } catch (err: unknown) {
      if (scopeRef.current !== requestScope) return
      setError(apiErrorMessage(err, 'Set aktive edilemedi'))
    } finally {
      if (scopeRef.current === requestScope) setActivating(false)
    }
  }

  const regenerateGroup = async (groupId: number) => {
    if (!activeWorkspace?.id) return
    const requestScope = scopeRef.current
    setError(null)
    setToastOpen(true)
    try {
      const res = await generationApi.regenerateAdGroup(groupId, activeWorkspace.id)
      const data = res.data as { task_id: string; generation_set_id?: number }
      // Görev kimliği HER ZAMAN başlatıldığı run'ın anahtarına yazılır (unmount/run değişimi
      // olsa bile kaybolmaz); UI state yalnız panel hâlâ aynı kapsamdaysa güncellenir.
      setStoredTaskId(taskStorageKey, data.task_id)
      if (!mountedRef.current || scopeRef.current !== requestScope) return
      setPendingSetId(data.generation_set_id ?? null)
      setTaskId(data.task_id)
    } catch (err: unknown) {
      if (scopeRef.current !== requestScope) return
      const stale = policyStaleFromError(err)
      setError(stale ? stale.message : apiErrorMessage(err, 'Grup yeniden üretilemedi'))
    }
  }

  const startGeneration = async () => {
    if (!activeWorkspace?.id) {
      setError('Marka çalışması seçin')
      return
    }
    const requestScope = scopeRef.current
    setLoading(true)
    setError(null)
    setResults(null)
    setToastOpen(true)
    try {
      const res = await generationApi.createAdsRsa(
        {
          scoring_run_id: runId,
          brand_name: brandName,
          website_url: websiteUrl ? websiteUrl.trim() : undefined,
          brand_usp: usps
            ? usps
                .split(',')
                .map((s) => s.trim())
                .join(', ')
            : undefined,
        },
        activeWorkspace.id
      )
      const data = res.data as { task_id: string; generation_set_id?: number }
      // Görev kimliği HER ZAMAN başlatıldığı run'ın anahtarına yazılır (unmount/run değişimi
      // olsa bile kaybolmaz); UI state yalnız panel hâlâ aynı kapsamdaysa güncellenir.
      setStoredTaskId(taskStorageKey, data.task_id)
      if (!mountedRef.current || scopeRef.current !== requestScope) return
      setPendingSetId(data.generation_set_id ?? null)
      setTaskId(data.task_id)
      void fetchSets()
    } catch (err: unknown) {
      if (scopeRef.current !== requestScope) return
      // Plan v13: bayat havuz 409'u "devam eden üretim" DEĞİLDİR — kendi
      // mesajıyla gösterilir (kanal atamasını yenileme çağrısı içerir)
      const stale = policyStaleFromError(err)
      if (stale) {
        setError(stale.message)
      } else {
        const message = apiErrorMessage(err)
        // 409: aynı run'da üretim zaten sürüyor (versiyonlama kilidi)
        setError(
          apiErrorStatus(err) === 409 ? `Devam eden bir ADS üretimi var — ${message}` : message
        )
      }
    } finally {
      if (scopeRef.current === requestScope) setLoading(false)
    }
  }

  const generating = loading || polling.isActive
  const previewUrl = displayUrlFrom(websiteUrl || activeWorkspace?.company_url || '')

  // Gösterilen set: sonuç payload'ındaki generation_set öncelikli
  const shownSet: AdGenerationSetSummary | null =
    results?.generation_set ??
    (selectedSetId != null
      ? (sets.find((s) => s.id === selectedSetId) ?? null)
      : (sets.find((s) => s.id === activeSetId) ?? null))
  const hasActiveNonStale = sets.some((s) => s.status === 'active' && !s.is_stale)
  const activatableDraft = sets.find(canActivateSet)

  return (
    <div className="adsx-root">
      {/* RSA form kartı — grid'in sağ hücresi */}
      <section className="chx-card adsx-form-card">
        <div className="adsx-form-head">
          <span className="adsx-form-icon">
            <Sparkles size={15} />
          </span>
          <div>
            <h2>Google Ads RSA Üretimi</h2>
            <div className="adsx-form-sub">
              Bu analizin ADS havuzundaki kelimeler için reklam grupları üretir.
            </div>
          </div>
        </div>

        {error && (
          <ErrorBanner
            error={error}
            onDismiss={() => setError(null)}
            onRetry={() => setError(null)}
          />
        )}

        <div className="adsx-form-grid">
          <label className="kwx-field">
            <span className="kwx-field-label">Marka Adı</span>
            <input
              className="kwx-input"
              type="text"
              value={brandName}
              onChange={(e) => setBrandName(e.target.value)}
              placeholder="Profilden otomatik dolar veya manuel girin"
            />
          </label>
          <label className="kwx-field">
            <span className="kwx-field-label">Web Sitesi URL</span>
            <input
              className="kwx-input"
              type="url"
              value={websiteUrl}
              onChange={(e) => setWebsiteUrl(e.target.value)}
              placeholder="https://example.com"
            />
          </label>
        </div>
        <label className="kwx-field adsx-usp-field">
          <span className="kwx-field-label">USP'ler (virgülle ayırın)</span>
          <input
            className="kwx-input"
            type="text"
            value={usps}
            onChange={(e) => setUsps(e.target.value)}
            placeholder="Ücretsiz kargo, 30 gün iade, 7/24 destek"
          />
        </label>

        {channelDisabled && (
          <p className="channel-disabled-hint">Bu çalışmada ADS kanalı devre dışı.</p>
        )}
        <div className="adsx-form-actions">
          <button
            type="button"
            className="kwx-run-btn"
            onClick={startGeneration}
            disabled={generating || !runId || channelDisabled}
            title={channelDisabled ? 'Bu çalışmada ADS kanalı aktif değil' : undefined}
          >
            {generating ? 'Üretiliyor…' : 'Reklam Üret'}
            <Sparkles size={15} strokeWidth={2.2} />
          </button>
        </div>
      </section>

      {/* Üretim ilerlemesi — tam genişlik banner (gerçek task progress) */}
      {polling.isActive && (
        <div className="adsx-span">
          <GenBanner progress={polling.progress} />
        </div>
      )}
      {polling.isFailed && (
        <div className="adsx-span">
          <ErrorBanner
            error={polling.errorMessage || 'RSA üretimi başarısız oldu.'}
            onDismiss={() => setTaskId(null)}
            onRetry={() => setTaskId(null)}
          />
        </div>
      )}

      {/* Set geçmişi + onay akışı (Faz E versiyonlama) */}
      {sets.length > 0 && (
        <div className="adsx-span adsx-sets">
          <div className="adsx-sets-label">Üretim Setleri</div>
          <div className="adsx-sets-row">
            {sets.map((s) => {
              const isShown = shownSet?.id === s.id
              return (
                <button
                  key={s.id}
                  type="button"
                  className={`adsx-set-chip${isShown ? ' is-shown' : ''}`}
                  onClick={() => setSelectedSetId(s.id === activeSetId ? null : s.id)}
                  title={`v${s.version_number} — ${SET_STATUS_LABELS[s.status]}${
                    s.is_stale ? ' (bayat)' : ''
                  }`}
                >
                  v{s.version_number}
                  <span className={`adsx-set-badge is-${s.status}`}>
                    {SET_STATUS_LABELS[s.status]}
                  </span>
                  {s.is_stale && <span className="adsx-set-badge is-stale">Bayat</span>}
                  {typeof s.groups_count === 'number' && (
                    <span className="adsx-set-count">{s.groups_count} grup</span>
                  )}
                </button>
              )
            })}
          </div>
          {!hasActiveNonStale && !polling.isActive && (
            <div className="adsx-sets-empty">
              Güncel aktif reklam seti yok
              {activatableDraft
                ? ' — aşağıdan taslağı inceleyip "Aktif Yap" ile onaylayın.'
                : ' — yeni bir ADS üretimi çalıştırın.'}
            </div>
          )}
        </div>
      )}

      {results && (
        <div className="adsx-span adsx-results">
          <div className="adsx-results-head">
            <CheckCircle size={18} strokeWidth={2.2} />
            <h2>
              {shownSet && shownSet.status !== 'active'
                ? `RSA Seti v${shownSet.version_number} (${SET_STATUS_LABELS[shownSet.status]})`
                : 'RSA Üretimi Tamamlandı'}
            </h2>
            {shownSet?.is_stale && <span className="adsx-set-badge is-stale">Bayat</span>}
            {shownSet && canActivateSet(shownSet) && (
              <button
                type="button"
                className="adsx-activate-btn"
                onClick={() => activateSet(shownSet.id)}
                disabled={activating || generating}
              >
                {activating ? 'Aktifleştiriliyor…' : 'Aktif Yap'}
              </button>
            )}
            {shownSet && shownSet.status === 'active' && !shownSet.is_stale && (
              <span className="adsx-set-badge is-active">Aktif Set</span>
            )}
          </div>

          <div className="adsx-stats">
            <div className="adsx-stat">
              <div className="adsx-stat-value">{results.total_groups}</div>
              <div className="adsx-stat-label">Reklam Grubu</div>
            </div>
            <div className="adsx-stat">
              <div className="adsx-stat-value">
                {results.total_headlines ??
                  results.ad_groups?.reduce(
                    (s: number, g: AdGroup) => s + (g.headlines?.length || 0),
                    0
                  ) ??
                  0}
              </div>
              <div className="adsx-stat-label">Başlık</div>
            </div>
            <div className="adsx-stat">
              <div className="adsx-stat-value">
                {results.total_descriptions ??
                  results.ad_groups?.reduce(
                    (s: number, g: AdGroup) => s + (g.descriptions?.length || 0),
                    0
                  ) ??
                  0}
              </div>
              <div className="adsx-stat-label">Açıklama</div>
            </div>
            <div className="adsx-stat">
              <div className="adsx-stat-value">
                {results.total_negative_keywords ??
                  results.ad_groups?.reduce(
                    (s: number, g: AdGroup) => s + (g.negative_keywords?.length || 0),
                    0
                  ) ??
                  0}
              </div>
              <div className="adsx-stat-label">Negatif Kelime</div>
            </div>
          </div>

          {results.validation_summary && (
            <div className="chx-card adsx-validation">
              <h4>Doğrulama Özeti</h4>
              <div className="validation-stats">
                <span>✅ Korunan: {results.validation_summary.headlines_kept}</span>
                <span>✂️ Kısaltılan: {results.validation_summary.headlines_shortened}</span>
                <span>🔄 Yeniden üretilen: {results.validation_summary.headlines_regenerated}</span>
                <span>❌ Elenen: {results.validation_summary.headlines_eliminated}</span>
                <span>
                  🔀 DKI dönüştürülen: {results.validation_summary.dki_converted_to_plain}
                </span>
              </div>
            </div>
          )}

          <div className="adsx-groups-grid">
            {results.ad_groups?.map((group: AdGroup, idx: number) => (
              <AdGroupCard
                key={group.id ?? idx}
                group={group}
                previewUrl={previewUrl}
                onRegenerate={canRegenerateFromSet(shownSet) ? regenerateGroup : undefined}
                regenerateDisabled={generating || activating}
              />
            ))}
          </div>
        </div>
      )}

      {polling.isActive && toastOpen && (
        <GenToast progress={polling.progress} onDismiss={() => setToastOpen(false)} />
      )}
    </div>
  )
}
