import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'

const create = vi.fn()

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: vi.fn(() => Promise.resolve({ data: [] })),
    get: vi.fn(() => Promise.resolve({ data: {} })),
    create: (...args: unknown[]) => create(...args),
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
}

const startButton = () =>
  screen.getByRole('button', { name: /Analizi Başlat/ }) as HTMLButtonElement
const clickCreate = () => fireEvent.click(startButton())
const urlField = () => screen.getByPlaceholderText('https://...') as HTMLInputElement
const option = (name: RegExp) => screen.getByRole('radio', { name })

describe('Çalışma oluşturma: alt sayfa kapsam sorusu', () => {
  beforeEach(() => {
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

  it('"Vazgeç" soruyu kapatır, oluşturmaz; "Analizi Başlat" soruyu yeniden açar', async () => {
    await openWizard(SUBPAGE)
    fireEvent.click(screen.getByRole('button', { name: 'Vazgeç' }))
    expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
    expect(create).not.toHaveBeenCalled()
    clickCreate()
    expect(await screen.findByRole('dialog', { name: DIALOG_TITLE })).toBeTruthy()
    expect(create).not.toHaveBeenCalled()
  })
})
