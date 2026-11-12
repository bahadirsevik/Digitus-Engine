/**
 * "Revize Et" penceresi (ADS + SEO + sosyal ortak): kullanıcı neyin değişeceğini yazar ve
 * hangi bölümlerin değişeceğini seçer. Seçilmeyen bölümler sunucuda AYNEN korunur
 * (app/generators/ads/revision.py, app/generators/seo_geo/revision.py,
 * app/core/social/content_revision.py).
 * Gönderim hatasında yazılan talep silinmez.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Loader2, X } from 'lucide-react'

export const FEEDBACK_MIN = 5
export const FEEDBACK_MAX = 500

export interface ScopeOption<S extends string> {
  value: S
  label: string
}

interface Props<S extends string> {
  title: string
  subjectName: string
  scopeOptions: ScopeOption<S>[]
  defaultScope: S[]
  // Sonucun nasıl kullanıma gireceği (ADS: taslak + Aktif Yap; SEO: önizleme + Kabul Et)
  outcomeNote: string
  placeholder: string
  // Hata metni döndürürse pencere açık kalır ve hatayı gösterir; null → başarı, kapanır
  onSubmit: (feedback: string, scope: S[]) => Promise<string | null>
  onClose: () => void
}

export default function ReviseDialog<S extends string>({
  title,
  subjectName,
  scopeOptions,
  defaultScope,
  outcomeNote,
  placeholder,
  onSubmit,
  onClose,
}: Props<S>) {
  const uid = useId()
  const [feedback, setFeedback] = useState('')
  const [scope, setScope] = useState<S[]>(defaultScope)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const dialogRef = useRef<HTMLDivElement>(null)
  // Açan öğe İLK render'da yakalanır (autoFocus effect'lerden önce odağı textarea'ya taşır)
  const openerRef = useRef(document.activeElement as HTMLElement | null)

  // Kapanınca odak pencereyi açan öğeye ("Revize Et" butonu) döner
  useEffect(() => {
    const opener = openerRef.current
    const node = dialogRef.current
    return () => {
      // StrictMode'un sahte unmount'unda pencere DOM'da kalır → odak çalınmaz
      setTimeout(() => {
        if (node && !node.isConnected && opener && opener.isConnected) opener.focus()
      })
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, submitting])

  // Odak tuzağı: Tab / Shift+Tab pencere içinde döner
  const trapTab = (e: ReactKeyboardEvent) => {
    if (e.key !== 'Tab' || !dialogRef.current) return
    const items = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled])'
      )
    )
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  const trimmed = feedback.trim()
  const ready = trimmed.length >= FEEDBACK_MIN && scope.length > 0 && !submitting

  const toggle = (value: S) =>
    setScope((cur) => (cur.includes(value) ? cur.filter((s) => s !== value) : [...cur, value]))

  const submit = async () => {
    if (!ready) return
    setSubmitting(true)
    setError('')
    // Kapsam sabit sırada gönderilir (sunucu da aynı sırayı kullanır)
    const ordered = scopeOptions.map((o) => o.value).filter((v) => scope.includes(v))
    const failure = await onSubmit(trimmed, ordered)
    setSubmitting(false)
    if (failure) setError(failure)
  }

  const titleId = `${uid}-title`
  const textId = `${uid}-text`
  const hintId = `${uid}-hint`
  const kept = scopeOptions.filter((o) => !scope.includes(o.value)).map((o) => o.label)

  return (
    // Yazılmış talep yanlışlıkla dışarı tıklanınca kaybolmasın (Vazgeç/Esc/× ile kapanır)
    <div
      className="adsx-revise-overlay"
      onClick={() => !submitting && !feedback.trim() && onClose()}
    >
      <div
        ref={dialogRef}
        className="adsx-revise"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapTab}
      >
        <div className="adsx-revise-head">
          <div>
            <div id={titleId} className="adsx-revise-title">
              {title}
            </div>
            <div className="adsx-revise-group">{subjectName}</div>
          </div>
          <button
            type="button"
            className="adsx-revise-close"
            onClick={onClose}
            disabled={submitting}
            aria-label="Kapat"
          >
            <X size={17} />
          </button>
        </div>

        <div className="adsx-revise-body">
          <fieldset className="adsx-revise-scope" disabled={submitting}>
            <legend>Değişecek bölümler</legend>
            {scopeOptions.map((o) => (
              <label key={o.value} className="adsx-revise-check">
                <input
                  type="checkbox"
                  checked={scope.includes(o.value)}
                  onChange={() => toggle(o.value)}
                />
                {o.label}
              </label>
            ))}
          </fieldset>
          {scope.length === 0 && (
            <p className="adsx-revise-warn" role="alert">
              En az bir bölüm seçin.
            </p>
          )}

          <label className="adsx-revise-label" htmlFor={textId}>
            Neyi değiştirelim?
          </label>
          <textarea
            id={textId}
            className="adsx-revise-text"
            rows={4}
            maxLength={FEEDBACK_MAX}
            value={feedback}
            disabled={submitting}
            aria-describedby={hintId}
            placeholder={placeholder}
            onChange={(e) => setFeedback(e.target.value)}
            autoFocus
          />
          <div id={hintId} className="adsx-revise-hint">
            <span>
              {kept.length > 0
                ? `${kept.join(', ')} aynen korunur.`
                : 'Tüm bölümler talebinize göre yeniden yazılır.'}{' '}
              {outcomeNote}
            </span>
            <span className="chx-mono">
              {feedback.length}/{FEEDBACK_MAX}
            </span>
          </div>

          {error && (
            <div className="error-banner" role="alert">
              {error}
            </div>
          )}
        </div>

        <div className="adsx-revise-foot">
          <button
            type="button"
            className="adsx-revise-cancel"
            onClick={onClose}
            disabled={submitting}
          >
            Vazgeç
          </button>
          <button
            type="button"
            className="adsx-activate-btn"
            onClick={() => void submit()}
            disabled={!ready}
          >
            {submitting && (
              <span className="chx-spin">
                <Loader2 size={14} />
              </span>
            )}
            {submitting ? 'Gönderiliyor…' : 'Revize Et'}
          </button>
        </div>
      </div>
    </div>
  )
}
