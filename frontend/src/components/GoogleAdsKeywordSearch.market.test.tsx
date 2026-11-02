/**
 * Pazar çalışmadan gelir: arama isteği brand_profile_id taşır, dil/geo alanı YOK;
 * yanıtın pazarı import'a iletilir; MARKET_NOT_SET profil bağlantısı gösterir.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import GoogleAdsKeywordSearch from './GoogleAdsKeywordSearch'
import { googleAdsApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    googleAdsApi: {
      ...actual.googleAdsApi,
      listCustomers: vi.fn().mockResolvedValue({ data: [{ customer_id: '123-456-7890' }] }),
      enrich: vi.fn(),
    },
  }
})

const IDEA = {
  keyword: 'berlin diş',
  avg_monthly_searches: 900,
  competition_index: 20,
  competition_score: 0.2,
  trend_3m: 0,
  trend_12m: 0,
  cpc_low: 0,
  cpc_high: 0,
}

const setWorkspace = () =>
  useBrandStore.setState({
    seedDrafts: {},
    activeWorkspace: {
      id: 31,
      name: 'Berlin Klinik',
      company_url: 'https://example.com',
      status: 'confirmed',
      profile_data: null,
      suggested_keywords: ['diş'],
      market_label: 'Berlin, Almanya · Rusça',
    },
  })

describe('GoogleAdsKeywordSearch pazar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setWorkspace()
  })
  afterEach(cleanup)

  it('pazar satırı salt okunur görünür; serbest dil/geo girişi yoktur', async () => {
    render(<GoogleAdsKeywordSearch onImport={vi.fn()} />)
    expect(
      await screen.findByText(/Pazar: Berlin, Almanya · Rusça · Hacim: Google Ads/)
    ).toBeTruthy()
    expect(screen.queryByText('Geo Target')).toBeNull()
    expect(screen.queryByText('Dil')).toBeNull()
  })

  it('enrich isteği brand_profile_id taşır, dil/geo göndermez; yanıt pazarı import’a gider', async () => {
    vi.mocked(googleAdsApi.enrich).mockResolvedValue({
      data: {
        count: 1,
        truncated: false,
        truncated_at: null,
        truncated_reason: null,
        keywords: [IDEA],
        geo_target_id: '1003854',
        language_id: '1031',
        market_label: 'Berlin, Almanya · Rusça',
      },
    } as never)
    const onImport = vi.fn().mockResolvedValue({ created: 1 })
    render(<GoogleAdsKeywordSearch onImport={onImport} />)

    const searchBtn = await screen.findByRole('button', { name: /Ara/ })
    await waitFor(() => expect((searchBtn as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(searchBtn)

    await waitFor(() => expect(googleAdsApi.enrich).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(googleAdsApi.enrich).mock.calls[0][0]
    expect(payload.brand_profile_id).toBe(31)
    expect(payload).not.toHaveProperty('language_id')
    expect(payload).not.toHaveProperty('geo_target_id')

    fireEvent.click(await screen.findByRole('button', { name: /Seçilenleri Havuza Ekle/ }))
    await waitFor(() => expect(onImport).toHaveBeenCalledTimes(1))
    expect(onImport.mock.calls[0][1]).toMatchObject({
      geo_target_id: '1003854',
      language_id: '1031',
    })
  })

  it('409 MARKET_NOT_SET mesajı ve profil bağlantısı gösterilir', async () => {
    vi.mocked(googleAdsApi.enrich).mockRejectedValue({
      status: 409,
      message: 'Bu çalışmanın pazarı seçilmemiş.',
      detail: { code: 'MARKET_NOT_SET', message: 'Bu çalışmanın pazarı seçilmemiş.' },
    })
    render(<GoogleAdsKeywordSearch onImport={vi.fn()} />)
    const searchBtn = await screen.findByRole('button', { name: /Ara/ })
    await waitFor(() => expect((searchBtn as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(searchBtn)

    expect(await screen.findByText(/Bu çalışmanın pazarı seçilmemiş/)).toBeTruthy()
    const link = screen.getByRole('link', { name: 'Çalışma profiline git' })
    expect(link.getAttribute('href')).toBe('/brand-profile?workspace_id=31')
  })
})
