import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, ChevronLeft, FileText, History, Plus, RefreshCw, Tag, X } from 'lucide-react'
import {
  apiErrorCode,
  channelsApi,
  socialBriefApi,
  type DurationPresetResponse,
  type SocialBriefCreateRequest,
  type SocialBriefResponse,
  type SocialBriefTargetCreateRequest,
  type SocialFormatMatrixResponse,
  type SocialFormatOptionResponse,
  type SocialPlatformOptionResponse,
} from '../../services/api'
import type { PoolKeyword } from '../ChannelWorkspaceView'
import type { ScoringRun } from '../../types/models'
import { useBrandStore } from '../../stores/brandStore'
import SocialBriefDetail from './socialBrief/SocialBriefDetail'
import { socialErrorText } from './socialBrief/labels'
import { isDuplicateTarget } from './socialBrief/briefRules'
import { SOCIAL_POLL_INTERVAL_MS } from './socialBrief/usePolling'
import {
  useBriefKeywordSelection,
  type BriefKeywordSelection,
} from './socialBrief/useBriefKeywordSelection'
import './SocialBriefWorkspace.css'

export interface SocialBriefWorkspaceProps {
  runId: number
  run?: ScoringRun | null
  /**
   * Üst sayfanın (ChannelWorkspaceView) SOCIAL havuzu. Verilirse TEK otoriter
   * kaynaktır: bileşen ikinci bir havuz isteği ATMAZ. Verilmezse (tek başına
   * kullanım) havuz burada yüklenir.
   */
  pool?: PoolKeyword[]
  poolLoading?: boolean
  poolError?: string | null
  onRetryPool?: () => void
  /** Üst havuz kartlarıyla PAYLAŞILAN brief kelime seçimi (tek durum). */
  keywordSelection?: BriefKeywordSelection
  disabled?: boolean
  onOpenHistory?: () => void
  /** Yoklama aralığı (ms); testler kısaltır. */
  pollIntervalMs?: number
}

const LAST_BRIEF_KEY = (ws: number, run: number) => `last_social_brief_${ws}_${run}`

export default function SocialBriefWorkspace({
  runId,
  run: _run,
  pool: upstreamPool,
  poolLoading: upstreamPoolLoading = false,
  poolError: upstreamPoolError = null,
  onRetryPool,
  keywordSelection,
  disabled = false,
  onOpenHistory,
  pollIntervalMs = SOCIAL_POLL_INTERVAL_MS,
}: SocialBriefWorkspaceProps) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const currentWorkspaceId = activeWorkspace?.id ?? null

  // Kapsam koruması: her async sonuç başladığı workspace+run'a aitse yazılır.
  const scopeToken = `${currentWorkspaceId ?? 'none'}:${runId}`
  const scopeRef = useRef(scopeToken)
  scopeRef.current = scopeToken

  // Format Matrix State
  const [matrix, setMatrix] = useState<SocialFormatMatrixResponse | null>(null)
  const [loadingMatrix, setLoadingMatrix] = useState(false)
  const [matrixError, setMatrixError] = useState<string | null>(null)

  // Channel Pool: üstten gelirse TEK kaynak o; yalnız tek başına kullanımda yedek istek
  const hasUpstreamPool = upstreamPool !== undefined
  const [fallbackPool, setFallbackPool] = useState<PoolKeyword[]>([])
  const [fallbackLoading, setFallbackLoading] = useState(false)
  const [fallbackError, setFallbackError] = useState<string | null>(null)
  const [fallbackAttempt, setFallbackAttempt] = useState(0)
  const poolRequestRef = useRef(0)
  const pool = hasUpstreamPool ? upstreamPool : fallbackPool
  const loadingPool = hasUpstreamPool ? upstreamPoolLoading : fallbackLoading
  const poolError = hasUpstreamPool ? upstreamPoolError : fallbackError
  const retryPool = hasUpstreamPool
    ? onRetryPool
    : () => setFallbackAttempt((attempt) => attempt + 1)

  // Brief kelime seçimi: üstten paylaşılan kontrolcü varsa O; yoksa yerel (tek başına)
  const localSelection = useBriefKeywordSelection(scopeToken)
  const selection = keywordSelection ?? localSelection
  const selectedKeywordIds = selection.selectedIds

  // Briefs List State
  const [briefs, setBriefs] = useState<SocialBriefResponse[]>([])
  const [loadingBriefs, setLoadingBriefs] = useState(false)
  const [briefsError, setBriefsError] = useState<string | null>(null)
  const [featureDisabled, setFeatureDisabled] = useState(false)

  // Active View State
  const [selectedBriefId, setSelectedBriefId] = useState<number | null>(null)
  const [isCreatingNew, setIsCreatingNew] = useState(false)

  // Creation Form State (kelime secimi yukaridaki paylasilan kontrolcude)
  const [kwSearch, setKwSearch] = useState('')
  const [selectedTargets, setSelectedTargets] = useState<SocialBriefTargetCreateRequest[]>([])
  const [targetPlatform, setTargetPlatform] = useState<string>('')
  const [targetFormat, setTargetFormat] = useState<string>('')
  const [targetDurationPresetId, setTargetDurationPresetId] = useState<string>('')
  const [brandName, setBrandName] = useState<string>(activeWorkspace?.name || '')
  const [brandContext, setBrandContext] = useState<string>('')
  const [formError, setFormError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // 1. Format Matrix Yükle
  useEffect(() => {
    let isMounted = true
    setLoadingMatrix(true)
    setMatrixError(null)
    socialBriefApi
      .getFormatMatrix()
      .then((res) => {
        if (!isMounted) return
        setMatrix(res.data)
        const firstPlatform = res.data.platforms[0]
        if (firstPlatform) {
          setTargetPlatform(firstPlatform.id)
          const firstFormat = firstPlatform.formats[0]
          if (firstFormat) {
            setTargetFormat(firstFormat.id)
            setTargetDurationPresetId(
              firstFormat.requires_duration ? (firstFormat.duration_presets[0]?.id ?? '') : ''
            )
          }
        }
      })
      .catch((err) => {
        if (isMounted) setMatrixError(socialErrorText(err, 'Format seçenekleri alınamadı.'))
      })
      .finally(() => {
        if (isMounted) setLoadingMatrix(false)
      })
    return () => {
      isMounted = false
    }
  }, [])

  // 2. Yedek havuz yüklemesi — YALNIZ üst havuz verilmediğinde. İstek kimliği ile
  // eski yanıt atılır; yükleme durumu her geçişte (başarı/hata/kapsam değişimi) kapanır.
  useEffect(() => {
    const requestId = ++poolRequestRef.current
    setFallbackPool([])
    setFallbackError(null)
    if (hasUpstreamPool || !currentWorkspaceId || !runId) {
      setFallbackLoading(false)
      return
    }
    setFallbackLoading(true)
    channelsApi
      .getPools(runId, currentWorkspaceId)
      .then((res) => {
        if (poolRequestRef.current !== requestId) return
        setFallbackPool((res.data.channels?.SOCIAL || []) as PoolKeyword[])
      })
      .catch((err) => {
        if (poolRequestRef.current !== requestId) return
        setFallbackError(socialErrorText(err, 'Sosyal kanal havuzu alınamadı.'))
      })
      .finally(() => {
        if (poolRequestRef.current === requestId) setFallbackLoading(false)
      })
    return () => {
      // Kapsam/kaynak değişince eski istek geçersiz; spinner açık kalmaz
      poolRequestRef.current += 1
      setFallbackLoading(false)
    }
  }, [hasUpstreamPool, runId, currentWorkspaceId, fallbackAttempt])

  // Seçim sınırı format matrisinden; form açıkken üst havuz kartları seçilebilir
  const { setComposing, setMaxKeywords, retainOnly } = selection
  useEffect(() => {
    if (matrix) setMaxKeywords(matrix.limits.max_keywords)
  }, [matrix, setMaxKeywords])
  useEffect(() => {
    setComposing(isCreatingNew)
  }, [isCreatingNew, setComposing])
  useEffect(() => () => setComposing(false), [setComposing])
  // Havuzdan düşen kelime seçimde kalmaz (üst karttan silme dahil)
  useEffect(() => {
    if (!loadingPool && pool.length > 0) retainOnly(pool.map((item) => item.keyword_id))
  }, [pool, loadingPool, retainOnly])

  // 3. Briefs Listesini Yükle (Workspace / Run izolasyonu)
  const fetchBriefs = useCallback(async () => {
    const token = `${currentWorkspaceId ?? 'none'}:${runId}`
    if (!currentWorkspaceId || !runId) {
      setBriefs([])
      setSelectedBriefId(null)
      return
    }
    setLoadingBriefs(true)
    setBriefsError(null)
    try {
      const res = await socialBriefApi.listBriefs(runId, currentWorkspaceId)
      if (scopeRef.current !== token) return
      const list = Array.isArray(res.data) ? res.data : []
      setBriefs(list)
      setFeatureDisabled(false)

      // Son açılan brief yalnız UI kolaylığı; aynı workspace+run listesinde varsa açılır
      try {
        const saved = Number(sessionStorage.getItem(LAST_BRIEF_KEY(currentWorkspaceId, runId)))
        if (saved && list.some((b) => b.id === saved)) {
          setSelectedBriefId((prev) => prev ?? saved)
        }
      } catch {
        // sessionStorage erişilemez: yok say
      }
    } catch (err) {
      if (scopeRef.current !== token) return
      setBriefs([])
      if (apiErrorCode(err) === 'FEATURE_DISABLED') {
        setFeatureDisabled(true)
      } else {
        setBriefsError(socialErrorText(err, 'Sosyal briefler alınamadı.'))
      }
    } finally {
      if (scopeRef.current === token) setLoadingBriefs(false)
    }
  }, [runId, currentWorkspaceId])

  // Workspace veya Run değişince state'i sıfırla ve yeniden çek
  useEffect(() => {
    setBriefs([])
    setSelectedBriefId(null)
    setIsCreatingNew(false)
    selection.clear()
    setSelectedTargets([])
    setFormError(null)
    setBriefsError(null)
    setIsSubmitting(false)
    setBrandName(activeWorkspace?.name || '')
    setBrandContext('')
    void fetchBriefs()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, currentWorkspaceId])

  // Aktif Seçili Brief
  const selectedBrief = useMemo(
    () => briefs.find((b) => b.id === selectedBriefId) || null,
    [briefs, selectedBriefId]
  )

  useEffect(() => {
    if (!currentWorkspaceId || !runId || !selectedBriefId) return
    try {
      sessionStorage.setItem(LAST_BRIEF_KEY(currentWorkspaceId, runId), String(selectedBriefId))
    } catch {
      // yok say
    }
  }, [selectedBriefId, currentWorkspaceId, runId])

  const handleBackToList = () => {
    setSelectedBriefId(null)
    if (currentWorkspaceId && runId) {
      try {
        sessionStorage.removeItem(LAST_BRIEF_KEY(currentWorkspaceId, runId))
      } catch {
        // yok say
      }
    }
  }

  // Platform veya Format Seçim Değişimi
  const currentPlatformObj = useMemo<SocialPlatformOptionResponse | undefined>(() => {
    return matrix?.platforms.find((p) => p.id === targetPlatform)
  }, [matrix, targetPlatform])

  const currentFormatObj = useMemo<SocialFormatOptionResponse | undefined>(() => {
    return currentPlatformObj?.formats.find((f) => f.id === targetFormat)
  }, [currentPlatformObj, targetFormat])

  const handlePlatformChange = (pId: string) => {
    setTargetPlatform(pId)
    const p = matrix?.platforms.find((item) => item.id === pId)
    const f = p?.formats[0]
    setTargetFormat(f?.id ?? '')
    setTargetDurationPresetId(f?.requires_duration ? (f.duration_presets[0]?.id ?? '') : '')
  }

  const handleFormatChange = (fId: string) => {
    setTargetFormat(fId)
    const f = currentPlatformObj?.formats.find((item) => item.id === fId)
    setTargetDurationPresetId(f?.requires_duration ? (f.duration_presets[0]?.id ?? '') : '')
  }

  const currentPairTaken = isDuplicateTarget(selectedTargets, targetPlatform, targetFormat)

  // Hedef Ekle
  const handleAddTarget = () => {
    if (!targetPlatform || !targetFormat) return
    const maxTargets = matrix?.limits.max_targets ?? 6
    if (selectedTargets.length >= maxTargets) {
      setFormError(`En fazla ${maxTargets} hedef ekleyebilirsiniz.`)
      return
    }
    if (currentFormatObj?.requires_duration && !targetDurationPresetId) {
      setFormError('Bu format için süre seçimi zorunludur.')
      return
    }
    // Backend tekilliği (platform, content_format): farklı süre aynı hedef sayılır
    if (isDuplicateTarget(selectedTargets, targetPlatform, targetFormat)) {
      setFormError(
        'Bu platform ve format zaten eklenmiş. Süreyi değiştirmek için önce mevcut hedefi kaldırın.'
      )
      return
    }

    setFormError(null)
    setSelectedTargets((prev) => [
      ...prev,
      {
        platform: targetPlatform,
        content_format: targetFormat,
        duration_preset_id: currentFormatObj?.requires_duration ? targetDurationPresetId : null,
      },
    ])
  }

  const handleRemoveTarget = (index: number) => {
    setSelectedTargets((prev) => prev.filter((_, i) => i !== index))
  }

  // Keyword Seçim
  // Sınır ve uyarı kontrolcüde: üst kart ve alt liste AYNI kuralı kullanır
  const handleToggleKeyword = (kwId: number) => {
    if (selection.toggle(kwId)) setFormError(null)
  }

  const handleRemoveKeyword = (kwId: number) => {
    selection.remove(kwId)
  }

  const maxKeywords = matrix?.limits.max_keywords ?? selection.maxKeywords

  // Havuz kelimelerini filtrele
  const filteredPool = useMemo(() => {
    if (!kwSearch.trim()) return pool
    const q = kwSearch.toLocaleLowerCase('tr-TR')
    return pool.filter((item) => item.keyword.toLocaleLowerCase('tr-TR').includes(q))
  }, [pool, kwSearch])

  // Yeni Brief Kaydet
  const handleCreateBrief = async (e: React.FormEvent) => {
    e.preventDefault()
    if (isSubmitting || disabled) return
    if (!currentWorkspaceId) {
      setFormError('Aktif marka çalışması seçilmelidir.')
      return
    }

    const minKw = matrix?.limits.min_keywords ?? 1
    const maxKw = matrix?.limits.max_keywords ?? 5
    const minTg = matrix?.limits.min_targets ?? 1
    const maxTg = matrix?.limits.max_targets ?? 6

    if (selectedKeywordIds.length < minKw || selectedKeywordIds.length > maxKw) {
      setFormError(`Lütfen ${minKw} ile ${maxKw} arasında anahtar kelime seçin.`)
      return
    }
    if (selectedTargets.length < minTg || selectedTargets.length > maxTg) {
      setFormError(`Lütfen ${minTg} ile ${maxTg} arasında hedef platform/format belirleyin.`)
      return
    }

    const token = scopeRef.current
    setIsSubmitting(true)
    setFormError(null)

    try {
      const payload: SocialBriefCreateRequest = {
        scoring_run_id: runId,
        keyword_ids: selectedKeywordIds,
        targets: selectedTargets,
        brand_name: brandName.trim() || undefined,
        brand_context: brandContext.trim() || undefined,
      }

      const res = await socialBriefApi.createBrief(payload, currentWorkspaceId)
      // Yanıt gelmeden workspace/run değiştiyse eski kapsamın brief'i ekrana yazılmaz
      if (scopeRef.current !== token) return
      const newBrief = res.data

      setBriefs((prev) => [newBrief, ...prev.filter((b) => b.id !== newBrief.id)])
      setSelectedBriefId(newBrief.id)
      setIsCreatingNew(false)
      selection.clear()
      setSelectedTargets([])
      setBrandContext('')
    } catch (err) {
      if (scopeRef.current !== token) return
      setFormError(socialErrorText(err, 'Sosyal brief oluşturulamadı.'))
    } finally {
      if (scopeRef.current === token) setIsSubmitting(false)
    }
  }

  // Yardımcı Label Bulucular
  const getPlatformLabel = useCallback(
    (pId: string) => matrix?.platforms.find((p) => p.id === pId)?.label || pId,
    [matrix]
  )

  const getFormatLabel = useCallback(
    (pId: string, fId: string) => {
      const p = matrix?.platforms.find((item) => item.id === pId)
      return p?.formats.find((f) => f.id === fId)?.label || fId
    },
    [matrix]
  )

  const getPresetLabel = (pId: string, fId: string, presetId?: string | null) => {
    if (!presetId) return null
    const p = matrix?.platforms.find((item) => item.id === pId)
    const f = p?.formats.find((item) => item.id === fId)
    return f?.duration_presets.find((d) => d.id === presetId)?.label || presetId
  }

  // RENDER: Özellik kapalı — kullanıcıya durumu açıkça bildir
  if (featureDisabled) {
    return (
      <div className="sb-workspace animate-fade-in">
        <div className="sb-card sb-empty-state" role="status">
          <div className="sb-empty-icon">
            <FileText size={24} />
          </div>
          <h3 className="sb-empty-title">Sosyal brief akışı bu ortamda henüz açık değil</h3>
          <p className="sb-empty-text">
            Sosyal brief özelliği bu ortamda kapalı. Kullanabilmek için yöneticinizden özelliği
            etkinleştirmesini isteyin.
          </p>
          {onOpenHistory && (
            <button type="button" className="sb-btn sb-btn-history" onClick={onOpenHistory}>
              <History size={15} />
              Geçmiş Sosyal İçerikler
            </button>
          )}
        </div>
      </div>
    )
  }

  // RENDER: Brief Oluşturma Formu
  if (isCreatingNew) {
    return (
      <div className="sb-workspace animate-fade-in">
        <div className="sb-card">
          <div className="sb-card-head">
            <div className="sb-card-title-wrap">
              <button
                type="button"
                className="sb-btn sb-btn-ghost"
                onClick={() => {
                  setIsCreatingNew(false)
                  setFormError(null)
                }}
              >
                <ChevronLeft size={16} />
                Vazgeç
              </button>
              <div>
                <h2 className="sb-card-title">Yeni Sosyal Brief Oluştur</h2>
                <p className="sb-card-subtitle">
                  Sosyal kanal havuzundan anahtar kelimeler ve hedef platform/formatları belirleyin.
                </p>
              </div>
            </div>
          </div>

          <form onSubmit={handleCreateBrief}>
            {formError && (
              <div
                className="channels-alert channels-alert-error"
                style={{ marginBottom: 18 }}
                role="alert"
              >
                {formError}
              </div>
            )}
            {selection.notice && (
              <div
                className="channels-alert channels-alert-error"
                style={{ marginBottom: 18 }}
                role="alert"
              >
                {selection.notice}
              </div>
            )}

            {/* Bölüm 1: Anahtar Kelime Seçimi (1-5 adet) */}
            <div className="sb-form-group">
              <div className="sb-form-heading">
                <span>1. Hedef Anahtar Kelimeler</span>
                <span className="sb-form-counter">
                  Seçilen: <b>{selectedKeywordIds.length}</b> / {maxKeywords} (Min 1, Max{' '}
                  {maxKeywords})
                </span>
              </div>
              <p className="sb-form-desc">
                Sosyal havuzundan brief'e dahil etmek istediğiniz 1–{maxKeywords} kelimeyi seçin.
                Yukarıdaki Social Havuzu kartlarına tıklayarak da seçebilirsiniz.
              </p>

              {/* Seçilen Kelimeler Çipleri */}
              {selectedKeywordIds.length > 0 && (
                <div className="sb-chip-container" style={{ marginBottom: 10 }}>
                  {selectedKeywordIds.map((kwId) => {
                    const kwItem = pool.find((p) => p.keyword_id === kwId)
                    return (
                      <span key={kwId} className="sb-chip sb-chip-selected">
                        <Tag size={12} />
                        <b>{kwItem?.keyword || `#${kwId}`}</b>
                        <button
                          type="button"
                          className="sb-chip-remove"
                          onClick={() => handleRemoveKeyword(kwId)}
                          aria-label={`Kaldır: ${kwItem?.keyword || kwId}`}
                        >
                          <X size={12} />
                        </button>
                      </span>
                    )
                  })}
                </div>
              )}

              {/* Üst Social Havuzu bu sayfanın tek seçicisidir. Bağımsız kullanılan
                  bileşenlerde üst havuz yoksa eski seçici erişilebilir kalır. */}
              {!keywordSelection && (
                <div className="sb-kw-picker">
                  <div className="sb-kw-search">
                    <input
                      id="kw-search-input"
                      type="text"
                      className="sb-input"
                      placeholder="Havuzda kelime ara..."
                      value={kwSearch}
                      onChange={(e) => setKwSearch(e.target.value)}
                    />
                  </div>

                  {loadingPool ? (
                    <p className="sb-empty-text" role="status">
                      Kanal havuzu yükleniyor...
                    </p>
                  ) : poolError ? (
                    <div className="channels-alert channels-alert-error" role="alert">
                      {poolError}{' '}
                      {retryPool && (
                        <button
                          type="button"
                          className="sb-btn sb-btn-secondary sb-btn-xs"
                          onClick={retryPool}
                        >
                          <RefreshCw size={12} />
                          Tekrar dene
                        </button>
                      )}
                    </div>
                  ) : filteredPool.length === 0 ? (
                    <p className="sb-empty-text">
                      {pool.length === 0
                        ? 'Sosyal kanal havuzunda kelime bulunamadı.'
                        : 'Aramaya uygun kelime bulunamadı.'}
                    </p>
                  ) : (
                    <div className="sb-kw-list">
                      {filteredPool.map((item) => {
                        const isSelected = selectedKeywordIds.includes(item.keyword_id)
                        return (
                          <button
                            key={item.id}
                            type="button"
                            className={`sb-kw-option ${isSelected ? 'is-selected' : ''}`}
                            aria-pressed={isSelected}
                            onClick={() => handleToggleKeyword(item.keyword_id)}
                          >
                            <span>{item.keyword}</span>
                            {item.volume != null && (
                              <span className="sb-kw-volume">
                                ({Number(item.volume).toLocaleString('tr-TR')} hacim)
                              </span>
                            )}
                            {isSelected && <CheckCircle2 size={12} />}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Bölüm 2: Hedef Platform ve Formatlar (1-6 adet) */}
            <div className="sb-form-group">
              <label htmlFor="target-platform-select">
                <span>2. Hedef Platform ve Formatlar</span>
                <span className="sb-form-counter">
                  Seçilen: <b>{selectedTargets.length}</b> / 6 (Min 1, Max 6)
                </span>
              </label>
              <p className="sb-form-desc">
                İçerik üretilecek platform ve formatları ekleyin. Video formatlarında süre seçimi
                zorunludur.
              </p>

              {/* Seçilen Hedefler Listesi */}
              {selectedTargets.length > 0 && (
                <div className="sb-chip-container" style={{ marginBottom: 12 }}>
                  {selectedTargets.map((target, idx) => {
                    const pLabel = getPlatformLabel(target.platform)
                    const fLabel = getFormatLabel(target.platform, target.content_format)
                    const dLabel = getPresetLabel(
                      target.platform,
                      target.content_format,
                      target.duration_preset_id
                    )
                    return (
                      <span key={idx} className="sb-chip sb-chip-target">
                        <b>{pLabel}</b> · {fLabel}
                        {dLabel && <span className="sb-badge sb-badge-accent">{dLabel}</span>}
                        <button
                          type="button"
                          className="sb-chip-remove"
                          onClick={() => handleRemoveTarget(idx)}
                          aria-label={`Kaldır hedef: ${pLabel} ${fLabel}`}
                        >
                          <X size={12} />
                        </button>
                      </span>
                    )
                  })}
                </div>
              )}

              {/* Hedef Ekleme Kontrolleri */}
              {loadingMatrix ? (
                <p className="sb-empty-text">Format seçenekleri yükleniyor...</p>
              ) : matrix ? (
                <div className="sb-target-builder">
                  <div className="sb-target-builder-field">
                    <label htmlFor="target-platform-select">Platform</label>
                    <select
                      id="target-platform-select"
                      className="sb-select"
                      value={targetPlatform}
                      onChange={(e) => handlePlatformChange(e.target.value)}
                    >
                      {matrix.platforms.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="sb-target-builder-field">
                    <label htmlFor="target-format-select">Format</label>
                    <select
                      id="target-format-select"
                      className="sb-select"
                      value={targetFormat}
                      onChange={(e) => handleFormatChange(e.target.value)}
                    >
                      {currentPlatformObj?.formats.map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Süre Seçimi: Yalnızca requires_duration=true ise görünür */}
                  {currentFormatObj?.requires_duration && (
                    <div className="sb-target-builder-field">
                      <label htmlFor="target-duration-select">Süre Ön Ayarı</label>
                      <select
                        id="target-duration-select"
                        className="sb-select"
                        value={targetDurationPresetId}
                        onChange={(e) => setTargetDurationPresetId(e.target.value)}
                      >
                        {currentFormatObj.duration_presets.map((d: DurationPresetResponse) => (
                          <option key={d.id} value={d.id}>
                            {d.label} ({d.min_sec}-{d.max_sec}s)
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  <button
                    type="button"
                    className="sb-btn sb-btn-primary"
                    onClick={handleAddTarget}
                    disabled={
                      selectedTargets.length >= (matrix.limits.max_targets ?? 6) || currentPairTaken
                    }
                    title={currentPairTaken ? 'Bu platform ve format zaten eklenmiş' : undefined}
                  >
                    <Plus size={14} />
                    Hedef Ekle
                  </button>
                  {currentPairTaken && (
                    <p className="sb-form-desc sb-target-dup-note" role="note">
                      Bu platform/format zaten ekli. Her platform-format çifti bir kez eklenebilir;
                      süreyi değiştirmek için mevcut hedefi kaldırın.
                    </p>
                  )}
                </div>
              ) : matrixError ? (
                <div className="channels-alert channels-alert-error" role="alert">
                  {matrixError}
                </div>
              ) : null}
            </div>

            {/* Bölüm 3: Marka Bilgileri */}
            <div className="sb-form-group">
              <label htmlFor="brief-brand-name">Marka Adı (Opsiyonel)</label>
              <input
                id="brief-brand-name"
                type="text"
                className="sb-input"
                maxLength={200}
                placeholder="Örn: Acme Tech"
                value={brandName}
                onChange={(e) => setBrandName(e.target.value)}
              />
            </div>

            <div className="sb-form-group">
              <label htmlFor="brief-brand-context">Marka Bağlamı / Özel Notlar (Opsiyonel)</label>
              <textarea
                id="brief-brand-context"
                className="sb-textarea"
                rows={3}
                placeholder="Kampanya amacı, hedef kitle veya tonlama notları..."
                value={brandContext}
                onChange={(e) => setBrandContext(e.target.value)}
              />
            </div>

            {/* Kaydet ve İptal Butonları */}
            <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
              <button
                type="submit"
                className="sb-btn sb-btn-primary"
                disabled={
                  isSubmitting ||
                  selectedKeywordIds.length < 1 ||
                  selectedKeywordIds.length > maxKeywords ||
                  selectedTargets.length < 1 ||
                  selectedTargets.length > 6
                }
              >
                {isSubmitting ? (
                  <>
                    <RefreshCw size={14} className="chx-spin" />
                    Kaydediliyor...
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={15} />
                    Brief'i Kaydet ve Aç
                  </>
                )}
              </button>
              <button
                type="button"
                className="sb-btn sb-btn-ghost"
                onClick={() => {
                  setIsCreatingNew(false)
                  setFormError(null)
                }}
                disabled={isSubmitting}
              >
                İptal
              </button>
            </div>
          </form>
        </div>
      </div>
    )
  }

  // RENDER: Seçili Brief Detay Görünümü — kapsam anahtarı eski yanıtları izole eder
  if (selectedBrief && currentWorkspaceId) {
    return (
      <SocialBriefDetail
        key={`${currentWorkspaceId}:${runId}:${selectedBrief.id}`}
        brief={selectedBrief}
        workspaceId={currentWorkspaceId}
        disabled={disabled}
        pollIntervalMs={pollIntervalMs}
        platformLabel={getPlatformLabel}
        formatLabel={getFormatLabel}
        onBack={handleBackToList}
        onNewBrief={() => {
          handleBackToList()
          setIsCreatingNew(true)
        }}
        onBriefChanged={fetchBriefs}
      />
    )
  }

  // RENDER: Brief Listesi (Varsayılan Görünüm)
  return (
    <div className="sb-workspace animate-fade-in">
      <div className="sb-card">
        <div className="sb-card-head">
          <div>
            <h2 className="sb-card-title">Kayıtlı Sosyal Briefler</h2>
            <p className="sb-card-subtitle">
              Bu analiz çalışması için tanımlanmış sosyal medya briefleri.
            </p>
          </div>
          <div className="sb-card-actions">
            <button
              type="button"
              className="sb-btn sb-btn-primary"
              onClick={() => setIsCreatingNew(true)}
              disabled={disabled}
            >
              <Plus size={15} />
              Yeni Brief Oluştur
            </button>
            {onOpenHistory && (
              <button type="button" className="sb-btn sb-btn-history" onClick={onOpenHistory}>
                <History size={15} />
                Geçmiş Sosyal İçerikler
              </button>
            )}
          </div>
        </div>

        {briefsError && (
          <div
            className="channels-alert channels-alert-error"
            style={{ marginBottom: 16 }}
            role="alert"
          >
            {briefsError}
          </div>
        )}

        {loadingBriefs ? (
          <div className="sb-empty-state">
            <RefreshCw size={24} className="chx-spin" style={{ color: 'var(--text-muted)' }} />
            <p className="sb-empty-text">Briefler yükleniyor...</p>
          </div>
        ) : briefs.length === 0 ? (
          <div className="sb-empty-state">
            <div className="sb-empty-icon">
              <FileText size={24} />
            </div>
            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Henüz brief oluşturulmadı</h3>
            <p className="sb-empty-text">
              Sosyal medya havuzundaki anahtar kelimeleri hedef platform ve formatlarla eşleştirmek
              için ilk briefinizi oluşturun.
            </p>
            <button
              type="button"
              className="sb-btn sb-btn-primary"
              style={{ marginTop: 8 }}
              onClick={() => setIsCreatingNew(true)}
              disabled={disabled}
            >
              <Plus size={14} />
              İlk Brief'i Oluştur
            </button>
          </div>
        ) : (
          <div className="sb-briefs-grid">
            {briefs.map((brief) => {
              const formattedDate = brief.created_at
                ? new Date(brief.created_at).toLocaleDateString('tr-TR', {
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : '-'

              return (
                <div key={brief.id} className="sb-brief-item-card">
                  <div className="sb-brief-item-head">
                    <div className="sb-brief-item-title">
                      <span>Brief #{brief.id}</span>
                      {brief.is_stale ? (
                        <span className="sb-badge sb-badge-stale">Stale</span>
                      ) : brief.locked_at ? (
                        <span className="sb-badge sb-badge-locked">Kilitli</span>
                      ) : (
                        <span className="sb-badge sb-badge-active">Hazır</span>
                      )}
                    </div>
                    <span className="sb-brief-item-date">{formattedDate}</span>
                  </div>

                  <div className="sb-brief-item-body">
                    {brief.brand_name_snapshot && (
                      <div className="sb-brief-item-row">
                        <span className="sb-brief-item-label">Marka</span>
                        <span style={{ fontWeight: 500 }}>{brief.brand_name_snapshot}</span>
                      </div>
                    )}

                    <div className="sb-brief-item-row">
                      <span className="sb-brief-item-label">
                        Kelimeler ({brief.keywords.length})
                      </span>
                      <div className="sb-chip-container">
                        {brief.keywords.slice(0, 3).map((kw) => (
                          <span key={kw.id} className="sb-chip">
                            {kw.keyword_snapshot}
                          </span>
                        ))}
                        {brief.keywords.length > 3 && (
                          <span className="sb-chip">+{brief.keywords.length - 3}</span>
                        )}
                      </div>
                    </div>

                    <div className="sb-brief-item-row">
                      <span className="sb-brief-item-label">Hedefler ({brief.targets.length})</span>
                      <div className="sb-chip-container">
                        {brief.targets.slice(0, 2).map((tg) => (
                          <span key={tg.id} className="sb-chip">
                            <b>{getPlatformLabel(tg.platform)}</b>{' '}
                            {getFormatLabel(tg.platform, tg.content_format)}
                          </span>
                        ))}
                        {brief.targets.length > 2 && (
                          <span className="sb-chip">+{brief.targets.length - 2}</span>
                        )}
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="sb-btn sb-btn-secondary"
                    style={{ width: '100%', justifyContent: 'center' }}
                    onClick={() => setSelectedBriefId(brief.id)}
                  >
                    Brief'i Aç →
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
