/**
 * Pazar seçimi: "Hedef konum" (aranabilir) + ayrı "Çıktı dili".
 * Konum değişince dil ASLA değişmez (plan_pazar_dil_secimi.md K1).
 * Sayısal ID yalnız `title` ipucunda görünür.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { apiErrorMessage, workspaceApi, type LocationItem } from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import type { MarketValue } from '../services/market'
import './MarketSelect.css'

const SEARCH_DEBOUNCE_MS = 300

interface Props {
  value: MarketValue
  onChange: (next: MarketValue) => void
  disabled?: boolean
  // Doluysa (kilitli çalışma / MARKET_LOCKED 409) alanların altında gösterilir
  lockedMessage?: string
  // Mevcut seçimin etiketi (workspace.market_label'dan) — ID ile aranamadığı için
  knownLocation?: { geo_target_id: string; label: string } | null
}

export default function MarketSelect({
  value,
  onChange,
  disabled = false,
  lockedMessage,
  knownLocation,
}: Props) {
  const uid = useId()
  const marketOptions = useBrandStore((s) => s.marketOptions)
  const optionsError = useBrandStore((s) => s.marketOptionsError)
  const loadMarketOptions = useBrandStore((s) => s.loadMarketOptions)

  const [query, setQuery] = useState('')
  const [country, setCountry] = useState('')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<LocationItem[]>([])
  const [loading, setLoading] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [activeIdx, setActiveIdx] = useState(-1)
  const [picked, setPicked] = useState<{ id: string; label: string } | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    void loadMarketOptions()
  }, [loadMarketOptions])

  useEffect(() => {
    if (!open || disabled) return
    const mySeq = ++seq.current
    setLoading(true)
    setSearchError('')
    const timer = window.setTimeout(async () => {
      try {
        const res = await workspaceApi.searchLocations(query.trim(), country || undefined)
        if (mySeq !== seq.current) return
        setItems(res.data.items || [])
        setActiveIdx(-1)
      } catch (err: unknown) {
        if (mySeq !== seq.current) return
        setItems([])
        setSearchError(apiErrorMessage(err, 'Konum araması başarısız oldu.'))
      } finally {
        if (mySeq === seq.current) setLoading(false)
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [query, country, open, disabled])

  const geo = value.geo_target_id
  const countryLabel = marketOptions?.countries.find((c) => c.geo_target_id === geo)?.label
  let currentLabel: string | null = null
  if (geo) {
    if (picked?.id === geo) currentLabel = picked.label
    else if (knownLocation?.geo_target_id === geo) currentLabel = knownLocation.label
    else currentLabel = countryLabel ?? `Tanımlanamayan mevcut seçim (${geo})`
  }

  const pick = (item: LocationItem) => {
    const id = String(item.geo_target_id)
    setPicked({ id, label: item.label })
    onChange({ geo_target_id: id, language_id: value.language_id })
    setQuery('')
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActiveIdx((i) => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && open && activeIdx >= 0 && items[activeIdx]) {
      e.preventDefault()
      pick(items[activeIdx])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const listId = `${uid}-list`
  const searchId = `${uid}-search`
  const countryId = `${uid}-country`
  const langId = `${uid}-lang`
  const languages = marketOptions?.languages ?? []
  const optionsLoading = !marketOptions && !optionsError

  return (
    <div className="msel">
      {optionsError && (
        <div className="msel-error" role="alert">
          <AlertCircle size={14} />
          <span>{optionsError}</span>
          <button
            type="button"
            className="bpx-btn-ghost"
            onClick={() => void loadMarketOptions(true)}
          >
            Tekrar dene
          </button>
        </div>
      )}
      <div className="msel-grid">
        <div>
          <label className="bpx-label" htmlFor={searchId}>
            Hedef konum
          </label>
          <p className="msel-current" aria-live="polite">
            Seçili konum:{' '}
            {currentLabel ? (
              <strong title={`Google geo hedef ID: ${geo}`}>{currentLabel}</strong>
            ) : (
              <em>seçilmedi</em>
            )}
          </p>
          <div className="msel-search-row">
            <div className="msel-combo">
              <input
                id={searchId}
                className="bpx-input"
                type="text"
                role="combobox"
                autoComplete="off"
                aria-expanded={open}
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={activeIdx >= 0 ? `${uid}-opt-${activeIdx}` : undefined}
                placeholder="Ülke veya şehir ara…"
                value={query}
                disabled={disabled}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setOpen(true)
                }}
                onFocus={() => setOpen(true)}
                onBlur={() => setOpen(false)}
                onKeyDown={onKeyDown}
              />
              {open && !disabled && (
                <ul id={listId} className="msel-list" role="listbox" aria-label="Konum sonuçları">
                  {loading && (
                    <li className="msel-status" role="presentation">
                      <Loader2 size={13} className="spin-icon" /> Aranıyor…
                    </li>
                  )}
                  {!loading && searchError && (
                    <li className="msel-status" role="presentation">
                      {searchError}
                    </li>
                  )}
                  {!loading && !searchError && items.length === 0 && (
                    <li className="msel-status" role="presentation">
                      Sonuç bulunamadı
                    </li>
                  )}
                  {!loading &&
                    !searchError &&
                    items.map((item, i) => (
                      <li
                        key={item.geo_target_id}
                        id={`${uid}-opt-${i}`}
                        role="option"
                        aria-selected={i === activeIdx}
                        className="msel-option"
                        title={`Google geo hedef ID: ${item.geo_target_id}`}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => pick(item)}
                      >
                        {item.label}
                      </li>
                    ))}
                </ul>
              )}
            </div>
            <select
              id={countryId}
              className="bpx-select"
              aria-label="Ülke ile daralt"
              value={country}
              disabled={disabled || !marketOptions}
              onChange={(e) => setCountry(e.target.value)}
            >
              <option value="">Tüm ülkeler</option>
              {(marketOptions?.countries ?? []).map((c) => (
                <option key={c.geo_target_id} value={c.code}>
                  {c.name_tr}
                </option>
              ))}
            </select>
          </div>
          <p className="msel-hint">Şehir adını İngilizce yazın, ör. Moscow, Munich.</p>
        </div>
        <div>
          <label className="bpx-label" htmlFor={langId}>
            Çıktı dili
          </label>
          <select
            id={langId}
            className="bpx-select"
            value={value.language_id ?? ''}
            disabled={disabled || !marketOptions}
            onChange={(e) =>
              onChange({ geo_target_id: value.geo_target_id, language_id: e.target.value })
            }
          >
            {optionsLoading && <option value="">Yükleniyor…</option>}
            {!optionsLoading && !value.language_id && <option value="">Dil seçin</option>}
            {!optionsLoading &&
              value.language_id &&
              !languages.some((l) => String(l.language_id) === value.language_id) && (
                <option value={value.language_id}>
                  Tanımlanamayan mevcut seçim ({value.language_id})
                </option>
              )}
            {languages.map((l) => (
              <option key={l.language_id} value={String(l.language_id)}>
                {l.name_tr}
              </option>
            ))}
          </select>
        </div>
      </div>
      {lockedMessage && (
        <p className="msel-locked" role="note">
          {lockedMessage}
        </p>
      )}
    </div>
  )
}
