import type { ResearchClaim, ResearchItem, ResearchTheme } from '../services/api'

export type Tone = 'ok' | 'warning' | 'problem' | 'neutral' | 'muted'

/** Analist kararı değiştirebilir mi? Kaynakta hiç bulunamayan iddia rapora alınamaz. */
export const claimDecidable = (claim: ResearchClaim) =>
  claim.check.status === 'found' || claim.check.status === 'short'

/**
 * Varsayılan: kaynakta geçen iddia rapora girer, kısa rakam girmez.
 * `overrides` listesindeki iddialarda analist bu kararı tersine çevirmiştir.
 */
export const claimInReport = (claim: ResearchClaim, overrides: string[]) =>
  claimDecidable(claim) && (claim.check.status === 'found') !== overrides.includes(claim.id)

export function claimChip(claim: ResearchClaim, overrides: string[]): { tone: Tone; text: string } {
  const flipped = overrides.includes(claim.id)
  switch (claim.check.status) {
    case 'found':
      return flipped
        ? { tone: 'muted', text: 'Rapordan çıkarıldı' }
        : { tone: 'ok', text: 'Kaynakta geçiyor' }
    case 'short':
      return flipped
        ? { tone: 'ok', text: 'Elle onaylandı' }
        : { tone: 'warning', text: 'Kısa rakam: bağlamı kontrol edin' }
    case 'no_number':
      return { tone: 'muted', text: 'Denetlenemedi' }
    default:
      return { tone: 'muted', text: 'Kaynakta bulunamadı' }
  }
}

/** İçindeki rakam doğrulanamayan madde rapora girmez; ekranda işaretli görünür. */
export const numberUnverified = (item: ResearchItem | ResearchTheme) => item.numbers_ok === false

export function yearNote(claim: ResearchClaim): string | null {
  if (!claimDecidable(claim) || claim.check.year_in_context !== false) return null
  return `Bağlamda ${claim.year} yılı görülmedi; rakamın bu yıla ait olduğunu kontrol edin.`
}
