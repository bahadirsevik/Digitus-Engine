import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ChannelWorkspaceView from './ChannelWorkspaceView'
import { useBrandStore } from '../stores/brandStore'
import { FORMAT_MATRIX, deferred } from './generation/socialBrief/testFixtures'

const mocks = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getPools: vi.fn(),
  removePoolItem: vi.fn(),
  listByRun: vi.fn(),
  getFormatMatrix: vi.fn(),
  listBriefs: vi.fn(),
  getContentHistory: vi.fn(),
}))

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    scoringApi: { ...actual.scoringApi, listRuns: mocks.listRuns },
    channelsApi: {
      ...actual.channelsApi,
      getPools: mocks.getPools,
      removePoolItem: mocks.removePoolItem,
    },
    tasksApi: { ...actual.tasksApi, listByRun: mocks.listByRun },
    socialBriefApi: {
      ...actual.socialBriefApi,
      getFormatMatrix: mocks.getFormatMatrix,
      listBriefs: mocks.listBriefs,
      getContentHistory: mocks.getContentHistory,
    },
  }
})

const RUNS = [
  { id: 10, run_name: 'Eylül', status: 'channel_assigned' },
  { id: 11, run_name: 'Ekim', status: 'channel_assigned' },
]

const row = (id: number, keywordId: number, keyword: string) => ({
  id,
  keyword_id: keywordId,
  keyword,
  volume: 100 * id,
  adjusted_score: 50 - id,
})

const POOL_10 = [
  row(1, 101, 'yapay zeka hisseleri'),
  row(2, 102, 'gelecegin hisseleri'),
  row(3, 103, 'piyasa analizi'),
  row(4, 104, 'blockchain hisse'),
  row(5, 105, 'bist hisseleri'),
  row(6, 106, 'iletisim hisseleri'),
  row(7, 107, 'enerji hisseleri'),
]
const POOL_11 = [row(21, 201, 'ekim kelimesi'), row(22, 202, 'ekim ikinci')]

function poolsResponse(rows: typeof POOL_10) {
  return { data: { channels: { SOCIAL: rows }, capacities: {} } }
}

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

function renderPage(runId = 10) {
  return render(
    <MemoryRouter initialEntries={[`/social?run_id=${runId}`]}>
      <ChannelWorkspaceView channel="SOCIAL" />
    </MemoryRouter>
  )
}

const topPick = (keyword: string) =>
  screen.getByRole('button', {
    name: new RegExp(`(Brief'e ekle|Brief seçiminden çıkar): ${keyword}$`),
  })

async function openBriefForm() {
  fireEvent.click(await screen.findByText("İlk Brief'i Oluştur"))
  await screen.findByText('Yeni Sosyal Brief Oluştur')
}

describe('Social Havuzu kartı ↔ brief formu (tek seçim durumu)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    setWorkspace(7)
    mocks.listRuns.mockResolvedValue({ data: RUNS })
    mocks.listByRun.mockResolvedValue({ data: [] })
    mocks.getPools.mockImplementation((runId: number) =>
      Promise.resolve(poolsResponse(runId === 10 ? POOL_10 : POOL_11))
    )
    mocks.getFormatMatrix.mockResolvedValue({ data: FORMAT_MATRIX })
    mocks.listBriefs.mockResolvedValue({ data: [] })
    mocks.getContentHistory.mockResolvedValue({
      data: { brand_profile_id: 7, total: 0, limit: 20, offset: 0, has_more: false, items: [] },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('form kapalıyken kartlar seçilemez; form açılınca seçim düğmesi belirir', async () => {
    renderPage()
    await screen.findByText('yapay zeka hisseleri')
    expect(screen.queryByRole('button', { name: /Brief'e ekle/ })).not.toBeInTheDocument()
    await openBriefForm()
    expect(topPick('yapay zeka hisseleri')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('note')).toHaveTextContent('0/5')
  })

  it('üst karttan seçim yalnız seçilen çipe yansır; altta ikinci havuz gösterilmez', async () => {
    renderPage()
    await openBriefForm()

    fireEvent.click(topPick('piyasa analizi'))
    const pick = topPick('piyasa analizi')
    expect(pick).toHaveAttribute('aria-pressed', 'true')
    expect(pick.closest('.chx-poolrow')).toHaveClass('is-picked')
    expect(screen.getByRole('button', { name: 'Kaldır: piyasa analizi' })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Havuzda kelime ara...')).not.toBeInTheDocument()
    expect(document.querySelector('.sb-kw-option')).toBeNull()

    // Alttaki çipten kaldır → üst kart vurgusu kalkar
    fireEvent.click(screen.getByRole('button', { name: 'Kaldır: piyasa analizi' }))
    expect(topPick('piyasa analizi')).toHaveAttribute('aria-pressed', 'false')
    expect(topPick('piyasa analizi').closest('.chx-poolrow')).not.toHaveClass('is-picked')

    // Üst havuzdan başka kelime seç → aşağıda yalnız seçilen çip görünür.
    fireEvent.click(topPick('blockchain hisse'))
    expect(topPick('blockchain hisse')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Kaldır: blockchain hisse' })).toBeInTheDocument()
  })

  it('en fazla 5 kelime: altıncı üst kart seçilmez ve uyarı gösterilir', async () => {
    renderPage()
    await openBriefForm()
    for (const kw of POOL_10.slice(0, 5)) fireEvent.click(topPick(kw.keyword))
    fireEvent.click(topPick('iletisim hisseleri'))
    expect(topPick('iletisim hisseleri')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('alert')).toHaveTextContent('En fazla 5 anahtar kelime')
    expect(screen.getAllByText(/Seçilen:/)[0].textContent).toContain('Seçilen: 5 / 5')
    // Bir tanesi çıkarılınca yeniden seçilebilir
    fireEvent.click(topPick('yapay zeka hisseleri'))
    fireEvent.click(topPick('iletisim hisseleri'))
    expect(topPick('iletisim hisseleri')).toHaveAttribute('aria-pressed', 'true')
  })

  it('"havuzdan sil" düğmesi seçimi tetiklemez; silme çalışır, silinen kelime seçimden düşer', async () => {
    mocks.removePoolItem.mockResolvedValue({ data: {} })
    renderPage()
    await openBriefForm()
    fireEvent.click(topPick('gelecegin hisseleri'))
    fireEvent.click(topPick('piyasa analizi'))

    const rowOf = (kw: string) => topPick(kw).closest('.chx-poolrow') as HTMLElement
    // Seçili olmayan satırın silme düğmesi → seçim değişmez, silme çağrılır
    fireEvent.click(within(rowOf('bist hisseleri')).getByTitle('Bu çalışmanın havuzundan kaldır'))
    await waitFor(() => expect(mocks.removePoolItem).toHaveBeenCalledWith(10, 5, 7))
    await waitFor(() => expect(screen.queryByText('bist hisseleri')).not.toBeInTheDocument())
    expect(topPick('gelecegin hisseleri')).toHaveAttribute('aria-pressed', 'true')

    // Seçili satırın silme düğmesi → satır silinir ve seçimden de düşer (toggle değil)
    fireEvent.click(within(rowOf('piyasa analizi')).getByTitle('Bu çalışmanın havuzundan kaldır'))
    await waitFor(() => expect(mocks.removePoolItem).toHaveBeenCalledWith(10, 3, 7))
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Kaldır: piyasa analizi' })
      ).not.toBeInTheDocument()
    )
    expect(topPick('gelecegin hisseleri')).toHaveAttribute('aria-pressed', 'true')

    // Onay iptal → silme yok, seçim de değişmez
    vi.mocked(window.confirm).mockReturnValue(false)
    fireEvent.click(within(rowOf('enerji hisseleri')).getByTitle('Bu çalışmanın havuzundan kaldır'))
    expect(mocks.removePoolItem).toHaveBeenCalledTimes(2)
    expect(topPick('enerji hisseleri')).toHaveAttribute('aria-pressed', 'false')
  })

  it('seçim klavyeyle yapılabilir (Enter/Space odaklanabilir düğmede)', async () => {
    renderPage()
    await openBriefForm()
    const pick = topPick('yapay zeka hisseleri')
    pick.focus()
    expect(pick).toHaveFocus()
    expect(pick.tagName).toBe('BUTTON') // yerel düğme: Enter/Space tıklama üretir
    fireEvent.click(pick)
    expect(pick).toHaveAttribute('aria-pressed', 'true')
  })

  it('run değişince önceki run’ın seçimi yeni kapsamda görünmez', async () => {
    renderPage()
    await openBriefForm()
    fireEvent.click(topPick('piyasa analizi'))
    fireEvent.change(screen.getByTitle('Analiz seç'), { target: { value: '11' } })
    await screen.findByText('ekim kelimesi')
    expect(screen.queryByText('piyasa analizi')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Kaldır: / })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Brief seçiminden çıkar/ })).not.toBeInTheDocument()
  })

  it('workspace değişince seçim ve eski havuz ekranda kalmaz', async () => {
    renderPage()
    await openBriefForm()
    fireEvent.click(topPick('piyasa analizi'))
    mocks.listRuns.mockResolvedValue({ data: [{ id: 30, run_name: 'Başka', status: 'completed' }] })
    mocks.getPools.mockResolvedValue(poolsResponse([row(31, 301, 'baska marka kelimesi')]))
    act(() => setWorkspace(8))
    await screen.findByText('baska marka kelimesi')
    expect(screen.queryByText('piyasa analizi')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Brief seçiminden çıkar/ })).not.toBeInTheDocument()
  })

  it('hızlı run değişiminde eski run’ın geç gelen havuzu yeni run’ın üzerine yazılmaz', async () => {
    const slow10 = deferred<ReturnType<typeof poolsResponse>>()
    mocks.getPools.mockImplementation((runId: number) =>
      runId === 10 ? slow10.promise : Promise.resolve(poolsResponse(POOL_11))
    )
    renderPage()
    await waitFor(() => expect(mocks.getPools).toHaveBeenCalledWith(10, 7))
    fireEvent.change(await screen.findByTitle('Analiz seç'), { target: { value: '11' } })
    await screen.findByText('ekim kelimesi')
    await act(async () => slow10.resolve(poolsResponse(POOL_10)))
    expect(screen.queryByText('yapay zeka hisseleri')).not.toBeInTheDocument()
    expect(screen.getByText('ekim kelimesi')).toBeInTheDocument()
    expect(screen.queryByText('Havuz yükleniyor...')).not.toBeInTheDocument()
  })

  it('havuz isteği başarısız olursa sonsuz yükleme/boş liste yerine hata + tekrar dene', async () => {
    mocks.getPools
      .mockRejectedValueOnce({ status: 500, message: 'x' })
      .mockResolvedValue(poolsResponse(POOL_10))
    renderPage()
    const alert = await screen.findByText(/Kanal havuzu alınamadı/)
    expect(screen.queryByText('Havuz yükleniyor...')).not.toBeInTheDocument()
    fireEvent.click(within(alert.closest('[role="alert"]') as HTMLElement).getByText('Tekrar dene'))
    expect(await screen.findByText('yapay zeka hisseleri')).toBeInTheDocument()
    // Brief formu ikinci bir havuz isteği atmaz: tek kaynak üst sayfa
    await openBriefForm()
    expect(mocks.getPools).toHaveBeenCalledTimes(2)
  })
})
