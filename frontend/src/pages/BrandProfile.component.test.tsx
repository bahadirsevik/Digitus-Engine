import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'

const mockWorkspaces = [
  {
    id: 10,
    name: 'Confirmed Brand',
    company_url: 'https://brand.com',
    status: 'confirmed',
    onboarding_flow: 'profile_first' as const,
    profile_approved_at: '2026-01-01T00:00:00Z',
    profile_data: {
      company_name: 'Confirmed Brand',
      sector: 'E-Ticaret',
      brand_summary: 'Test summary',
      products: ['Ürün A', 'Ürün B'],
      services: ['Hizmet 1'],
      target_audience: 'Genel',
      anchor_texts: ['anchor1', 'anchor2'],
    },
    suggested_keywords: null,
    preliminary_info: null,
    excluded_info: null,
    default_geo_target_id: '2792',
    default_language_id: '1037',
    deleted_at: null,
    created_at: '2026-01-01T00:00:00Z',
    run_count: 2,
  },
]

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: vi.fn(() => Promise.resolve({ data: mockWorkspaces })),
    get: vi.fn((id: number) => Promise.resolve({ data: mockWorkspaces.find((w) => w.id === id) })),
    update: vi.fn(() => Promise.resolve({ data: {} })),
  },
  brandProfileApi: {
    policyReview: vi.fn(() => Promise.resolve({ data: {} })),
    startCompetitorPreview: vi.fn(() => Promise.resolve({ data: { task_id: 't1' } })),
    getCompetitorPreview: vi.fn(() =>
      Promise.resolve({ data: { status: 'completed', result: [] } })
    ),
  },
  apiErrorMessage: (_: unknown, fallback = 'Hata') => fallback,
}))

describe('BrandProfile confirmed workspace edit modal', () => {
  it('confirmed profil düzenleme görünümünde Marka Odakları Önizlemesi ve Kanal Stratejisi bulunmaz', async () => {
    render(
      <MemoryRouter initialEntries={['/brand-profile?workspace_id=10']}>
        <BrandProfile />
      </MemoryRouter>
    )

    // Modal açılınca düzenlenebilir alanlar gelmeli
    await waitFor(() => {
      expect(screen.getByDisplayValue('Confirmed Brand')).toBeTruthy()
    })

    // "Marka Odakları Önizlemesi" bulunmamalı
    expect(screen.queryByText(/Marka Odakları Önizlemesi/i)).toBeNull()

    // "Kanal Stratejisi" bulunmamalı
    expect(screen.queryByText(/Kanal Stratejisi/i)).toBeNull()

    // Yalnız düzenlenebilir profil alanları ve kaydetme butonu görünmeli
    expect(screen.getByDisplayValue(/Ürün A/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Değişiklikleri Kaydet/i })).toBeTruthy()
  })
})
