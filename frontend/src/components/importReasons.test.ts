import { describe, expect, it } from 'vitest'
import { reasonLabel } from './importReasons'

describe('reasonLabel', () => {
  it('labels skipped_junk in Turkish', () => {
    expect(reasonLabel({ keyword: '123', reason: 'skipped_junk', matched: 'numeric_only' })).toBe(
      'Geçersiz kelime (boş / yalnız sayı veya sembol)'
    )
  })

  it('falls back to the raw reason for unknown codes', () => {
    expect(reasonLabel({ keyword: 'x', reason: 'something_else' })).toBe('something_else')
  })
})
