import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Channels from './Channels'
import { channelsApi, scoringApi, brandProfileApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    scoringApi: { listRuns: vi.fn() },
    channelsApi: {
      getPools: vi.fn(),
      assign: vi.fn(),
    },
    brandProfileApi: {
      ...actual.brandProfileApi,
      getLocationAudit: vi.fn(),
    },
  }
})

const RUN_ASSIGNED = {
  id: 24,
  run_name: 'Dijital',
  status: 'channel_assigned',
  default_relevance_coefficient: 1,
}

const RUN_SCORED = {
  id: 25,
  run_name: 'Henüz Atanmamış',
  status: 'scored',
  default_relevance_coefficient: 1,
}

const AUDIT_RESPONSE = {
  scoring_run_id: 24,
  mode: 'exclude_all',
  city_lexicon_version: 'tr-provinces-v1',
  total_rows: 2,
  kept_count: 1,
  excluded_count: 1,
  limit: 50,
  offset: 0,
  rows: [
    {
      keyword_id: 1,
      keyword: 'ankara ofis mobilyası',
      is_kept: false,
      reason_code: 'LOCATION_CITY_FILTER',
      matched_city: 'Ankara',
      matched_exempt_term: null,
    },
    {
      keyword_id: 2,
      keyword: 'ofis mobilyası',
      is_kept: true,
      reason_code: null,
      matched_city: null,
      matched_exempt_term: null,
    },
  ],
}

describe('Channels lokasyon denetimi paneli', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.setState({ activeWorkspace: { id: 29, name: 'Dijital' } } as never)
    vi.mocked(channelsApi.getPools).mockResolvedValue({
      data: { channels: {}, capacities: {} },
    } as never)
    vi.mocked(brandProfileApi.getLocationAudit).mockResolvedValue({
      data: AUDIT_RESPONSE,
    } as never)
  })

  it('run channel_assigned durumundayken panel görünür ve tıklanınca denetimi çeker', async () => {
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN_ASSIGNED] } as never)

    render(
      <MemoryRouter initialEntries={['/channels?run_id=24']}>
        <Channels />
      </MemoryRouter>
    )
    await waitFor(() => expect(scoringApi.listRuns).toHaveBeenCalled())

    const toggle = await screen.findByRole('button', { name: /Lokasyon Denetimi/i })
    expect(brandProfileApi.getLocationAudit).not.toHaveBeenCalled()

    fireEvent.click(toggle)

    await waitFor(() =>
      expect(brandProfileApi.getLocationAudit).toHaveBeenCalledWith(24, {
        brand_profile_id: 29,
        limit: 50,
        offset: 0,
        only_excluded: false,
      })
    )
    expect(await screen.findByText('ankara ofis mobilyası')).toBeInTheDocument()
    expect(screen.getByText('Elendi')).toBeInTheDocument()
    expect(screen.getByText('Tutuldu')).toBeInTheDocument()
  })

  it('yalnız elenenler kutusu işaretlenince only_excluded=true ile yeniden çeker', async () => {
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN_ASSIGNED] } as never)

    render(
      <MemoryRouter initialEntries={['/channels?run_id=24']}>
        <Channels />
      </MemoryRouter>
    )
    fireEvent.click(await screen.findByRole('button', { name: /Lokasyon Denetimi/i }))
    await screen.findByText('ankara ofis mobilyası')

    fireEvent.click(screen.getByLabelText(/Yalnız lokasyon nedeniyle elenenleri göster/i))

    await waitFor(() =>
      expect(brandProfileApi.getLocationAudit).toHaveBeenLastCalledWith(24, {
        brand_profile_id: 29,
        limit: 50,
        offset: 0,
        only_excluded: true,
      })
    )
  })

  it('run henüz channel_assigned/completed değilse panel gösterilmez', async () => {
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN_SCORED] } as never)

    render(
      <MemoryRouter initialEntries={['/channels?run_id=25']}>
        <Channels />
      </MemoryRouter>
    )
    await waitFor(() => expect(scoringApi.listRuns).toHaveBeenCalled())

    expect(screen.queryByRole('button', { name: /Lokasyon Denetimi/i })).not.toBeInTheDocument()
  })
})
