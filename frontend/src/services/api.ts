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
  
  exportCsv: (runId: number) => 
    api.get(`/scoring/runs/${runId}/export/csv`, { responseType: 'blob' })
}

// Channels API
export const channelsApi = {
  assign: (runId: number) => 
    api.post(`/channels/runs/${runId}/assign`),
  
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
  
  download: (filename: string) => 
    api.get(`/export/download/${filename}`, { responseType: 'blob' })
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
}

export interface ExportRequest {
  scoring_run_id: number
  format: 'docx' | 'pdf' | 'excel'
  channels?: string[]
  include_scores?: boolean
  include_intent_analysis?: boolean
  include_content?: boolean
}

export default api
