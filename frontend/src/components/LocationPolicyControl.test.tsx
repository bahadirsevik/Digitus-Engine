import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import LocationPolicyControl from './LocationPolicyControl'
import { workspaceApi } from '../services/api'
import type { LocationFilterMode } from '../services/locationPolicy'

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    workspaceApi: {
      ...actual.workspaceApi,
      previewLocationFilter: vi.fn(),
    },
  }
})

const mockedWorkspace = vi.mocked(workspaceApi)

function response(data: unknown) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} } as never
}

function previewResponse(overrides: Partial<Record<string, unknown>> = {}) {
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
    universe_fingerprint: 'u',
    location_policy_fingerprint: 'p',
    is_saved_policy: false,
    ...overrides,
  })
}

// Çözülme zamanını testten kontrol etmek için — gerçek ağ gecikmesini
// simüle eder (QA bulgu 2: sıralama koruması testi).
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function baseProps(
  overrides: {
    mode?: LocationFilterMode
    focusCities?: string[]
    exemptTerms?: string[]
    companyName?: string
    brandTerms?: string[]
    onModeChange?: (mode: LocationFilterMode) => void
    onFocusCitiesChange?: (cities: string[]) => void
    onExemptTermsChange?: (terms: string[]) => void
  } = {}
) {
  return {
    workspaceId: 1,
    companyName: overrides.companyName ?? 'Örnek Firma',
    brandTerms: overrides.brandTerms ?? [],
    mode: overrides.mode ?? ('none' as LocationFilterMode),
    focusCities: overrides.focusCities ?? [],
    exemptTerms: overrides.exemptTerms ?? [],
    onModeChange: overrides.onModeChange ?? (() => {}),
    onFocusCitiesChange: overrides.onFocusCitiesChange ?? (() => {}),
    onExemptTermsChange: overrides.onExemptTermsChange ?? (() => {}),
  }
}

describe('LocationPolicyControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedWorkspace.previewLocationFilter.mockResolvedValue(
      response({
        has_keywords: true,
        mode: 'none',
        evaluated_count: 0,
        kept_count: 0,
        excluded_count: 0,
        excluded_by_reason: {},
        excluded_by_city: [],
        sample_excluded: [],
        sample_exempted: [],
        city_lexicon_version: 'tr-provinces-v1',
        city_lexicon_sha256: 'abc',
        universe_fingerprint: 'u',
        location_policy_fingerprint: 'p',
        is_saved_policy: false,
      })
    )
  })

  it('üç seçenekli şehir politikası kontrolünü ve varsayılan seçimi gösterir', () => {
    render(<LocationPolicyControl {...baseProps({ mode: 'none' })} />)

    expect(screen.getByRole('radiogroup', { name: 'Şehir politikası' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Şehir filtresi yok' })).toBeChecked()
    expect(
      screen.getByRole('radio', { name: "Tüm şehirli keyword'leri hariç tut" })
    ).not.toBeChecked()
    expect(screen.getByRole('radio', { name: 'Yalnız seçtiğim şehirler kalsın' })).not.toBeChecked()
  })

  it('focus_only dışındayken odak şehir seçici pasif olur ama kayıtlı şehirler silinmez', () => {
    render(
      <LocationPolicyControl {...baseProps({ mode: 'exclude_all', focusCities: ['İstanbul'] })} />
    )

    // Kayıtlı şehir hâlâ görünür (silinmedi) — yalnız etkisiz.
    expect(screen.getByText('İstanbul')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'İstanbul sil' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Şehir ekle/i })).toBeDisabled()
    expect(screen.getByText(/etkisiz/i)).toBeInTheDocument()
  })

  it('focus_only modunda odak şehir seçici etkin olur', () => {
    render(
      <LocationPolicyControl {...baseProps({ mode: 'focus_only', focusCities: ['İstanbul'] })} />
    )

    expect(screen.getByRole('button', { name: 'İstanbul sil' })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Şehir ekle/i })).toBeEnabled()
  })

  it('şehir ekle önerileri Türkçe büyük/küçük harften bağımsızdır (istanbul → İstanbul)', () => {
    const onFocusCitiesChange = vi.fn()
    render(<LocationPolicyControl {...baseProps({ mode: 'focus_only', onFocusCitiesChange })} />)

    fireEvent.click(screen.getByRole('button', { name: /Şehir ekle/i }))
    const input = screen.getByRole('combobox')

    fireEvent.change(input, { target: { value: 'istanbul' } })
    expect(screen.getByRole('option', { name: 'İstanbul' })).toBeInTheDocument()

    fireEvent.change(input, { target: { value: 'izm' } })
    expect(screen.getByRole('option', { name: 'İzmir' })).toBeInTheDocument()

    fireEvent.change(input, { target: { value: 'canak' } })
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Çanakkale' }))
    expect(onFocusCitiesChange).toHaveBeenCalledWith(['Çanakkale'])
  })

  it('şehir ekle: Enter vurgulanan öneriyi ekler, eklenmiş şehri önermez', () => {
    const onFocusCitiesChange = vi.fn()
    render(
      <LocationPolicyControl
        {...baseProps({ mode: 'focus_only', focusCities: ['Ankara'], onFocusCitiesChange })}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Şehir ekle/i }))
    const input = screen.getByRole('combobox')

    fireEvent.change(input, { target: { value: 'ankara' } })
    expect(screen.queryByRole('option', { name: 'Ankara' })).not.toBeInTheDocument()

    fireEvent.change(input, { target: { value: 'ist' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onFocusCitiesChange).toHaveBeenCalledWith(['Ankara', 'İstanbul'])
  })

  it('muafiyet alanı yalnız aktif modlarda (exclude_all/focus_only) görünür', () => {
    const { rerender } = render(<LocationPolicyControl {...baseProps({ mode: 'none' })} />)
    expect(screen.queryByText('Lokasyon filtresi muafiyetleri')).not.toBeInTheDocument()

    rerender(<LocationPolicyControl {...baseProps({ mode: 'exclude_all' })} />)
    expect(screen.getByText('Lokasyon filtresi muafiyetleri')).toBeInTheDocument()

    rerender(
      <LocationPolicyControl {...baseProps({ mode: 'focus_only', focusCities: ['İstanbul'] })} />
    )
    expect(screen.getByText('Lokasyon filtresi muafiyetleri')).toBeInTheDocument()
  })

  it('marka adı bir il adı içerince uyarı gösterir ama OTOMATİK muafiyet eklemez', () => {
    const onExemptTermsChange = vi.fn()
    render(
      <LocationPolicyControl
        {...baseProps({
          mode: 'exclude_all',
          companyName: 'Van Mobilya',
          onExemptTermsChange,
        })}
      />
    )

    const warning = screen.getByTestId('brand-city-warning')
    expect(warning).toHaveTextContent('Van')
    expect(warning).toHaveTextContent('OTOMATİK muaf tutmaz')
    expect(onExemptTermsChange).not.toHaveBeenCalled()
  })

  it('marka teriminde il adı yoksa uyarı gösterilmez', () => {
    render(
      <LocationPolicyControl
        {...baseProps({ mode: 'exclude_all', companyName: 'Örnek Firma', brandTerms: ['yazılım'] })}
      />
    )

    expect(screen.queryByTestId('brand-city-warning')).not.toBeInTheDocument()
  })

  it("mode='none' iken uyarı gösterilmez (hiçbir şey elenmiyor)", () => {
    render(<LocationPolicyControl {...baseProps({ mode: 'none', companyName: 'Van Mobilya' })} />)

    expect(screen.queryByTestId('brand-city-warning')).not.toBeInTheDocument()
  })
})

// QA bulgu 2: debounce + manuel "Yenile" birlikte, ordering koruması
// (istek-nesli sayacı) olmadan yavaş/eski bir isteğin YENİ bir isteğin
// sonucunu ezmesine izin verebilir. Senaryo: kullanıcı ayarları değiştirir
// (istek A başlar), sonra tekrar değiştirir (istek B başlar) — A YAVAŞ, B
// HIZLI döner. Ekranda B'nin sonucu kalmalı; A sonradan çözülse bile onu
// EZMEMELİ.
describe('LocationPolicyControl — istek sıralama koruması (QA bulgu 2)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('erken başlayıp GEÇ çözülen istek (A), sonra başlayıp ERKEN çözülen isteğin (B) sonucunu ezmez', async () => {
    const callA = deferred<unknown>()
    const callB = deferred<unknown>()
    mockedWorkspace.previewLocationFilter
      .mockImplementationOnce(() => callA.promise as never)
      .mockImplementationOnce(() => callB.promise as never)

    const { rerender } = render(<LocationPolicyControl {...baseProps({ mode: 'exclude_all' })} />)

    // Mount debounce penceresi (500ms) geçer — istek A başlar (ağda asılı kalır).
    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1), {
      timeout: 2000,
    })

    // Kullanıcı ayarı değiştirir (yeni muafiyet) — yeni debounce penceresi
    // açılır ve süresi dolunca istek B başlar.
    rerender(
      <LocationPolicyControl {...baseProps({ mode: 'exclude_all', exemptTerms: ['örnek'] })} />
    )
    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(2), {
      timeout: 2000,
    })

    // B (YENİ/hızlı) ÖNCE çözülür.
    callB.resolve(previewResponse({ excluded_count: 22, kept_count: 78 }))
    await waitFor(() =>
      expect(screen.getByTestId('location-preview-summary')).toHaveTextContent('22')
    )

    // A (ESKİ/yavaş) SONRADAN çözülür — ekranı EZMEMELİ.
    callA.resolve(previewResponse({ excluded_count: 11, kept_count: 89 }))
    await sleep(80)
    expect(screen.getByTestId('location-preview-summary')).toHaveTextContent('22')
    expect(screen.getByTestId('location-preview-summary')).not.toHaveTextContent('11')
  }, 10000)

  it('geç çözülen İLK isteğin HATASI, sonra çözülen YENİ isteğin başarısını EZMEZ', async () => {
    const callA = deferred<unknown>()
    const callB = deferred<unknown>()
    mockedWorkspace.previewLocationFilter
      .mockImplementationOnce(() => callA.promise as never)
      .mockImplementationOnce(() => callB.promise as never)

    const { rerender } = render(<LocationPolicyControl {...baseProps({ mode: 'exclude_all' })} />)

    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1), {
      timeout: 2000,
    })

    rerender(
      <LocationPolicyControl {...baseProps({ mode: 'exclude_all', exemptTerms: ['örnek'] })} />
    )
    await waitFor(() => expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(2), {
      timeout: 2000,
    })

    // B (YENİ) ÖNCE başarıyla çözülür.
    callB.resolve(previewResponse({ excluded_count: 33, kept_count: 67 }))
    await waitFor(() =>
      expect(screen.getByTestId('location-preview-summary')).toHaveTextContent('33')
    )

    // A (ESKİ) SONRADAN HATA ile çözülür — taze başarıyı EZMEMELİ / hata
    // banner'ı göstermemeli.
    callA.reject({ status: 502, message: 'Sunucu hatası', detail: 'Sunucu hatası' })
    await sleep(80)
    expect(screen.getByTestId('location-preview-summary')).toHaveTextContent('33')
    expect(screen.queryByText('Sunucu hatası')).not.toBeInTheDocument()
  }, 10000)

  // QA — 500ms delik: sayaç eskiden yalnız runPreview() İÇİNDE artıyordu, bu
  // da B henüz DISPATCH EDİLMEDEN (hâlâ debounce penceresindeyken) A
  // çözülürse A'nın hâlâ "güncel" sayılmasına ve ekrana yazılmasına izin
  // veriyordu. Düzeltme sayacı girdi DEĞİŞİKLİĞİNDE (debounce effect'in en
  // başında) artırır — bu test tam o pencereyi (B zamanlanmış ama HENÜZ
  // başlamamış) fake timer'larla kontrol ederek doğrular.
  it("500ms delik kapalı: B henüz dispatch edilmeden A çözülürse A'nın sonucu YAZILMAZ, yalnız B görünür", async () => {
    vi.useFakeTimers()
    try {
      const callA = deferred<unknown>()
      const callB = deferred<unknown>()
      mockedWorkspace.previewLocationFilter
        .mockImplementationOnce(() => callA.promise as never)
        .mockImplementationOnce(() => callB.promise as never)

      const { rerender } = render(<LocationPolicyControl {...baseProps({ mode: 'exclude_all' })} />)

      // Mount debounce'u geçir — istek A dispatch edilir (ağda asılı kalır).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })
      expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1)

      // Kullanıcı ayarı değiştirir — B için YENİ bir debounce penceresi
      // başlar. Zamanlayıcıyı henüz İLERLETMİYORUZ — B DISPATCH EDİLMEDİ.
      rerender(
        <LocationPolicyControl {...baseProps({ mode: 'exclude_all', exemptTerms: ['örnek'] })} />
      )
      expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1)

      // A, B dispatch edilmeden ÖNCE (hâlâ debounce penceresindeyken) çözülür.
      await act(async () => {
        callA.resolve(previewResponse({ excluded_count: 11, kept_count: 89 }))
        await Promise.resolve()
        await Promise.resolve()
      })

      // B hâlâ dispatch edilmedi; A'nın (artık bayat) sonucu YAZILMAMALI —
      // preview state hiç değişmediği için özet bloğu HİÇ render edilmez
      // (preview===null kaldı). A'nın finally'si de bayat sayıldığı için
      // previewLoading temizlenmedi — "Yenile" hâlâ devre dışı (bekleyen bir
      // yenileme GERÇEKTEN var; QA notundaki kasıtlı yan etki).
      expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(1)
      expect(screen.queryByTestId('location-preview-summary')).toBeNull()
      expect(screen.getByRole('button', { name: /Yenile/i })).toBeDisabled()

      // Debounce süresi dolar — B dispatch edilir.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })
      expect(mockedWorkspace.previewLocationFilter).toHaveBeenCalledTimes(2)

      // B çözülür — yalnız B'nin sonucu görünmeli.
      await act(async () => {
        callB.resolve(previewResponse({ excluded_count: 22, kept_count: 78 }))
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(screen.getByTestId('location-preview-summary')).toHaveTextContent('22')
      expect(screen.getByTestId('location-preview-summary')).not.toHaveTextContent('11')
    } finally {
      vi.useRealTimers()
    }
  }, 10000)
})
