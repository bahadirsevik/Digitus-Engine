import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useExportJob } from './useExportJob'
import { MAX_POLL_FAILURES } from './useTaskPolling'
import { exportApi } from '../services/api'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    exportApi: { create: vi.fn(), status: vi.fn(), download: vi.fn(), listForRun: vi.fn() },
  }
})

const api = vi.mocked(exportApi)
const flush = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}
const job = (status: string) => ({ export_id: 'e1', status, progress: 20, file_name: 'x.xlsx' })

describe('useExportJob durum yoklama hatası', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    api.listForRun.mockResolvedValue({ data: { exports: [job('processing')] } } as never)
  })
  afterEach(() => vi.useRealTimers())

  it('ardışık hatalarda busy korunur, pollFailed olur; recheck yoklamayı sürdürür', async () => {
    api.status.mockRejectedValue({ status: 0, message: 'Network Error' })
    const { result } = renderHook(() => useExportJob(9, 3))
    await flush(0) // geçmişten süren job devralınır
    expect(result.current.busy).toBe(true)

    for (let i = 0; i < MAX_POLL_FAILURES; i += 1) await flush(2000)
    expect(result.current.pollFailed).toBe(true)
    expect(result.current.busy).toBe(true) // takip bırakılmaz
    expect(result.current.error).toBeNull() // başarısızlık gibi sunulmaz
    const calls = api.status.mock.calls.length
    await flush(10000)
    expect(api.status.mock.calls.length).toBe(calls)

    api.status.mockResolvedValue({ data: job('completed') } as never)
    await act(async () => {
      result.current.recheck()
    })
    await flush(0)
    expect(result.current.pollFailed).toBe(false)
    expect(result.current.activeJob?.status).toBe('completed')
    expect(result.current.busy).toBe(false)
  })

  it('404 (job yok) pollFailed olmaz: takip bırakılır, busy kapanır', async () => {
    api.status.mockRejectedValue({ status: 404, message: 'Export bulunamadı.' })
    const { result } = renderHook(() => useExportJob(9, 3))
    await flush(0)
    expect(result.current.busy).toBe(true)
    await flush(2000)
    expect(result.current.pollFailed).toBe(false)
    expect(result.current.busy).toBe(false)
    expect(result.current.activeJob).toBeNull()
    const calls = api.status.mock.calls.length
    await flush(10000)
    expect(api.status.mock.calls.length).toBe(calls)
  })
})
