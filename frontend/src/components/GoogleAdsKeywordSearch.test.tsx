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
