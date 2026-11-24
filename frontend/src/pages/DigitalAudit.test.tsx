import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import DigitalAudit from './DigitalAudit'
import { buildSections, findingChip, findingSummary } from './digitalAuditView'
import { useBrandStore } from '../stores/brandStore'
import {
  digitalAuditApi,
  digitalResearchApi,
  downloadBlobResponse,
  type AuditFinding,
  type DigitalAuditDetail,
} from '../services/api'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    digitalAuditApi: { start: vi.fn(), list: vi.fn(), get: vi.fn(), saveAnswers: vi.fn() },
    digitalResearchApi: { ...actual.digitalResearchApi, exportReport: vi.fn() },
    downloadBlobResponse: vi.fn(),
  }
})

const api = vi.mocked(digitalAuditApi)

function finding(over: Partial<AuditFinding>): AuditFinding {
  return {
    section: 'contact',
    check: 'contact.phone',
    label: 'Telefon bağlantısı',
    state: 'observed',
    assessment: 'ok',
    value: { links: ['tel:+902120000000'] },
    note: null,
    source: null,
    limits: [],
    ...over,
  }
}

const DETAIL: DigitalAuditDetail = {
  id: 7,
  status: 'completed',
  progress: 100,
  error_message: null,
  created_at: '2026-11-14T10:00:00Z',
  completed_at: '2026-11-14T10:05:00Z',
  answers: {},
  questions: [{ key: 'crm', label: 'CRM var mı?', options: ['Evet', 'Hayır'] }],
  targets: [
    {
      id: 1,
      kind: 'own',
      url: 'https://www.marka.com/',
      status: 'completed',
      pages_checked: 10,
      error_message: null,
      findings: [finding({})],
    },
    {
      id: 2,
      kind: 'competitor',
      url: 'https://rakip.com/',
      status: 'blocked',
      pages_checked: 0,
      error_message: null,
      findings: [finding({ state: 'blocked', assessment: null, value: null, note: 'Okunamadı' })],
    },
  ],
}

function ok<T>(data: T) {
  return Promise.resolve({ data }) as never
}

beforeEach(() => {
  vi.clearAllMocks()
  useBrandStore.setState({
    activeWorkspace: {
      id: 3,
      name: 'Marka',
      company_url: 'https://www.marka.com/',
      status: 'confirmed',
      profile_data: null,
      suggested_keywords: null,
      competitor_urls: ['https://rakip.com/'],
    },
  })
})

describe('findingChip', () => {
  it('engellenen ya da okunamayan kontrolü yok/sorun gibi göstermez', () => {
    expect(findingChip(finding({ state: 'blocked', assessment: null }))).toEqual({
      text: 'Engellendi',
      tone: 'muted',
    })
    expect(findingChip(finding({ state: 'unknown', assessment: null })).text).toBe('Bilinmiyor')
    expect(findingChip(undefined).tone).toBe('muted')
  })

  it('gözlenmeyeni yalnız değerlendirme uyarıysa uyarı tonunda verir', () => {
    expect(findingChip(finding({ state: 'not_observed', assessment: null })).tone).toBe('neutral')
    expect(findingChip(finding({ state: 'not_observed', assessment: 'warning' })).tone).toBe(
      'warning'
    )
  })

  it('gözlenen bulguyu değerlendirmesine göre etiketler', () => {
    expect(findingChip(finding({ assessment: 'problem' })).text).toBe('Sorun')
    expect(findingChip(finding({ assessment: 'info' })).text).toBe('Görüldü')
  })
})

describe('findingSummary', () => {
  it('değer yoksa boş döner', () => {
    expect(findingSummary(finding({ state: 'blocked', value: null }))).toBe('')
  })

  it('bilinen kontrolleri okunur özetler', () => {
    expect(findingSummary(finding({}))).toBe('+902120000000')
    expect(
      findingSummary(
        finding({ check: 'seo.titles', value: { missing: 1, duplicate: 0, too_long: 2 } })
      )
    ).toBe('1 eksik, 2 uzun')
    expect(
      findingSummary(finding({ check: 'seo.sitemap', value: { urls: 200, capped: true } }))
    ).toBe('200+ adres')
  })
})

describe('buildSections', () => {
  it('her hedef için aynı satırda hücre üretir', () => {
    const [section] = buildSections(DETAIL.targets)
    expect(section.section).toBe('contact')
    expect(section.rows[0].cells.map((c) => c?.state)).toEqual(['observed', 'blocked'])
  })
})

describe('DigitalAudit sayfası', () => {
  it('workspace yoksa seçim ister', () => {
    useBrandStore.setState({ activeWorkspace: null })
    render(<DigitalAudit />)
    expect(screen.getByText(/önce bir marka çalışması seçin/)).toBeInTheDocument()
    expect(api.list).not.toHaveBeenCalled()
  })

  it('son denetimi yükler ve engellenen rakibi "Engellendi" olarak gösterir', async () => {
    api.list.mockReturnValue(ok({ audits: [DETAIL] }))
    api.get.mockReturnValue(ok(DETAIL))
    render(<DigitalAudit />)

    const row = (await screen.findByText('Telefon bağlantısı')).closest('tr') as HTMLElement
    const cells = within(row).getAllByRole('cell')
    expect(cells[0]).toHaveTextContent('İyi')
    expect(cells[1]).toHaveTextContent('Engellendi')
    expect(cells[1]).not.toHaveTextContent('Görülmedi')
    expect(screen.getByText('Site erişimi engelledi')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith(7, 3)
  })

  it('denetim raporunu seçili denetimle indirir', async () => {
    api.list.mockReturnValue(ok({ audits: [DETAIL] }))
    api.get.mockReturnValue(ok(DETAIL))
    vi.mocked(digitalResearchApi.exportReport).mockReturnValue(ok(new Blob()))
    render(<DigitalAudit />)

    fireEvent.click(await screen.findByRole('button', { name: /Denetim raporu \(PDF\)/ }))
    await waitFor(() => expect(downloadBlobResponse).toHaveBeenCalled())
    expect(digitalResearchApi.exportReport).toHaveBeenCalledWith(3, { format: 'pdf', audit_id: 7 })
  })

  it('formu workspace bilgisiyle doldurur ve denetimi başlatır', async () => {
    api.list.mockReturnValue(ok({ audits: [] }))
    api.start.mockReturnValue(ok({ audit_id: 7, status: 'pending', reused: false }))
    api.get.mockReturnValue(ok({ ...DETAIL, status: 'pending', progress: 0 }))
    render(<DigitalAudit />)

    await screen.findByText(/henüz denetim yok/)
    expect(screen.getByLabelText('Marka sitesi')).toHaveValue('https://www.marka.com/')
    expect(screen.getByLabelText('Rakip 1')).toHaveValue('https://rakip.com/')
    fireEvent.click(screen.getByRole('button', { name: /Denetimi başlat/ }))

    await waitFor(() =>
      expect(api.start).toHaveBeenCalledWith(3, {
        own_url: 'https://www.marka.com/',
        competitor_urls: ['https://rakip.com/'],
      })
    )
    expect(await screen.findByRole('button', { name: /Denetim sürüyor/ })).toBeDisabled()
  })

  it('soru formu cevaplarını kaydeder', async () => {
    api.list.mockReturnValue(ok({ audits: [DETAIL] }))
    api.get.mockReturnValue(ok(DETAIL))
    api.saveAnswers.mockReturnValue(ok({ answers: { crm: { value: 'Evet', note: null } } }))
    render(<DigitalAudit />)

    fireEvent.change(await screen.findByLabelText('CRM var mı?'), { target: { value: 'Evet' } })
    fireEvent.click(screen.getByRole('button', { name: /Cevapları kaydet/ }))
    await waitFor(() =>
      expect(api.saveAnswers).toHaveBeenCalledWith(7, 3, { crm: { value: 'Evet' } })
    )
  })

  it('brifi ayrı gösterir; kanıt girilen cevabı Doğrulandı, girilmeyeni Beyan etiketler', async () => {
    const detail = {
      ...DETAIL,
      questions: [
        { key: 'business_model', label: 'İş modeli nedir?', options: null, group: 'brief' },
        { key: 'crm', label: 'CRM var mı?', options: ['Evet', 'Hayır'], group: 'account' },
      ],
      answers: { crm: { value: 'Evet', note: null } },
    }
    api.list.mockReturnValue(ok({ audits: [detail] }))
    api.get.mockReturnValue(ok(detail))
    api.saveAnswers.mockReturnValue(ok({ answers: detail.answers }))
    render(<DigitalAudit />)

    expect(await screen.findByRole('heading', { name: 'Analist brifi' })).toBeInTheDocument()
    expect(screen.getByText('Beyan')).toBeInTheDocument()
    // Brif sorusunda kanıt alanı yok
    expect(screen.queryByLabelText(/İş modeli nedir\? Kaynak/)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/CRM var mı\? Kaynak/), {
      target: { value: 'HubSpot paneli' },
    })
    expect(screen.getByText('Analist doğruladı')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Cevapları kaydet/ }))
    await waitFor(() =>
      expect(api.saveAnswers).toHaveBeenCalledWith(7, 3, {
        crm: { value: 'Evet', note: null, evidence: { source: 'HubSpot paneli' } },
      })
    )
  })
})
