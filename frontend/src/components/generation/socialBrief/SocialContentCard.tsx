import { useState, type ReactNode } from 'react'
import { AlertTriangle, Check, Clock, Copy } from 'lucide-react'
import type { SocialFormatPayload } from '../../../services/api'
import { DURATION_STATUS_TEXT, formatSeconds, hookStyleLabel, reasonText } from './labels'
import './SocialBriefFlow.css'

export interface SocialContentView {
  id: number
  ideaTitle?: string | null
  platformLabel: string
  formatLabel: string
  keyword?: string | null
  categoryName?: string | null
  hooks: { text: string; style?: string | null }[]
  caption: string
  ctaText?: string | null
  hashtags: string[]
  visualSuggestion?: string | null
  videoConcept?: string | null
  platformNotes?: string | null
  postingSuggestion?: string | null
  scenario?: string | null
  formatPayload?: SocialFormatPayload | null
  durationStatus?: string | null
  actualDurationSec?: number | null
  durationMinSec?: number | null
  durationMaxSec?: number | null
  validationWarnings: string[]
  isStale: boolean
  meta?: ReactNode
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'error'>('idle')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setState('done')
    } catch {
      setState('error')
    }
    window.setTimeout(() => setState('idle'), 2000)
  }
  return (
    <button type="button" className="sb-btn sb-btn-ghost sb-btn-xs" onClick={copy}>
      {state === 'done' ? <Check size={12} /> : <Copy size={12} />}
      {state === 'done' ? 'Kopyalandı' : state === 'error' ? 'Kopyalanamadı' : label}
    </button>
  )
}

function DurationLine({ view }: { view: SocialContentView }) {
  const status = view.durationStatus
  if (!status || status === 'not_applicable') return null
  const range =
    view.durationMinSec != null && view.durationMaxSec != null
      ? `${formatSeconds(view.durationMinSec)} – ${formatSeconds(view.durationMaxSec)}`
      : null
  const isWarn = status !== 'valid'
  return (
    <div
      className={`sb-duration-line ${isWarn ? 'is-warn' : 'is-ok'}`}
      role={isWarn ? 'status' : undefined}
    >
      <Clock size={13} />
      <span>
        Gerçek süre:{' '}
        <b>{view.actualDurationSec != null ? formatSeconds(view.actualDurationSec) : '—'}</b>
        {range && (
          <>
            {' '}
            · Seçilen aralık: <b>{range}</b>
          </>
        )}
      </span>
      <span className={`sb-badge ${isWarn ? 'sb-badge-stale' : 'sb-badge-active'}`}>
        {DURATION_STATUS_TEXT[status] || status}
      </span>
    </div>
  )
}

function FormatBody({ view }: { view: SocialContentView }) {
  const p = view.formatPayload
  if (p?.kind === 'video') {
    return (
      <div className="sb-content-section">
        <div className="sb-content-section-head">
          <span className="sb-content-label">Senaryo ({p.segments.length} sahne)</span>
          <CopyButton
            label="Senaryoyu kopyala"
            text={p.segments
              .map(
                (s) =>
                  `[${s.start_sec}-${s.end_sec} sn] ${s.scene}\nEkran: ${s.on_screen_text}\nSes: ${s.voiceover}`
              )
              .join('\n\n')}
          />
        </div>
        <ol className="sb-segment-list">
          {p.segments.map((s, i) => (
            <li key={i} className="sb-segment">
              <span className="sb-segment-time">
                {s.start_sec}–{s.end_sec} sn
              </span>
              <div className="sb-segment-body">
                <p className="sb-segment-scene">{s.scene}</p>
                {s.on_screen_text && (
                  <p>
                    <span className="sb-content-label">Ekran yazısı:</span> {s.on_screen_text}
                  </p>
                )}
                {s.voiceover && (
                  <p>
                    <span className="sb-content-label">Seslendirme:</span> {s.voiceover}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
    )
  }
  if (p?.kind === 'carousel') {
    return (
      <div className="sb-content-section">
        <span className="sb-content-label">Slaytlar ({p.slides.length})</span>
        <ol className="sb-slide-list">
          {p.slides.map((s, i) => (
            <li key={i} className="sb-slide">
              <span className="sb-slide-no">{s.position ?? i + 1}</span>
              <div>
                <p className="sb-slide-headline">{s.headline}</p>
                <p className="sb-slide-body">{s.body}</p>
                {s.visual_direction && (
                  <p className="sb-slide-visual">Görsel: {s.visual_direction}</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
    )
  }
  if (p?.kind === 'thread') {
    return (
      <div className="sb-content-section">
        <div className="sb-content-section-head">
          <span className="sb-content-label">Gönderi dizisi ({p.posts.length})</span>
          <CopyButton label="Diziyi kopyala" text={p.posts.map((t) => t.text).join('\n\n')} />
        </div>
        <ol className="sb-thread-list">
          {p.posts.map((t, i) => (
            <li key={i} className="sb-thread-post">
              <p>{t.text}</p>
              <span className="sb-thread-count">{t.text.length}/280</span>
            </li>
          ))}
        </ol>
      </div>
    )
  }
  if (view.scenario) {
    return (
      <div className="sb-content-section">
        <span className="sb-content-label">Senaryo</span>
        <p className="sb-prewrap">{view.scenario}</p>
      </div>
    )
  }
  return null
}

export default function SocialContentCard({ view }: { view: SocialContentView }) {
  const captionWithTags = [
    view.caption,
    view.hashtags.map((h) => `#${h.replace(/^#/, '')}`).join(' '),
  ]
    .filter(Boolean)
    .join('\n\n')
  return (
    <article className="sb-content-card" aria-label={view.ideaTitle || `İçerik #${view.id}`}>
      <header className="sb-content-card-head">
        <div className="sb-chip-container">
          <span className="sb-chip">
            <b>{view.platformLabel}</b> · {view.formatLabel}
          </span>
          {view.keyword && <span className="sb-chip">{view.keyword}</span>}
          {view.categoryName && <span className="sb-chip">{view.categoryName}</span>}
          {view.isStale && (
            <span className="sb-badge sb-badge-stale">Güncel değil (kanal ataması değişti)</span>
          )}
        </div>
        {view.ideaTitle && <h4 className="sb-content-title">{view.ideaTitle}</h4>}
        {view.meta}
      </header>

      <DurationLine view={view} />

      {view.hooks.length > 0 && (
        <div className="sb-content-section">
          <span className="sb-content-label">Açılış kancaları</span>
          <ul className="sb-hook-list">
            {view.hooks.map((h, i) => (
              <li key={i}>
                {h.text}
                {h.style && <span className="sb-hook-style">{hookStyleLabel(h.style)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="sb-content-section">
        <div className="sb-content-section-head">
          <span className="sb-content-label">Metin</span>
          <CopyButton label="Metni kopyala" text={captionWithTags} />
        </div>
        <p className="sb-prewrap">{view.caption}</p>
      </div>

      <FormatBody view={view} />

      <dl className="sb-content-details">
        {view.ctaText && (
          <>
            <dt>Harekete geçirici çağrı</dt>
            <dd>{view.ctaText}</dd>
          </>
        )}
        {view.hashtags.length > 0 && (
          <>
            <dt>Hashtag'ler</dt>
            <dd>{view.hashtags.map((h) => `#${h.replace(/^#/, '')}`).join(' ')}</dd>
          </>
        )}
        {view.visualSuggestion && (
          <>
            <dt>Görsel önerisi</dt>
            <dd>{view.visualSuggestion}</dd>
          </>
        )}
        {view.videoConcept && (
          <>
            <dt>Video konsepti</dt>
            <dd>{view.videoConcept}</dd>
          </>
        )}
        {view.platformNotes && (
          <>
            <dt>Platform notları</dt>
            <dd>{view.platformNotes}</dd>
          </>
        )}
        {view.postingSuggestion && (
          <>
            <dt>Paylaşım önerisi</dt>
            <dd>{view.postingSuggestion}</dd>
          </>
        )}
      </dl>

      {view.validationWarnings.length > 0 && (
        <ul className="sb-warning-list" aria-label="Doğrulama uyarıları">
          {view.validationWarnings.map((w, i) => (
            <li key={i}>
              <AlertTriangle size={12} />
              {reasonText(w)}
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}
