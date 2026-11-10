import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getStoredTaskId, MAX_POLL_FAILURES, useTaskPolling } from './useTaskPolling'
import { tasksApi } from '../services/api'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return { ...actual, tasksApi: { getStatus: vi.fn() } }
})

const getStatus = vi.mocked(tasksApi.getStatus)
const KEY = 'channel_assign:7:3'
const running = { data: { task_id: 't1', status: 'running', progress: 10, scoring_run_id: 3 } }
const networkError = { status: 0, message: 'Network Error' }

const flush = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useTaskPolling hata dayanıklılığı', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.localStorage.clear()
    getStatus.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('ardışık hatalardan sonra pollFailed olur, görev kaydı korunur, recheck sürdürür', async () => {
    getStatus.mockRejectedValue(networkError)
    const { result } = renderHook(() => useTaskPolling('t1', KEY, 1000, 7, 3))
    await flush(0) // ilk istek: 1. hata
    expect(result.current.pollFailed).toBe(false)
    await flush(1000)
    await flush(1000)
    expect(getStatus).toHaveBeenCalledTimes(MAX_POLL_FAILURES)
    expect(result.current.pollFailed).toBe(true)
    expect(result.current.isFailed).toBe(false) // görev başarısız SAYILMAZ
    expect(getStoredTaskId(KEY)).toBe('t1')

    // pollFailed iken yoklama durur
    await flush(5000)
    expect(getStatus).toHaveBeenCalledTimes(MAX_POLL_FAILURES)

    getStatus.mockResolvedValue(running as never)
    await act(async () => {
      result.current.recheck()
    })
    await flush(0)
    expect(result.current.pollFailed).toBe(false)
    expect(result.current.isActive).toBe(true)
    await flush(1000)
    expect(getStatus.mock.calls.length).toBeGreaterThan(MAX_POLL_FAILURES + 1)
  })

  it('404 (görev yok) pollFailed DEĞİL: kayıt silinir, yoklama durur, boşta durum', async () => {
    window.localStorage.setItem('digitus_active_tasks', JSON.stringify({ [KEY]: 't1' }))
    getStatus.mockRejectedValue({ status: 404, message: 'Görev bulunamadı.' })
    const { result } = renderHook(() => useTaskPolling('t1', KEY, 1000, 7, 3))
    await flush(0)
    await flush(5000)
    expect(getStatus).toHaveBeenCalledTimes(1)
    expect(result.current.pollFailed).toBe(false)
    expect(result.current.status).toBeNull()
    expect(result.current.isActive).toBe(false)
    expect(getStoredTaskId(KEY)).toBeNull()
  })

  it('başarı hata sayacını sıfırlar (kesintili hatalar pollFailed yapmaz)', async () => {
    getStatus
      .mockRejectedValueOnce(networkError)
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(running as never)
      .mockRejectedValueOnce(networkError)
      .mockRejectedValueOnce(networkError)
      .mockResolvedValue(running as never)
    const { result } = renderHook(() => useTaskPolling('t1', KEY, 1000, 7, 3))
    await flush(0)
    for (let i = 0; i < 5; i += 1) await flush(1000)
    expect(getStatus.mock.calls.length).toBeGreaterThanOrEqual(6)
    expect(result.current.pollFailed).toBe(false)
  })

  it('istekler çakışmaz: yanıt gelmeden yenisi başlamaz', async () => {
    let resolve: (v: unknown) => void = () => {}
    getStatus.mockImplementation(() => new Promise((r) => (resolve = r)) as never)
    renderHook(() => useTaskPolling('t1', KEY, 1000, 7, 3))
    await flush(0)
    await flush(5000)
    expect(getStatus).toHaveBeenCalledTimes(1)
    await act(async () => resolve(running))
    await flush(1000)
    expect(getStatus).toHaveBeenCalledTimes(2)
  })

  it('başka göreve geçilince eski görevin geç hatası yeni görevi pollFailed yapmaz', async () => {
    let rejectOld: (e: unknown) => void = () => {}
    getStatus.mockImplementationOnce(() => new Promise((_, rej) => (rejectOld = rej)) as never)
    getStatus.mockResolvedValue({
      data: { task_id: 't2', status: 'running', progress: 1, scoring_run_id: 3 },
    } as never)
    const { result, rerender } = renderHook(({ id }) => useTaskPolling(id, KEY, 1000, 7, 3), {
      initialProps: { id: 't1' },
    })
    await flush(0)
    rerender({ id: 't2' })
    await flush(0)
    await act(async () => rejectOld(networkError))
    await flush(0)
    expect(result.current.pollFailed).toBe(false)
    expect(result.current.status?.task_id).toBe('t2')
  })
})
