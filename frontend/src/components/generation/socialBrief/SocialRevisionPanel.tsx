/**
 * Sosyal içerik kartının "Revize Et" durumu: hazırlanıyor / önizleme / başarısız.
 * Önerilen içerik tam kart olarak önizlenir; "Kabul Et" denene kadar canlı içerik değişmez.
 */
import { RefreshCw } from 'lucide-react'
import type { SocialContentRevisionItem } from '../../../services/api'
import SocialContentCard, { type SocialContentView } from './SocialContentCard'
import '../../../pages/Generation.css'

const REVISION_FIELD_LABEL: Record<string, string> = {
  hooks: 'Açılış kancaları',
  caption: 'Metin',
  cta_text: 'Harekete geçirici çağrı',
  hashtags: "Hashtag'ler",
  format_payload: 'Senaryo / slaytlar / gönderiler',
  visual_suggestion: 'Görsel önerisi',
  video_concept: 'Video konsepti',
  platform_notes: 'Platform notları',
  industry_posting_suggestion: 'Paylaşım önerisi',
}

export default function SocialRevisionPanel({
  item,
  base,
  busy,
  error,
  onAccept,
  onDiscard,
}: {
  item: SocialContentRevisionItem
  // Önizleme kartının platform/format/kelime gibi değişmeyen bilgileri canlı karttan gelir
  base: SocialContentView
  busy: boolean
  error?: string | null
  onAccept: () => void
  onDiscard: () => void
}) {
  if (item.status === 'generating') {
    return (
      <div className="adsx-group-revising seox-rev" role="status">
        <span className="chx-spin">
          <RefreshCw size={12} strokeWidth={2.4} />
        </span>{' '}
        Talebinize göre revize ediliyor (“{item.feedback}”). Bitince öneri burada önizlenir; mevcut
        içerik o zamana kadar değişmez.
      </div>
    )
  }

  if (item.status === 'failed') {
    return (
      <div className="seox-rev seox-rev-failed" role="alert">
        <span>
          Revizyon başarısız: {item.error_message || 'Öneri üretilemedi.'} Mevcut içerik değişmedi.
        </span>
        <button type="button" className="adsx-revise-cancel" onClick={onDiscard} disabled={busy}>
          Kapat
        </button>
        {error && <div className="seox-rev-error">{error}</div>}
      </div>
    )
  }

  const p = item.proposed
  if (!p) return null
  const view: SocialContentView = {
    ...base,
    ideaTitle: `${base.ideaTitle || `İçerik #${base.id}`} (öneri)`,
    hooks: p.hooks,
    caption: p.caption,
    ctaText: p.cta_text,
    hashtags: p.hashtags,
    visualSuggestion: p.visual_suggestion,
    imagePrompt: p.image_prompt ?? base.imagePrompt,
    // Teklif/şart satırlarını sunucu kilitli tekliften kurar (eski içerikte overlay olmasa da)
    overlayText: p.overlay_text ?? base.overlayText,
    videoConcept: p.video_concept,
    platformNotes: p.platform_notes,
    postingSuggestion: p.industry_posting_suggestion,
    scenario: p.scenario,
    formatPayload: p.format_payload,
    durationStatus: p.duration_status,
    actualDurationSec: p.actual_duration_sec,
    validationWarnings: p.validation_warnings,
    meta: undefined,
  }
  return (
    <section className="adsx-revision-note is-draft seox-rev" aria-label="Revizyon önerisi">
      <div>
        <b>Revizyon önerisi hazır</b> — talep: “{item.feedback}”. Değişen bölümler:{' '}
        {item.changes.map((c) => REVISION_FIELD_LABEL[c] ?? c).join(', ')}. Siz “Kabul Et” diyene
        kadar mevcut içerik kullanılır; seçmediğiniz bölümler aynen korundu.
      </div>
      <SocialContentCard view={view} />
      {error && (
        <div className="seox-rev-error" role="alert">
          {error}
        </div>
      )}
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
