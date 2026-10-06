/**
 * Şehir politikası kontrolü — profil düzenleme yüzeylerinde paylaşılan
 * bileşen (plan_v3_lokasyon_filtresi.md §6). İki tüketicisi var:
 *   - frontend/src/pages/brandProfile/ProfileReviewCards.tsx (ilk onay)
 *   - frontend/src/pages/BrandProfile.tsx (onay sonrası düzenleme)
 *
 * Bu bileşen HİÇBİR ŞEY KAYDETMEZ — yalnız üç lokasyon alanını (mode,
 * focus_cities, location_exempt_terms) düzenler ve draft değerlerle
 * salt-okunur bir önizleme (`/policy/location-preview`) çağırır. Kaydetme
 * her iki tüketicinin KENDİ "Onayla" / "Kaydet" akışındadır.
 */
import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Plus, RefreshCw, X } from 'lucide-react'
import { apiErrorMessage, workspaceApi, LocationFilterPreviewResponse } from '../services/api'
import {
  findBrandCityMatches,
  isActiveLocationMode,
  LOCATION_MODE_OPTIONS,
  LocationFilterMode,
  locationReasonLabel,
  normalizeLocationText,
  TURKISH_PROVINCES,
} from '../services/locationPolicy'

const PREVIEW_DEBOUNCE_MS = 500
const CITY_SUGGESTION_LIMIT = 8

export interface LocationPolicyControlProps {
  workspaceId: number
  companyName: string
  brandTerms: string[]
  mode: LocationFilterMode
  focusCities: string[]
  exemptTerms: string[]
  onModeChange: (mode: LocationFilterMode) => void
  onFocusCitiesChange: (cities: string[]) => void
  onExemptTermsChange: (terms: string[]) => void
  disabled?: boolean
}

export default function LocationPolicyControl({
  workspaceId,
  companyName,
  brandTerms,
  mode,
  focusCities,
  exemptTerms,
  onModeChange,
  onFocusCitiesChange,
  onExemptTermsChange,
  disabled = false,
}: LocationPolicyControlProps) {
  const [addingCity, setAddingCity] = useState(false)
  const [newCity, setNewCity] = useState('')
  const [cityError, setCityError] = useState('')
  const [highlightedCity, setHighlightedCity] = useState(0)

  const [addingExempt, setAddingExempt] = useState(false)
  const [newExempt, setNewExempt] = useState('')

  const [preview, setPreview] = useState<LocationFilterPreviewResponse | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')

  const active = isActiveLocationMode(mode)
  const focusOnly = mode === 'focus_only'
  const brandCityMatches = findBrandCityMatches(companyName, brandTerms)

  // Sıralama koruması (QA bulgu 2): setTimeout debounce + manuel "Yenile"
  // butonu aynı anda birden fazla istek başlatabilir. AbortController yerine
  // basit bir istek-nesli sayacı kullanılır — abort desteklenmeyen ortamları
  // da kapsar. Yalnız EN SON başlatılan istek state yazabilir; daha ÖNCE
  // başlayıp SONRA çözülen bir istek (yavaş ağ) her iki yolda da (başarı VE
  // hata) yok sayılır — kullanıcının değiştirdiği bir politikanın üstüne
  // eski sayılar yazılmasın diye.
  const requestIdRef = useRef(0)

  const runPreview = async () => {
    const requestId = ++requestIdRef.current
    setPreviewLoading(true)
    setPreviewError('')
    try {
      const res = await workspaceApi.previewLocationFilter(workspaceId, {
        location_filter_mode: mode,
        focus_cities: focusCities,
        location_exempt_terms: exemptTerms,
        keyword_selection_mode: 'all',
      })
      if (requestIdRef.current !== requestId) return
      setPreview(res.data)
    } catch (err: unknown) {
      if (requestIdRef.current !== requestId) return
      setPreview(null)
      setPreviewError(apiErrorMessage(err, 'Önizleme alınamadı'))
    } finally {
      if (requestIdRef.current === requestId) setPreviewLoading(false)
    }
  }

  // Ayarlar değiştikçe önizleme kendiliğinden tazelenir (yüksek kayıp sessiz
  // kalmasın — plan §6) — debounce'lu; manuel "Etkisini önizle" butonu da
  // aynı fonksiyonu tetikler (hata sonrası yeniden dene).
  //
  // QA bulgu — 500ms delik: istek-nesli sayacı yalnız `runPreview()` İÇİNDE
  // artırılırsa, girdiler değişip B zamanlanır ama HENÜZ başlamazken (hâlâ
  // debounce penceresinde) A çözülürse, A'nın id'si hâlâ "güncel" sayılır ve
  // A'nın (artık eski) sonucu ekrana yazılır. Düzeltme: sayaç, bir sonraki
  // isteğin BAŞLAMASINI değil, girdilerin DEĞİŞMESİNİ bekler — bu effect'in
  // en başında (setTimeout zamanlanmadan ÖNCE) artırılır ki o anda ağda
  // asılı olan HERHANGİ bir istek (hâlâ debounce'da bekleyen B dâhil değil,
  // zaten dispatch edilmiş A) anında bayatlasın. `runPreview()` kendi
  // artışını KORUR — zararsızdır, yalnız her döngü iki id tüketir.
  const debounceRef = useRef<number | null>(null)
  useEffect(() => {
    requestIdRef.current += 1
    if (debounceRef.current) window.clearTimeout(debounceRef.current)
    debounceRef.current = window.setTimeout(() => {
      void runPreview()
    }, PREVIEW_DEBOUNCE_MS)
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, mode, focusCities.join('|'), exemptTerms.join('|')])

  // Tarayıcının yerleşik <datalist>'i Türkçe büyük/küçük harfi eşleştiremiyor
  // ("istanbul" yazınca "İstanbul" önerilmiyordu) — öneriler bu yüzden
  // normalizeLocationText ile burada süzülür; önce baştan eşleşenler gelir.
  const citySuggestions = (() => {
    const query = normalizeLocationText(newCity)
    if (!query) return []
    const available = TURKISH_PROVINCES.filter((p) => !focusCities.includes(p))
    const prefix = available.filter((p) => normalizeLocationText(p).startsWith(query))
    const contains = available.filter(
      (p) => !prefix.includes(p) && normalizeLocationText(p).includes(query)
    )
    return [...prefix, ...contains].slice(0, CITY_SUGGESTION_LIMIT)
  })()

  const closeCityInput = () => {
    setNewCity('')
    setCityError('')
    setHighlightedCity(0)
    setAddingCity(false)
  }

  const addCity = (canonical: string) => {
    if (!focusCities.includes(canonical)) {
      onFocusCitiesChange([...focusCities, canonical])
    }
    closeCityInput()
  }

  const commitCity = () => {
    const raw = newCity.trim()
    if (!raw) {
      setAddingCity(false)
      setCityError('')
      return
    }
    const canonical = TURKISH_PROVINCES.find(
      (p) => normalizeLocationText(p) === normalizeLocationText(raw)
    )
    if (!canonical) {
      setCityError(
        `"${raw}" tanınan bir il adı değil — yalnız il seçebilirsiniz; ilçe/mahalle desteklenmez.`
      )
      return
    }
    addCity(canonical)
  }

  const commitExempt = () => {
    const raw = newExempt.trim()
    if (!raw) {
      setAddingExempt(false)
      return
    }
    const key = normalizeLocationText(raw)
    if (!exemptTerms.some((t) => normalizeLocationText(t) === key)) {
      onExemptTermsChange([...exemptTerms, raw])
    }
    setNewExempt('')
    setAddingExempt(false)
  }

  const brandCityAffected =
    active && brandCityMatches.length > 0
      ? preview?.excluded_by_city.filter((row) => brandCityMatches.includes(row.city)) || []
      : []
  const brandCityAffectedCount = brandCityAffected.reduce((sum, row) => sum + row.count, 0)
  const brandCityExamples =
    preview?.sample_excluded
      .filter((s) => s.matched_city && brandCityMatches.includes(s.matched_city))
      .slice(0, 5) || []

  return (
    <div className="bpx-location-policy">
      <label className="bpx-label">Şehir politikası</label>
      <div className="bpx-radio-row" role="radiogroup" aria-label="Şehir politikası">
        {LOCATION_MODE_OPTIONS.map((opt) => (
          <label key={opt.value} className="bpx-radio-option">
            <input
              type="radio"
              name={`location-filter-mode-${workspaceId}`}
              value={opt.value}
              checked={mode === opt.value}
              disabled={disabled}
              onChange={() => onModeChange(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>
      <p className="bpx-section-hint">
        <b>Şehir filtresi yok:</b> şehir içeren keyword'ler normal değerlendirilir.{' '}
        <b>Tüm şehirli keyword'leri hariç tut:</b> desteklenen bir il adı geçen her keyword elenir.{' '}
        <b>Yalnız seçtiğim şehirler kalsın:</b> yalnız aşağıda seçtiğiniz odak şehirleri (veya
        şehirsiz keyword'ler) kalır.
      </p>

      <div className="bpx-focus-cities">
        <label className="bpx-label">Odak şehirleri</label>
        <p className="bpx-section-hint">
          Yalnız il seçebilirsiniz; ilçe/mahalle desteklenmez.
          {!focusOnly && focusCities.length > 0 && (
            <>
              {' '}
              Kayıtlı şehirler şu an <b>etkisiz</b> ("Yalnız seçtiğim şehirler kalsın" modunda
              değilsiniz) — silinmediler, mod değiştiğinde tekrar geçerli olurlar.
            </>
          )}
          {!focusOnly && focusCities.length === 0 && (
            <> Yalnız "Yalnız seçtiğim şehirler kalsın" modunda etkilidir.</>
          )}
        </p>
        <div className="bpx-chiprow">
          {focusCities.map((city) => (
            <span key={city} className={`bpx-chip has-x${focusOnly ? '' : ' is-inactive'}`}>
              {city}
              <button
                type="button"
                className="bpx-chip-x"
                aria-label={`${city} sil`}
                title="Sil"
                onClick={() => onFocusCitiesChange(focusCities.filter((c) => c !== city))}
                disabled={disabled || !focusOnly}
              >
                <X size={12} strokeWidth={2} />
              </button>
            </span>
          ))}
          {!addingCity ? (
            <button
              type="button"
              className="bpx-btn-raised"
              onClick={() => setAddingCity(true)}
              disabled={disabled || !focusOnly}
              title={
                focusOnly
                  ? undefined
                  : "Yalnız 'Yalnız seçtiğim şehirler kalsın' modunda eklenebilir"
              }
            >
              <Plus size={14} /> Şehir ekle
            </button>
          ) : (
            <span className="bpx-chip-add bpx-city-combobox">
              <input
                autoFocus
                role="combobox"
                aria-expanded={citySuggestions.length > 0}
                aria-controls="location-policy-city-suggestions"
                aria-autocomplete="list"
                value={newCity}
                placeholder="İl adı…"
                onChange={(e) => {
                  setNewCity(e.target.value)
                  setCityError('')
                  setHighlightedCity(0)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown' && citySuggestions.length > 0) {
                    e.preventDefault()
                    setHighlightedCity((i) => (i + 1) % citySuggestions.length)
                  } else if (e.key === 'ArrowUp' && citySuggestions.length > 0) {
                    e.preventDefault()
                    setHighlightedCity(
                      (i) => (i - 1 + citySuggestions.length) % citySuggestions.length
                    )
                  } else if (e.key === 'Enter') {
                    e.preventDefault()
                    const picked = citySuggestions[highlightedCity]
                    if (picked) addCity(picked)
                    else commitCity()
                  } else if (e.key === 'Escape') {
                    closeCityInput()
                  }
                }}
                onBlur={commitCity}
              />
              {citySuggestions.length > 0 && (
                <ul
                  id="location-policy-city-suggestions"
                  role="listbox"
                  className="bpx-city-suggestions"
                >
                  {citySuggestions.map((city, idx) => (
                    <li
                      key={city}
                      role="option"
                      aria-selected={idx === highlightedCity}
                      className={idx === highlightedCity ? 'is-highlighted' : undefined}
                      // mousedown: input'un blur'u (commitCity) tıklamadan önce çalışmasın
                      onMouseDown={(e) => {
                        e.preventDefault()
                        addCity(city)
                      }}
                      onMouseEnter={() => setHighlightedCity(idx)}
                    >
                      {city}
                    </li>
                  ))}
                </ul>
              )}
            </span>
          )}
        </div>
        {cityError && (
          <p className="bpx-section-hint warning-hint" style={{ marginTop: 6 }}>
            <AlertTriangle size={12} /> {cityError}
          </p>
        )}
      </div>

      {active && (
        <div className="bpx-exempt-terms">
          <label className="bpx-label">Lokasyon filtresi muafiyetleri</label>
          <p className="bpx-section-hint">
            Şehir adı içerse bile korunacak terimler — örn. <i>gaziantep fıstığı</i>,{' '}
            <i>batman filmi</i>, <i>uşak arayanlar</i>. Bu ardışık kelime dizisini içeren
            keyword'ler lokasyon nedeniyle elenmez.
          </p>
          <div className="bpx-chiprow">
            {exemptTerms.map((term) => (
              <span key={term} className="bpx-chip has-x">
                {term}
                <button
                  type="button"
                  className="bpx-chip-x"
                  aria-label={`${term} sil`}
                  title="Sil"
                  onClick={() => onExemptTermsChange(exemptTerms.filter((t) => t !== term))}
                  disabled={disabled}
                >
                  <X size={12} strokeWidth={2} />
                </button>
              </span>
            ))}
            {!addingExempt ? (
              <button
                type="button"
                className="bpx-btn-raised"
                onClick={() => setAddingExempt(true)}
                disabled={disabled}
              >
                <Plus size={14} /> Muafiyet ekle
              </button>
            ) : (
              <span className="bpx-chip-add">
                <input
                  autoFocus
                  value={newExempt}
                  placeholder="örn. gaziantep fıstığı"
                  onChange={(e) => setNewExempt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitExempt()
                    if (e.key === 'Escape') {
                      setNewExempt('')
                      setAddingExempt(false)
                    }
                  }}
                  onBlur={commitExempt}
                />
              </span>
            )}
          </div>
        </div>
      )}

      {brandCityMatches.length > 0 && active && (
        <div className="bpx-location-warning" role="alert" data-testid="brand-city-warning">
          <AlertTriangle size={14} />
          <div>
            <p>
              Marka adınız veya marka teriminiz <b>{brandCityMatches.join(', ')}</b> il adını
              içeriyor. Bu şehir filtresi{' '}
              {brandCityAffectedCount > 0 ? (
                <>
                  şu anki kelime havuzunda <b>{brandCityAffectedCount}</b> keyword'ü etkileyebilir
                </>
              ) : (
                "markanızla ilgili keyword'leri de etkileyebilir"
              )}
              . Sistem bunu OTOMATİK muaf tutmaz — gerekirse tam marka ifadesini yukarıdaki
              muafiyetler alanına siz eklemelisiniz.
            </p>
            {brandCityExamples.length > 0 && (
              <p className="bpx-section-hint">
                Örnek: {brandCityExamples.map((s) => s.keyword).join(', ')}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="bpx-location-preview">
        <div className="bpx-location-preview-head">
          <span className="bpx-label">Etkisini önizle</span>
          <button
            type="button"
            className="bpx-btn-raised"
            onClick={() => void runPreview()}
            disabled={previewLoading}
          >
            {previewLoading ? <Loader2 size={13} className="spin-icon" /> : <RefreshCw size={13} />}
            Yenile
          </button>
        </div>
        {previewError && (
          <p className="bpx-section-hint warning-hint">
            <AlertTriangle size={12} /> {previewError}
          </p>
        )}
        {!previewError && preview && !preview.has_keywords && (
          <p className="bpx-section-hint">Önizlenecek keyword yok (havuz henüz boş).</p>
        )}
        {!previewError && preview && preview.has_keywords && (
          <div className="bpx-location-summary" data-testid="location-preview-summary">
            <span>
              Değerlendirilen: <b>{preview.evaluated_count}</b>
            </span>
            <span>
              Kalacak: <b>{preview.kept_count}</b>
            </span>
            <span className={preview.excluded_count > 0 ? 'is-loss' : ''}>
              Elenecek: <b>{preview.excluded_count}</b>
            </span>
            {preview.excluded_by_city.length > 0 && (
              <ul className="bpx-location-city-list">
                {preview.excluded_by_city.slice(0, 10).map((row) => (
                  <li key={`${row.city}-${row.reason}`}>
                    {row.city} ({locationReasonLabel(row.reason)}): {row.count}
                  </li>
                ))}
              </ul>
            )}
            {preview.sample_excluded.length > 0 && (
              <p className="bpx-section-hint">
                Örnek elenen:{' '}
                {preview.sample_excluded
                  .slice(0, 5)
                  .map((s) => s.keyword)
                  .join(', ')}
              </p>
            )}
            {preview.sample_exempted.length > 0 && (
              <p className="bpx-section-hint">
                Muafiyetle korunan:{' '}
                {preview.sample_exempted
                  .slice(0, 5)
                  .map((s) => s.keyword)
                  .join(', ')}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
