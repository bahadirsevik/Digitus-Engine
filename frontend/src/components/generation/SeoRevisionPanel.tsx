/**
 * SEO içerik kartındaki "Revize Et" durumu: hazırlanıyor / önizleme (önce→sonra) /
 * başarısız. Önizlemedeki öneri "Kabul Et" denene kadar canlı içeriğe yazılmaz.
 */
import { RefreshCw } from 'lucide-react'
import type { SeoPendingRevision } from '../../types/models'

const pct = (v: number) => `${Math.round(v * 100)}%`

// Alan değerini okunur satırlara çevirir (metin, liste, SSS, madde)
function lines(value: unknown): string[] {
  if (value == null) return []
  if (typeof value === 'string') return value ? [value] : []
  if (Array.isArray(value)) {
    return value.flatMap((v) => {
      if (typeof v === 'string') return [v]
      const o = v as { question?: string; answer?: string; text?: string }
      if (o?.question) return [`S: ${o.question} — C: ${o.answer ?? ''}`]
      if (o?.text) return [o.text]
      return []
    })
  }
  return [String(value)]
}

export default function SeoRevisionPanel({
  revision,
  busy,
  error,
  onAccept,
  onDiscard,
}: {
  revision: SeoPendingRevision
  busy: boolean
  error?: string | null
  onAccept: () => void
  onDiscard: () => void
}) {
  if (revision.status === 'generating') {
    return (
      <div className="adsx-group-revising seox-rev" role="status">
        <span className="chx-spin">
          <RefreshCw size={12} strokeWidth={2.4} />
        </span>{' '}
        Talebinize göre revize ediliyor (“{revision.feedback}”). Bitince öneri burada önizlenir;
        mevcut içerik o zamana kadar değişmez.{' '}
        <button type="button" className="seox-rev-link" onClick={onDiscard} disabled={busy}>
          İptal et
        </button>
        {error && <div className="seox-rev-error">{error}</div>}
      </div>
    )
  }

  if (revision.status === 'failed') {
    return (
      <div className="seox-rev seox-rev-failed" role="alert">
        <span>
          Revizyon başarısız: {revision.error_message || 'Öneri üretilemedi.'} Mevcut içerik
          değişmedi.
        </span>
        <button type="button" className="adsx-revise-cancel" onClick={onDiscard} disabled={busy}>
          Kapat
        </button>
        {error && <div className="seox-rev-error">{error}</div>}
      </div>
    )
  }

  const s = revision.scores
  return (
    <section className="adsx-revision-note is-draft seox-rev" aria-label="Revizyon önerisi">
      <div>
        <b>Revizyon önerisi hazır</b> — talep: “{revision.feedback}”. Siz “Kabul Et” diyene kadar
        mevcut içerik kullanılır; seçmediğiniz bölümler aynen korundu.
      </div>
      {revision.changes.length === 0 ? (
        <p className="seox-rev-empty">Öneri, seçilen bölümlerde değişiklik içermiyor.</p>
      ) : (
        <div className="seox-rev-changes">
          {revision.changes.map((c) => (
            <div key={c.field} className="seox-rev-change">
              <div className="seox-section-label">{c.label.toLocaleUpperCase('tr-TR')}</div>
              <details className="seox-rev-before">
                <summary>Önceki hali</summary>
                {lines(c.before).map((t, i) => (
                  <p key={i}>{t}</p>
                ))}
              </details>
              <div className="seox-rev-after">
                {lines(c.after).map((t, i) => (
                  <p key={i}>{t}</p>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {s && (
        <div className="seox-rev-scores chx-mono">
          SEO {pct(s.seo_before)} → {pct(s.seo_after)} · GEO {pct(s.geo_before)} →{' '}
          {pct(s.geo_after)}
        </div>
      )}
      {(revision.regressions?.length ?? 0) > 0 && (
        <div className="seox-rev-warn" role="note" aria-label="Bozulan kontroller">
          <b>Önceden geçen kontroller bu öneride bozuldu:</b>
          <ul>
            {revision.regressions!.map((r) => (
              <li key={`${r.channel}-${r.criterion}`}>
                {r.label}: {r.before} → {r.after}
                {r.after_detail ? ` (${r.after_detail})` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {revision.publish_review?.required && (
        <div className="seox-rev-warn">
          {revision.publish_review.label}:{' '}
          {revision.publish_review.critical_failures
            .map((f) => f.detail || f.criterion)
            .join(' · ')}
        </div>
      )}
      {error && <div className="seox-rev-error">{error}</div>}
      <div className="adsx-revision-note-foot">
        <button type="button" className="adsx-revise-cancel" onClick={onDiscard} disabled={busy}>
          Vazgeç
        </button>
        <button type="button" className="adsx-activate-btn" onClick={onAccept} disabled={busy}>
          {busy ? 'Uygulanıyor…' : 'Kabul Et'}
        </button>
      </div>
    </section>
  )
}
