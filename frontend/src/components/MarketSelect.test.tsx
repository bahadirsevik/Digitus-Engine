import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MarketSelect from './MarketSelect'
import { useBrandStore } from '../stores/brandStore'
import type { MarketValue } from '../services/market'

const marketOptions = vi.fn()
const searchLocations = vi.fn()

vi.mock('../services/api', () => ({
  workspaceApi: {
    marketOptions: () => marketOptions(),
    searchLocations: (...args: unknown[]) => searchLocations(...args),
  },
  apiErrorMessage: (err: unknown, fallback = 'Hata') =>
    (err && typeof err === 'object' && 'message' in err
      ? String((err as { message?: unknown }).message)
      : '') || fallback,
}))

const OPTIONS = {
  countries: [{ geo_target_id: '2276', code: 'DE', name_tr: 'Almanya', label: 'Almanya (DE)' }],
  languages: [
    { language_id: '1031', code: 'ru', name_tr: 'Rusça', name_native: 'Русский' },
    { language_id: '1001', code: 'de', name_tr: 'Almanca', name_native: 'Deutsch' },
  ],
  default: { geo_target_id: '2276', language_id: '1031' },
}

const BERLIN = {
  geo_target_id: '1003854',
  label: 'Berlin, Almanya — Şehir',
  kind: 'city' as const,
  country_code: 'DE',
  name: 'Berlin',
}

function Harness({
  initial,
  onChangeSpy,
  ...rest
}: {
  initial: MarketValue
  onChangeSpy?: (v: MarketValue) => void
  disabled?: boolean
  lockedMessage?: string
}) {
  const [value, setValue] = useState(initial)
  return (
    <MarketSelect
      value={value}
      onChange={(v) => {
        setValue(v)
        onChangeSpy?.(v)
      }}
      {...rest}
    />
  )
}

describe('MarketSelect', () => {
  beforeEach(() => {
    useBrandStore.setState({ marketOptions: null, marketOptionsError: null })
    marketOptions.mockReset().mockResolvedValue({ data: OPTIONS })
    searchLocations.mockReset().mockResolvedValue({ data: { items: [BERLIN], total: 1 } })
  })
  afterEach(cleanup)

  it('yazarken 300 ms debounce uygular, tek arama yapar ve sonuç etiketini gösterir', async () => {
    render(<Harness initial={{ geo_target_id: '2276', language_id: '1031' }} />)
    await screen.findByText('Almanya (DE)')

    const input = screen.getByLabelText('Hedef konum')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'be' } })
    fireEvent.change(input, { target: { value: 'berl' } })
    expect(searchLocations).not.toHaveBeenCalled()

    const option = await screen.findByRole('option', { name: 'Berlin, Almanya — Şehir' })
    expect(searchLocations).toHaveBeenCalledTimes(1)
    expect(searchLocations).toHaveBeenCalledWith('berl', undefined)
    // Sayısal ID yalnız ipucunda
    expect(option.getAttribute('title')).toContain('1003854')
    expect(option.textContent).not.toContain('1003854')
  })

  it('ülke daraltması arama isteğine country olarak gider', async () => {
    render(<Harness initial={{ geo_target_id: '2276', language_id: '1031' }} />)
    await screen.findByText('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Ülke ile daralt'), { target: { value: 'DE' } })
    const input = screen.getByLabelText('Hedef konum')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'ber' } })
    await within(await screen.findByRole('listbox')).findByRole('option')
    expect(searchLocations).toHaveBeenLastCalledWith('ber', 'DE')
  })

  it('konum seçmek dili DEĞİŞTİRMEZ', async () => {
    const spy = vi.fn()
    render(<Harness initial={{ geo_target_id: '2276', language_id: '1001' }} onChangeSpy={spy} />)
    await screen.findByText('Almanya (DE)')
    const input = screen.getByLabelText('Hedef konum')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'berlin' } })
    fireEvent.click(await screen.findByRole('option', { name: /Berlin/ }))

    expect(spy).toHaveBeenCalledWith({ geo_target_id: '1003854', language_id: '1001' })
    expect((screen.getByLabelText('Çıktı dili') as HTMLSelectElement).value).toBe('1001')
    expect(screen.getByText('Berlin, Almanya — Şehir')).toBeTruthy()
  })

  it('klavye: ok tuşu + Enter ile seçilir', async () => {
    const spy = vi.fn()
    render(<Harness initial={{ geo_target_id: null, language_id: '1031' }} onChangeSpy={spy} />)
    const input = screen.getByLabelText('Hedef konum')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'berlin' } })
    await within(await screen.findByRole('listbox')).findByRole('option')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(spy).toHaveBeenCalledWith({ geo_target_id: '1003854', language_id: '1031' })
  })

  it('dil değişince konum değişmez', async () => {
    const spy = vi.fn()
    render(<Harness initial={{ geo_target_id: '2276', language_id: '1031' }} onChangeSpy={spy} />)
    await screen.findByText('Almanya (DE)')
    fireEvent.change(screen.getByLabelText('Çıktı dili'), { target: { value: '1001' } })
    expect(spy).toHaveBeenCalledWith({ geo_target_id: '2276', language_id: '1001' })
  })

  it('bilinmeyen mevcut konum kimlik göstermeden yeniden seçim ister', async () => {
    render(<Harness initial={{ geo_target_id: '999999', language_id: '1031' }} />)
    expect(await screen.findByText('Kayıtlı konum tanınamadı; lütfen yeniden seçin')).toBeTruthy()
  })

  it('kilitli: alanlar pasif, mesaj görünür, arama yapılmaz', async () => {
    render(
      <Harness
        initial={{ geo_target_id: '2276', language_id: '1031' }}
        disabled
        lockedMessage="Bu çalışmada veri var"
      />
    )
    await screen.findByText('Almanya (DE)')
    const input = screen.getByLabelText('Hedef konum') as HTMLInputElement
    expect(input.disabled).toBe(true)
    expect((screen.getByLabelText('Çıktı dili') as HTMLSelectElement).disabled).toBe(true)
    expect(screen.getByRole('note').textContent).toContain('Bu çalışmada veri var')
    fireEvent.focus(input)
    expect(searchLocations).not.toHaveBeenCalled()
  })

  it('arama hatası ve boş sonuç durumları gösterilir', async () => {
    render(<Harness initial={{ geo_target_id: '2276', language_id: '1031' }} />)
    await screen.findByText('Almanya (DE)')
    const input = screen.getByLabelText('Hedef konum')

    searchLocations.mockRejectedValueOnce({ message: 'Katalog yok' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'xx' } })
    expect(await screen.findByText('Katalog yok')).toBeTruthy()

    searchLocations.mockResolvedValueOnce({ data: { items: [], total: 0 } })
    fireEvent.change(input, { target: { value: 'zzz' } })
    expect(await screen.findByText('Sonuç bulunamadı')).toBeTruthy()
  })

  it('seçenekler yüklenemezse hata gösterilir, tekrar dene ile düzelir', async () => {
    marketOptions.mockRejectedValueOnce({ message: 'Sunucu hatası' })
    render(<Harness initial={{ geo_target_id: null, language_id: null }} />)
    expect((await screen.findByRole('alert')).textContent).toContain('Sunucu hatası')

    fireEvent.click(screen.getByRole('button', { name: 'Tekrar dene' }))
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(screen.getByRole('option', { name: 'Rusça' })).toBeTruthy()
  })
})
