import { describe, expect, it } from 'vitest'
import {
  extractErrorMessage,
  getWorkspacePhase,
  mergeConfirmedWorkspace,
  statusLabel,
} from './brandProfileState'
import { WorkspaceResponse } from '../services/api'

const current = {
  id: 1,
  name: 'Draft Workspace',
  company_url: 'https://example.com',
  status: 'draft',
  profile_data: null,
  suggested_keywords: null,
  default_geo_target_id: '2792',
  default_language_id: '1037',
  deleted_at: null,
  created_at: '2026-01-01T00:00:00',
  run_count: 4,
}

function response(overrides: Partial<WorkspaceResponse> = {}): WorkspaceResponse {
  return {
    id: 1,
    name: 'Confirmed Workspace',
    company_url: 'https://example.com',
    status: 'confirmed',
    profile_data: { anchor_texts: ['example'] },
    suggested_keywords: ['seo'],
    default_geo_target_id: '2792',
    default_language_id: '1037',
    deleted_at: null,
    created_at: '2026-01-01T00:00:00',
    ...overrides,
  }
}

describe('extractErrorMessage — interceptor ApiError sözleşmesi', () => {
  it('ANALYSIS_RUNNING 409 mesajını kullanıcıya taşır (ApiError axios hatası DEĞİLDİR)', () => {
    const apiError = {
      status: 409,
      message: 'Analiz/keyword üretimi sürüyor — politika değişikliği için bitmesini bekleyin.',
      detail: {
        code: 'ANALYSIS_RUNNING',
        message: 'Analiz/keyword üretimi sürüyor — politika değişikliği için bitmesini bekleyin.',
      },
    }
    expect(extractErrorMessage(apiError)).toContain('Analiz/keyword üretimi sürüyor')
  })

  it('mesajsız ApiError detay mesajına, tanınmayan hata fallback mesajına düşer', () => {
    expect(
      extractErrorMessage({
        status: 409,
        message: '',
        detail: { code: 'X', message: 'Detay mesajı' },
      })
    ).toBe('Detay mesajı')
    expect(extractErrorMessage(new Error('ağ koptu'))).toBe('ağ koptu')
    expect(extractErrorMessage(undefined)).toBe('Beklenmeyen hata')
  })
})

describe('mergeConfirmedWorkspace', () => {
  it('merges the confirm response into the matching workspace', () => {
    const merged = mergeConfirmedWorkspace(current, response())

    expect(merged?.status).toBe('confirmed')
    expect(merged?.name).toBe('Confirmed Workspace')
    expect(merged?.profile_data).toEqual({ anchor_texts: ['example'] })
    expect(merged?.run_count).toBe(4)
  })

  it('does not merge a response for a different workspace id', () => {
    const merged = mergeConfirmedWorkspace(current, response({ id: 99 }))

    expect(merged).toBeNull()
  })
})

describe('getWorkspacePhase — profil-önce sihirbaz matrisi', () => {
  const base = { deleted_at: null, profile_data: null, suggested_keywords: null }

  it('running + profile_first + onaysız → analyzing_site (refresh senaryosu)', () => {
    expect(
      getWorkspacePhase({
        ...base,
        status: 'running',
        onboarding_flow: 'profile_first',
        profile_approved_at: null,
        // profil task ortasında yazılmış olsa bile faz değişmemeli
        profile_data: { company_name: 'X' },
      })
    ).toBe('analyzing_site')
  })

  it('running + profile_first + onaylı → generating_keywords', () => {
    expect(
      getWorkspacePhase({
        ...base,
        status: 'running',
        onboarding_flow: 'profile_first',
        profile_approved_at: '2026-07-09T10:00:00',
      })
    ).toBe('generating_keywords')
  })

  it('profile_review → profile cards; competitor_review → discovery; keywords_review → anchors', () => {
    expect(
      getWorkspacePhase({ ...base, status: 'profile_review', onboarding_flow: 'profile_first' })
    ).toBe('profile_cards_review')
    expect(
      getWorkspacePhase({ ...base, status: 'competitor_review', onboarding_flow: 'profile_first' })
    ).toBe('competitor_review')
    expect(
      getWorkspacePhase({ ...base, status: 'keywords_review', onboarding_flow: 'profile_first' })
    ).toBe('keyword_anchor_review')
  })

  it('legacy davranış değişmedi', () => {
    expect(getWorkspacePhase({ ...base, status: 'keywords_review' })).toBe('keywords_review')
    expect(getWorkspacePhase({ ...base, status: 'draft' })).toBe('profile_review')
    expect(getWorkspacePhase({ ...base, status: 'running', suggested_keywords: ['kw'] })).toBe(
      'generating_profile'
    )
    expect(getWorkspacePhase({ ...base, status: 'running' })).toBe('analyzing_keywords')
  })

  it('confirmed/failed/arşiv akıştan bağımsız', () => {
    expect(
      getWorkspacePhase({ ...base, status: 'confirmed', onboarding_flow: 'profile_first' })
    ).toBe('confirmed')
    expect(getWorkspacePhase({ ...base, status: 'failed', onboarding_flow: 'profile_first' })).toBe(
      'failed'
    )
    expect(
      getWorkspacePhase({
        ...base,
        status: 'confirmed',
        onboarding_flow: 'profile_first',
        deleted_at: '2026-07-09T10:00:00',
      })
    ).toBe('archived')
  })

  it('statusLabel yeni fazlar için Türkçe etiket döner', () => {
    expect(statusLabel('profile_cards_review')).toBe('Profil Onayı Bekliyor')
    expect(statusLabel('competitor_review')).toBe('Rakip İncelemesi')
    expect(statusLabel('keyword_anchor_review')).toBe('Keyword & Odak Onayı')
    expect(statusLabel('analyzing_site')).toBe('Site İnceleniyor')
  })
})
