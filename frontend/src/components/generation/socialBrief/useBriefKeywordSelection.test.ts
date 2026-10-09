import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useBriefKeywordSelection } from './useBriefKeywordSelection'

describe('useBriefKeywordSelection', () => {
  it('ekler/çıkarır ve 5 sınırında reddedip uyarı üretir', () => {
    const { result } = renderHook(() => useBriefKeywordSelection('7:10'))
    for (const id of [1, 2, 3, 4, 5]) act(() => void result.current.toggle(id))
    expect(result.current.selectedIds).toEqual([1, 2, 3, 4, 5])
    let changed = true
    act(() => {
      changed = result.current.toggle(6)
    })
    expect(changed).toBe(false)
    expect(result.current.selectedIds).toEqual([1, 2, 3, 4, 5])
    expect(result.current.notice).toMatch(/En fazla 5/)
    act(() => void result.current.toggle(3))
    expect(result.current.selectedIds).toEqual([1, 2, 4, 5])
    expect(result.current.notice).toBeNull()
  })

  it('format matrisinden gelen sınıra uyar', () => {
    const { result } = renderHook(() => useBriefKeywordSelection('7:10'))
    act(() => result.current.setMaxKeywords(2))
    act(() => void result.current.toggle(1))
    act(() => void result.current.toggle(2))
    act(() => void result.current.toggle(3))
    expect(result.current.selectedIds).toEqual([1, 2])
  })

  it('kapsam değişince seçim ve form durumu İLK render’da boşalır', () => {
    const { result, rerender } = renderHook(({ scope }) => useBriefKeywordSelection(scope), {
      initialProps: { scope: '7:10' },
    })
    act(() => result.current.setComposing(true))
    act(() => void result.current.toggle(1))
    rerender({ scope: '7:11' })
    expect(result.current.selectedIds).toEqual([])
    expect(result.current.composing).toBe(false)
    expect(result.current.isSelected(1)).toBe(false)
    // Eski kapsama dönüş de temiz başlar (seçim kapsamlar arasında taşınmaz)
    rerender({ scope: '7:10' })
    expect(result.current.selectedIds).toEqual([])
  })

  it('remove, retainOnly ve clear aynı durumu günceller', () => {
    const { result } = renderHook(() => useBriefKeywordSelection('7:10'))
    for (const id of [1, 2, 3]) act(() => void result.current.toggle(id))
    act(() => result.current.remove(2))
    expect(result.current.selectedIds).toEqual([1, 3])
    act(() => result.current.retainOnly([3, 9]))
    expect(result.current.selectedIds).toEqual([3])
    act(() => result.current.clear())
    expect(result.current.selectedIds).toEqual([])
  })
})
