import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MarketCopyDialog, { MAX_MARKET_COPIES } from './MarketCopyDialog'
import { useBrandStore } from '../stores/brandStore'

const marketOptions = vi.fn()
const searchLocations = vi.fn()
const addMarkets = vi.fn()

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    workspaceApi: {
      marketOptions: () => marketOptions(),
      searchLocations: (...args: unknown[]) => searchLocations(...args),
      addMarkets: (...args: unknown[]) => addMarkets(...args),
    },
  }
})

const OPTIONS = {
  countries: [{ geo_target_id: '2276', code: 'DE', name_tr: 'Almanya', label: 'Almanya (DE)' }],
  languages: [
    { language_id: '1031', code: 'ru', name_tr: 'Rusça', name_native: 'Русский' },
    { language_id: '1001', code: 'de', name_tr: 'Almanca', name_native: 'Deutsch' },
  ],
  default: { geo_target_id: '2276', language_id: '1031' },
}

const loc = (id: string, label: string, kind: 'country' | 'city' = 'country') => ({
  geo_target_id: id,
  label,
  kind,
  country_code: 'DE',
  name: label,
})
const GERMANY = loc('2276', 'Almanya (DE)')
const BERLIN = loc('1003854', 'Berlin, Almanya', 'city')

const wsRow = (id: number, name: string) => ({ id, name, status: 'confirmed' })

let results: ReturnType<typeof loc>[] = []

const onClose = vi.fn()
const onCreated = vi.fn()
const onOpenFirst = vi.fn()

function renderDialog() {
  return render(
    <MarketCopyDialog
      source={{ id: 7, name: 'Klinik X' }}
      onClose={onClose}
      onCreated={onCreated}
      onOpenFirst={onOpenFirst}
    />
  )
}

// Aramadan bir konum seç → listeye çip olarak eklenir
async function addLocation(label: string, typed = 'x') {
  const input = screen.getByPlaceholderText('Ülke veya şehir ara…')
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: typed } })
  fireEvent.click(await screen.findByRole('option', { name: label }))
}

const rowsList = () => screen.getByRole('list', { name: 'Seçilen pazarlar' })

describe('MarketCopyDialog', () => {
  beforeEach(() => {
    useBrandStore.setState({ marketOptions: null, marketOptionsError: null })
    marketOptions.mockReset().mockResolvedValue({ data: OPTIONS })
    results = [GERMANY, BERLIN]
    searchLocations
      .mockReset()
      .mockImplementation(() =>
        Promise.resolve({ data: { items: results, total: results.length } })
      )
    addMarkets.mockReset()
    onClose.mockReset()
    onCreated.mockReset()
    onOpenFirst.mockReset()
  })
  afterEach(cleanup)

  it('konum ekler/kaldırır ve özeti yazar', async () => {
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    expect(screen.getByRole('button', { name: 'Çalışma oluştur' })).toBeDisabled()

    await addLocation('Almanya (DE)')
    await addLocation('Berlin, Almanya')
    expect(within(rowsList()).getAllByRole('listitem')).toHaveLength(2)

    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    expect(
      screen.getByText('Almanya (DE) · Rusça, Berlin, Almanya · Rusça', { exact: false })
    ).toBeInTheDocument()
    expect(screen.getByText('2 çalışma oluşturulacak:')).toBeInTheDocument()
    expect(screen.getByText(/kelimeler ve analizler kopyalanmaz/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Berlin, Almanya kaldır' }))
    expect(within(rowsList()).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('1 çalışma oluşturulacak:')).toBeInTheDocument()
  })

  it(`en fazla ${MAX_MARKET_COPIES} konum kabul eder`, async () => {
    results = Array.from({ length: MAX_MARKET_COPIES + 1 }, (_, i) =>
      loc(String(5000 + i), `Şehir ${i}`, 'city')
    )
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    const input = screen.getByPlaceholderText('Ülke veya şehir ara…')
    // Arama gecikmesi (300 ms) 30 tur için sahte zamanlayıcıyla atlanır
    vi.useFakeTimers()
    try {
      for (let i = 0; i < MAX_MARKET_COPIES; i++) {
        fireEvent.focus(input)
        fireEvent.change(input, { target: { value: 's' } })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(400)
        })
        fireEvent.click(screen.getByRole('option', { name: `Şehir ${i}` }))
      }
    } finally {
      vi.useRealTimers()
    }
    expect(within(rowsList()).getAllByRole('listitem')).toHaveLength(MAX_MARKET_COPIES)
    expect(screen.getByText(`En fazla ${MAX_MARKET_COPIES} pazar eklenebilir.`)).toBeInTheDocument()
    expect(input).toBeDisabled()
  }, 15_000) // 30 tur render; yük altında (paralel pytest) 5 sn sınırını aşıyordu

  it('aynı konum iki kez, farklı dillerle: iki ayrı pazar olarak gönderilir', async () => {
    addMarkets.mockResolvedValue({ data: { created: [], skipped: [], source_id: 7 } })
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    await addLocation('Almanya (DE)')
    expect(within(rowsList()).getAllByRole('listitem')).toHaveLength(2)
    expect(
      screen.getByText('Aynı konum ikinci kez eklendi — satır dillerini farklı seçin.')
    ).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    const [, second] = screen.getAllByLabelText('Almanya (DE) için dil')
    fireEvent.change(second, { target: { value: '1001' } })
    expect(
      screen.getByText('Almanya (DE) · Rusça, Almanya (DE) · Almanca', { exact: false })
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2 çalışma oluştur' }))

    await waitFor(() => expect(addMarkets).toHaveBeenCalledTimes(1))
    expect(addMarkets).toHaveBeenCalledWith(7, [
      { geo_target_id: '2276', language_id: '1031' },
      { geo_target_id: '2276', language_id: '1001' },
    ])
  })

  it('aynı konum + aynı etkin dil: gönderim engellenir, satır dili değişince açılır', async () => {
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    await addLocation('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Aynı pazar iki kez listede: Almanya (DE) · Rusça'
    )
    const items = within(rowsList()).getAllByRole('listitem')
    expect(items.every((li) => li.classList.contains('is-dup'))).toBe(true)
    expect(screen.getByRole('button', { name: '2 çalışma oluştur' })).toBeDisabled()

    // Satır geçersiz kılması da aynı çifti üretirse yine engellenir
    const [, second] = screen.getAllByLabelText('Almanya (DE) için dil')
    fireEvent.change(second, { target: { value: '1031' } })
    expect(screen.getByRole('alert')).toBeInTheDocument()

    fireEvent.change(second, { target: { value: '1001' } })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(items.some((li) => li.classList.contains('is-dup'))).toBe(false)
    expect(screen.getByRole('button', { name: '2 çalışma oluştur' })).toBeEnabled()
  })

  it('genel dil + satır başına dil geçersiz kılma: istek gövdesi doğru biçimde', async () => {
    addMarkets.mockResolvedValue({
      data: { created: [wsRow(31, 'Klinik X — Almanya (DE)/Rusça')], skipped: [], source_id: 7 },
    })
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    await addLocation('Berlin, Almanya')

    // Dil seçilmeden gönderilemez
    expect(screen.getByRole('button', { name: '2 çalışma oluştur' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    fireEvent.change(screen.getByLabelText('Berlin, Almanya için dil'), {
      target: { value: '1001' },
    })
    fireEvent.click(screen.getByRole('button', { name: '2 çalışma oluştur' }))

    await waitFor(() => expect(addMarkets).toHaveBeenCalledTimes(1))
    expect(addMarkets).toHaveBeenCalledWith(7, [
      { geo_target_id: '2276', language_id: '1031' },
      { geo_target_id: '1003854', language_id: '1001' },
    ])
  })

  it('3 oluşturuldu, 1 atlandı: hata değil, sonuç paneli; ilk çalışmaya geç', async () => {
    const created = [wsRow(31, 'A/Rusça'), wsRow(32, 'B/Rusça'), wsRow(33, 'C/Rusça')]
    addMarkets.mockResolvedValue({
      data: {
        created,
        skipped: [
          {
            geo_target_id: '2276',
            language_id: '1031',
            market_label: 'Almanya (DE) · Rusça',
            reason: 'Bu pazar için çalışma zaten var.',
          },
        ],
        source_id: 7,
      },
    })
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    fireEvent.click(screen.getByRole('button', { name: '1 çalışma oluştur' }))

    expect(await screen.findByText('3 oluşturuldu, 1 atlandı')).toBeInTheDocument()
    const skipped = screen.getByRole('list', { name: 'Atlananlar' })
    expect(skipped).toHaveTextContent('Almanya (DE) · Rusça')
    expect(skipped).toHaveTextContent('Bu pazar için çalışma zaten var.')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onCreated).toHaveBeenCalledWith(created)

    fireEvent.click(screen.getByRole('button', { name: 'İlk çalışmaya geç' }))
    expect(onOpenFirst).toHaveBeenCalledWith(created[0])
  })

  it('409 MARKET_COPY_ALL_EXIST bilgi durumudur (kırmızı hata değil, geçiş düğmesi yok)', async () => {
    addMarkets.mockRejectedValue({
      status: 409,
      message: 'Seçilen pazarların hepsi mevcut.',
      detail: {
        code: 'MARKET_COPY_ALL_EXIST',
        message: 'Seçilen pazarların hepsi mevcut.',
        skipped: [
          {
            geo_target_id: '2276',
            language_id: '1031',
            market_label: 'Almanya (DE) · Rusça',
            reason: 'Bu pazar için çalışma zaten var.',
          },
        ],
      },
    })
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    fireEvent.click(screen.getByRole('button', { name: '1 çalışma oluştur' }))

    expect(await screen.findByText(/hepsi zaten var/)).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Atlananlar' })).toHaveTextContent('Almanya (DE)')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: 'İlk çalışmaya geç' })).toBeNull()
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('diğer hatalarda mesajı gösterir ve formu korur', async () => {
    addMarkets.mockRejectedValue({
      status: 409,
      message: 'Profil analizi sürüyor.',
      detail: { code: 'ANALYSIS_RUNNING', message: 'Profil analizi sürüyor.' },
    })
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    fireEvent.click(screen.getByRole('button', { name: '1 çalışma oluştur' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Profil analizi sürüyor.')
    expect(within(rowsList()).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '1 çalışma oluştur' })).toBeEnabled()
  })

  it('gönderirken düğmeler pasif (yükleniyor durumu)', async () => {
    addMarkets.mockReturnValue(new Promise(() => {}))
    renderDialog()
    await screen.findByRole('option', { name: 'Rusça' })
    await addLocation('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    fireEvent.click(screen.getByRole('button', { name: '1 çalışma oluştur' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Vazgeç' })).toBeDisabled())
    expect(screen.getByRole('button', { name: '1 çalışma oluştur' })).toBeDisabled()
  })
})
