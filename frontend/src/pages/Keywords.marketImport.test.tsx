/**
 * Import satırları pazarı yalnız Google'a GERÇEKTEN gönderilen yanıttan alır;
 * yanıt pazarı yoksa alanlar hiç gönderilmez (sunucu çalışma pazarını damgalar).
 * Sabit '2276'/'1001' literal'i payload'a asla girmez.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Keywords from './Keywords'
import { keywordsApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../components/GoogleAdsKeywordSearch', () => ({
  default: ({
    onImport,
  }: {
    onImport: (
      kws: unknown[],
      market?: { geo_target_id: string; language_id: string }
    ) => Promise<unknown>
  }) => {
    const kw = [
      {
        keyword: 'berlin diş',
        avg_monthly_searches: 100,
        trend_3m: 0,
        trend_12m: 0,
        competition_score: 0.4,
      },
    ]
    return (
      <>
        <button
          type="button"
          onClick={() => void onImport(kw, { geo_target_id: '1003854', language_id: '1031' })}
        >
          import-with-market
        </button>
        <button type="button" onClick={() => void onImport(kw)}>
          import-without-market
        </button>
      </>
    )
  },
}))

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    keywordsApi: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
      getStats: vi.fn().mockResolvedValue({ data: {} }),
      import: vi.fn().mockResolvedValue({ data: { created: 1, skipped: 0, message: 'ok' } }),
    },
    scoringApi: {
      listRuns: vi.fn().mockResolvedValue({ data: [] }),
      getRun: vi.fn(),
      getScores: vi.fn().mockResolvedValue({ data: { scores: [], total: 0 } }),
    },
    tasksApi: { listByRun: vi.fn(), getStatus: vi.fn() },
  }
})

describe('Keywords import pazar damgası', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useBrandStore.setState({
      activeWorkspace: {
        id: 31,
        name: 'Berlin Klinik',
        company_url: 'https://example.com',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
        default_geo_target_id: '2276',
        default_language_id: '1001',
      },
    })
  })
  afterEach(cleanup)

  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={['/keywords?tab=google-ads']}>
        <Keywords />
      </MemoryRouter>
    )

  it('yanıttaki pazar import satırlarına yazılır; çalışma varsayılanı kullanılmaz', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('import-with-market'))
    await waitFor(() => expect(keywordsApi.import).toHaveBeenCalledTimes(1))
    const [rows, workspaceId] = vi.mocked(keywordsApi.import).mock.calls[0]
    expect(workspaceId).toBe(31)
    expect(rows[0]).toMatchObject({
      keyword: 'berlin diş',
      data_source: 'google_ads_api',
      geo_target_id: '1003854',
      language_id: '1031',
    })
  })

  it('yanıt pazarı yoksa alanlar hiç gönderilmez ve literal yoktur', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('import-without-market'))
    await waitFor(() => expect(keywordsApi.import).toHaveBeenCalledTimes(1))
    const [rows] = vi.mocked(keywordsApi.import).mock.calls[0]
    expect(rows[0]).not.toHaveProperty('geo_target_id')
    expect(rows[0]).not.toHaveProperty('language_id')
    expect(JSON.stringify(rows)).not.toMatch(/2792|1037/)
  })
})
