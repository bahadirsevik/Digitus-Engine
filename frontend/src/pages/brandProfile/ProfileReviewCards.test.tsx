import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfileReviewCards from './ProfileReviewCards'
import { useBrandStore } from '../../stores/brandStore'

const approveProfile = vi.fn()

const MARKET_OPTIONS = {
  countries: [
    { geo_target_id: '2276', code: 'DE', name_tr: 'Almanya', label: 'Almanya (DE)' },
    { geo_target_id: '2643', code: 'RU', name_tr: 'Rusya', label: 'Rusya (RU)' },
  ],
  languages: [
    { language_id: '1031', code: 'ru', name_tr: 'Rusça', name_native: 'Русский' },
    { language_id: '1001', code: 'de', name_tr: 'Almanca', name_native: 'Deutsch' },
  ],
  default: { geo_target_id: '2276', language_id: '1031' },
}

vi.mock('../../services/api', () => ({
  workspaceApi: {
    approveProfile: (...args: unknown[]) => approveProfile(...args),
    marketOptions: () => Promise.resolve({ data: MARKET_OPTIONS }),
    searchLocations: () => Promise.resolve({ data: { items: [], total: 0 } }),
  },
  apiErrorCode: (err: unknown) =>
    (err as { detail?: { code?: string } } | null)?.detail?.code ?? null,
  // brandProfileState.extractErrorMessage bu helper'a delege eder — modül
  // mock'u gerçek implementasyonu sildiğinden minimal eşdeğeri sağlanır
  apiErrorMessage: (err: unknown, fallback = 'Beklenmeyen hata') =>
    (err && typeof err === 'object' && 'message' in err
      ? String((err as { message?: unknown }).message)
      : '') || fallback,
}))

vi.mock('./CompetitorReviewCard', () => ({
  default: () => <div data-testid="competitor-review" />,
}))

vi.mock('./useCompetitorReview', () => ({
  useCompetitorReview: () => ({
    isValid: true,
    buildPayload: () => ({ competitor_urls: [], competitor_decisions: [] }),
  }),
}))

const workspace = {
  id: 24,
  name: 'Hissefy',
  company_url: 'https://hissefy.com',
  status: 'profile_review',
  onboarding_flow: 'profile_first' as const,
  profile_approved_at: null,
  profile_data: {
    company_name: 'Hissefy',
    sector: 'Finansal teknoloji',
    brand_summary: 'Hisse analiz platformu',
    products: ['Analiz'],
    services: [],
    target_audience: 'Yatırımcılar',
  },
  suggested_keywords: null,
  preliminary_info: null,
  excluded_info: null,
  default_geo_target_id: '2643',
  default_language_id: '1001',
  market_label: 'Moscow, Rusya · Almanca',
  market_locked: false,
  deleted_at: null,
  created_at: '2026-07-22T00:00:00Z',
  run_count: 0,
}

describe('ProfileReviewCards', () => {
  beforeEach(() => {
    useBrandStore.setState({ marketOptions: null, marketOptionsError: null })
    approveProfile.mockReset().mockResolvedValue({
      data: { ...workspace, status: 'competitor_review' },
    })
  })

  it('profil onayında strateji endpointi çağrılmaz, tek bir approveProfile isteği yapılır', async () => {
    const onApproved = vi.fn()
    render(<ProfileReviewCards workspace={workspace} onApproved={onApproved} />)

    // Kanal Stratejisi ve ilgili alanlar görünmemeli
    expect(screen.queryByText(/Kanal Stratejisi/i)).toBeNull()
    expect(screen.queryByText(/İçerik stratejisi/i)).toBeNull()
    expect(screen.queryByText(/Sosyal strateji modu/i)).toBeNull()
    expect(screen.queryByText(/Ürün\/hizmet tanımı/i)).toBeNull()

    const approveBtn = screen.getByRole('button', {
      name: 'Onayla ve devam et',
    }) as HTMLButtonElement
    expect(approveBtn.disabled).toBe(false)

    fireEvent.click(approveBtn)
    await waitFor(() => expect(approveProfile).toHaveBeenCalledTimes(1))

    expect(approveProfile.mock.calls[0][1]).toMatchObject({ rerun_keywords: false })
    expect(onApproved.mock.calls[0][0].status).toBe('competitor_review')
  })

  it('korunacak konular alanı liste boşken de görünür ve payload ile gönderilir', async () => {
    render(<ProfileReviewCards workspace={workspace} onApproved={vi.fn()} />)

    const protectedField = screen.getByPlaceholderText(/kapsamda kalsın/)
    expect((protectedField as HTMLTextAreaElement).value).toBe('')

    fireEvent.change(protectedField, {
      target: { value: 'saç boyası bakımı\nkuru saç' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Onayla ve devam et' }))

    await waitFor(() => expect(approveProfile).toHaveBeenCalledTimes(1))
    expect(approveProfile.mock.calls[0][1].profile_data).toMatchObject({
      protected_themes: ['saç boyası bakımı', 'kuru saç'],
    })
  })

  it('onay hatası durumunda hata bannerı gösterilir', async () => {
    approveProfile.mockRejectedValue(new Error('Sunucu hatası'))
    const onApproved = vi.fn()
    render(<ProfileReviewCards workspace={workspace} onApproved={onApproved} />)

    fireEvent.click(screen.getByRole('button', { name: 'Onayla ve devam et' }))
    await waitFor(() => expect(approveProfile).toHaveBeenCalledTimes(1))
    expect(onApproved).not.toHaveBeenCalled()
    expect(await screen.findByText(/Sunucu hatası/)).toBeTruthy()
  })

  it('mevcut pazar etiketiyle gösterilir ve onayda ID değerleri gönderilir', async () => {
    render(<ProfileReviewCards workspace={workspace} onApproved={vi.fn()} />)
    expect(await screen.findByText('Moscow, Rusya')).toBeTruthy()
    expect((screen.getByLabelText('Çıktı dili') as HTMLSelectElement).value).toBe('1001')

    fireEvent.click(screen.getByRole('button', { name: 'Onayla ve devam et' }))
    await waitFor(() => expect(approveProfile).toHaveBeenCalledTimes(1))
    expect(approveProfile.mock.calls[0][1]).toMatchObject({
      default_geo_target_id: '2643',
      default_language_id: '1001',
    })
  })

  it('veri olan (kilitli) çalışmada pazar pasif, mesaj görünür ve alan gönderilmez', async () => {
    render(
      <ProfileReviewCards workspace={{ ...workspace, market_locked: true }} onApproved={vi.fn()} />
    )
    expect(await screen.findByText(/hedef konum\/dil değiştirilemez/)).toBeTruthy()
    expect((screen.getByLabelText('Hedef konum') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Çıktı dili') as HTMLSelectElement).disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Onayla ve devam et' }))
    await waitFor(() => expect(approveProfile).toHaveBeenCalledTimes(1))
    expect(approveProfile.mock.calls[0][1]).not.toHaveProperty('default_geo_target_id')
  })

  it('409 MARKET_LOCKED gelirse kilit mesajı satır içi gösterilir', async () => {
    approveProfile.mockRejectedValue({
      status: 409,
      message: 'x',
      detail: { code: 'MARKET_LOCKED', message: 'x' },
    })
    render(<ProfileReviewCards workspace={workspace} onApproved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Onayla ve devam et' }))
    expect(await screen.findByText(/Yeni pazar çalışması açın/)).toBeTruthy()
    expect((screen.getByLabelText('Hedef konum') as HTMLInputElement).disabled).toBe(true)
  })
})
