import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import DigitalResearch from './DigitalResearch'
import { claimChip, claimInReport, yearNote } from './digitalResearchView'
import { digitalResearchApi, type DigitalResearchDetail, type ResearchClaim } from '../services/api'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    digitalResearchApi: {
      start: vi.fn(),
      list: vi.fn(),
      get: vi.fn(),
      saveClaimOverrides: vi.fn(),
      startBriefs: vi.fn(),
      exportReport: vi.fn(),
    },
  }
})

const api = vi.mocked(digitalResearchApi)

function claim(over: Partial<ResearchClaim>): ResearchClaim {
  return {
    id: 'sector:0',
    claim: 'Diş hekimi sayısı',
    value: '45.000',
    unit: 'kişi',
    year: '2023',
    geography: 'Türkiye',
    source_name: 'TDB',
    check: {
      status: 'found',
      source_url: 'https://kaynak.test/rapor',
      context: '2023 yılında 45 000 diş hekimi görev yaptı',
      year_in_context: true,
    },
    ...over,
  }
}

const DETAIL: DigitalResearchDetail = {
  id: 3,
  status: 'completed',
  stage: 'research',
  progress: 100,
  error_message: null,
  inputs: { sector: 'Diş kliniği', geography: 'İstanbul' },
  created_at: '2026-11-15T10:00:00Z',
  completed_at: '2026-11-15T10:05:00Z',
  audit_id: null,
  briefs: null,
  claim_overrides: [],
  usage: {},
  channels: { google_search: 'Google Arama Ağı', meta: 'Instagram / Facebook' },
  sections: {
    demand: { status: 'unavailable', reason: 'Google Ads bağlantısı tanımlı değil.' },
    sector: {
      status: 'ok',
      verified: true,
      definition: [{ text: 'Özel diş sağlığı', numbers_ok: null }],
      services: [{ text: 'Pazarda 99.000 klinik var', numbers_ok: false }],
      restrictions: [{ text: 'Ücretli öne çıkarma yasak', source_name: 'Yönetmelik' }],
      claims: [
        claim({}),
        claim({ id: 'sector:1', claim: 'Pazar büyüklüğü', check: { status: 'not_found' } }),
      ],
    },
    audience: {
      status: 'ok',
      needs: [
        {
          theme: 'Randevu bulma',
          quote: 'Randevu almak çok zor',
          source_name: 'Forum',
          quote_found: true,
        },
        {
          theme: 'Fiyat belirsizliği',
          quote: 'uydurma',
          source_name: 'Sözlük',
          quote_found: false,
        },
      ],
    },
    synthesis: {
      status: 'ok',
      segments: [
        {
          name: 'Acil hasta',
          need: 'Hızlı randevu',
          stage: 'karar',
          evidence: 'soru aramaları',
          assumption: 'gece arıyor',
          value_proposition: 'Aynı hafta randevu',
          messages: ['Bugün arayın'],
          warnings: [],
        },
      ],
    },
  },
}

const ok = <T,>(data: T) => Promise.resolve({ data }) as never

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockReturnValue(ok({ reports: [DETAIL] }))
  api.get.mockReturnValue(ok(DETAIL))
})

describe('digitalResearchView', () => {
  it('kaynakta geçen iddia girer, kısa rakam elle onayla girer, bulunamayan hiç girmez', () => {
    expect(claimInReport(claim({}), [])).toBe(true)
    expect(claimInReport(claim({}), ['sector:0'])).toBe(false)
    const short = claim({ check: { status: 'short' } })
    expect(claimInReport(short, [])).toBe(false)
    expect(claimInReport(short, ['sector:0'])).toBe(true)
    expect(claimChip(short, ['sector:0']).text).toBe('Elle onaylandı')
    expect(claimInReport(claim({ check: { status: 'not_found' } }), ['sector:0'])).toBe(false)
    expect(claimChip(claim({ check: { status: 'not_found' } }), []).tone).toBe('muted')
    expect(claimChip(claim({}), ['sector:0']).text).toBe('Rapordan çıkarıldı')
  })

  it('bağlamda yıl yoksa uyarır', () => {
    expect(yearNote(claim({}))).toBeNull()
    const noYear = claim({ check: { status: 'found', year_in_context: false } })
    expect(yearNote(noYear)).toContain('2023')
  })
})

describe('DigitalResearch', () => {
  it('doğrulanan ve doğrulanamayan rakamı ayrı gösterir', async () => {
    render(<DigitalResearch workspaceId={1} />)
    const found = (await screen.findByText('Diş hekimi sayısı')).closest('tr') as HTMLElement
    expect(within(found).getByText('Kaynakta geçiyor')).toBeInTheDocument()
    expect(within(found).getByText(/45 000 diş hekimi/)).toBeInTheDocument()
    const missing = screen.getByText('Pazar büyüklüğü').closest('tr') as HTMLElement
    expect(within(missing).getByText('Kaynakta bulunamadı')).toBeInTheDocument()
    expect(within(missing).getByText('Girmez')).toBeInTheDocument()
    expect(screen.getByText('Rakamı doğrulanamadı')).toBeInTheDocument()
  })

  it('birebir bulunamayan ifadeyi alıntı olarak göstermez', async () => {
    render(<DigitalResearch workspaceId={1} />)
    expect(await screen.findByText('Randevu almak çok zor')).toBeInTheDocument()
    expect(screen.queryByText('uydurma')).not.toBeInTheDocument()
    expect(screen.getByText('Birebir alıntı bulunamadı')).toBeInTheDocument()
  })

  it('Google Ads yoksa arama talebini veri yok olarak gösterir', async () => {
    render(<DigitalResearch workspaceId={1} />)
    expect(await screen.findByText(/Veri yok: Google Ads bağlantısı/)).toBeInTheDocument()
  })

  it('iddiayı rapordan çıkarır', async () => {
    api.saveClaimOverrides.mockReturnValue(ok({ claim_overrides: ['sector:0'] }))
    render(<DigitalResearch workspaceId={1} />)
    fireEvent.click(await screen.findByLabelText('Diş hekimi sayısı rapora girsin'))
    await waitFor(() => expect(api.saveClaimOverrides).toHaveBeenCalledWith(3, 1, ['sector:0']))
    expect(await screen.findByText('Rapordan çıkarıldı')).toBeInTheDocument()
  })

  it('segment ve kanal seçilmeden brief üretilemez', async () => {
    api.startBriefs.mockReturnValue(ok({ research_id: 3, status: 'pending' }))
    render(<DigitalResearch workspaceId={1} />)
    const button = await screen.findByRole('button', { name: /Brief üret/ })
    expect(button).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/Acil hasta/))
    fireEvent.click(screen.getByLabelText('Instagram / Facebook'))
    expect(button).toBeEnabled()
    fireEvent.click(button)
    await waitFor(() =>
      expect(api.startBriefs).toHaveBeenCalledWith(3, 1, { segments: [0], channels: ['meta'] })
    )
  })

  it('kısıtlı sektörde brief uyarısını gösterir', async () => {
    const note = 'Bu sektör için reklam/tanıtım kısıtı bulundu.'
    api.get.mockReturnValue(
      ok({
        ...DETAIL,
        briefs: {
          status: 'ok',
          ad_ideas: [
            {
              segment: 'Acil hasta',
              channel: 'Google Arama Ağı',
              insight: 'i',
              offer: 'o',
              message: 'Aynı hafta randevu',
              cta: 'Ara',
              test_hypothesis: 'h',
              warnings: ['%50 daha hızlı'],
              restriction_note: note,
            },
          ],
          creative_briefs: [],
        },
      })
    )
    render(<DigitalResearch workspaceId={1} />)
    expect(await screen.findByText(note)).toBeInTheDocument()
    expect(screen.getByText(/Kanıtı olmayan iddia: %50 daha hızlı/)).toBeInTheDocument()
  })

  it('araştırma yokken boş durum gösterir ve sektör yazılmadan başlatmaz', async () => {
    api.list.mockReturnValue(ok({ reports: [] }))
    render(<DigitalResearch workspaceId={1} />)
    expect(await screen.findByText(/henüz araştırma yok/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Araştırmayı başlat/ })).toBeDisabled()
  })
})
