/**
 * Görevler sayfası bağlantısı (?run_id=X&set_id=Y) aktif seti değil görevin
 * ürettiği seti açar; geç dönen eski (set'siz) yanıt onun üzerine yazmaz.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import AdsPanel from './AdsPanel'
import { useBrandStore } from '../../stores/brandStore'

const mocks = vi.hoisted(() => ({
  getAdsRsa: vi.fn(),
  getAdsSets: vi.fn(),
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
    },
  }
})

const set = (id: number, version: number, status: string) => ({
  id,
  scoring_run_id: 91,
  version_number: version,
  status,
  is_stale: false,
  groups_count: 1,
  failed_groups: 0,
})

describe('AdsPanel set bağlantısı', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.values(mocks).forEach((m) => m.mockReset())
    mocks.getAdsSets.mockResolvedValue({
      data: {
        scoring_run_id: 91,
        sets: [set(44, 2, 'draft'), set(38, 1, 'active')],
        active_set_id: 38,
      },
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
  afterEach(() => {
    cleanup()
    window.history.replaceState(null, '', '/')
  })

  it('URLdeki set_id ile taslak seti ister ve gösterir', async () => {
    window.history.replaceState(null, '', '/ads?run_id=91&set_id=44')
    mocks.getAdsRsa.mockImplementation((_run: number, _ws: number, setId?: number) =>
      Promise.resolve({
        data: {
          total_groups: 1,
          ad_groups: [{ id: 1, group_name: 'Grup', headlines: [] }],
          generation_set: setId === 44 ? set(44, 2, 'draft') : set(38, 1, 'active'),
        },
      })
    )

    const { container } = render(<AdsPanel runId={91} />)

    await waitFor(() => expect(mocks.getAdsRsa).toHaveBeenCalledWith(91, 10, 44))
    await waitFor(() =>
      expect(container.querySelector('.adsx-set-chip.is-shown')?.textContent).toContain('v2')
    )
  })

  it('başka analizin set_id değeri yok sayılır', async () => {
    window.history.replaceState(null, '', '/ads?run_id=97&set_id=44')
    mocks.getAdsRsa.mockResolvedValue({ data: { total_groups: 0, ad_groups: [] } })

    render(<AdsPanel runId={91} />)

    await waitFor(() => expect(mocks.getAdsRsa).toHaveBeenCalled())
    expect(mocks.getAdsRsa).not.toHaveBeenCalledWith(91, 10, 44)
  })
})
