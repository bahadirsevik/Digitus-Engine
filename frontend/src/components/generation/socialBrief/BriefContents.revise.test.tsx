import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import SocialBriefWorkspace from '../SocialBriefWorkspace'
import { useBrandStore } from '../../../stores/brandStore'
import type { SocialContentRevisionItem } from '../../../services/api'
import {
  FORMAT_MATRIX,
  LOCKED_BRIEF,
  NETWORK_ERROR,
  POOL,
  RUN_ID,
  WS_ID,
  apiError,
  attempt,
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
  reviseContent: vi.fn(),
  listRevisions: vi.fn(),
  acceptRevision: vi.fn(),
  discardRevision: vi.fn(),
}))

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>()
  return { ...actual, socialBriefApi: mocks }
})

const POST = historyItem(31, { idea_id: 1, idea_title: 'Kanca Fikri', caption: 'Eski metin' })
const OTHER = historyItem(32, { idea_id: 2, idea_title: 'Diğer Fikir', caption: 'Diğer metin' })

const rev = (
  status: SocialContentRevisionItem['status'],
  extra: Partial<SocialContentRevisionItem> = {}
): SocialContentRevisionItem => ({
  id: 900,
  content_id: POST.id,
  status,
  feedback: 'Metni samimi yap',
  scope: ['text'],
  created_at: null,
  proposed: null,
  changes: [],
  error_message: null,
  ...extra,
})

const READY = rev('ready', {
  changes: ['caption'],
  proposed: {
    hooks: POST.hooks,
    caption: 'Yeni samimi metin',
    cta_text: POST.cta_text,
    hashtags: POST.hashtags,
    format_payload: null,
    scenario: null,
    visual_suggestion: null,
    video_concept: null,
    industry_posting_suggestion: null,
    platform_notes: null,
    duration_status: 'not_applicable',
    actual_duration_sec: null,
    validation_warnings: [],
  },
})

const list = (...items: SocialContentRevisionItem[]) => ({ data: { brief_id: 1, items } })

// Kayıtlı içerik varken brief doğrudan içerik adımında açılır
async function openSavedContents() {
  render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
  fireEvent.click(await screen.findByText("Brief'i Aç →"))
  return screen.findByRole('article', { name: 'Kanca Fikri' })
}

async function submitRevise(card: HTMLElement, feedback: string) {
  fireEvent.click(within(card).getByRole('button', { name: /Revize Et/ }))
  const dialog = screen.getByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('Neyi değiştirelim?'), {
    target: { value: feedback },
  })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
  return dialog
}

describe('Sosyal içerik "Revize Et"', () => {
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
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({
        ideas_attempt: attempt(301, 'ideas', 'completed'),
        content_idea_ids: [1, 2],
      }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({
      data: ideasResponse(301, 'completed', [idea(1, 1), idea(2, 1)]),
    })
    mocks.getContentHistory.mockResolvedValue({ data: historyPage([POST, OTHER]) })
    mocks.listRevisions.mockResolvedValue(list())
  })

  afterEach(() => cleanup())

  it('talebi gönderir, yoklar; öneri önizlenir ve kabulde içerik yeniden yüklenir', async () => {
    // Yoklama fazı testten yönetilir: önce süren, sonra hazır öneri
    let phase: 'none' | 'generating' | 'ready' = 'none'
    mocks.listRevisions.mockImplementation(async () =>
      phase === 'none' ? list() : list(phase === 'generating' ? rev('generating') : READY)
    )
    mocks.reviseContent.mockImplementation(async () => {
      phase = 'generating'
      return list(rev('generating'))
    })
    const card = await openSavedContents()

    fireEvent.click(within(card).getByRole('button', { name: /Revize Et/ }))
    const dialog = screen.getByRole('dialog')
    // Gönderi formatında gövde seçeneği yok; varsayılan "Metin"
    expect(within(dialog).queryByLabelText(/Senaryo/)).toBeNull()
    expect(
      within(dialog).getByLabelText("Metin, harekete geçirici çağrı ve hashtag'ler")
    ).toBeChecked()
    fireEvent.change(within(dialog).getByLabelText('Neyi değiştirelim?'), {
      target: { value: 'Metni samimi yap' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const [briefId, contentId, body, ws] = mocks.reviseContent.mock.calls[0]
    expect([briefId, contentId, ws]).toEqual([LOCKED_BRIEF.id, POST.id, WS_ID])
    expect(body).toMatchObject({ feedback: 'Metni samimi yap', scope: ['text'] })
    expect(body.idempotency_key).toBeTruthy()
    expect(await screen.findByText(/Talebinize göre revize ediliyor/)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: /Revize ediliyor/ })).toBeDisabled()
    phase = 'ready'

    const preview = await screen.findByRole('region', { name: 'Revizyon önerisi' })
    expect(within(preview).getByText('Yeni samimi metin')).toBeInTheDocument()
    expect(within(preview).getByText(/Değişen bölümler: Metin/)).toBeInTheDocument()
    // Canlı kart kabul edilene kadar değişmez
    expect(within(card).getByText('Eski metin')).toBeInTheDocument()

    mocks.acceptRevision.mockResolvedValue({ data: { status: 'accepted' } })
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([{ ...POST, caption: 'Yeni samimi metin' }, OTHER]),
    })
    phase = 'none'
    fireEvent.click(within(preview).getByRole('button', { name: 'Kabul Et' }))
    await waitFor(() => expect(mocks.acceptRevision).toHaveBeenCalledWith(900, WS_ID))
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Revizyon önerisi' })).toBeNull()
    )
    const updated = await screen.findByRole('article', { name: 'Kanca Fikri' })
    expect(within(updated).getByText('Yeni samimi metin')).toBeInTheDocument()
  })

  it('brief’te revizyon sürerken diğer içeriğin butonu nedeniyle kapalıdır', async () => {
    mocks.listRevisions.mockResolvedValue(list(rev('generating')))
    const card = await openSavedContents()
    expect(await within(card).findByRole('button', { name: /Revize ediliyor/ })).toBeDisabled()
    const other = screen.getByRole('article', { name: 'Diğer Fikir' })
    const button = within(other).getByRole('button', { name: /Revize Et/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription(/süren bir revizyon var/)
  })

  it('başarısız revizyonda nedeni gösterir; Kapat bildirimi kapatır', async () => {
    mocks.listRevisions
      .mockResolvedValueOnce(
        list(rev('failed', { error_message: 'Yapay zekâ yanıtı işlenemedi.' }))
      )
      .mockResolvedValue(list())
    mocks.discardRevision.mockResolvedValue({ data: { status: 'discarded' } })
    await openSavedContents()
    const alert = await screen.findByText(/Revizyon başarısız: Yapay zekâ yanıtı işlenemedi/)
    fireEvent.click(within(alert.closest('[role="alert"]') as HTMLElement).getByText('Kapat'))
    await waitFor(() => expect(mocks.discardRevision).toHaveBeenCalledWith(900, WS_ID))
    await waitFor(() => expect(screen.queryByText(/Revizyon başarısız/)).toBeNull())
  })

  it('ağ hatasında aynı anahtar korunur, talep değişince yeni anahtar üretilir; hata pencerede kalır', async () => {
    const card = await openSavedContents()
    mocks.reviseContent.mockRejectedValue(NETWORK_ERROR)
    const dialog = await submitRevise(card, 'Metni kısalt')
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    await waitFor(() => expect(mocks.reviseContent).toHaveBeenCalledTimes(2))
    fireEvent.change(within(dialog).getByLabelText('Neyi değiştirelim?'), {
      target: { value: 'Metni kısalt ve soru ekle' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    await waitFor(() => expect(mocks.reviseContent).toHaveBeenCalledTimes(3))
    const keys = mocks.reviseContent.mock.calls.map((c) => c[2].idempotency_key)
    expect(keys[0]).toBe(keys[1])
    expect(keys[2]).not.toBe(keys[0])
    expect(within(dialog).getByLabelText('Neyi değiştirelim?')).toHaveValue(
      'Metni kısalt ve soru ekle'
    )
  })

  it('sunucu 503 dönerse aynı talebin tekrarı yeni anahtarla gider (bitmiş deneme yeniden oynatılmaz)', async () => {
    const card = await openSavedContents()
    mocks.reviseContent.mockRejectedValue({
      ...apiError(503, 'REVISION_DISPATCH_FAILED'),
      message: 'Revizyon başlatılamadı. Yeniden deneyin.',
    })
    const dialog = await submitRevise(card, 'Metni kısalt')
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    await waitFor(() => expect(mocks.reviseContent).toHaveBeenCalledTimes(2))
    const keys = mocks.reviseContent.mock.calls.map((c) => c[2].idempotency_key)
    expect(keys[1]).not.toBe(keys[0])
  })

  it('kabul reddedilirse (içerik değişti) hata kartta görünür, içerik değişmez', async () => {
    mocks.listRevisions.mockResolvedValueOnce(list(READY)).mockResolvedValue(list())
    // Gerçek istemci sunucunun Türkçe mesajını üst düzey `message`'a taşır
    mocks.acceptRevision.mockRejectedValue({
      ...apiError(409, 'REVISION_SOURCE_CHANGED'),
      message: 'İçerik bu öneriden sonra değişti; öneri geçersiz.',
    })
    const card = await openSavedContents()
    const preview = await screen.findByRole('region', { name: 'Revizyon önerisi' })
    fireEvent.click(within(preview).getByRole('button', { name: 'Kabul Et' }))
    // Geçersiz öneri listeden düşer; hata kartın altında kalır
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Revizyon önerisi' })).toBeNull()
    )
    expect(screen.getByRole('alert')).toHaveTextContent(/İçerik bu öneriden sonra değişti/)
    expect(within(card).getByText('Eski metin')).toBeInTheDocument()
  })

  it('video içerikte senaryo kapsamı seçilebilir', async () => {
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([
        {
          ...POST,
          content_format: 'reels',
          format_payload: {
            kind: 'video',
            segments: [
              {
                start_sec: 0,
                end_sec: 40,
                scene: 'Sahne',
                on_screen_text: 'Yazı',
                voiceover: 'Ses',
              },
            ],
          },
        },
      ]),
    })
    const card = await openSavedContents()
    fireEvent.click(within(card).getByRole('button', { name: /Revize Et/ }))
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByLabelText('Senaryo (sahneler, ekran yazısı, seslendirme)')
    ).toBeInTheDocument()
  })
})
