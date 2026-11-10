/**
 * Sonuç yükleme hatası görünür olmalı; "yeniden yükle" yalnız okur, üretimi TETİKLEMEZ.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AdsPanel from './AdsPanel'
import { useBrandStore } from '../../stores/brandStore'

const mocks = vi.hoisted(() => ({
  getAdsRsa: vi.fn(),
  createAdsRsa: vi.fn(),
  getAdsSets: vi.fn(),
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
      createAdsRsa: mocks.createAdsRsa,
      regenerateAdGroup: mocks.createAdsRsa,
      getAdsSets: mocks.getAdsSets,
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

const FAIL = { status: 500, message: 'Reklam grupları alınamadı.' }

describe('AdsPanel sonuç yükleme hatası', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.values(mocks).forEach((m) => m.mockReset())
    mocks.getAdsSets.mockResolvedValue({
      data: { scoring_run_id: 1, sets: [], active_set_id: null },
    })
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

  it('yükleme hatası görünür; yeniden yükle üretim başlatmaz ve sonuç gelir', async () => {
    mocks.getAdsRsa.mockRejectedValueOnce(FAIL)
    render(<AdsPanel runId={1} />)

    expect(
      await screen.findByText(/Reklam sonuçları yüklenemedi\. Reklam grupları alınamadı/)
    ).toBeTruthy()
    mocks.getAdsRsa.mockResolvedValue({
      data: { total_groups: 1, ad_groups: [{ id: 1, group_name: 'Grup 1', headlines: [] }] },
    })
    fireEvent.click(screen.getByRole('button', { name: /Sonuçları yeniden yükle/ }))

    await waitFor(() => expect(screen.queryByText(/Reklam sonuçları yüklenemedi/)).toBeNull())
    expect(await screen.findByText('RSA Üretimi Tamamlandı')).toBeTruthy()
    expect(mocks.createAdsRsa).not.toHaveBeenCalled()
  })

  it('görev tamamlandı ama sonuçlar yüklenemediyse bunu söyler', async () => {
    localStorage.setItem('digitus_active_tasks', JSON.stringify({ 'ads_task:10:1': 'task-A' }))
    mocks.getStatus.mockResolvedValue({
      data: { task_id: 'task-A', status: 'completed', progress: 100, scoring_run_id: 1 },
    })
    mocks.getAdsRsa.mockRejectedValue(FAIL)
    render(<AdsPanel runId={1} />)

    expect(await screen.findByText(/Üretim tamamlandı ancak sonuçlar yüklenemedi\./)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Sonuçları yeniden yükle/ })).toBeTruthy()
    expect(mocks.createAdsRsa).not.toHaveBeenCalled()
  })

  it('set listesi hatası, başarılı sonuç yüklemesiyle silinmez', async () => {
    mocks.getAdsSets.mockRejectedValue({ status: 500, message: 'Set listesi alınamadı.' })
    mocks.getAdsRsa.mockResolvedValue({
      data: { total_groups: 1, ad_groups: [{ id: 1, group_name: 'Grup 1', headlines: [] }] },
    })
    render(<AdsPanel runId={1} />)

    expect(await screen.findByText('RSA Üretimi Tamamlandı')).toBeTruthy()
    expect(
      await screen.findByText(/Reklam seti listesi yüklenemedi: Set listesi alınamadı/)
    ).toBeTruthy()
    expect(screen.queryByText(/sonuçlar yüklenemedi/)).toBeNull()
    expect(screen.getByRole('button', { name: /Sonuçları yeniden yükle/ })).toBeTruthy()
  })
})
