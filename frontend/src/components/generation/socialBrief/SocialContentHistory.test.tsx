import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import SocialPanel from '../SocialPanel'
import { useBrandStore } from '../../../stores/brandStore'
import {
  FORMAT_MATRIX,
  POOL,
  RUN_ID,
  WS_ID,
  apiError,
  deferred,
  historyItem,
  historyPage,
} from './testFixtures'

const mocks = vi.hoisted(() => ({
  getFormatMatrix: vi.fn(),
  createBrief: vi.fn(),
  listBriefs: vi.fn(),
  getBrief: vi.fn(),
  generateCategories: vi.fn(),
  getBriefState: vi.fn(),
  generateIdeas: vi.fn(),
  getIdeasAttempt: vi.fn(),
  retryIdeas: vi.fn(),
  getIdeasRetryAttempt: vi.fn(),
  generateContents: vi.fn(),
  getContentsAttempt: vi.fn(),
  getContentHistory: vi.fn(),
}))

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>()
  return { ...actual, socialBriefApi: mocks }
})

function setWorkspace(id: number) {
  useBrandStore.setState({
    activeWorkspace: {
      id,
      name: `Marka ${id}`,
      company_url: 'https://example.com',
      status: 'confirmed',
      profile_data: null,
      suggested_keywords: null,
    },
  })
}

async function openHistory() {
  fireEvent.click(screen.getByRole('button', { name: /Geçmiş Sosyal İçerikler/ }))
  return screen.findByRole('region', { name: /Geçmiş Sosyal İçerikler/ })
}

describe('Geçmiş Sosyal İçerikler (workspace geneli)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace(WS_ID)
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [] })
  })

  afterEach(() => cleanup())

  it('farklı run/brief ve legacy içerikleri tek listede, bağlam bilgisiyle gösterir', async () => {
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([
        historyItem(3, { idea_title: 'Yeni içerik', brief_id: 42, run_name: 'Eylül analizi' }),
        historyItem(2, {
          idea_title: 'Başka run içeriği',
          brief_id: 50,
          scoring_run_id: 11,
          run_name: 'Ağustos analizi',
          platform: 'twitter',
          content_format: 'thread',
          format_payload: null,
        }),
        historyItem(1, {
          idea_title: 'Legacy içerik',
          brief_id: null,
          brief_is_stale: null,
          is_stale: true,
          format_payload: null,
        }),
      ]),
    })
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    const region = await openHistory()
    expect(mocks.getContentHistory).toHaveBeenCalledWith(WS_ID, { limit: 20, offset: 0 })
    const firstToggle = await within(region).findByRole('button', { name: /Yeni içerik/ })
    expect(firstToggle).toHaveAttribute('aria-expanded', 'false')
    expect(within(region).queryByRole('article')).not.toBeInTheDocument()
    fireEvent.click(firstToggle)
    const first = within(region).getByRole('article', { name: 'Yeni içerik' })
    expect(first).toHaveTextContent('Brief #42 · Analiz: Eylül analizi')
    fireEvent.click(within(region).getByRole('button', { name: /Başka run içeriği/ }))
    const other = within(region).getByRole('article', { name: 'Başka run içeriği' })
    expect(other).toHaveTextContent('Analiz: Ağustos analizi')
    expect(other).toHaveTextContent('X · Thread')
    fireEvent.click(within(region).getByRole('button', { name: /Legacy içerik/ }))
    const legacy = within(region).getByRole('article', { name: 'Legacy içerik' })
    expect(legacy).toHaveTextContent('Eski (brief’siz) içerik')
    expect(within(legacy).getByText(/Güncel değil/)).toBeInTheDocument()
    // Sıra sunucudan geldiği gibi korunur (en yeniden eskiye)
    const titles = Array.from(region.querySelectorAll('.sb-history-row-title')).map(
      (title) => title.textContent
    )
    expect(titles).toEqual(['Yeni içerik', 'Başka run içeriği', 'Legacy içerik'])
  })

  it('sonraki sayfayı ekler, yinelenen kart üretmez ve son sayfada düğmeyi gizler', async () => {
    mocks.getContentHistory
      .mockResolvedValueOnce({
        data: historyPage([historyItem(5), historyItem(4)], { total: 3, hasMore: true }),
      })
      .mockResolvedValueOnce({
        data: historyPage([historyItem(4), historyItem(3)], { total: 3, offset: 2 }),
      })
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    const region = await openHistory()
    await within(region).findByRole('button', { name: /İçerik fikri 5/ })
    expect(within(region).getByText(/Toplam 3 içerik/)).toBeInTheDocument()
    fireEvent.click(within(region).getByText('Daha fazla göster'))
    await within(region).findByRole('button', { name: /İçerik fikri 3/ })
    expect(mocks.getContentHistory).toHaveBeenLastCalledWith(WS_ID, { limit: 20, offset: 2 })
    expect(region.querySelectorAll('.sb-history-entry')).toHaveLength(3)
    expect(within(region).queryByText('Daha fazla göster')).not.toBeInTheDocument()
  })

  it('uzun veya başlıksız içerikleri kısa başlıkla listeler; metni yalnız açılınca gösterir', async () => {
    const longTitle = 'Uzun sosyal içerik başlığı '.repeat(6)
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([
        historyItem(7, { idea_title: longTitle, caption: 'Gizli uzun metin' }),
        historyItem(8, { idea_title: null, caption: 'İlk satır\nDevam metni' }),
      ]),
    })
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    const region = await openHistory()
    const title = await within(region).findByRole('button', { name: /Uzun sosyal içerik başlığı/ })
    expect(title.querySelector('.sb-history-row-title')?.textContent?.length).toBeLessThanOrEqual(
      90
    )
    expect(within(region).queryByText('Gizli uzun metin')).not.toBeInTheDocument()
    expect(within(region).getByRole('button', { name: /İlk satır/ })).toBeInTheDocument()
    fireEvent.click(title)
    expect(within(region).getByText('Gizli uzun metin')).toBeInTheDocument()
    fireEvent.click(title)
    expect(within(region).queryByText('Gizli uzun metin')).not.toBeInTheDocument()
  })

  it('boş ve hata durumlarını gösterir; hata sonrası tekrar denenebilir', async () => {
    mocks.getContentHistory
      .mockRejectedValueOnce(apiError(500, 'HISTORY_READ_FAILED'))
      .mockResolvedValueOnce({ data: historyPage([]) })
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    const region = await openHistory()
    expect(await within(region).findByText(/Sunucu hatası oluştu/)).toBeInTheDocument()
    fireEvent.click(within(region).getByText('Tekrar dene'))
    expect(await within(region).findByText(/henüz sosyal içerik üretilmedi/)).toBeInTheDocument()
  })

  it('workspace değişince panel kapanır ve eski workspace yanıtı yeni görünüme girmez', async () => {
    const slow = deferred<{ data: ReturnType<typeof historyPage> }>()
    mocks.getContentHistory.mockImplementation((ws: number) =>
      ws === WS_ID ? slow.promise : Promise.resolve({ data: historyPage([]) })
    )
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    await openHistory()
    act(() => setWorkspace(8))
    await waitFor(() =>
      expect(
        screen.queryByRole('region', { name: /Geçmiş Sosyal İçerikler/ })
      ).not.toBeInTheDocument()
    )
    const region = await openHistory()
    expect(mocks.getContentHistory).toHaveBeenLastCalledWith(8, { limit: 20, offset: 0 })
    await act(async () =>
      slow.resolve({
        data: historyPage([historyItem(99, { idea_title: 'Eski workspace içeriği' })]),
      })
    )
    expect(within(region).queryByText('Eski workspace içeriği')).not.toBeInTheDocument()
    expect(within(region).getByText(/henüz sosyal içerik üretilmedi/)).toBeInTheDocument()
  })
})
