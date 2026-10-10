import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import SocialBriefWorkspace from '../SocialBriefWorkspace'
import { useBrandStore } from '../../../stores/brandStore'
import {
  FORMAT_MATRIX,
  LOCKED_BRIEF,
  NETWORK_ERROR,
  POOL,
  RUN_ID,
  WS_ID,
  apiError,
  attempt,
  deferred,
  idea,
  ideasResponse,
  stateWithCategories,
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

const EMPTY_HISTORY = {
  data: { brand_profile_id: WS_ID, total: 0, limit: 50, offset: 0, has_more: false, items: [] },
}

function renderWorkspace(runId = RUN_ID) {
  return render(<SocialBriefWorkspace runId={runId} pool={POOL} pollIntervalMs={10} />)
}

async function openBrief() {
  fireEvent.click(await screen.findByText("Brief'i Aç →"))
  await screen.findByRole('navigation', { name: 'Sosyal brief adımları' })
  const next = screen.queryByRole('button', { name: /Fikirlere Geç/ })
  if (next) {
    await waitFor(() => expect(next).toBeEnabled())
    fireEvent.click(next)
  }
}

describe('Fikir üretimi, yoklama, kapsama ve tekrar deneme', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    useBrandStore.setState({
      activeWorkspace: {
        id: WS_ID,
        name: 'Dijital Ajans',
        company_url: 'https://example.com',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [LOCKED_BRIEF] })
    mocks.getBriefState.mockResolvedValue({ data: stateWithCategories() })
    mocks.getContentHistory.mockResolvedValue(EMPTY_HISTORY)
  })

  afterEach(() => cleanup())

  it('kategori kartının tamamı seçim yapar ve ileri geri gezilebilir', async () => {
    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByRole('button', { name: 'Geri' }))
    const categoryCard = screen
      .getByText('Ajanslar için pratik dijital pazarlama ipuçları.')
      .closest('.sb-category-card')!
    expect(categoryCard).toHaveClass('is-selected')
    fireEvent.click(screen.getByText('Ajanslar için pratik dijital pazarlama ipuçları.'))
    expect(categoryCard).toHaveClass('is-unselected')
    fireEvent.click(screen.getByText('Ajanslar için pratik dijital pazarlama ipuçları.'))
    expect(categoryCard).toHaveClass('is-selected')

    fireEvent.click(screen.getByRole('button', { name: /Fikirlere Geç/ }))
    expect(screen.getByRole('heading', { name: 'Fikirler' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Geri' }))
    expect(categoryCard).toHaveClass('is-selected')
  })

  it('fikir kartına tıklanarak seçim yapılır ve aşamalar arasında korunur', async () => {
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({
      data: ideasResponse(301, 'completed', [idea(1, 1)]),
    })
    renderWorkspace()
    await openBrief()
    const ideaCard = (await screen.findByText('Fikir 1')).closest('.sb-idea-card')!
    expect(ideaCard).toHaveClass('is-unselected')
    fireEvent.click(screen.getByText('Açıklama 1'))
    expect(ideaCard).toHaveClass('is-selected')
    fireEvent.click(screen.getByRole('button', { name: /İçeriklere Geç/ }))
    expect(screen.getByRole('heading', { name: 'İçerikler' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Geri' }))
    expect(ideaCard).toHaveClass('is-selected')
  })

  it('seçili kategorilerle üretimi başlatır, sonucu yoklar ve hedeflere göre gruplar', async () => {
    const pending = ideasResponse(301, 'pending', [])
    const done = ideasResponse(301, 'completed', [idea(1, 1), idea(2, 2), idea(3, 1)])
    mocks.generateIdeas.mockResolvedValue({ status: 202, data: pending })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: stateWithCategories() })
      .mockResolvedValueOnce({
        data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'pending') }),
      })
      .mockResolvedValue({
        data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed') }),
      })
    mocks.getIdeasAttempt
      .mockResolvedValueOnce({ data: ideasResponse(301, 'running', []) })
      .mockResolvedValue({ data: done })

    renderWorkspace()
    await openBrief()
    // Kategori seçimi: biri kaldırılır
    fireEvent.click(await screen.findByLabelText('SEO Rehberleri'))
    fireEvent.change(screen.getByLabelText('Kategori başına'), { target: { value: '2' } })
    fireEvent.click(screen.getByText('Fikirleri Üret'))

    await waitFor(() =>
      expect(mocks.generateIdeas).toHaveBeenCalledWith(
        LOCKED_BRIEF.id,
        {
          idempotency_key: expect.stringMatching(/^ideas-/),
          category_ids: [201],
          ideas_per_category: 2,
        },
        WS_ID
      )
    )
    expect(await screen.findByText('Fikir 1')).toBeInTheDocument()

    const postGroup = screen.getByRole('heading', { name: /Instagram · Post/ }).parentElement!
    expect(within(postGroup).getByText('Fikir 1')).toBeInTheDocument()
    expect(within(postGroup).getByText('Fikir 3')).toBeInTheDocument()
    const reelsGroup = screen.getByRole('heading', {
      name: /Instagram · Reels \(31 sn–1 dk\)/,
    }).parentElement!
    expect(within(reelsGroup).getByText('Fikir 2')).toBeInTheDocument()
    // Kart ayrıntıları: kategori, kelime, trend uyumu
    expect(within(postGroup).getAllByText('Pazarlama Taktikleri').length).toBeGreaterThan(0)
    expect(within(postGroup).getAllByText('Trend uyumu %70').length).toBe(2)
    // Sahte yüzde ilerleme gösterilmez
    expect(screen.queryByText(/%\s*tamamlandı/)).not.toBeInTheDocument()
  })

  it.each(['pending', 'running'] as const)(
    '%s fikir denemesinde temaya uygun yüklenme kartı gösterir, sahte yüzde göstermez',
    async (status) => {
      mocks.getBriefState.mockResolvedValue({
        data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', status) }),
      })
      mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, status, []) })

      renderWorkspace()
      await openBrief()

      const title = await screen.findByText('Fikirler üretiliyor')
      const loadingCard = title.closest('.sb-ideas-loading')
      expect(loadingCard).toBeInTheDocument()
      expect(
        within(loadingCard as HTMLElement).getByText('Fikirler hazırlanıyor')
      ).toBeInTheDocument()
      expect(
        screen.getByRole('progressbar', { name: 'Fikir üretimi sürüyor' })
      ).not.toHaveAttribute('aria-valuenow')
      expect(screen.queryByText(/%\s*tamamlandı/)).not.toBeInTheDocument()
    }
  )

  it('sayfa yenilenince sunucudaki fikir denemesi geri getirilir (POST yapılmaz)', async () => {
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({
      data: ideasResponse(301, 'completed', [idea(1, 1), idea(2, 2)]),
    })
    renderWorkspace()
    await openBrief()
    expect(await screen.findByText('Fikir 1')).toBeInTheDocument()
    expect(mocks.getIdeasAttempt).toHaveBeenCalledWith(LOCKED_BRIEF.id, 301, WS_ID)
    expect(mocks.generateIdeas).not.toHaveBeenCalled()
    expect(screen.queryByText('Fikirleri Üret')).not.toBeInTheDocument()
    // Kategoriler artık seçilemez (fikir üretimi başladı)
    expect(screen.queryByLabelText('Pazarlama Taktikleri')).not.toBeInTheDocument()
  })

  it('kısmi sonuçta eksik hedefi ve uyarıyı gösterir; retry yalnız eksik hedefler için çalışır ve kartlar yinelenmez', async () => {
    const partial = ideasResponse(301, 'partial', [idea(1, 1), idea(3, 1)], {
      coverage: [
        { target_id: 1, requested: 3, accepted: 2, missing: 1 },
        { target_id: 2, requested: 3, accepted: 0, missing: 3 },
      ],
      warnings: [{ target_id: 2, category_id: 201, reason_code: 'target_unfilled' }],
    })
    const stateBase = stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'partial') })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: stateBase })
      .mockResolvedValueOnce({
        data: { ...stateBase, idea_retry_attempts: [attempt(401, 'ideas_retry', 'running')] },
      })
      .mockResolvedValue({
        data: { ...stateBase, idea_retry_attempts: [attempt(401, 'ideas_retry', 'completed')] },
      })
    mocks.getIdeasAttempt.mockResolvedValue({ data: partial })
    mocks.retryIdeas.mockResolvedValue({
      status: 202,
      data: ideasResponse(401, 'pending', [idea(1, 1), idea(3, 1)]),
    })
    mocks.getIdeasRetryAttempt.mockResolvedValue({
      data: ideasResponse(401, 'completed', [idea(1, 1), idea(3, 1), idea(5, 2)]),
    })

    renderWorkspace()
    await openBrief()
    expect(await screen.findByText('Eksik hedef')).toBeInTheDocument()
    expect(screen.getByText(/1 hedef için henüz fikir yok/)).toBeInTheDocument()
    expect(screen.getByText(/Bu hedef için geçerli fikir üretilemedi/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Eksik hedefleri tekrar dene'))
    await waitFor(() =>
      expect(mocks.retryIdeas).toHaveBeenCalledWith(
        LOCKED_BRIEF.id,
        { idempotency_key: expect.stringMatching(/^ideas_retry-/), source_attempt_id: 301 },
        WS_ID
      )
    )
    expect(await screen.findByText('Fikir 5')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Eksik hedef')).not.toBeInTheDocument())
    expect(screen.getAllByText('Fikir 1')).toHaveLength(1)
    expect(screen.getAllByText('Fikir 3')).toHaveLength(1)
    // Tarihsel sonuç korunur: ilk üretim hâlâ "kısmen"
    expect(screen.getByText('İlk üretim: Kısmen tamamlandı')).toBeInTheDocument()
    expect(screen.queryByText('Eksik hedefleri tekrar dene')).not.toBeInTheDocument()
  })

  it('tüm hedefler dolu ama bir kategori boşsa (category_unfilled) tekrar dene sunulur ve onarım sonrası kaybolur', async () => {
    const partial = ideasResponse(301, 'partial', [idea(1, 1), idea(2, 2)], {
      coverage: [
        { target_id: 1, requested: 3, accepted: 1, missing: 2 },
        { target_id: 2, requested: 3, accepted: 1, missing: 2 },
      ],
      reason_code: 'category_unfilled',
      warnings: [{ target_id: 1, category_id: 202, reason_code: 'category_unfilled' }],
    })
    const stateBase = stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'partial') })
    mocks.getBriefState.mockResolvedValueOnce({ data: stateBase }).mockResolvedValue({
      data: { ...stateBase, idea_retry_attempts: [attempt(401, 'ideas_retry', 'completed')] },
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: partial })
    mocks.retryIdeas.mockResolvedValue({
      status: 202,
      data: ideasResponse(401, 'pending', [idea(1, 1), idea(2, 2)]),
    })
    mocks.getIdeasRetryAttempt.mockResolvedValue({
      data: ideasResponse(401, 'completed', [
        idea(1, 1),
        idea(2, 2),
        idea(7, 1, { category_id: 202 }),
      ]),
    })

    renderWorkspace()
    await openBrief()
    // Hedef eksik yok ama kategori boş: uyarı ve tekrar dene görünür
    expect(await screen.findByText(/1 kategori için henüz fikir yok/)).toBeInTheDocument()
    expect(screen.queryByText(/hedef için henüz fikir yok/)).not.toBeInTheDocument()
    expect(screen.getByText(/Bu kategori için geçerli fikir üretilemedi/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Boş kategorileri tekrar dene'))
    await waitFor(() =>
      expect(mocks.retryIdeas).toHaveBeenCalledWith(
        LOCKED_BRIEF.id,
        { idempotency_key: expect.stringMatching(/^ideas_retry-/), source_attempt_id: 301 },
        WS_ID
      )
    )
    expect(await screen.findByText('Fikir 7')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText(/kategori için henüz fikir yok/)).not.toBeInTheDocument()
    )
    expect(screen.queryByText('Boş kategorileri tekrar dene')).not.toBeInTheDocument()
  })

  it('eski kayıtlarda boş kategori ai_failed uyarısıyla gelir; retry yine sunulur', async () => {
    const partial = ideasResponse(301, 'partial', [idea(1, 1), idea(2, 2)], {
      reason_code: 'ai_failed',
      warnings: [
        { target_id: 1, category_id: 202, reason_code: 'ai_failed' },
        { target_id: 2, category_id: 202, reason_code: 'ai_failed' },
      ],
    })
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'partial') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: partial })
    renderWorkspace()
    await openBrief()
    expect(await screen.findByText('Boş kategorileri tekrar dene')).toBeInTheDocument()
    expect(screen.getByText(/1 kategori için henüz fikir yok: SEO Rehberleri/)).toBeInTheDocument()
  })

  it('yenileme sonrası fikirler yüklenmeden kapsama ve retry gösterilmez (yanlış "eksik hedef" yok)', async () => {
    const slow = deferred<{ data: ReturnType<typeof ideasResponse> }>()
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'partial') }),
    })
    mocks.getIdeasAttempt.mockReturnValue(slow.promise)
    renderWorkspace()
    await openBrief()
    expect(await screen.findByText('Fikirler yükleniyor...')).toBeInTheDocument()
    expect(screen.queryByText('Eksik hedef')).not.toBeInTheDocument()
    expect(screen.queryByText('Eksik hedefleri tekrar dene')).not.toBeInTheDocument()
    await act(async () =>
      slow.resolve({ data: ideasResponse(301, 'partial', [idea(1, 1), idea(2, 2)]) })
    )
    expect(await screen.findAllByText('Kota altında')).toHaveLength(2)
    expect(screen.queryByText('Fikirler yükleniyor...')).not.toBeInTheDocument()
  })

  it('retry ağ hatasında aynı anahtarla tekrarlanır; 409 sonrası anahtar bırakılır', async () => {
    const partial = ideasResponse(301, 'partial', [idea(1, 1)])
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'partial') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: partial })
    mocks.retryIdeas
      .mockRejectedValueOnce(NETWORK_ERROR)
      .mockRejectedValueOnce(apiError(409, 'ATTEMPT_CONFLICT'))
      .mockRejectedValueOnce(NETWORK_ERROR)
    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByText('Eksik hedefleri tekrar dene'))
    await screen.findByText(/Sunucuya ulaşılamadı/)
    fireEvent.click(screen.getByText('Eksik hedefleri tekrar dene'))
    await screen.findByText(/başka bir üretim çalışıyor/)
    fireEvent.click(screen.getByText('Eksik hedefleri tekrar dene'))
    await waitFor(() => expect(mocks.retryIdeas).toHaveBeenCalledTimes(3))
    const keys = mocks.retryIdeas.mock.calls.map((c) => c[1].idempotency_key)
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[1])
  })

  it('başarısız (fikirsiz) üretimde yeniden üretim sunulur, retry sunulmaz', async () => {
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({
        ideas_attempt: attempt(301, 'ideas', 'failed', { reason_code: 'idea_provider_error' }),
      }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({
      data: ideasResponse(301, 'failed', [], { reason_code: 'idea_provider_error' }),
    })
    mocks.generateIdeas.mockResolvedValue({ status: 202, data: ideasResponse(302, 'pending', []) })
    renderWorkspace()
    await openBrief()
    expect(await screen.findByText(/Yapay zekâ servisi hata verdi/)).toBeInTheDocument()
    expect(screen.getByText(/Sahte yedek fikir oluşturulmaz/)).toBeInTheDocument()
    expect(screen.queryByText('Eksik hedefleri tekrar dene')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Fikirleri Yeniden Üret'))
    await waitFor(() => expect(mocks.generateIdeas).toHaveBeenCalledTimes(1))
  })

  it('kota altında ama her hedef ≥1 fikir aldıysa retry sunulmaz', async () => {
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'partial') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({
      data: ideasResponse(301, 'partial', [idea(1, 1), idea(2, 2)]),
    })
    renderWorkspace()
    await openBrief()
    expect(await screen.findAllByText('Kota altında')).toHaveLength(2)
    expect(screen.getByText(/her hedef en az bir fikre sahip/)).toBeInTheDocument()
    expect(screen.queryByText('Eksik hedefleri tekrar dene')).not.toBeInTheDocument()
  })

  it('IDEAS_ALREADY_GENERATED ve eskime hatalarını anlaşılır gösterir ve durumu tazeler', async () => {
    mocks.generateIdeas.mockRejectedValueOnce(apiError(409, 'IDEAS_ALREADY_GENERATED'))
    renderWorkspace()
    await openBrief()
    const callsBefore = mocks.getBriefState.mock.calls.length
    fireEvent.click(await screen.findByText('Fikirleri Üret'))
    expect(await screen.findByText(/fikirler zaten üretilmiş/)).toBeInTheDocument()
    await waitFor(() => expect(mocks.getBriefState.mock.calls.length).toBeGreaterThan(callsBefore))

    mocks.generateIdeas.mockRejectedValueOnce(apiError(409, 'ASSIGNMENT_CHANGED'))
    fireEvent.click(screen.getByText('Fikirleri Üret'))
    expect(
      await screen.findByText(/Kanal ataması bu brief oluşturulduktan sonra/)
    ).toBeInTheDocument()
  })

  it('run değişince eski brief’in geç gelen fikir yanıtı yeni ekrana yazılmaz', async () => {
    const slow = deferred<{ data: ReturnType<typeof ideasResponse> }>()
    mocks.listBriefs.mockImplementation((runId: number) =>
      Promise.resolve({ data: runId === RUN_ID ? [LOCKED_BRIEF] : [] })
    )
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed') }),
    })
    mocks.getIdeasAttempt.mockReturnValue(slow.promise)
    const { rerender } = renderWorkspace()
    await openBrief()
    await waitFor(() => expect(mocks.getIdeasAttempt).toHaveBeenCalled())
    rerender(<SocialBriefWorkspace runId={20} pool={POOL} pollIntervalMs={10} />)
    expect(await screen.findByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
    await act(async () => slow.resolve({ data: ideasResponse(301, 'completed', [idea(1, 1)]) }))
    expect(screen.queryByText('Fikir 1')).not.toBeInTheDocument()
  })
})
