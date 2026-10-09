/**
 * Plan 1.3 — SeoGeoPanel: görev anahtarı run'a bağlı, geç gelen yanıt yeni
 * run'ın panelini değiştirmez.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import SeoGeoPanel from './SeoGeoPanel'
import { useBrandStore } from '../../stores/brandStore'

const STORAGE = 'digitus_active_tasks'

const mocks = vi.hoisted(() => ({
  listSeoGeo: vi.fn(),
  bulkSeoGeo: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    generationApi: {
      ...actual.generationApi,
      listSeoGeo: mocks.listSeoGeo,
      bulkSeoGeo: mocks.bulkSeoGeo,
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

function storedTasks(): Record<string, string> {
  return JSON.parse(localStorage.getItem(STORAGE) || '{}')
}

describe('SeoGeoPanel görev kapsamı (run bazlı)', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.listSeoGeo.mockReset()
    mocks.bulkSeoGeo.mockReset()
    mocks.getStatus.mockReset()
    mocks.listSeoGeo.mockResolvedValue({ data: { items: [] } })
    mocks.getStatus.mockImplementation((taskId: string) =>
      Promise.resolve({
        data: {
          task_id: taskId,
          status: 'running',
          progress: 10,
          scoring_run_id: taskId === 'task-A' ? 1 : 2,
        },
      })
    )
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
  })

  it('A -> B geçişinde panel A’nın görevini göstermez', async () => {
    localStorage.setItem(STORAGE, JSON.stringify({ 'seo_task:10:1': 'task-A' }))
    const { rerender } = render(<SeoGeoPanel runId={1} />)
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()

    mocks.getStatus.mockClear()
    rerender(<SeoGeoPanel runId={2} />)

    expect(await screen.findByText('SEO İçerik Üret')).toBeTruthy()
    expect(mocks.getStatus).not.toHaveBeenCalledWith('task-A', 10)
    expect(storedTasks()['seo_task:10:1']).toBe('task-A')
  })

  it('A’da başlatılan isteğin geç yanıtı B’yi etkilemez', async () => {
    let resolveStart: (value: unknown) => void = () => {}
    mocks.bulkSeoGeo.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve
        })
    )
    const { rerender } = render(<SeoGeoPanel runId={1} />)
    fireEvent.click(await screen.findByText('SEO İçerik Üret'))
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()

    rerender(<SeoGeoPanel runId={2} />)
    expect(await screen.findByText('SEO İçerik Üret')).toBeTruthy()

    await act(async () => {
      resolveStart({ data: { task_id: 'task-A' } })
    })

    expect(screen.getByText('SEO İçerik Üret')).toBeTruthy()
    expect(mocks.getStatus).not.toHaveBeenCalled()
    await waitFor(() => expect(storedTasks()['seo_task:10:1']).toBe('task-A'))
    expect(storedTasks()['seo_task:10:2']).toBeUndefined()
  })
})
