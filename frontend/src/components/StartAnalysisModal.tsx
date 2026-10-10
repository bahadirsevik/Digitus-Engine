/**
 * "Bu analizi ne için yapıyoruz?" — Claude Design "Anahtar Kelimeler"
 * tasarımındaki 3 kanal kartlı modal. İşleyiş aynı: amaç seçimi →
 * createRun('all' + auto_assign_channels) → execute → onStarted(runId).
 *
 * Analiz motoru kullanıcı seçimi değildir: kilitli v3 salt-okunur gösterilir.
 * Backend capability v3'ü kapalı bildirirse sessizce legacy motora düşülmez;
 * başlatma açık bir uyarıyla engellenir.
 *
 * Sağlayıcı, model, ücret ve tarama modu bu arayüzde YOKTUR (sunucu kararı).
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, Loader2, Play } from 'lucide-react'
import {
  apiErrorCode,
  apiErrorMessage,
  scoringApi,
  workspaceApi,
  LocationFilterPreviewResponse,
  WorkspaceResponse,
} from '../services/api'
import {
  isActiveLocationMode,
  locationModeLabel,
  readLocationFilterMode,
  LocationFilterMode,
} from '../services/locationPolicy'
import {
  DEFAULT_CAPACITIES,
  buildStartAnalysisPayload,
  defaultRunName,
  validateStartAnalysis,
} from './startAnalysis'

// Lokasyon önizlemesi/create/execute'un tipli 400/409 kodları
// (plan_v3_lokasyon_filtresi.md §5.7) — üçü de "run gerçekten başlamadı,
// önizlemeyi yenile ve tekrar dene" anlamına gelir.
const LOCATION_RETRY_CODES = new Set([
  'LOCATION_PREVIEW_STALE',
  'LOCATION_PREVIEW_REQUIRED',
  'LOCATION_PREVIEW_DRAFT_REJECTED',
])

const CHANNELS = [
  { id: 'ads' as const, label: 'Google Ads', countLabel: 'Ads kelime sayısı' },
  { id: 'seo' as const, label: 'SEO + GEO', countLabel: 'SEO kelime sayısı' },
  { id: 'social' as const, label: 'Sosyal Medya', countLabel: 'Sosyal kelime sayısı' },
]

export default function StartAnalysisModal({
  open,
  brandProfileId,
  poolCount,
  onClose,
  onStarted,
}: {
  open: boolean
  brandProfileId: number
  poolCount: number
  onClose: () => void
  onStarted: (runId: number) => void
}) {
  const [on, setOn] = useState({ ads: true, seo: true, social: true })
  const [counts, setCounts] = useState({
    ads: String(DEFAULT_CAPACITIES.ads),
    seo: String(DEFAULT_CAPACITIES.seo),
    social: String(DEFAULT_CAPACITIES.social),
  })
  const [engineV3Enabled, setEngineV3Enabled] = useState<boolean | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')

  // Lokasyon filtresi kapısı (plan_v3_lokasyon_filtresi.md §5.7/§6): mod
  // `none` ise bu bölümün TAMAMI atlanır — token yok, ekstra istek yok.
  // Aktif moddaysa (exclude_all/focus_only) kayıtlı workspace politikasına
  // karşı TAZE bir önizleme alınır (draft alan göndermeden → is_saved_policy).
  //
  // KRİTİK: kayıtlı mod SUNUCUDAN TAZE okunur (workspaceApi.get) — Zustand
  // persist store'daki `activeWorkspace` (localStorage) GÜVEN ZİNCİRİNE
  // ALINMAZ. Reload'suz bir sekme, ikinci sekme veya başka yerde yapılan bir
  // politika değişikliği store'u bayatlatabilir; bayat "none" store'a
  // güvenilirse gate hiç tetiklenmez ve create 400 LOCATION_PREVIEW_REQUIRED
  // döner (QA blocker — sonsuz, açıklamasız retry döngüsü).
  const [fetchedLocationMode, setFetchedLocationMode] = useState<LocationFilterMode | null>(null)
  const [workspaceFetchError, setWorkspaceFetchError] = useState(false)
  const [locationPreview, setLocationPreview] = useState<LocationFilterPreviewResponse | null>(null)
  const [locationStatus, setLocationStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>(
    'idle'
  )
  const [locationError, setLocationError] = useState('')

  const refreshLocationPreview = useCallback(async () => {
    setLocationStatus('loading')
    setLocationError('')
    try {
      const res = await workspaceApi.previewLocationFilter(brandProfileId, {
        keyword_selection_mode: 'all',
      })
      setLocationPreview(res.data)
      if (!res.data.has_keywords || res.data.kept_count <= 0) {
        setLocationStatus('error')
        setLocationError(
          'Lokasyon filtresi sonrası kalacak keyword yok — analiz başlatılamaz. Filtre ayarlarını gözden geçirin.'
        )
        return
      }
      setLocationStatus('ready')
    } catch (err: unknown) {
      setLocationPreview(null)
      setLocationStatus('error')
      setLocationError(apiErrorMessage(err, 'Lokasyon filtresi önizlemesi alınamadı'))
    }
  }, [brandProfileId])

  const retryWorkspaceFetch = useCallback(async () => {
    setWorkspaceFetchError(false)
    try {
      const res = await workspaceApi.get(brandProfileId)
      setFetchedLocationMode(readLocationFilterMode((res.data as WorkspaceResponse)?.profile_data))
    } catch {
      setWorkspaceFetchError(true)
    }
  }, [brandProfileId])

  useEffect(() => {
    if (!open) return
    setEngineV3Enabled(null)
    let cancelled = false
    void (async () => {
      try {
        const caps = await scoringApi.getCapabilities()
        if (!cancelled) setEngineV3Enabled(Boolean(caps.data?.engine_v3_enabled))
      } catch {
        if (!cancelled) setEngineV3Enabled(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open])

  // (b) Birincil düzeltme: modal her açılışta workspace'i SUNUCUDAN taze
  // çeker; kararı store DEĞİL bu çağrı belirler. Bu sayede kayıtlı mod
  // aktifse İLK tıklama (aşağıdaki önizleme yüklendikten sonra) başarılı
  // olur — store bayat olsa bile bir kez başarısız olup tekrar denemeye
  // gerek kalmaz.
  useEffect(() => {
    if (!open) return
    setFetchedLocationMode(null)
    setWorkspaceFetchError(false)
    setLocationPreview(null)
    setLocationError('')
    setLocationStatus('idle')
    let cancelled = false
    void (async () => {
      try {
        const res = await workspaceApi.get(brandProfileId)
        if (cancelled) return
        setFetchedLocationMode(
          readLocationFilterMode((res.data as WorkspaceResponse)?.profile_data)
        )
      } catch {
        if (!cancelled) setWorkspaceFetchError(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, brandProfileId])

  useEffect(() => {
    if (!open) return
    if (fetchedLocationMode === null) return
    if (!isActiveLocationMode(fetchedLocationMode)) {
      setLocationStatus('idle')
      return
    }
    void refreshLocationPreview()
  }, [open, fetchedLocationMode, refreshLocationPreview])

  if (!open) return null

  const validation = validateStartAnalysis(on)
  const invalidCapacity = CHANNELS.some(
    (channel) =>
      on[channel.id] &&
      (!/^[1-9]\d*$/.test(counts[channel.id]) || !Number.isSafeInteger(Number(counts[channel.id])))
  )
  const engineUnavailable = engineV3Enabled === false

  // (a) Belt-and-braces: bir önizleme BAŞARIYLA yüklendiyse (ör. create'in
  // 400 LOCATION_PREVIEW_REQUIRED dönmesi sonrası tetiklenen otomatik
  // yenilemeden), önizlemenin kendi `mode` alanı — HER ZAMAN canlı backend
  // durumunu yansıtır — kararı KOŞULSUZ ele alır (mod `none` DÂHİL). Aksi
  // halde kayıtlı politika başka yerde `none`'a çekildiğinde bayat AKTİF
  // `fetchedLocationMode` kazanmaya devam eder, modal token eklemeyi
  // sürdürür ve backend'in `none` için token'ı reddetmesiyle YENİ bir
  // döngüye girilir (QA bulgu 1). Yalnız HİÇ önizleme yüklenmediyse
  // `fetchedLocationMode` devreye girer.
  const effectiveLocationMode: string | null = locationPreview
    ? locationPreview.mode
    : fetchedLocationMode
  const locationModeActive =
    effectiveLocationMode !== null && isActiveLocationMode(effectiveLocationMode)
  // Kayıtlı mod henüz bilinmiyor (ilk workspace fetch sürüyor/başarısız) ve
  // onu geçersiz kılacak bir önizleme de yok — güvenli taraf: engelle.
  const locationUnknown = fetchedLocationMode === null && !locationPreview
  const locationBlocked = locationUnknown || (locationModeActive && locationStatus !== 'ready')

  const handleStart = async () => {
    if (validation || invalidCapacity || engineV3Enabled !== true || locationBlocked) return
    setStarting(true)
    setError('')
    try {
      const payload = buildStartAnalysisPayload({
        brandProfileId,
        runName: defaultRunName(on),
        ads: on.ads,
        seo: on.seo,
        social: on.social,
        adsCapacity: Number(counts.ads),
        seoCapacity: Number(counts.seo),
        socialCapacity: Number(counts.social),
        locationGate:
          locationModeActive && locationPreview
            ? {
                location_universe_fingerprint: locationPreview.universe_fingerprint,
                location_policy_fingerprint: locationPreview.location_policy_fingerprint,
                location_preview_is_saved_policy: locationPreview.is_saved_policy,
              }
            : undefined,
      })
      const created = await scoringApi.createRun(payload)
      const runId = created.data?.id
      try {
        await scoringApi.executeRun(runId, brandProfileId)
      } catch (execErr: unknown) {
        if (LOCATION_RETRY_CODES.has(apiErrorCode(execErr) || '')) {
          setError(
            'Lokasyon önizlemesi bayatladığı için bu koşu başarısız oldu. Önizleme yenilendi — lütfen tekrar başlatın.'
          )
          void refreshLocationPreview()
          return
        }
        throw execErr
      }
      onStarted(runId)
    } catch (err: unknown) {
      if (LOCATION_RETRY_CODES.has(apiErrorCode(err) || '')) {
        setError('Lokasyon önizlemesi güncel değil. Önizleme yenilendi — lütfen tekrar başlatın.')
        void refreshLocationPreview()
        return
      }
      const e = err as { message?: string; detail?: unknown }
      setError(e?.message || String(e?.detail || 'Analiz başlatılamadı'))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="kwx-modal-overlay">
      <div className="kwx-modal">
        <h2>Bu analizi ne için yapıyoruz?</h2>
        <p className="kwx-modal-sub">
          Havuzdaki <b>{poolCount}</b> keyword skorlanacak; seçtiğiniz amaçlara göre kanal atamaları
          yapılır.
        </p>

        <div className="kwx-count-field kwx-engine-field">
          <span className="lbl">Analiz motoru</span>
          <div className="kwx-engine-value" aria-label="Analiz motoru">
            v3 — kilitli üretim motoru
          </div>
        </div>

        <div className="kwx-channel-grid">
          {CHANNELS.map((c) => {
            const active = on[c.id]
            return (
              <div key={c.id} className="kwx-channel-col">
                <button
                  type="button"
                  className={`kwx-channel-card${active ? ' active' : ''}`}
                  onClick={() => setOn((s) => ({ ...s, [c.id]: !s[c.id] }))}
                >
                  <span className="kwx-checkbox">
                    {active && <Check size={12} strokeWidth={3} />}
                  </span>
                  <span>{c.label}</span>
                </button>
                <label className="kwx-count-field">
                  <span className="lbl">{c.countLabel}</span>
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={counts[c.id]}
                    disabled={!active || starting}
                    onChange={(e) => setCounts((s) => ({ ...s, [c.id]: e.target.value }))}
                  />
                </label>
              </div>
            )
          })}
        </div>

        {validation && (
          <div className="kwx-modal-warning" role="alert">
            <AlertTriangle size={14} />
            <span>{validation.message}</span>
          </div>
        )}
        {invalidCapacity && (
          <div className="kwx-modal-warning" role="alert">
            <AlertTriangle size={14} />
            <span>Seçili kanalların kelime sayısı 1 veya daha büyük bir tam sayı olmalı.</span>
          </div>
        )}
        {engineUnavailable && (
          <div className="kwx-modal-warning" role="alert">
            <AlertTriangle size={14} />
            <span>Motor v3 sunucuda kapalı veya doğrulanamadı. Analiz başlatılmadı.</span>
          </div>
        )}

        {workspaceFetchError && (
          <div className="kwx-modal-warning" role="alert" data-testid="location-gate-error">
            <AlertTriangle size={14} />
            <span>
              Marka çalışması bilgisi alınamadı — lokasyon filtresi durumu doğrulanamadı.{' '}
              <button
                type="button"
                className="kwx-modal-cancel"
                style={{ padding: '2px 10px', marginLeft: 6 }}
                onClick={() => void retryWorkspaceFetch()}
              >
                Tekrar dene
              </button>
            </span>
          </div>
        )}
        {!workspaceFetchError && locationUnknown && (
          <div className="kwx-modal-warning" role="status" data-testid="location-gate-loading">
            <Loader2 size={14} className="spin-icon" />
            <span>Şehir politikası kontrol ediliyor…</span>
          </div>
        )}
        {locationModeActive && locationStatus === 'loading' && (
          <div className="kwx-modal-warning" role="status" data-testid="location-gate-loading">
            <Loader2 size={14} className="spin-icon" />
            <span>Lokasyon filtresi etkisi kontrol ediliyor…</span>
          </div>
        )}
        {locationModeActive && locationStatus === 'ready' && locationPreview && (
          <div className="kwx-modal-warning" role="status" data-testid="location-gate-summary">
            <AlertTriangle size={14} />
            <span>
              Şehir politikası: <b>{locationModeLabel(effectiveLocationMode || '')}</b> —{' '}
              <b>{locationPreview.excluded_count}</b> keyword lokasyon filtresi nedeniyle hariç
              tutulacak, <b>{locationPreview.kept_count}</b> keyword skorlanacak.
            </span>
          </div>
        )}
        {locationModeActive && locationStatus === 'error' && (
          <div className="kwx-modal-warning" role="alert" data-testid="location-gate-error">
            <AlertTriangle size={14} />
            <span>
              {locationError || 'Lokasyon filtresi önizlemesi alınamadı.'}{' '}
              <button
                type="button"
                className="kwx-modal-cancel"
                style={{ padding: '2px 10px', marginLeft: 6 }}
                onClick={() => void refreshLocationPreview()}
              >
                Tekrar dene
              </button>
            </span>
          </div>
        )}

        {error && <div className="error-banner">{error}</div>}

        <div className="kwx-modal-actions">
          <button type="button" className="kwx-modal-cancel" onClick={onClose} disabled={starting}>
            İptal
          </button>
          <button
            type="button"
            className="kwx-modal-start"
            onClick={handleStart}
            disabled={
              starting ||
              validation !== null ||
              invalidCapacity ||
              engineV3Enabled !== true ||
              locationBlocked
            }
          >
            {starting ? (
              <Loader2 size={14} className="spin-icon" />
            ) : (
              <Play size={14} fill="currentColor" strokeWidth={0} />
            )}
            {starting ? 'Başlatılıyor…' : 'Başlat'}
          </button>
        </div>
      </div>
    </div>
  )
}
