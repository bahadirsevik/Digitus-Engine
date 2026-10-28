import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'
import {
  canHaveFocuses,
  getWorkspacePhase,
  orderWithFocuses,
  readPendingFocusUrl,
  rememberPendingFocusUrl,
} from './brandProfileState'

const base = {
  company_url: 'https://brand.com',
  onboarding_flow: 'profile_first' as const,
  profile_approved_at: '2026-01-01T00:00:00Z',
  profile_data: {
    company_name: 'X',
    sector: 'E-Ticaret',
    products: ['Ürün A'],
    anchor_texts: ['anchor1'],
  },
  suggested_keywords: null,
  preliminary_info: null,
  excluded_info: null,
  default_geo_target_id: '2792',
  default_language_id: '1037',
  deleted_at: null,
  created_at: '2026-01-01T00:00:00Z',
  run_count: 0,
}

// Liste sırası bilerek karışık: odak ebeveyninden ÖNCE gelir
const rows = [
  {
    ...base,
    id: 11,
    name: 'Alt Odak',
    status: 'confirmed',
    parent_brand_profile_id: 10,
    crawl_scope: 'page',
  },
  {
    ...base,
    id: 12,
    name: 'Yetim Odak',
    status: 'confirmed',
    parent_brand_profile_id: 99,
    crawl_scope: 'page',
  },
  { ...base, id: 10, name: 'Marka A', status: 'confirmed', crawl_scope: 'site' },
  { ...base, id: 13, name: 'Tek Sayfa', status: 'confirmed', crawl_scope: 'page' },
  { ...base, id: 14, name: 'Taslak Marka', status: 'profile_review', crawl_scope: 'site' },
  {
    ...base,
    id: 15,
    name: 'Rakipte Odak',
    status: 'competitor_review',
    parent_brand_profile_id: 10,
    crawl_scope: 'page',
  },
]
const created = {
  ...base,
  id: 20,
  name: 'Yeni Odak',
  status: 'pending',
  parent_brand_profile_id: 10,
  crawl_scope: 'page',
  profile_approved_at: null,
  profile_data: null,
}

const createFocus = vi.fn()
const createWs = vi.fn()
const startCompetitorDiscovery = vi.fn()

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: vi.fn(() => Promise.resolve({ data: rows })),
    get: vi.fn((id: number) =>
      Promise.resolve({ data: [...rows, created].find((w) => w.id === id) })
    ),
    create: (...args: unknown[]) => createWs(...args),
    createFocus: (...args: unknown[]) => createFocus(...args),
    update: vi.fn(() => Promise.resolve({ data: {} })),
  },
  brandProfileApi: {
    policyReview: vi.fn(() => Promise.resolve({ data: {} })),
    startCompetitorPreview: vi.fn(() => Promise.resolve({ data: { task_id: 't1' } })),
    getCompetitorPreview: vi.fn(() =>
      Promise.resolve({ data: { status: 'completed', result: [] } })
    ),
    startCompetitorDiscovery: (...args: unknown[]) => startCompetitorDiscovery(...args),
  },
  apiErrorMessage: (e: unknown, fallback = 'Hata') =>
    (e as { message?: string })?.message ?? fallback,
}))

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <BrandProfile />
    </MemoryRouter>
  )

const addButton = () => screen.queryByRole('button', { name: /Alt sayfa ekle/ })

describe('Alt sayfa odakları: liste', () => {
  it('odak ebeveyninin ardından girintili + etiketli; yetim odak üst seviyede', async () => {
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(rows.length))
    const cards = Array.from(container.querySelectorAll('.bpx-card'))
    const names = cards.map((c) => c.querySelector('.bpx-card-name')?.firstChild?.textContent)
    expect(names).toEqual([
      'Yetim Odak',
      'Marka A',
      'Alt Odak',
      'Rakipte Odak',
      'Tek Sayfa',
      'Taslak Marka',
    ])
    const nested = cards.map((c) => c.classList.contains('is-focus-nested'))
    expect(nested).toEqual([false, false, true, true, false, false])
    // Etiket her odakta (yetim dahil), markada yok
    expect(cards[0].textContent).toContain('Alt sayfa')
    expect(cards[1].textContent).not.toMatch(/Alt sayfa(?!lar)/)
  })
})

describe('Alt sayfa odakları: "Alt sayfalar" kartı', () => {
  beforeEach(() => {
    createFocus.mockReset()
    window.localStorage.clear()
  })
  afterEach(() => vi.restoreAllMocks())

  it('onaylı markada görünür; odakları listeler', async () => {
    renderAt('/brand-profile?workspace_id=10')
    expect(await screen.findByText('Alt sayfalar')).toBeTruthy()
    expect(addButton()).toBeTruthy()
    expect(
      screen.getByPlaceholderText('https://… (markanın sitesindeki bir alt sayfa)')
    ).toBeTruthy()
    // Modal'ın kendi satırı: ad düğme olarak listelenir
    expect(await screen.findByRole('button', { name: 'Alt Odak' })).toBeTruthy()
  })

  it.each([
    ['odak', 11],
    ['bağımsız tek sayfa çalışması', 13],
    ['onaysız marka', 14],
  ])('%s için görünmez', async (_label, id) => {
    renderAt(`/brand-profile?workspace_id=${id}`)
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /Kapat/ }).length).toBeGreaterThan(0)
    )
    expect(screen.queryByText('Alt sayfalar')).toBeNull()
    expect(addButton()).toBeNull()
  })

  it('boş durumda "Henüz alt sayfa yok."', async () => {
    const api = await import('../services/api')
    vi.mocked(api.workspaceApi.list).mockResolvedValueOnce({ data: [rows[2]] } as never)
    renderAt('/brand-profile?workspace_id=10')
    expect(await screen.findByText('Henüz alt sayfa yok.')).toBeTruthy()
  })

  it('ekle: createFocus(parentId, url) çağrılır, saklanan öneri temizlenir, odak açılır', async () => {
    createFocus.mockResolvedValue({ data: created })
    rememberPendingFocusUrl(10, 'https://brand.com/tr/urunler')
    renderAt('/brand-profile?workspace_id=10')
    const input = (await screen.findByPlaceholderText(
      'https://… (markanın sitesindeki bir alt sayfa)'
    )) as HTMLInputElement
    expect(input.value).toBe('https://brand.com/tr/urunler')
    fireEvent.click(addButton() as HTMLElement)
    await waitFor(() =>
      expect(createFocus).toHaveBeenCalledWith(10, 'https://brand.com/tr/urunler')
    )
    await waitFor(() => expect(readPendingFocusUrl(10)).toBe(''))
    // Yeni odak modalda açıldı (analiz yükleme adımı)
    expect(await screen.findByText('Siteniz inceleniyor…')).toBeTruthy()
  })

  it('hata: sunucu detayı gösterilir', async () => {
    createFocus.mockRejectedValue({ message: 'Aynı URL için aktif bir alt sayfa var.' })
    renderAt('/brand-profile?workspace_id=10')
    const input = await screen.findByPlaceholderText(
      'https://… (markanın sitesindeki bir alt sayfa)'
    )
    fireEvent.change(input, { target: { value: 'https://brand.com/a' } })
    fireEvent.click(addButton() as HTMLElement)
    expect(await screen.findByText('Aynı URL için aktif bir alt sayfa var.')).toBeTruthy()
  })

  it('depolama atsa bile çalışır (öneri boş)', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    renderAt('/brand-profile?workspace_id=10')
    const input = (await screen.findByPlaceholderText(
      'https://… (markanın sitesindeki bir alt sayfa)'
    )) as HTMLInputElement
    expect(input.value).toBe('')
    expect(() => rememberPendingFocusUrl(10, 'x')).not.toThrow()
  })

  it('Faz 2 "ana sayfayla oluştur" seçimi özgün alt sayfa URL’sini yeni marka id’siyle saklar', async () => {
    createWs.mockResolvedValue({ data: { ...created, id: 77, parent_brand_profile_id: null } })
    renderAt('/brand-profile')
    fireEvent.click((await screen.findAllByRole('button', { name: /Yeni Çalışma Oluştur/ }))[0])
    fireEvent.change(await screen.findByPlaceholderText('https://...'), {
      target: { value: 'https://brand.com/tr/urunler' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Analizi Başlat/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Markayı ana sayfayla oluştur' }))
    fireEvent.click(screen.getByRole('button', { name: /Analizi Başlat/ }))
    await waitFor(() => expect(createWs).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(readPendingFocusUrl(77)).toBe('https://brand.com/tr/urunler'))
  })
})

describe('Alt sayfa odakları: odak görünümü ve adımlar', () => {
  beforeEach(() => startCompetitorDiscovery.mockReset())

  it('odakta "Marka: <ad>" bağlantısı ve etiket var; bağlantı markayı açar', async () => {
    renderAt('/brand-profile?workspace_id=11')
    const link = await screen.findByRole('button', { name: 'Marka: Marka A' })
    fireEvent.click(link)
    expect(await screen.findByText('Alt sayfalar')).toBeTruthy()
  })

  it('competitor_review durumundaki odak rakip keşfi teklif etmez', async () => {
    renderAt('/brand-profile?workspace_id=15')
    expect(await screen.findByText('Markanızı böyle anladık')).toBeTruthy()
    expect(screen.queryByText('Rakiplerinizi bulalım mı?')).toBeNull()
    expect(startCompetitorDiscovery).not.toHaveBeenCalled()
  })
})

describe('Alt sayfa odakları: durum yardımcıları', () => {
  const pf = {
    deleted_at: null,
    profile_data: null,
    suggested_keywords: null,
    onboarding_flow: 'profile_first' as const,
  }

  it('odak hiçbir durumda competitor_review fazı üretmez; marka üretir', () => {
    expect(getWorkspacePhase({ ...pf, status: 'competitor_review' })).toBe('competitor_review')
    expect(
      getWorkspacePhase({ ...pf, status: 'competitor_review', parent_brand_profile_id: 3 })
    ).toBe('profile_cards_review')
    for (const status of ['pending', 'running', 'profile_review', 'keywords_review', 'confirmed']) {
      expect(getWorkspacePhase({ ...pf, status, parent_brand_profile_id: 3 })).not.toBe(
        'competitor_review'
      )
    }
  })

  it('canHaveFocuses: yalnız onaylı marka (eksik crawl_scope = site)', () => {
    expect(canHaveFocuses({ status: 'confirmed' })).toBe(true)
    expect(canHaveFocuses({ status: 'confirmed', crawl_scope: 'site' })).toBe(true)
    expect(canHaveFocuses({ status: 'confirmed', crawl_scope: 'page' })).toBe(false)
    expect(canHaveFocuses({ status: 'confirmed', parent_brand_profile_id: 1 })).toBe(false)
    expect(canHaveFocuses({ status: 'profile_review' })).toBe(false)
  })

  it('orderWithFocuses: odak ebeveynin ardına, yetim üstte kalır', () => {
    const ordered = orderWithFocuses([
      { id: 2, parent_brand_profile_id: 1 },
      { id: 3, parent_brand_profile_id: 99 },
      { id: 1 },
    ])
    expect(ordered.map((r) => r.id)).toEqual([3, 1, 2])
  })
})
