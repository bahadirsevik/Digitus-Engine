/**
 * Plan 1.3 — URL'deki task_id yalnız yönlendirilen run için uygulanır;
 * run değişince başka run'ın kayıtlı görevi ezilmez/silinmez.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Channels from './Channels'
import { channelsApi, scoringApi, tasksApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    scoringApi: { listRuns: vi.fn() },
    channelsApi: { getPools: vi.fn(), assign: vi.fn() },
    tasksApi: { ...actual.tasksApi, getStatus: vi.fn() },
  }
})

const STORAGE = 'digitus_active_tasks'
const RUN_A = { id: 24, run_name: 'Run A', status: 'channel_assigned' }
const RUN_B = { id: 25, run_name: 'Run B', status: 'channel_assigned' }

function storedTasks(): Record<string, string> {
  return JSON.parse(localStorage.getItem(STORAGE) || '{}')
}

describe('Channels URL task_id kapsamı', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    useBrandStore.setState({ activeWorkspace: { id: 29, name: 'Dijital' } } as never)
    vi.mocked(scoringApi.listRuns).mockResolvedValue({ data: [RUN_A, RUN_B] } as never)
    vi.mocked(channelsApi.getPools).mockResolvedValue({
      data: { channels: {}, capacities: {} },
    } as never)
    vi.mocked(tasksApi.getStatus).mockImplementation(((taskId: string) =>
      Promise.resolve({
        data: {
          task_id: taskId,
          status: 'running',
          progress: 5,
          scoring_run_id: taskId === 'task-url' ? 24 : 25,
        },
      })) as never)
  })

  afterEach(() => {
    cleanup()
  })

  it('run değişince URL görevi B’nin anahtarına yazılmaz, B’nin kayıtlı görevi korunur', async () => {
    localStorage.setItem(STORAGE, JSON.stringify({ 'channel_assign:29:25': 'task-B' }))
    render(
      <MemoryRouter initialEntries={['/channels?run_id=24&task_id=task-url']}>
        <Channels />
      </MemoryRouter>
    )
    await waitFor(() => expect(storedTasks()['channel_assign:29:24']).toBe('task-url'))

    fireEvent.click(await screen.findByText('Run B'))

    await waitFor(() => expect(tasksApi.getStatus).toHaveBeenCalledWith('task-B', 29))
    expect(storedTasks()['channel_assign:29:25']).toBe('task-B')
    expect(storedTasks()['channel_assign:29:24']).toBe('task-url')
  })
})
