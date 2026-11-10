/**
 * SEO sonuç listesi yüklenemezse boş liste gibi görünmez; yeniden yükle üretim başlatmaz.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import SeoGeoPanel from './SeoGeoPanel'
import { useBrandStore } from '../../stores/brandStore'

const mocks = vi.hoisted(() => ({
  listSeoGeo: vi.fn(),
  bulkSeoGeo: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    generationApi: {
      ...actual.generationApi,
      listSeoGeo: mocks.listSeoGeo,
      bulkSeoGeo: mocks.bulkSeoGeo,
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

describe('SeoGeoPanel sonuç yükleme hatası', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.values(mocks).forEach((m) => m.mockReset())
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

  it('görev tamamlandı ama liste yüklenemedi: mesaj + yeniden yükle (üretim yok)', async () => {
    localStorage.setItem('digitus_active_tasks', JSON.stringify({ 'seo_task:10:1': 'task-A' }))
    mocks.getStatus.mockResolvedValue({
      data: { task_id: 'task-A', status: 'completed', progress: 100, scoring_run_id: 1 },
    })
    // Panel açılışta listeyi birden çok kez isteyebilir: hepsi başarısız kalır,
    // yalnız kullanıcı "yeniden yükle"ye bastıktan sonra liste gelir.
    mocks.listSeoGeo.mockRejectedValue({ status: 500, message: 'Liste alınamadı.' })
    render(<SeoGeoPanel runId={1} />)

    expect(
      await screen.findByText(/Üretim tamamlandı ancak sonuçlar yüklenemedi\. Liste alınamadı/)
    ).toBeTruthy()
    mocks.listSeoGeo.mockResolvedValue({ data: { items: [] } })
    const callsBefore = mocks.listSeoGeo.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: /Sonuçları yeniden yükle/ }))
    await waitFor(() => expect(mocks.listSeoGeo.mock.calls.length).toBeGreaterThan(callsBefore))
    await waitFor(() => expect(screen.queryByText(/sonuçlar yüklenemedi/)).toBeNull())
    expect(mocks.bulkSeoGeo).not.toHaveBeenCalled()
  })
})
