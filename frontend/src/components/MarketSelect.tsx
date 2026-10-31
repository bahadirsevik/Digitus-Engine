/**
 * Pazar seçimi: "Hedef konum" (aranabilir) + ayrı "Çıktı dili".
 * Konum değişince dil ASLA değişmez (plan_pazar_dil_secimi.md K1).
 * Sayısal ID yalnız `title` ipucunda görünür.
 */
import { useEffect, useId, useState } from 'react'
import { AlertCircle } from 'lucide-react'
import type { LocationItem } from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import type { MarketValue } from '../services/market'
import LocationSearch from './LocationSearch'
import './MarketSelect.css'

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

  const [picked, setPicked] = useState<{ id: string; label: string } | null>(null)

  useEffect(() => {
    void loadMarketOptions()
  }, [loadMarketOptions])

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
  }

  const searchId = `${uid}-search`
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
          <LocationSearch inputId={searchId} onPick={pick} disabled={disabled} />
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
