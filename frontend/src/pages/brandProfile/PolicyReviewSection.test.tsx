/**
 * Kalıcılık sözleşmesi testleri (Codex blocker'ı):
 * - workspace yanıtındaki competitor_urls kartı DOLU başlatır (boş değil)
 * - kaydedilmiş not_competitor kararı varsayılan 'block'u EZER
 * - kullanıcı yalnız konu dışlamasını değiştirse bile kayıt, mevcut URL +
 *   kararları AYNEN taşır (tam-replacement mevcut veriyi silemez)
 * - workspace verisi sonradan gelirse (poll/refresh) kart yeniden kurulur
 */
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import PolicyReviewSection from './PolicyReviewSection'

const policyReview = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('../../services/api', () => ({
  brandProfileApi: {
    policyReview: (...args: unknown[]) => policyReview(...(args as [])),
    startCompetitorPreview: vi.fn(() => Promise.resolve({ data: { task_id: 't1' } })),
    getCompetitorPreview: vi.fn(() =>
      Promise.resolve({ data: { task_id: 't1', status: 'completed', result: [] } })
    ),
  },
}))

const workspace = {
  id: 21,
  competitor_urls: ['https://fintables.com'],
  competitor_url_decisions: {
    'fintables.com': {
      url: 'https://fintables.com',
      term: '',
      decision: 'not_competitor' as const,
    },
  },
  validation_data: {
    competitors: [
      {
        url: 'https://fintables.com/',
        status: 'same_sector',
        summary: 'Aynı sektör',
        detected_name: 'Fintables',
      },
    ],
  },
  excluded_info: 'temettü takibi',
}

describe('PolicyReviewSection (kalıcılık sözleşmesi)', () => {
  it('workspace verisinden dolu başlar; kaydedilmiş karar block varsayılanını ezer', async () => {
    render(<PolicyReviewSection workspace={workspace} />)
    // URL slotu dolu (boş başlasaydı kayıt mevcut URL'yi silerdi)
    expect(screen.getByDisplayValue('https://fintables.com')).toBeTruthy()
    // Kaydedilmiş kullanıcı kararı authoritative: "Rakip değil" seçili
    const notCompetitorBtn = screen.getByText('Rakip değil').closest('button') as HTMLButtonElement
    expect(notCompetitorBtn.className).toContain('is-active')
    // excluded_info forma yüklendi
    expect(screen.getByDisplayValue('temettü takibi')).toBeTruthy()
  })

  it('yalnız konu dışlaması değişse bile kayıt mevcut URL + kararı aynen taşır', async () => {
    policyReview.mockClear()
    render(<PolicyReviewSection workspace={workspace} />)
    const textarea = screen.getByPlaceholderText(/Dışlanacak konuları yazın/)
    fireEvent.change(textarea, { target: { value: 'kripto para' } })
    fireEvent.click(screen.getByText(/Politikayı kaydet/))
    await waitFor(() => expect(policyReview).toHaveBeenCalledTimes(1))
    const [wsId, payload] = policyReview.mock.calls[0] as unknown as [
      number,
      {
        competitor_urls: string[]
        competitor_decisions: { url: string; decision: string }[]
        excluded_info: string
      },
    ]
    expect(wsId).toBe(21)
    expect(payload.competitor_urls).toEqual(['https://fintables.com'])
    expect(payload.competitor_decisions).toEqual([
      { url: 'https://fintables.com', term: '', decision: 'not_competitor' },
    ])
    expect(payload.excluded_info).toBe('kripto para')
  })

  it('workspace verisi sonradan gelirse kart yeniden kurulur (boş → dolu)', async () => {
    const { rerender } = render(
      <PolicyReviewSection workspace={{ id: 21, competitor_urls: [], excluded_info: '' }} />
    )
    expect(screen.queryByDisplayValue('https://fintables.com')).toBeNull()
    rerender(<PolicyReviewSection workspace={workspace} />)
    await waitFor(() => expect(screen.getByDisplayValue('https://fintables.com')).toBeTruthy())
  })
})
