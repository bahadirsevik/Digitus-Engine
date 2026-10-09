import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Keywords from './Keywords'
import { channelsApi, keywordsApi, scoringApi, tasksApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    keywordsApi: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
      getStats: vi.fn().mockResolvedValue({ data: {} }),
    },
    scoringApi: {
      listRuns: vi.fn().mockResolvedValue({ data: [] }),
      getScores: vi.fn().mockResolvedValue({ data: { scores: [], total: 0 } }),
    },
    tasksApi: { listByRun: vi.fn().mockResolvedValue({ data: [] }) },
    channelsApi: {
      getScreeningStatus: vi.fn().mockResolvedValue({ data: { exists: false } }),
      getPools: vi.fn().mockResolvedValue({ data: { pools: {}, capacities: {} } }),
    },
  }
})

const RUN = { id: 24, run_name: 'Dijital', status: 'channel_assigned' }

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/keywords?view=scores&run_id=24']}>
      <Keywords />
    </MemoryRouter>
  )
}

describe('Anahtar Kelimeler server-controlled screening UI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.setState({ activeWorkspace: { id: 29, name: 'Dijital' } } as never)
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN] } as never)
    vi.mocked(keywordsApi.list).mockResolvedValue({
      data: { items: [], total: 0 },
    } as never)
    vi.mocked(tasksApi.listByRun).mockResolvedValue({ data: [] } as never)
  })

  it('never exposes provider mode, price approval, or a separate screening card', async () => {
    vi.mocked(channelsApi.getScreeningStatus).mockResolvedValue({
      data: {
        exists: true,
        status: 'completed',
        cost_usd: 0.036041,
        applied_to_live_pool: true,
      },
    } as never)

    renderPage()
    await waitFor(() => expect(scoringApi.listRuns).toHaveBeenCalled())

    expect(screen.queryByLabelText(/AI aday sıralaması/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/DeepSeek/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/\$0\.0360/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /üst sınır/i })).not.toBeInTheDocument()
  })
})
