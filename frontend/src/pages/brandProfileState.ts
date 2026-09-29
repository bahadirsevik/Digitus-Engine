import axios from 'axios'
import { apiErrorMessage, WorkspaceResponse } from '../services/api'
import { ActiveWorkspace } from '../stores/brandStore'

// ── Ortak form yardımcıları (BrandProfile + sihirbaz bileşenleri) ──

export function listToTextarea(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean)
    .join('\n')
}

export function splitNewlineItems(raw: string): string[] {
  return raw
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === 'string') return item
          if (item && typeof item === 'object' && 'msg' in item)
            return String((item as { msg: unknown }).msg)
          return JSON.stringify(item)
        })
        .join(' | ')
    }
    return error.message
  }
  // Axios interceptor hataları düz ApiError nesnesine ({status, message,
  // detail}) çevirir — isAxiosError bunu TANIMAZ. apiErrorMessage bu
  // sözleşmeyi (ANALYSIS_RUNNING gibi {code, message} detayları dahil)
  // çözer; aksi halde kullanıcı "Beklenmeyen hata" görürdü (Codex, 22.07).
  return apiErrorMessage(error, 'Beklenmeyen hata')
}

export const GEO_OPTIONS = [
  { value: '2792', label: 'Türkiye (2792)' },
  { value: '2276', label: 'Almanya (2276)' },
] as const

export const LANGUAGE_OPTIONS = [
  { value: '1037', label: 'Türkçe (1037)' },
  { value: '1001', label: 'Almanca (1001)' },
  { value: '1000', label: 'İngilizce (1000)' },
] as const

export interface WorkspaceListRow {
  id: number
  name: string
  company_url: string
  status: string
  onboarding_flow?: 'legacy' | 'profile_first'
  profile_approved_at?: string | null
  validation_data?: Record<string, unknown> | null
  competitor_urls?: string[] | null
  competitor_url_decisions?: Record<string, unknown> | null
  profile_data: Record<string, unknown> | null
  suggested_keywords?: string[] | null
  preliminary_info?: string | null
  excluded_info?: string | null
  default_geo_target_id?: string | null
  default_language_id?: string | null
  deleted_at: string | null
  created_at: string
  updated_at?: string
  run_count: number
}

export type WorkspacePhase =
  | 'analyzing_keywords'
  | 'keywords_review'
  | 'generating_profile'
  | 'profile_review'
  | 'confirmed'
  | 'failed'
  | 'archived'
  | 'idle'
  // profil-önce sihirbaz fazları
  | 'analyzing_site'
  | 'profile_cards_review'
  | 'competitor_review'
  | 'generating_keywords'
  | 'keyword_anchor_review'

interface PhaseInput {
  status: string
  onboarding_flow?: 'legacy' | 'profile_first'
  profile_approved_at?: string | null
  profile_data?: Record<string, unknown> | null
  suggested_keywords?: string[] | null
  deleted_at?: string | null
}

// Tek faz kaynağı: hem workspace kartları hem sihirbaz panelleri bunu kullanır.
// Profil-önce ayrımı KALICI onboarding_flow alanından yapılır (refresh-güvenli);
// running içindeki alt-faz ayrımı profile_approved_at ile (profile_data'nın task
// ortasında yazılmış olabileceği ara anına güvenilmez).
export function getWorkspacePhase(workspace: PhaseInput): WorkspacePhase {
  if (workspace.deleted_at) return 'archived'
  if (workspace.status === 'failed') return 'failed'
  if (workspace.status === 'confirmed') return 'confirmed'

  if (workspace.onboarding_flow === 'profile_first') {
    if (workspace.status === 'profile_review') return 'profile_cards_review'
    if (workspace.status === 'competitor_review') return 'competitor_review'
    if (workspace.status === 'keywords_review') return 'keyword_anchor_review'
    if (workspace.status === 'pending' || workspace.status === 'running') {
      return workspace.profile_approved_at ? 'generating_keywords' : 'analyzing_site'
    }
    return 'idle'
  }

  if (workspace.status === 'draft') return 'profile_review'
  if (workspace.status === 'keywords_review') return 'keywords_review'
  if (workspace.status === 'running' && workspace.suggested_keywords?.length) {
    return 'generating_profile'
  }
  if (workspace.status === 'pending' || workspace.status === 'running') {
    return 'analyzing_keywords'
  }
  return 'idle'
}

export function statusLabel(workspaceOrStatus: string | PhaseInput): string {
  const status =
    typeof workspaceOrStatus === 'string' ? workspaceOrStatus : getWorkspacePhase(workspaceOrStatus)
  switch (status) {
    case 'pending':
    case 'analyzing_keywords':
      return 'Analiz Başlıyor'
    case 'analyzing_site':
      return 'Site İnceleniyor'
    case 'running':
    case 'generating_profile':
      return 'Analiz Ediliyor'
    case 'generating_keywords':
      return 'Keyword Önerileri Hazırlanıyor'
    case 'keywords_review':
      return 'KW Onayi'
    case 'keyword_anchor_review':
      return 'Keyword & Odak Onayı'
    case 'profile_cards_review':
      return 'Profil Onayı Bekliyor'
    case 'competitor_review':
      return 'Rakip İncelemesi'
    case 'draft':
    case 'profile_review':
      return 'Onay Bekliyor'
    case 'confirmed':
      return 'Onaylandı'
    case 'failed':
      return 'Hata'
    default:
      return status
  }
}

// Workspace (liste satırı veya response) → aktif workspace store şekli.
// BrandProfile ve Dashboard workspace-seçici aynı dönüşümü paylaşır.
export function toActiveWorkspace(workspace: {
  id: number
  name: string
  company_url: string
  status: string
  profile_data: Record<string, unknown> | null
  suggested_keywords?: string[] | null
  competitor_urls?: string[] | null
  default_geo_target_id?: string | null
  default_language_id?: string | null
}): ActiveWorkspace {
  return {
    id: workspace.id,
    name: workspace.name,
    company_url: workspace.company_url,
    status: workspace.status,
    profile_data: workspace.profile_data,
    suggested_keywords: workspace.suggested_keywords || null,
    competitor_urls: workspace.competitor_urls || null,
    default_geo_target_id: workspace.default_geo_target_id || null,
    default_language_id: workspace.default_language_id || null,
  }
}

export function workspaceResponseToListRow(
  workspace: WorkspaceResponse,
  fallback?: WorkspaceListRow
): WorkspaceListRow {
  return {
    ...workspace,
    run_count: fallback?.run_count ?? 0,
  }
}

export function mergeConfirmedWorkspace(
  current: WorkspaceListRow,
  response: WorkspaceResponse
): WorkspaceListRow | null {
  if (current.id !== response.id) return null
  return workspaceResponseToListRow(response, current)
}
