import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Scoring from './Scoring'
import { scoringApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', () => ({
  scoringApi: {
    getCapabilities: vi.fn(),
    listRuns: vi.fn(),
    getScores: vi.fn(),
    createRun: vi.fn(),
    executeRun: vi.fn(),
    deleteRun: vi.fn(),
    exportXlsx: vi.fn(),
  },
  apiErrorMessage: (err: unknown, fallback = 'Bir hata oluştu') => {
    const detail = (err as { detail?: { message?: string } } | null)?.detail
    return typeof detail?.message === 'string' ? detail.message : fallback
  },
}))

const mockedScoringApi = vi.mocked(scoringApi)

function response(data: unknown) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} } as never
}

function activeWorkspace() {
  return {
    id: 7,
    name: 'Test Marka',
    company_url: 'https://example.com',
    status: 'confirmed',
    profile_data: { anchor_texts: ['example'] },
    suggested_keywords: null,
    default_geo_target_id: null,
    default_language_id: null,
  }
}

const run = {
  id: 11,
  run_name: 'Run 11',
  status: 'scored',
  ads_capacity: 20,
  seo_capacity: 30,
  social_capacity: 25,
  default_relevance_coefficient: 1,
  keyword_source_filter: null,
  enable_ads: true,
  enable_seo: true,
  enable_social: true,
  skip_relevance: false,
  created_at: '2026-01-01T00:00:00',
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Scoring />
    </MemoryRouter>
  )
}

describe('Scoring table pagination', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.getState().setActiveWorkspace(activeWorkspace())
    mockedScoringApi.listRuns.mockResolvedValue(response([run]))
    mockedScoringApi.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
  })

  it('appends the next page with the current sort params', async () => {
    mockedScoringApi.getScores
      .mockResolvedValueOnce(
        response({
          total_scored: 3,
          scores: [
            { keyword_id: 1, keyword: 'alfa', ads_score: 9, seo_score: 1, social_score: 1 },
            { keyword_id: 2, keyword: 'beta', ads_score: 8, seo_score: 1, social_score: 1 },
          ],
        })
      )
      .mockResolvedValueOnce(
        response({
          total_scored: 3,
          scores: [{ keyword_id: 3, keyword: 'gama', ads_score: 7, seo_score: 1, social_score: 1 }],
        })
      )

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Görüntüle/i }))
    expect(await screen.findByText('alfa')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Daha fazla göster/i }))
    expect(await screen.findByText('gama')).toBeInTheDocument()
    expect(mockedScoringApi.getScores).toHaveBeenNthCalledWith(2, {
      runId: 11,
      brand_profile_id: 7,
      limit: 100,
      offset: 2,
      sort_by: 'ads_score',
      sort_dir: 'desc',
    })
  })

  it('shows an inline error when loading more fails without clearing the table', async () => {
    mockedScoringApi.getScores
      .mockResolvedValueOnce(
        response({
          total_scored: 2,
          scores: [{ keyword_id: 1, keyword: 'alfa', ads_score: 9, seo_score: 1, social_score: 1 }],
        })
      )
      .mockRejectedValueOnce(new Error('boom'))

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Görüntüle/i }))
    expect(await screen.findByText('alfa')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Daha fazla göster/i }))
    expect(await screen.findByText('Daha fazla skor yüklenemedi.')).toBeInTheDocument()
    expect(screen.getByText('alfa')).toBeInTheDocument()
  })
})

describe('Scoring v3 ürün akışı', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.getState().setActiveWorkspace(activeWorkspace())
    mockedScoringApi.listRuns.mockResolvedValue(response([run]))
  })

  async function openModal() {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Yeni Skorlama/i }))
  }

  it('motoru salt-okunur v3 gösterir ve seçim sunmaz', async () => {
    mockedScoringApi.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    await openModal()

    expect(screen.getByLabelText('Analiz motoru')).toHaveTextContent('v3 — kilitli üretim motoru')
    expect(screen.queryByTitle('Skorlama/seçim algoritma sürümü')).toBeNull()
  })

  it('daima v3 ve auto_assign_channels=true gönderir', async () => {
    mockedScoringApi.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    mockedScoringApi.createRun.mockResolvedValue(response({ id: 101 }))
    await openModal()

    fireEvent.click(screen.getByRole('button', { name: /^Oluştur$/i }))
    await waitFor(() =>
      expect(mockedScoringApi.createRun).toHaveBeenCalledWith(
        expect.objectContaining({ algorithm_version: 'v3', auto_assign_channels: true })
      )
    )
  })

  it('v3 kapalıysa legacy motora düşmez ve oluşturmayı engeller', async () => {
    mockedScoringApi.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: false }))
    await openModal()

    expect(await screen.findByText(/Motor v3 sunucuda kapalı/i)).toBeInTheDocument()
    const create = screen.getByRole('button', { name: /^Oluştur$/i })
    expect(create).toBeDisabled()
    fireEvent.click(create)
    expect(mockedScoringApi.createRun).not.toHaveBeenCalled()
  })

  it('capability hatasında legacy motora düşmez', async () => {
    mockedScoringApi.getCapabilities.mockRejectedValue(new Error('network'))
    await openModal()

    expect(await screen.findByText(/Motor v3 sunucuda kapalı/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Oluştur$/i })).toBeDisabled()
    expect(mockedScoringApi.createRun).not.toHaveBeenCalled()
  })

  it('legacy v2.1 run kartlarını görüntülemeyi sürdürür', async () => {
    mockedScoringApi.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    mockedScoringApi.listRuns.mockResolvedValue(response([{ ...run, algorithm_version: 'v2_1' }]))
    renderPage()
    expect(await screen.findByText('v2.1')).toBeInTheDocument()
  })

  it('v3 sonucunda gerçek skor, aile ve teslim durumunu gösterir', async () => {
    mockedScoringApi.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    mockedScoringApi.listRuns.mockResolvedValue(
      response([{ ...run, algorithm_version: 'v3', status: 'channel_assigned' }])
    )
    mockedScoringApi.getScores.mockResolvedValue(
      response({
        algorithm_version: 'v3',
        total_scored: 1,
        scores: [
          {
            keyword_id: 1,
            keyword: 'spiral çelik boru',
            family_id: 'boru',
            family_name: 'Çelik Borular',
            ads_score: 81.25,
            ads_rank: 2,
            ads_final_rank: 1,
            seo_score: 72.5,
            seo_rank: 4,
            seo_exclude_reason: 'CAPACITY_LIMIT',
            social_score: 66.75,
            social_rank: 3,
            social_final_rank: 2,
            social_priority: 'TREND_CONTENT',
          },
        ],
      })
    )

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Görüntüle/i }))

    expect(await screen.findByText('spiral çelik boru')).toBeInTheDocument()
    expect(screen.getByText('Çelik Borular')).toBeInTheDocument()
    expect(screen.getByText('81.25')).toBeInTheDocument()
    expect(screen.getByText('Teslim #1')).toBeInTheDocument()
    expect(screen.getByText('Elendi: CAPACITY_LIMIT')).toBeInTheDocument()
    expect(screen.getByText(/TREND_CONTENT/)).toBeInTheDocument()
  })
})
