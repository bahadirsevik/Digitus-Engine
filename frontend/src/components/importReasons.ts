import { SkippedKeywordDetail } from '../services/api'

// Import raporundaki skip nedenlerinin kullanıcı etiketi (tek kaynak).
export const reasonLabel = (detail: SkippedKeywordDetail): string => {
  switch (detail.reason) {
    case 'skipped_exact':
      return "Workspace'te ayni metriklerle zaten var"
    case 'skipped_fuzzy':
      return detail.matched
        ? `Workspace'te benzer kelime var: "${detail.matched}"`
        : "Workspace'te benzer kelime var"
    case 'batch_duplicate':
      return detail.matched
        ? `Bu listede benzer kelime: "${detail.matched}"`
        : 'Bu listede benzer kelime'
    case 'skipped_theme':
      return detail.matched
        ? `Yasakli temayla elendi: "${detail.matched}"`
        : 'Yasakli temayla elendi'
    case 'limit_exceeded':
      return 'Havuz limiti doldu'
    default:
      return detail.reason
  }
}
