/**
 * SEO "Revize Et": talep penceresi (kapsam + idempotency), kartta hazırlanıyor /
 * önizleme (önce→sonra, Kabul Et / Vazgeç) / başarısız durumu, kart içi hata ve
 * güncel olmayan içerikte pasif buton.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import SeoGeoPanel from './SeoGeoPanel'
import { useBrandStore } from '../../stores/brandStore'
import type { SEOGeoItem, SeoPendingRevision } from '../../types/models'

const mocks = vi.hoisted(() => ({
  listSeoGeo: vi.fn(),
  reviseSeoGeo: vi.fn(),
  acceptSeoRevision: vi.fn(),
  discardSeoRevision: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    generationApi: {
      ...actual.generationApi,
      listSeoGeo: mocks.listSeoGeo,
      reviseSeoGeo: mocks.reviseSeoGeo,
      acceptSeoRevision: mocks.acceptSeoRevision,
      discardSeoRevision: mocks.discardSeoRevision,
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

const baseItem: SEOGeoItem = {
  id: 7,
  keyword: 'hisse takip',
  title: 'Eski Başlık',
  seo_score: 0.6,
  geo_score: 1,
  combined_score: 0.8,
  intro_paragraph: 'Eski giriş.',
}

const listWith = (item: SEOGeoItem) => ({ data: { items: [item] } })

const readyRevision: SeoPendingRevision = {
  id: 31,
  status: 'ready',
  feedback: 'Başlığı daha samimi yap',
  scope: ['title_meta'],
  changes: [
    { field: 'title', label: 'Başlık', before: 'Eski Başlık', after: 'Yeni Samimi Başlık' },
  ],
  scores: { seo_before: 0.6, geo_before: 1, seo_after: 0.7, geo_after: 1 },
  publish_review: null,
}

describe('SeoGeoPanel — Revize Et', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.values(mocks).forEach((m) => m.mockReset())
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
  afterEach(cleanup)

  it('pencere SEO kapsamlarıyla gönderir; hata sonrası tekrar aynı idempotency anahtarı', async () => {
    mocks.listSeoGeo.mockResolvedValue(listWith(baseItem))
    mocks.reviseSeoGeo
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: {} } })
      .mockResolvedValueOnce({ data: { revision_id: 31, status: 'generating', created: true } })
    render(<SeoGeoPanel runId={1} />)

    fireEvent.click(await screen.findByRole('button', { name: /Revize Et/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText('Başlık ve meta')).toBeTruthy()
    fireEvent.click(within(dialog).getByLabelText('Gövde (alt başlıklar, bölümler, SSS)'))
    fireEvent.change(within(dialog).getByLabelText('Neyi değiştirelim?'), {
      target: { value: '  Girişi kısalt  ' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    expect(await within(dialog).findByRole('alert')).toBeTruthy()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    await waitFor(() => expect(mocks.reviseSeoGeo).toHaveBeenCalledTimes(2))
    const [first, second] = mocks.reviseSeoGeo.mock.calls
    expect(first[0]).toBe(7)
    expect(first[2]).toMatchObject({
      feedback: 'Girişi kısalt',
      scope: ['title_meta', 'intro', 'body'],
    })
    expect(second[2].idempotency_key).toBe(first[2].idempotency_key)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('talep değiştirilip yeniden gönderilirse yeni idempotency anahtarı kullanılır', async () => {
    mocks.listSeoGeo.mockResolvedValue(listWith(baseItem))
    mocks.reviseSeoGeo
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: {} } })
      .mockResolvedValueOnce({ data: { revision_id: 32, status: 'generating', created: true } })
    render(<SeoGeoPanel runId={1} />)
    fireEvent.click(await screen.findByRole('button', { name: /Revize Et/ }))
    const dialog = screen.getByRole('dialog')
    const text = within(dialog).getByLabelText('Neyi değiştirelim?')
    fireEvent.change(text, { target: { value: 'Girişi kısalt' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    expect(await within(dialog).findByRole('alert')).toBeTruthy()
    fireEvent.change(text, { target: { value: 'Girişi kısalt ve yılı ekle' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))
    await waitFor(() => expect(mocks.reviseSeoGeo).toHaveBeenCalledTimes(2))
    const [first, second] = mocks.reviseSeoGeo.mock.calls
    expect(second[2].idempotency_key).not.toBe(first[2].idempotency_key)
  })

  it('hazır öneri önce→sonra gösterilir; canlı başlık değişmez; Kabul Et uygular', async () => {
    mocks.listSeoGeo.mockResolvedValue(listWith({ ...baseItem, pending_revision: readyRevision }))
    mocks.acceptSeoRevision.mockResolvedValue({ data: { status: 'accepted' } })
    render(<SeoGeoPanel runId={1} />)

    const preview = await screen.findByRole('region', { name: 'Revizyon önerisi' })
    expect(within(preview).getByText('Yeni Samimi Başlık')).toBeTruthy()
    expect(within(preview).getByText(/SEO 60% → 70%/)).toBeTruthy()
    expect(screen.getAllByText('Eski Başlık').length).toBeGreaterThan(0) // kart hâlâ canlı içerik
    fireEvent.click(within(preview).getByRole('button', { name: 'Kabul Et' }))
    await waitFor(() => expect(mocks.acceptSeoRevision).toHaveBeenCalledWith(31, 10))
    await waitFor(() => expect(mocks.listSeoGeo.mock.calls.length).toBeGreaterThan(1))
  })

  it('kabul hatası ilgili kartta görünür', async () => {
    mocks.listSeoGeo.mockResolvedValue(listWith({ ...baseItem, pending_revision: readyRevision }))
    mocks.acceptSeoRevision.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'İçerik bu öneri hazırlandıktan sonra değişti.' } },
    })
    render(<SeoGeoPanel runId={1} />)
    const preview = await screen.findByRole('region', { name: 'Revizyon önerisi' })
    // Sunucu öneriyi kapattı (409): yenilenen listede öneri yok, hata kartta kalmalı
    mocks.listSeoGeo.mockResolvedValue(listWith(baseItem))
    fireEvent.click(within(preview).getByRole('button', { name: 'Kabul Et' }))
    expect(await screen.findByText(/İçerik bu öneri hazırlandıktan sonra değişti/)).toBeTruthy()
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Revizyon önerisi' })).toBeNull()
    )
    expect(screen.getByText(/İçerik bu öneri hazırlandıktan sonra değişti/)).toBeTruthy()
  })

  it('hazırlanırken durum + İptal; başarısızda mesaj + Kapat (iptal)', async () => {
    mocks.listSeoGeo.mockResolvedValue(
      listWith({ ...baseItem, pending_revision: { ...readyRevision, status: 'generating' } })
    )
    mocks.discardSeoRevision.mockResolvedValue({ data: { status: 'discarded' } })
    const { unmount } = render(<SeoGeoPanel runId={1} />)
    expect(await screen.findByText(/Talebinize göre revize ediliyor/)).toBeTruthy()
    expect((screen.getByRole('button', { name: /Revize Et/ }) as HTMLButtonElement).disabled).toBe(
      true
    )
    fireEvent.click(screen.getByRole('button', { name: 'İptal et' }))
    await waitFor(() => expect(mocks.discardSeoRevision).toHaveBeenCalledWith(31, 10))
    unmount()

    mocks.listSeoGeo.mockResolvedValue(
      listWith({
        ...baseItem,
        pending_revision: { ...readyRevision, status: 'failed', error_message: 'Zaman aşımı.' },
      })
    )
    render(<SeoGeoPanel runId={1} />)
    expect(await screen.findByText(/Revizyon başarısız: Zaman aşımı\./)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Kapat' }))
    await waitFor(() => expect(mocks.discardSeoRevision).toHaveBeenCalledTimes(2))
  })

  it('güncel olmayan içerikte buton pasif ve nedeni yazılı', async () => {
    mocks.listSeoGeo.mockResolvedValue(listWith({ ...baseItem, is_stale: true }))
    render(<SeoGeoPanel runId={1} />)
    const btn = (await screen.findByRole('button', { name: /Revize Et/ })) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(
      screen.getByText(/güncel değil \(kanal ataması yenilendi\); revize edilemez/)
    ).toBeTruthy()
  })
  it('revize edilmiş içerikte kabul tarihi gösterilir; üretim tarihi ipucunda kalır', async () => {
    mocks.listSeoGeo.mockResolvedValue(
      listWith({
        ...baseItem,
        created_at: '2026-10-31T19:16:00Z',
        revised_at: '2026-11-12T09:30:00Z',
        revision_count: 1,
      })
    )
    render(<SeoGeoPanel runId={1} />)
    const stamp = await screen.findByText(/Revize: 12\.11\.2026/)
    expect(stamp.closest('span')?.getAttribute('title')).toBe('Üretim: 31.10.2026')
  })
  const hierarchyReview = {
    required: true,
    status: 'review_required' as const,
    label: 'Yayın öncesi inceleme gerekli',
    critical_failures: [
      {
        criterion: 'info_hierarchy_strong',
        source: 'geo_ai' as const,
        detail: 'İlk ~200 kelime konuyu özetlemiyor (AI değerlendirmesi)',
      },
    ],
    manual_checks: [],
  }

  it('kartta kayıtlı değerlendirici notu gösterilir', async () => {
    mocks.listSeoGeo.mockResolvedValue(
      listWith({
        ...baseItem,
        publish_review: hierarchyReview,
        geo_notes: 'Sondaki tekrar eden madde listesi kaldırılmalı.',
      })
    )
    render(<SeoGeoPanel runId={1} />)
    expect(await screen.findByText('Sondaki tekrar eden madde listesi kaldırılmalı.')).toBeTruthy()
  })

  it('seçilen kapsam sorunu düzeltmeye yetmiyorsa pencere uyarır; Gövde seçilince kalkar', async () => {
    mocks.listSeoGeo.mockResolvedValue(listWith({ ...baseItem, publish_review: hierarchyReview }))
    render(<SeoGeoPanel runId={1} />)
    fireEvent.click(await screen.findByRole('button', { name: /Revize Et/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Giriş ve Gövde seçilmezse düzelmeyebilir/)).toBeTruthy()
    fireEvent.click(within(dialog).getByLabelText('Gövde (alt başlıklar, bölümler, SSS)'))
    expect(within(dialog).queryByText(/Giriş ve Gövde seçilmezse/)).toBeNull()
  })

  it('önizlemede önceden geçip bozulan kontroller listelenir', async () => {
    mocks.listSeoGeo.mockResolvedValue(
      listWith({
        ...baseItem,
        pending_revision: {
          ...readyRevision,
          regressions: [
            {
              criterion: 'intro_keyword_count',
              label: 'Girişte anahtar kelime (≥2)',
              channel: 'SEO',
              before: 'Geçti',
              after: 'Kısmi',
              after_detail: 'Giriş paragrafında keyword: 1 kez (min 2)',
            },
          ],
        },
      })
    )
    render(<SeoGeoPanel runId={1} />)
    const note = await screen.findByRole('note', { name: 'Bozulan kontroller' })
    expect(within(note).getByText(/Girişte anahtar kelime \(≥2\): Geçti → Kısmi/)).toBeTruthy()
  })
})
