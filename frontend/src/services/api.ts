import axios, { AxiosError } from 'axios'

const API_BASE = '/api/v1'

// Backend'de API_KEY set ise tum /api/v1/* istekleri X-API-Key header'i ister
// (app/core/security.py). Anahtar build sirasinda VITE_API_KEY env'den gelir.
const API_KEY: string | undefined = import.meta.env.VITE_API_KEY

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
    ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
  },
})

export interface ApiError {
  status: number
  message: string
  detail?: unknown
}

// ── Plan v13: POLICY_STALE 409 yardımcıları ──
// Backend guard 409 detayını NESNE olarak döndürür:
// {code: 'POLICY_STALE', message, channel_pool_stale, policy_stale, relevance_stale}
// Ekranlar tüm 409'ları tek mesaja indirmemeli — bayat havuz farklı bir durumdur.

export interface PolicyStaleDetail {
  code: 'POLICY_STALE'
  message: string
  channel_pool_stale: boolean
  policy_stale: boolean
  relevance_stale: boolean
  // v2.1 Faz C: üçüncü eksen — yalnız algorithm_version=v2_1 run'larda dolar
  strategy_stale?: boolean
}

function detailFromError(err: unknown): unknown {
  if (!err || typeof err !== 'object') return undefined
  // Interceptor'dan geçen ApiError şekli
  if ('detail' in err) return (err as ApiError).detail
  // Ham AxiosError şekli (interceptor'suz test/edge yolları)
  const resp = (err as { response?: { data?: { detail?: unknown } } }).response
  return resp?.data?.detail
}

export function policyStaleFromError(err: unknown): PolicyStaleDetail | null {
  const detail = detailFromError(err)
  if (
    detail &&
    typeof detail === 'object' &&
    (detail as { code?: unknown }).code === 'POLICY_STALE'
  ) {
    const d = detail as Partial<PolicyStaleDetail>
    return {
      code: 'POLICY_STALE',
      message:
        typeof d.message === 'string'
          ? d.message
          : 'Kanal havuzu güncel değil — kanal atamasını yenileyin.',
      channel_pool_stale: Boolean(d.channel_pool_stale),
      policy_stale: Boolean(d.policy_stale),
      relevance_stale: Boolean(d.relevance_stale),
      strategy_stale: Boolean(d.strategy_stale),
    }
  }
  return null
}

// Tipli hata sözleşmesi ({code, message, ...}) taşıyan HERHANGİ bir 400/409
// yanıtından yalnız `code` alanını okur (plan_v3_lokasyon_filtresi.md §5.7:
// LOCATION_PREVIEW_REQUIRED / LOCATION_PREVIEW_DRAFT_REJECTED /
// LOCATION_PREVIEW_STALE gibi lokasyon kapısı kodları dahil). `policyStaleFromError`
// yalnız POLICY_STALE'a özeldir; bu genel amaçlı okuyucudur.
export function apiErrorCode(err: unknown): string | null {
  const detail = detailFromError(err)
  if (
    detail &&
    typeof detail === 'object' &&
    typeof (detail as { code?: unknown }).code === 'string'
  ) {
    return (detail as { code: string }).code
  }
  return null
}

export function apiErrorMessage(err: unknown, fallback = 'Bir hata oluştu'): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message
    if (typeof msg === 'string' && msg) return msg
  }
  const detail = detailFromError(err)
  if (typeof detail === 'string') return detail
  if (
    detail &&
    typeof detail === 'object' &&
    typeof (detail as { message?: unknown }).message === 'string'
  ) {
    return (detail as { message: string }).message
  }
  return fallback
}

export function apiErrorStatus(err: unknown): number {
  if (err && typeof err === 'object') {
    if (typeof (err as ApiError).status === 'number') return (err as ApiError).status
    const resp = (err as { response?: { status?: number } }).response
    if (typeof resp?.status === 'number') return resp.status
  }
  return 0
}

// FastAPI 422 detayını (Pydantic hata listesi) okunur mesaja çevirir.
// Hem `{detail: [...]}`ten gelen array'i hem doğrudan array/string formunu kapsar.
export function formatValidationDetail(raw: unknown): string | null {
  if (typeof raw === 'string') return raw
  if (!Array.isArray(raw)) return null
  const msgs = raw
    .map((item) => {
      const row = item as { loc?: unknown[]; msg?: string }
      if (!row?.msg) return ''
      const field = Array.isArray(row.loc) ? String(row.loc[row.loc.length - 1] ?? '') : ''
      return field && field !== 'body' ? `${field}: ${row.msg}` : row.msg
    })
    .filter(Boolean)
  return msgs.length ? msgs.join(' · ') : null
}

// Blob response'u tarayıcı indirmesine çevirir (plan v4: Export.tsx'ten ortak util'e).
// Dosya adı Content-Disposition'dan okunur; yoksa fallbackName kullanılır.
export function downloadBlobResponse(
  res: { data: BlobPart; headers?: Record<string, unknown> },
  fallbackName: string
) {
  let filename = fallbackName
  const contentDisposition = res.headers?.['content-disposition']
  if (typeof contentDisposition === 'string') {
    const match = contentDisposition.match(/filename="?([^";\n]+)"?/)
    if (match) filename = match[1]
  }
  const url = window.URL.createObjectURL(new Blob([res.data]))
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

api.interceptors.response.use(
  (res) => res,
  async (err: AxiosError<{ detail?: unknown }>) => {
    const status = err.response?.status ?? 0

    // responseType:'blob' isteklerinde (xlsx/dosya indirme) hata gövdesi de
    // Blob gelir — JSON'a çevrilmezse detail okunamaz ve 409 POLICY_STALE /
    // 422 NO_CONTENT mesajları "Request failed..." jeneriğine düşer
    // (codex post-review #4).
    let data: unknown = err.response?.data
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      try {
        data = JSON.parse(await data.text())
      } catch {
        data = undefined
      }
    }
    const raw = (data as { detail?: unknown } | undefined)?.detail

    // Auto-clear stale activeWorkspace when backend says workspace doesn't exist
    if (
      status === 404 &&
      typeof raw === 'string' &&
      /Marka çalışması.*bulunamadı|workspace.*not found/i.test(raw)
    ) {
      try {
        const stored = window.localStorage.getItem('brand-store')
        if (stored) {
          window.localStorage.removeItem('brand-store')
          // Reload once to drop in-memory store state and re-render
          if (!sessionStorage.getItem('workspace-cleanup-reloaded')) {
            sessionStorage.setItem('workspace-cleanup-reloaded', '1')
            window.location.reload()
          }
        }
      } catch {
        // ignore localStorage access errors
      }
    }

    // Tipli hata sözleşmeleri ({code, message} — NO_CONTENT vb.) kendi
    // mesajını taşır; Pydantic doğrulama listesinden ayrı ele alınır.
    const detailMessage =
      raw && typeof raw === 'object' && typeof (raw as { message?: unknown }).message === 'string'
        ? (raw as { message: string }).message
        : ''

    let message: string
    if (status === 422) {
      message =
        formatValidationDetail(raw) || detailMessage || 'Gönderilen değerler doğrulamadan geçemedi.'
    } else if (status === 401) {
      message = 'Kimlik doğrulama gerekli. Lütfen oturum açın.'
    } else if (status === 403) {
      message = 'Bu işlem için yetkiniz yok.'
    } else if (status === 404) {
      message = typeof raw === 'string' ? raw : 'İstenen kaynak bulunamadı.'
    } else if (status === 400) {
      message = typeof raw === 'string' ? raw : 'Geçersiz istek parametreleri.'
    } else if (status === 503 && typeof raw === 'string') {
      message = raw
    } else if (status >= 500) {
      message = 'Sunucu hatası oluştu. Lütfen daha sonra tekrar deneyin.'
    } else {
      message = typeof raw === 'string' ? raw : detailMessage || err.message
    }
    const apiError: ApiError = { status, message, detail: raw }
    return Promise.reject(apiError)
  }
)

// Keywords API
export const keywordsApi = {
  list: (params?: {
    skip?: number
    limit?: number
    active_only?: boolean
    brand_profile_id?: number
  }) => api.get('/keywords/', { params }),

  get: (id: number, brand_profile_id?: number) =>
    api.get(`/keywords/${id}`, { params: { brand_profile_id } }),

  create: (data: KeywordCreate, brand_profile_id?: number) =>
    api.post('/keywords', { ...data, brand_profile_id }),

  update: (id: number, data: Partial<KeywordCreate>, brand_profile_id?: number) =>
    api.put(`/keywords/${id}`, data, { params: { brand_profile_id } }),

  delete: (wk_id: number, brand_profile_id?: number) =>
    api.delete(`/keywords/${wk_id}`, { params: { brand_profile_id } }),

  import: (keywords: KeywordCreate[], brand_profile_id?: number) =>
    api.post<KeywordImportResponse>('/keywords/import', { keywords, brand_profile_id }),

  uploadCsv: (file: File, brand_profile_id: number) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('brand_profile_id', String(brand_profile_id))
    formData.append('dry_run', 'true')
    return api.post<KeywordUploadCsvResponse>('/keywords/upload-csv', formData)
  },

  commitCsvUpload: (brand_profile_id: number, dry_run_token: string) =>
    api.post<KeywordUploadCsvResponse>('/keywords/upload-csv/commit', {
      brand_profile_id,
      dry_run_token,
    }),

  deleteAll: (brand_profile_id?: number) =>
    api.delete('/keywords/all', { params: { brand_profile_id } }),

  // Plan v4 (export dağıtımı): aktif kelime havuzunu tek tıkla XLSX indir
  exportPoolXlsx: (brand_profile_id?: number) =>
    api.get('/keywords/pool/export.xlsx', {
      params: { brand_profile_id },
      responseType: 'blob',
    }),
}

// Scoring API
export type ScoringSortBy =
  | 'ads_score'
  | 'seo_score'
  | 'social_score'
  | 'ads_rank'
  | 'seo_rank'
  | 'social_rank'

export type SortDir = 'asc' | 'desc'

export const scoringApi = {
  // v2.1 deney bayrağı görünürlüğü (Codex Faz E #2): UI seçeneği 409
  // duvarına çarpmadan gizleyebilsin/kapatabilsin
  getCapabilities: () =>
    api.get<{ v21_experiment_enabled: boolean; engine_v3_enabled?: boolean }>(
      '/scoring/capabilities'
    ),

  listRuns: (params?: { skip?: number; limit?: number; brand_profile_id?: number }) =>
    api.get('/scoring/runs', { params }),

  getRun: (id: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${id}`, { params: { brand_profile_id } }),

  createRun: (data: ScoringRunCreate) => api.post('/scoring/runs', data),

  executeRun: (id: number, brand_profile_id?: number) =>
    api.post(`/scoring/runs/${id}/execute`, undefined, { params: { brand_profile_id } }),

  getScores: (params: {
    runId: number
    brand_profile_id?: number
    limit?: number
    offset?: number
    sort_by?: ScoringSortBy
    sort_dir?: SortDir
  }) => {
    const { runId, ...query } = params
    return api.get(`/scoring/runs/${runId}/scores`, { params: query })
  },

  getTopByChannel: (runId: number, channel: string, limit?: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${runId}/top/${channel}`, { params: { limit, brand_profile_id } }),

  deleteRun: (runId: number, brand_profile_id?: number) =>
    api.delete(`/scoring/runs/${runId}`, { params: { brand_profile_id } }),

  exportXlsx: (runId: number, brand_profile_id?: number) =>
    api.get(`/scoring/runs/${runId}/export/xlsx`, {
      params: { brand_profile_id },
      responseType: 'blob',
    }),
}

// Workspace API
export const workspaceApi = {
  list: (includeArchived = false) =>
    api.get('/brand-profile/workspaces', {
      params: { include_archived: includeArchived },
    }),

  get: (id: number) => api.get(`/brand-profile/workspaces/${id}`),

  create: (data: WorkspaceCreateRequest) => api.post('/brand-profile/workspaces', data),

  approveKeywords: (id: number, data: WorkspaceKeywordApproveRequest) =>
    api.put<WorkspaceResponse>(`/brand-profile/workspaces/${id}/keywords/approve`, data),

  approveProfile: (id: number, data: WorkspaceProfileApproveRequest) =>
    api.put<WorkspaceResponse>(`/brand-profile/workspaces/${id}/profile/approve`, data),

  anchorsPreview: (id: number, data?: AnchorPreviewRequest) =>
    api.post<AnchorPreviewResponse>(`/brand-profile/workspaces/${id}/anchors/preview`, data || {}),

  confirm: (id: number, data?: ProfileConfirmRequest) =>
    api.put<WorkspaceResponse>(`/brand-profile/workspaces/${id}/confirm`, data || {}),

  archive: (id: number) => api.post(`/brand-profile/workspaces/${id}/archive`),

  restore: (id: number) => api.post(`/brand-profile/workspaces/${id}/restore`),

  refreshKeywords: (id: number, data?: WorkspaceKeywordRefreshRequest) =>
    api.post<WorkspaceKeywordRefreshResponse>(
      `/brand-profile/workspaces/${id}/keywords/refresh`,
      data || {}
    ),

  // Lokasyon filtresi — salt-okunur, kaydetmeyen önizleme (plan §5.7).
  // AI çağırmaz, hiçbir kayıt yazmaz.
  previewLocationFilter: (id: number, data?: LocationFilterPreviewRequest) =>
    api.post<LocationFilterPreviewResponse>(
      `/brand-profile/workspaces/${id}/policy/location-preview`,
      data || {}
    ),
}

// Brand Profile API
export const brandProfileApi = {
  getProfile: (runId: number, brand_profile_id?: number) =>
    api.get(`/brand-profile/runs/${runId}/profile`, { params: { brand_profile_id } }),

  // ── Policy yönetimi (plan A+B + v13): rakip + konu politikası ──
  getPolicy: (workspaceId: number) =>
    api.get<WorkspacePolicyResponse>(`/brand-profile/workspaces/${workspaceId}/policy`),

  upsertPolicyTerm: (
    workspaceId: number,
    term: string,
    status: 'approved' | 'rejected',
    kind: 'competitor' | 'topic_term' | 'topic_alias'
  ) => api.post(`/brand-profile/workspaces/${workspaceId}/policy/terms`, { term, status, kind }),

  setCompetitorPolicy: (
    workspaceId: number,
    policy: { ads: string; seo: string; social: string }
  ) => api.put(`/brand-profile/workspaces/${workspaceId}/policy/competitor-policy`, policy),

  // v13: kalıcı rakip/konu incelemesi (onay sonrası + legacy yüzey)
  policyReview: (workspaceId: number, data: PolicyReviewRequest) =>
    api.put(`/brand-profile/workspaces/${workspaceId}/policy/review`, data),

  // v13: async marka-adı tespiti (202 + poll; fail-open kolaylık)
  startCompetitorPreview: (workspaceId: number, urls: string[]) =>
    api.post<{ task_id: string; status: string }>(
      `/brand-profile/workspaces/${workspaceId}/policy/competitors/preview`,
      { urls }
    ),

  getCompetitorPreview: (workspaceId: number, taskId: string) =>
    api.get<{
      task_id: string
      status: string
      result?: CompetitorPreviewEntry[] | null
      error_message?: string | null
    }>(`/brand-profile/workspaces/${workspaceId}/policy/competitors/preview/${taskId}`),

  startCompetitorDiscovery: (workspaceId: number, forceRefresh = false) =>
    api.post<{ task_id: string; status: string; reused: boolean }>(
      `/brand-profile/workspaces/${workspaceId}/policy/competitor-discovery`,
      { force_refresh: forceRefresh }
    ),

  getCompetitorDiscovery: (workspaceId: number, taskId: string) =>
    api.get<CompetitorDiscoveryStatus>(
      `/brand-profile/workspaces/${workspaceId}/policy/competitor-discovery/${taskId}`
    ),

  decideDiscoveredCompetitors: (workspaceId: number, decisions: CompetitorDiscoveryDecision[]) =>
    api.post<WorkspacePolicyResponse>(
      `/brand-profile/workspaces/${workspaceId}/policy/competitor-discovery/decisions`,
      { decisions }
    ),

  computeRelevance: (runId: number, brand_profile_id?: number) =>
    api.post(`/brand-profile/runs/${runId}/relevance/compute`, undefined, {
      params: { brand_profile_id },
    }),

  getRelevance: (runId: number, minScore = 0, brand_profile_id?: number) =>
    api.get(`/brand-profile/runs/${runId}/relevance`, {
      params: { min_score: minScore, brand_profile_id },
    }),

  // Lokasyon denetimi (plan §5.6) — TAMAMLANMIŞ (channel_assigned/completed)
  // bir run'ın MÜHÜRLÜ evren + politikasına karşı salt-okunur, sayfalı
  // per-keyword karar listesi. "Bu keyword neden havuzda değil?" sorusu için.
  getLocationAudit: (
    runId: number,
    params: { brand_profile_id: number; limit?: number; offset?: number; only_excluded?: boolean }
  ) => api.get<LocationAuditResponse>(`/brand-profile/runs/${runId}/location-audit`, { params }),
}

// Corpus screening (plan_corpus_screening_production) — UI sözleşmesi.
// ÖNEMLİ: kullanıcı YALNIZ `enforced_hard_cap_usd` onaylar; downstream
// tahmini ve toplam maruziyet BİLGİ amaçlıdır (ledger'a bağlı değildir).
export type ScreeningMode = 'off' | 'shadow' | 'assistive'

export interface AssignmentPreflight {
  requires_approval: boolean
  screening_mode: ScreeningMode
  screening_enabled?: boolean
  scoring_run_id?: number
  preflight_sha256?: string
  universe_size?: number
  planned_screening_requests?: number
  screening_expected_rough_usd?: number | null
  screening_single_pass_ceiling_usd?: number
  screening_hard_cap_usd?: number
  enforced_hard_cap_usd?: number
  enforced_scopes?: string[]
  downstream_hard_cap_usd?: number
  downstream_enforced?: boolean
  combined_exposure_usd?: number
  applied_candidate_multiplier?: number
  counterfactual_target_multiplier?: number
  applied_screening_channels?: string[]
  capacities?: Record<string, number>
}

export interface ScreeningRunStatus {
  exists: boolean
  screening_job_id?: number
  status?: string
  error_code?: string | null
  screening_mode?: ScreeningMode | null
  cost_usd?: number | null
  provider_calls?: number | null
  planned_requests?: number | null
  ceiling_charges?: number | null
  coverage_resolved?: number | null
  universe_size?: number
  unresolved?: number | null
  contract_violations?: number | null
  applied_to_live_pool?: boolean
  counterfactual?: Record<string, unknown> | null
}

export interface ScreeningApproval {
  screening_mode: ScreeningMode
  preflight_sha256: string
  approved_screening_hard_cap_usd: number
}

// Channels API
export const channelsApi = {
  assign: (
    runId: number,
    relevanceOverride?: number,
    brand_profile_id?: number,
    approval?: ScreeningApproval
  ) =>
    api.post(
      `/channels/runs/${runId}/assign`,
      {
        ...(relevanceOverride !== undefined ? { relevance_override: relevanceOverride } : {}),
        ...(approval ?? {}),
      },
      { params: { brand_profile_id } }
    ),

  // Son tarama işinin durumu/sonucu (salt okunur)
  getScreeningStatus: (runId: number, brand_profile_id: number) =>
    api.get<ScreeningRunStatus>(`/channels/runs/${runId}/screening`, {
      params: { brand_profile_id },
    }),

  // Hiçbir şey başlatmaz; ücretli çağrı YAPMAZ
  getAssignmentPreflight: (
    runId: number,
    brand_profile_id: number,
    mode?: ScreeningMode,
    relevanceOverride?: number
  ) =>
    api.get<AssignmentPreflight>(`/channels/runs/${runId}/assignment-preflight`, {
      params: {
        brand_profile_id,
        ...(mode ? { mode } : {}),
        // Katsayi SHA bilesenidir: dispatch ile AYNI deger gonderilmeli
        ...(relevanceOverride !== undefined ? { relevance_override: relevanceOverride } : {}),
      },
    }),

  getPools: (runId: number, brand_profile_id?: number) =>
    api.get(`/channels/runs/${runId}/pools`, { params: { brand_profile_id } }),

  getPool: (runId: number, channel: string, brand_profile_id?: number) =>
    api.get(`/channels/runs/${runId}/pools/${channel}`, { params: { brand_profile_id } }),

  removePoolItem: (runId: number, poolId: number, brand_profile_id?: number) =>
    api.delete(`/channels/runs/${runId}/pools/${poolId}`, { params: { brand_profile_id } }),

  getStrategic: (runId: number) => api.get(`/channels/runs/${runId}/strategic`),

  // Plan v4 (export dağıtımı): kanal havuzunu XLSX indir (stale havuzda 409)
  exportPoolXlsx: (runId: number, channel: string, brand_profile_id?: number) =>
    api.get(`/channels/runs/${runId}/pools/${channel}/export.xlsx`, {
      params: { brand_profile_id },
      responseType: 'blob',
    }),
}

// Export API
export const exportApi = {
  create: (data: ExportRequest, brand_profile_id?: number) =>
    api.post('/export/', data, { params: { brand_profile_id } }),

  status: (exportId: string, brand_profile_id?: number) =>
    api.get(`/export/${exportId}/status`, { params: { brand_profile_id } }),

  download: (exportId: string, brand_profile_id?: number) =>
    api.get(`/export/${exportId}/download`, {
      params: { brand_profile_id },
      responseType: 'blob',
    }),

  listForRun: (runId: number, brand_profile_id?: number, limit?: number) =>
    api.get(`/export/run/${runId}`, { params: { brand_profile_id, limit } }),
}

// Generation API
export const generationApi = {
  generateSeoGeo: (keywordIds: number[], contentType?: string, wordCount?: number) =>
    api.post('/generation/seo-geo', {
      keyword_ids: keywordIds,
      content_type: contentType || 'blog_post',
      target_word_count: wordCount || 1500,
    }),

  listSeoGeo: (runId: number, limit = 100, brand_profile_id?: number) =>
    api.get(`/generation/seo-geo/list/${runId}`, { params: { limit, brand_profile_id } }),

  bulkSeoGeo: (runId: number, limit = 10, brand_profile_id?: number, tone = 'informative') =>
    api.post(`/generation/seo-geo/bulk/${runId}`, undefined, {
      params: { limit, brand_profile_id, tone },
    }),

  getAdsRsa: (runId: number, brand_profile_id?: number, set_id?: number) =>
    api.get(`/generation/ads/rsa/${runId}`, { params: { brand_profile_id, set_id } }),

  createAdsRsa: (data: AdsRsaRequest, brand_profile_id?: number) =>
    api.post('/generation/ads/rsa', data, { params: { brand_profile_id } }),

  // ADS set versiyonlama (Faz E): geçmiş + onay akışı
  getAdsSets: (runId: number, brand_profile_id?: number) =>
    api.get('/generation/ads/sets', { params: { scoring_run_id: runId, brand_profile_id } }),

  activateAdsSet: (setId: number, brand_profile_id?: number) =>
    api.post(`/generation/ads/sets/${setId}/activate`, undefined, {
      params: { brand_profile_id },
    }),

  regenerateAdGroup: (groupId: number, brand_profile_id?: number) =>
    api.post(`/generation/ads/rsa/group/${groupId}/regenerate`, undefined, {
      params: { brand_profile_id },
    }),

  createSocialCategories: (data: SocialCategoriesRequest, brand_profile_id?: number) =>
    api.post('/generation/social/categories', data, { params: { brand_profile_id } }),

  createSocialIdeas: (data: SocialIdeasRequest, brandName: string, brand_profile_id?: number) =>
    api.post(`/generation/social/ideas`, data, {
      params: { brand_name: brandName, brand_profile_id },
    }),

  createSocialContents: (data: SocialContentsRequest, brand_profile_id?: number) =>
    api.post('/generation/social/contents', data, { params: { brand_profile_id } }),

  // P7: içerik fazı Celery'de koşar; {task_id, scoring_run_id} döner
  createSocialContentsAsync: (data: SocialContentsRequest, brand_profile_id?: number) =>
    api.post('/generation/social/contents/async', data, { params: { brand_profile_id } }),

  getSocialResults: (runId: number, brand_profile_id?: number) =>
    api.get(`/generation/social/${runId}`, { params: { brand_profile_id } }),
}

// Health API (uses base URL without /api/v1 prefix)
export const healthApi = {
  check: () => axios.get('/health'),
  detailed: () => axios.get('/health'),
}

// Google Ads API — URL Seed
export const googleAdsUrlSeedApi = {
  keywordIdeasByUrl: (data: UrlSeedRequest) =>
    api.post<UrlSeedResponse>('/google-ads/keyword-ideas-by-url', data),
}

// Google Ads API
export const googleAdsApi = {
  health: () => api.get('/google-ads/health'),

  listCustomers: () => api.get<CustomerIdItem[]>('/google-ads/customers'),

  getCustomer: (customerId: string) =>
    api.get<CustomerDetailOut>(`/google-ads/customers/${customerId}`),

  enrich: (data: EnrichRequest) => api.post<EnrichResponse>('/google-ads/enrich', data),

  import: (data: ImportRequest) => api.post<ImportResponse>('/google-ads/import', data),

  listCampaigns: (customerId: string, refresh = false) =>
    api.get<CampaignInfo[]>('/google-ads/campaigns', {
      params: { customer_id: customerId, refresh },
    }),

  listCampaignKeywords: (params: {
    customer_id: string
    campaign_id?: string
    min_impressions?: number
    date_range?: string
    limit?: number
    refresh?: boolean
  }) =>
    api.get<CampaignKeywordsResponse>('/google-ads/campaigns/keywords', {
      params,
    }),

  importCampaignKeywords: (data: CampaignKeywordsImportRequest) =>
    api.post<ImportResponse>('/google-ads/campaigns/keywords/import', data),
}

// Tasks API
/** `GET /tasks/{id}` ve `GET /tasks/run/{run_id}` görev durumu. */
export interface TaskStatusInfo {
  task_id: string
  task_type?: string | null
  /** Görevin bağlı olduğu scoring run (eski kayıtlarda null olabilir) */
  scoring_run_id?: number | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  result_data?: Record<string, unknown>
  error_message?: string
}

export const tasksApi = {
  getStatus: (taskId: string, brand_profile_id?: number) =>
    api.get<TaskStatusInfo>(`/tasks/${taskId}`, { params: { brand_profile_id } }),

  listByRun: (runId: number, brand_profile_id?: number) =>
    api.get(`/tasks/run/${runId}`, { params: { brand_profile_id } }),

  list: (params?: { limit?: number; status_filter?: string; brand_profile_id?: number }) =>
    api.get('/tasks/', { params }),

  cancel: (taskId: string, brand_profile_id?: number) =>
    api.post(`/tasks/${taskId}/cancel`, undefined, { params: { brand_profile_id } }),
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
  data_source?: 'csv' | 'google_ads_api' | 'url_seed' | 'manual'
  geo_target_id?: string
  language_id?: string
  // YALNIZ yasaklı-tema kapısını atlar (Geri al akışı); duplicate/limit'i atlamaz
  force_include?: boolean
}

export interface SkippedKeywordDetail {
  keyword: string
  reason:
    | 'skipped_exact'
    | 'skipped_fuzzy'
    | 'batch_duplicate'
    | 'skipped_theme'
    | 'limit_exceeded'
    | string
  matched?: string | null
  monthly_volume?: number | null
  trend_3m?: number | null
  trend_12m?: number | null
  competition_score?: number | null
  data_source?: 'csv' | 'google_ads_api' | 'url_seed' | 'manual' | null
  geo_target_id?: string | null
  language_id?: string | null
}

export interface KeywordImportResponse {
  created: number
  skipped: number
  requested: number
  total_before: number
  total_after: number
  created_new: number
  linked_existing: number
  fuzzy_merged_in_batch?: number
  skipped_theme?: number
  skipped_limit?: number
  pool_limit?: number
  pool_total?: number
  skipped_details?: SkippedKeywordDetail[]
  message: string
}

export interface KeywordImportDetail {
  keyword?: string
  source_file?: string
  source_row?: number
  reason: string
  matched_keyword?: string
  matched_keyword_id?: number
  matched_in_workspace?: boolean
  match_ratio?: number
  monthly_volume?: number
  trend_3m?: number
  trend_12m?: number
  competition_score?: number
  data_source?: string
  geo_target_id?: string
  language_id?: string
  competition_index?: number
  top_bid_high?: number
}

export interface KeywordUploadCsvResponse extends KeywordImportResponse {
  parsed: number
  accepted: number
  skipped_exact: number
  skipped_fuzzy: number
  kept_fuzzy_different_metrics: number
  kept_fuzzy_default_metrics: number
  exact_duplicate_different_metrics: number
  skipped_due_to_race: number
  dry_run_token?: string | null
  parser_meta: Record<string, unknown>
  accepted_keywords: KeywordImportDetail[]
  excluded_keywords: KeywordImportDetail[]
  exact_duplicates: KeywordImportDetail[]
  exact_duplicates_different_metrics: KeywordImportDetail[]
  fuzzy_skipped: KeywordImportDetail[]
  fuzzy_kept: KeywordImportDetail[]
  junk_rows: KeywordImportDetail[]
  skipped_theme?: number
  theme_excluded?: KeywordImportDetail[]
}

export interface ScoringRunCreate {
  run_name?: string
  brand_profile_id?: number
  ads_capacity: number
  seo_capacity: number
  social_capacity: number
  default_relevance_coefficient?: number
  company_url?: string
  competitor_urls?: string[]
  keyword_source_filter?: 'csv' | 'google_ads_api' | null
  enable_ads?: boolean
  enable_seo?: boolean
  enable_social?: boolean
  keyword_selection_mode?: 'all' | 'top_n' | 'specific'
  keyword_limit?: number
  selected_keyword_ids?: number[]
  skip_relevance?: boolean
  auto_assign_channels?: boolean
  algorithm_version?: 'v2' | 'v2_1' | 'v3'
  // Lokasyon filtresi kapısı (plan_v3_lokasyon_filtresi.md §5.7,
  // app/schemas/location_gate.py::LocationPreviewTokenFields). Workspace'in
  // kayıtlı `location_filter_mode` alanı `none` ise HİÇBİRİ gönderilmez —
  // mevcut davranış birebir korunur. Aktif moddaysa üçü de
  // `/policy/location-preview`'dan (draft alan göndermeden) gelen TAZE
  // yanıttan taşınır.
  location_universe_fingerprint?: string
  location_policy_fingerprint?: string
  location_preview_is_saved_policy?: boolean
}

export interface WorkspaceCreateRequest {
  name?: string
  company_url: string
  competitor_urls?: string[]
  preliminary_info?: string
  must_have_info?: string
  excluded_info?: string
  default_geo_target_id?: string
  default_language_id?: string
  flow_version?: 'legacy' | 'profile_first'
}

export interface WorkspaceResponse {
  id: number
  name: string
  company_url: string
  competitor_urls?: string[] | null
  competitor_url_decisions?: Record<string, CompetitorUrlDecision> | null
  status: string
  onboarding_flow?: 'legacy' | 'profile_first'
  profile_approved_at?: string | null
  profile_data: Record<string, unknown> | null
  validation_data?: Record<string, unknown> | null
  suggested_keywords: string[] | null
  preliminary_info?: string | null
  excluded_info?: string | null
  default_geo_target_id?: string | null
  default_language_id?: string | null
  deleted_at: string | null
  created_at: string
  updated_at?: string
}

export interface CompetitorDecisionPayload {
  url: string
  term: string
  decision: 'block' | 'not_competitor'
}

export interface WorkspaceProfileApproveRequest {
  profile_data?: Record<string, unknown>
  competitor_urls?: string[]
  competitor_decisions?: CompetitorDecisionPayload[]
  must_have_info?: string
  excluded_info?: string
  default_geo_target_id?: string
  default_language_id?: string
  rerun_keywords?: boolean
}

export interface PolicyTermEntry {
  term: string
  status: 'approved' | 'rejected' | 'suggested' | string
  source?: string
  manual_approved: boolean
  source_urls: string[]
  legacy: boolean
  discovery_id?: string
  candidate_domain?: string
  candidate_url?: string
  rationale?: string
  confidence?: 'high' | 'medium'
  verification_status?: 'direct' | 'nearby'
  evidence_urls?: string[]
  discovered_at?: string
  profile_fingerprint?: string
}

export interface CompetitorDiscoveryDecision {
  discovery_id: string
  decision: 'approved' | 'rejected'
  term: string
}

export interface CompetitorDiscoveryStatus {
  task_id: string
  status: string
  progress: number
  candidates: PolicyTermEntry[]
  warnings: string[]
  fresh_channel_run_count: number
  cost?: {
    grounding_queries?: number
    grounding_list_price_usd?: number
    usage?: Record<string, number>
  } | null
  fallback_used?: boolean
  code?: string | null
  error_message?: string | null
}

export interface CompetitorUrlDecision {
  url: string
  term: string
  decision: 'block' | 'not_competitor'
  updated_at?: string
}

export interface WorkspacePolicyResponse {
  competitor_terms: PolicyTermEntry[]
  competitor_policy: { ads: string; seo: string; social: string }
  topic_policy: {
    excluded_terms: { term: string; status: string }[]
    excluded_aliases: { term: string; status: string }[]
  }
  competitor_url_decisions: Record<string, CompetitorUrlDecision>
  policy_version: number
  anchor_version: number
  discovery_impact: {
    fresh_channel_run_count: number
    profile_fingerprint?: string
    has_stale_suggestions?: boolean
  }
}

export interface PolicyReviewRequest {
  competitor_urls?: string[]
  competitor_decisions?: CompetitorDecisionPayload[]
  excluded_info?: string
}

export interface CompetitorPreviewEntry {
  url: string
  detected_name: string
  source?: string
  error?: string
}

export interface PoolFreshness {
  channel_pool_stale: boolean
  policy_stale: boolean
  relevance_stale: boolean
  // v2.1 Faz C: üçüncü eksen — yalnız algorithm_version=v2_1 run'larda dolar
  strategy_stale?: boolean
}

// Bayatlık nedeni etiketi — TEK kaynak (Channels + PolicyPanel bunu kullanır;
// Codex Faz C 2. tur: strateji bayatlığı "Profil değişti" diye etiketlenmez)
export function poolFreshnessLabel(
  f: Pick<PoolFreshness, 'policy_stale' | 'relevance_stale' | 'strategy_stale'>
): string {
  return (
    [
      f.policy_stale && 'Politika değişti',
      f.relevance_stale && 'Profil değişti (relevance bayat)',
      f.strategy_stale && 'Kanal stratejisi değişti',
    ]
      .filter(Boolean)
      .join(' · ') || 'Havuz güncel değil'
  )
}

export interface AnchorGroup {
  source_field: string
  label: string
  kept_items: string[]
  excluded_items: string[]
  anchor: string
}

export interface AnchorPreviewRequest {
  profile_data?: Record<string, unknown>
}

export interface AnchorPreviewResponse {
  groups: AnchorGroup[]
}

export interface WorkspaceKeywordApproveRequest {
  keywords: string[]
}

export interface WorkspaceKeywordRefreshRequest {
  customer_id?: string
  max_results?: number
  min_volume?: number
  include_new_ideas?: boolean
}

export interface WorkspaceKeywordRefreshResponse {
  workspace_id: number
  refreshed: number
  unchanged: number
  added: number
  removed: number
  total_after: number
}

export interface UrlSeedRequest {
  url: string
  language_id?: string
  geo_target_id?: string
  max_results?: number
  min_volume?: number
  include_keyword_seed?: boolean
  brand_profile_id: number
  customer_id?: string
  refresh?: boolean
}

export interface UrlSeedResponse {
  ideas: UrlSeedIdea[]
  total: number
  cached: boolean
  cache_key: string | null
  quota_remaining: number | null
  warnings?: string[]
  source_status?: UrlSeedSourceStatus | null
}

export interface UrlSeedIdea {
  keyword: string
  monthly_volume: number
  trend_3m: number
  trend_12m: number
  competition: number
}

export interface UrlSeedSourceStatus {
  reachable: boolean
  http_status?: number | null
  final_url?: string | null
  warning?: string | null
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

// ── Lokasyon filtresi önizleme + denetim (plan_v3_lokasyon_filtresi.md §5.6/§5.7) ──

export interface LocationFilterPreviewRequest {
  // Üçü de OPSİYONEL: hiçbiri gönderilmezse workspace'in KAYITLI değeri
  // kullanılır (is_saved_policy=true döner) — start modalının "draft
  // göndermeden taze önizleme" akışı budur. Profil düzenleme ekranı bu
  // alanları HER ZAMAN açıkça gönderir (is_saved_policy=false döner).
  location_filter_mode?: string
  focus_cities?: string[]
  location_exempt_terms?: string[]
  keyword_selection_mode?: 'all' | 'top_n' | 'specific'
  keyword_limit?: number
  selected_keyword_ids?: number[]
  keyword_source_filter?: 'csv' | 'google_ads_api' | null
}

export interface LocationPreviewCityCount {
  city: string
  reason: string
  count: number
}

export interface LocationPreviewSample {
  keyword: string
  matched_city?: string | null
}

export interface LocationPreviewExemptSample {
  keyword: string
  matched_exempt_term: string
}

export interface LocationFilterPreviewResponse {
  has_keywords: boolean
  mode: string
  evaluated_count: number
  kept_count: number
  excluded_count: number
  excluded_by_reason: Record<string, number>
  excluded_by_city: LocationPreviewCityCount[]
  sample_excluded: LocationPreviewSample[]
  sample_exempted: LocationPreviewExemptSample[]
  city_lexicon_version: string
  city_lexicon_sha256: string
  universe_fingerprint: string
  location_policy_fingerprint: string
  is_saved_policy: boolean
}

export interface LocationAuditRow {
  keyword_id: number
  keyword: string
  is_kept: boolean
  reason_code?: string | null
  matched_city?: string | null
  matched_exempt_term?: string | null
}

export interface LocationAuditResponse {
  scoring_run_id: number
  mode: string
  city_lexicon_version: string
  total_rows: number
  kept_count: number
  excluded_count: number
  limit: number
  offset: number
  rows: LocationAuditRow[]
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
  include_stale_content?: boolean
}

// Plan v4 (export dağıtımı): job durumu — geçmiş satırları tür etiketi
// üretebilsin diye format + sections da taşınır
export interface ExportStatusResponse {
  export_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  progress: number
  file_name?: string | null
  error_message?: string | null
  created_at?: string | null
  policy_outdated?: boolean
  format?: string | null
  sections?: string[]
}

// ── Dashboard cockpit (P5.1) ────────────────────────────────────────────────

export type DashboardSeverity = 'primary' | 'warning' | 'danger' | 'neutral'
export type ChannelStatus = 'empty' | 'ready' | 'partial' | 'complete'

export interface WorkspaceSummary {
  id: number
  name: string | null
  company_url: string | null
  status: string
  profile_ready: boolean
}

export interface RunSummary {
  id: number
  run_name: string | null
  status: string
  keyword_selection_mode: string | null
  ads_capacity: number
  seo_capacity: number
  social_capacity: number
  enable_ads: boolean
  enable_seo: boolean
  enable_social: boolean
  skip_relevance: boolean
  algorithm_version?: string | null
  created_at: string | null
}

export interface ChannelProgress {
  pool_count: number
  expected_count: number
  generated_count: number
  status: ChannelStatus
}

export interface ChannelGroup {
  ADS: ChannelProgress
  SEO: ChannelProgress
  SOCIAL: ChannelProgress
}

export interface PipelineStep {
  key: string
  label: string
  state: 'pending' | 'in_progress' | 'complete' | 'blocked' | 'skipped'
  detail: string | null
  path: string | null
}

export interface ExportSummary {
  export_id: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  format: string | null
  file_name: string | null
  created_at: string | null
  // Plan v4: kart etiketi gerçek türden üretilir + eski-politika rozeti
  sections?: string[]
  policy_outdated?: boolean
}

export interface TaskSummary {
  task_id: string
  task_type: string | null
  status: string
  progress: number
  error_message: string | null
  is_blocking: boolean
}

export interface DashboardHealth {
  api: string
  google_ads: string | null
  google_ads_error?: string | null
}

export interface DashboardNextAction {
  key: string
  label: string
  path: string
  severity: DashboardSeverity
  reason: string
  // Plan v4: download_export aksiyonunda kart doğrudan bu id ile indirir
  export_id?: string | null
}

export interface DashboardSummary {
  workspace: WorkspaceSummary | null
  active_run: RunSummary | null
  runs: RunSummary[]
  keyword_count: number
  pipeline: PipelineStep[]
  channels: ChannelGroup
  latest_export: ExportSummary | null
  blocking_task: TaskSummary | null
  active_tasks: TaskSummary[]
  health: DashboardHealth
  next_action: DashboardNextAction
  // Plan v13: aktif run'ın havuz bayatlığı (band + CTA bundan beslenir)
  pool_freshness?: PoolFreshness | null
}

export interface AdsRsaRequest {
  scoring_run_id: number
  brand_name?: string
  website_url?: string
  brand_usp?: string
}

export interface SocialCategoriesRequest {
  scoring_run_id: number
  brand_name?: string
  brand_context?: string
  max_categories?: number
}

export interface SocialIdeasRequest {
  category_ids: number[]
  ideas_per_category?: number
}

export interface SocialContentsRequest {
  idea_ids: number[]
  brand_name?: string
}

// Dashboard API (P5.1 cockpit summary). Declared after interfaces so the generic
// type reference resolves without forward-declaration friction in the IDE.
export const dashboardApi = {
  workspaceSummary: (params?: {
    brand_profile_id?: number
    active_run_id?: number
    refresh?: boolean
    refresh_health?: boolean
  }) => api.get<DashboardSummary>('/dashboard/workspace-summary', { params }),
}

// Social Brief Flow (F1-D / F1-E)
export interface DurationPresetResponse {
  id: string
  label: string
  min_sec: number
  max_sec: number
}

export interface SocialFormatOptionResponse {
  id: string
  label: string
  requires_duration: boolean
  media_mode?: string | null
  duration_profile?: string | null
  duration_presets: DurationPresetResponse[]
}

export interface SocialPlatformOptionResponse {
  id: string
  label: string
  formats: SocialFormatOptionResponse[]
}

export interface SocialFormatMatrixLimitsResponse {
  min_keywords: number
  max_keywords: number
  min_targets: number
  max_targets: number
}

export interface SocialFormatMatrixResponse {
  version: string
  limits: SocialFormatMatrixLimitsResponse
  platforms: SocialPlatformOptionResponse[]
}

export interface SocialBriefTargetCreateRequest {
  platform: string
  content_format: string
  duration_preset_id?: string | null
}

export interface SocialBriefCreateRequest {
  scoring_run_id: number
  keyword_ids: number[]
  targets: SocialBriefTargetCreateRequest[]
  brand_name?: string | null
  brand_context?: string | null
}

export interface SocialBriefKeywordResponse {
  id: number
  keyword_id: number
  keyword_snapshot: string
  position: number
}

export interface SocialBriefTargetResponse {
  id: number
  platform: string
  content_format: string
  duration_preset_id?: string | null
  duration_min_sec?: number | null
  duration_max_sec?: number | null
}

export interface SocialBriefResponse {
  id: number
  scoring_run_id: number
  brand_name_snapshot?: string | null
  brand_context_snapshot?: string | null
  channel_assignment_version: number
  format_matrix_version: string
  locked_at?: string | null
  is_stale: boolean
  created_at?: string | null
  keywords: SocialBriefKeywordResponse[]
  targets: SocialBriefTargetResponse[]
}

export interface SocialBriefCategoriesGenerateRequest {
  idempotency_key: string
  max_categories?: number
}

export interface SocialGeneratedCategoryResponse {
  id: number
  category_name: string
  category_type: string
  description: string
  relevance_score: number
  suggested_keyword_ids: number[]
}

export interface SocialCategoriesGenerateResponse {
  brief_id: number
  scoring_run_id: number
  attempt_id: number
  attempt_status: string
  total_categories: number
  categories: SocialGeneratedCategoryResponse[]
  ai_calls_used?: number | null
  replayed: boolean
}

export type SocialAttemptStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed'

export interface SocialAttemptSummaryResponse {
  id: number
  stage: 'categories' | 'ideas' | 'ideas_retry' | 'contents'
  status: SocialAttemptStatus
  reason_code?: string | null
  created_at?: string | null
  completed_at?: string | null
  lease_expired: boolean
  requested_idea_ids: number[]
}

export interface SocialBriefStateResponse {
  brief_id: number
  scoring_run_id: number
  is_stale: boolean
  locked_at?: string | null
  category_attempt?: SocialAttemptSummaryResponse | null
  categories: SocialGeneratedCategoryResponse[]
  ideas_attempt?: SocialAttemptSummaryResponse | null
  idea_retry_attempts: SocialAttemptSummaryResponse[]
  content_attempts: SocialAttemptSummaryResponse[]
  content_idea_ids: number[]
}

export interface SocialBriefIdeasGenerateRequest {
  idempotency_key: string
  category_ids: number[]
  ideas_per_category: number
}

export interface SocialBriefIdeasRetryRequest {
  idempotency_key: string
  source_attempt_id: number
}

export interface SocialGeneratedIdeaResponse {
  id: number
  category_id: number
  keyword_id: number
  brief_id: number
  brief_target_id: number
  idea_title: string
  idea_description: string
  target_platform: string
  content_format: string
  trend_alignment: number
  is_stale: boolean
}

export interface SocialIdeaCoverageResponse {
  target_id: number
  requested: number
  accepted: number
  missing: number
}

export interface SocialIdeaWarningResponse {
  target_id: number
  category_id?: number | null
  reason_code: string
}

export interface SocialIdeasGenerateResponse {
  brief_id: number
  scoring_run_id: number
  attempt_id: number
  attempt_status: SocialAttemptStatus
  total_ideas: number
  ideas: SocialGeneratedIdeaResponse[]
  coverage: SocialIdeaCoverageResponse[]
  warnings: SocialIdeaWarningResponse[]
  reason_code?: string | null
  replayed: boolean
}

export interface SocialBriefContentsGenerateRequest {
  idempotency_key: string
  idea_ids: number[]
  trusted_brand_usp?: string | null
}

export interface SocialContentHookResponse {
  text: string
  style?: string | null
  ab_score?: number | null
}

export interface SocialVideoSegment {
  start_sec: number
  end_sec: number
  scene: string
  on_screen_text: string
  voiceover: string
}

export interface SocialCarouselSlide {
  position: number
  headline: string
  body: string
  visual_direction: string
}

export interface SocialThreadPost {
  position: number
  text: string
}

export type SocialFormatPayload =
  | { kind: 'video'; segments: SocialVideoSegment[] }
  | { kind: 'carousel'; slides: SocialCarouselSlide[] }
  | { kind: 'thread'; posts: SocialThreadPost[] }
  | { kind: 'caption' }

export interface SocialGeneratedContentItemResponse {
  id: number
  idea_id: number
  brief_id: number
  target_id: number
  platform: string
  content_format: string
  hooks: SocialContentHookResponse[]
  caption: string
  scenario?: string | null
  format_payload?: SocialFormatPayload | null
  visual_suggestion?: string | null
  video_concept?: string | null
  cta_text?: string | null
  hashtags: string[]
  industry_posting_suggestion?: string | null
  platform_notes?: string | null
  duration_status: string
  actual_duration_sec?: number | null
  validation_warnings: string[]
  is_stale: boolean
}

export interface SocialContentWarningResponse {
  idea_id: number
  reason_code: string
  claims: string[]
  ai_calls_used: number
}

export interface SocialBriefContentsAttemptResponse {
  brief_id: number
  scoring_run_id: number
  attempt_id: number
  attempt_status: SocialAttemptStatus
  requested_idea_ids: number[]
  successful_idea_ids: number[]
  unresolved_idea_ids: number[]
  total_contents: number
  contents: SocialGeneratedContentItemResponse[]
  warnings: SocialContentWarningResponse[]
  reason_code?: string | null
  replayed: boolean
}

export interface SocialContentHistoryItemResponse {
  id: number
  idea_id: number
  idea_title?: string | null
  brief_id?: number | null
  brief_is_stale?: boolean | null
  scoring_run_id: number
  run_name?: string | null
  category_name?: string | null
  keyword?: string | null
  platform?: string | null
  content_format?: string | null
  hooks: { text: string; style?: string | null }[]
  caption: string
  scenario?: string | null
  format_payload?: SocialFormatPayload | null
  visual_suggestion?: string | null
  video_concept?: string | null
  cta_text?: string | null
  hashtags: string[]
  industry_posting_suggestion?: string | null
  platform_notes?: string | null
  duration_status?: string | null
  actual_duration_sec?: number | null
  duration_min_sec?: number | null
  duration_max_sec?: number | null
  validation_warnings: string[]
  is_stale: boolean
  created_at?: string | null
}

export interface SocialContentHistoryResponse {
  brand_profile_id: number
  total: number
  limit: number
  offset: number
  has_more: boolean
  items: SocialContentHistoryItemResponse[]
}

export const socialBriefApi = {
  getFormatMatrix: () => api.get<SocialFormatMatrixResponse>('/generation/social/format-matrix'),

  createBrief: (data: SocialBriefCreateRequest, brand_profile_id: number) =>
    api.post<SocialBriefResponse>('/generation/social/briefs', data, {
      params: { brand_profile_id },
    }),

  listBriefs: (scoring_run_id: number, brand_profile_id: number) =>
    api.get<SocialBriefResponse[]>('/generation/social/briefs', {
      params: { scoring_run_id, brand_profile_id },
    }),

  getBrief: (brief_id: number, brand_profile_id: number) =>
    api.get<SocialBriefResponse>(`/generation/social/briefs/${brief_id}`, {
      params: { brand_profile_id },
    }),

  generateCategories: (
    brief_id: number,
    data: SocialBriefCategoriesGenerateRequest,
    brand_profile_id: number
  ) =>
    api.post<SocialCategoriesGenerateResponse>(
      `/generation/social/briefs/${brief_id}/categories/generate`,
      data,
      { params: { brand_profile_id } }
    ),

  // Brief ekranını sunucudan yeniden kurar (kategoriler + attempt kimlikleri)
  getBriefState: (brief_id: number, brand_profile_id: number) =>
    api.get<SocialBriefStateResponse>(`/generation/social/briefs/${brief_id}/state`, {
      params: { brand_profile_id },
    }),

  generateIdeas: (
    brief_id: number,
    data: SocialBriefIdeasGenerateRequest,
    brand_profile_id: number
  ) =>
    api.post<SocialIdeasGenerateResponse>(
      `/generation/social/briefs/${brief_id}/ideas/generate`,
      data,
      { params: { brand_profile_id } }
    ),

  getIdeasAttempt: (brief_id: number, attempt_id: number, brand_profile_id: number) =>
    api.get<SocialIdeasGenerateResponse>(
      `/generation/social/briefs/${brief_id}/ideas/attempts/${attempt_id}`,
      { params: { brand_profile_id } }
    ),

  retryIdeas: (brief_id: number, data: SocialBriefIdeasRetryRequest, brand_profile_id: number) =>
    api.post<SocialIdeasGenerateResponse>(
      `/generation/social/briefs/${brief_id}/ideas/retry`,
      data,
      { params: { brand_profile_id } }
    ),

  getIdeasRetryAttempt: (brief_id: number, attempt_id: number, brand_profile_id: number) =>
    api.get<SocialIdeasGenerateResponse>(
      `/generation/social/briefs/${brief_id}/ideas/retry/attempts/${attempt_id}`,
      { params: { brand_profile_id } }
    ),

  generateContents: (
    brief_id: number,
    data: SocialBriefContentsGenerateRequest,
    brand_profile_id: number
  ) =>
    api.post<SocialBriefContentsAttemptResponse>(
      `/generation/social/briefs/${brief_id}/contents/async`,
      data,
      { params: { brand_profile_id } }
    ),

  getContentsAttempt: (brief_id: number, attempt_id: number, brand_profile_id: number) =>
    api.get<SocialBriefContentsAttemptResponse>(
      `/generation/social/briefs/${brief_id}/contents/attempts/${attempt_id}`,
      { params: { brand_profile_id } }
    ),

  // Workspace geneli sosyal içerik geçmişi (K13); brief_id verilirse yalnız o brief
  getContentHistory: (
    brand_profile_id: number,
    params: { limit?: number; offset?: number; brief_id?: number } = {}
  ) =>
    api.get<SocialContentHistoryResponse>('/generation/social/contents/history', {
      params: { brand_profile_id, ...params },
    }),
}

export default api
