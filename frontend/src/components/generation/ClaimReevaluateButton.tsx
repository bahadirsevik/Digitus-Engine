/**
 * "Profili güncelledim, yeniden değerlendir" — iddia incelemesini güncel profille tazeler.
 * Ücretsiz ve deterministiktir; metin değişmez. Sonuç sayıları kanal bazında olduğu gibi
 * gösterilir (temizlik vaat edilmez).
 */
import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import ErrorBanner from '../ErrorBanner'
import { apiErrorMessage, generationApi } from '../../services/api'
import type { ClaimReevaluateChannel } from '../../services/api'
import '../../pages/Generation.css'

const channelText = (label: string, c?: ClaimReevaluateChannel) =>
  `${label}: ${c?.cleared ?? 0} iddia uyarısı kalktı, ${c?.still_flagged ?? 0} uyarı hâlâ kontrol bekliyor`

export default function ClaimReevaluateButton({
  runId,
  workspaceId,
  disabled = false,
  onDone,
}: {
  runId: number
  workspaceId?: number | null
  disabled?: boolean
  onDone?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    if (!workspaceId || !runId) return
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const { data } = await generationApi.reevaluateClaims(runId, workspaceId)
      setMessage(
        `Yeniden değerlendirildi — ${channelText('RSA', data.rsa)}; ${channelText('SEO', data.seo)}; ${channelText('Sosyal', data.social)}`
      )
      onDone?.()
    } catch (err: unknown) {
      setError(apiErrorMessage(err, 'İddia incelemesi yeniden değerlendirilemedi'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="adsx-reeval">
      <button
        type="button"
        className="adsx-regen-btn"
        onClick={run}
        disabled={disabled || busy || !workspaceId}
        title="Profili güncelledikten sonra iddia bulgularını yeniden değerlendirir (içerik metni değişmez)"
      >
        <RefreshCw size={12} strokeWidth={2.4} className={busy ? 'chx-spin' : undefined} />
        {busy ? 'Değerlendiriliyor…' : 'Profili güncelledim, yeniden değerlendir'}
      </button>
      {message && (
        <div className="adsx-reeval-result" role="status">
          {message}
        </div>
      )}
      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
    </div>
  )
}
