// Sosyal brief akışı testleri için ortak, saf veri fikstürleri (vitest bağımlılığı yok).
import type { PoolKeyword } from '../../ChannelWorkspaceView'
import type {
  SocialAttemptSummaryResponse,
  SocialBriefContentsAttemptResponse,
  SocialBriefResponse,
  SocialBriefStateResponse,
  SocialContentHistoryItemResponse,
  SocialFormatMatrixResponse,
  SocialGeneratedIdeaResponse,
  SocialIdeasGenerateResponse,
} from '../../../services/api'

export const WS_ID = 7
export const RUN_ID = 10

export const FORMAT_MATRIX: SocialFormatMatrixResponse = {
  version: 'v1',
  limits: { min_keywords: 1, max_keywords: 5, min_targets: 1, max_targets: 6 },
  platforms: [
    {
      id: 'instagram',
      label: 'Instagram',
      formats: [
        { id: 'post', label: 'Post', requires_duration: false, duration_presets: [] },
        {
          id: 'reels',
          label: 'Reels',
          requires_duration: true,
          duration_presets: [
            { id: 'short_1_15', label: '1-15 saniye', min_sec: 1, max_sec: 15 },
            { id: 'short_31_60', label: '31-60 saniye', min_sec: 31, max_sec: 60 },
          ],
        },
        { id: 'carousel', label: 'Carousel', requires_duration: false, duration_presets: [] },
      ],
    },
    {
      id: 'twitter',
      label: 'X',
      formats: [{ id: 'thread', label: 'Thread', requires_duration: false, duration_presets: [] }],
    },
  ],
}

export const POOL: PoolKeyword[] = [
  { id: 101, keyword_id: 1, keyword: 'dijital pazarlama ajansı', volume: 5400, score: 85 },
  { id: 102, keyword_id: 2, keyword: 'seo optimizasyonu', volume: 3200, score: 78 },
  { id: 103, keyword_id: 3, keyword: 'sosyal medya yönetimi', volume: 4100, score: 82 },
  { id: 104, keyword_id: 4, keyword: 'google reklamları', volume: 6000, score: 90 },
  { id: 105, keyword_id: 5, keyword: 'içerik pazarlaması', volume: 1800, score: 70 },
  { id: 106, keyword_id: 6, keyword: 'e-ticaret seo', volume: 2200, score: 74 },
]

export const BRIEF: SocialBriefResponse = {
  id: 42,
  scoring_run_id: RUN_ID,
  brand_name_snapshot: 'Test Brand',
  brand_context_snapshot: 'Test Brand Context',
  channel_assignment_version: 1,
  format_matrix_version: 'v1',
  locked_at: null,
  is_stale: false,
  created_at: '2026-09-27T10:00:00Z',
  keywords: [
    { id: 1, keyword_id: 1, keyword_snapshot: 'dijital pazarlama ajansı', position: 0 },
    { id: 2, keyword_id: 2, keyword_snapshot: 'seo optimizasyonu', position: 1 },
  ],
  targets: [
    { id: 1, platform: 'instagram', content_format: 'post' },
    {
      id: 2,
      platform: 'instagram',
      content_format: 'reels',
      duration_preset_id: 'short_31_60',
      duration_min_sec: 31,
      duration_max_sec: 60,
    },
  ],
}

export const LOCKED_BRIEF: SocialBriefResponse = { ...BRIEF, locked_at: '2026-09-27T10:05:00Z' }

export const CATEGORIES = [
  {
    id: 201,
    category_name: 'Pazarlama Taktikleri',
    category_type: 'educational',
    description: 'Ajanslar için pratik dijital pazarlama ipuçları.',
    relevance_score: 0.94,
    suggested_keyword_ids: [1],
  },
  {
    id: 202,
    category_name: 'SEO Rehberleri',
    category_type: 'product_benefit',
    description: 'Arama motoru görünürlüğünü artıran rehberler.',
    relevance_score: 0.88,
    suggested_keyword_ids: [2],
  },
]

export function attempt(
  id: number,
  stage: SocialAttemptSummaryResponse['stage'],
  status: SocialAttemptSummaryResponse['status'],
  extra: Partial<SocialAttemptSummaryResponse> = {}
): SocialAttemptSummaryResponse {
  return {
    id,
    stage,
    status,
    reason_code: null,
    created_at: '2026-09-27T10:10:00Z',
    completed_at: null,
    lease_expired: false,
    requested_idea_ids: [],
    ...extra,
  }
}

export function briefState(
  overrides: Partial<SocialBriefStateResponse> = {}
): SocialBriefStateResponse {
  return {
    brief_id: BRIEF.id,
    scoring_run_id: RUN_ID,
    is_stale: false,
    locked_at: null,
    category_attempt: null,
    categories: [],
    ideas_attempt: null,
    idea_retry_attempts: [],
    content_attempts: [],
    content_idea_ids: [],
    ...overrides,
  }
}

export function stateWithCategories(
  overrides: Partial<SocialBriefStateResponse> = {}
): SocialBriefStateResponse {
  return briefState({
    locked_at: LOCKED_BRIEF.locked_at,
    category_attempt: attempt(101, 'categories', 'completed'),
    categories: CATEGORIES,
    ...overrides,
  })
}

export function idea(
  id: number,
  targetId: number,
  extra: Partial<SocialGeneratedIdeaResponse> = {}
): SocialGeneratedIdeaResponse {
  const target = BRIEF.targets.find((t) => t.id === targetId)!
  return {
    id,
    category_id: 201,
    keyword_id: 1,
    brief_id: BRIEF.id,
    brief_target_id: targetId,
    idea_title: `Fikir ${id}`,
    idea_description: `Açıklama ${id}`,
    target_platform: target.platform,
    content_format: target.content_format,
    trend_alignment: 0.7,
    is_stale: false,
    ...extra,
  }
}

export function ideasResponse(
  attemptId: number,
  status: SocialIdeasGenerateResponse['attempt_status'],
  ideas: SocialGeneratedIdeaResponse[],
  extra: Partial<SocialIdeasGenerateResponse> = {}
): SocialIdeasGenerateResponse {
  return {
    brief_id: BRIEF.id,
    scoring_run_id: RUN_ID,
    attempt_id: attemptId,
    attempt_status: status,
    total_ideas: ideas.length,
    ideas,
    coverage: [
      {
        target_id: 1,
        requested: 3,
        accepted: ideas.filter((i) => i.brief_target_id === 1).length,
        missing: 0,
      },
      {
        target_id: 2,
        requested: 3,
        accepted: ideas.filter((i) => i.brief_target_id === 2).length,
        missing: 0,
      },
    ],
    warnings: [],
    reason_code: null,
    replayed: false,
    ...extra,
  }
}

export function contentsResponse(
  attemptId: number,
  status: SocialBriefContentsAttemptResponse['attempt_status'],
  extra: Partial<SocialBriefContentsAttemptResponse> = {}
): SocialBriefContentsAttemptResponse {
  return {
    brief_id: BRIEF.id,
    scoring_run_id: RUN_ID,
    attempt_id: attemptId,
    attempt_status: status,
    requested_idea_ids: [],
    successful_idea_ids: [],
    unresolved_idea_ids: [],
    total_contents: 0,
    contents: [],
    warnings: [],
    reason_code: null,
    replayed: false,
    ...extra,
  }
}

export function historyItem(
  id: number,
  extra: Partial<SocialContentHistoryItemResponse> = {}
): SocialContentHistoryItemResponse {
  return {
    id,
    idea_id: 300 + id,
    idea_title: `İçerik fikri ${id}`,
    brief_id: BRIEF.id,
    brief_is_stale: false,
    scoring_run_id: RUN_ID,
    run_name: 'Eylül analizi',
    category_name: 'Pazarlama Taktikleri',
    keyword: 'dijital pazarlama ajansı',
    platform: 'instagram',
    content_format: 'post',
    hooks: [{ text: 'Bunu biliyor muydunuz?', style: 'question' }],
    caption: `Metin ${id}`,
    scenario: null,
    format_payload: { kind: 'caption' },
    visual_suggestion: null,
    video_concept: null,
    cta_text: 'Hemen inceleyin',
    hashtags: ['pazarlama'],
    industry_posting_suggestion: null,
    platform_notes: null,
    duration_status: 'not_applicable',
    actual_duration_sec: null,
    duration_min_sec: null,
    duration_max_sec: null,
    validation_warnings: [],
    is_stale: false,
    created_at: '2026-09-27T11:00:00Z',
    ...extra,
  }
}

export function historyPage(
  items: SocialContentHistoryItemResponse[],
  {
    total,
    offset = 0,
    hasMore = false,
  }: { total?: number; offset?: number; hasMore?: boolean } = {}
) {
  return {
    brand_profile_id: WS_ID,
    total: total ?? items.length,
    limit: 20,
    offset,
    has_more: hasMore,
    items,
  }
}

/** Yanıtı dışarıdan çözülen promise (yarış testleri için). */
export function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

export const NETWORK_ERROR = { status: 0, message: 'Network Error' }
export function apiError(status: number, code: string, extra: Record<string, unknown> = {}) {
  return { status, message: code, detail: { code, message: code, ...extra } }
}
