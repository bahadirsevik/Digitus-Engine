/**
 * SEO+GEO üretim paneli — Claude Design "SEO+GEO" tasarımı.
 * Kanal sayfası içinde inline çalışır; run seçimi dışarıdan gelir.
 * Kök eleman display:contents — form kartı ChannelWorkspaceView'daki iki
 * kolonlu gridin sağ hücresine, banner/sonuçlar tam genişliğe oturur.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlignLeft,
  Calendar,
  CheckCircle,
  FileText,
  Heading,
  Image as ImageIcon,
  Link2,
  MessageCircleQuestion,
  RefreshCw,
  Sparkles,
  Tag,
  X,
} from 'lucide-react'
import {
  useTaskPolling,
  useScopedTaskId,
  getWorkspaceTaskKey,
  setStoredTaskId,
} from '../../hooks/useTaskPolling'
import ErrorBanner from '../ErrorBanner'
import { useBrandStore } from '../../stores/brandStore'
import { apiErrorMessage, generationApi, policyStaleFromError } from '../../services/api'
import type { ScoringRun, SEOGeoItem } from '../../types/models'
import '../../pages/Generation.css'

// Tasarımdaki üretim adımları; gerçek task progress eşiklerinden türetilir
const SEO_STEPS = [
  'SEO havuzu kelimeleri kümeleniyor',
  'Başlık ve slug üretiliyor',
  'Yapay zekâ · içerik gövdesi yazımı',
  'Yapay zekâ · meta açıklama & alt başlıklar',
  'İç/dış link önerileri çıkarılıyor',
  'SEO & GEO skorları hesaplanıyor',
]

const TONES = [
  { value: 'informative', label: 'Bilgilendirici' },
  { value: 'professional', label: 'Profesyonel' },
  { value: 'casual', label: 'Samimi' },
]

function seoStepFromProgress(progress: number): number {
  if (progress < 12) return 0
  if (progress < 25) return 1
  if (progress < 60) return 2
  if (progress < 80) return 3
  if (progress < 92) return 4
  return 5
}

const hostFrom = (url?: string | null) => {
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

const shortDate = (iso?: string | null) => {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString('tr-TR')
}

function GenBanner({ progress }: { progress: number }) {
  const step = seoStepFromProgress(progress)
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
          <div className="adsx-banner-title">SEO+GEO içerikleri üretiliyor</div>
          <div className="adsx-banner-sub">
            Havuzdaki kelimeler için blog içerikleri yapay zekâ ile yazılıyor.{' '}
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
        {SEO_STEPS.map((label, i) => {
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
  const step = seoStepFromProgress(progress)
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
          <div className="adsx-toast-title">İçerikler üretiliyor</div>
          <div className="adsx-toast-sub">{SEO_STEPS[Math.min(step, SEO_STEPS.length - 1)]}</div>
        </div>
        <button type="button" className="adsx-toast-close" onClick={onDismiss} title="Kapat">
          <X size={13} />
        </button>
      </div>
      <div className="adsx-toast-body">
        <div className="adsx-toast-meta">
          <span>
            {step}/{SEO_STEPS.length} adım
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

// Tasarım: zengin içerik kartı — tag+tarih, başlık+slug, SERP önizlemesi,
// giriş paragrafı, H2 alt başlıklar, İÇ/DIŞ link önerileri, istatistik+skorlar
function ContentCard({ item, host }: { item: SEOGeoItem; host: string }) {
  const slug = item.url_suggestion ? `/${item.url_suggestion.replace(/^\//, '')}` : null
  const metaLen = item.meta_description?.length || 0
  const subs = item.subheadings || []
  const date = shortDate(item.created_at)
  const review = item.publish_review
  const stats = [
    item.word_count ? `${item.word_count} kelime` : null,
    subs.length ? `${subs.length} alt başlık` : null,
    item.keyword_density != null ? `%${item.keyword_density} yoğunluk` : null,
  ].filter(Boolean)

  const links = [
    item.internal_link?.anchor || item.internal_link?.url
      ? { kind: 'İÇ' as const, ...item.internal_link }
      : null,
    item.external_link?.anchor || item.external_link?.url
      ? { kind: 'DIŞ' as const, ...item.external_link }
      : null,
  ].filter(Boolean) as { kind: 'İÇ' | 'DIŞ'; anchor?: string | null; url?: string | null }[]

  return (
    <div className="seox-card">
      <div className="seox-card-top">
        <span className="seox-kw-chip">
          <Tag size={12} /> {item.keyword}
        </span>
        <span className="seox-meta-right">
          {item.is_stale && <span className="seox-stale">Güncel değil</span>}
          {review?.required && <span className="seox-review">{review.label}</span>}
          {date && (
            <span className="seox-date chx-mono">
              <Calendar size={12} /> {date}
            </span>
          )}
        </span>
      </div>

      <h3 className="seox-title">{item.title || item.keyword}</h3>
      {slug && <div className="seox-url chx-mono">{slug}</div>}

      {item.meta_description && (
        <div className="seox-serp">
          <div className="seox-serp-head">
            <span className="seox-section-label">META AÇIKLAMA · GOOGLE ÖNİZLEME</span>
            <span className={`seox-serp-count chx-mono${metaLen > 160 ? ' is-over' : ''}`}>
              {metaLen}/160
            </span>
          </div>
          <div className="seox-serp-title">{item.title || item.keyword}</div>
          <div className="seox-serp-url">
            {host}
            {slug || ''}
          </div>
          <div className="seox-serp-desc">{item.meta_description}</div>
        </div>
      )}

      {item.intro_paragraph && (
        <>
          <div className="seox-section-label with-icon">
            <AlignLeft size={12} /> GİRİŞ PARAGRAFI
          </div>
          <p className="seox-intro">{item.intro_paragraph}</p>
        </>
      )}

      {subs.length > 0 && (
        <>
          <div className="seox-section-label with-icon">
            <Heading size={12} /> ALT BAŞLIKLAR ({subs.length})
          </div>
          <div className="seox-list">
            {subs.map((s, i) => (
              <div key={i} className="seox-sub">
                <span className="seox-h2 chx-mono">H2</span>
                <span>{s}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {(item.faq_items?.length || 0) > 0 && (
        <>
          <div className="seox-section-label with-icon">
            <MessageCircleQuestion size={12} /> FAQ ({item.faq_items!.length}) · SCHEMA'YA HAZIR
            VERİ
          </div>
          <div className="seox-list">
            {item.faq_items!.map((faq, fi) => (
              <div key={fi} className="seox-faq">
                <div className="seox-faq-q">{faq.question}</div>
                <div className="seox-faq-a">{faq.answer}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {(item.image_alt_texts?.length || 0) > 0 && (
        <>
          <div className="seox-section-label with-icon">
            <ImageIcon size={12} /> GÖRSEL ALT METNİ ÖNERİLERİ · GÖRSELE UYGULANMADI
          </div>
          <div className="seox-list">
            {item.image_alt_texts!.map((alt, ai) => (
              <div key={ai} className="seox-alt chx-mono">
                {alt}
              </div>
            ))}
          </div>
        </>
      )}

      {links.length > 0 && (
        <>
          <div className="seox-section-label with-icon">
            <Link2 size={12} /> LİNK ÖNERİLERİ
          </div>
          <div className="seox-list">
            {links.map((l, i) => (
              <div key={i} className="seox-link">
                <span className={`seox-link-kind${l.kind === 'DIŞ' ? ' is-ext' : ''}`}>
                  {l.kind}
                </span>
                {l.anchor && <span className="seox-link-anchor">{l.anchor}</span>}
                {l.url && (
                  <>
                    <span className="seox-link-arrow">→</span>
                    <span className="seox-link-url chx-mono">
                      {l.url.replace(/^https?:\/\//, '')}
                    </span>
                  </>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {review && (
        <details className="seox-review-box">
          <summary className="seox-section-label">
            YAYIN ÖNCESİ KONTROL{review.required ? ' · İNCELEME GEREKLİ' : ''}
          </summary>
          {review.critical_failures.length > 0 && (
            <ul className="seox-review-list">
              {review.critical_failures.map((f, i) => (
                <li key={`f${i}`}>
                  <span className="seox-review-src">
                    {f.source === 'geo_ai' ? 'AI değerlendirmesi' : 'Otomatik kontrol'}
                  </span>{' '}
                  {f.detail || f.criterion}
                </li>
              ))}
            </ul>
          )}
          <ul className="seox-review-list is-manual">
            {review.manual_checks.map((m, i) => (
              <li key={`m${i}`}>{m}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="seox-footer">
        {stats.length > 0 && <span className="seox-stats chx-mono">{stats.join(' · ')}</span>}
        <div className="seox-scores">
          <span className="seox-score">SEO {(item.seo_score * 100).toFixed(0)}%</span>
          <span className="seox-score">GEO {(item.geo_score * 100).toFixed(0)}%</span>
          <span className="seox-score is-combined">
            Kombine {(item.combined_score * 100).toFixed(0)}%
          </span>
        </div>
      </div>
    </div>
  )
}

export default function SeoGeoPanel({ runId, run }: { runId: number; run?: ScoringRun | null }) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const taskStorageKey = getWorkspaceTaskKey('seo_task', activeWorkspace?.id, runId)
  // Run/workspace kapsamı: await sonrası dönen yanıt, kapsam değiştiyse state'e yazılmaz
  const scopeKey = `${activeWorkspace?.id ?? 'none'}:${runId}`
  const scopeRef = useRef(scopeKey)
  scopeRef.current = scopeKey
  // Unmount sonrası dönen başlatma yanıtı UI state'ine dokunmaz
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const [limit, setLimit] = useState(10)
  const [tone, setTone] = useState('informative')
  const [taskId, setTaskId] = useScopedTaskId(taskStorageKey)
  const [results, setResults] = useState<SEOGeoItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toastOpen, setToastOpen] = useState(true)

  const polling = useTaskPolling(taskId, taskStorageKey, 3000, activeWorkspace?.id, runId)
  const channelDisabled = run?.enable_seo === false

  // Run/workspace değişince önceki run'ın sonuçları ve devam eden istek durumu taşınmaz
  useEffect(() => {
    setResults([])
    setError(null)
    setLoading(false)
  }, [scopeKey])

  const fetchResults = useCallback(async () => {
    if (!activeWorkspace?.id || !runId) return
    const requestScope = scopeRef.current
    try {
      const res = await generationApi.listSeoGeo(runId, 100, activeWorkspace.id)
      if (scopeRef.current !== requestScope) return
      setResults((res.data as { items?: SEOGeoItem[] }).items || [])
    } catch (e) {
      console.error('Failed to fetch SEO results:', e)
    }
  }, [activeWorkspace?.id, runId])

  useEffect(() => {
    void fetchResults()
  }, [fetchResults])

  useEffect(() => {
    if (polling.isCompleted) {
      void fetchResults()
    }
  }, [polling.isCompleted, fetchResults])

  const startGeneration = async () => {
    if (!activeWorkspace?.id) {
      setError('Marka çalışması seçin')
      return
    }
    const requestScope = scopeRef.current
    setLoading(true)
    setError(null)
    setToastOpen(true)
    try {
      const res = await generationApi.bulkSeoGeo(runId, limit, activeWorkspace.id, tone)
      const startedTaskId = (res.data as { task_id: string }).task_id
      // Görev kimliği HER ZAMAN başlatıldığı run'ın anahtarına yazılır (unmount/run değişimi
      // olsa bile kaybolmaz); UI state yalnız panel hâlâ aynı kapsamdaysa güncellenir.
      setStoredTaskId(taskStorageKey, startedTaskId)
      if (!mountedRef.current || scopeRef.current !== requestScope) return
      setTaskId(startedTaskId)
    } catch (err: unknown) {
      if (scopeRef.current !== requestScope) return
      // Plan v13: bayat havuz 409'u kendi mesajıyla (yeniden atama çağrısı)
      const stale = policyStaleFromError(err)
      setError(stale ? stale.message : apiErrorMessage(err))
    } finally {
      if (scopeRef.current === requestScope) setLoading(false)
    }
  }

  const generating = loading || polling.isActive
  const host = hostFrom(activeWorkspace?.company_url)
  const brandName = (activeWorkspace?.profile_data?.company_name as string) || activeWorkspace?.name

  return (
    <div className="adsx-root">
      {/* Üretim form kartı — grid'in sağ hücresi */}
      <section className="chx-card adsx-form-card">
        <div className="adsx-form-head">
          <span className="adsx-form-icon">
            <FileText size={15} />
          </span>
          <div>
            <h2>SEO+GEO İçerik Üretimi</h2>
            <div className="adsx-form-sub">
              Bu analizin SEO havuzundaki kelimeler için blog içerikleri üretir.
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
              value={brandName || ''}
              readOnly
              title="Marka profilinden gelir"
            />
          </label>
          <label className="kwx-field">
            <span className="kwx-field-label">Web Sitesi URL</span>
            <input
              className="kwx-input"
              type="url"
              value={activeWorkspace?.company_url || ''}
              readOnly
              title="Marka profilinden gelir"
            />
          </label>
        </div>
        <div className="adsx-form-grid adsx-usp-field">
          <label className="kwx-field">
            <span className="kwx-field-label">Limit (içerik adedi)</span>
            <input
              className="kwx-input"
              type="number"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              min={1}
              max={100}
            />
          </label>
          <label className="kwx-field">
            <span className="kwx-field-label">İçerik Tonu</span>
            <select className="kwx-input" value={tone} onChange={(e) => setTone(e.target.value)}>
              {TONES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {channelDisabled && (
          <p className="channel-disabled-hint">Bu çalışmada SEO kanalı devre dışı.</p>
        )}
        <div className="adsx-form-actions">
          <button
            type="button"
            className="kwx-run-btn"
            onClick={startGeneration}
            disabled={generating || !runId || channelDisabled}
            title={channelDisabled ? 'Bu çalışmada SEO kanalı aktif değil' : undefined}
          >
            {generating ? 'Üretiliyor…' : 'SEO İçerik Üret'}
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
            error={polling.errorMessage || 'SEO içerik üretimi başarısız oldu.'}
            onDismiss={() => setTaskId(null)}
            onRetry={() => setTaskId(null)}
          />
        </div>
      )}

      {results.length > 0 && (
        <div className="adsx-span adsx-results">
          <div className="adsx-results-head">
            <CheckCircle size={18} strokeWidth={2.2} />
            <h2>Üretilen İçerikler ({results.length})</h2>
          </div>
          <div className="seox-grid">
            {results.map((item) => (
              <ContentCard key={item.id} item={item} host={host} />
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
