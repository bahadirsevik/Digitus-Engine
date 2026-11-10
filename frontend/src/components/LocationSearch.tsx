/**
 * Aranabilir konum seçici (ülke/şehir) + ülke daraltma. Seçimi `onPick` ile bildirir;
 * seçili değeri tutmaz (MarketSelect tekli, MarketCopyDialog çoklu kullanır).
 */
import { useEffect, useId, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { apiErrorMessage, workspaceApi, type LocationItem } from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import './MarketSelect.css'

const SEARCH_DEBOUNCE_MS = 300

interface Props {
  // Konum etiketini bağlayan <label htmlFor> için
  inputId: string
  onPick: (item: LocationItem) => void
  disabled?: boolean
}

export default function LocationSearch({ inputId, onPick, disabled = false }: Props) {
  const uid = useId()
  const marketOptions = useBrandStore((s) => s.marketOptions)
  const loadMarketOptions = useBrandStore((s) => s.loadMarketOptions)

  const [query, setQuery] = useState('')
  const [country, setCountry] = useState('')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<LocationItem[]>([])
  const [loading, setLoading] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [activeIdx, setActiveIdx] = useState(-1)
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

  const pick = (item: LocationItem) => {
    onPick(item)
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

  return (
    <>
      <div className="msel-search-row">
        <div className="msel-combo">
          <input
            id={inputId}
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
    </>
  )
}
