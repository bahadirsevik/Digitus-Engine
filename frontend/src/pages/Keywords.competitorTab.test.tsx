/**
 * Rakip URL'leri sekmesi: profildeki rakip URL'lerine ek olarak Google ile
 * bulunup ONAYLANAN rakipler de seçilebilir; önerilen/reddedilen gelmez,
 * aynı site iki kez görünmez.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Keywords from './Keywords'
import { brandProfileApi, keywordsApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    keywordsApi: { list: vi.fn(), getStats: vi.fn().mockResolvedValue({ data: {} }) },
    scoringApi: { listRuns: vi.fn().mockResolvedValue({ data: [] }), getRun: vi.fn() },
    tasksApi: { listByRun: vi.fn().mockResolvedValue({ data: [] }), getStatus: vi.fn() },
    brandProfileApi: { ...actual.brandProfileApi, getPolicy: vi.fn() },
    googleAdsApi: {
      ...actual.googleAdsApi,
      listCustomers: vi.fn().mockResolvedValue({ data: [] }),
    },
  }
})

const renderTab = () =>
  render(
    <MemoryRouter initialEntries={['/keywords?tab=competitor']}>
      <Keywords />
    </MemoryRouter>
  )

describe('Keywords rakip URL sekmesi', () => {
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
        competitor_urls: ['https://rakip-profil.com'],
      },
    })
    vi.mocked(keywordsApi.list).mockResolvedValue({ data: { items: [], total: 0 } } as never)
  })
  afterEach(cleanup)

  it('onaylanan keşif rakiplerini profil rakipleriyle birlikte gösterir', async () => {
    vi.mocked(brandProfileApi.getPolicy).mockResolvedValue({
      data: {
        competitor_terms: [
          {
            term: 'Bulunan',
            status: 'approved',
            discovery_id: 'd1',
            candidate_url: 'https://bulunan.com',
          },
          {
            term: 'Öneri',
            status: 'suggested',
            discovery_id: 'd2',
            candidate_url: 'https://oneri.com',
          },
          {
            term: 'Profil',
            status: 'approved',
            discovery_id: 'd3',
            candidate_url: 'https://www.rakip-profil.com/',
          },
          { term: 'Elle', status: 'approved', candidate_url: 'https://elle.com' },
        ],
      },
    } as never)

    renderTab()

    expect(await screen.findByText('bulunan.com')).toBeTruthy()
    expect(screen.getByText('rakip-profil.com')).toBeTruthy()
    expect(screen.queryByText('oneri.com')).toBeNull()
    expect(screen.queryByText('elle.com')).toBeNull()
    expect(screen.queryAllByText(/rakip-profil\.com/)).toHaveLength(1)
    expect(screen.queryByText(/rakip adresi yok/)).toBeNull()
  })

  it('politika yüklenemezse nedenini söyler, profil rakipleri yine görünür', async () => {
    vi.mocked(brandProfileApi.getPolicy).mockRejectedValue({
      status: 500,
      message: 'Sunucu hatası.',
    })

    renderTab()

    expect(await screen.findByText(/Google ile bulunan rakipler yüklenemedi/)).toBeTruthy()
    expect(screen.getByText('rakip-profil.com')).toBeTruthy()
  })
})
