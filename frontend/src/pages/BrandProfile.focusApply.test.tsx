import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'
import { useBrandStore } from '../stores/brandStore'

const base = {
  company_url: 'https://brand.com',
  onboarding_flow: 'profile_first' as const,
  profile_approved_at: '2026-01-01T00:00:00Z',
  profile_data: { company_name: 'X', sector: 'E-Ticaret', products: ['A'], anchor_texts: ['a'] },
  suggested_keywords: null,
  preliminary_info: null,
  excluded_info: null,
  default_geo_target_id: '2792',
  default_language_id: '1037',
  deleted_at: null,
  created_at: '2026-01-01T00:00:00Z',
  run_count: 0,
  status: 'confirmed',
}

const rows = [
  { ...base, id: 10, name: 'Marka A', crawl_scope: 'site' },
  { ...base, id: 11, name: 'Odak Bir', parent_brand_profile_id: 10, crawl_scope: 'page' },
  { ...base, id: 12, name: 'Odak Iki', parent_brand_profile_id: 10, crawl_scope: 'page' },
  { ...base, id: 13, name: 'Odak Uc', parent_brand_profile_id: 10, crawl_scope: 'page' },
  { ...base, id: 20, name: 'Marka B', crawl_scope: 'site' },
  {
    ...base,
    id: 31,
    name: 'Arsiv Odak',
    parent_brand_profile_id: 30,
    crawl_scope: 'page',
    deleted_at: '2026-02-01T00:00:00Z',
    archived_with_parent: false,
  },
]

const list = vi.fn()
const archive = vi.fn()
const restore = vi.fn()
const previewFocusSettings = vi.fn()
const applyFocusSettings = vi.fn()

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: (...a: unknown[]) => list(...a),
    get: vi.fn((id: number) => Promise.resolve({ data: rows.find((w) => w.id === id) })),
    archive: (...a: unknown[]) => archive(...a),
    restore: (...a: unknown[]) => restore(...a),
    previewFocusSettings: (...a: unknown[]) => previewFocusSettings(...a),
    applyFocusSettings: (...a: unknown[]) => applyFocusSettings(...a),
  },
  brandProfileApi: {},
  apiErrorMessage: (e: unknown, fallback = 'Hata') =>
    (e as { message?: string })?.message ?? fallback,
}))

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <BrandProfile />
    </MemoryRouter>
  )

const applyBtn = () =>
  screen.queryByRole('button', { name: 'Marka ayarlarını alt sayfalara uygula' })

beforeEach(() => {
  list.mockReset()
  list.mockResolvedValue({ data: rows })
  archive.mockReset()
  archive.mockResolvedValue({ data: {} })
  restore.mockReset()
  previewFocusSettings.mockReset()
  applyFocusSettings.mockReset()
  useBrandStore.setState({ activeWorkspace: null })
})
afterEach(() => vi.restoreAllMocks())

describe('Marka arşivleme: alt sayfa sayısı', () => {
  const brandCard = async (container: HTMLElement, name: string) => {
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBeGreaterThan(0))
    return Array.from(container.querySelectorAll<HTMLElement>('.bpx-card')).find(
      (c) => c.querySelector('.bpx-card-name')?.firstChild?.textContent === name
    ) as HTMLElement
  }

  it('onay metni alt sayfa sayısını söyler; onayda arşivler ve listeyi yeniler', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { container } = renderAt('/brand-profile')
    const card = await brandCard(container, 'Marka A')
    const before = list.mock.calls.length
    fireEvent.click(within(card).getByRole('button', { name: 'Arşivle' }))
    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining('Bu markanın 3 alt sayfası da arşivlenecek.')
    )
    await waitFor(() => expect(archive).toHaveBeenCalledWith(10))
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before))
  })

  it('marka arşivlenince aktif seçili alt sayfası da temizlenir', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    useBrandStore.setState({
      activeWorkspace: {
        id: 11,
        name: 'Odak Bir',
        company_url: 'https://brand.com/a',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
    const { container } = renderAt('/brand-profile')
    const card = await brandCard(container, 'Marka A')
    fireEvent.click(within(card).getByRole('button', { name: 'Arşivle' }))
    await waitFor(() => expect(archive).toHaveBeenCalledWith(10))
    await waitFor(() => expect(useBrandStore.getState().activeWorkspace).toBeNull())
  })

  it('başka markanın aktif alt sayfası arşivde seçili kalır', async () => {
    useBrandStore.setState({
      activeWorkspace: {
        id: 11,
        name: 'Odak Bir',
        company_url: 'https://brand.com/a',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
    const { container } = renderAt('/brand-profile')
    const card = await brandCard(container, 'Marka B')
    fireEvent.click(within(card).getByRole('button', { name: 'Arşivle' }))
    await waitFor(() => expect(archive).toHaveBeenCalledWith(20))
    expect(useBrandStore.getState().activeWorkspace?.id).toBe(11)
  })

  it('onay reddedilirse arşivlemez', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { container } = renderAt('/brand-profile')
    const card = await brandCard(container, 'Marka A')
    fireEvent.click(within(card).getByRole('button', { name: 'Arşivle' }))
    expect(archive).not.toHaveBeenCalled()
  })

  it('alt sayfası olmayan marka onaysız arşivlenir', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const { container } = renderAt('/brand-profile')
    const card = await brandCard(container, 'Marka B')
    fireEvent.click(within(card).getByRole('button', { name: 'Arşivle' }))
    await waitFor(() => expect(archive).toHaveBeenCalledWith(20))
    expect(confirm).not.toHaveBeenCalled()
  })

  it('arşivdeki alt sayfa geri getirilirken 409 detayı gösterilir', async () => {
    restore.mockRejectedValue({ message: 'Önce markayı arşivden çıkarın' })
    const { container } = renderAt('/brand-profile')
    await brandCard(container, 'Marka A')
    fireEvent.click(await screen.findByRole('checkbox'))
    // Kartın kendisi de role=button: düğmeyi kart içinde ara
    const card = await waitFor(async () => {
      const c = await brandCard(container, 'Arsiv Odak')
      expect(c).toBeTruthy()
      return c
    })
    fireEvent.click(within(card).getByText(/Geri Getir/))
    await waitFor(() => expect(restore).toHaveBeenCalledWith(31))
    expect(await screen.findByText('Önce markayı arşivden çıkarın')).toBeTruthy()
  })

  it('modal içindeki Arşivden Çıkar 409 detayını modalda gösterir', async () => {
    restore.mockRejectedValue({ message: 'Önce markayı arşivden çıkarın' })
    const { container } = renderAt('/brand-profile')
    await brandCard(container, 'Marka A')
    fireEvent.click(await screen.findByRole('checkbox'))
    const card = await waitFor(async () => {
      const c = await brandCard(container, 'Arsiv Odak')
      expect(c).toBeTruthy()
      return c
    })
    fireEvent.click(card)
    fireEvent.click(await screen.findByRole('button', { name: /Arşivden Çıkar/ }))
    await waitFor(() => expect(restore).toHaveBeenCalledWith(31))
    const dialog = document.querySelector('.bpx-modal') as HTMLElement
    expect(await within(dialog).findByText('Önce markayı arşivden çıkarın')).toBeTruthy()
  })
})

describe('Marka ayarlarını alt sayfalara uygula', () => {
  it('aktif alt sayfası olmayan markada düğme yok', async () => {
    renderAt('/brand-profile?workspace_id=20')
    await screen.findByText('Alt sayfalar')
    expect(applyBtn()).toBeNull()
  })

  it('önizleme: meşgul / değişiklik yok / değişecek; Uygula sonucu + atlananlar + liste yenileme', async () => {
    previewFocusSettings.mockResolvedValue({
      data: {
        focuses: [
          { id: 11, name: 'Odak Bir', busy: true, changes: [] },
          { id: 12, name: 'Odak Iki', busy: false, changes: [] },
          {
            id: 13,
            name: 'Odak Uc',
            busy: false,
            changes: [
              { field: 'a', label: 'Hedef kitle' },
              { field: 'b', label: 'Ton' },
            ],
          },
        ],
      },
    })
    applyFocusSettings.mockResolvedValue({
      data: {
        applied: [
          { id: 12, name: 'Odak Iki', changed: false },
          { id: 13, name: 'Odak Uc', changed: true },
        ],
        skipped: [{ id: 11, name: 'Odak Bir', reason: 'busy' }],
      },
    })
    renderAt('/brand-profile?workspace_id=10')
    fireEvent.click(
      await screen.findByRole('button', { name: 'Marka ayarlarını alt sayfalara uygula' })
    )
    expect(await screen.findByText('Önizleme')).toBeTruthy()
    expect(previewFocusSettings).toHaveBeenCalledWith(10)
    expect(screen.getByText(/İşlem sürüyor — atlanacak/)).toBeTruthy()
    expect(screen.getByText(/Değişiklik yok/)).toBeTruthy()
    expect(screen.getByText(/Değişecek: Hedef kitle, Ton/)).toBeTruthy()
    expect(
      screen.getByText('Bu alt sayfadaki yerel düzenlemeler marka ayarlarıyla değiştirilecek.')
    ).toBeTruthy()

    const before = list.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Uygula' }))
    expect(await screen.findByText('2 alt sayfaya uygulandı.')).toBeTruthy()
    expect(applyFocusSettings).toHaveBeenCalledWith(10)
    expect(screen.getByText('Odak Bir: atlandı (busy)')).toBeTruthy()
    expect(screen.queryByText('Önizleme')).toBeNull()
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before))
  })

  it('Vazgeç uygulamadan kapatır', async () => {
    previewFocusSettings.mockResolvedValue({
      data: { focuses: [{ id: 11, name: 'Odak Bir', busy: false, changes: [] }] },
    })
    renderAt('/brand-profile?workspace_id=10')
    fireEvent.click(
      await screen.findByRole('button', { name: 'Marka ayarlarını alt sayfalara uygula' })
    )
    await screen.findByText('Önizleme')
    fireEvent.click(screen.getByRole('button', { name: 'Vazgeç' }))
    expect(screen.queryByText('Önizleme')).toBeNull()
    expect(applyFocusSettings).not.toHaveBeenCalled()
  })

  it('önizleme hatası extractErrorMessage ile gösterilir', async () => {
    previewFocusSettings.mockRejectedValue({ message: 'Önizleme başarısız' })
    renderAt('/brand-profile?workspace_id=10')
    fireEvent.click(
      await screen.findByRole('button', { name: 'Marka ayarlarını alt sayfalara uygula' })
    )
    expect(await screen.findByText('Önizleme başarısız')).toBeTruthy()
  })
})
