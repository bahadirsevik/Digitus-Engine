/**
 * Seed chip düzenlemeleri sekme değişimi (unmount/remount) sonrası korunur;
 * profil seed listesi değişirse eski taslak bırakılır.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import GoogleAdsKeywordSearch from './GoogleAdsKeywordSearch'
import { useBrandStore, ActiveWorkspace } from '../stores/brandStore'

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    googleAdsApi: {
      ...actual.googleAdsApi,
      listCustomers: vi.fn().mockResolvedValue({ data: [] }),
    },
  }
})

const workspace = (suggested: string[]): ActiveWorkspace => ({
  id: 7,
  name: 'ws',
  company_url: 'https://example.com',
  status: 'confirmed',
  profile_data: null,
  suggested_keywords: suggested,
})

const chips = () =>
  Array.from(document.querySelectorAll('.kwx-chip')).map((el) => el.textContent?.trim())

const renderSearch = () =>
  render(<GoogleAdsKeywordSearch onImport={vi.fn().mockResolvedValue({})} />)

describe('GoogleAdsKeywordSearch seed taslağı', () => {
  beforeEach(() => {
    useBrandStore.setState({ activeWorkspace: workspace(['alfa', 'beta']), seedDrafts: {} })
  })
  afterEach(cleanup)

  it('silme ve ekleme remount sonrası korunur', () => {
    const first = renderSearch()
    fireEvent.click(screen.getAllByTitle('Sil')[0])
    fireEvent.click(screen.getByText('Ekle'))
    const input = screen.getByPlaceholderText('Kelime yaz…')
    fireEvent.change(input, { target: { value: 'gama' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(chips()).toEqual(['beta', 'gama'])

    first.unmount()
    renderSearch()
    expect(chips()).toEqual(['beta', 'gama'])
  })

  it('profil seed listesi değişince taslak yerine yeni liste gelir', () => {
    const first = renderSearch()
    fireEvent.click(screen.getAllByTitle('Sil')[0])
    first.unmount()

    useBrandStore.setState({ activeWorkspace: workspace(['delta']) })
    renderSearch()
    expect(chips()).toEqual(['delta'])
  })
})

describe('GoogleAdsKeywordSearch başlangıç kelimesi sınırı', () => {
  afterEach(cleanup)

  it('20 kelime varken yenisi eklenmez ve uyarı çıkar', () => {
    const twenty = Array.from({ length: 20 }, (_, i) => `k${i}`)
    useBrandStore.setState({
      activeWorkspace: workspace([]),
      seedDrafts: { 7: { base: '', words: twenty } },
    })
    renderSearch()
    fireEvent.click(screen.getByText('Ekle'))
    const input = screen.getByPlaceholderText('Kelime yaz…')
    fireEvent.change(input, { target: { value: 'fazla' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(chips()).toHaveLength(20)
    expect(screen.getByRole('alert').textContent).toMatch(/en fazla 20 başlangıç kelimesi/)

    fireEvent.click(screen.getAllByTitle('Sil')[0])
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('kayıtlı listede 20den fazla kelime varsa arama kapalı ve kaç tane silineceği söylenir', () => {
    const many = Array.from({ length: 23 }, (_, i) => `k${i}`)
    useBrandStore.setState({
      activeWorkspace: workspace([]),
      seedDrafts: { 7: { base: '', words: many } },
    })
    renderSearch()
    expect(screen.getByRole('alert').textContent).toMatch(
      /23 kelime var; aramak için 3 tanesini silin/
    )
    expect(screen.getByRole('button', { name: /Ara/ })).toBeDisabled()
  })
})
