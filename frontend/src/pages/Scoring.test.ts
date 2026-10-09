import { describe, expect, it } from 'vitest'
import { initialScoreSortDir, nextScoreSortState } from './scoringSort'

describe('Scoring sort helpers', () => {
  it('uses desc as the first direction for score columns', () => {
    expect(initialScoreSortDir('ads_score')).toBe('desc')
    expect(initialScoreSortDir('seo_score')).toBe('desc')
    expect(initialScoreSortDir('social_score')).toBe('desc')
  })

  it('uses asc as the first direction for rank columns', () => {
    expect(initialScoreSortDir('ads_rank')).toBe('asc')
    expect(initialScoreSortDir('seo_rank')).toBe('asc')
    expect(initialScoreSortDir('social_rank')).toBe('asc')
  })

  it('toggles direction when the same column is selected twice', () => {
    const current = { sort_by: 'ads_score' as const, sort_dir: 'desc' as const }

    expect(nextScoreSortState(current, 'ads_score')).toEqual({
      sort_by: 'ads_score',
      sort_dir: 'asc',
    })
  })

  it('uses the new column default direction instead of sticky per-column state', () => {
    const current = { sort_by: 'ads_score' as const, sort_dir: 'asc' as const }

    expect(nextScoreSortState(current, 'seo_rank')).toEqual({
      sort_by: 'seo_rank',
      sort_dir: 'asc',
    })
    expect(nextScoreSortState(current, 'seo_score')).toEqual({
      sort_by: 'seo_score',
      sort_dir: 'desc',
    })
  })
})
