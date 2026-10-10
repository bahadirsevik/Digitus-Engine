/**
 * Plan v4 (export dağıtımı) testleri:
 * - jobTypeLabel tür etiketleri (sections+format → "Tam rapor · Excel")
 * - ExportMenu: havuz Excel indirmesi, job create→poll→completed→İndir,
 *   409 POLICY_STALE / 422 NO_CONTENT mesajları, geçmişin menü açılışında gelmesi
 * - /export route'u Keywords'e yönlenir (query korunur), nav'da Dışa Aktarım yok
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import ExportMenu from './ExportMenu'
import { jobTypeLabel } from './exportLabels'
import RedirectWithParams from './RedirectWithParams'
import Layout from './Layout'
import { useBrandStore } from '../stores/brandStore'

const mocks = vi.hoisted(() => ({
  exportCreate: vi.fn(),
  exportStatus: vi.fn(),
  exportDownload: vi.fn(),
  exportListForRun: vi.fn(),
  channelPoolXlsx: vi.fn(),
}))

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    exportApi: {
      create: mocks.exportCreate,
      status: mocks.exportStatus,
      download: mocks.exportDownload,
      listForRun: mocks.exportListForRun,
    },
    channelsApi: {
      ...actual.channelsApi,
      exportPoolXlsx: mocks.channelPoolXlsx,
    },
  }
})

function setWorkspace() {
  useBrandStore.setState({
    activeWorkspace: {
      id: 3,
      name: 'Test WS',
      company_url: 'https://example.com',
      status: 'confirmed',
      profile_data: null,
      suggested_keywords: null,
    },
  })
}

describe('jobTypeLabel', () => {
  it('sections+format birleşiminden etiket üretir', () => {
    expect(jobTypeLabel({ sections: ['all'], format: 'excel' })).toBe('Tam rapor · Excel')
    expect(jobTypeLabel({ sections: ['ads'], format: 'docx' })).toBe('ADS içerikleri · Word')
    expect(jobTypeLabel({ sections: ['seo_content'], format: 'pdf' })).toBe(
      'SEO+GEO içerikleri · PDF'
    )
    expect(jobTypeLabel({ sections: ['social'], format: 'excel' })).toBe(
      'Social içerikleri · Excel'
    )
  })

  it('all diğer bölümlere baskındır; bilinmeyen bölüm/formatta kırılmaz', () => {
    expect(jobTypeLabel({ sections: ['ads', 'all'], format: 'excel' })).toBe('Tam rapor · Excel')
    expect(jobTypeLabel({ sections: ['mystery'], format: null })).toBe('Rapor')
    expect(jobTypeLabel({})).toBe('Rapor')
  })
})

describe('ExportMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setWorkspace()
    mocks.exportListForRun.mockResolvedValue({ data: { exports: [], total: 0 } })
  })

  afterEach(() => {
    cleanup()
  })

  it('run yokken tetikleyici disabled olur', async () => {
    render(<ExportMenu channel="ADS" runId={null} />)
    expect((screen.getByRole('button', { name: /İndir/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('kanal havuzu satırı senkron XLSX indirir', async () => {
    mocks.channelPoolXlsx.mockResolvedValue({ data: new Blob(['x']), headers: {} })
    const createObjectURL = vi.fn(() => 'blob:x')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })

    render(<ExportMenu channel="SEO" runId={7} />)
    fireEvent.click(screen.getByRole('button', { name: /İndir/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Kanal havuzu/ }))

    await waitFor(() => expect(mocks.channelPoolXlsx).toHaveBeenCalledWith(7, 'SEO', 3))
    expect(createObjectURL).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('içerik job akışı: create → poll → completed → İndir', async () => {
    mocks.exportCreate.mockResolvedValue({
      data: {
        export_id: 'job-1',
        status: 'pending',
        progress: 0,
        sections: ['seo_content'],
        format: 'docx',
      },
    })
    mocks.exportStatus.mockResolvedValue({
      data: {
        export_id: 'job-1',
        status: 'completed',
        progress: 100,
        file_name: 'rapor.docx',
        sections: ['seo_content'],
        format: 'docx',
      },
    })
    mocks.exportDownload.mockResolvedValue({ data: new Blob(['d']), headers: {} })
    const createObjectURL = vi.fn(() => 'blob:y')
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() })

    render(<ExportMenu channel="SEO" runId={7} />)
    fireEvent.click(screen.getByRole('button', { name: /İndir/ }))
    const wordButtons = await screen.findAllByRole('button', { name: 'Word' })
    fireEvent.click(wordButtons[0]) // İçerikler satırı

    await waitFor(() =>
      expect(mocks.exportCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          scoring_run_id: 7,
          format: 'docx',
          sections: ['seo_content'],
        }),
        3
      )
    )

    // Poll aralığı (2sn) gerçek zamanla dolana kadar bekle — job completed olur
    await waitFor(() => expect(mocks.exportStatus).toHaveBeenCalledWith('job-1', 3), {
      timeout: 4000,
    })
    await waitFor(() =>
      expect(screen.getAllByText('SEO+GEO içerikleri · Word').length).toBeGreaterThan(0)
    )

    // Kullanıcının başlattığı job tamamlanınca OTOMATİK indirilir —
    // ikinci bir İndir tıklaması gerekmez (codex post-review #3)
    await waitFor(() => expect(mocks.exportDownload).toHaveBeenCalledWith('job-1', 3))
    vi.unstubAllGlobals()
  }, 10000)

  it('geçmişteki süren job devralınır ve poll edilir', async () => {
    mocks.exportListForRun.mockResolvedValue({
      data: {
        exports: [
          {
            export_id: 'resume-1',
            status: 'processing',
            progress: 40,
            sections: ['ads'],
            format: 'excel',
          },
        ],
        total: 1,
      },
    })
    mocks.exportStatus.mockResolvedValue({
      data: {
        export_id: 'resume-1',
        status: 'completed',
        progress: 100,
        sections: ['ads'],
        format: 'excel',
      },
    })

    render(<ExportMenu channel="ADS" runId={4} />)
    // Sayfaya dönüşte poll devreye girer (codex post-review #2)
    await waitFor(() => expect(mocks.exportStatus).toHaveBeenCalledWith('resume-1', 3), {
      timeout: 4000,
    })
    // Devralınan job'da SÜRPRİZ otomatik indirme olmaz
    expect(mocks.exportDownload).not.toHaveBeenCalled()
  }, 10000)

  it('menü kapat-aç geçmişi yeniden ister', async () => {
    render(<ExportMenu channel="SEO" runId={7} />)
    await waitFor(() => expect(mocks.exportListForRun).toHaveBeenCalledTimes(1))

    const trigger = screen.getByRole('button', { name: /İndir/ })
    fireEvent.click(trigger) // aç
    await waitFor(() => expect(mocks.exportListForRun).toHaveBeenCalledTimes(2))
    fireEvent.click(trigger) // kapat — istek atmaz
    fireEvent.click(trigger) // tekrar aç
    await waitFor(() => expect(mocks.exportListForRun).toHaveBeenCalledTimes(3))
  })

  it('422 NO_CONTENT detayı kendi mesajıyla gösterilir', async () => {
    mocks.exportCreate.mockRejectedValue({
      status: 422,
      detail: { code: 'NO_CONTENT', message: 'Bu kanal için üretilmiş içerik yok.' },
    })
    render(<ExportMenu channel="ADS" runId={7} />)
    fireEvent.click(screen.getByRole('button', { name: /İndir/ }))
    const excelButtons = await screen.findAllByRole('button', { name: 'Excel' })
    fireEvent.click(excelButtons[0])
    expect(await screen.findByText(/üretilmiş içerik yok/)).toBeTruthy()
  })

  it('409 POLICY_STALE havuz indirmesinde kendi mesajıyla gösterilir', async () => {
    mocks.channelPoolXlsx.mockRejectedValue({
      status: 409,
      detail: {
        code: 'POLICY_STALE',
        message: 'Kanal havuzu güncel değil — kanal atamasını yenileyin.',
        channel_pool_stale: true,
        policy_stale: true,
        relevance_stale: false,
      },
    })
    render(<ExportMenu channel="ADS" runId={7} />)
    fireEvent.click(screen.getByRole('button', { name: /İndir/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Kanal havuzu/ }))
    expect(await screen.findByText(/güncel değil/)).toBeTruthy()
  })

  it('menü açılınca geçmiş listeden gelir (tür etiketli)', async () => {
    mocks.exportListForRun.mockResolvedValue({
      data: {
        exports: [
          {
            export_id: 'old-1',
            status: 'completed',
            progress: 100,
            sections: ['all'],
            format: 'excel',
            policy_outdated: true,
          },
        ],
        total: 1,
      },
    })
    render(<ExportMenu channel="SOCIAL" runId={9} />)
    await waitFor(() => expect(mocks.exportListForRun).toHaveBeenCalledWith(9, 3, 5))
    fireEvent.click(screen.getByRole('button', { name: /İndir/ }))
    expect(await screen.findByText('Tam rapor · Excel')).toBeTruthy()
    expect(screen.getByText('eski politika')).toBeTruthy()
  })
})

describe('/export route yönlendirmesi + nav', () => {
  afterEach(() => cleanup())

  function LocationProbe() {
    const location = useLocation()
    return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
  }

  it('/export?run_id=5 → /keywords?run_id=5 (query korunur)', () => {
    render(
      <MemoryRouter initialEntries={['/export?run_id=5']}>
        <Routes>
          <Route path="/export" element={<RedirectWithParams to="/keywords" />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByTestId('location').textContent).toBe('/keywords?run_id=5')
  })

  it("nav'da Dışa Aktarım kalemi yok", () => {
    render(
      <MemoryRouter>
        <Layout>
          <div />
        </Layout>
      </MemoryRouter>
    )
    expect(screen.queryByText('Disa Aktarim')).toBeNull()
    expect(screen.getByText('Anahtar Kelimeler')).toBeTruthy()
  })
})
