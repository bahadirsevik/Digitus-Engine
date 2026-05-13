/** Shared domain types used across multiple pages and components. */

export interface ScoringRun {
  id: number;
  run_name?: string;
  status: string;
  ads_capacity?: number;
  seo_capacity?: number;
  social_capacity?: number;
  default_relevance_coefficient?: number;
  keyword_source_filter?: "csv" | "google_ads_api" | null;
  brand_profile_id?: number;
  enable_ads?: boolean;
  enable_seo?: boolean;
  enable_social?: boolean;
  keyword_selection_mode?: string;
  keyword_limit?: number;
  skip_relevance?: boolean;
  created_at?: string;
}

export interface AdHeadline {
  text: string;
  pinned_position?: number | null;
  char_count?: number;
}

export interface AdDescription {
  text: string;
  pinned_position?: number | null;
  char_count?: number;
}

export interface AdGroup {
  name: string;
  theme: string;
  keywords?: string[];
  headlines: AdHeadline[];
  descriptions: AdDescription[];
  negative_keywords: string[];
}

export interface AdsValidationSummary {
  headlines_kept: number;
  headlines_shortened: number;
  headlines_regenerated: number;
  headlines_eliminated: number;
  dki_converted_to_plain: number;
}

export interface AdsResult {
  total_groups: number;
  total_headlines?: number;
  total_descriptions?: number;
  total_negative_keywords?: number;
  ad_groups?: AdGroup[];
  validation_summary?: AdsValidationSummary;
}

export interface SEOGeoItem {
  id: number;
  keyword: string;
  title?: string;
  word_count?: number;
  is_stale?: boolean;
  seo_score: number;
  geo_score: number;
  combined_score: number;
}

export interface SocialIdea {
  id?: number;
  title: string;
  concept?: string;
}

export interface SocialCategory {
  id?: number;
  name: string;
  type?: string;
  ideas?: SocialIdea[];
}

export interface KeywordRelevanceResult {
  keyword_id: number;
  keyword?: string;
  relevance_score: number;
}

export type ProfileData = Record<string, unknown> | null;
