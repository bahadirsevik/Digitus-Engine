/**
 * Analiz listesi yüklenemezse "boş liste" gibi görünmez: neden + yeniden yükle gösterilir.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Keywords from './Keywords'
import { keywordsApi, scoringApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    keywordsApi: { list: vi.fn(), getStats: vi.fn().mockResolvedValue({ data: {} }) },
    scoringApi: {
      listRuns: vi.fn(),
      getRun: vi.fn(),
      getScores: vi.fn().mockResolvedValue({ data: { scores: [], total_scored: 0 } }),
    },
    tasksApi: { listByRun: vi.fn().mockResolvedValue({ data: [] }), getStatus: vi.fn() },
  }
})

describe('Keywords analiz listesi yükleme hatası', () => {
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
    vi.mocked(keywordsApi.list).mockResolvedValue({ data: { items: [], total: 0 } } as never)
  })
  afterEach(cleanup)

  it('listRuns hatasında neden gösterilir; yeniden yükleyince liste gelir', async () => {
    vi.mocked(scoringApi.listRuns).mockRejectedValueOnce({
      status: 500,
      message: 'Analizler okunamadı.',
    })
    render(
      <MemoryRouter initialEntries={['/keywords?view=scores']}>
        <Keywords />
      </MemoryRouter>
    )

    expect(await screen.findByText(/Analiz listesi yüklenemedi: Analizler okunamadı/)).toBeTruthy()

    vi.mocked(scoringApi.listRuns).mockResolvedValue({
      data: [{ id: 7, run_name: 'Deneme', status: 'completed' }],
    } as never)
    fireEvent.click(screen.getByRole('button', { name: /Listeyi yeniden yükle/ }))

    await waitFor(() => expect(screen.queryByText(/Analiz listesi yüklenemedi/)).toBeNull())
    expect(await screen.findByText(/#7 Deneme/)).toBeTruthy()
  })
})
