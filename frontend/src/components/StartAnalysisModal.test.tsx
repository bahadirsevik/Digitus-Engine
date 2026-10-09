import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import StartAnalysisModal from './StartAnalysisModal'
import { scoringApi, workspaceApi } from '../services/api'
import { useBrandStore } from '../stores/brandStore'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    scoringApi: {
      getCapabilities: vi.fn(),
      createRun: vi.fn(),
      executeRun: vi.fn(),
    },
    workspaceApi: {
      ...actual.workspaceApi,
      get: vi.fn(),
      previewLocationFilter: vi.fn(),
    },
  }
})

const mockedScoring = vi.mocked(scoringApi)
const mockedWorkspace = vi.mocked(workspaceApi)

function response(data: unknown) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} } as never
}

function locationPreviewResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return response({
    has_keywords: true,
    mode: 'exclude_all',
    evaluated_count: 100,
    kept_count: 90,
    excluded_count: 10,
    excluded_by_reason: { LOCATION_CITY_FILTER: 10 },
    excluded_by_city: [],
    sample_excluded: [],
    sample_exempted: [],
    city_lexicon_version: 'tr-provinces-v1',
    city_lexicon_sha256: 'abc',
    universe_fingerprint: 'universe-fp-1',
    location_policy_fingerprint: 'policy-fp-1',
    is_saved_policy: true,
    ...overrides,
  })
}

// KASITLI OLARAK BAYAT/YANLIŞ: bileşen artık lokasyon kararını Zustand
// persist store'undan (localStorage) OKUMAZ — yalnız `workspaceApi.get` ile
// TAZE çekilen değeri kullanır. Store'u burada sabit 'none' bırakmak, her
// testin (locationMode aktif geçse bile) fix'in store'u güven zincirinden
// çıkardığını dolaylı olarak doğrulamasını sağlar — bkz. "QA blocker" testi
// aşağıda ayrıca da açıkça adlandırılmıştır.
function seedStaleStore() {
  useBrandStore.setState({
    activeWorkspace: {
      id: 39,
      name: 'Test Marka',
      company_url: 'https://example.com',
      status: 'confirmed',
      profile_data: { location_filter_mode: 'none' },
      suggested_keywords: null,
    },
  } as never)
}

function setup({
  v3 = true,
  capabilityError = false,
  locationMode = 'none',
  workspaceFetchFails = false,
  previewError,
  previewOverrides,
  onStarted = () => {},
}: {
  v3?: boolean
  capabilityError?: boolean
  locationMode?: 'none' | 'exclude_all' | 'focus_only'
  workspaceFetchFails?: boolean
  previewError?: unknown
  previewOverrides?: Partial<Record<string, unknown>>
  onStarted?: (runId: number) => void
} = {}) {
  seedStaleStore()
  if (capabilityError) {
    mockedScoring.getCapabilities.mockRejectedValue(new Error('network'))
  } else {
    mockedScoring.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: v3 }))
  }
  if (workspaceFetchFails) {
    mockedWorkspace.get.mockRejectedValue(new Error('network'))
  } else {
    mockedWorkspace.get.mockResolvedValue(
      response({ id: 39, profile_data: { location_filter_mode: locationMode } })
    )
  }
  mockedScoring.createRun.mockResolvedValue(response({ id: 77 }))
  mockedScoring.executeRun.mockResolvedValue(response({ ok: true }))
  // Önizleme mock'u render'DAN ÖNCE sabitlenir — mount effect'inin ilk
  // gerçek çağrısı zaten doğru (başarılı/başarısız) sonucu görsün; render
  // sonrası override etmek mikro-görev sıralamasına güvenmek anlamına gelir.
  if (previewError !== undefined) {
    mockedWorkspace.previewLocationFilter.mockRejectedValue(previewError)
  } else {
    mockedWorkspace.previewLocationFilter.mockResolvedValue(
      locationPreviewResponse(previewOverrides)
    )
  }
  return render(
    <StartAnalysisModal
      open
      brandProfileId={39}
      poolCount={892}
      onClose={() => {}}
      onStarted={onStarted}
    />
  )
}

describe('StartAnalysisModal v3 ürün akışı', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.setState({ activeWorkspace: null })
  })

  it('motoru salt-okunur v3 olarak gösterir; seçim alanı sunmaz', async () => {
    setup()

    expect(await screen.findByLabelText('Analiz motoru')).toHaveTextContent(
      'v3 — kilitli üretim motoru'
    )
    expect(screen.queryByRole('combobox', { name: 'Analiz motoru' })).toBeNull()
  })

  it('v3 payload ve otomatik kanal atamasıyla başlatır', async () => {
    setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalled())

    expect(mockedScoring.createRun).toHaveBeenCalledWith(
      expect.objectContaining({
        brand_profile_id: 39,
        algorithm_version: 'v3',
        auto_assign_channels: true,
        enable_ads: true,
        enable_seo: true,
        enable_social: true,
      })
    )
    expect(mockedScoring.executeRun).toHaveBeenCalledWith(77, 39)
  })

  it('kelime sayısı silinince 0 eklemez; geçerli sayı girilene kadar başlatmaz', async () => {
    setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    const adsCount = screen.getByRole('spinbutton', { name: 'Ads kelime sayısı' })
    fireEvent.change(adsCount, { target: { value: '' } })

    expect(adsCount).toHaveValue(null)
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
    expect(mockedScoring.createRun).not.toHaveBeenCalled()

    fireEvent.change(adsCount, { target: { value: '12' } })
    expect(adsCount).toHaveValue(12)
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalled())
    expect(mockedScoring.createRun).toHaveBeenCalledWith(
      expect.objectContaining({ ads_capacity: 12 })
    )
  })

  it('sıfır veya ondalık kelime sayısıyla analizi başlatmaz', async () => {
    setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    const seoCount = screen.getByRole('spinbutton', { name: 'SEO kelime sayısı' })
    fireEvent.change(seoCount, { target: { value: '0' } })
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()

    fireEvent.change(seoCount, { target: { value: '1.5' } })
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
    expect(mockedScoring.createRun).not.toHaveBeenCalled()
  })

  it('v3 kapalıysa legacy motora düşmez ve başlatmayı engeller', async () => {
    setup({ v3: false })

    expect(await screen.findByRole('alert')).toHaveTextContent('Motor v3 sunucuda kapalı')
    const start = screen.getByRole('button', { name: /Başlat/ })
    expect(start).toBeDisabled()
    fireEvent.click(start)
    expect(mockedScoring.createRun).not.toHaveBeenCalled()
  })

  it('capability çağrısı başarısızsa legacy motora düşmez', async () => {
    setup({ capabilityError: true })

    expect(await screen.findByRole('alert')).toHaveTextContent('Motor v3 sunucuda kapalı')
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
    expect(mockedScoring.createRun).not.toHaveBeenCalled()
  })

  it('tüm kanallar kapatıldığında başlatmayı engeller', async () => {
    setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    fireEvent.click(screen.getByText('Google Ads'))
    fireEvent.click(screen.getByText('SEO + GEO'))
    fireEvent.click(screen.getByText('Sosyal Medya'))

    const warning = await screen.findByRole('alert')
    expect(warning).toHaveTextContent('En az bir amaç seçin')
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
  })

  it('sosyal medya kanalı herhangi bir strateji veya mod engeli olmadan başlatılabilir', async () => {
    setup()
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    // Yalnız sosyal medya açık kalsın
    fireEvent.click(screen.getByText('Google Ads'))
    fireEvent.click(screen.getByText('SEO + GEO'))

    const start = screen.getByRole('button', { name: /Başlat/ })
    expect(start).toBeEnabled()
    fireEvent.click(start)

    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalled())
    expect(mockedScoring.createRun).toHaveBeenCalledWith(
      expect.objectContaining({
        algorithm_version: 'v3',
        enable_ads: false,
        enable_seo: false,
        enable_social: true,
      })
    )
  })

  it("mod 'none' (sunucuda gerçekten none) iken lokasyon önizlemesi hiç çağrılmaz ve payload'a token eklenmez", async () => {
    setup({ locationMode: 'none' })
    await waitFor(() => expect(mockedWorkspace.get).toHaveBeenCalledWith(39))
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalled())

    expect(mockedWorkspace.previewLocationFilter).not.toHaveBeenCalled()
    const payload = mockedScoring.createRun.mock.calls[0][0]
    expect(payload).not.toHaveProperty('location_universe_fingerprint')
    expect(payload).not.toHaveProperty('location_policy_fingerprint')
    expect(payload).not.toHaveProperty('location_preview_is_saved_policy')
  })
})

describe('StartAnalysisModal lokasyon filtresi kapısı', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useBrandStore.setState({ activeWorkspace: null })
  })

  it('aktif moddayken önizleme otomatik çağrılır ve kaybı gösterir', async () => {
    setup({ locationMode: 'exclude_all' })

    await waitFor(() =>
      expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledWith(39, {
        keyword_selection_mode: 'all',
      })
    )
    expect(await screen.findByTestId('location-gate-summary')).toHaveTextContent('10')
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled()
  })

  it('önizleme başarısız olursa başlatmayı engeller', async () => {
    setup({
      locationMode: 'exclude_all',
      previewError: { status: 502, message: 'Sunucu hatası', detail: 'Sunucu hatası' },
    })

    expect(await screen.findByTestId('location-gate-error')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    expect(mockedScoring.createRun).not.toHaveBeenCalled()
  })

  it('önizleme sıfır keyword bırakırsa başlatmayı engeller', async () => {
    setup({
      locationMode: 'focus_only',
      previewOverrides: { kept_count: 0, excluded_count: 100 },
    })

    expect(await screen.findByTestId('location-gate-error')).toHaveTextContent(
      'analiz başlatılamaz'
    )
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
  })

  it('başarılı önizlemenin fingerprint alanlarını createRun payload’ına taşır', async () => {
    setup({ locationMode: 'exclude_all' })
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalled())

    expect(mockedScoring.createRun).toHaveBeenCalledWith(
      expect.objectContaining({
        location_universe_fingerprint: 'universe-fp-1',
        location_policy_fingerprint: 'policy-fp-1',
        location_preview_is_saved_policy: true,
      })
    )
  })

  it('workspace bilgisi sunucudan alınamazsa güvenli tarafta kalır ve başlatmayı engeller', async () => {
    setup({ workspaceFetchFails: true })

    expect(await screen.findByTestId('location-gate-error')).toHaveTextContent(
      'Marka çalışması bilgisi alınamadı'
    )
    expect(screen.getByRole('button', { name: /Başlat/ })).toBeDisabled()
    expect(mockedWorkspace.previewLocationFilter).not.toHaveBeenCalled()
  })

  it('execute LOCATION_PREVIEW_STALE dönerse run başlamış gibi ilerlemez, önizleme yenilenir', async () => {
    const onStarted = vi.fn()
    setup({ locationMode: 'exclude_all', onStarted })
    mockedScoring.executeRun.mockRejectedValue({
      status: 409,
      message: 'bayat',
      detail: { code: 'LOCATION_PREVIEW_STALE', message: 'bayat' },
    })

    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))

    await waitFor(() => expect(mockedScoring.executeRun).toHaveBeenCalled())
    expect(onStarted).not.toHaveBeenCalled()
    expect(await screen.findByText(/bayatladığı için/)).toBeInTheDocument()
  })
})

// ── QA blocker — sonsuz/açıklamasız retry döngüsü (bkz. koordinatör mesajı) ──
// Kök neden: karar Zustand persist store'undan (localStorage) okunuyordu;
// store yalnız BrandProfile.tsx/DashboardCockpit.tsx'teki açık
// setActiveWorkspace çağrılarıyla güncellenir — reload'suz bir sekme, ikinci
// bir sekme veya başka yerde yapılan bir politika değişikliği store'u
// bayatlatabilir. Düzeltme: (b) modal her açılışta workspace'i SUNUCUDAN
// taze çeker (workspaceApi.get) ve karar ORADAN türetilir; (a) bir önizleme
// başarıyla yüklendiyse önizlemenin KENDİ `mode` alanı kararı ele alır
// (kendi kendini iyileştiren retry — locationPreview.mode HER ZAMAN canlı
// backend durumunu yansıtır).
describe('StartAnalysisModal — lokasyon kapısı store bayatlığına karşı dayanıklı (QA blocker)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('store bayat "none" derken sunucudaki kayıtlı politika aktifse İLK başlatma denemesi gate alanlarını taşır', async () => {
    // Store KASITLI OLARAK "none" — bileşen bunu okumamalı.
    useBrandStore.setState({
      activeWorkspace: {
        id: 39,
        name: 'Test Marka',
        company_url: 'https://example.com',
        status: 'confirmed',
        profile_data: { location_filter_mode: 'none' },
        suggested_keywords: null,
      },
    } as never)
    // Ama SUNUCUDAN taze çekilen workspace (workspaceApi.get) aktif modu
    // döndürüyor — karar buna göre verilmeli.
    mockedScoring.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    mockedWorkspace.get.mockResolvedValue(
      response({ id: 39, profile_data: { location_filter_mode: 'exclude_all' } })
    )
    mockedWorkspace.previewLocationFilter.mockResolvedValue(locationPreviewResponse())
    mockedScoring.createRun.mockResolvedValue(response({ id: 77 }))
    mockedScoring.executeRun.mockResolvedValue(response({ ok: true }))

    render(
      <StartAnalysisModal
        open
        brandProfileId={39}
        poolCount={892}
        onClose={() => {}}
        onStarted={() => {}}
      />
    )

    await waitFor(() => expect(mockedWorkspace.get).toHaveBeenCalledWith(39))
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))

    // İLK deneme — createRun tek sefer çağrılır ve gate alanlarını taşır.
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalledTimes(1))
    expect(mockedScoring.createRun).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        location_universe_fingerprint: 'universe-fp-1',
        location_policy_fingerprint: 'policy-fp-1',
        location_preview_is_saved_policy: true,
      })
    )
  })

  it('retry döngüsü kendi kendini iyileştirir: LOCATION_PREVIEW_REQUIRED sonrası ikinci tıklama gate alanlarını taşır', async () => {
    useBrandStore.setState({ activeWorkspace: null })
    mockedScoring.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    // Taze fetch bile — bir yarış/gecikme yüzünden — YANLIŞ ('none') döner;
    // bileşen bunun İLK denemede 400 ile geri döneceğini bilemez.
    mockedWorkspace.get.mockResolvedValue(
      response({ id: 39, profile_data: { location_filter_mode: 'none' } })
    )
    mockedWorkspace.previewLocationFilter.mockResolvedValue(locationPreviewResponse())
    mockedScoring.createRun
      .mockRejectedValueOnce({
        status: 400,
        message: 'gerekli',
        detail: { code: 'LOCATION_PREVIEW_REQUIRED', message: 'gerekli' },
      })
      .mockResolvedValue(response({ id: 77 }))
    mockedScoring.executeRun.mockResolvedValue(response({ ok: true }))
    const onStarted = vi.fn()

    render(
      <StartAnalysisModal
        open
        brandProfileId={39}
        poolCount={892}
        onClose={() => {}}
        onStarted={onStarted}
      />
    )

    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())
    // Mod bilgisi "none" olduğu için mount'ta önizleme HENÜZ çağrılmadı.
    expect(mockedWorkspace.previewLocationFilter).not.toHaveBeenCalled()

    // 1. tıklama: gate alanı yok → createRun 400 LOCATION_PREVIEW_REQUIRED döner.
    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalledTimes(1))
    expect(mockedScoring.createRun.mock.calls[0][0]).not.toHaveProperty(
      'location_universe_fingerprint'
    )
    expect(onStarted).not.toHaveBeenCalled()

    // Kendi kendini iyileştirme: hata sonrası tetiklenen önizleme yüklenir,
    // buton yeniden etkinleşir.
    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    // 2. tıklama: artık gate alanlarını TAŞIR — sonsuz döngü yok.
    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalledTimes(2))
    expect(mockedScoring.createRun).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        location_universe_fingerprint: 'universe-fp-1',
        location_policy_fingerprint: 'policy-fp-1',
        location_preview_is_saved_policy: true,
      })
    )
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(77))
  })

  // QA bulgu 1: bir önizleme yeniden `none` dönerse (kayıtlı politika başka
  // yerde kapatıldıysa), bayat AKTİF `fetchedLocationMode` KAZANMAMALI —
  // önizlemenin kendi güncel `mode`'u (`none` dâhil) kararı ele almalı.
  // Aksi halde modal token eklemeye devam eder ve backend `none` için
  // token'ı reddedince YENİ bir döngüye girilirdi.
  it("yenilenen önizleme mode='none' dönerse ikinci istek gate alanı TAŞIMAZ ve run normal başlar", async () => {
    useBrandStore.setState({ activeWorkspace: null })
    mockedScoring.getCapabilities.mockResolvedValue(response({ engine_v3_enabled: true }))
    // Workspace fetch kayıtlı politikayı AKTİF (exclude_all) olarak döndürür.
    mockedWorkspace.get.mockResolvedValue(
      response({ id: 39, profile_data: { location_filter_mode: 'exclude_all' } })
    )
    // Mount'taki otomatik önizleme aktif modu doğrular (ilk çağrı).
    // Yenilenen (2.) önizleme ise politika ELSEWHERE 'none'a çekilmiş gibi
    // mode='none' döner — has_keywords/kept_count hâlâ dolu (boş havuz
    // engeli bu senaryoyu karıştırmasın diye).
    mockedWorkspace.previewLocationFilter
      .mockResolvedValueOnce(locationPreviewResponse())
      .mockResolvedValue(
        locationPreviewResponse({ mode: 'none', kept_count: 100, excluded_count: 0 })
      )
    mockedScoring.createRun
      .mockRejectedValueOnce({
        status: 409,
        message: 'bayat',
        detail: { code: 'LOCATION_PREVIEW_STALE', message: 'bayat' },
      })
      .mockResolvedValue(response({ id: 78 }))
    mockedScoring.executeRun.mockResolvedValue(response({ ok: true }))
    const onStarted = vi.fn()

    render(
      <StartAnalysisModal
        open
        brandProfileId={39}
        poolCount={892}
        onClose={() => {}}
        onStarted={onStarted}
      />
    )

    // Mount: mod aktif olduğu için önizleme otomatik çağrılır (1. çağrı,
    // mode='exclude_all' döner) ve buton etkinleşir.
    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    // 1. tıklama: gate alanları TAŞINIR (mode hâlâ aktif) ama backend bayat
    // döner (LOCATION_PREVIEW_STALE) — bu, önizlemenin yeniden çekilmesini
    // tetikler ve bu kez policy 'none'a çekilmiş görünür.
    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalledTimes(1))
    expect(mockedScoring.createRun).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ location_universe_fingerprint: 'universe-fp-1' })
    )
    expect(onStarted).not.toHaveBeenCalled()

    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByRole('button', { name: /Başlat/ })).toBeEnabled())

    // 2. tıklama: artık mode='none' — hiçbir gate alanı TAŞINMAMALI ve run
    // normal başlamalı.
    fireEvent.click(screen.getByRole('button', { name: /Başlat/ }))
    await waitFor(() => expect(mockedScoring.createRun).toHaveBeenCalledTimes(2))
    const secondPayload = mockedScoring.createRun.mock.calls[1][0]
    expect(secondPayload).not.toHaveProperty('location_universe_fingerprint')
    expect(secondPayload).not.toHaveProperty('location_policy_fingerprint')
    expect(secondPayload).not.toHaveProperty('location_preview_is_saved_policy')
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(78))
  })
})
