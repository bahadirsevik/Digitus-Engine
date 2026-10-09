import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import BrandProfile from './BrandProfile'

// Sunucu durumunu taklit eden değiştirilebilir çalışma verisi: kutu B kaydı
// (policy/review) excluded_info'yu yazar VE terimi exclude_themes'e birleştirir.
function freshWorkspace() {
  return {
    id: 10,
    name: 'Confirmed Brand',
    company_url: 'https://brand.com',
    status: 'confirmed',
    onboarding_flow: 'profile_first' as const,
    profile_approved_at: '2026-01-01T00:00:00Z',
    profile_data: {
      company_name: 'Confirmed Brand',
      sector: 'E-Ticaret',
      products: ['Ürün A'],
      exclude_themes: ['ai teması'],
      anchor_texts: ['anchor1'],
    } as Record<string, unknown>,
    competitor_urls: [] as string[],
    suggested_keywords: null,
    preliminary_info: null,
    excluded_info: null as string | null,
    default_geo_target_id: '2792',
    default_language_id: '1037',
    deleted_at: null,
    created_at: '2026-01-01T00:00:00Z',
    run_count: 0,
  }
}

const serverState = { ws: freshWorkspace() }

const confirm = vi.fn()
const policyReview = vi.fn()

vi.mock('../services/api', () => ({
  workspaceApi: {
    list: vi.fn(() => Promise.resolve({ data: [serverState.ws] })),
    get: vi.fn(() => Promise.resolve({ data: structuredClone(serverState.ws) })),
    update: vi.fn(() => Promise.resolve({ data: {} })),
    confirm: (...args: unknown[]) => confirm(...args),
  },
  brandProfileApi: {
    policyReview: (...args: unknown[]) => policyReview(...args),
    startCompetitorPreview: vi.fn(() => Promise.resolve({ data: { task_id: 't1' } })),
    getCompetitorPreview: vi.fn(() =>
      Promise.resolve({ data: { status: 'completed', result: [] } })
    ),
  },
  apiErrorMessage: (_: unknown, fallback = 'Hata') => fallback,
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/brand-profile?workspace_id=10']}>
      <BrandProfile />
    </MemoryRouter>
  )
}

describe('BrandProfile dışlama kutuları (plan 3.1)', () => {
  beforeEach(() => {
    serverState.ws = freshWorkspace()
    confirm.mockReset()
    policyReview.mockReset()
    policyReview.mockImplementation((_id: number, payload: { excluded_info?: string }) => {
      const terms = (payload.excluded_info || '')
        .split(/[,\n]+/)
        .map((t) => t.trim())
        .filter(Boolean)
      const profile = serverState.ws.profile_data
      serverState.ws.excluded_info = payload.excluded_info || null
      profile.exclude_themes = [...terms, 'ai teması']
      return Promise.resolve({ data: {} })
    })
    confirm.mockImplementation(() => Promise.resolve({ data: structuredClone(serverState.ws) }))
  })

  it('etiketler ve yardım metinleri yeni sözleşmeyle görünür', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Kaçınılacak temalar')).toBeTruthy())
    expect(screen.getByText('Eleme yapmaz, yapay zekâyı yönlendirir.')).toBeTruthy()
    expect(screen.getByLabelText('Kesin dışlama')).toBeTruthy()
    expect(screen.getByText('Bu konuları içeren kelimeler elenir.')).toBeTruthy()
    expect(screen.queryByText('Dışlanacak Temalar')).toBeNull()
    expect(screen.queryByText('Mutlaka olmaması gerekenler')).toBeNull()
  })

  it('A düzenle → B kaydet → A kaydet: düzenleme kaybolmaz, B çipi salt-okunur, payload yalnız A terimlerini taşır', async () => {
    renderPage()
    const themes = (await waitFor(() => {
      const el = screen.getByLabelText('Kaçınılacak temalar')
      expect(el).toBeTruthy()
      return el
    })) as HTMLTextAreaElement
    expect(themes.value).toBe('ai teması')

    // 1) A'da kaydedilmemiş düzenleme
    fireEvent.change(themes, { target: { value: 'ai teması\nucuz taklit' } })

    // 2) B'ye kesin dışlama ekle + kaydet (sayfa çalışmayı yeniden yükler)
    fireEvent.change(screen.getByLabelText('Kesin dışlama'), {
      target: { value: 'kripto para' },
    })
    fireEvent.click(screen.getByText(/Politikayı kaydet/))
    await waitFor(() => expect(policyReview).toHaveBeenCalledTimes(1))

    // B terimi A'da salt-okunur çip olarak görünür
    const chips = await screen.findByTestId('hard-exclude-chips')
    expect(within(chips).getByText('kripto para')).toBeTruthy()
    expect(within(chips).getByText('kesin dışlamadan gelir')).toBeTruthy()
    // Silinemez: çipte düğme yok
    expect(within(chips).queryAllByRole('button')).toHaveLength(0)

    // Kaydedilmemiş A düzenlemesi yeniden yüklemede SİLİNMEDİ; B terimi A metnine sızmadı
    const themesAfter = screen.getByLabelText('Kaçınılacak temalar') as HTMLTextAreaElement
    expect(themesAfter.value).toBe('ai teması\nucuz taklit')

    // 3) A'yı kaydet
    fireEvent.click(screen.getByRole('button', { name: /Değişiklikleri Kaydet/i }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    const [wsId, body] = confirm.mock.calls[0] as unknown as [
      number,
      { profile_data: { exclude_themes: string[] } },
    ]
    expect(wsId).toBe(10)
    // Yalnız kullanıcının A terimleri gider; B çipi gönderilmez (sunucu korur)
    expect(body.profile_data.exclude_themes).toEqual(['ai teması', 'ucuz taklit'])
  })

  it('kaydedilmemiş düzenleme yokken B kaydı sonrası A listesi sunucudan yenilenir (B terimi hariç)', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Kaçınılacak temalar')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Kesin dışlama'), { target: { value: 'kripto para' } })
    fireEvent.click(screen.getByText(/Politikayı kaydet/))
    await screen.findByTestId('hard-exclude-chips')

    const themes = screen.getByLabelText('Kaçınılacak temalar') as HTMLTextAreaElement
    expect(themes.value).toBe('ai teması')
  })
})
