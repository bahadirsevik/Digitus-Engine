import type {
  SocialBriefResponse,
  SocialBriefStateResponse,
  SocialBriefTargetResponse,
} from '../../../services/api'

/** Brief detay bölümlerinin paylaştığı bağlam (bileşen prop'u olarak geçer). */
export interface BriefSectionContext {
  brief: SocialBriefResponse
  workspaceId: number
  state: SocialBriefStateResponse | null
  disabled: boolean
  pollIntervalMs: number
  /** Sunucudaki brief durumunu yeniden okur (kategori/attempt keşfi). */
  refreshState: () => Promise<void>
  platformLabel: (platform: string) => string
  formatLabel: (platform: string, format: string) => string
  targetLabel: (target: SocialBriefTargetResponse) => string
  keywordLabel: (keywordId: number) => string
}
