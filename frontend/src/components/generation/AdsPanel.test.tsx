/**
 * AdsPanel davranış testi — codex bulgusu: reset + autofill ayrı effect'lerdeyken
 * autofill eski closure değerlerini görüp doldurmayı atlıyordu (run değişince
 * alanlar boş kalıyordu). Bu test run değişiminde YENİ profil değerlerinin
 * gerçekten dolduğunu kilitler.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import AdsPanel from './AdsPanel'
import { useBrandStore } from '../../stores/brandStore'

const profiles: Record<number, { company_url: string; company_name: string }> = {
  1: { company_url: 'https://marka-a.test', company_name: 'Marka A' },
  2: { company_url: 'https://marka-b.test', company_name: 'Marka B' },
}

vi.mock('../../services/api', () => ({
  brandProfileApi: {
    getProfile: vi.fn((runId: number) =>
      Promise.resolve({
        data: {
          company_url: profiles[runId]?.company_url,
          status: 'confirmed',
          profile_data: {
            company_name: profiles[runId]?.company_name,
            products: ['Ürün X'],
            sector: 'Sektör Y',
          },
        },
      })
    ),
  },
  generationApi: {
    getAdsRsa: vi.fn(() => Promise.resolve({ data: { total_groups: 0 } })),
    createAdsRsa: vi.fn(() => Promise.resolve({ data: { task_id: 't1', generation_set_id: 5 } })),
    getAdsSets: vi.fn(() =>
      Promise.resolve({ data: { scoring_run_id: 1, sets: mockSets, active_set_id: null } })
    ),
    activateAdsSet: vi.fn(() => Promise.resolve({ data: {} })),
    regenerateAdGroup: vi.fn(() => Promise.resolve({ data: { task_id: 't2' } })),
  },
}))

// Faz E set senaryosu: yalnız stale-olmayan draft var, aktif non-stale yok
let mockSets: Array<Record<string, unknown>> = []

vi.mock('../../hooks/useTaskPolling', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../hooks/useTaskPolling')>()),
  useTaskPolling: () => ({
    status: null,
    progress: 0,
    isActive: false,
    isCompleted: false,
    errorMessage: null,
  }),
}))

describe('AdsPanel autofill', () => {
  beforeEach(() => {
    mockSets = []
    useBrandStore.setState({
      activeWorkspace: {
        id: 10,
        name: 'WS',
        company_url: 'https://ws.test',
        status: 'confirmed',
        profile_data: { company_name: 'WS Fallback', products: ['P'], sector: 'S' },
        suggested_keywords: null,
      },
    })
  })

  it('run profili alanları doldurur', async () => {
    render(<AdsPanel runId={1} />)
    expect(await screen.findByDisplayValue('Marka A')).toBeTruthy()
    expect(await screen.findByDisplayValue('https://marka-a.test')).toBeTruthy()
  })

  it('run değişince ESKİ değerler taşınmaz, YENİ profil dolar (closure regresyonu)', async () => {
    const { rerender } = render(<AdsPanel runId={1} />)
    expect(await screen.findByDisplayValue('Marka A')).toBeTruthy()

    rerender(<AdsPanel runId={2} />)

    // Eski kodda: reset alanları boşaltıyor ama autofill eski closure'daki
    // dolu değerleri görüp atlıyordu → alanlar boş kalıyordu.
    expect(await screen.findByDisplayValue('Marka B')).toBeTruthy()
    expect(await screen.findByDisplayValue('https://marka-b.test')).toBeTruthy()
    expect(screen.queryByDisplayValue('Marka A')).toBeNull()
  })

  // Faz E versiyonlama: stale aktif + taze taslak varken kullanıcı
  // onay yönlendirmesini ve durum rozetlerini görmeli
  it('aktif non-stale set yokken taslak onay uyarısı ve rozetler görünür', async () => {
    mockSets = [
      {
        id: 5,
        scoring_run_id: 1,
        version_number: 2,
        status: 'draft',
        is_stale: false,
        groups_count: 3,
      },
      {
        id: 4,
        scoring_run_id: 1,
        version_number: 1,
        status: 'active',
        is_stale: true,
        groups_count: 2,
      },
    ]
    render(<AdsPanel runId={1} />)
    expect(await screen.findByText(/Güncel aktif reklam seti yok/)).toBeTruthy()
    expect(screen.getByText('Taslak')).toBeTruthy()
    expect(screen.getAllByText('Bayat').length).toBeGreaterThan(0)
  })
})
