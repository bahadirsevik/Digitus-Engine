import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'

const baseWorkspace = {
  status: 'confirmed',
  onboarding_flow: 'profile_first' as const,
  profile_approved_at: '2026-01-01T00:00:00Z',
  suggested_keywords: null,
  preliminary_info: null,
  excluded_info: null,
  default_geo_target_id: '2792',
  default_language_id: '1037',
  created_at: '2026-01-01T00:00:00Z',
  run_count: 1,
}

const mockWorkspaces = [
  {
    ...baseWorkspace,
    id: 1,
    name: 'İstanbul Mobilya',
    company_url: 'https://istmobilya.com',
    profile_data: { sector: 'Mobilya' },
    deleted_at: null,
  },
  {
    ...baseWorkspace,
    id: 2,
    name: 'Akıllı Saat Dünyası',
    company_url: 'https://saat.com',
    profile_data: { sector: 'Elektronik' },
    deleted_at: null,
  },
  {
    ...baseWorkspace,
    id: 3,
    name: 'Eski Mobilya Markası',
    company_url: 'https://eski.com',
    profile_data: { sector: 'Mobilya' },
    deleted_at: '2026-02-01T00:00:00Z',
  },
]

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: vi.fn(() => Promise.resolve({ data: mockWorkspaces })),
    get: vi.fn((id: number) => Promise.resolve({ data: mockWorkspaces.find((w) => w.id === id) })),
  },
  brandProfileApi: {},
  apiErrorMessage: (_: unknown, fallback = 'Hata') => fallback,
}))

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/brand-profile']}>
      <BrandProfile />
    </MemoryRouter>
  )
}

describe('BrandProfile marka arama', () => {
  it('Türkçe karakterden bağımsız filtreler ve eşleşmeyenleri gizler', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('İstanbul Mobilya')).toBeTruthy())

    const input = screen.getByLabelText('Marka çalışması ara')
    fireEvent.change(input, { target: { value: 'akilli' } })
    expect(screen.getByText('Akıllı Saat Dünyası')).toBeTruthy()
    expect(screen.queryByText('İstanbul Mobilya')).toBeNull()

    fireEvent.change(input, { target: { value: 'ISTANBUL' } })
    expect(screen.getByText('İstanbul Mobilya')).toBeTruthy()
    expect(screen.queryByText('Akıllı Saat Dünyası')).toBeNull()

    fireEvent.change(input, { target: { value: 'yokboylebirmarka' } })
    expect(screen.getByText(/ile eşleşen marka çalışması bulunamadı/)).toBeTruthy()
  })

  it('arşiv gizliyken arşivdeki eşleşmeleri ipucu ile gösterir', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('İstanbul Mobilya')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Marka çalışması ara'), {
      target: { value: 'mobilya' },
    })
    expect(screen.queryByText('Eski Mobilya Markası')).toBeNull()
    expect(screen.getByText(/Arşivde aramaya uyan 1 çalışma daha var/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Arşivlenmişleri de göster' }))
    await waitFor(() => expect(screen.getByText('Eski Mobilya Markası')).toBeTruthy())
    expect(screen.getByText('İstanbul Mobilya')).toBeTruthy()
  })
})
