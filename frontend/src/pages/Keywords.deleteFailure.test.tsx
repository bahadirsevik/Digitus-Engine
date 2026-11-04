/**
 * Silme başarısızsa satır listede KALIR ve gerçek hata gösterilir
 * (eskiden satır istemcide sessizce silinmiş gibi kaldırılıyordu).
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Keywords from './Keywords'
import { keywordsApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    keywordsApi: {
      list: vi.fn(),
      getStats: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn(),
      import: vi.fn(),
    },
    scoringApi: {
      listRuns: vi.fn().mockResolvedValue({ data: [] }),
      getRun: vi.fn(),
      getScores: vi.fn().mockResolvedValue({ data: { scores: [], total: 0 } }),
    },
    tasksApi: { listByRun: vi.fn(), getStatus: vi.fn() },
  }
})

const ROW = {
  id: 5,
  wk_id: 55,
  keyword: 'implant fiyatları',
  monthly_volume: 100,
  trend_3m: 0,
  trend_12m: 0,
  competition_score: 0.4,
  is_active: true,
}

describe('Keywords silme hatası', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useBrandStore.setState({
      activeWorkspace: {
        id: 31,
        name: 'Klinik',
        company_url: 'https://example.com',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
    vi.mocked(keywordsApi.list).mockResolvedValue({ data: { items: [ROW], total: 1 } } as never)
  })
  afterEach(cleanup)

  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={['/keywords?tab=google-ads']}>
        <Keywords />
      </MemoryRouter>
    )

  it('silme başarısızsa satır korunur ve sunucu mesajı gösterilir', async () => {
    vi.mocked(keywordsApi.delete).mockRejectedValue({
      status: 409,
      message: 'Kelime bir koşuda kullanılıyor.',
      detail: 'Kelime bir koşuda kullanılıyor.',
    })
    renderPage()
    expect(await screen.findByText('implant fiyatları')).toBeInTheDocument()

    fireEvent.click(screen.getByTitle('Havuzdan sil'))

    expect(await screen.findByText(/Kelime bir koşuda kullanılıyor/)).toBeInTheDocument()
    expect(screen.getByText('implant fiyatları')).toBeInTheDocument()
    await waitFor(() => expect(keywordsApi.delete).toHaveBeenCalledTimes(1))
  })

  it('liste yüklenemezse ağ hatası dışında gerçek mesaj gösterilir', async () => {
    vi.mocked(keywordsApi.list).mockRejectedValue({ status: 500, message: 'Sunucu hatası oluştu.' })
    renderPage()
    expect(
      await screen.findByText(/Anahtar kelime listesi yüklenemedi: Sunucu hatası/)
    ).toBeTruthy()
    expect(screen.queryByText(/Backend çalışıyor mu/)).toBeNull()
  })
})
