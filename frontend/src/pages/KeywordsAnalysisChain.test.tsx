import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Keywords from './Keywords'
import { channelsApi, scoringApi, tasksApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../components/StartAnalysisModal', () => ({
  default: ({ onStarted }: { onStarted: (runId: number) => void }) => (
    <button type="button" onClick={() => onStarted(24)}>
      mock-start
    </button>
  ),
}))

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    keywordsApi: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
      getStats: vi.fn().mockResolvedValue({ data: {} }),
    },
    scoringApi: {
      listRuns: vi.fn(),
      getRun: vi.fn(),
      getScores: vi.fn().mockResolvedValue({ data: { scores: [], total: 0 } }),
    },
    tasksApi: { listByRun: vi.fn(), getStatus: vi.fn() },
  }
})

const RUN = {
  id: 24,
  run_name: 'Dijital',
  status: 'channel_assigning',
  enable_ads: false,
  enable_seo: false,
  enable_social: true,
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/keywords']}>
      <Keywords />
    </MemoryRouter>
  )
}

async function startAnalysis() {
  renderPage()
  fireEvent.click(await screen.findByText('mock-start'))
}

describe('Keywords V3 analysis chain', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useBrandStore.setState({ activeWorkspace: { id: 29, name: 'Dijital' } } as never)
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN] } as never)
    vi.mocked(scoringApi.getRun).mockResolvedValue({ data: RUN } as never)
    vi.mocked(tasksApi.listByRun).mockResolvedValue({ data: [] } as never)
  })

  it('has no screening poll left in the client', () => {
    expect((channelsApi as unknown as Record<string, unknown>).getScreeningStatus).toBeUndefined()
  })

  it('shows engine message and only the enabled channel steps while the task runs', async () => {
    // Uçun GERÇEK biçimi: { tasks, total } (TaskListResponse). Eski testler dizi
    // veriyordu ve sayfanın görevi hiç bulamadığını gizliyordu.
    vi.mocked(tasksApi.listByRun).mockResolvedValue({
      data: {
        tasks: [{ task_id: 't1', task_type: 'channel_assignment', status: 'running' }],
        total: 1,
      },
    } as never)
    vi.mocked(tasksApi.getStatus).mockResolvedValue({
      data: {
        task_id: 't1',
        status: 'running',
        progress: 85,
        scoring_run_id: 24,
        result_data: { current_message: 'SOCIAL V5 motoru çalışıyor', step: 'x' },
      },
    } as never)

    await startAnalysis()

    expect(await screen.findByTestId('analysis-message')).toHaveTextContent(
      'SOCIAL V5 motoru çalışıyor'
    )
    const labels = Array.from(document.querySelectorAll('.kwx-banner .kwx-step')).map((el) =>
      (el.textContent ?? '').trim()
    )
    expect(labels).toEqual(['Analiz başladı', 'SOCIAL', 'Havuz teslimi'])
    expect(screen.queryByText(/İlgi skoru/)).not.toBeInTheDocument()
  })

  it('run failed before any task was discovered: banner shows the error, not stuck', async () => {
    vi.mocked(scoringApi.getRun).mockResolvedValue({
      data: { ...RUN, status: 'failed' },
    } as never)

    await startAnalysis()

    expect(await screen.findByText('Analiz sırasında hata oluştu')).toBeInTheDocument()
    expect(tasksApi.getStatus).not.toHaveBeenCalled()
  })

  it('task failed: banner shows the task error message', async () => {
    vi.mocked(tasksApi.listByRun).mockResolvedValue({
      data: {
        tasks: [{ task_id: 't2', task_type: 'channel_assignment', status: 'failed' }],
        total: 1,
      },
    } as never)
    vi.mocked(tasksApi.getStatus).mockResolvedValue({
      data: {
        task_id: 't2',
        status: 'failed',
        progress: 40,
        scoring_run_id: 24,
        error_message: 'motor çöktü',
      },
    } as never)

    await startAnalysis()

    expect(await screen.findByText('Analiz sırasında hata oluştu')).toBeInTheDocument()
    // Hata hem ana bantta hem sağ üst bildirimde görünür; bildirim "çalışıyor" demez
    await waitFor(() => expect(screen.getAllByText('motor çöktü')).toHaveLength(2))
    expect(screen.getByText('Analiz başarısız oldu')).toBeInTheDocument()
    expect(screen.queryByText('Yapay zekâ analizi çalışıyor')).not.toBeInTheDocument()
  })
})
