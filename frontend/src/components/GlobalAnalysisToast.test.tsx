import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GlobalAnalysisToast from './GlobalAnalysisToast'
import { tasksApi } from '../services/api'
import { useAnalysisToastStore } from '../stores/analysisToastStore'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', () => ({ tasksApi: { getStatus: vi.fn() } }))

const getStatus = vi.mocked(tasksApi.getStatus)
const reply = (task_id: string, status: string, extra: object = {}) =>
  getStatus.mockResolvedValue({
    data: { task_id, status, progress: 50, scoring_run_id: 7, ...extra },
  } as never)

function Shell() {
  const navigate = useNavigate()
  const loc = useLocation()
  return (
    <>
      <button onClick={() => navigate('/other')}>go-other</button>
      <div data-testid="loc">{loc.pathname + loc.search}</div>
      <Routes>
        <Route path="*" element={<div>page</div>} />
      </Routes>
      <GlobalAnalysisToast />
    </>
  )
}

const track = (taskId: string) =>
  act(() =>
    useAnalysisToastStore.getState().track({
      taskId,
      runId: 7,
      workspaceId: 29,
      channels: { ads: true, seo: false, social: false },
    })
  )

describe('GlobalAnalysisToast', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    useAnalysisToastStore.setState({ task: null, dismissedTaskId: null })
    useBrandStore.setState({ activeWorkspace: { id: 29, name: 'W' } } as never)
  })

  it('survives route change, stays closed per task id, reappears for a new task', async () => {
    reply('t1', 'running')
    render(
      <MemoryRouter initialEntries={['/keywords']}>
        <Shell />
      </MemoryRouter>
    )
    expect(screen.queryByText('Yapay zekâ analizi çalışıyor')).not.toBeInTheDocument()
    track('t1')
    expect(await screen.findByText('Yapay zekâ analizi çalışıyor')).toBeInTheDocument()

    fireEvent.click(screen.getByText('go-other'))
    expect(screen.getByText('Yapay zekâ analizi çalışıyor')).toBeInTheDocument()

    fireEvent.click(screen.getByTitle('Kapat'))
    expect(screen.queryByText('Yapay zekâ analizi çalışıyor')).not.toBeInTheDocument()
    track('t1')
    expect(screen.queryByText('Yapay zekâ analizi çalışıyor')).not.toBeInTheDocument()

    reply('t2', 'running')
    track('t2')
    expect(await screen.findByText('Yapay zekâ analizi çalışıyor')).toBeInTheDocument()
  })

  it('completed: opens the run scores and lists only enabled channels', async () => {
    reply('t1', 'completed', { progress: 100 })
    render(
      <MemoryRouter initialEntries={['/ads']}>
        <Shell />
      </MemoryRouter>
    )
    track('t1')
    expect(await screen.findByText('Analiz tamamlandı')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Social/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Skorları gör'))
    await waitFor(() =>
      expect(screen.getByTestId('loc').textContent).toBe('/keywords?view=scores&run_id=7')
    )
  })

  it('shows cancelled as failed and hides for another workspace', async () => {
    reply('t1', 'cancelled')
    render(
      <MemoryRouter>
        <Shell />
      </MemoryRouter>
    )
    track('t1')
    expect(await screen.findByText('Analiz başarısız oldu')).toBeInTheDocument()
    expect(screen.getByText('Analiz iptal edildi')).toBeInTheDocument()
    act(() => useBrandStore.setState({ activeWorkspace: { id: 30, name: 'X' } } as never))
    expect(screen.queryByText('Analiz başarısız oldu')).not.toBeInTheDocument()
  })
})
