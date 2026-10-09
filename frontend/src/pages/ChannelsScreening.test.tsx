import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Channels from './Channels'
import { channelsApi, scoringApi } from '../services/api'
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
  }
})

const RUN = {
  id: 24,
  run_name: 'Dijital',
  status: 'channel_assigned',
  default_relevance_coefficient: 1,
}

describe('Channels server-controlled screening UI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.setState({ activeWorkspace: { id: 29, name: 'Dijital' } } as never)
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN] } as never)
    vi.mocked(channelsApi.getPools).mockResolvedValue({
      data: { channels: {}, capacities: {} },
    } as never)
  })

  it('keeps screening provider and cost controls out of the channel page', async () => {
    render(
      <MemoryRouter initialEntries={['/channels?run_id=24']}>
        <Channels />
      </MemoryRouter>
    )
    await waitFor(() => expect(scoringApi.listRuns).toHaveBeenCalled())

    expect(screen.queryByLabelText(/AI aday sıralaması/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/DeepSeek/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /üst sınır/i })).not.toBeInTheDocument()
  })
})
