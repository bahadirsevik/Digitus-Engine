import { useEffect, useState } from 'react'
import { AlertCircle, Globe, Info, Loader2 } from 'lucide-react'
import { useMemo } from 'react'
import { useBrandStore } from '../stores/brandStore'
import {
  googleAdsUrlSeedApi,
  KeywordImportResponse,
  SkippedKeywordDetail,
  UrlSeedIdea,
} from '../services/api'
import KeywordImportResult from './KeywordImportResult'
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
  onImport: (keywords: UrlSeedIdea[]) => Promise<KeywordImportResponse | void>
  // Rakip URL'leri kaynak kartından hazır URL ile açılır
  initialUrl?: string
  onForceInclude?: (detail: SkippedKeywordDetail) => Promise<void>
}

const ideaVolume = (idea: UrlSeedIdea) => idea.monthly_volume || 0

const normalizeGoogleAdsLanguageId = (languageId?: string | null) =>
  languageId === '1055' ? '1037' : languageId || '1037'

const errorMessageFrom = (err: unknown, fallback: string): string => {
  const e = err as {
    message?: string
    detail?: unknown
    response?: { data?: { detail?: string } }
  }
  return (
    e?.message ||
    (typeof e?.detail === 'string' ? e.detail : undefined) ||
    e?.response?.data?.detail ||
    fallback
  )
}

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
    setShowThemePreview(false)
  }, [activeWorkspace?.id, activeWorkspace?.company_url, initialUrl])

  const handleExtract = async (forceRefresh = false) => {
    if (!url.trim() || !activeWorkspace?.id) return
    const normalizedUrl = /^https?:\/\//i.test(url.trim()) ? url.trim() : `https://${url.trim()}`
    setLoading(true)
    setError(null)
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
        language_id: normalizeGoogleAdsLanguageId(activeWorkspace.default_language_id),
        geo_target_id: activeWorkspace.default_geo_target_id || '2792',
        refresh: forceRefresh,
      })
      const data = res.data
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
              status.reachable ? 'URL kontrolu basarili' : 'URL kontrolu uyari verdi',
              status.http_status ? `HTTP ${status.http_status}` : null,
              status.warning || null,
            ]
              .filter(Boolean)
              .join(' | ')
          : null
      )
      if ((data.ideas || []).length === 0) {
        setError(
          "Google Ads bu URL'den keyword fikri uretmedi. Alt kategori URL'si deneyin veya seed keyword ile arama yapin."
        )
      }
    } catch (err: unknown) {
      setIdeas([])
      setTotal(0)
      setCached(false)
      setError(
        errorMessageFrom(
          err,
          "URL'den keyword cikarma basarisiz oldu. Alt kategori URL'si deneyin veya seed keyword ile Google Ads aramasi yapin."
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
      const data = await onImport(chosen)
      if (!data) {
        throw new Error('Aktarim sonucu alinamadi')
      }
      setImportResult({ kind: 'success', data })
      setIdeas([])
      setSelected(new Set())
    } catch (err: unknown) {
      setImportResult({
        kind: 'error',
        message: errorMessageFrom(err, 'Aktarim basarisiz oldu'),
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
          <span className="kwx-field-label">Max Sonuç</span>
          <input
            className="kwx-input"
            type="number"
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
          />
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">Min Volume</span>
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
        Marka seed kelimeleri de kullan
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
            <span>Onceki sonuc kullanildi. Guncel sorgu icin yeniden cekebilirsiniz.</span>
          </div>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => handleExtract(true)}
            disabled={loading || !url.trim() || !activeWorkspace?.id}
          >
            Google Ads'ten yeniden cek
          </button>
        </div>
      )}

      {(sourceStatus || warnings.length > 0) && (
        <div className="field-warning">
          <AlertCircle size={13} />
          <span>
            {[sourceStatus, ...warnings].filter(Boolean).join(' | ')}
            {warnings.length > 0 &&
              " | Google Ads yine de denendi. Sonuc zayifsa alt kategori URL'si veya seed keyword deneyin."}
          </span>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
      <KeywordImportResult result={importResult} onForceInclude={onForceInclude} />

      {excludeThemes.length > 0 && ideas.length > 0 && (
        <div
          className={themePreview.length > 0 ? 'theme-preview-warning' : 'theme-preview-neutral'}
        >
          <div className="theme-preview-summary">
            <AlertCircle size={14} />
            <span>
              {themePreview.length > 0
                ? `${themePreview.length} keyword yasakli tema filtresine takilacak.`
                : 'Bu sonuclarda yasakli tema filtresine takilan keyword yok.'}
            </span>
            {themePreview.length > 0 && (
              <button
                type="button"
                className="skipped-toggle"
                onClick={() => setShowThemePreview((v) => !v)}
              >
                {showThemePreview ? 'elenenleri gizle' : 'elenenleri goster'}
              </button>
            )}
          </div>
          {showThemePreview && themePreview.length > 0 && (
            <ul className="skipped-list themed-list">
              {themePreview.map((item) => (
                <li key={item.keyword}>
                  <strong>{item.keyword}</strong>
                  <span className="skipped-reason">
                    - Yasakli temayla eslesti: "{item.matched}"
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
              {selected.size} secili / {ideas.length} / {total} keyword bulundu
              {cached && ' (onceki sonuc)'}
              {!showLowVolume && lowVolumeIdeas.length > 0 && (
                <> · {lowVolumeIdeas.length} dusuk hacimli gizli</>
              )}
            </span>
            <button
              className="btn btn-primary"
              onClick={handleImport}
              disabled={importing || selected.size === 0}
            >
              {importing ? (
                <>
                  <Loader2 size={15} className="spin-icon" /> Aktariliyor...
                </>
              ) : (
                `Secilenleri Havuza Ekle (${selected.size})`
              )}
            </button>
          </div>
          <div className="bulk-select-bar">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setSelected(selectTopN(ideas, (i) => i.keyword, ideaVolume, 200))}
            >
              Ilk 200'u sec
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setSelected(selectMinVolume(ideas, (i) => i.keyword, ideaVolume, 100))}
            >
              Hacim ≥100 sec
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
              {selected.size === ideas.length ? 'Secimi birak' : 'Tumunu sec'}
            </button>
            {lowVolumeIdeas.length > 0 && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowLowVolume((v) => !v)}
              >
                Dusuk hacimlileri (&lt;{LOW_VOLUME_THRESHOLD}) {showLowVolume ? 'gizle' : 'goster'}{' '}
                ({lowVolumeIdeas.length})
              </button>
            )}
          </div>
          <div className="preview-table">
            <table>
              <thead>
                <tr>
                  <th aria-label="Sec"></th>
                  <th>Keyword</th>
                  <th>Aylik Arama</th>
                  <th>Trend 3M</th>
                  <th>Trend 12M</th>
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
                          title={`${idea.keyword} sec`}
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
                          <span className="theme-pill">Yasakli: {matchedTheme}</span>
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
