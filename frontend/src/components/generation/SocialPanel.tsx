/**
 * Sosyal medya üretim paneli — Generation sayfasından çıkarıldı (Faz 4).
 * Kanal sayfası (Social) içinde inline çalışır; run seçimi dışarıdan gelir.
 * Yeni brief akışını ve sosyal içerik geçmişini gösterir.
 */
import { useEffect, useState } from 'react'
import SocialBriefWorkspace from './SocialBriefWorkspace'
import SocialContentHistory from './socialBrief/SocialContentHistory'
import { useBrandStore } from '../../stores/brandStore'
import type { ScoringRun } from '../../types/models'
import type { PoolKeyword } from '../ChannelWorkspaceView'
import type { BriefKeywordSelection } from './socialBrief/useBriefKeywordSelection'
import '../../pages/Generation.css'
import './socialBrief/SocialBriefFlow.css'

export default function SocialPanel({
  runId,
  run,
  pool,
  poolLoading,
  poolError,
  onRetryPool,
  keywordSelection,
  pollIntervalMs,
}: {
  runId: number
  run?: ScoringRun | null
  pool?: PoolKeyword[]
  poolLoading?: boolean
  poolError?: string | null
  onRetryPool?: () => void
  keywordSelection?: BriefKeywordSelection
  pollIntervalMs?: number
}) {
  const [historyOpen, setHistoryOpen] = useState(false)
  const workspaceId = useBrandStore((s) => s.activeWorkspace?.id ?? null)
  const channelDisabled = run?.enable_social === false

  // Workspace değişince geçmiş paneli kapanır (başka workspace içeriği görünmesin)
  useEffect(() => {
    setHistoryOpen(false)
  }, [workspaceId])

  return (
    <div className="social-panel">
      {channelDisabled && (
        <p className="channel-disabled-hint">Bu çalışmada SOCIAL kanalı devre dışı.</p>
      )}

      {historyOpen && workspaceId && (
        <SocialContentHistory
          key={workspaceId}
          workspaceId={workspaceId}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      <div style={{ display: historyOpen ? 'none' : 'block' }}>
        <SocialBriefWorkspace
          runId={runId}
          run={run}
          pool={pool}
          poolLoading={poolLoading}
          poolError={poolError}
          onRetryPool={onRetryPool}
          keywordSelection={keywordSelection}
          disabled={channelDisabled}
          pollIntervalMs={pollIntervalMs}
          onOpenHistory={workspaceId ? () => setHistoryOpen(true) : undefined}
        />
      </div>
    </div>
  )
}
