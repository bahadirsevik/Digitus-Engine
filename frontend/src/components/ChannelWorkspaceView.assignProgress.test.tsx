import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ChannelWorkspaceView from './ChannelWorkspaceView'
import { useBrandStore } from '../stores/brandStore'

const mocks = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getPools: vi.fn(),
  listByRun: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    scoringApi: { ...actual.scoringApi, listRuns: mocks.listRuns },
    channelsApi: { ...actual.channelsApi, getPools: mocks.getPools },
    tasksApi: { ...actual.tasksApi, listByRun: mocks.listByRun, getStatus: mocks.getStatus },
  }
})
vi.mock('./generation/AdsPanel', () => ({ default: () => null }))

const TASK = { task_id: 't-1', task_type: 'channel_assignment', scoring_run_id: 10 }

function renderAds() {
  return render(
    <MemoryRouter initialEntries={['/ads?run_id=10']}>
      <ChannelWorkspaceView channel="ADS" />
    </MemoryRouter>
  )
}

describe('ADS sayfası kanal atama ilerlemesi', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    useBrandStore.setState({
      activeWorkspace: {
        id: 7,
        name: 'Marka 7',
        company_url: 'https://example.com',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
    mocks.listByRun.mockResolvedValue({ data: [{ ...TASK, status: 'running' }] })
    mocks.getPools.mockResolvedValue({ data: { channels: { ADS: [] } } })
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('atama sürerken boş havuz, havuzun tüm kanallar bitince açılacağını söyler', async () => {
    mocks.listRuns.mockResolvedValue({ data: [{ id: 10, status: 'channel_assigning' }] })
    mocks.getStatus.mockResolvedValue({
      data: {
        ...TASK,
        status: 'running',
        progress: 65,
        result_data: { current_message: 'SEO katı-2 motoru çalışıyor' },
      },
    })
    renderAds()
    expect(
      await screen.findByText(/tüm kanallar bitince burada kendiliğinden açılır/)
    ).toBeInTheDocument()
    expect(await screen.findByText('SEO katı-2 motoru çalışıyor')).toBeInTheDocument()
  })

  it('"Tamamlandı" kartı birkaç saniye sonra kendiliğinden kapanır', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.listRuns.mockResolvedValue({ data: [{ id: 10, status: 'channel_assigned' }] })
    mocks.getStatus.mockResolvedValue({ data: { ...TASK, status: 'completed', progress: 100 } })
    renderAds()
    expect(await screen.findByText('Tamamlandı')).toBeInTheDocument()
    await act(async () => {
      vi.advanceTimersByTime(3100)
    })
    expect(screen.queryByText('Tamamlandı')).not.toBeInTheDocument()
  })

  it('hata kartı kendiliğinden kapanmaz', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.listRuns.mockResolvedValue({ data: [{ id: 10, status: 'channel_assigned' }] })
    mocks.getStatus.mockResolvedValue({
      data: { ...TASK, status: 'failed', progress: 40, error_message: 'motor hatası' },
    })
    renderAds()
    expect(await screen.findByText('Hata')).toBeInTheDocument()
    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    expect(screen.getByText('Hata')).toBeInTheDocument()
  })
})
