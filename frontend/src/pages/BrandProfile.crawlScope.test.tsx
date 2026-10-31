import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'
import { useBrandStore } from '../stores/brandStore'

const create = vi.fn()

const MARKET_OPTIONS = {
  countries: [{ geo_target_id: '2276', code: 'DE', name_tr: 'Almanya', label: 'Almanya (DE)' }],
  languages: [
    { language_id: '1031', code: 'ru', name_tr: 'Rusça', name_native: 'Русский' },
    { language_id: '1001', code: 'de', name_tr: 'Almanca', name_native: 'Deutsch' },
  ],
  default: { geo_target_id: '2276', language_id: '1031' },
}

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: vi.fn(() => Promise.resolve({ data: [] })),
    get: vi.fn(() => Promise.resolve({ data: {} })),
    create: (...args: unknown[]) => create(...args),
    marketOptions: vi.fn(() => Promise.resolve({ data: MARKET_OPTIONS })),
    searchLocations: vi.fn(() => Promise.resolve({ data: { items: [], total: 0 } })),
  },
  brandProfileApi: {},
  apiErrorMessage: (_: unknown, fallback = 'Hata') => fallback,
}))

const DIALOG_TITLE = 'Bu adres bir alt sayfa'
const SUBPAGE = 'https://www.example.com/tr/urunler?x=1'

async function openWizard(url: string) {
  render(
    <MemoryRouter initialEntries={['/brand-profile']}>
      <BrandProfile />
    </MemoryRouter>
  )
  fireEvent.click((await screen.findAllByRole('button', { name: /Yeni Çalışma Oluştur/ }))[0])
  fireEvent.change(await screen.findByPlaceholderText('https://...'), { target: { value: url } })
  // Pazar varsayılanı yüklenmeden "Analizi Başlat" pasiftir
  await screen.findByText('Almanya (DE)')
}

const startButton = () =>
  screen.getByRole('button', { name: /Analizi Başlat/ }) as HTMLButtonElement
const clickCreate = () => fireEvent.click(startButton())
const urlField = () => screen.getByPlaceholderText('https://...') as HTMLInputElement
const option = (name: RegExp) => screen.getByRole('radio', { name })

describe('Çalışma oluşturma: alt sayfa kapsam sorusu', () => {
  beforeEach(() => {
    useBrandStore.setState({ marketOptions: null, marketOptionsError: null })
    create.mockReset()
    // Hiç ilerlemeyen istek: yalnız çağrı argümanları doğrulanır
    create.mockReturnValue(new Promise(() => {}))
  })

  it.each(['example.com', 'https://example.com/'])(
    'kök URL (%s) sormadan crawl_scope=site ile oluşturur',
    async (url) => {
      await openWizard(url)
      expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
      expect(startButton().disabled).toBe(false)
      clickCreate()
      await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
      expect(create.mock.calls[0][0]).toMatchObject({ company_url: url, crawl_scope: 'site' })
      expect(create.mock.calls[0][0]).not.toHaveProperty('pending_focus_url')
    }
  )

  it('pazar varsayılanı market-options.default değerinden gelir ve create payload içine girer', async () => {
    await openWizard('example.com')
    expect((screen.getByLabelText('Çıktı dili') as HTMLSelectElement).value).toBe('1031')
    clickCreate()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      default_geo_target_id: '2276',
      default_language_id: '1031',
    })
  })

  it('pazar seçenekleri yüklenemezse "Analizi Başlat" pasif kalır', async () => {
    const { workspaceApi } = await import('../services/api')
    vi.mocked(workspaceApi.marketOptions).mockRejectedValueOnce({ message: 'Sunucu hatası' })
    render(
      <MemoryRouter initialEntries={['/brand-profile']}>
        <BrandProfile />
      </MemoryRouter>
    )
    fireEvent.click((await screen.findAllByRole('button', { name: /Yeni Çalışma Oluştur/ }))[0])
    fireEvent.change(await screen.findByPlaceholderText('https://...'), {
      target: { value: 'example.com' },
    })
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(startButton().disabled).toBe(true)
  })

  it('alt sayfa URL için soru açılır; seçim yokken "Analizi Başlat" pasif ve istek yok', async () => {
    await openWizard(SUBPAGE)
    expect(screen.getByRole('dialog', { name: DIALOG_TITLE })).toBeTruthy()
    expect(screen.getByRole('radiogroup')).toBeTruthy()
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(startButton().disabled).toBe(true)
    clickCreate()
    expect(create).not.toHaveBeenCalled()
  })

  it('seçenek seçmek başlatmaz; seçince "Analizi Başlat" aktif olur', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(option(/Yalnız ana marka/))
    expect(create).not.toHaveBeenCalled()
    expect(startButton().disabled).toBe(false)
  })

  it('(1) Ana marka + bu alt sayfa: origin + site + pending_focus_url, tek istek', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(option(/Ana marka \+ bu alt sayfa/))
    clickCreate()
    clickCreate()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      company_url: 'https://www.example.com',
      crawl_scope: 'site',
      flow_version: 'profile_first',
      pending_focus_url: SUBPAGE,
    })
  })

  it('(2) Yalnız ana marka: origin + site, pending_focus_url yok', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(option(/Yalnız ana marka/))
    clickCreate()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      company_url: 'https://www.example.com',
      crawl_scope: 'site',
    })
    expect(create.mock.calls[0][0]).not.toHaveProperty('pending_focus_url')
  })

  it('(3) Yalnız bu alt sayfa: girilen URL + crawl_scope=page', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(option(/Yalnız bu alt sayfa/))
    clickCreate()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({ company_url: SUBPAGE, crawl_scope: 'page' })
    expect(create.mock.calls[0][0]).not.toHaveProperty('pending_focus_url')
  })

  it('adres değişince seçim sıfırlanır ve soru yeniden sorulur; kök adreste soru kapanır', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(option(/Yalnız ana marka/))
    expect(startButton().disabled).toBe(false)

    fireEvent.change(urlField(), { target: { value: 'https://www.example.com/tr' } })
    expect((option(/Yalnız ana marka/) as HTMLInputElement).checked).toBe(false)
    expect(startButton().disabled).toBe(true)

    fireEvent.change(urlField(), { target: { value: 'https://www.example.com/' } })
    expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
    expect(startButton().disabled).toBe(false)
  })

  it('"Vazgeç" adresi temizler, oluşturmaz; seçimsiz "Analizi Başlat" pasif kalır', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(screen.getByRole('button', { name: 'Vazgeç' }))
    expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
    expect(urlField().value).toBe('')
    expect(startButton().disabled).toBe(true)
    expect(create).not.toHaveBeenCalled()
  })
})
