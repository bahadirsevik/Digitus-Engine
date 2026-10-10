/**
 * Plan 1.3 — görev takibi run'lar arasında karışmamalı.
 * Gerçek useTaskPolling kullanılır (yalnız API mock'lanır): anahtar run'a bağlı,
 * run değişince eski run'ın görevi gösterilmez, geç gelen yanıt yeni run'ın
 * panelini değiştirmez ve B'de başlayan görev A'nın kaydını ezmez.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AdsPanel from './AdsPanel'
import { useBrandStore } from '../../stores/brandStore'

const STORAGE = 'digitus_active_tasks'

const mocks = vi.hoisted(() => ({
  getAdsRsa: vi.fn(),
  createAdsRsa: vi.fn(),
  getStatus: vi.fn(),
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
      createAdsRsa: mocks.createAdsRsa,
      getAdsSets: vi.fn(() =>
        Promise.resolve({ data: { scoring_run_id: 1, sets: [], active_set_id: null } })
      ),
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

const TASKS: Record<string, { scoring_run_id: number }> = {
  'task-A': { scoring_run_id: 1 },
  'task-B': { scoring_run_id: 2 },
  'task-late': { scoring_run_id: 1 },
}

function storedTasks(): Record<string, string> {
  return JSON.parse(localStorage.getItem(STORAGE) || '{}')
}

const RESULT_TITLE = 'RSA Üretimi Tamamlandı'

describe('AdsPanel görev kapsamı (run bazlı)', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.getAdsRsa.mockReset()
    mocks.createAdsRsa.mockReset()
    mocks.getStatus.mockReset()
    mocks.getAdsRsa.mockResolvedValue({ data: { total_groups: 0 } })
    mocks.getStatus.mockImplementation((taskId: string) =>
      Promise.resolve({
        data: { task_id: taskId, status: 'running', progress: 10, ...TASKS[taskId] },
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
    localStorage.setItem(STORAGE, JSON.stringify({ 'ads_task:10:1': 'task-A' }))
    const { rerender } = render(<AdsPanel runId={1} />)
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()
    expect(mocks.getStatus).toHaveBeenCalledWith('task-A', 10)

    mocks.getStatus.mockClear()
    rerender(<AdsPanel runId={2} />)

    expect(await screen.findByText('Reklam Üret')).toBeTruthy()
    expect(screen.queryByText('Üretiliyor…')).toBeNull()
    expect(mocks.getStatus).not.toHaveBeenCalledWith('task-A', 10)
    // A'nın kaydı yerinde durur (geri dönülünce izlenmeye devam eder)
    expect(storedTasks()['ads_task:10:1']).toBe('task-A')
  })

  it('eski (run’sız) anahtar yok sayılır', async () => {
    localStorage.setItem(STORAGE, JSON.stringify({ 'ads_task:10': 'task-A' }))
    render(<AdsPanel runId={1} />)
    expect(await screen.findByText('Reklam Üret')).toBeTruthy()
    expect(mocks.getStatus).not.toHaveBeenCalled()
  })

  it('görev başka run’a aitse (scoring_run_id uyuşmaz) panel onu kendisinin saymaz', async () => {
    // Run 2 anahtarında yanlışlıkla run 1'in görevi duruyor
    localStorage.setItem(STORAGE, JSON.stringify({ 'ads_task:10:2': 'task-A' }))
    render(<AdsPanel runId={2} />)
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalledWith('task-A', 10))
    expect(screen.queryByText('Üretiliyor…')).toBeNull()
    expect(await screen.findByText('Reklam Üret')).toBeTruthy()
    await waitFor(() => expect(storedTasks()['ads_task:10:2']).toBeUndefined())
  })

  it('A’nın geç gelen sonuç yanıtı B panelini değiştirmez', async () => {
    let resolveA: (value: unknown) => void = () => {}
    mocks.getAdsRsa.mockImplementation((runId: number) =>
      runId === 1
        ? new Promise((resolve) => {
            resolveA = resolve
          })
        : Promise.resolve({ data: { total_groups: 0 } })
    )

    const { rerender } = render(<AdsPanel runId={1} />)
    await waitFor(() => expect(mocks.getAdsRsa).toHaveBeenCalledWith(1, 10, undefined))

    rerender(<AdsPanel runId={2} />)
    await waitFor(() => expect(mocks.getAdsRsa).toHaveBeenCalledWith(2, 10, undefined))

    await act(async () => {
      resolveA({ data: { total_groups: 3, ad_groups: [] } })
    })

    expect(screen.queryByText(RESULT_TITLE)).toBeNull()
  })

  it('A’da başlatılan isteğin geç yanıtı B’yi etkilemez, A’nın anahtarına yazılır', async () => {
    let resolveStart: (value: unknown) => void = () => {}
    mocks.createAdsRsa.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve
        })
    )

    const { rerender } = render(<AdsPanel runId={1} />)
    fireEvent.click(await screen.findByText('Reklam Üret'))
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()

    rerender(<AdsPanel runId={2} />)
    // B’de A’nın yükleme durumu taşınmaz
    expect(await screen.findByText('Reklam Üret')).toBeTruthy()

    await act(async () => {
      resolveStart({ data: { task_id: 'task-late', generation_set_id: 9 } })
    })

    expect(screen.getByText('Reklam Üret')).toBeTruthy()
    expect(screen.queryByText('Üretiliyor…')).toBeNull()
    expect(mocks.getStatus).not.toHaveBeenCalledWith('task-late', 10)
    expect(storedTasks()['ads_task:10:1']).toBe('task-late')
    expect(storedTasks()['ads_task:10:2']).toBeUndefined()
  })

  it('A -> B -> A: B’deyken gelen geç görev, A’ya dönünce gösterilir ve yoklanır', async () => {
    let resolveStart: (value: unknown) => void = () => {}
    mocks.createAdsRsa.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve
        })
    )

    const { rerender } = render(<AdsPanel runId={1} />)
    fireEvent.click(await screen.findByText('Reklam Üret'))
    rerender(<AdsPanel runId={2} />)
    expect(await screen.findByText('Reklam Üret')).toBeTruthy()

    await act(async () => {
      resolveStart({ data: { task_id: 'task-late', generation_set_id: 9 } })
    })
    expect(storedTasks()['ads_task:10:1']).toBe('task-late')

    rerender(<AdsPanel runId={1} />)
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalledWith('task-late', 10))
  })

  it('A’nın tamamlanmış görevi A -> B -> A sonrası yeniden yoklanmaz', async () => {
    localStorage.setItem(STORAGE, JSON.stringify({ 'ads_task:10:1': 'task-A' }))
    mocks.getStatus.mockImplementation((taskId: string) =>
      Promise.resolve({
        data: { task_id: taskId, status: 'completed', progress: 100, ...TASKS[taskId] },
      })
    )

    const { rerender } = render(<AdsPanel runId={1} />)
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalledWith('task-A', 10))
    await waitFor(() => expect(storedTasks()['ads_task:10:1']).toBeUndefined())

    rerender(<AdsPanel runId={2} />)
    expect(await screen.findByText('Reklam Üret')).toBeTruthy()
    mocks.getStatus.mockClear()
    rerender(<AdsPanel runId={1} />)
    expect(await screen.findByText('Reklam Üret')).toBeTruthy()

    expect(mocks.getStatus).not.toHaveBeenCalledWith('task-A', 10)
  })

  it('başlatma sürerken panel unmount olursa görev kimliği kaybolmaz (generate)', async () => {
    let resolveStart: (value: unknown) => void = () => {}
    mocks.createAdsRsa.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve
        })
    )

    const first = render(<AdsPanel runId={1} />)
    fireEvent.click(await screen.findByText('Reklam Üret'))
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()

    first.unmount()
    await act(async () => {
      resolveStart({ data: { task_id: 'task-late', generation_set_id: 9 } })
    })
    expect(storedTasks()['ads_task:10:1']).toBe('task-late')
    expect(mocks.getStatus).not.toHaveBeenCalled()

    render(<AdsPanel runId={1} />)
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalledWith('task-late', 10))
  })

  it('grup yeniden üretimi başlatılırken unmount olursa görev kimliği kaybolmaz', async () => {
    mocks.getAdsRsa.mockResolvedValue({
      data: {
        total_groups: 1,
        ad_groups: [{ id: 5, group_name: 'Grup 1', headlines: [], descriptions: [] }],
        generation_set: {
          id: 3,
          scoring_run_id: 1,
          version_number: 1,
          status: 'active',
          is_stale: false,
          groups_count: 1,
        },
      },
    })
    const regenerate = vi.fn()
    let resolveStart: (value: unknown) => void = () => {}
    regenerate.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve
        })
    )
    const api = await import('../../services/api')
    const spy = vi.spyOn(api.generationApi, 'regenerateAdGroup').mockImplementation(regenerate)

    const first = render(<AdsPanel runId={1} />)
    fireEvent.click(await screen.findByText('Yeniden Üret'))
    await waitFor(() => expect(regenerate).toHaveBeenCalled())

    first.unmount()
    await act(async () => {
      resolveStart({ data: { task_id: 'task-regen', generation_set_id: 11 } })
    })
    expect(storedTasks()['ads_task:10:1']).toBe('task-regen')

    render(<AdsPanel runId={1} />)
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()
    await waitFor(() => expect(mocks.getStatus).toHaveBeenCalledWith('task-regen', 10))
    spy.mockRestore()
  })

  it('B’de başlatılan görev A’nın kaydını ezmez', async () => {
    localStorage.setItem(STORAGE, JSON.stringify({ 'ads_task:10:1': 'task-A' }))
    mocks.createAdsRsa.mockResolvedValue({ data: { task_id: 'task-B', generation_set_id: 7 } })

    const { rerender } = render(<AdsPanel runId={1} />)
    expect(await screen.findByText('Üretiliyor…')).toBeTruthy()

    rerender(<AdsPanel runId={2} />)
    fireEvent.click(await screen.findByText('Reklam Üret'))

    await waitFor(() => expect(storedTasks()['ads_task:10:2']).toBe('task-B'))
    expect(storedTasks()['ads_task:10:1']).toBe('task-A')
  })
})
