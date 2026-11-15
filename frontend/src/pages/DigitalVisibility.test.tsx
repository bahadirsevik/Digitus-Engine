import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import DigitalVisibility from './DigitalVisibility'
import { adsText, rankText } from './digitalAuditView'
import { digitalAuditApi, type AuditVisibility } from '../services/api'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return { ...actual, digitalAuditApi: { livePlaces: vi.fn() } }
})

const api = vi.mocked(digitalAuditApi)

const VISIBILITY: AuditVisibility = {
  measured_at: '2026-11-15T10:00:00Z',
  location: 'Istanbul, Turkey',
  sites: ['marka.test', 'rakip.test'],
  serp: {
    status: 'ok',
    keywords: [
      {
        keyword: 'diş hekimi',
        positions: { 'marka.test': 14, 'rakip.test': null },
        ads: ['rakip.test'],
        local_pack: true,
        checked_top: 20,
        top: [],
      },
      {
        keyword: 'implant',
        positions: {},
        ads: [],
        local_pack: false,
        checked_top: 0,
        top: [],
        error: 'Sorgu sonuç vermedi (kota ya da servis hatası).',
      },
    ],
    brand: { query: 'Marka', own_position: 1, knowledge_panel: false, other_ads: [] },
    reputation: [
      {
        query: 'Marka şikayet',
        results: [{ position: 2, title: 'Şikayet başlığı', domain: 'sikayet.test', shown: '' }],
      },
    ],
    queries_used: 7,
    query_limit: 23,
    location_used: 'Istanbul,Turkey',
    device: 'desktop',
  },
  youtube: {
    status: 'ok',
    channels: {
      'marka.test': {
        status: 'ok',
        title: 'Marka TV',
        subscribers: null,
        video_count: 40,
        last_upload: '2026-11-01',
        uploads_90d: 3,
        recent_avg_views: 850,
        recent_sample: 20,
      },
      'rakip.test': { status: 'not_observed', reason: 'Sitede YouTube bağlantısı görülmedi.' },
    },
  },
  places: {
    status: 'ok',
    profiles: {
      'marka.test': { status: 'matched', place_id: 'P1', maps_url: 'https://maps.test/P1' },
      'rakip.test': { status: 'not_matched', reason: 'Profil bulunamadı (yok demek değildir).' },
    },
  },
}

beforeEach(() => vi.clearAllMocks())

describe('DigitalVisibility', () => {
  it('gözlenmeyen sırayı ölçülen derinlikle, ölçülemeyeni ayrı yazar', () => {
    const [found, failed] = VISIBILITY.serp!.keywords
    expect(rankText(found, 'marka.test')).toBe('14. sıra')
    expect(rankText(found, 'rakip.test')).toBe("İlk 20'de gözlenmedi")
    expect(rankText(failed, 'marka.test')).toBe('Ölçülmedi')
    // Adresi okunamayan sonuç varsa kesin yokluk yazılmaz
    expect(rankText({ ...found, unresolved: 2 }, 'rakip.test')).toBe(
      "İlk 20'de eşleşmedi (2 sonucun adresi okunamadı)"
    )
    expect(adsText([], 1)).toBe('1 reklamın adresi okunamadı')
  })

  it('ölçüm koşullarını, reklamı ve itibar aramasını gösterir', () => {
    render(<DigitalVisibility auditId={7} workspaceId={1} visibility={VISIBILITY} />)
    expect(screen.getByText(/Istanbul,Turkey · masaüstü/)).toBeInTheDocument()
    expect(screen.getByText(/Kullanılan sorgu:\s*7 \/ 23/)).toBeInTheDocument()
    const row = screen.getByText('diş hekimi').closest('tr') as HTMLElement
    expect(within(row).getByText("İlk 20'de gözlenmedi")).toBeInTheDocument()
    expect(within(row).getByText('rakip.test')).toBeInTheDocument()
    expect(screen.getByText(/Şikayet başlığı/)).toBeInTheDocument()
    expect(screen.getByText('gizli')).toBeInTheDocument()
    expect(screen.getByText(/3 \(son 20 video içinde\)/)).toBeInTheDocument()
    expect(screen.getByText('Gözlenmedi')).toBeInTheDocument()
  })

  it('Google profil bilgisini yalnız istenince ve saklamadan getirir', async () => {
    api.livePlaces.mockResolvedValue({
      data: {
        profiles: {
          'marka.test': { available: true, name: 'Marka Klinik', rating: 4.7, rating_count: 321 },
        },
      },
    } as never)
    render(<DigitalVisibility auditId={7} workspaceId={1} visibility={VISIBILITY} />)
    expect(api.livePlaces).not.toHaveBeenCalled()
    expect(screen.queryByText(/4.7 puan/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Güncel profil bilgisini getir/ }))
    expect(await screen.findByText(/4.7 puan · 321 yorum/)).toBeInTheDocument()
    expect(api.livePlaces).toHaveBeenCalledWith(7, 1)
    expect(screen.getByText(/saklanmaz, rapora\s+girmez/)).toBeInTheDocument()
  })

  it('servis yokken bölümü veri yok olarak gösterir', () => {
    render(
      <DigitalVisibility
        auditId={7}
        workspaceId={1}
        visibility={{
          ...VISIBILITY,
          serp: {
            ...VISIBILITY.serp!,
            status: 'unavailable',
            reason: 'Arama servisi yapılandırılmamış (API anahtarı yok).',
          },
        }}
      />
    )
    expect(screen.getByText(/Veri yok: Arama servisi yapılandırılmamış/)).toBeInTheDocument()
    expect(screen.queryByText('diş hekimi')).not.toBeInTheDocument()
  })
})
