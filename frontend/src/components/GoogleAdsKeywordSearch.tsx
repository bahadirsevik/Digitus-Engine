import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, Loader2, Plus, Search, X } from 'lucide-react'
import { useBrandStore } from '../stores/brandStore'
import {
  googleAdsApi,
  EnrichedKeywordOut,
  KeywordImportResponse,
  SkippedKeywordDetail,
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
  onImport: (keywords: EnrichedKeywordOut[]) => Promise<KeywordImportResponse>
  // Seed kaynak kartından gelen hazır seed listesi (profil önerileri yerine geçer)
  initialSeeds?: string[]
  onForceInclude?: (detail: SkippedKeywordDetail) => Promise<void>
}

const normalizeGoogleAdsLanguageId = (languageId?: string | null) =>
  languageId === '1055' ? '1037' : languageId || '1037'

const ideaVolume = (idea: EnrichedKeywordOut) => idea.avg_monthly_searches || 0

export default function GoogleAdsKeywordSearch({ onImport, initialSeeds, onForceInclude }: Props) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)

  const [customerId, setCustomerId] = useState('')
  const [customers, setCustomers] = useState<string[]>([])
  // Tasarımdaki chip editörü: seed kelimeler liste olarak tutulur
  const [words, setWords] = useState<string[]>([])
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const draftInputRef = useRef<HTMLInputElement>(null)
  const [maxResults, setMaxResults] = useState(100)
  const [minVolume, setMinVolume] = useState(0)
  const [languageId, setLanguageId] = useState(
    normalizeGoogleAdsLanguageId(activeWorkspace?.default_language_id)
  )
  const [geoId, setGeoId] = useState(activeWorkspace?.default_geo_target_id || '2792')
  const [ideas, setIdeas] = useState<EnrichedKeywordOut[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
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
  const suggestedSeeds =
    initialSeeds && initialSeeds.length > 0
      ? initialSeeds
          .map((kw) => kw.trim())
          .filter(Boolean)
          .slice(0, 20)
      : activeWorkspace?.suggested_keywords
          ?.map((kw) => kw.trim())
          .filter(Boolean)
          .slice(0, 10) || []
  const suggestedSeedText = suggestedSeeds.join('\n')

  useEffect(() => {
    const loadCustomers = async () => {
      try {
        const res = await googleAdsApi.listCustomers()
        const ids = (res.data || []).map((item) => item.customer_id)
        setCustomers(ids)
        if (!customerId && ids.length === 1) setCustomerId(ids[0])
      } catch {
        setCustomers([])
      }
    }
    loadCustomers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    setLanguageId(normalizeGoogleAdsLanguageId(activeWorkspace?.default_language_id))
    setGeoId(activeWorkspace?.default_geo_target_id || '2792')
    setWords(suggestedSeedText ? suggestedSeedText.split('\n') : [])
    setAdding(false)
    setDraft('')
    setIdeas([])
    setError(null)
    setShowThemePreview(false)
  }, [
    activeWorkspace?.id,
    activeWorkspace?.default_language_id,
    activeWorkspace?.default_geo_target_id,
    suggestedSeedText,
  ])

  const addDraftWord = () => {
    const v = draft.trim()
    if (v && !words.includes(v)) setWords((w) => [...w, v])
    setDraft('')
    draftInputRef.current?.focus()
  }

  const removeWord = (w: string) => setWords((list) => list.filter((x) => x !== w))

  useEffect(() => {
    if (adding) draftInputRef.current?.focus()
  }, [adding])

  const handleSearch = async () => {
    const seedList = words.map((s) => s.trim()).filter(Boolean)
    if (!customerId || seedList.length === 0) return
    setLoading(true)
    setError(null)
    try {
      const res = await googleAdsApi.enrich({
        customer_id: customerId,
        seeds: seedList,
        max_results: maxResults,
        min_volume: minVolume,
        language_id: languageId,
        geo_target_id: geoId,
      })
      const sorted = sortByVolumeDesc(res.data.keywords || [], ideaVolume)
      setIdeas(sorted)
      setSelected(new Set(sorted.map((idea) => idea.keyword)))
      setShowLowVolume(false)
      setShowThemePreview(false)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setError(e?.response?.data?.detail || 'Google Ads araması başarısız oldu')
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
      setImportResult({ kind: 'success', data })
      setIdeas([])
      setSelected(new Set())
    } catch (err) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setImportResult({
        kind: 'error',
        message: e?.response?.data?.detail || e?.message || 'Aktarım başarısız oldu',
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
    <div className="google-ads-component">
      {/* Tasarım: chip editörü — Önerilen Keywordler + Ekle */}
      <div className="kwx-seed-editor">
        <div className="kwx-seed-head">
          <span className="kwx-field-label">Önerilen Keywordler ({words.length})</span>
          <button
            type="button"
            className={`kwx-add-toggle${adding ? ' active' : ''}`}
            onClick={() => setAdding((a) => !a)}
          >
            <Plus size={14} strokeWidth={2.2} /> Ekle
          </button>
        </div>
        <div className="kwx-chips">
          {words.map((w) => (
            <span key={w} className="kwx-chip">
              {w}
              <button
                type="button"
                className="kwx-chip-x"
                title="Sil"
                onClick={() => removeWord(w)}
              >
                <X size={13} />
              </button>
            </span>
          ))}
          {adding && (
            <span className="kwx-chip-add">
              <input
                ref={draftInputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addDraftWord()
                  }
                  if (e.key === 'Escape') {
                    setDraft('')
                    setAdding(false)
                  }
                }}
                onBlur={() => {
                  if (!draft.trim()) setAdding(false)
                }}
                placeholder="Kelime yaz…"
                size={Math.max(draft.length, 10)}
              />
              <button
                type="button"
                className="kwx-chip-commit"
                title="Ekle"
                onMouseDown={(e) => {
                  e.preventDefault()
                  addDraftWord()
                }}
              >
                <Plus size={13} strokeWidth={2.6} />
              </button>
            </span>
          )}
          {words.length === 0 && !adding && (
            <span className="kwx-chips-empty">Henüz kelime yok — "Ekle" ile başlayın.</span>
          )}
        </div>
      </div>

      {/* Tasarım: 5 kolonlu kompakt parametre satırı */}
      <div className="kwx-grid-5">
        <label className="kwx-field">
          <span className="kwx-field-label">Google Ads Hesabı</span>
          {customers.length > 0 ? (
            <select
              className="kwx-input sm"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
            >
              <option value="">Hesap seçin</option>
              {customers.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="kwx-input sm"
              type="text"
              placeholder="xxx-xxx-xxxx"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
            />
          )}
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">Dil</span>
          <input
            className="kwx-input sm"
            type="text"
            value={languageId}
            onChange={(e) => setLanguageId(e.target.value)}
          />
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">Geo Target</span>
          <input
            className="kwx-input sm"
            type="text"
            value={geoId}
            onChange={(e) => setGeoId(e.target.value)}
          />
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">Min Volume</span>
          <input
            className="kwx-input sm"
            type="number"
            value={minVolume}
            onChange={(e) => setMinVolume(Number(e.target.value))}
          />
        </label>
        <label className="kwx-field">
          <span className="kwx-field-label">Max Sonuç</span>
          <input
            className="kwx-input sm"
            type="number"
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
          />
        </label>
      </div>

      {!customerId && (
        <div className="field-warning">
          <AlertCircle size={13} />
          Müşteri Google Ads Hesabı seçiniz
        </div>
      )}

      <div className="kwx-run-row-btn">
        <button
          type="button"
          className="kwx-run-btn"
          onClick={handleSearch}
          disabled={loading || !customerId || words.length === 0}
        >
          {loading ? (
            <Loader2 size={15} className="spin-icon" />
          ) : (
            <Search size={15} strokeWidth={2.2} />
          )}
          {loading ? 'Aranıyor...' : 'Ara'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <KeywordImportResult result={importResult} onForceInclude={onForceInclude} />

      {ideas.length > 0 && (
        <>
          {excludeThemes.length > 0 && (
            <div
              className={
                themePreview.length > 0 ? 'theme-preview-warning' : 'theme-preview-neutral'
              }
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
          <div className="preview-header">
            <span>
              {selected.size} seçili / {ideas.length} keyword bulundu
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
                  <th>Keyword</th>
                  <th>Aylık Arama</th>
                  <th>Trend 3M</th>
                  <th>Trend 12M</th>
                  <th>Rekabet</th>
                  <th>CPC</th>
                  <th>Filtre</th>
                </tr>
              </thead>
              <tbody>
                {displayedIdeas.map((idea, idx) => (
                  <tr key={idx}>
                    <td>
                      <input
                        type="checkbox"
                        title={`${idea.keyword} seç`}
                        checked={selected.has(idea.keyword)}
                        onChange={() => toggleSelected(idea.keyword)}
                      />
                    </td>
                    <td>{idea.keyword}</td>
                    <td>{idea.avg_monthly_searches}</td>
                    <td>{idea.trend_3m}</td>
                    <td>{idea.trend_12m}</td>
                    <td>{idea.competition_score}</td>
                    <td>
                      {idea.cpc_low}-{idea.cpc_high}
                    </td>
                    <td>
                      {themePreviewMap.get(idea.keyword) ? (
                        <span className="theme-pill">
                          Yasakli: {themePreviewMap.get(idea.keyword)}
                        </span>
                      ) : (
                        <span className="muted-cell">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
