import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'
import {
  canHaveFocuses,
  focusShortName,
  getWorkspacePhase,
  hasAnalyzedProfile,
  orderWithFocuses,
} from './brandProfileState'
import { useBrandStore } from '../stores/brandStore'

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
  default_geo_target_id: '2276',
  default_language_id: '1001',
  deleted_at: null,
  created_at: '2026-01-01T00:00:00Z',
  run_count: 0,
}

const FOCUS_NAME = 'Marka A — /hizmetler/toprak-analizi-hizmeti'

// Liste sırası bilerek karışık: odak ebeveyninden ÖNCE gelir
const rows = [
  {
    ...base,
    id: 11,
    name: FOCUS_NAME,
    company_url: 'https://brand.com/hizmetler/toprak-analizi-hizmeti',
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

// Alt sayfa devam senaryoları (liste testlerini etkilememek için ayrı kümede)
const pendingUrl = 'https://brand.com/tr/urunler'
const flowRows = [
  // keyword onayı bekleyen marka + bekleyen alt sayfa adresi
  {
    ...base,
    id: 40,
    name: 'Marka K',
    status: 'keywords_review',
    crawl_scope: 'site',
    suggested_keywords: ['kw1'],
    pending_focus_url: pendingUrl,
    pending_focus_error: null,
  },
  { ...created, id: 41, name: 'Marka K — /tr/urunler', parent_brand_profile_id: 40 },
  // onaylı marka; alt sayfa oluşturulamamış
  {
    ...base,
    id: 50,
    name: 'Marka E',
    status: 'confirmed',
    crawl_scope: 'site',
    pending_focus_url: pendingUrl,
    pending_focus_error: 'Aynı adres için aktif bir alt sayfa var.',
  },
  // onaylı marka; onaylanmamış aktif alt sayfa
  { ...base, id: 60, name: 'Marka C', status: 'confirmed', crawl_scope: 'site' },
  {
    ...base,
    id: 61,
    name: 'Marka C — /hizmetler/toprak-analizi-hizmeti',
    status: 'profile_review',
    parent_brand_profile_id: 60,
    crawl_scope: 'page',
  },
  // başarısız: profilsiz (tekrar denenebilir) ve profilli
  {
    ...base,
    id: 70,
    name: 'Basarisiz Marka',
    status: 'failed',
    crawl_scope: 'site',
    profile_data: null,
    profile_approved_at: null,
  },
  { ...base, id: 71, name: 'Profilli Basarisiz', status: 'failed', crawl_scope: 'site' },
]
const allRows: Array<Record<string, unknown> & { id: number }> = [...rows, created, ...flowRows]

const createFocus = vi.fn()
const createWs = vi.fn()
const startCompetitorDiscovery = vi.fn()
const list = vi.fn()
const approveKeywords = vi.fn()
const retryPendingFocus = vi.fn()
const cancelPendingFocus = vi.fn()
const retryAnalysis = vi.fn()
const approveProfile = vi.fn()

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: (...a: unknown[]) => list(...a),
    get: vi.fn((id: number) => Promise.resolve({ data: allRows.find((w) => w.id === id) })),
    create: (...args: unknown[]) => createWs(...args),
    createFocus: (...args: unknown[]) => createFocus(...args),
    update: vi.fn(() => Promise.resolve({ data: {} })),
    anchorsPreview: vi.fn(() => Promise.resolve({ data: { groups: [] } })),
    approveKeywords: (...a: unknown[]) => approveKeywords(...a),
    retryPendingFocus: (...a: unknown[]) => retryPendingFocus(...a),
    cancelPendingFocus: (...a: unknown[]) => cancelPendingFocus(...a),
    retryAnalysis: (...a: unknown[]) => retryAnalysis(...a),
    approveProfile: (...a: unknown[]) => approveProfile(...a),
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

beforeEach(() => {
  list.mockReset()
  list.mockResolvedValue({ data: rows })
  useBrandStore.setState({ activeWorkspace: null })
})

describe('Alt sayfa odakları: liste', () => {
  it('aktif alt sayfalar ayrı kart değil; yetim odak üst seviyede, tek sayfa rozetli', async () => {
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(4))
    const cards = Array.from(container.querySelectorAll('.bpx-card'))
    const names = cards.map((c) => c.querySelector('.bpx-card-name')?.firstChild?.textContent)
    expect(names).toEqual(['Yetim Odak', 'Marka A', 'Tek Sayfa', 'Taslak Marka'])
    expect(cards.some((c) => c.classList.contains('is-focus-nested'))).toBe(false)
    // Yetim odak (ebeveyni listede yok) etiketli; bağımsız tek sayfa "Tek sayfa" rozetli
    expect(cards[0].textContent).toContain('Alt sayfa')
    expect(cards[2].textContent).toContain('Tek sayfa')
    expect(cards[1].textContent).not.toContain('Tek sayfa')
  })

  it('marka kartı "Çalışmaya geç" menüsü: kısa ad, durum etiketi; onaylı -> aktif çalışma', async () => {
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(4))
    const trigger = screen.getByRole('button', { name: /^Çalışmaya geç$/ })
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu')
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    // Alt sayfası olmayan marka eski düz düğmeyi korur
    expect(screen.getAllByRole('button', { name: 'Bu Çalışmaya Geç' }).length).toBeGreaterThan(0)

    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getAllByRole('menuitem').map((el) => el.textContent)).toEqual([
      'Ana marka',
      'Toprak analizi hizmeti',
      'Rakipte OdakProfil Onayı Bekliyor',
    ])

    fireEvent.click(screen.getByRole('menuitem', { name: /Toprak analizi hizmeti/ }))
    expect(screen.queryByRole('menu')).toBeNull()
    expect(useBrandStore.getState().activeWorkspace?.id).toBe(11)
    // Aktif etiket görünür, menü kalır
    expect(screen.getByText('Aktif: Toprak analizi hizmeti')).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Çalışmaya geç$/ })).toBeTruthy()
  })

  it('"Ana marka" markayı aktif yapar; aktif etiket "Ana marka"', async () => {
    renderAt('/brand-profile')
    fireEvent.click(await screen.findByRole('button', { name: /^Çalışmaya geç$/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Ana marka' }))
    expect(useBrandStore.getState().activeWorkspace?.id).toBe(10)
    expect(screen.getByText('Aktif: Ana marka')).toBeTruthy()
  })

  it('onaylanmamış alt sayfa -> o alt sayfanın penceresini açar, aktif yapmaz', async () => {
    renderAt('/brand-profile')
    fireEvent.click(await screen.findByRole('button', { name: /^Çalışmaya geç$/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Rakipte Odak/ }))
    expect(await screen.findByText('Markanızı böyle anladık')).toBeTruthy()
    expect(useBrandStore.getState().activeWorkspace).toBeNull()
  })

  it('menü Escape ve dışarı tıklama ile kapanır', async () => {
    renderAt('/brand-profile')
    const trigger = await screen.findByRole('button', { name: /^Çalışmaya geç$/ })
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(trigger)
    fireEvent.click(trigger)
    expect(screen.getByRole('menu')).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('arama alt sayfa adını/URL’sini tarar: marka kartı + "Eşleşen alt sayfa"', async () => {
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(4))
    fireEvent.change(screen.getByLabelText('Marka çalışması ara'), {
      target: { value: 'toprak-analizi' },
    })
    const cards = Array.from(container.querySelectorAll('.bpx-card'))
    expect(cards).toHaveLength(1)
    expect(cards[0].textContent).toContain('Marka A')
    expect(cards[0].textContent).toContain('Eşleşen alt sayfa: Toprak analizi hizmeti')
  })

  it('arşiv görünümünde arşivli alt sayfa ayrı kart (Geri Getir)', async () => {
    const archivedFocus = {
      ...base,
      id: 16,
      name: 'Marka A — /eski-sayfa',
      status: 'confirmed',
      parent_brand_profile_id: 10,
      crawl_scope: 'page',
      deleted_at: '2026-02-01T00:00:00Z',
    }
    list.mockResolvedValue({ data: [...rows, archivedFocus] })
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(4))
    fireEvent.click(screen.getByLabelText(/Arşivlenmiş Çalışmaları Göster/))
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(5))
    const archivedCard = Array.from(container.querySelectorAll('.bpx-card')).find((c) =>
      c.textContent?.includes('/eski-sayfa')
    ) as HTMLElement
    expect(archivedCard.textContent).toContain('Geri Getir')
    expect(archivedCard.classList.contains('is-focus-nested')).toBe(true)
  })
})

describe('Alt sayfa odakları: "Alt sayfalar" kartı', () => {
  beforeEach(() => {
    createFocus.mockReset()
  })

  it('onaylı markada görünür; odakları listeler', async () => {
    renderAt('/brand-profile?workspace_id=10')
    expect(await screen.findByText('Alt sayfalar')).toBeTruthy()
    expect(addButton()).toBeTruthy()
    expect(
      screen.getByPlaceholderText('https://… (markanın sitesindeki bir alt sayfa)')
    ).toBeTruthy()
    // Modal'ın kendi satırı: ad düğme olarak listelenir
    expect(await screen.findByRole('button', { name: FOCUS_NAME })).toBeTruthy()
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
    list.mockResolvedValue({ data: [rows[2]] })
    renderAt('/brand-profile?workspace_id=10')
    expect(await screen.findByText('Henüz alt sayfa yok.')).toBeTruthy()
  })

  it('ekle: createFocus(parentId, url) çağrılır ve odak açılır (alan boş başlar)', async () => {
    createFocus.mockResolvedValue({ data: created })
    renderAt('/brand-profile?workspace_id=10')
    const input = (await screen.findByPlaceholderText(
      'https://… (markanın sitesindeki bir alt sayfa)'
    )) as HTMLInputElement
    expect(input.value).toBe('')
    fireEvent.change(input, { target: { value: 'https://brand.com/tr/urunler' } })
    fireEvent.click(addButton() as HTMLElement)
    await waitFor(() =>
      expect(createFocus).toHaveBeenCalledWith(10, 'https://brand.com/tr/urunler')
    )
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

describe('Bekleyen alt sayfa: onay, devam şeridi, tekrar deneme', () => {
  const brand40 = flowRows[0]
  beforeEach(() => {
    approveKeywords.mockReset()
    retryPendingFocus.mockReset()
    cancelPendingFocus.mockReset()
    retryAnalysis.mockReset()
    approveProfile.mockReset()
    list.mockResolvedValue({ data: allRows })
  })

  it('pending_focus_url dolu markada adım listesi "Alt sayfa" ile biter ve düğme metni değişir', async () => {
    renderAt('/brand-profile?workspace_id=40')
    const steps = await screen.findByRole('list', { name: 'Akış adımları' })
    expect(Array.from(steps.querySelectorAll('li')).map((li) => li.textContent)).toEqual([
      'Profil',
      'Rakipler',
      'Anahtar Kelimeler',
      'Alt sayfa',
    ])
    expect(
      screen.getByRole('button', { name: 'Markayı onayla ve alt sayfaya devam et' })
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Onayla ve analize geç' })).toBeNull()
  })

  it('pending_focus_url yoksa adım listesi yok, düğme eski metin', async () => {
    const plain = { ...brand40, id: 42, pending_focus_url: null }
    allRows.push(plain)
    renderAt('/brand-profile?workspace_id=42')
    expect(await screen.findByRole('button', { name: 'Onayla ve analize geç' })).toBeTruthy()
    expect(screen.queryByRole('list', { name: 'Akış adımları' })).toBeNull()
    allRows.pop()
  })

  it('onay cevabında focus_handoff_id: sayfadan ayrılmadan alt sayfaya geçer', async () => {
    approveKeywords.mockResolvedValue({
      data: { ...brand40, status: 'confirmed', pending_focus_url: null, focus_handoff_id: 41 },
    })
    renderAt('/brand-profile?workspace_id=40')
    fireEvent.click(
      await screen.findByRole('button', { name: 'Markayı onayla ve alt sayfaya devam et' })
    )
    await waitFor(() => expect(approveKeywords).toHaveBeenCalledWith(40, { keywords: ['kw1'] }))
    // Alt sayfa analizi sürüyor: modal o alt sayfaya geçti
    expect(await screen.findByText('Siteniz inceleniyor…')).toBeTruthy()
    expect(useBrandStore.getState().activeWorkspace).toBeNull()
  })

  it('onay cevabında pending_focus_error: marka penceresinde kalır, hata + tekrar dene şeridi', async () => {
    approveKeywords.mockResolvedValue({
      data: {
        ...brand40,
        status: 'confirmed',
        pending_focus_error: 'Alt sayfa şu an hazırlanamadı.',
      },
    })
    renderAt('/brand-profile?workspace_id=40')
    fireEvent.click(
      await screen.findByRole('button', { name: 'Markayı onayla ve alt sayfaya devam et' })
    )
    expect(
      await screen.findByText('Alt sayfa hazırlanamadı: Alt sayfa şu an hazırlanamadı.')
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Tekrar dene' })).toBeTruthy()
    expect(useBrandStore.getState().activeWorkspace).toBeNull()
  })

  it('hata şeridi sunucu verisinden gelir: Tekrar dene -> alt sayfaya geçer', async () => {
    retryPendingFocus.mockResolvedValue({
      data: { ...flowRows[2], pending_focus_error: null, focus_handoff_id: 41 },
    })
    renderAt('/brand-profile?workspace_id=50')
    expect(
      await screen.findByText('Alt sayfa hazırlanamadı: Aynı adres için aktif bir alt sayfa var.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Tekrar dene' }))
    await waitFor(() => expect(retryPendingFocus).toHaveBeenCalledWith(50))
    expect(await screen.findByText('Siteniz inceleniyor…')).toBeTruthy()
  })

  it('tekrar deneme yine başarısızsa şerit yeni hatayı gösterir', async () => {
    retryPendingFocus.mockResolvedValue({
      data: { ...flowRows[2], pending_focus_error: 'Hâlâ olmadı.' },
    })
    renderAt('/brand-profile?workspace_id=50')
    fireEvent.click(await screen.findByRole('button', { name: 'Tekrar dene' }))
    expect(await screen.findByText('Alt sayfa hazırlanamadı: Hâlâ olmadı.')).toBeTruthy()
  })

  it('Vazgeç: DELETE çağrılır, şerit kalkar', async () => {
    cancelPendingFocus.mockResolvedValue({
      data: { ...flowRows[2], pending_focus_url: null, pending_focus_error: null },
    })
    renderAt('/brand-profile?workspace_id=50')
    fireEvent.click(await screen.findByRole('button', { name: 'Vazgeç' }))
    await waitFor(() => expect(cancelPendingFocus).toHaveBeenCalledWith(50))
    await waitFor(() => expect(screen.queryByText(/Alt sayfa hazırlanamadı/)).toBeNull())
  })

  it('onaylanmamış aktif alt sayfa: "Alt sayfa: <kısa ad> — <durum> · Devam et" şeridi o alt sayfayı açar', async () => {
    renderAt('/brand-profile?workspace_id=60')
    expect(
      await screen.findByText('Alt sayfa: Toprak analizi hizmeti — Profil Onayı Bekliyor')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Devam et' }))
    expect(await screen.findByText('Markanızı böyle anladık')).toBeTruthy()
  })

  it('şerit yalnız onaylanmamış alt sayfalar içindir (onaylı alt sayfa için şerit yok)', async () => {
    renderAt('/brand-profile?workspace_id=10')
    await screen.findByText('Alt sayfalar')
    // 15 (profil onayı bekliyor) ve 20 (bekliyor) için şerit; onaylı 11 için yok
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Devam et' })).toHaveLength(2))
    expect(screen.queryByText(/^Alt sayfa: Toprak analizi hizmeti/)).toBeNull()
  })

  it('başarısız + profilsiz çalışma: "Analizi tekrar dene" -> pending, yükleme', async () => {
    retryAnalysis.mockResolvedValue({
      data: { ...flowRows[5], status: 'pending' },
    })
    renderAt('/brand-profile?workspace_id=70')
    expect(await screen.findByText('Analiz başarısız oldu.')).toBeTruthy()
    expect(screen.queryByText(/Yeni bir çalışma oluşturmayı/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Analizi tekrar dene' }))
    await waitFor(() => expect(retryAnalysis).toHaveBeenCalledWith(70))
    expect(await screen.findByText('Siteniz inceleniyor…')).toBeTruthy()
  })

  it('başarısız ama profilli çalışma: yalnız keyword üretimi tekrar denenir', async () => {
    approveProfile.mockResolvedValue({ data: { ...flowRows[6], status: 'running' } })
    renderAt('/brand-profile?workspace_id=71')
    expect(await screen.findByText(/Anahtar kelime üretimi tamamlanamadı/)).toBeTruthy()
    expect(screen.queryByText(/Yeni bir çalışma oluşturmayı/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Analizi tekrar dene' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Anahtar kelime üretimini tekrar dene' }))
    await waitFor(() => expect(approveProfile).toHaveBeenCalledWith(71, { rerun_keywords: true }))
    expect(retryAnalysis).not.toHaveBeenCalled()
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

  it('hasAnalyzedProfile: miras konum alanları profil sayılmaz (başarısız odakta tekrar dene)', () => {
    expect(hasAnalyzedProfile(null)).toBe(false)
    expect(hasAnalyzedProfile({ location_filter_mode: 'none', focus_cities: [] })).toBe(false)
    expect(hasAnalyzedProfile({ location_filter_mode: 'none', services: ['x'] })).toBe(true)
  })

  it('focusShortName: " — " sonrası; yol/slug son parçası insanlaştırılır', () => {
    expect(focusShortName('Marka A — /hizmetler/toprak-analizi-hizmeti')).toBe(
      'Toprak analizi hizmeti'
    )
    expect(focusShortName('Marka A — toprak-analizi-hizmeti')).toBe('Toprak analizi hizmeti')
    expect(focusShortName('Marka A — https://brand.com/tr/izmir-hizmeti/?x=1')).toBe(
      'İzmir hizmeti'
    )
    expect(focusShortName('Marka A — Toprak Analizi')).toBe('Toprak Analizi')
    expect(focusShortName('Yetim Odak')).toBe('Yetim Odak')
  })
})
