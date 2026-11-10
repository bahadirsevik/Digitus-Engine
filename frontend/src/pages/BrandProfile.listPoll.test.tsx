/**
 * Liste yoklaması: efekt yeniden kurulurken uçuşta olan eski tick yeni zinciri
 * çiftlememeli (ikinci zamanlayıcı zinciri / yetim zamanlayıcı yok).
 */
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'
import { workspaceApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

const RUNNING = {
  id: 10,
  name: 'Marka',
  company_url: 'https://brand.com',
  status: 'running',
  onboarding_flow: 'profile_first' as const,
  profile_data: null,
  suggested_keywords: null,
  deleted_at: null,
  created_at: '2026-01-01T00:00:00Z',
  run_count: 0,
}

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    workspaceApi: { list: vi.fn(), get: vi.fn(), update: vi.fn() },
  }
})

const POLL_MS = 3000
const flush = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('BrandProfile liste yoklaması', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    window.localStorage.clear()
    useBrandStore.setState({ activeWorkspace: null })
    vi.mocked(workspaceApi.list).mockResolvedValue({ data: [RUNNING] } as never)
  })
  afterEach(() => vi.useRealTimers())

  it('uçuştaki tick sırasında efekt yeniden kurulursa tek zincir kalır', async () => {
    render(
      <MemoryRouter initialEntries={['/brand-profile']}>
        <BrandProfile />
      </MemoryRouter>
    )
    await flush(0) // liste yüklenir, yoklama zamanlayıcısı kurulur

    let resolveSlow: (v: unknown) => void = () => {}
    vi.mocked(workspaceApi.get).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSlow = resolve
        }) as never
    )
    await flush(POLL_MS) // tick #1 başlar, yanıt bekler
    expect(workspaceApi.get).toHaveBeenCalledTimes(1)

    // Tick uçuştayken efekt yeniden kurulur (activeWorkspace bağımlılığı değişir)
    act(() => {
      useBrandStore.setState({
        activeWorkspace: {
          id: 10,
          name: 'Marka',
          company_url: 'https://brand.com',
          status: 'running',
          profile_data: null,
          suggested_keywords: null,
        },
      })
    })
    vi.mocked(workspaceApi.get).mockResolvedValue({ data: RUNNING } as never)
    await act(async () => {
      resolveSlow({ data: RUNNING })
    })

    vi.mocked(workspaceApi.get).mockClear()
    await flush(POLL_MS)
    // Yalnız yeni zincirin tick'i çalışır (eski tick yeniden zamanlama yapmaz)
    expect(workspaceApi.get).toHaveBeenCalledTimes(1)
  })
})
