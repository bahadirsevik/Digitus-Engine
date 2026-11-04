/**
 * Kısmi başarı görünür olmalı: ADS failed_groups ve SEO failed_keywords uyarısı.
 * Alan yoksa (eski yanıt / eski görev) uyarı çıkmaz.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import AdsPanel from './AdsPanel'
import SeoGeoPanel from './SeoGeoPanel'
import { useBrandStore } from '../../stores/brandStore'

const mocks = vi.hoisted(() => ({
  getAdsRsa: vi.fn(),
  getAdsSets: vi.fn(),
  listSeoGeo: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    brandProfileApi: {
      ...actual.brandProfileApi,
      getProfile: vi.fn(() => Promise.reject(new Error('no profile'))),
    },
    generationApi: {
      ...actual.generationApi,
      getAdsRsa: mocks.getAdsRsa,
      getAdsSets: mocks.getAdsSets,
      listSeoGeo: mocks.listSeoGeo,
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

const adsResult = (failed?: number) => ({
  data: {
    total_groups: 2,
    ad_groups: [{ id: 1, group_name: 'Grup 1', headlines: [], descriptions: [] }],
    generation_set: {
      id: 5,
      scoring_run_id: 1,
      version_number: 1,
      status: 'active',
      is_stale: false,
      ...(failed === undefined ? {} : { failed_groups: failed }),
    },
  },
})

describe('kısmi başarı uyarıları', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.values(mocks).forEach((m) => m.mockReset())
    mocks.getAdsSets.mockResolvedValue({
      data: { scoring_run_id: 1, sets: [], active_set_id: null },
    })
    mocks.listSeoGeo.mockResolvedValue({ data: { items: [] } })
    useBrandStore.setState({
      activeWorkspace: {
        id: 10,
        name: 'WS',
        company_url: 'https://ws.test',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
  })
  afterEach(cleanup)

  it('ADS: failed_groups > 0 ise toplam ve üretilemeyen grup sayısını söyler', async () => {
    mocks.getAdsRsa.mockResolvedValue(adsResult(1))
    render(<AdsPanel runId={1} />)
    expect(await screen.findByText(/3 reklam grubundan 1 tanesi üretilemedi/)).toBeTruthy()
  })

  it('ADS: failed_groups 0 veya yoksa uyarı yok', async () => {
    mocks.getAdsRsa.mockResolvedValue(adsResult(0))
    const { unmount } = render(<AdsPanel runId={1} />)
    expect(await screen.findByText('RSA Üretimi Tamamlandı')).toBeTruthy()
    expect(screen.queryByText(/üretilemedi/)).toBeNull()
    unmount()

    mocks.getAdsRsa.mockResolvedValue(adsResult())
    render(<AdsPanel runId={1} />)
    expect(await screen.findByText('RSA Üretimi Tamamlandı')).toBeTruthy()
    expect(screen.queryByText(/üretilemedi/)).toBeNull()
  })

  const seoTask = (resultData: Record<string, unknown>) => {
    localStorage.setItem('digitus_active_tasks', JSON.stringify({ 'seo_task:10:1': 'task-A' }))
    mocks.getStatus.mockResolvedValue({
      data: {
        task_id: 'task-A',
        status: 'completed',
        progress: 100,
        scoring_run_id: 1,
        result_data: resultData,
      },
    })
  }

  it('SEO: failed_keywords varsa üretilen/istenen sayıyı ve kelime-sebep listesini gösterir', async () => {
    seoTask({
      total: 8,
      requested_count: 10,
      failed_keywords: [
        { keyword: 'implant fiyat', reason: 'AI yanıtı işlenemedi' },
        { keyword: 'diş beyazlatma', reason: 'AI yanıtı işlenemedi' },
      ],
    })
    render(<SeoGeoPanel runId={1} />)
    expect(
      await screen.findByText(/10 kelimeden 8 içerik üretildi; 2 kelime üretilemedi/)
    ).toBeTruthy()
    expect(screen.getByText(/implant fiyat — AI yanıtı işlenemedi/)).toBeTruthy()
    expect(screen.getByText(/diş beyazlatma — AI yanıtı işlenemedi/)).toBeTruthy()
  })

  it('SEO: alan yoksa veya boşsa uyarı yok', async () => {
    seoTask({ total: 10 })
    const { unmount } = render(<SeoGeoPanel runId={1} />)
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalled())
    await act(async () => {})
    expect(screen.queryByText(/üretilemedi/)).toBeNull()
    unmount()

    seoTask({ total: 10, requested_count: 10, failed_keywords: [] })
    render(<SeoGeoPanel runId={1} />)
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalled())
    await act(async () => {})
    expect(screen.queryByText(/üretilemedi/)).toBeNull()
  })
})
