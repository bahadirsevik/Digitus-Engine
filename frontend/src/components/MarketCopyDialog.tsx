/**
 * "Pazar ekle": onaylı markadan tek ya da toplu pazar kopyası (plan_pazar_dil_secimi.md Faz 3).
 * Her konum ayrı çalışma olur; kelimeler ve analizler kopyalanmaz.
 * Kısmi sonuç hata değildir: "3 oluşturuldu, 1 atlandı". Yalnız tümü mevcutsa 409 bilgi durumu.
 * Görünüm sınıfları (bpx-*) BrandProfile.css'ten gelir; yalnız o sayfada kullanılır.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import {
  apiErrorMessage,
  marketCopyAllExistFromError,
  workspaceApi,
  type LocationItem,
  type MarketCopySkipped,
  type WorkspaceResponse,
} from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import LocationSearch from './LocationSearch'
import './MarketSelect.css'
import './MarketCopyDialog.css'

export const MAX_MARKET_COPIES = 30

interface Row {
  // Aynı konum birden çok satır olabilir; satır kimliği yerel
  key: number
  geo_target_id: string
  label: string
  // null = genel "Çıktı dili"yi kullan
  language_id: string | null
}

type Outcome =
  | { kind: 'created'; created: WorkspaceResponse[]; skipped: MarketCopySkipped[] }
  | { kind: 'all_exist'; skipped: MarketCopySkipped[] }

interface Props {
  source: { id: number; name: string }
  onClose: () => void
  // Kopyalar oluşunca (liste yenilensin diye) bir kez çağrılır
  onCreated: (created: WorkspaceResponse[]) => void
  onOpenFirst: (first: WorkspaceResponse) => void
}

export default function MarketCopyDialog({ source, onClose, onCreated, onOpenFirst }: Props) {
  const uid = useId()
  const marketOptions = useBrandStore((s) => s.marketOptions)
  const optionsError = useBrandStore((s) => s.marketOptionsError)
  const languages = marketOptions?.languages ?? []

  const [rows, setRows] = useState<Row[]>([])
  const nextKey = useRef(0)
  const [globalLang, setGlobalLang] = useState('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  const langName = (id: string) =>
    languages.find((l) => String(l.language_id) === id)?.name_tr ?? id
  const effectiveLang = (r: Row) => r.language_id ?? globalLang
  const full = rows.length >= MAX_MARKET_COPIES
  // (konum, etkin dil) çifti tekrarı: gönderimi engeller, satırlar vurgulanır
  const pairKey = (r: Row) => `${r.geo_target_id}|${effectiveLang(r)}`
  const pairCounts = new Map<string, number>()
  rows.forEach((r) => {
    if (effectiveLang(r)) pairCounts.set(pairKey(r), (pairCounts.get(pairKey(r)) ?? 0) + 1)
  })
  const isDup = (r: Row) => !!effectiveLang(r) && (pairCounts.get(pairKey(r)) ?? 0) > 1
  const dupRows = rows.filter(isDup)
  const dupMessage = dupRows.length
    ? `Aynı pazar iki kez listede: ${[...new Set(dupRows.map((r) => `${r.label} · ${langName(effectiveLang(r))}`))].join(', ')}`
    : ''
  const ready = rows.length > 0 && rows.every((r) => !!effectiveLang(r)) && !dupMessage

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, submitting])

  const addLocation = (item: LocationItem) => {
    const id = String(item.geo_target_id)
    if (full) return
    setNote(
      rows.some((r) => r.geo_target_id === id)
        ? 'Aynı konum ikinci kez eklendi — satır dillerini farklı seçin.'
        : ''
    )
    setRows((prev) => [
      ...prev,
      { key: nextKey.current++, geo_target_id: id, label: item.label, language_id: null },
    ])
  }

  const removeRow = (key: number) => {
    setNote('')
    setRows((prev) => prev.filter((r) => r.key !== key))
  }

  const setRowLang = (key: number, language_id: string) =>
    setRows((prev) =>
      prev.map((r) => (r.key === key ? { ...r, language_id: language_id || null } : r))
    )

  const summary = rows
    .map((r) => (effectiveLang(r) ? `${r.label} · ${langName(effectiveLang(r))}` : r.label))
    .join(', ')

  const submit = async () => {
    setSubmitting(true)
    setError('')
    try {
      const res = await workspaceApi.addMarkets(
        source.id,
        rows.map((r) => ({ geo_target_id: r.geo_target_id, language_id: effectiveLang(r) }))
      )
      const { created, skipped } = res.data
      setOutcome({ kind: 'created', created, skipped })
      if (created.length > 0) onCreated(created)
    } catch (err: unknown) {
      const allExist = marketCopyAllExistFromError(err)
      if (allExist) setOutcome({ kind: 'all_exist', skipped: allExist })
      else setError(apiErrorMessage(err, 'Pazar çalışmaları oluşturulamadı.'))
    } finally {
      setSubmitting(false)
    }
  }

  const titleId = `${uid}-title`
  const searchId = `${uid}-search`
  const langId = `${uid}-lang`

  const skippedList = (skipped: MarketCopySkipped[]) =>
    skipped.length > 0 && (
      <ul className="mcd-skipped" aria-label="Atlananlar">
        {skipped.map((s) => (
          <li key={`${s.geo_target_id}-${s.language_id}`}>
            <strong>{s.market_label}</strong> — {s.reason}
          </li>
        ))}
      </ul>
    )

  return (
    <div className="bpx-overlay" onClick={() => !submitting && onClose()}>
      <div
        className="bpx-modal mcd"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="bpx-modal-header">
          <div className="bpx-modal-header-main">
            <div id={titleId} className="bpx-modal-title">
              Pazar ekle
            </div>
            <p className="mcd-source">{source.name}</p>
          </div>
          <button
            type="button"
            className="bpx-close"
            onClick={onClose}
            disabled={submitting}
            aria-label="Kapat"
          >
            <X size={17} />
          </button>
        </div>

        {outcome ? (
          <>
            <div className="bpx-modal-body" role="status">
              {outcome.kind === 'created' ? (
                <>
                  <p className="mcd-result">
                    {outcome.created.length} oluşturuldu, {outcome.skipped.length} atlandı
                  </p>
                  {outcome.created.length > 0 && (
                    <ul className="mcd-created" aria-label="Oluşturulanlar">
                      {outcome.created.map((w) => (
                        <li key={w.id}>{w.name}</li>
                      ))}
                    </ul>
                  )}
                  {skippedList(outcome.skipped)}
                </>
              ) : (
                <>
                  <p className="mcd-result">
                    Seçilen pazarların hepsi zaten var; yeni çalışma açılmadı.
                  </p>
                  {skippedList(outcome.skipped)}
                </>
              )}
            </div>
            <div className="bpx-modal-footer is-end">
              <button type="button" className="bpx-btn-raised" onClick={onClose}>
                Kapat
              </button>
              {outcome.kind === 'created' && outcome.created.length > 0 && (
                <button
                  type="button"
                  className="bpx-btn-primary"
                  onClick={() => onOpenFirst(outcome.created[0])}
                >
                  İlk çalışmaya geç
                </button>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="bpx-modal-body">
              {optionsError && (
                <div className="error-banner" role="alert">
                  {optionsError}
                </div>
              )}
              <div className="mcd-grid">
                <div>
                  <label className="bpx-label" htmlFor={searchId}>
                    Hedef konumlar
                  </label>
                  <LocationSearch
                    inputId={searchId}
                    onPick={addLocation}
                    disabled={submitting || full}
                  />
                  {full && (
                    <p className="msel-hint">En fazla {MAX_MARKET_COPIES} pazar eklenebilir.</p>
                  )}
                  {note && (
                    <p className="msel-hint" role="status">
                      {note}
                    </p>
                  )}
                </div>
                <div>
                  <label className="bpx-label" htmlFor={langId}>
                    Çıktı dili
                  </label>
                  <select
                    id={langId}
                    className="bpx-select"
                    value={globalLang}
                    disabled={submitting || !marketOptions}
                    onChange={(e) => setGlobalLang(e.target.value)}
                  >
                    <option value="">{marketOptions ? 'Dil seçin' : 'Yükleniyor…'}</option>
                    {languages.map((l) => (
                      <option key={l.language_id} value={String(l.language_id)}>
                        {l.name_tr}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {rows.length === 0 ? (
                <p className="mcd-empty">Henüz konum eklenmedi. Ülke veya şehir arayıp seçin.</p>
              ) : (
                <ul className="mcd-rows" aria-label="Seçilen pazarlar">
                  {rows.map((r) => (
                    <li key={r.key} className={`mcd-row${isDup(r) ? ' is-dup' : ''}`}>
                      <span className="mcd-chip" title={`Google geo hedef ID: ${r.geo_target_id}`}>
                        {r.label}
                      </span>
                      <select
                        className="bpx-select"
                        aria-label={`${r.label} için dil`}
                        value={r.language_id ?? ''}
                        disabled={submitting}
                        onChange={(e) => setRowLang(r.key, e.target.value)}
                      >
                        <option value="">
                          {globalLang ? `Genel dil (${langName(globalLang)})` : 'Genel dil'}
                        </option>
                        {languages.map((l) => (
                          <option key={l.language_id} value={String(l.language_id)}>
                            {l.name_tr}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="bpx-icon-btn"
                        aria-label={`${r.label} kaldır`}
                        title="Kaldır"
                        disabled={submitting}
                        onClick={() => removeRow(r.key)}
                      >
                        <X size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {rows.length > 0 && (
                <p className="mcd-summary" aria-live="polite">
                  <strong>{rows.length} çalışma oluşturulacak:</strong> {summary}
                </p>
              )}
              {dupMessage && (
                <div className="error-banner" role="alert">
                  {dupMessage}
                </div>
              )}
              <p className="msel-hint">
                Her pazar ayrı çalışma olur; kelimeler ve analizler kopyalanmaz.
              </p>
              {error && (
                <div className="error-banner" role="alert">
                  {error}
                </div>
              )}
            </div>
            <div className="bpx-modal-footer is-end">
              <button
                type="button"
                className="bpx-btn-raised"
                onClick={onClose}
                disabled={submitting}
              >
                Vazgeç
              </button>
              <button
                type="button"
                className="bpx-btn-primary"
                onClick={() => void submit()}
                disabled={!ready || submitting}
              >
                {submitting && <Loader2 size={14} className="spin-icon" />}
                {rows.length > 0 ? `${rows.length} çalışma oluştur` : 'Çalışma oluştur'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
