import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ScoreResults from './ScoreResults'
import { scoringApi } from '../services/api'

vi.mock('../services/api', () => ({
  scoringApi: { getScores: vi.fn() },
}))

const mockedScoringApi = vi.mocked(scoringApi)

function response(data: unknown) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} } as never
}

describe('ScoreResults motor v3 görünümü', () => {
  beforeEach(() => vi.clearAllMocks())

  it('Anahtar Kelimeler sayfasında gerçek skor, aile ve kanal kararlarını gösterir', async () => {
    mockedScoringApi.getScores.mockResolvedValue(
      response({
        algorithm_version: 'v3',
        total_scored: 1,
        scores: [
          {
            keyword_id: 10,
            keyword: 'çelik boru üretimi',
            family_id: 'steel_pipe',
            family_name: 'Çelik Boru',
            ads_score: 78.2,
            ads_rank: 1,
            ads_final_rank: 1,
            seo_score: 65.4,
            seo_rank: 5,
            seo_exclude_reason: 'CAPACITY_LIMIT',
            social_score: 58.6,
            social_rank: 3,
            social_final_rank: 2,
            social_priority: 'SECONDARY',
          },
        ],
      })
    )

    render(<ScoreResults runId={51} workspaceId={50} />)

    expect(await screen.findByText('çelik boru üretimi')).toBeInTheDocument()
    expect(screen.getByText('Çelik Boru')).toBeInTheDocument()
    expect(screen.getByText('78.2000')).toBeInTheDocument()
    expect(screen.getByText('Havuzda · 1. sıra')).toBeInTheDocument()
    expect(screen.getByText('Kanal kapasitesi nedeniyle seçilmedi')).toBeInTheDocument()
    expect(screen.getByText('Aday sırası 3 · İkincil')).toBeInTheDocument()
    // Ham motor kodları ve teknik aile kimliği ekrana basılmaz
    expect(screen.queryByText(/CAPACITY_LIMIT|SECONDARY|steel_pipe/)).not.toBeInTheDocument()
  })

  it('art arda iki sıralamada geç dönen eski yanıt yenisini ezmez', async () => {
    const row = (kw: string) => ({
      keyword_id: kw.length,
      keyword: kw,
      seo_score: 1,
      social_score: 1,
    })
    mockedScoringApi.getScores.mockResolvedValueOnce(
      response({ algorithm_version: 'v3', total_scored: 1, scores: [row('ilk')] })
    )
    render(<ScoreResults runId={51} workspaceId={50} />)
    expect(await screen.findByText('ilk')).toBeInTheDocument()

    let resolveSeo: (v: unknown) => void = () => {}
    mockedScoringApi.getScores
      .mockImplementationOnce(() => new Promise((r) => (resolveSeo = r)) as never)
      .mockResolvedValueOnce(
        response({ algorithm_version: 'v3', total_scored: 1, scores: [row('social-sirali')] })
      )

    fireEvent.click(screen.getByRole('button', { name: /^SEO/ }))
    fireEvent.click(screen.getByRole('button', { name: /^SOCIAL/ }))
    expect(await screen.findByText('social-sirali')).toBeInTheDocument()

    resolveSeo(response({ algorithm_version: 'v3', total_scored: 1, scores: [row('seo-sirali')] }))
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('seo-sirali')).not.toBeInTheDocument()
    expect(screen.getByText('social-sirali')).toBeInTheDocument()
  })

  it('skorları 25lik sayfalarla sunucudan ister ve konumu gösterir', async () => {
    const row = (id: number) => ({ keyword_id: id, keyword: `kw ${id}`, ads_score: 1 })
    mockedScoringApi.getScores.mockResolvedValue(
      response({
        algorithm_version: 'v3',
        total_scored: 60,
        scores: Array.from({ length: 25 }, (_, i) => row(i + 1)),
      })
    )

    render(<ScoreResults runId={51} workspaceId={50} />)

    expect(await screen.findByText('1–25 / 60 kelime')).toBeInTheDocument()
    expect(mockedScoringApi.getScores).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 25, offset: 0 })
    )

    fireEvent.click(screen.getByRole('button', { name: /Sonraki/ }))

    expect(await screen.findByText('26–50 / 60 kelime')).toBeInTheDocument()
    expect(mockedScoringApi.getScores).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 25, offset: 25 })
    )
  })
})
