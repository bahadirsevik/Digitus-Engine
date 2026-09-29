import { ScoringSortBy, SortDir } from '../services/api'

export interface ScoreSortState {
  sort_by: ScoringSortBy
  sort_dir: SortDir
}

const RANK_SORT_COLUMNS = new Set<ScoringSortBy>(['ads_rank', 'seo_rank', 'social_rank'])

export const DEFAULT_SCORE_SORT: ScoreSortState = { sort_by: 'ads_score', sort_dir: 'desc' }

export function initialScoreSortDir(column: ScoringSortBy): SortDir {
  return RANK_SORT_COLUMNS.has(column) ? 'asc' : 'desc'
}

export function nextScoreSortState(current: ScoreSortState, column: ScoringSortBy): ScoreSortState {
  if (current.sort_by === column) {
    return { sort_by: column, sort_dir: current.sort_dir === 'asc' ? 'desc' : 'asc' }
  }
  return { sort_by: column, sort_dir: initialScoreSortDir(column) }
}
