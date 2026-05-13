import axios, { AxiosError } from "axios";

const API_BASE = "/api/v1";

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    "Content-Type": "application/json",
  },
});

export interface ApiError {
  status: number;
  message: string;
  detail?: unknown;
}

api.interceptors.response.use(
  (res) => res,
  (err: AxiosError<{ detail?: unknown }>) => {
    const status = err.response?.status ?? 0;
    const raw = err.response?.data?.detail;
    let message: string;
    if (status === 401) {
      message = "Kimlik doğrulama gerekli. Lütfen oturum açın.";
    } else if (status === 403) {
      message = "Bu işlem için yetkiniz yok.";
    } else if (status === 404) {
      message = typeof raw === "string" ? raw : "İstenen kaynak bulunamadı.";
    } else if (status === 400) {
      message = typeof raw === "string" ? raw : "Geçersiz istek parametreleri.";
    } else if (status >= 500) {
      message = "Sunucu hatası oluştu. Lütfen daha sonra tekrar deneyin.";
    } else {
      message = typeof raw === "string" ? raw : err.message;
    }
    const apiError: ApiError = { status, message, detail: raw };
    return Promise.reject(apiError);
  }
);

// Keywords API
export const keywordsApi = {
  list: (params?: {
    skip?: number;
    limit?: number;
    active_only?: boolean;
    brand_profile_id?: number;
  }) => api.get("/keywords/", { params }),

  get: (id: number, brand_profile_id?: number) =>
    api.get(`/keywords/${id}`, { params: { brand_profile_id } }),

  create: (data: KeywordCreate, brand_profile_id?: number) =>
    api.post("/keywords", { ...data, brand_profile_id }),

  update: (id: number, data: Partial<KeywordCreate>, brand_profile_id?: number) =>
    api.put(`/keywords/${id}`, data, { params: { brand_profile_id } }),

  delete: (id: number, brand_profile_id?: number) =>
    api.delete(`/keywords/${id}`, { params: { brand_profile_id } }),

  import: (keywords: KeywordCreate[], brand_profile_id?: number) =>
    api.post("/keywords/import", { keywords, brand_profile_id }),

  deleteAll: (brand_profile_id?: number) =>
    api.delete("/keywords/all", { params: { brand_profile_id } }),
};

// Scoring API
export const scoringApi = {
  listRuns: (params?: { skip?: number; limit?: number; brand_profile_id?: number }) =>
    api.get("/scoring/runs", { params }),

  getRun: (id: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${id}`, { params: { brand_profile_id } }),

  createRun: (data: ScoringRunCreate) => api.post("/scoring/runs", data),

  executeRun: (id: number, brand_profile_id?: number) =>
    api.post(`/scoring/runs/${id}/execute`, undefined, { params: { brand_profile_id } }),

  getScores: (runId: number, limit?: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${runId}/scores`, { params: { limit, brand_profile_id } }),

  getTopByChannel: (runId: number, channel: string, limit?: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${runId}/top/${channel}`, { params: { limit, brand_profile_id } }),

  deleteRun: (runId: number, brand_profile_id?: number) =>
    api.delete(`/scoring/runs/${runId}`, { params: { brand_profile_id } }),

  exportXlsx: (runId: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${runId}/export/xlsx`, {
      params: { brand_profile_id },
      responseType: "blob",
    }),
};

// Workspace API
export const workspaceApi = {
  list: (includeArchived = false) =>
    api.get("/brand-profile/workspaces", {
      params: { include_archived: includeArchived },
    }),

  get: (id: number) => api.get(`/brand-profile/workspaces/${id}`),

  create: (data: WorkspaceCreateRequest) =>
    api.post("/brand-profile/workspaces", data),

  confirm: (id: number, data?: ProfileConfirmRequest) =>
    api.put(`/brand-profile/workspaces/${id}/confirm`, data || {}),

  archive: (id: number) => api.post(`/brand-profile/workspaces/${id}/archive`),

  restore: (id: number) => api.post(`/brand-profile/workspaces/${id}/restore`),

  refreshKeywords: (id: number, data?: WorkspaceKeywordRefreshRequest) =>
    api.post<WorkspaceKeywordRefreshResponse>(
      `/brand-profile/workspaces/${id}/keywords/refresh`,
      data || {},
    ),
};

// Brand Profile API
export const brandProfileApi = {
  getProfile: (runId: number, brand_profile_id?: number) =>
    api.get(`/brand-profile/runs/${runId}/profile`, { params: { brand_profile_id } }),

  analyzeProfile: (runId: number, data: ProfileAnalyzeRequest, brand_profile_id?: number) =>
    api.post(`/brand-profile/runs/${runId}/profile/analyze`, data, { params: { brand_profile_id } }),

  confirmProfile: (runId: number, data?: ProfileConfirmRequest, brand_profile_id?: number) =>
    api.put(`/brand-profile/runs/${runId}/profile/confirm`, data || {}, { params: { brand_profile_id } }),

  computeRelevance: (runId: number, brand_profile_id?: number) =>
    api.post(`/brand-profile/runs/${runId}/relevance/compute`, undefined, { params: { brand_profile_id } }),

  getRelevance: (runId: number, minScore = 0, brand_profile_id?: number) =>
    api.get(`/brand-profile/runs/${runId}/relevance`, {
      params: { min_score: minScore, brand_profile_id },
    }),
};

// Channels API
export const channelsApi = {
  assign: (runId: number, relevanceOverride?: number, brand_profile_id?: number) =>
    api.post(
      `/channels/runs/${runId}/assign`,
      relevanceOverride !== undefined
        ? { relevance_override: relevanceOverride }
        : undefined,
      { params: { brand_profile_id } },
    ),

  getPools: (runId: number, brand_profile_id?: number) =>
    api.get(`/channels/runs/${runId}/pools`, { params: { brand_profile_id } }),

  getPool: (runId: number, channel: string, brand_profile_id?: number) =>
    api.get(`/channels/runs/${runId}/pools/${channel}`, { params: { brand_profile_id } }),

  getStrategic: (runId: number) => api.get(`/channels/runs/${runId}/strategic`),
};

// Export API
export const exportApi = {
  create: (data: ExportRequest, brand_profile_id?: number) =>
    api.post("/export", data, { params: { brand_profile_id } }),

  status: (exportId: string, brand_profile_id?: number) =>
    api.get(`/export/${exportId}/status`, { params: { brand_profile_id } }),

  download: (exportId: string, brand_profile_id?: number) =>
    api.get(`/export/${exportId}/download`, {
      params: { brand_profile_id },
      responseType: "blob",
    }),

  listForRun: (runId: number, brand_profile_id?: number) =>
    api.get(`/export/run/${runId}`, { params: { brand_profile_id } }),
};

// Generation API
export const generationApi = {
  generateSeoGeo: (
    keywordIds: number[],
    contentType?: string,
    wordCount?: number,
  ) =>
    api.post("/generation/seo-geo", {
      keyword_ids: keywordIds,
      content_type: contentType || "blog_post",
      target_word_count: wordCount || 1500,
    }),

  listSeoGeo: (runId: number, limit = 100, brand_profile_id?: number) =>
    api.get(`/generation/seo-geo/list/${runId}`, { params: { limit, brand_profile_id } }),

  bulkSeoGeo: (runId: number, limit = 10, brand_profile_id?: number) =>
    api.post(`/generation/seo-geo/bulk/${runId}`, undefined, { params: { limit, brand_profile_id } }),

  getAdsRsa: (runId: number, brand_profile_id?: number) =>
    api.get(`/generation/ads/rsa/${runId}`, { params: { brand_profile_id } }),

  createAdsRsa: (data: AdsRsaRequest, brand_profile_id?: number) =>
    api.post("/generation/ads/rsa", data, { params: { brand_profile_id } }),

  createSocialCategories: (data: SocialCategoriesRequest, brand_profile_id?: number) =>
    api.post("/generation/social/categories", data, { params: { brand_profile_id } }),

  createSocialIdeas: (data: SocialIdeasRequest, brandName: string, brand_profile_id?: number) =>
    api.post(`/generation/social/ideas`, data, { params: { brand_name: brandName, brand_profile_id } }),

  createSocialContents: (data: SocialContentsRequest, brand_profile_id?: number) =>
    api.post("/generation/social/contents", data, { params: { brand_profile_id } }),
};

// Health API (uses base URL without /api/v1 prefix)
export const healthApi = {
  check: () => axios.get("/health"),
  detailed: () => axios.get("/health"),
};

// Google Ads API — URL Seed
export const googleAdsUrlSeedApi = {
  keywordIdeasByUrl: (data: UrlSeedRequest) =>
    api.post<UrlSeedResponse>("/google-ads/keyword-ideas-by-url", data),
};

// Google Ads API
export const googleAdsApi = {
  health: () => api.get("/google-ads/health"),

  listCustomers: () => api.get<CustomerIdItem[]>("/google-ads/customers"),

  getCustomer: (customerId: string) =>
    api.get<CustomerDetailOut>(`/google-ads/customers/${customerId}`),

  enrich: (data: EnrichRequest) =>
    api.post<EnrichResponse>("/google-ads/enrich", data),

  import: (data: ImportRequest) =>
    api.post<ImportResponse>("/google-ads/import", data),

  listCampaigns: (customerId: string) =>
    api.get<CampaignInfo[]>("/google-ads/campaigns", {
      params: { customer_id: customerId },
    }),

  listCampaignKeywords: (params: {
    customer_id: string;
    campaign_id?: string;
    min_impressions?: number;
    date_range?: string;
    limit?: number;
  }) =>
    api.get<CampaignKeywordsResponse>("/google-ads/campaigns/keywords", {
      params,
    }),

  importCampaignKeywords: (data: CampaignKeywordsImportRequest) =>
    api.post<ImportResponse>("/google-ads/campaigns/keywords/import", data),
};

// Tasks API
export const tasksApi = {
  getStatus: (taskId: string, brand_profile_id?: number) =>
    api.get(`/tasks/${taskId}`, { params: { brand_profile_id } }),

  listByRun: (runId: number, brand_profile_id?: number) =>
    api.get(`/tasks/run/${runId}`, { params: { brand_profile_id } }),

  list: (params?: { limit?: number; status_filter?: string; brand_profile_id?: number }) =>
    api.get("/tasks/", { params }),

  cancel: (taskId: string, brand_profile_id?: number) =>
    api.post(`/tasks/${taskId}/cancel`, undefined, { params: { brand_profile_id } }),
};

// Types
export interface KeywordCreate {
  keyword: string;
  sector?: string;
  target_market?: string;
  monthly_volume?: number;
  trend_12m?: number;
  trend_3m?: number;
  competition_score?: number;
  data_source?: "csv" | "google_ads_api" | "url_seed" | "manual";
  geo_target_id?: string;
  language_id?: string;
}

export interface ScoringRunCreate {
  run_name?: string;
  brand_profile_id?: number;
  ads_capacity: number;
  seo_capacity: number;
  social_capacity: number;
  default_relevance_coefficient?: number;
  company_url?: string;
  competitor_urls?: string[];
  keyword_source_filter?: "csv" | "google_ads_api" | null;
  enable_ads?: boolean;
  enable_seo?: boolean;
  enable_social?: boolean;
  keyword_selection_mode?: "all" | "top_n" | "specific";
  keyword_limit?: number;
  selected_keyword_ids?: number[];
  skip_relevance?: boolean;
}

export interface WorkspaceCreateRequest {
  name: string;
  company_url: string;
  competitor_urls?: string[];
  preliminary_info?: string;
  default_geo_target_id?: string;
  default_language_id?: string;
}

export interface WorkspaceResponse {
  id: number;
  name: string;
  company_url: string;
  status: string;
  profile_data: Record<string, unknown> | null;
  suggested_keywords: string[] | null;
  default_geo_target_id?: string | null;
  default_language_id?: string | null;
  deleted_at: string | null;
  created_at: string;
}

export interface WorkspaceKeywordRefreshRequest {
  customer_id?: string;
  max_results?: number;
  min_volume?: number;
  include_new_ideas?: boolean;
}

export interface WorkspaceKeywordRefreshResponse {
  workspace_id: number;
  refreshed: number;
  unchanged: number;
  added: number;
  removed: number;
  total_after: number;
}

export interface UrlSeedRequest {
  url: string;
  language_id?: string;
  geo_target_id?: string;
  max_results?: number;
  min_volume?: number;
  include_keyword_seed?: boolean;
  brand_profile_id: number;
  customer_id?: string;
  refresh?: boolean;
}

export interface UrlSeedResponse {
  ideas: UrlSeedIdea[];
  total: number;
  cached: boolean;
  cache_key: string | null;
  quota_remaining: number | null;
}

export interface UrlSeedIdea {
  keyword: string;
  monthly_volume: number;
  trend_3m: number;
  trend_12m: number;
  competition: number;
}

export interface CustomerIdItem {
  customer_id: string;
}

export interface CustomerDetailOut {
  customer_id: string;
  name: string;
  currency_code: string;
  time_zone: string;
}

export interface EnrichRequest {
  customer_id: string;
  seeds: string[];
  max_results: number;
  min_volume: number;
  language_id?: string;
  geo_target_id?: string;
}

export interface EnrichedKeywordOut {
  keyword: string;
  avg_monthly_searches: number;
  competition_index: number | null;
  competition_score: number;
  trend_3m: number;
  trend_12m: number;
  cpc_low: number;
  cpc_high: number;
}

export interface EnrichResponse {
  count: number;
  truncated: boolean;
  truncated_at: number | null;
  truncated_reason: string | null;
  keywords: EnrichedKeywordOut[];
}

export interface ImportRequest {
  customer_id: string;
  seeds: string[];
  max_results: number;
  min_volume: number;
  sector?: string;
  target_market?: string;
}

export interface ImportResponse {
  created: number;
  already_existing: number;
  skipped_fuzzy: number;
  truncated: boolean;
  truncated_reason: string | null;
  message: string;
}

export interface CampaignInfo {
  campaign_id: string;
  campaign_name: string;
  status: string;
}

export interface CampaignKeywordOut {
  keyword: string;
  match_type: string;
  campaign_name: string;
  campaign_id: string;
  ad_group_name: string;
  impressions: number;
  clicks: number;
  cost: number;
  avg_cpc: number;
  ctr: number;
}

export interface CampaignKeywordsResponse {
  count: number;
  keywords: CampaignKeywordOut[];
}

export interface CampaignKeywordsImportRequest {
  customer_id: string;
  campaign_id?: string;
  min_impressions?: number;
  date_range?: string;
  limit?: number;
  sector?: string;
  target_market?: string;
}

export interface ProfileAnalyzeRequest {
  company_url: string;
  competitor_urls?: string[];
}

export interface ProfileConfirmRequest {
  profile_data?: Record<string, unknown>;
}

export interface BrandProfileResponse {
  id: number;
  scoring_run_id: number;
  company_url: string;
  competitor_urls?: string[];
  status: string;
  profile_data?: Record<string, unknown>;
  validation_data?: Record<string, unknown>;
  source_pages?: Array<{ url: string; title: string; status: number }>;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RelevanceComputeResponse {
  scoring_run_id: number;
  total_keywords: number;
  computed: number;
  failed: number;
  average_relevance: number;
}

export interface KeywordRelevanceResponse {
  keyword_id: number;
  keyword: string;
  relevance_score: number;
  matched_anchor?: string;
  method: string;
}

export interface ExportRequest {
  scoring_run_id: number;
  format: "docx" | "pdf" | "excel" | "csv";
  sections?: string[];
  include_compliance_details?: boolean;
  include_stale_content?: boolean;
}

export interface AdsRsaRequest {
  scoring_run_id: number;
  brand_name?: string;
  website_url?: string;
  brand_usp?: string;
}

export interface SocialCategoriesRequest {
  scoring_run_id: number;
  brand_name?: string;
  brand_context?: string;
  max_categories?: number;
}

export interface SocialIdeasRequest {
  category_ids: number[];
  ideas_per_category?: number;
}

export interface SocialContentsRequest {
  idea_ids: number[];
  brand_name?: string;
}

export default api;
