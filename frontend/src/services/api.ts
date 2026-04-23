import axios from 'axios'

const API_BASE = '/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Keywords API
export const keywordsApi = {
  list: (params?: { skip?: number; limit?: number; active_only?: boolean }) => 
    api.get('/keywords', { params }),
  
  get: (id: number) => 
    api.get(`/keywords/${id}`),
  
  create: (data: KeywordCreate) => 
    api.post('/keywords', data),
  
  update: (id: number, data: Partial<KeywordCreate>) => 
    api.put(`/keywords/${id}`, data),
  
  delete: (id: number) => 
    api.delete(`/keywords/${id}`),
  
  import: (keywords: KeywordCreate[]) => 
    api.post('/keywords/import', { keywords }),
  
  deleteAll: () => 
    api.delete('/keywords/all')
}

// Scoring API
export const scoringApi = {
  listRuns: (params?: { skip?: number; limit?: number }) => 
    api.get('/scoring/runs', { params }),
  
  getRun: (id: number) => 
    api.get(`/scoring/runs/${id}`),
  
  createRun: (data: ScoringRunCreate) => 
    api.post('/scoring/runs', data),
  
  executeRun: (id: number) => 
    api.post(`/scoring/runs/${id}/execute`),
  
  getScores: (runId: number, limit?: number) => 
    api.get(`/scoring/runs/${runId}/scores`, { params: { limit } }),
  
  getTopByChannel: (runId: number, channel: string, limit?: number) => 
    api.get(`/scoring/runs/${runId}/top/${channel}`, { params: { limit } }),
  
  deleteRun: (runId: number) => 
    api.delete(`/scoring/runs/${runId}`),
  
  exportXlsx: (runId: number) => 
    api.get(`/scoring/runs/${runId}/export/xlsx`, { responseType: 'blob' })
}

// Brand Profile API
export const brandProfileApi = {
  getProfile: (runId: number) =>
    api.get(`/brand-profile/runs/${runId}/profile`),

  analyzeProfile: (runId: number, data: ProfileAnalyzeRequest) =>
    api.post(`/brand-profile/runs/${runId}/profile/analyze`, data),

  confirmProfile: (runId: number, data?: ProfileConfirmRequest) =>
    api.put(`/brand-profile/runs/${runId}/profile/confirm`, data || {}),

  computeRelevance: (runId: number) =>
    api.post(`/brand-profile/runs/${runId}/relevance/compute`),

  getRelevance: (runId: number, minScore = 0) =>
    api.get(`/brand-profile/runs/${runId}/relevance`, {
      params: { min_score: minScore }
    })
}

// Channels API
export const channelsApi = {
  assign: (runId: number, relevanceOverride?: number) => 
    api.post(`/channels/runs/${runId}/assign`, relevanceOverride !== undefined
      ? { relevance_override: relevanceOverride }
      : undefined),
  
  getPools: (runId: number) => 
    api.get(`/channels/runs/${runId}/pools`),
  
  getPool: (runId: number, channel: string) => 
    api.get(`/channels/runs/${runId}/pools/${channel}`),
  
  getStrategic: (runId: number) => 
    api.get(`/channels/runs/${runId}/strategic`)
}

// Export API
export const exportApi = {
  create: (data: ExportRequest) => 
    api.post('/export', data),
  
  status: (exportId: string) =>
    api.get(`/export/${exportId}/status`),
  
  download: (exportId: string) => 
    api.get(`/export/${exportId}/download`, { responseType: 'blob' })
}

// Generation API
export const generationApi = {
  generateAds: (keywordIds: number[]) => 
    api.post('/generation/ads', { keyword_ids: keywordIds }),
  
  generateSeoGeo: (keywordIds: number[], contentType?: string, wordCount?: number) => 
    api.post('/generation/seo-geo', { 
      keyword_ids: keywordIds, 
      content_type: contentType || 'blog_post',
      target_word_count: wordCount || 1500
    }),
  
  generateSocial: (keywordIds: number[], platforms?: string[]) => 
    api.post('/generation/social', { 
      keyword_ids: keywordIds, 
      platforms: platforms || ['instagram', 'twitter', 'linkedin']
    })
}

// Health API (uses base URL without /api/v1 prefix)
export const healthApi = {
  check: () => axios.get('http://localhost:8000/'),
  detailed: () => axios.get('http://localhost:8000/health')
}

// Google Ads API
export const googleAdsApi = {
  health: () =>
    api.get('/google-ads/health'),

  listCustomers: () =>
    api.get<CustomerIdItem[]>('/google-ads/customers'),

  getCustomer: (customerId: string) =>
    api.get<CustomerDetailOut>(`/google-ads/customers/${customerId}`),

  enrich: (data: EnrichRequest) =>
    api.post<EnrichResponse>('/google-ads/enrich', data),

  import: (data: ImportRequest) =>
    api.post<ImportResponse>('/google-ads/import', data),

  listCampaigns: (customerId: string) =>
    api.get<CampaignInfo[]>('/google-ads/campaigns', {
      params: { customer_id: customerId }
    }),

  listCampaignKeywords: (params: {
    customer_id: string
    campaign_id?: string
    min_impressions?: number
    date_range?: string
    limit?: number
  }) =>
    api.get<CampaignKeywordsResponse>('/google-ads/campaigns/keywords', { params }),

  importCampaignKeywords: (data: CampaignKeywordsImportRequest) =>
    api.post<ImportResponse>('/google-ads/campaigns/keywords/import', data),
}

// Tasks API
export const tasksApi = {
  getStatus: (taskId: string) =>
    api.get(`/tasks/${taskId}`),
  
  listByRun: (runId: number) =>
    api.get(`/tasks/run/${runId}`),
  
  list: (params?: { limit?: number; status_filter?: string }) =>
    api.get('/tasks/', { params }),
  
  cancel: (taskId: string) =>
    api.post(`/tasks/${taskId}/cancel`)
}


// Types
export interface KeywordCreate {
  keyword: string
  sector?: string
  target_market?: string
  monthly_volume?: number
  trend_12m?: number
  trend_3m?: number
  competition_score?: number
}

export interface ScoringRunCreate {
  run_name?: string
  ads_capacity: number
  seo_capacity: number
  social_capacity: number
  default_relevance_coefficient?: number
  company_url?: string
  competitor_urls?: string[]
  keyword_source_filter?: 'csv' | 'google_ads_api' | null
}

export interface CustomerIdItem {
  customer_id: string
}

export interface CustomerDetailOut {
  customer_id: string
  name: string
  currency_code: string
  time_zone: string
}

export interface EnrichRequest {
  customer_id: string
  seeds: string[]
  max_results: number
  min_volume: number
  language_id?: string
  geo_target_id?: string
}

export interface EnrichedKeywordOut {
  keyword: string
  avg_monthly_searches: number
  competition_index: number | null
  competition_score: number
  trend_3m: number
  trend_12m: number
  cpc_low: number
  cpc_high: number
}

export interface EnrichResponse {
  count: number
  truncated: boolean
  truncated_at: number | null
  truncated_reason: string | null
  keywords: EnrichedKeywordOut[]
}

export interface ImportRequest {
  customer_id: string
  seeds: string[]
  max_results: number
  min_volume: number
  sector?: string
  target_market?: string
}

export interface ImportResponse {
  created: number
  already_existing: number
  skipped_fuzzy: number
  truncated: boolean
  truncated_reason: string | null
  message: string
}

export interface CampaignInfo {
  campaign_id: string
  campaign_name: string
  status: string
}

export interface CampaignKeywordOut {
  keyword: string
  match_type: string
  campaign_name: string
  campaign_id: string
  ad_group_name: string
  impressions: number
  clicks: number
  cost: number
  avg_cpc: number
  ctr: number
}

export interface CampaignKeywordsResponse {
  count: number
  keywords: CampaignKeywordOut[]
}

export interface CampaignKeywordsImportRequest {
  customer_id: string
  campaign_id?: string
  min_impressions?: number
  date_range?: string
  limit?: number
  sector?: string
  target_market?: string
}

export interface ProfileAnalyzeRequest {
  company_url: string
  competitor_urls?: string[]
}

export interface ProfileConfirmRequest {
  profile_data?: Record<string, unknown>
}

export interface BrandProfileResponse {
  id: number
  scoring_run_id: number
  company_url: string
  competitor_urls?: string[]
  status: string
  profile_data?: Record<string, unknown>
  validation_data?: Record<string, unknown>
  source_pages?: Array<{ url: string; title: string; status: number }>
  error_message?: string | null
  created_at: string
  updated_at: string
}

export interface RelevanceComputeResponse {
  scoring_run_id: number
  total_keywords: number
  computed: number
  failed: number
  average_relevance: number
}

export interface KeywordRelevanceResponse {
  keyword_id: number
  keyword: string
  relevance_score: number
  matched_anchor?: string
  method: string
}

export interface ExportRequest {
  scoring_run_id: number
  format: 'docx' | 'pdf' | 'excel' | 'csv'
  sections?: string[]
  include_compliance_details?: boolean
}

export default api
