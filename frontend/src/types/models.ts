/** Shared domain types used across multiple pages and components. */

export interface ScoringRun {
  id: number
  run_name?: string
  status: string
  ads_capacity?: number
  seo_capacity?: number
  social_capacity?: number
  default_relevance_coefficient?: number
  keyword_source_filter?: 'csv' | 'google_ads_api' | null
  brand_profile_id?: number
  enable_ads?: boolean
  enable_seo?: boolean
  enable_social?: boolean
  keyword_selection_mode?: string
  keyword_limit?: number
  skip_relevance?: boolean
  auto_assign_channels?: boolean
  algorithm_version?: string | null
  created_at?: string
}

export interface AdHeadline {
  id?: number
  text: string
  type?: string
  pinned_position?: number | null
  char_count?: number
  validation_action?: string | null
  original_length?: number | null
  sort_order?: number
}

export interface AdDescription {
  id?: number
  text: string
  type?: string
  pinned_position?: number | null
  char_count?: number
  validation_action?: string | null
  sort_order?: number
}

export interface NegativeKeyword {
  id?: number
  keyword: string
  match_type?: string
  category?: string | null
  reason?: string | null
}

export interface AdGroup {
  id?: number
  name?: string
  theme?: string
  group_name?: string
  group_theme?: string | null
  target_keyword_ids?: number[]
  target_keywords?: string[]
  keywords?: string[]
  headlines: AdHeadline[]
  descriptions: AdDescription[]
  negative_keywords: NegativeKeyword[]
}

export interface AdsValidationSummary {
  headlines_kept: number
  headlines_shortened: number
  headlines_regenerated: number
  headlines_eliminated: number
  dki_converted_to_plain: number
}

// ADS üretim seti özeti (versiyonlama — Faz E)
export interface AdGenerationSetSummary {
  id: number
  scoring_run_id: number
  version_number: number
  status: 'generating' | 'active' | 'draft' | 'archived' | 'failed'
  is_stale: boolean
  groups_count?: number | null
  failed_groups?: number | null
  task_id?: string | null
  created_at?: string | null
  completed_at?: string | null
}

export interface AdsSetListResult {
  scoring_run_id: number
  sets: AdGenerationSetSummary[]
  active_set_id?: number | null
}

export interface AdsResult {
  total_groups: number
  total_headlines?: number
  total_descriptions?: number
  total_negative_keywords?: number
  ad_groups?: AdGroup[]
  validation_summary?: AdsValidationSummary
  generation_set?: AdGenerationSetSummary | null
}

// Yayın öncesi inceleme özeti — backend okuma anında türetir. `required`
// kritik checklist başarısızlığı demektir; manual_checks sistemde verisi
// olmayan (yazar, kaynak doğrulama, JSON-LD, güncelleme takvimi) maddelerdir.
export interface PublishReview {
  required: boolean
  status: 'review_required' | 'manual_check_only'
  label: string
  critical_failures: { criterion: string; source: 'seo_auto' | 'geo_ai'; detail: string }[]
  manual_checks: string[]
}

export interface SEOGeoItem {
  id: number
  keyword: string
  title?: string
  word_count?: number
  is_stale?: boolean
  seo_score: number
  geo_score: number
  combined_score: number
  // Kart özeti alanları (liste endpoint'i; tam gövde dönmez)
  intro_paragraph?: string | null
  meta_description?: string | null
  url_suggestion?: string | null
  subheadings?: string[]
  subheading_count?: number | null
  keyword_density?: number | null
  internal_link?: { anchor?: string | null; url?: string | null } | null
  external_link?: { anchor?: string | null; url?: string | null } | null
  faq_items?: { question: string; answer: string }[]
  image_alt_texts?: string[]
  publish_review?: PublishReview | null
  created_at?: string | null
}

export interface SocialIdea {
  id?: number
  title: string
  concept?: string
}

export interface SocialCategory {
  id?: number
  name: string
  type?: string
  ideas?: SocialIdea[]
}

export interface KeywordRelevanceResult {
  keyword_id: number
  keyword?: string
  relevance_score: number
}

export type ProfileData = Record<string, unknown> | null
