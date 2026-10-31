import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'
import { useBrandStore } from '../stores/brandStore'

const base = {
  company_url: 'https://brand.com',
  onboarding_flow: 'profile_first' as const,
  profile_approved_at: '2026-01-01T00:00:00Z',
  profile_data: { company_name: 'X', sector: 'Sağlık', products: ['Ürün A'], anchor_texts: ['a'] },
  suggested_keywords: null,
  preliminary_info: null,
  excluded_info: null,
  default_geo_target_id: '2792',
  default_language_id: '1037',
  market_label: 'Türkiye · Türkçe',
  deleted_at: null,
  created_at: '2026-01-01T00:00:00Z',
  run_count: 0,
}

const brand = { ...base, id: 10, name: 'Klinik X', status: 'confirmed', crawl_scope: 'site' }
const draft = {
  ...base,
  id: 14,
  name: 'Taslak Marka',
  status: 'profile_review',
  crawl_scope: 'site',
}
const orphanFocus = {
  ...base,
  id: 12,
  name: 'Yetim Odak',
  status: 'confirmed',
  parent_brand_profile_id: 99,
  crawl_scope: 'page',
}
const copy = {
  ...base,
  id: 30,
  name: 'Klinik X — Almanya (DE)/Rusça',
  status: 'confirmed',
  crawl_scope: 'site',
  market_label: 'Almanya (DE) · Rusça',
}
const allRows: Array<Record<string, unknown> & { id: number }> = [brand, draft, orphanFocus, copy]

const list = vi.fn()
const addMarkets = vi.fn()
const marketOptions = vi.fn()
const searchLocations = vi.fn()

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api')
  return {
    ...actual,
    workspaceApi: {
      list: (...a: unknown[]) => list(...a),
      get: vi.fn((id: number) => Promise.resolve({ data: allRows.find((w) => w.id === id) })),
      update: vi.fn(() => Promise.resolve({ data: {} })),
      anchorsPreview: vi.fn(() => Promise.resolve({ data: { groups: [] } })),
      addMarkets: (...a: unknown[]) => addMarkets(...a),
      marketOptions: () => marketOptions(),
      searchLocations: (...a: unknown[]) => searchLocations(...a),
    },
    brandProfileApi: {
      policyReview: vi.fn(() => Promise.resolve({ data: {} })),
      startCompetitorPreview: vi.fn(() => Promise.resolve({ data: { task_id: 't1' } })),
      getCompetitorPreview: vi.fn(() =>
        Promise.resolve({ data: { status: 'completed', result: [] } })
      ),
      startCompetitorDiscovery: vi.fn(),
    },
  }
})

const OPTIONS = {
  countries: [{ geo_target_id: '2276', code: 'DE', name_tr: 'Almanya', label: 'Almanya (DE)' }],
  languages: [{ language_id: '1031', code: 'ru', name_tr: 'Rusça', name_native: 'Русский' }],
  default: { geo_target_id: '2792', language_id: '1037' },
}

const BANNER = /Pazar kopyası — rakipleri ve konum ayarını/

const renderAt = (entry: string | { pathname: string; search: string; state?: unknown }) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <BrandProfile />
    </MemoryRouter>
  )

const cardOf = (container: HTMLElement, name: string) =>
  Array.from(container.querySelectorAll<HTMLElement>('.bpx-card')).find(
    (c) => c.querySelector('.bpx-card-name')?.firstChild?.textContent === name
  )

beforeEach(() => {
  list.mockReset().mockResolvedValue({ data: [brand, draft, orphanFocus] })
  addMarkets.mockReset()
  marketOptions.mockReset().mockResolvedValue({ data: OPTIONS })
  searchLocations.mockReset().mockResolvedValue({
    data: {
      items: [
        {
          geo_target_id: '2276',
          label: 'Almanya (DE)',
          kind: 'country',
          country_code: 'DE',
          name: 'Almanya',
        },
      ],
      total: 1,
    },
  })
  useBrandStore.setState({ activeWorkspace: null, marketOptions: null, marketOptionsError: null })
})
afterEach(cleanup)

describe('Pazar ekle: kart eylemi', () => {
  it('onaylı marka kartında etkin; taslakta pasif + ipucu; alt sayfa kartında yok', async () => {
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(3))

    const ok = within(cardOf(container, 'Klinik X')!).getByRole('button', { name: /Pazar ekle/ })
    expect(ok).toBeEnabled()

    const pending = within(cardOf(container, 'Taslak Marka')!).getByRole('button', {
      name: /Pazar ekle/,
    })
    expect(pending).toBeDisabled()
    expect(pending).toHaveAttribute('title', 'Önce profili onaylayın')

    expect(
      within(cardOf(container, 'Yetim Odak')!).queryByRole('button', { name: /Pazar ekle/ })
    ).toBeNull()
  })

  it('oluşturunca liste yenilenir; "İlk çalışmaya geç" kopyayı aktif yapar ve uyarı şeridi gösterir', async () => {
    addMarkets.mockImplementation(() => {
      list.mockResolvedValue({ data: [brand, draft, orphanFocus, copy] })
      return Promise.resolve({ data: { created: [copy], skipped: [], source_id: 10 } })
    })
    const { container } = renderAt('/brand-profile')
    await waitFor(() => expect(container.querySelectorAll('.bpx-card').length).toBe(3))
    fireEvent.click(
      within(cardOf(container, 'Klinik X')!).getByRole('button', { name: /Pazar ekle/ })
    )

    const dialog = await screen.findByRole('dialog', { name: 'Pazar ekle' })
    const input = within(dialog).getByPlaceholderText('Ülke veya şehir ara…')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'alman' } })
    fireEvent.click(await within(dialog).findByRole('option', { name: 'Almanya (DE)' }))
    await waitFor(() => expect(within(dialog).getByLabelText('Çıktı dili')).toBeEnabled())
    fireEvent.change(within(dialog).getByLabelText('Çıktı dili'), { target: { value: '1031' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '1 çalışma oluştur' }))

    await within(dialog).findByText('1 oluşturuldu, 0 atlandı')
    expect(addMarkets).toHaveBeenCalledWith(10, [{ geo_target_id: '2276', language_id: '1031' }])
    // Liste yeniden çekildi: yeni kart rozetiyle görünür
    await waitFor(() => expect(cardOf(container, copy.name)).toBeTruthy())
    expect(cardOf(container, copy.name)).toHaveTextContent('Almanya (DE) · Rusça')

    fireEvent.click(within(dialog).getByRole('button', { name: 'İlk çalışmaya geç' }))
    expect(await screen.findByText(BANNER)).toBeInTheDocument()
    expect(useBrandStore.getState().activeWorkspace?.id).toBe(30)
    expect(screen.queryByRole('dialog', { name: 'Pazar ekle' })).toBeNull()

    // Anladım -> uyarı kalıcı değil
    fireEvent.click(screen.getByRole('button', { name: 'Anladım' }))
    await waitFor(() => expect(screen.queryByText(BANNER)).toBeNull())
  })
})

describe('Pazar kopyası uyarı şeridi', () => {
  it('gezinme durumunda marketCopyId varsa, yalnız o çalışma için görünür', async () => {
    renderAt({
      pathname: '/brand-profile',
      search: '?workspace_id=30',
      state: { marketCopyId: 30 },
    })
    expect(await screen.findByText(BANNER)).toBeInTheDocument()
  })

  it('gezinme durumu yoksa görünmez; başka çalışmanın durumuyla da görünmez', async () => {
    renderAt('/brand-profile?workspace_id=30')
    await screen.findAllByText('Klinik X — Almanya (DE)/Rusça')
    expect(screen.queryByText(BANNER)).toBeNull()
    cleanup()

    renderAt({
      pathname: '/brand-profile',
      search: '?workspace_id=10',
      state: { marketCopyId: 30 },
    })
    await screen.findAllByText('Klinik X')
    expect(screen.queryByText(BANNER)).toBeNull()
  })
})
