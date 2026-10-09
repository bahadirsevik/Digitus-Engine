/**
 * P7 Adım 5 — Social içerik üretimi async akışı (stepper davranışı):
 * - task tamamlanınca /generation/social/{run_id} çekilir ve YALNIZ
 *   result_data.content_ids'teki yeni içerikler store'a konur (eski karışmaz)
 * - polling RUN bazlı storage key ile kurulur (workspace-key sızıntısı yok)
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import SocialStepper from './SocialStepper'
import { useBrandStore } from '../stores/brandStore'
import { useSocialStore } from '../stores/socialStore'

const getSocialResults = vi.fn(() =>
  Promise.resolve({
    data: {
      contents: [
        { id: 101, idea_id: 1, hooks: [], caption: 'Eski içerik', cta_text: 'x', hashtags: [] },
        { id: 202, idea_id: 2, hooks: [], caption: 'Yeni içerik', cta_text: 'y', hashtags: [] },
      ],
    },
  })
)

vi.mock('../services/api', () => ({
  scoringApi: { listRuns: vi.fn(() => Promise.resolve({ data: [] })) },
  brandProfileApi: { getProfile: vi.fn(() => Promise.reject(new Error('yok'))) },
  generationApi: {
    getSocialResults: (...args: unknown[]) => getSocialResults(...(args as [])),
    createSocialContentsAsync: vi.fn(() => Promise.resolve({ data: { task_id: 't-new' } })),
    createSocialCategories: vi.fn(),
    createSocialIdeas: vi.fn(),
    createSocialContents: vi.fn(),
  },
}))

const pollingCalls: unknown[][] = []
let pollingResultData: Record<string, unknown> = { content_ids: [202] }
vi.mock('../hooks/useTaskPolling', () => ({
  useTaskPolling: (...args: unknown[]) => {
    pollingCalls.push(args)
    return {
      status: { status: 'completed', progress: 100 },
      progress: 100,
      isActive: false,
      isCompleted: true,
      isFailed: false,
      resultData: pollingResultData,
      errorMessage: null,
    }
  },
  getStoredTaskId: () => null,
  setStoredTaskId: () => undefined,
}))

describe('SocialStepper async contents', () => {
  beforeEach(() => {
    pollingCalls.length = 0
    pollingResultData = { content_ids: [202] }
    getSocialResults.mockClear()
    window.localStorage.clear()
    useSocialStore.getState().reset()
    useBrandStore.setState({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      activeWorkspace: { id: 10, name: 'WS', status: 'confirmed' } as any,
    })
    useSocialStore.setState({
      workspaceId: 10,
      scoringRunId: 5,
      step: 3,
      taskId: 't-abc',
      brandName: 'Marka',
    })
  })

  it("task bitince yalnız yeni content_ids store'a konur ve 4. adıma geçilir", async () => {
    render(<SocialStepper hideRunSelector />)

    await waitFor(() => {
      expect(useSocialStore.getState().step).toBe(4)
    })
    const contents = useSocialStore.getState().contents
    expect(contents).toHaveLength(1)
    expect(contents[0].id).toBe(202)
    expect(getSocialResults).toHaveBeenCalledWith(5, 10)
    // İşlem bitti; taskId temizlendi
    expect(useSocialStore.getState().taskId).toBeNull()
  })

  it('polling RUN bazlı key ile kurulur', () => {
    render(<SocialStepper hideRunSelector />)
    const keys = pollingCalls.map((args) => args[1])
    expect(keys).toContain('social_content:10:5')
  })

  it('content_ids yoksa eski run iceriklerini yeni sonuc gibi gostermez', async () => {
    pollingResultData = {}

    render(<SocialStepper hideRunSelector />)

    await waitFor(() => {
      expect(useSocialStore.getState().taskId).toBeNull()
    })
    expect(getSocialResults).not.toHaveBeenCalled()
    expect(useSocialStore.getState().step).toBe(3)
    expect(useSocialStore.getState().contents).toEqual([])
  })
})
