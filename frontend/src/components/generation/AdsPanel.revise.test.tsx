/**
 * ADS "Revize Et": talep penceresi, çift tıklama kilidi, hata sonrası talebin
 * korunması, hedef kartta ilerleme, taslak açıklaması ve pasif buton nedeni.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import AdsPanel from './AdsPanel'
import { useBrandStore } from '../../stores/brandStore'

const mocks = vi.hoisted(() => ({
  getAdsRsa: vi.fn(),
  getAdsSets: vi.fn(),
  regenerateAdGroup: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    brandProfileApi: {
      ...actual.brandProfileApi,
      getProfile: vi.fn(() => Promise.reject(new Error('no profile'))),
    },
    generationApi: {
      ...actual.generationApi,
      getAdsRsa: mocks.getAdsRsa,
      getAdsSets: mocks.getAdsSets,
      regenerateAdGroup: mocks.regenerateAdGroup,
    },
    tasksApi: { ...actual.tasksApi, getStatus: mocks.getStatus },
  }
})

const activeSet = {
  id: 3,
  scoring_run_id: 1,
  version_number: 1,
  status: 'active',
  is_stale: false,
  groups_count: 1,
}
const group = { id: 5, group_name: 'Laptop', headlines: [], descriptions: [] }

const resultFor = (generation_set: object) => ({
  data: { total_groups: 1, ad_groups: [group], generation_set },
})

function openDialog() {
  return screen.findByRole('button', { name: /Revize Et/ }).then((b) => fireEvent.click(b))
}

describe('AdsPanel — Revize Et', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.values(mocks).forEach((m) => m.mockReset())
    mocks.getAdsRsa.mockResolvedValue(resultFor(activeSet))
    mocks.getAdsSets.mockResolvedValue({
      data: { scoring_run_id: 1, sets: [activeSet], active_set_id: 3 },
    })
    mocks.getStatus.mockImplementation((taskId: string) =>
      Promise.resolve({
        data: { task_id: taskId, status: 'running', progress: 20, scoring_run_id: 1 },
      })
    )
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

  afterEach(() => cleanup())

  it('boş/çok kısa talep gönderilemez; geçerli talep kapsamla birlikte gider', async () => {
    mocks.regenerateAdGroup.mockResolvedValue({ data: { task_id: 't1', generation_set_id: 4 } })
    render(<AdsPanel runId={1} />)
    await openDialog()
    const dialog = screen.getByRole('dialog')
    const submit = within(dialog).getByRole('button', { name: 'Revize Et' })
    const text = within(dialog).getByLabelText('Neyi değiştirelim?')

    fireEvent.change(text, { target: { value: '    ' } })
    expect((submit as HTMLButtonElement).disabled).toBe(true)

    // Varsayılan kapsam: başlık + açıklama; negatif seçilince sıralı gider
    fireEvent.click(within(dialog).getByLabelText('Negatif kelimeler'))
    fireEvent.click(within(dialog).getByLabelText('Başlıklar'))
    fireEvent.change(text, { target: { value: '  Açıklamaları daha samimi yap  ' } })
    expect((submit as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(submit)

    await waitFor(() =>
      expect(mocks.regenerateAdGroup).toHaveBeenCalledWith(5, 10, {
        feedback: 'Açıklamaları daha samimi yap',
        scope: ['descriptions', 'negatives'],
      })
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('odak pencerede kalır ve kapanınca açan butona döner', async () => {
    render(<AdsPanel runId={1} />)
    const opener = await screen.findByRole('button', { name: /Revize Et/ })
    opener.focus()
    fireEvent.click(opener)
    const dialog = screen.getByRole('dialog')
    const text = within(dialog).getByLabelText('Neyi değiştirelim?')
    expect(document.activeElement).toBe(text)

    // Son odaklanabilir öğeden Tab → ilk öğeye döner (pencere dışına çıkmaz)
    fireEvent.change(text, { target: { value: 'Daha kısa başlıklar' } })
    const last = within(dialog).getByRole('button', { name: 'Revize Et' })
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Kapat' }))
    fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)

    fireEvent.click(within(dialog).getByRole('button', { name: 'Vazgeç' }))
    await waitFor(() => expect(document.activeElement).toBe(opener))
  })

  it('istek yoldayken ikinci tıklama ikinci istek göndermez', async () => {
    let resolveStart: (v: unknown) => void = () => {}
    mocks.regenerateAdGroup.mockImplementation(
      () => new Promise((resolve) => (resolveStart = resolve))
    )
    render(<AdsPanel runId={1} />)
    await openDialog()
    const dialog = screen.getByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Neyi değiştirelim?'), {
      target: { value: 'Daha kısa başlıklar' },
    })
    const submit = within(dialog).getByRole('button', { name: 'Revize Et' })
    fireEvent.click(submit)
    fireEvent.click(submit)
    fireEvent.click(within(dialog).getByRole('button', { name: /Gönderiliyor/ }))
    expect(mocks.regenerateAdGroup).toHaveBeenCalledTimes(1)
    await act(async () => resolveStart({ data: { task_id: 't1', generation_set_id: 4 } }))
  })

  it('gönderim hatasında pencere açık kalır, talep silinmez ve hata gösterilir', async () => {
    mocks.regenerateAdGroup.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: { message: 'Üretim sürüyor.' } } },
    })
    render(<AdsPanel runId={1} />)
    await openDialog()
    const dialog = screen.getByRole('dialog')
    const text = within(dialog).getByLabelText('Neyi değiştirelim?') as HTMLTextAreaElement
    fireEvent.change(text, { target: { value: 'Fiyat vurgusunu azalt' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revize Et' }))

    expect(await within(dialog).findByRole('alert')).toBeTruthy()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(text.value).toBe('Fiyat vurgusunu azalt')
    // Dışarı tıklama yazılmış talebi silmez
    fireEvent.click(dialog.parentElement!)
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('süren revizyon hedef kartta ve ilerleme alanında grup adıyla görünür', async () => {
    localStorage.setItem('digitus_active_tasks', JSON.stringify({ 'ads_task:10:1': 'task-rev' }))
    mocks.getAdsSets.mockResolvedValue({
      data: {
        scoring_run_id: 1,
        active_set_id: 3,
        sets: [
          activeSet,
          {
            ...activeSet,
            id: 4,
            version_number: 2,
            status: 'generating',
            revision: {
              feedback: 'Daha samimi',
              scope: ['descriptions'],
              source_set_id: 3,
              source_group_id: 5,
            },
          },
        ],
      },
    })
    render(<AdsPanel runId={1} />)
    expect(await screen.findByText('“Laptop” grubu revize ediliyor')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Revize ediliyor/ })).toBeTruthy()
    expect(screen.getByText(/mevcut\s+reklamlar o zamana kadar değişmez/)).toBeTruthy()
  })

  it('revizyon taslağı açıkça taslak olarak anlatılır ve aktif sete dönüş sunulur', async () => {
    const draft = {
      ...activeSet,
      id: 4,
      version_number: 2,
      status: 'draft',
      revision: {
        feedback: 'Açıklamaları daha samimi yap',
        scope: ['descriptions'],
        source_set_id: 3,
        source_group_id: 5,
      },
    }
    mocks.getAdsRsa.mockResolvedValue(resultFor(draft))
    mocks.getAdsSets.mockResolvedValue({
      data: { scoring_run_id: 1, sets: [activeSet, draft], active_set_id: 3 },
    })
    render(<AdsPanel runId={1} />)
    expect(await screen.findByText(/Kullanımdaki set hâlâ v1/)).toBeTruthy()
    expect(screen.getByText(/“Açıklamaları daha samimi yap” talebiyle revize edildi/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Aktif Yap' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Aktif sete dön/ }))
    await waitFor(() => expect(mocks.getAdsRsa).toHaveBeenLastCalledWith(1, 10, undefined))
  })

  it('güncel olmayan sette buton pasif kalır ve nedeni yazılır', async () => {
    const stale = { ...activeSet, is_stale: true }
    mocks.getAdsRsa.mockResolvedValue(resultFor(stale))
    mocks.getAdsSets.mockResolvedValue({
      data: { scoring_run_id: 1, sets: [stale], active_set_id: 3 },
    })
    render(<AdsPanel runId={1} />)
    const btn = (await screen.findByRole('button', { name: /Revize Et/ })) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.getByText(/Revize Et kullanılamıyor: Bu set güncel değil/)).toBeTruthy()
  })
})
