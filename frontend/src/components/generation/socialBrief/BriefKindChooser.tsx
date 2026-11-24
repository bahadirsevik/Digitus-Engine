/** Yeni brief'in ilk adımı: organik sosyal içerik mi, reklam kampanyası mı. */
import { Megaphone, MessageSquareText } from 'lucide-react'

export type BriefKind = 'social' | 'campaign'

interface Props {
  onChoose: (kind: BriefKind) => void
}

export default function BriefKindChooser({ onChoose }: Props) {
  return (
    <div className="sb-kind-chooser" role="group" aria-label="Brief türü">
      <button type="button" className="sb-kind-card" onClick={() => onChoose('social')}>
        <MessageSquareText size={26} aria-hidden />
        <span className="sb-kind-title">Sosyal Medya İçeriği</span>
        <span className="sb-kind-desc">
          Takipçilere yönelik bilgilendirici ve etkileşim odaklı paylaşımlar: post, carousel, reels,
          story.
        </span>
      </button>
      <button type="button" className="sb-kind-card" onClick={() => onChoose('campaign')}>
        <Megaphone size={26} aria-hidden />
        <span className="sb-kind-title">Reklam Kampanyası</span>
        <span className="sb-kind-desc">
          İndirim, paket, ücretsiz kargo veya hediye gibi bir teklifle satış ya da talep toplayan
          içerikler. Her içerikte çağrı (CTA) ve görsel prompt'u gelir.
        </span>
      </button>
    </div>
  )
}
