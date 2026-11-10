import { useEffect, useState } from 'react'
import { AlertCircle, Globe, Info, Loader2 } from 'lucide-react'
import { useMemo } from 'react'
import { useBrandStore } from '../stores/brandStore'
import {
  apiErrorCode,
  apiErrorMessage,
  googleAdsUrlSeedApi,
  KeywordImportResponse,
  SkippedKeywordDetail,
  UrlSeedIdea,
} from '../services/api'
import KeywordImportResult from './KeywordImportResult'
import { MarketLine } from './MarketBadge'
import { FetchedMarket, fetchedMarketFrom, marketWorkspaceLink } from '../services/market'
import {
  LOW_VOLUME_THRESHOLD,
  selectMinVolume,
  selectTopN,
  sortByVolumeDesc,
  splitLowVolume,
} from './keywordSelection'
import { getExcludeThemesFromProfile, previewThemeExclusions } from './themePreview'
import './GoogleAdsKeywordSearch.css'

interface Props {
  // market: Google'a gerçekten gönderilen pazar; yoksa import satırlarında alan atlanır
  onImport: (
    keywords: UrlSeedIdea[],
    market?: FetchedMarket
  ) => Promise<KeywordImportResponse | void>
  // Rakip URL'leri kaynak kartından hazır URL ile açılır
  initialUrl?: string
  onForceInclude?: (detail: SkippedKeywordDetail) => Promise<void>
}

const ideaVolume = (idea: UrlSeedIdea) => idea.monthly_volume || 0

const errorMessageFrom = apiErrorMessage

export default function UrlKeywordExtractor({ onImport, initialUrl, onForceInclude }: Props) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)

  const [url, setUrl] = useState(initialUrl || activeWorkspace?.company_url || '')
  const [maxResults, setMaxResults] = useState(300)
  const [minVolume, setMinVolume] = useState(0)
  const [includeSeed, setIncludeSeed] = useState(false)
  const [ideas, setIdeas] = useState<UrlSeedIdea[]>([])
  const [total, setTotal] = useState(0)
  const [cached, setCached] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const [sourceStatus, setSourceStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fetchedMarket, setFetchedMarket] = useState<FetchedMarket | null>(null)
  const [marketNotSet, setMarketNotSet] = useState(false)
  const [importResult, setImportResult] = useState<
    { kind: 'success'; data: KeywordImportResponse } | { kind: 'error'; message: string } | null
  >(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [showLowVolume, setShowLowVolume] = useState(false)
  const [showThemePreview, setShowThemePreview] = useState(false)

  const excludeThemes = useMemo(
    () => getExcludeThemesFromProfile(activeWorkspace?.profile_data),
    [activeWorkspace?.profile_data]
  )
  const themePreview = useMemo(
    () => previewThemeExclusions(ideas, (idea) => idea.keyword, excludeThemes),
    [ideas, excludeThemes]
  )
  const themePreviewMap = useMemo(
    () => new Map(themePreview.map((item) => [item.keyword, item.matched])),
    [themePreview]
  )

  useEffect(() => {
    setUrl(initialUrl || activeWorkspace?.company_url || '')
    setIdeas([])
    setTotal(0)
    setCached(false)
    setWarnings([])
    setSourceStatus(null)
    setImportResult(null)
    setError(null)
    setFetchedMarket(null)
    setMarketNotSet(false)
    setShowThemePreview(false)
  }, [activeWorkspace?.id, activeWorkspace?.company_url, initialUrl])

  const handleExtract = async (forceRefresh = false) => {
    if (!url.trim() || !activeWorkspace?.id) return
    const normalizedUrl = /^https?:\/\//i.test(url.trim()) ? url.trim() : `https://${url.trim()}`
    setLoading(true)
    setError(null)
    setMarketNotSet(false)
    setWarnings([])
    setSourceStatus(null)
    setImportResult(null)
    setShowThemePreview(false)
    try {
      const res = await googleAdsUrlSeedApi.keywordIdeasByUrl({
        url: normalizedUrl,
        brand_profile_id: activeWorkspace.id,
        max_results: maxResults,
        min_volume: minVolume,
        include_keyword_seed: includeSeed,
        refresh: forceRefresh,
      })
      const data = res.data
      setFetchedMarket(fetchedMarketFrom(data))
      const sorted = sortByVolumeDesc(data.ideas || [], ideaVolume)
      setIdeas(sorted)
      setSelected(new Set(sorted.map((idea) => idea.keyword)))
      setShowLowVolume(false)
      setShowThemePreview(false)
      setTotal(data.total)
      setCached(data.cached)
      setWarnings(data.warnings || [])
      const status = data.source_status
      setSourceStatus(
        status
          ? [
              status.reachable ? 'URL kontrolü başarılı' : 'URL kontrolü uyarı verdi',
              status.http_status ? `HTTP ${status.http_status}` : null,
              status.warning || null,
            ]
              .filter(Boolean)
              .join(' | ')
          : null
      )
      if ((data.ideas || []).length === 0) {
        setError(
          "Google Ads bu URL'den anahtar kelime fikri üretmedi. Alt kategori URL'si deneyin veya başlangıç kelimesiyle arama yapın."
        )
      }
    } catch (err: unknown) {
      setIdeas([])
      setTotal(0)
      setCached(false)
      setMarketNotSet(apiErrorCode(err) === 'MARKET_NOT_SET')
      setError(
        errorMessageFrom(
          err,
          "URL'den anahtar kelime çıkarma başarısız oldu. Alt kategori URL'si deneyin veya başlangıç kelimesiyle Google Ads araması yapın."
        )
      )
    } finally {
      setLoading(false)
    }
  }

  const handleImport = async () => {
    setImporting(true)
    setImportResult(null)
    try {
      const chosen = ideas.filter((idea) => selected.has(idea.keyword))
      const data = await onImport(chosen, fetchedMarket ?? undefined)
      if (!data) {
        throw new Error('Aktarım sonucu alınamadı')
      }
      setImportResult({ kind: 'success', data })
      setIdeas([])
      setSelected(new Set())
    } catch (err: unknown) {
      setImportResult({
        kind: 'error',
        message: errorMessageFrom(err, 'Aktarım başarısız oldu'),
      })
    } finally {
      setImporting(false)
    }
  }

  const toggleSelected = (keyword: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(keyword)) next.delete(keyword)
      else next.add(keyword)
      return next
    })
  }

  const { visible: highVolumeIdeas, low: lowVolumeIdeas } = splitLowVolume(ideas, ideaVolume)
  const displayedIdeas = showLowVolume ? ideas : highVolumeIdeas

  return (
    <div className="url-extractor">
      <MarketLine label={fetchedMarket?.market_label || activeWorkspace?.market_label} />

      {/* Tasarım: URL (geniş) + Max Sonuç + Min Volume tek satırda */}
      <div className="kwx-grid-url">
        <label className="kwx-field">
          <span className="kwx-field-label">URL</span>
          <input
            className="kwx-input"
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={activeWorkspace?.company_url || 'https://...'}
          />
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">En fazla sonuç</span>
          <input
            className="kwx-input"
            type="number"
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
          />
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">En az aylık arama</span>
          <input
            className="kwx-input"
            type="number"
            value={minVolume}
            onChange={(e) => setMinVolume(Number(e.target.value))}
          />
        </label>
      </div>

      <label className="kwx-check-inline">
        <input
          type="checkbox"
          checked={includeSeed}
          onChange={(e) => setIncludeSeed(e.target.checked)}
        />
        Markanın başlangıç kelimelerini de kullan
      </label>

      <div className="kwx-run-row-btn">
        <button
          type="button"
          className="kwx-run-btn"
          onClick={() => handleExtract(false)}
          disabled={loading || !url.trim() || !activeWorkspace?.id}
        >
          {loading ? (
            <Loader2 size={15} className="spin-icon" />
          ) : (
            <Globe size={15} strokeWidth={2.2} />
          )}
          {loading ? 'Çıkarılıyor...' : "URL'den Çıkar"}
        </button>
      </div>

      {cached && (
        <div className="cache-badge">
          <div className="cache-copy">
            <Info size={14} />
            <span>Önceki sonuç kullanıldı. Güncel sorgu için yeniden çekebilirsiniz.</span>
          </div>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => handleExtract(true)}
            disabled={loading || !url.trim() || !activeWorkspace?.id}
          >
            Google Ads'ten yeniden çek
          </button>
        </div>
      )}

      {(sourceStatus || warnings.length > 0) && (
        <div className="field-warning">
          <AlertCircle size={13} />
          <span>
            {[sourceStatus, ...warnings].filter(Boolean).join(' | ')}
            {warnings.length > 0 &&
              " | Google Ads yine de denendi. Sonuç zayıfsa alt kategori URL'si veya başlangıç kelimesi deneyin."}
          </span>
        </div>
      )}

      {error && (
        <div className="error-banner">
          {error}
          {marketNotSet && activeWorkspace && (
            <>
              {' '}
              <a href={marketWorkspaceLink(activeWorkspace.id)}>Çalışma profiline git</a>
            </>
          )}
        </div>
      )}
      <KeywordImportResult result={importResult} onForceInclude={onForceInclude} />

      {excludeThemes.length > 0 && ideas.length > 0 && (
        <div
          className={themePreview.length > 0 ? 'theme-preview-warning' : 'theme-preview-neutral'}
        >
          <div className="theme-preview-summary">
            <AlertCircle size={14} />
            <span>
              {themePreview.length > 0
                ? `${themePreview.length} kelime yasaklı tema filtresine takılacak.`
                : 'Bu sonuçlarda yasaklı tema filtresine takılan kelime yok.'}
            </span>
            {themePreview.length > 0 && (
              <button
                type="button"
                className="skipped-toggle"
                onClick={() => setShowThemePreview((v) => !v)}
              >
                {showThemePreview ? 'elenenleri gizle' : 'elenenleri göster'}
              </button>
            )}
          </div>
          {showThemePreview && themePreview.length > 0 && (
            <ul className="skipped-list themed-list">
              {themePreview.map((item) => (
                <li key={item.keyword}>
                  <strong>{item.keyword}</strong>
                  <span className="skipped-reason">
                    - Yasaklı temayla eşleşti: "{item.matched}"
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {ideas.length > 0 && (
        <>
          <div className="preview-header">
            <span>
              {selected.size} seçili / {ideas.length} / {total} kelime bulundu
              {cached && ' (önceki sonuç)'}
              {!showLowVolume && lowVolumeIdeas.length > 0 && (
                <> · {lowVolumeIdeas.length} düşük hacimli gizli</>
              )}
            </span>
            <button
              className="btn btn-primary"
              onClick={handleImport}
              disabled={importing || selected.size === 0}
            >
              {importing ? (
                <>
                  <Loader2 size={15} className="spin-icon" /> Aktarılıyor...
                </>
              ) : (
                `Seçilenleri Havuza Ekle (${selected.size})`
              )}
            </button>
          </div>
          <div className="bulk-select-bar">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setSelected(selectTopN(ideas, (i) => i.keyword, ideaVolume, 200))}
            >
              İlk 200'ü seç
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setSelected(selectMinVolume(ideas, (i) => i.keyword, ideaVolume, 100))}
            >
              Hacim ≥100 seç
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() =>
                setSelected((prev) =>
                  prev.size === ideas.length ? new Set() : new Set(ideas.map((i) => i.keyword))
                )
              }
            >
              {selected.size === ideas.length ? 'Seçimi bırak' : 'Tümünü seç'}
            </button>
            {lowVolumeIdeas.length > 0 && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowLowVolume((v) => !v)}
              >
                Düşük hacimlileri (&lt;{LOW_VOLUME_THRESHOLD}) {showLowVolume ? 'gizle' : 'göster'}{' '}
                ({lowVolumeIdeas.length})
              </button>
            )}
          </div>
          <div className="preview-table">
            <table>
              <thead>
                <tr>
                  <th aria-label="Seç"></th>
                  <th>Anahtar Kelime</th>
                  <th>Aylık Arama</th>
                  <th>3 Aylık Trend</th>
                  <th>12 Aylık Trend</th>
                  <th>Rekabet</th>
                  <th>Filtre</th>
                </tr>
              </thead>
              <tbody>
                {displayedIdeas.map((idea, idx) => {
                  const matchedTheme = themePreviewMap.get(idea.keyword)
                  return (
                    <tr key={idx} className={matchedTheme ? 'theme-preview-row' : undefined}>
                      <td>
                        <input
                          type="checkbox"
                          title={`${idea.keyword} seç`}
                          checked={selected.has(idea.keyword)}
                          onChange={() => toggleSelected(idea.keyword)}
                        />
                      </td>
                      <td>{idea.keyword}</td>
                      <td>{idea.monthly_volume}</td>
                      <td>{idea.trend_3m}</td>
                      <td>{idea.trend_12m}</td>
                      <td>{idea.competition}</td>
                      <td>
                        {matchedTheme ? (
                          <span className="theme-pill">Yasaklı: {matchedTheme}</span>
                        ) : (
                          <span className="muted-cell">-</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
