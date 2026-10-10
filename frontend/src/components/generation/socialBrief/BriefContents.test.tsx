import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  contentsResponse,
  historyItem,
  historyPage,
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

const IDEAS = [idea(1, 1), idea(2, 2), idea(3, 1)]
const ideasState = (extra = {}) =>
  stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed'), ...extra })

const VIDEO_CONTENT = historyItem(11, {
  idea_id: 2,
  idea_title: 'Fikir 2',
  content_format: 'reels',
  caption: 'Reels metni',
  format_payload: {
    kind: 'video',
    segments: [
      {
        start_sec: 0,
        end_sec: 40,
        scene: 'Açılış sahnesi',
        on_screen_text: 'Başlık',
        voiceover: 'Merhaba',
      },
      {
        start_sec: 40,
        end_sec: 75,
        scene: 'Kapanış sahnesi',
        on_screen_text: 'CTA',
        voiceover: 'Takip et',
      },
    ],
  },
  duration_status: 'mismatch',
  actual_duration_sec: 75,
  duration_min_sec: 31,
  duration_max_sec: 60,
  validation_warnings: ['voiceover_duration_mismatch'],
})

function renderWorkspace() {
  return render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
}

async function openBrief() {
  fireEvent.click(await screen.findByText("Brief'i Aç →"))
}

async function openContentsStep() {
  const next = await screen.findByRole('button', { name: /İçeriklere Geç/ })
  await waitFor(() => expect(next).toBeEnabled())
  fireEvent.click(next)
}

describe('İçerik seçimi, üretimi, yoklama ve format kartları', () => {
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
    mocks.getBriefState.mockResolvedValue({ data: ideasState() })
    mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, 'completed', IDEAS) })
    mocks.getContentHistory.mockResolvedValue({ data: historyPage([]) })
  })

  afterEach(() => cleanup())

  it('seçilen fikirler ve kırpılmış USP ile async üretimi başlatır, yoklar ve video kartını gösterir', async () => {
    mocks.generateContents.mockResolvedValue({
      status: 202,
      data: contentsResponse(501, 'pending', { requested_idea_ids: [1, 2] }),
    })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: ideasState() })
      .mockResolvedValueOnce({
        data: ideasState({
          content_attempts: [attempt(501, 'contents', 'running', { requested_idea_ids: [1, 2] })],
        }),
      })
      .mockResolvedValue({
        data: ideasState({
          content_attempts: [attempt(501, 'contents', 'completed', { requested_idea_ids: [1, 2] })],
          content_idea_ids: [1, 2],
        }),
      })
    mocks.getContentsAttempt
      .mockResolvedValueOnce({
        data: contentsResponse(501, 'running', { requested_idea_ids: [1, 2] }),
      })
      .mockResolvedValue({
        data: contentsResponse(501, 'completed', {
          requested_idea_ids: [1, 2],
          successful_idea_ids: [1, 2],
        }),
      })
    mocks.getContentHistory
      .mockResolvedValueOnce({ data: historyPage([]) })
      .mockResolvedValue({ data: historyPage([VIDEO_CONTENT]) })

    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByLabelText('Fikir 2'))
    fireEvent.click(screen.getByLabelText('Fikir 1'))
    await openContentsStep()
    fireEvent.change(screen.getByLabelText("Güvenilir marka USP'si (opsiyonel)"), {
      target: { value: '  2012’den beri hizmet  ' },
    })
    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))

    await waitFor(() =>
      expect(mocks.generateContents).toHaveBeenCalledWith(
        LOCKED_BRIEF.id,
        {
          idempotency_key: expect.stringMatching(/^contents-/),
          idea_ids: [1, 2],
          trusted_brand_usp: '2012’den beri hizmet',
        },
        WS_ID
      )
    )
    expect(mocks.getContentHistory).toHaveBeenCalledWith(WS_ID, {
      brief_id: LOCKED_BRIEF.id,
      limit: 50,
    })

    const card = await screen.findByRole('article', { name: 'Fikir 2' })
    expect(within(card).getByText(/Gerçek süre:/)).toHaveTextContent('Gerçek süre: 1 dk 15 sn')
    expect(within(card).getByText(/Seçilen aralık:/)).toBeInTheDocument()
    expect(within(card).getByText('Süre seçilen aralığın dışında')).toBeInTheDocument()
    expect(within(card).getByText('Açılış sahnesi')).toBeInTheDocument()
    // Kanca stili ham kod değil Türkçe etiket
    expect(within(card).getByText('Soru')).toBeInTheDocument()
    expect(within(card).queryByText('question')).not.toBeInTheDocument()
    expect(within(card).getByText('40–75 sn')).toBeInTheDocument()
    expect(within(card).getByText(/Seslendirme metninin uzunluğu/)).toBeInTheDocument()
    // İçeriği oluşan fikir tekrar seçilemez ve karta yönlendirilir
    fireEvent.click(screen.getByRole('button', { name: 'Geri' }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Fikir 2' })).toBeDisabled())
    expect(screen.getAllByText(/İçerik var · Gör/).length).toBe(2)
  })

  it('geri dönüp başka fikir seçildiğinde eski içeriği sonuç gibi göstermez; yeni fikrin içeriğini açar', async () => {
    const oldContent = historyItem(41, { idea_id: 1, idea_title: 'Fikir 1' })
    const newContent = historyItem(42, { idea_id: 2, idea_title: 'Fikir 2' })
    const oldState = ideasState({
      content_attempts: [attempt(501, 'contents', 'completed', { requested_idea_ids: [1] })],
      content_idea_ids: [1],
    })
    const newState = ideasState({
      content_attempts: [
        attempt(502, 'contents', 'completed', { requested_idea_ids: [2] }),
        attempt(501, 'contents', 'completed', { requested_idea_ids: [1] }),
      ],
      content_idea_ids: [1, 2],
    })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: oldState })
      .mockResolvedValue({ data: newState })
    mocks.getContentsAttempt.mockImplementation((_briefId: number, attemptId: number) =>
      Promise.resolve({
        data: contentsResponse(attemptId, 'completed', {
          requested_idea_ids: attemptId === 501 ? [1] : [2],
          successful_idea_ids: attemptId === 501 ? [1] : [2],
        }),
      })
    )
    mocks.getContentHistory
      .mockResolvedValueOnce({ data: historyPage([oldContent]) })
      .mockResolvedValue({ data: historyPage([newContent, oldContent]) })
    mocks.generateContents.mockResolvedValue({
      status: 201,
      data: contentsResponse(502, 'completed', {
        requested_idea_ids: [2],
        successful_idea_ids: [2],
      }),
    })

    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByRole('button', { name: 'Geri' }))
    fireEvent.click(await screen.findByLabelText('Fikir 2'))
    await openContentsStep()
    expect(screen.getByRole('article', { name: 'Fikir 1' }).closest('details')).not.toHaveAttribute(
      'open'
    )
    expect(screen.getByText('Seçilen fikirler için henüz içerik üretilmedi.')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))
    await waitFor(() => expect(mocks.generateContents).toHaveBeenCalledTimes(1))
    expect(mocks.generateContents.mock.calls[0][1].idea_ids).toEqual([2])
    expect(await screen.findByRole('article', { name: 'Fikir 2' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Fikir 1' }).closest('details')).not.toHaveAttribute(
      'open'
    )
    fireEvent.click(screen.getByText('Önceki içerikler (1)'))
    expect(screen.getByRole('article', { name: 'Fikir 1' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Geri' }))
    fireEvent.click(screen.getAllByRole('button', { name: /İçerik var · Gör/ })[0])
    expect(screen.getByRole('article', { name: 'Fikir 1' }).closest('details')).toBeNull()
    expect(screen.getByRole('article', { name: 'Fikir 2' }).closest('details')).not.toHaveAttribute(
      'open'
    )
  })

  it('kısmi sonuçta claim reddini gösterir; yalnız çözülemeyen ve içeriksiz fikir yeni anahtarla tekrar üretilir', async () => {
    mocks.getBriefState.mockResolvedValue({
      data: ideasState({
        content_attempts: [attempt(501, 'contents', 'partial', { requested_idea_ids: [1, 2] })],
        content_idea_ids: [1],
      }),
    })
    mocks.getContentsAttempt.mockResolvedValue({
      data: contentsResponse(501, 'partial', {
        requested_idea_ids: [1, 2],
        successful_idea_ids: [1],
        unresolved_idea_ids: [2],
        warnings: [
          {
            idea_id: 2,
            reason_code: 'content_rejected',
            claims: ['%100 garanti'],
            ai_calls_used: 2,
          },
        ],
        reason_code: 'content_partial',
      }),
    })
    mocks.generateContents.mockResolvedValue({
      status: 202,
      data: contentsResponse(502, 'pending', { requested_idea_ids: [2] }),
    })
    localStorage.setItem(
      `digitus.socialIdem.v1:${WS_ID}:${LOCKED_BRIEF.id}:contents`,
      JSON.stringify({ fp: 'ids=1,2|usp=-', key: 'contents-old', at: Date.now() })
    )

    renderWorkspace()
    await openBrief()
    expect(await screen.findByText(/doğrulanmamış iddia/)).toBeInTheDocument()
    expect(screen.getByText(/“%100 garanti”/)).toBeInTheDocument()
    expect(screen.getByText(/Üretilen/).parentElement).toHaveTextContent(
      'İstenen 2 · Üretilen 1 · Üretilemeyen 1'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Geri' }))
    fireEvent.click(await screen.findByLabelText('Fikir 3'))
    await openContentsStep()
    expect(screen.getByText('Önceki üretimin özeti')).toBeInTheDocument()
    expect(screen.getByText(/“%100 garanti”/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Üretilemeyen 1 fikir için yeniden dene'))
    await waitFor(() => expect(mocks.generateContents).toHaveBeenCalledTimes(1))
    const [, body] = mocks.generateContents.mock.calls[0]
    expect(body.idea_ids).toEqual([2])
    expect(body.idempotency_key).not.toBe('contents-old')
  })

  it('ağ hatasında aynı anahtar korunur; terminal 409 sonrası yeni anahtar üretilir', async () => {
    mocks.generateContents
      .mockRejectedValueOnce(NETWORK_ERROR)
      .mockRejectedValueOnce(
        apiError(409, 'CONTENT_ATTEMPT_TERMINAL', { retry_with_new_idempotency_key: true })
      )
      .mockRejectedValueOnce(apiError(409, 'ATTEMPT_CONFLICT'))
    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByLabelText('Fikir 1'))
    await openContentsStep()
    const btn = () => screen.getByText('Seçilen Fikirlerden İçerik Üret')
    fireEvent.click(btn())
    await screen.findByText(/Sunucuya ulaşılamadı/)
    fireEvent.click(btn())
    await screen.findByText(/Önceki içerik üretimi sonuçlandı/)
    fireEvent.click(btn())
    await screen.findByText(/başka bir üretim çalışıyor/)
    const keys = mocks.generateContents.mock.calls.map((c) => c[1].idempotency_key)
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[1])
  })

  it('200 tamamlanmış replay yanıtında içerikler hemen yüklenir', async () => {
    mocks.generateContents.mockResolvedValue({
      status: 200,
      data: contentsResponse(501, 'completed', {
        requested_idea_ids: [1],
        successful_idea_ids: [1],
        replayed: true,
      }),
    })
    mocks.getContentHistory.mockResolvedValueOnce({ data: historyPage([]) }).mockResolvedValue({
      data: historyPage([historyItem(12, { idea_id: 1, idea_title: 'Fikir 1' })]),
    })
    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByLabelText('Fikir 1'))
    await openContentsStep()
    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))
    expect(await screen.findByRole('article', { name: 'Fikir 1' })).toBeInTheDocument()
  })

  it('aktif içerik denemesi sürerken yeni üretim düğmesi kapalıdır', async () => {
    mocks.getBriefState.mockResolvedValue({
      data: ideasState({ content_attempts: [attempt(501, 'contents', 'running')] }),
    })
    mocks.getContentsAttempt.mockResolvedValue({ data: contentsResponse(501, 'running') })
    renderWorkspace()
    await openBrief()
    fireEvent.click(await screen.findByLabelText('Fikir 1'))
    expect(await screen.findByText(/İçerikler üretiliyor/)).toBeInTheDocument()
    expect(screen.getByText('Seçilen Fikirlerden İçerik Üret').closest('button')).toBeDisabled()
  })

  it('en fazla 30 fikir seçilebilir', async () => {
    const many = Array.from({ length: 31 }, (_, i) => idea(100 + i, 1))
    mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, 'completed', many) })
    renderWorkspace()
    await openBrief()
    await screen.findByLabelText('Fikir 100')
    for (let i = 0; i < 30; i++) fireEvent.click(screen.getByLabelText(`Fikir ${100 + i}`))
    expect(screen.getByLabelText('Fikir 130')).toBeDisabled()
    expect(screen.getByText(/30 fikir seçili/)).toBeInTheDocument()
    // 30 ardisik tiklama + 31 kartlik yeniden render: tek basina ~3.6 sn, paralel
    // paket yukunde 5 sn varsayilanini asabiliyor.
  }, 20_000)

  it('carousel, thread ve eski senaryo içerikleri formatına göre gösterilir; HTML enjekte edilmez', async () => {
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([
        historyItem(21, {
          idea_title: 'Carousel içerik',
          content_format: 'carousel',
          format_payload: {
            kind: 'carousel',
            slides: [
              {
                position: 1,
                headline: 'Slayt başlığı',
                body: 'Slayt metni',
                visual_direction: 'Mavi',
              },
            ],
          },
        }),
        historyItem(22, {
          idea_title: 'Thread içerik',
          platform: 'twitter',
          content_format: 'thread',
          format_payload: { kind: 'thread', posts: [{ position: 1, text: 'İlk gönderi' }] },
        }),
        historyItem(23, {
          idea_title: 'Eski içerik',
          caption: '<b>kalın değil</b>',
          format_payload: null,
          scenario: 'Eski senaryo metni',
        }),
      ]),
    })
    renderWorkspace()
    await openBrief()
    await openContentsStep()
    const carousel = await screen.findByRole('article', { name: 'Carousel içerik' })
    expect(within(carousel).getByText('Slaytlar (1)')).toBeInTheDocument()
    expect(within(carousel).getByText('Slayt başlığı')).toBeInTheDocument()
    const thread = screen.getByRole('article', { name: 'Thread içerik' })
    expect(within(thread).getByText('İlk gönderi')).toBeInTheDocument()
    expect(within(thread).getByText('11/280')).toBeInTheDocument()
    const legacy = screen.getByRole('article', { name: 'Eski içerik' })
    expect(within(legacy).getByText('Eski senaryo metni')).toBeInTheDocument()
    expect(within(legacy).getByText('<b>kalın değil</b>')).toBeInTheDocument()
    expect(legacy.querySelector('b')?.textContent).not.toBe('kalın değil')
  })

  it('metin kopyalama gerçekten panoya yazar', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([historyItem(31, { idea_title: 'Kopya', caption: 'Paylaşım metni' })]),
    })
    renderWorkspace()
    await openBrief()
    await openContentsStep()
    const card = await screen.findByRole('article', { name: 'Kopya' })
    fireEvent.click(within(card).getByText('Metni kopyala'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Paylaşım metni\n\n#pazarlama'))
    expect(await within(card).findByText('Kopyalandı')).toBeInTheDocument()
  })
})
