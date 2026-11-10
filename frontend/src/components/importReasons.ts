import { SkippedKeywordDetail } from '../services/api'

// Import raporundaki skip nedenlerinin kullanıcı etiketi (tek kaynak).
export const reasonLabel = (detail: SkippedKeywordDetail): string => {
  switch (detail.reason) {
    case 'skipped_exact':
      return 'Bu marka çalışmasında aynı metriklerle zaten var'
    case 'skipped_fuzzy':
      return detail.matched
        ? `Bu marka çalışmasında benzer kelime var: "${detail.matched}"`
        : 'Bu marka çalışmasında benzer kelime var'
    case 'batch_duplicate':
      return detail.matched
        ? `Bu listede benzer kelime: "${detail.matched}"`
        : 'Bu listede benzer kelime'
    case 'skipped_theme':
      return detail.matched
        ? `Yasaklı temayla elendi: "${detail.matched}"`
        : 'Yasaklı temayla elendi'
    case 'limit_exceeded':
      return 'Havuz limiti doldu'
    case 'skipped_junk':
      return 'Geçersiz kelime (boş / yalnız sayı veya sembol)'
    default:
      return detail.reason
  }
}
