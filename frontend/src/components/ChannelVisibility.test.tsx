import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AnalysisBanner, AnalysisToast } from './AnalysisProgress'
import ScoreResults from './ScoreResults'
import { scoringApi } from '../services/api'

vi.mock('../services/api', () => ({ scoringApi: { getScores: vi.fn() } }))

const ADS_ONLY = { ads: true, seo: false, social: false }
const btn = (re: RegExp) => screen.queryAllByRole('button', { name: re })[0] ?? null

describe('kanal görünürlüğü: yalnız açık kanallar', () => {
  beforeEach(() => vi.clearAllMocks())

  it('banner (done): ADS-only tek link ve eşleşen metin', () => {
    render(
      <MemoryRouter>
        <AnalysisBanner stage="done" taskProgress={100} channels={ADS_ONLY} onDismiss={vi.fn()} />
      </MemoryRouter>
    )
    expect(btn(/ADS/)).toBeInTheDocument()
    expect(btn(/SEO\+GEO/)).not.toBeInTheDocument()
    expect(btn(/Social/)).not.toBeInTheDocument()
    expect(screen.getByText(/İçerik üretimini ADS sayfasından yapabilirsiniz/)).toBeInTheDocument()
  })

  it('banner (done): bilinmeyen bayrakta üç link ve tam metin', () => {
    render(
      <MemoryRouter>
        <AnalysisBanner stage="done" taskProgress={100} onDismiss={vi.fn()} />
      </MemoryRouter>
    )
    expect(btn(/SEO\+GEO/)).toBeInTheDocument()
    expect(btn(/Social/)).toBeInTheDocument()
    expect(screen.getByText(/ADS, SEO\+GEO ve Social sayfalarından/)).toBeInTheDocument()
  })

  it('toast (done): ADS-only tek link', () => {
    render(
      <MemoryRouter>
        <AnalysisToast
          stage="done"
          taskProgress={100}
          channels={ADS_ONLY}
          onDismiss={vi.fn()}
          onOpen={vi.fn()}
        />
      </MemoryRouter>
    )
    expect(btn(/ADS/)).toBeInTheDocument()
    expect(btn(/SEO\+GEO/)).not.toBeInTheDocument()
    expect(btn(/Social/)).not.toBeInTheDocument()
  })

  it.each([
    ['v3', { algorithm_version: 'v3' }],
    ['legacy', {}],
  ])('ScoreResults %s: kapalı sütunlar gizlenir, bayrak yoksa hepsi görünür', async (_n, extra) => {
    vi.mocked(scoringApi.getScores).mockResolvedValue({
      data: {
        ...extra,
        total_scored: 1,
        scores: [{ keyword_id: 1, keyword: 'kelime', ads_score: 1, seo_score: 2, social_score: 3 }],
      },
    } as never)
    const { unmount } = render(<ScoreResults runId={1} workspaceId={2} channels={ADS_ONLY} />)
    expect(await screen.findByText('kelime')).toBeInTheDocument()
    expect(btn(/^ADS/)).toBeInTheDocument()
    expect(btn(/^SEO/)).not.toBeInTheDocument()
    expect(btn(/^SOCIAL/)).not.toBeInTheDocument()
    unmount()

    render(<ScoreResults runId={1} workspaceId={2} />)
    expect(await screen.findByText('kelime')).toBeInTheDocument()
    expect(btn(/^SEO/)).toBeInTheDocument()
    expect(btn(/^SOCIAL/)).toBeInTheDocument()
  })

  it('ScoreResults: ADS kapalı run ilk sorguyu açık kanalın skoruna göre sıralar', async () => {
    vi.mocked(scoringApi.getScores).mockResolvedValue({
      data: { total_scored: 0, scores: [] },
    } as never)
    render(
      <ScoreResults runId={1} workspaceId={2} channels={{ ads: false, seo: true, social: false }} />
    )
    await waitFor(() => expect(scoringApi.getScores).toHaveBeenCalled())
    expect(vi.mocked(scoringApi.getScores).mock.lastCall?.[0]).toMatchObject({
      sort_by: 'seo_score',
      sort_dir: 'desc',
    })
  })
})
