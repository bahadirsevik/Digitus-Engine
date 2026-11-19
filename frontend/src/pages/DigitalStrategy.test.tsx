import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import DigitalStrategy from './DigitalStrategy'
import {
  digitalResearchApi,
  type DigitalResearchDetail,
  type StrategyContent,
} from '../services/api'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    downloadBlobResponse: vi.fn(),
    digitalResearchApi: {
      startStrategy: vi.fn(),
      saveStrategy: vi.fn(),
      approveStrategy: vi.fn(),
      exportReport: vi.fn(),
    },
  }
})

const api = vi.mocked(digitalResearchApi)
const ok = <T,>(data: T) => Promise.resolve({ data }) as never

const HELD = {
  action: 'Arama reklamı aç',
  period: '30-60',
  priority: 'P2',
  publication: 'sponsorlu',
  channel: 'google_search',
  refs: ['B2'],
}
const CONTENT: StrategyContent = {
  summary: [{ finding: 'Başlıklar eksik', meaning: 'Görünürlük kaybı', refs: ['B1'] }],
  section_notes: [],
  competition: [],
  opportunities: [],
  decisions: [
    {
      question: 'Ücretli reklam başlatılsın mı?',
      needed_info: 'Uygunluk',
      owner_role: 'Analist ve müşteri',
      refs: ['B2'],
      held_action: HELD,
      ad_status: 'koşullu',
    },
  ],
  actions: [
    {
      action: 'Başlıkları tamamla',
      period: '0-30',
      priority: 'P1',
      owner_role: 'SEO',
      refs: ['B1'],
    },
  ],
  swot: { strengths: [], weaknesses: [], opportunities: [], threats: [] },
  dropped: 1,
}

function detail(over: Partial<DigitalResearchDetail> = {}): DigitalResearchDetail {
  return {
    id: 3,
    status: 'completed',
    stage: 'strategy',
    progress: 100,
    error_message: null,
    inputs: {},
    created_at: '2026-11-19T10:00:00Z',
    completed_at: '2026-11-19T10:05:00Z',
    audit_id: 4,
    briefs: null,
    claim_overrides: [],
    usage: {},
    channels: {},
    strategy_state: 'draft',
    sections: {
      synthesis: { status: 'ok', segments: [] },
      strategy: {
        status: 'ok',
        version: 2,
        revision: 5,
        generated_at: '2026-11-19T10:05:00Z',
        catalogue: [
          { id: 'B1', kind: 'Ölçüm (marka sitesi)', text: 'Sayfa başlıkları: Uyarı' },
          { id: 'B2', kind: 'Hipotez (segment)', text: 'Acil hasta' },
        ],
        gaps: ['Arama görünürlüğü ölçülmedi.'],
        ad_base_status: 'koşullu',
        draft: CONTENT,
        edited: null,
        approval: null,
      },
    },
    ...over,
  }
}

function show(d: DigitalResearchDetail) {
  const onDetail = vi.fn()
  const guarded = async (_: string, action: () => Promise<unknown>) => {
    await action()
  }
  render(
    <DigitalStrategy
      detail={d}
      workspaceId={1}
      running={false}
      busy={false}
      guarded={guarded}
      onDetail={onDetail}
      reload={vi.fn()}
    />
  )
  return onDetail
}

describe('DigitalStrategy', () => {
  beforeEach(() => vi.clearAllMocks())

  it('düzeltmeyi sürümle kaydeder; kaydedilmemiş düzeltme varken onay ve indirme kapalıdır', async () => {
    api.saveStrategy.mockReturnValue(ok(detail()))
    show(detail())
    expect(screen.getByText('Taslak: analist onayı yok')).toBeInTheDocument()
    expect(screen.getByText('Arama görünürlüğü ölçülmedi.')).toBeInTheDocument()
    expect(screen.getAllByText('Yeterli kanıt yok; madde üretilmedi.').length).toBeGreaterThan(0)

    fireEvent.change(screen.getByLabelText('İnceleyen analist'), { target: { value: 'Analist A' } })
    const approve = screen.getByRole('button', { name: /İncelendi olarak işaretle/ })
    expect(approve).toBeEnabled()
    fireEvent.change(screen.getByLabelText('90 günlük aksiyon planı 1 Sorumlu rol'), {
      target: { value: 'Ajans SEO ekibi' },
    })
    expect(approve).toBeDisabled()
    expect(screen.getByRole('button', { name: /Yönetici raporu \(PDF\)/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /Düzeltmeleri kaydet/ }))
    await waitFor(() => expect(api.saveStrategy).toHaveBeenCalled())
    const [id, ws, version, revision, content] = api.saveStrategy.mock.calls[0]
    expect([id, ws, version, revision]).toEqual([3, 1, 2, 5])
    expect(content.actions[0].owner_role).toBe('Ajans SEO ekibi')
  })

  it('bekletilen ücretli reklam yalnız analist dayanağıyla plana alınır', async () => {
    api.saveStrategy.mockReturnValue(ok(detail()))
    show(detail())
    const release = screen.getByRole('button', { name: 'Plana al' })
    expect(release).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Karar 1 analist dayanağı'), {
      target: { value: 'Hukuk görüşü 12.11.2026' },
    })
    fireEvent.click(release)
    fireEvent.click(screen.getByRole('button', { name: /Düzeltmeleri kaydet/ }))
    await waitFor(() => expect(api.saveStrategy).toHaveBeenCalled())
    const content = api.saveStrategy.mock.calls[0][4]
    expect(content.decisions).toHaveLength(0)
    expect(content.actions[1]).toMatchObject({
      action: 'Arama reklamı aç',
      ad_status: 'önerilebilir',
      ad_basis: 'Hukuk görüşü 12.11.2026',
    })
  })

  it('uyarısı duran madde varken onay kapalıdır; sınıflandırma notu görünür', () => {
    const warnedContent: StrategyContent = {
      ...CONTENT,
      summary: [{ ...CONTENT.summary[0], warnings: ['Dayanak “görülmedi” ölçümüdür'] }],
      actions: [{ ...CONTENT.actions[0], kind: 'seo', class_note: 'Kanal kaldırıldı.' }],
    }
    const d = detail()
    d.sections.strategy!.draft = warnedContent
    show(d)
    fireEvent.change(screen.getByLabelText('İnceleyen analist'), { target: { value: 'Analist A' } })
    expect(screen.getByRole('button', { name: /İncelendi olarak işaretle/ })).toBeDisabled()
    expect(screen.getByText(/Uyarısı duran 1 madde var/)).toBeInTheDocument()
    expect(screen.getByText(/Sınıflandırma notu: Kanal kaldırıldı/)).toBeInTheDocument()
    expect(screen.getByLabelText('90 günlük aksiyon planı 1 Tür')).toHaveValue('seo')
  })

  it('güncel olmayan taslağı düzeltmeye açmaz; belge araştırmanın kendi denetimiyle indirilir', async () => {
    api.exportReport.mockReturnValue(ok(new Blob()))
    show(detail({ strategy_state: 'stale' }))
    expect(screen.getByText(/Bu taslak ve varsa analist onayı güncel değil/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Düzeltmeleri kaydet/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Uygulama belgesi \(Word\)/ }))
    await waitFor(() =>
      expect(api.exportReport).toHaveBeenCalledWith(1, {
        format: 'docx',
        research_id: 3,
        view: 'implementation',
      })
    )
  })
})
