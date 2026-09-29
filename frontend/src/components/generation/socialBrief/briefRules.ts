import type { SocialBriefTargetCreateRequest } from '../../../services/api'

/** API sınırı: bir içerik isteğinde 1–30 benzersiz fikir. */
export const MAX_CONTENT_SELECTION = 30

/** Hedef tekilliği backend ile aynı: (platform, content_format). Süre ayırt etmez. */
export function isDuplicateTarget(
  targets: SocialBriefTargetCreateRequest[],
  platform: string,
  contentFormat: string
): boolean {
  return targets.some((t) => t.platform === platform && t.content_format === contentFormat)
}
