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

async function openWizard(url: string) {
  render(
    <MemoryRouter initialEntries={['/brand-profile']}>
      <BrandProfile />
    </MemoryRouter>
  )
  fireEvent.click((await screen.findAllByRole('button', { name: /Yeni Çalışma Oluştur/ }))[0])
  fireEvent.change(await screen.findByPlaceholderText('https://...'), { target: { value: url } })
}

const clickCreate = () => fireEvent.click(screen.getByRole('button', { name: /Analizi Başlat/ }))
const urlField = () => screen.getByPlaceholderText('https://...') as HTMLInputElement

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
      clickCreate()
      await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
      expect(create.mock.calls[0][0]).toMatchObject({ company_url: url, crawl_scope: 'site' })
      expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
    }
  )

  it('alt sayfa URL için diyalog açılır, oluşturma henüz çağrılmaz', async () => {
    await openWizard('https://example.com/tr/urunler')
    clickCreate()
    expect(await screen.findByRole('dialog', { name: DIALOG_TITLE })).toBeTruthy()
    expect(create).not.toHaveBeenCalled()
  })

  it('"Yalnız bu alt sayfa ile devam" crawl_scope=page ve girilen URL ile oluşturur', async () => {
    await openWizard('https://example.com/tr/urunler')
    clickCreate()
    fireEvent.click(await screen.findByRole('button', { name: 'Yalnız bu alt sayfa ile devam' }))
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      company_url: 'https://example.com/tr/urunler',
      crawl_scope: 'page',
    })
  })

  it('"Markayı ana sayfayla oluştur" URL alanını origin yapar; sonra site olarak, tekrar sormadan oluşturur', async () => {
    await openWizard('https://www.example.com/tr/urunler?x=1')
    clickCreate()
    fireEvent.click(await screen.findByRole('button', { name: 'Markayı ana sayfayla oluştur' }))
    expect(urlField().value).toBe('https://www.example.com')
    expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
    expect(create).not.toHaveBeenCalled()

    // Kullanıcı başka bir alt yola düzenlese bile soru döngüsü olmaz
    fireEvent.change(urlField(), { target: { value: 'https://www.example.com/tr' } })
    clickCreate()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      company_url: 'https://www.example.com/tr',
      crawl_scope: 'site',
    })
    expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
  })

  it('origin ile oluştur: URL alanı origin iken oluşturma crawl_scope=site gönderir', async () => {
    await openWizard('https://www.example.com/tr/urunler')
    clickCreate()
    fireEvent.click(await screen.findByRole('button', { name: 'Markayı ana sayfayla oluştur' }))
    clickCreate()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      company_url: 'https://www.example.com',
      crawl_scope: 'site',
    })
  })

  it('"Vazgeç" diyalogu oluşturmadan kapatır', async () => {
    await openWizard('https://example.com/tr/urunler')
    clickCreate()
    fireEvent.click(await screen.findByRole('button', { name: 'Vazgeç' }))
    expect(screen.queryByText(DIALOG_TITLE)).toBeNull()
    expect(create).not.toHaveBeenCalled()
  })
})
