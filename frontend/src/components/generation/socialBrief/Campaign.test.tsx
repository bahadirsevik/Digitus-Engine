import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import SocialBriefWorkspace from '../SocialBriefWorkspace'
import { useBrandStore } from '../../../stores/brandStore'
import type { SocialCampaignSpec } from '../../../services/api'
import {
  BRIEF,
  FORMAT_MATRIX,
  LOCKED_BRIEF,
  POOL,
  RUN_ID,
  WS_ID,
  attempt,
  contentsResponse,
  historyItem,
  historyPage,
  idea,
  ideasResponse,
  stateWithCategories,
} from './testFixtures'
import { offerViolationText } from './labels'

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

const SPEC: SocialCampaignSpec = {
  mode: 'defined',
  usage: 'paid',
  objective: 'sales',
  destination: 'whatsapp',
  promoted_item: 'Başlangıç paketi',
  audience: null,
  focus_note: null,
  disallowed_offer_types: [],
  offer_draft: {
    type: 'percent_discount',
    value: '%20',
    applies_to: 'Başlangıç paketi',
    conditions: null,
    ends: null,
  },
}
const CAMPAIGN_BRIEF = { ...LOCKED_BRIEF, campaign_spec: SPEC }
const payload = (offer_type: 'percent_discount' | 'bundle') => ({
  campaign_payload: {
    offer_type,
    cta_type: 'message' as const,
    offer_rationale: 'Yeni müşteri denemesi için değerlendirilebilir.',
  },
})
const IDEAS = [
  idea(1, 1, payload('percent_discount')),
  idea(2, 2, payload('percent_discount')),
  idea(3, 1, payload('bundle')),
]

function setWorkspace() {
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
}

async function openContentsStep() {
  const next = await screen.findByRole('button', { name: /İçeriklere Geç/ })
  await waitFor(() => expect(next).toBeEnabled())
  fireEvent.click(next)
}

describe('Kampanya modu arayüzü', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace()
    mocks.getFormatMatrix.mockResolvedValue({ data: { ...FORMAT_MATRIX, campaign_enabled: true } })
    mocks.getContentHistory.mockResolvedValue({ data: historyPage([]) })
  })

  afterEach(() => cleanup())

  it('önce tür seçilir; Reklam Kampanyası seçilince istek kampanya taşır', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [] })
    mocks.createBrief.mockResolvedValue({ data: BRIEF })
    mocks.getBriefState.mockResolvedValue({ data: stateWithCategories() })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('Ne hazırlamak istiyorsunuz?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Reklam Kampanyası/ }))
    expect(await screen.findByText('Yeni Reklam Kampanyası')).toBeInTheDocument()
    fireEvent.click(screen.getByText(POOL[0].keyword))
    fireEvent.click(screen.getByText('Hedef Ekle'))

    fireEvent.change(screen.getByLabelText('Yönlendirme'), { target: { value: 'whatsapp' } })
    fireEvent.click(screen.getByLabelText(/Yüzde indirim/))
    // Tanıtılan ürün zorunlu: istek gitmez
    fireEvent.click(screen.getByText("Brief'i Kaydet ve Aç"))
    expect(await screen.findByText(/tanıtılacak ürün veya hizmeti yazın/)).toBeInTheDocument()
    expect(mocks.createBrief).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Tanıtılacak ürün / hizmet'), {
      target: { value: ' Başlangıç paketi ' },
    })
    fireEvent.change(screen.getByLabelText('Bu pakete dahil özellikler (opsiyonel)'), {
      target: { value: ' Sınırsız portföy takibi ' },
    })
    fireEvent.click(screen.getByText("Brief'i Kaydet ve Aç"))
    await waitFor(() =>
      expect(mocks.createBrief).toHaveBeenCalledWith(
        expect.objectContaining({
          campaign: {
            mode: 'suggest',
            usage: 'paid',
            objective: 'sales',
            destination: 'whatsapp',
            promoted_item: 'Başlangıç paketi',
            audience: null,
            focus_note: null,
            included_features: 'Sınırsız portföy takibi',
            disallowed_offer_types: ['percent_discount'],
            offer_draft: null,
          },
        }),
        WS_ID
      )
    )
  })

  it('Sosyal Medya İçeriği seçilince kampanya alanı yok, istek kampanya taşımaz', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [] })
    mocks.createBrief.mockResolvedValue({ data: BRIEF })
    mocks.getBriefState.mockResolvedValue({ data: stateWithCategories() })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    fireEvent.click(await screen.findByRole('button', { name: /Sosyal Medya İçeriği/ }))
    expect(await screen.findByText('Yeni Sosyal Medya İçeriği')).toBeInTheDocument()
    expect(screen.queryByText('3. Kampanya Bilgileri')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText(POOL[0].keyword))
    fireEvent.click(screen.getByText('Hedef Ekle'))
    fireEvent.click(screen.getByText("Brief'i Kaydet ve Aç"))
    await waitFor(() => expect(mocks.createBrief).toHaveBeenCalled())
    expect(mocks.createBrief.mock.calls[0][0]).not.toHaveProperty('campaign')
  })

  it('tür değiştirip geri dönünce girilen kampanya bilgileri korunur', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [] })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    fireEvent.click(await screen.findByRole('button', { name: /Reklam Kampanyası/ }))
    fireEvent.change(screen.getByLabelText('Tanıtılacak ürün / hizmet'), {
      target: { value: 'Başlangıç paketi' },
    })
    fireEvent.click(screen.getByLabelText(/Teklifim hazır/))
    fireEvent.change(screen.getByLabelText('Teklif detayı'), { target: { value: '%20' } })

    fireEvent.click(screen.getByText('Türü değiştir'))
    fireEvent.click(screen.getByRole('button', { name: /Sosyal Medya İçeriği/ }))
    expect(screen.queryByText('3. Kampanya Bilgileri')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Türü değiştir'))
    fireEvent.click(screen.getByRole('button', { name: /Reklam Kampanyası/ }))

    expect(screen.getByLabelText('Tanıtılacak ürün / hizmet')).toHaveValue('Başlangıç paketi')
    expect(screen.getByLabelText('Teklif detayı')).toHaveValue('%20')
  })

  it('kampanya kapalıyken tür seçimi atlanır, doğrudan form açılır', async () => {
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [] })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('Yeni Sosyal Medya İçeriği')).toBeInTheDocument()
    expect(screen.queryByText('Ne hazırlamak istiyorsunuz?')).not.toBeInTheDocument()
    expect(screen.queryByText('3. Kampanya Bilgileri')).not.toBeInTheDocument()
  })

  it('fikir kartı teklif türünü ve CTA’yı gösterir; içerik isteği taslaktan doldurulan teklifi taşır', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [CAMPAIGN_BRIEF] })
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, 'completed', IDEAS) })
    mocks.generateContents.mockResolvedValue({
      status: 202,
      data: contentsResponse(501, 'pending', { requested_idea_ids: [1, 2] }),
    })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
    fireEvent.click(await screen.findByText("Brief'i Aç →"))

    expect(await screen.findByText(/Kampanya belli/)).toBeInTheDocument()
    const card = (await screen.findByLabelText('Fikir 1')).closest('.sb-idea-card') as HTMLElement
    expect(within(card).getByText('Yüzde indirim')).toBeInTheDocument()
    expect(within(card).getByText(/CTA: Mesaj yaz/)).toBeInTheDocument()
    expect(within(card).getByText(/Neden bu teklif/)).toBeInTheDocument()

    // Farklı türden fikirler birlikte seçilince istek gitmez
    fireEvent.click(screen.getByLabelText('Fikir 1'))
    fireEvent.click(screen.getByLabelText('Fikir 3'))
    await openContentsStep()
    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))
    expect(await screen.findByText(/teklif türleri farklı/)).toBeInTheDocument()
    expect(mocks.generateContents).not.toHaveBeenCalled()
  })

  it('teklifsiz devam ve aynı türden fikirler: istek offer_terms taşır', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [CAMPAIGN_BRIEF] })
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({ ideas_attempt: attempt(301, 'ideas', 'completed') }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, 'completed', IDEAS) })
    mocks.generateContents.mockResolvedValue({
      status: 202,
      data: contentsResponse(501, 'pending', { requested_idea_ids: [1, 2] }),
    })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
    fireEvent.click(await screen.findByText("Brief'i Aç →"))
    fireEvent.click(await screen.findByLabelText('Fikir 1'))
    fireEvent.click(screen.getByLabelText('Fikir 2'))
    await openContentsStep()

    expect(screen.getByLabelText('Teklif detayı')).toHaveValue('%20')
    fireEvent.change(screen.getByLabelText('Şartlar (opsiyonel)'), {
      target: { value: '500 TL üzeri' },
    })
    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))
    await waitFor(() =>
      expect(mocks.generateContents).toHaveBeenCalledWith(
        CAMPAIGN_BRIEF.id,
        expect.objectContaining({
          idea_ids: [1, 2],
          offer_terms: {
            type: 'percent_discount',
            value: '%20',
            applies_to: 'Başlangıç paketi',
            conditions: '500 TL üzeri',
            ends: null,
          },
        }),
        WS_ID
      )
    )
  })

  it('içerik kartı görsel prompt’unu ve teklif özetini gösterir', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [CAMPAIGN_BRIEF] })
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({
        ideas_attempt: attempt(301, 'ideas', 'completed'),
        content_attempts: [attempt(501, 'contents', 'completed', { requested_idea_ids: [1] })],
        content_idea_ids: [1],
      }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, 'completed', IDEAS) })
    mocks.getContentsAttempt.mockResolvedValue({
      data: contentsResponse(501, 'completed', {
        requested_idea_ids: [1],
        successful_idea_ids: [1],
      }),
    })
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([
        historyItem(11, {
          idea_id: 1,
          idea_title: 'Fikir 1',
          image_prompt: 'Two products on marble, empty space left. Aspect ratio 4:5.',
          overlay_text: {
            headline: 'Bakımınızı sadeleştirin',
            benefit: 'Üç adımlık rutin.',
            offer_line: 'Başlangıç paketi için %20',
            terms_line: null,
            cta: 'Mesaj yazın',
          },
          campaign_payload: {
            offer_terms: { type: 'percent_discount', value: '%20', applies_to: 'Başlangıç paketi' },
            cta_type: 'message',
            usage: 'paid',
          },
        }),
      ]),
    })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
    fireEvent.click(await screen.findByText("Brief'i Aç →"))
    const card = await screen.findByRole('article', { name: 'Fikir 1' })
    expect(within(card).getByText(/Two products on marble/)).toBeInTheDocument()
    expect(within(card).getByText("Prompt'u kopyala")).toBeInTheDocument()
    expect(within(card).getByText('Bakımınızı sadeleştirin')).toBeInTheDocument()
    expect(within(card).getByText('Başlangıç paketi için %20')).toBeInTheDocument()
    expect(within(card).getByText('Metinleri kopyala')).toBeInTheDocument()
    expect(
      within(card).getByText('Yüzde indirim — %20 · kapsam: Başlangıç paketi')
    ).toBeInTheDocument()
  })
})

describe('içerik üretildikten sonra teklif', () => {
  const LOCKED = {
    type: 'percent_discount' as const,
    value: '%25',
    applies_to: 'Pro yıllık plan',
    conditions: 'ilk aboneliğe özel',
    ends: null,
  }

  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace()
    mocks.getFormatMatrix.mockResolvedValue({ data: { ...FORMAT_MATRIX, campaign_enabled: true } })
    mocks.listBriefs.mockResolvedValue({ data: [CAMPAIGN_BRIEF] })
    mocks.getBriefState.mockResolvedValue({
      data: stateWithCategories({
        ideas_attempt: attempt(301, 'ideas', 'completed'),
        content_attempts: [attempt(501, 'contents', 'completed', { requested_idea_ids: [1] })],
        content_idea_ids: [1],
        locked_offer_terms: LOCKED,
      }),
    })
    mocks.getIdeasAttempt.mockResolvedValue({ data: ideasResponse(301, 'completed', IDEAS) })
    mocks.getContentsAttempt.mockResolvedValue({
      data: contentsResponse(501, 'completed', {
        requested_idea_ids: [1],
        successful_idea_ids: [1],
      }),
    })
    mocks.getContentHistory.mockResolvedValue({
      data: historyPage([
        historyItem(11, {
          idea_id: 1,
          idea_title: 'Fikir 1',
          // The paginated history is not the authority for the campaign lock.
          campaign_payload: {
            offer_terms: { ...LOCKED, value: '%10' },
            cta_type: 'message',
            usage: 'paid',
          },
        }),
      ]),
    })
    mocks.generateContents.mockResolvedValue({
      status: 202,
      data: contentsResponse(502, 'pending', { requested_idea_ids: [2] }),
    })
  })

  afterEach(() => cleanup())

  it('teklif formu ve üret paneli gizlenir; yeni fikir seçilince kilitli teklifle üretilir', async () => {
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
    fireEvent.click(await screen.findByText("Brief'i Aç →"))
    await screen.findByRole('article', { name: 'Fikir 1' })
    expect(screen.queryByText('Seçilen Fikirlerden İçerik Üret')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Teklif detayı')).not.toBeInTheDocument()
    expect(screen.getByText(/Başka fikirler için içerik üretmek isterseniz/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Geri/ }))
    fireEvent.click(screen.getByLabelText('Fikir 2'))
    await openContentsStep()
    expect(screen.getByText(/içerik üretildiği için sabitlendi/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Teklif detayı')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))
    await waitFor(() =>
      expect(mocks.generateContents).toHaveBeenCalledWith(
        CAMPAIGN_BRIEF.id,
        expect.objectContaining({ idea_ids: [2], offer_terms: LOCKED }),
        WS_ID
      )
    )
  })

  it('kilitli teklifle farklı türden fikir seçilirse istek gitmez', async () => {
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
    fireEvent.click(await screen.findByText("Brief'i Aç →"))
    await screen.findByRole('article', { name: 'Fikir 1' })
    fireEvent.click(screen.getByRole('button', { name: /Geri/ }))
    fireEvent.click(screen.getByLabelText('Fikir 3'))
    await openContentsStep()
    fireEvent.click(screen.getByText('Seçilen Fikirlerden İçerik Üret'))
    expect(await screen.findByText(/yalnız bu türdeki fikirlerden/)).toBeInTheDocument()
    expect(mocks.generateContents).not.toHaveBeenCalled()
  })
})

describe('teklif ihlali metni', () => {
  it('kodu okunur metne çevirir, bilinmeyeni olduğu gibi bırakır', () => {
    expect(offerViolationText('offer_not_in_terms:%30')).toBe('Koşullarda olmayan teklif: “%30”')
    expect(offerViolationText('offer_end_date_wrong_year:30 Kasım 2026')).toBe(
      'Bitiş tarihi yanlış yıl: “30 Kasım 2026”'
    )
    expect(offerViolationText('bilinmeyen:x')).toBe('bilinmeyen:x')
  })
})
