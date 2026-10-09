import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import SocialBriefWorkspace from './SocialBriefWorkspace'
import SocialPanel from './SocialPanel'
import { useBrandStore } from '../../stores/brandStore'
import {
  BRIEF,
  CATEGORIES,
  FORMAT_MATRIX,
  LOCKED_BRIEF,
  NETWORK_ERROR,
  POOL,
  RUN_ID,
  WS_ID,
  apiError,
  attempt,
  briefState,
  deferred,
  stateWithCategories,
} from './socialBrief/testFixtures'
import type { SocialCategoriesGenerateResponse } from '../../services/api'

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
  getPools: vi.fn(),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  const { getPools, ...social } = mocks
  return {
    ...actual,
    socialBriefApi: social,
    channelsApi: { ...actual.channelsApi, getPools },
  }
})

const CATEGORIES_RESP: SocialCategoriesGenerateResponse = {
  brief_id: BRIEF.id,
  scoring_run_id: RUN_ID,
  attempt_id: 101,
  attempt_status: 'completed',
  total_categories: 2,
  categories: CATEGORIES,
  ai_calls_used: 1,
  replayed: false,
}

function setWorkspace(id: number, name = 'Dijital Ajans') {
  useBrandStore.setState({
    activeWorkspace: {
      id,
      name,
      company_url: 'https://example.com',
      status: 'confirmed',
      profile_data: null,
      suggested_keywords: null,
    },
  })
}

async function openBrief(briefLabel = "Brief'i Aç →") {
  fireEvent.click(await screen.findByText(briefLabel))
}

describe('SocialBriefWorkspace — brief oluşturma ve kategori (H.1 + düzeltmeler)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace(WS_ID)
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [] })
    mocks.createBrief.mockResolvedValue({ data: BRIEF })
    mocks.generateCategories.mockResolvedValue({ status: 201, data: CATEGORIES_RESP })
    mocks.getBriefState.mockResolvedValue({ data: briefState() })
    mocks.getContentHistory.mockResolvedValue({
      data: { brand_profile_id: WS_ID, total: 0, limit: 50, offset: 0, has_more: false, items: [] },
    })
    mocks.getPools.mockResolvedValue({ data: { channels: { SOCIAL: POOL } } })
  })

  afterEach(() => cleanup())

  it('boş durumda ilk brief oluşturma butonunu gösterir', async () => {
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    expect(await screen.findByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
    expect(screen.getByText("İlk Brief'i Oluştur")).toBeInTheDocument()
  })

  it('kayıtlı briefleri listeler ve açma butonunu sunar', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    expect(await screen.findByText('Brief #42')).toBeInTheDocument()
    expect(screen.getByText("Brief'i Aç →")).toBeInTheDocument()
    expect(screen.getByText('Test Brand')).toBeInTheDocument()
  })

  it('format matrisi yüklenir; süre seçimi yalnız video formatında görünür', async () => {
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('Yeni Sosyal Brief Oluştur')).toBeInTheDocument()
    expect(screen.queryByLabelText('Süre Ön Ayarı')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Format'), { target: { value: 'reels' } })
    expect(await screen.findByLabelText('Süre Ön Ayarı')).toBeInTheDocument()
  })

  it('kelimede 1–5 sınırını korur ve keyword_id gönderir', async () => {
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    for (const kw of POOL.slice(0, 5)) fireEvent.click(screen.getByText(kw.keyword))
    fireEvent.click(screen.getByText('e-ticaret seo'))
    expect(screen.getByText(/En fazla 5 anahtar kelime seçebilirsiniz/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Hedef Ekle'))
    fireEvent.click(screen.getByText("Brief'i Kaydet ve Aç"))
    await waitFor(() =>
      expect(mocks.createBrief).toHaveBeenCalledWith(
        {
          scoring_run_id: RUN_ID,
          keyword_ids: [1, 2, 3, 4, 5],
          targets: [{ platform: 'instagram', content_format: 'post', duration_preset_id: null }],
          brand_name: 'Dijital Ajans',
          brand_context: undefined,
        },
        WS_ID
      )
    )
  })

  it('hedef tekilliği (platform, format): farklı süreyle aynı çift ikinci kez eklenemez', async () => {
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    fireEvent.click(screen.getByText('dijital pazarlama ajansı'))
    fireEvent.change(screen.getByLabelText('Format'), { target: { value: 'reels' } })
    fireEvent.change(await screen.findByLabelText('Süre Ön Ayarı'), {
      target: { value: 'short_1_15' },
    })
    fireEvent.click(screen.getByText('Hedef Ekle'))

    // Aynı instagram/reels çifti, farklı süreyle
    fireEvent.change(screen.getByLabelText('Süre Ön Ayarı'), { target: { value: 'short_31_60' } })
    const addBtn = screen.getByText('Hedef Ekle').closest('button')!
    expect(addBtn).toBeDisabled()
    expect(screen.getByRole('note')).toHaveTextContent(/zaten ekli/)

    fireEvent.click(screen.getByText("Brief'i Kaydet ve Aç"))
    await waitFor(() => expect(mocks.createBrief).toHaveBeenCalled())
    expect(mocks.createBrief.mock.calls[0][0].targets).toEqual([
      { platform: 'instagram', content_format: 'reels', duration_preset_id: 'short_1_15' },
    ])
  })

  it('kategori sonucu tarayıcı önbelleği olmadan DB durumundan geri gelir (başka oturum)', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [LOCKED_BRIEF] })
    mocks.getBriefState.mockResolvedValue({ data: stateWithCategories() })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()

    expect(await screen.findByText('Pazarlama Taktikleri')).toBeVisible()
    expect(screen.getByText('SEO Rehberleri')).toBeVisible()
    // Kategori tipi ham kod değil Türkçe etiket olarak görünür
    expect(screen.getByText('Eğitici')).toBeVisible()
    expect(screen.getByText('Ürün faydası')).toBeVisible()
    expect(screen.queryByText('educational')).not.toBeInTheDocument()
    expect(mocks.getBriefState).toHaveBeenCalledWith(BRIEF.id, WS_ID)
    expect(mocks.generateCategories).not.toHaveBeenCalled()
    expect(screen.queryByText('Kategorileri Üret')).not.toBeInTheDocument()
    // Kategori sonucu tarayıcı depolamasına yazılmaz: gerçek kaynak DB
    expect(JSON.stringify(sessionStorage)).not.toContain('Pazarlama Taktikleri')
    expect(JSON.stringify(localStorage)).not.toContain('Pazarlama Taktikleri')
  })

  it('kategori üretimi başarılı olunca sonuçları gösterir ve durumu tazeler', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: briefState() })
      .mockResolvedValue({ data: stateWithCategories() })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()
    fireEvent.click(await screen.findByText('Kategorileri Üret'))

    await waitFor(() =>
      expect(mocks.generateCategories).toHaveBeenCalledWith(
        BRIEF.id,
        { idempotency_key: expect.stringMatching(/^categories-/), max_categories: 4 },
        WS_ID
      )
    )
    expect(await screen.findByText('Pazarlama Taktikleri')).toBeVisible()
    expect(screen.getByText('%94')).toBeVisible()
    expect(screen.getByRole('button', { name: /Fikirlere Geç/ })).toBeEnabled()
  })

  it('ağ yanıtı kaybolursa aynı işlem aynı anahtarla, kesin ret sonrası yeni anahtarla tekrarlanır', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    mocks.generateCategories
      .mockRejectedValueOnce(NETWORK_ERROR)
      .mockRejectedValueOnce(
        apiError(409, 'CATEGORY_ATTEMPT_TERMINAL', { retry_with_new_idempotency_key: true })
      )
      .mockResolvedValueOnce({ status: 201, data: CATEGORIES_RESP })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()

    fireEvent.click(await screen.findByText('Kategorileri Üret'))
    expect(await screen.findByText(/Sunucuya ulaşılamadı/)).toBeInTheDocument()
    expect(screen.getByText(/aynı işlemi güvenle tekrar dener/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Kategorileri Üret'))
    expect(await screen.findByText(/Önceki kategori üretimi başarısız oldu/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Kategorileri Üret'))
    await waitFor(() => expect(mocks.generateCategories).toHaveBeenCalledTimes(3))

    const keys = mocks.generateCategories.mock.calls.map((c) => c[1].idempotency_key)
    expect(keys[1]).toBe(keys[0]) // aynı niyetin tekrarı
    expect(keys[2]).not.toBe(keys[1]) // terminal ret → yeni niyet
  })

  it('sağlayıcı hatası (502 + yeni anahtar bayrağı) sonrası tekrar yeni anahtarla gider', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: briefState() })
      .mockResolvedValue({ data: stateWithCategories() })
    mocks.generateCategories
      .mockRejectedValueOnce(
        apiError(502, 'CATEGORY_PROVIDER_ERROR', {
          retryable: true,
          retry_with_new_idempotency_key: true,
        })
      )
      .mockResolvedValueOnce({ status: 201, data: CATEGORIES_RESP })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()

    fireEvent.click(await screen.findByText('Kategorileri Üret'))
    expect(await screen.findByText(/Yapay zekâ servisi hata verdi/)).toBeInTheDocument()
    // Anahtar bırakıldı: "aynı işlemi tekrar dener" ipucu gösterilmez
    expect(screen.queryByText(/aynı işlemi güvenle tekrar dener/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Kategorileri Üret'))
    await waitFor(() => expect(mocks.generateCategories).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Pazarlama Taktikleri')).toBeInTheDocument()

    const [first, second] = mocks.generateCategories.mock.calls.map((c) => c[1].idempotency_key)
    expect(first).toMatch(/^categories-/)
    expect(second).toMatch(/^categories-/)
    expect(second).not.toBe(first)
  })

  it('parametre değişirse bekleyen anahtar yeniden kullanılmaz', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    mocks.generateCategories.mockRejectedValue(NETWORK_ERROR)
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()
    fireEvent.click(await screen.findByText('Kategorileri Üret'))
    await screen.findByText(/Sunucuya ulaşılamadı/)
    fireEvent.change(screen.getByLabelText('En fazla kategori'), { target: { value: '5' } })
    fireEvent.click(screen.getByText('Kategorileri Üret'))
    await waitFor(() => expect(mocks.generateCategories).toHaveBeenCalledTimes(2))
    const [a, b] = mocks.generateCategories.mock.calls.map((c) => c[1].idempotency_key)
    expect(a).not.toBe(b)
  })

  it('202 (aynı anahtarla süren deneme) sonrası sunucu durumu yoklanır', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    mocks.generateCategories.mockResolvedValue({
      status: 202,
      data: { ...CATEGORIES_RESP, attempt_status: 'running', categories: [], total_categories: 0 },
    })
    const running = briefState({ category_attempt: attempt(101, 'categories', 'running') })
    mocks.getBriefState
      .mockResolvedValueOnce({ data: briefState() })
      .mockResolvedValueOnce({ data: running })
      .mockResolvedValueOnce({ data: running })
      .mockResolvedValue({ data: stateWithCategories() })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} pollIntervalMs={10} />)
    await openBrief()
    fireEvent.click(await screen.findByText('Kategorileri Üret'))
    expect(await screen.findByText(/Kategori üretimi sürüyor/)).toBeInTheDocument()
    expect(await screen.findByText('Pazarlama Taktikleri')).toBeInTheDocument()
    expect(mocks.getBriefState.mock.calls.length).toBeGreaterThanOrEqual(4)
  })

  it('kilitli brief rozetini, eskimiş brief uyarısını gösterir ve üretimi engeller', async () => {
    mocks.listBriefs.mockResolvedValue({ data: [{ ...BRIEF, is_stale: true }] })
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()
    expect(
      await screen.findByText(/Kanal havuzu bu brief oluşturulduktan sonra/)
    ).toBeInTheDocument()
    expect(screen.getByText('Kategorileri Üret').closest('button')).toBeDisabled()
  })

  it('run değişince önceki run’ın geç gelen brief listesi ekrana yazılmaz', async () => {
    const slow = deferred<{ data: (typeof BRIEF)[] }>()
    mocks.listBriefs.mockImplementation((runId: number) =>
      runId === RUN_ID ? slow.promise : Promise.resolve({ data: [] })
    )
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    rerender(<SocialBriefWorkspace runId={20} pool={POOL} />)
    expect(await screen.findByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
    await act(async () => slow.resolve({ data: [BRIEF] }))
    expect(screen.queryByText('Brief #42')).not.toBeInTheDocument()
    expect(screen.getByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
  })

  it('workspace değişince eski workspace’in geç yanıtı yeni ekranı kirletmez', async () => {
    const slow = deferred<{ data: (typeof BRIEF)[] }>()
    mocks.listBriefs.mockImplementation((_run: number, ws: number) =>
      ws === WS_ID ? slow.promise : Promise.resolve({ data: [] })
    )
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    act(() => setWorkspace(8, 'Başka Marka'))
    expect(await screen.findByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
    await act(async () => slow.resolve({ data: [BRIEF] }))
    expect(screen.queryByText('Brief #42')).not.toBeInTheDocument()
    expect(mocks.listBriefs).toHaveBeenCalledWith(RUN_ID, 8)
  })

  it('havuz: eski run’ın geç gelen SOCIAL havuzu yeni run’ın seçicisine yazılmaz', async () => {
    const slowPool = deferred<{ data: { channels: { SOCIAL: typeof POOL } } }>()
    mocks.getPools.mockImplementation((runId: number) =>
      runId === RUN_ID
        ? slowPool.promise
        : Promise.resolve({
            data: { channels: { SOCIAL: [{ ...POOL[0], id: 900, keyword: 'yeni run kelimesi' }] } },
          })
    )
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} />)
    rerender(<SocialBriefWorkspace runId={20} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('yeni run kelimesi')).toBeInTheDocument()
    await act(async () => slowPool.resolve({ data: { channels: { SOCIAL: POOL } } }))
    expect(screen.queryByText('seo optimizasyonu')).not.toBeInTheDocument()
  })

  it('brief oluşturma yanıtı kapsam değiştikten sonra gelirse yeni kapsamda açılmaz', async () => {
    const slowCreate = deferred<{ data: typeof BRIEF }>()
    mocks.createBrief.mockReturnValue(slowCreate.promise)
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    fireEvent.click(screen.getByText('dijital pazarlama ajansı'))
    fireEvent.click(screen.getByText('Hedef Ekle'))
    fireEvent.click(screen.getByText("Brief'i Kaydet ve Aç"))
    rerender(<SocialBriefWorkspace runId={20} pool={POOL} />)
    await act(async () => slowCreate.resolve({ data: BRIEF }))
    expect(screen.queryByText('Brief #42')).not.toBeInTheDocument()
    expect(screen.getByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
  })

  it('kategori yanıtı kapsam değiştikten sonra gelirse yeni ekrana yazılmaz', async () => {
    const slowCats = deferred<{ status: number; data: SocialCategoriesGenerateResponse }>()
    mocks.listBriefs.mockImplementation((runId: number) =>
      Promise.resolve({ data: runId === RUN_ID ? [BRIEF] : [] })
    )
    mocks.generateCategories.mockReturnValue(slowCats.promise)
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await openBrief()
    fireEvent.click(await screen.findByText('Kategorileri Üret'))
    rerender(<SocialBriefWorkspace runId={20} pool={POOL} />)
    expect(await screen.findByText('Henüz brief oluşturulmadı')).toBeInTheDocument()
    await act(async () => slowCats.resolve({ status: 201, data: CATEGORIES_RESP }))
    expect(screen.queryByText('Pazarlama Taktikleri')).not.toBeInTheDocument()
  })

  it('FEATURE_DISABLED: klasik akışa yönlendirmeden açıklama sunar', async () => {
    mocks.listBriefs.mockRejectedValue(apiError(404, 'FEATURE_DISABLED'))
    render(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    expect(await screen.findByText(/henüz açık değil/)).toBeInTheDocument()
    expect(screen.queryByText("İlk Brief'i Oluştur")).not.toBeInTheDocument()
    expect(screen.getByText(/yöneticinizden özelliği/)).toBeInTheDocument()
    expect(screen.queryByText('Klasik sihirbaza geç')).not.toBeInTheDocument()
  })
})

describe('Havuz kaynağı ve yükleme durumu (takılı spinner regresyonu)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace(WS_ID)
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [] })
  })

  afterEach(() => cleanup())

  it('yedek istek hiç çözülmese bile üstten dolu havuz gelince spinner kalkar', async () => {
    mocks.getPools.mockReturnValue(new Promise(() => {})) // asla çözülmez
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('Kanal havuzu yükleniyor...')).toBeInTheDocument()

    rerender(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} />)
    await waitFor(() =>
      expect(screen.queryByText('Kanal havuzu yükleniyor...')).not.toBeInTheDocument()
    )
    expect(screen.getByText('dijital pazarlama ajansı')).toBeInTheDocument()
  })

  it('üst havuz verilince (boş başlasa bile) ikinci havuz isteği atılmaz', async () => {
    mocks.getPools.mockReturnValue(new Promise(() => {}))
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} pool={[]} poolLoading />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('Kanal havuzu yükleniyor...')).toBeInTheDocument()
    rerender(<SocialBriefWorkspace runId={RUN_ID} pool={POOL} poolLoading={false} />)
    expect(await screen.findByText('seo optimizasyonu')).toBeInTheDocument()
    expect(screen.queryByText('Kanal havuzu yükleniyor...')).not.toBeInTheDocument()
    expect(mocks.getPools).not.toHaveBeenCalled()
  })

  it('üst havuz hatası sessiz boş liste değil; tekrar dene üst kaynağı çağırır', async () => {
    const retry = vi.fn()
    render(
      <SocialBriefWorkspace
        runId={RUN_ID}
        pool={[]}
        poolError="Kanal havuzu alınamadı."
        onRetryPool={retry}
      />
    )
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    const alert = (await screen.findByText(/Kanal havuzu alınamadı/)).closest(
      '[role="alert"]'
    ) as HTMLElement
    expect(screen.queryByText('Sosyal kanal havuzunda kelime bulunamadı.')).not.toBeInTheDocument()
    fireEvent.click(within(alert).getByText('Tekrar dene'))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('tek başına kullanımda yedek istek hatası gösterilir ve tekrar dene yeniden yükler', async () => {
    mocks.getPools
      .mockRejectedValueOnce({ status: 500, message: 'x' })
      .mockResolvedValue({ data: { channels: { SOCIAL: POOL } } })
    render(<SocialBriefWorkspace runId={RUN_ID} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    const alert = (await screen.findByText(/Sunucu hatası oluştu/)).closest(
      '[role="alert"]'
    ) as HTMLElement
    fireEvent.click(within(alert).getByText('Tekrar dene'))
    expect(await screen.findByText('dijital pazarlama ajansı')).toBeInTheDocument()
    expect(screen.queryByText('Kanal havuzu yükleniyor...')).not.toBeInTheDocument()
    expect(mocks.getPools).toHaveBeenCalledTimes(2)
  })

  it('run değişirken çözülmeyen eski yedek istek yeni run’da spinner bırakmaz', async () => {
    mocks.getPools.mockImplementation((runId: number) =>
      runId === RUN_ID
        ? new Promise(() => {})
        : Promise.resolve({
            data: { channels: { SOCIAL: [{ ...POOL[0], id: 900, keyword: 'yeni run' }] } },
          })
    )
    const { rerender } = render(<SocialBriefWorkspace runId={RUN_ID} />)
    rerender(<SocialBriefWorkspace runId={20} />)
    fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
    expect(await screen.findByText('yeni run')).toBeInTheDocument()
    expect(screen.queryByText('Kanal havuzu yükleniyor...')).not.toBeInTheDocument()
  })
})

describe('SocialPanel — doğrudan brief akışı', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace(WS_ID)
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [BRIEF] })
    mocks.getBriefState.mockResolvedValue({ data: briefState() })
    mocks.getPools.mockResolvedValue({ data: { channels: { SOCIAL: POOL } } })
  })

  afterEach(() => cleanup())

  it('mod seçici göstermeden brief akışını açar', async () => {
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    expect(await screen.findByText('Brief #42')).toBeInTheDocument()
    expect(screen.queryByRole('tablist', { name: 'Sosyal üretim modu' })).not.toBeInTheDocument()
    expect(screen.queryByText('Klasik Sihirbaz (Legacy)')).not.toBeInTheDocument()
  })

  it('geçmiş düğmesini brief kartında Yeni Brief yanında gösterir', async () => {
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    const heading = await screen.findByText('Kayıtlı Sosyal Briefler')
    const header = heading.closest('.sb-card-head') as HTMLElement
    expect(within(header).getByRole('button', { name: 'Yeni Brief Oluştur' })).toBeInTheDocument()
    expect(within(header).getByRole('button', { name: 'Geçmiş Sosyal İçerikler' })).toHaveClass(
      'sb-btn-history'
    )
    expect(screen.queryByRole('button', { name: 'Geçmiş Sosyal İçerikler' })).toBe(
      within(header).getByRole('button', { name: 'Geçmiş Sosyal İçerikler' })
    )
  })

  it('backend akışı kapalıysa otomatik klasik sihirbaza geçmez', async () => {
    mocks.listBriefs.mockRejectedValue(apiError(404, 'FEATURE_DISABLED'))
    render(<SocialPanel runId={RUN_ID} pool={POOL} />)
    expect(await screen.findByText(/henüz açık değil/)).toBeInTheDocument()
    expect(screen.queryByRole('tablist', { name: 'Sosyal üretim modu' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Geçmiş Sosyal İçerikler' })).toBeInTheDocument()
  })
})
