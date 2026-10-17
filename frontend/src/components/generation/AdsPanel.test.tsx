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
    getAdsRsa: vi.fn(() => Promise.resolve({ data: mockAdsResult })),
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
let mockAdsResult: Record<string, unknown> = { total_groups: 0 }

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
    mockAdsResult = { total_groups: 0 }
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

// 15 KW teslim standardı raporu (standard_report) — yalnız olgu ve liste
describe('AdsPanel standart raporu', () => {
  const baseReport = {
    keyword_count: 15,
    headline_count: 15,
    kw_description_count: 4,
    missing_headlines: 0,
    missing_kw_descriptions: 0,
    below_standard: false,
    pool_below_target: false,
    keywords_in_headlines: [],
    keywords_description_only: [],
    keywords_unused: [],
    fill_keywords: [],
  }
  const showGroup = (standard_report?: Record<string, unknown> | null) => {
    mockAdsResult = {
      total_groups: 1,
      ad_groups: [
        {
          id: 1,
          group_name: 'Grup 1',
          headlines: [],
          descriptions: [],
          negative_keywords: [],
          ...(standard_report === undefined ? {} : { standard_report }),
        },
      ],
    }
    return render(<AdsPanel runId={1} />)
  }

  beforeEach(() => {
    mockSets = []
    mockAdsResult = { total_groups: 0 }
    useBrandStore.setState({
      activeWorkspace: {
        id: 10,
        name: 'WS',
        company_url: 'https://ws.test',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
  })

  it('özet satırı sayıları gösterir; standart altı rozeti ve boş listeler yok', async () => {
    showGroup(baseReport)
    expect(await screen.findByText("15 KW · 15 başlık · 4 KW'li açıklama")).toBeTruthy()
    expect(screen.queryByText(/teslim standardının altında/)).toBeNull()
    expect(screen.queryByText(/standart 15/)).toBeNull()
    expect(screen.queryByText(/Başlıklarda kullanılan KW/)).toBeNull()
    expect(screen.queryByText(/Hiç kullanılmayan KW/)).toBeNull()
  })

  it('standart altı rozeti yalnız eksik olan parçayı yazar', async () => {
    showGroup({
      ...baseReport,
      headline_count: 13,
      missing_headlines: 2,
      below_standard: true,
    })
    expect(await screen.findByText('15 KW teslim standardının altında')).toBeTruthy()
    expect(screen.getByText('Eksik: 2 başlık')).toBeTruthy()
  })

  it('iki eksik de varsa ikisini listeler', async () => {
    showGroup({
      ...baseReport,
      missing_headlines: 3,
      missing_kw_descriptions: 1,
      below_standard: true,
    })
    expect(await screen.findByText("Eksik: 3 başlık, 1 KW'li açıklama")).toBeTruthy()
  })

  it('havuz hedefin altındaysa KW sayısını yazar', async () => {
    showGroup({ ...baseReport, keyword_count: 12, pool_below_target: true })
    expect(await screen.findByText('Bu grupta 12 KW var (standart 15)')).toBeTruthy()
  })

  it('yalnız dolu KW listeleri çizilir', async () => {
    showGroup({
      ...baseReport,
      keywords_unused: ['kalem'],
      fill_keywords: ['defter'],
    })
    expect(await screen.findByText("Hiç kullanılmayan KW'ler (1)")).toBeTruthy()
    expect(screen.getByText("15'e tamamlamak için eklenen KW'ler (1)")).toBeTruthy()
    expect(screen.getByText('kalem')).toBeTruthy()
    expect(screen.getByText('defter')).toBeTruthy()
    expect(screen.queryByText(/Başlıklarda kullanılan KW/)).toBeNull()
    expect(screen.queryByText(/Yalnız açıklamada geçen KW/)).toBeNull()
  })

  it.each([null, undefined])('rapor %s ise ek bir şey çizilmez', async (report) => {
    const { container } = showGroup(report)
    expect(await screen.findByText('Grup 1')).toBeTruthy()
    expect(container.querySelector('.adsx-std')).toBeNull()
  })
})
