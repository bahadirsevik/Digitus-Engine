/**
 * formatValidationDetail — 422 Pydantic detayının okunur mesaja çevrilmesi.
 * Interceptor bu yardımcıyı kullanır; hem {detail:[...]}ten çıkarılan array
 * hem string detail formu kapsanır (codex plan-review şartı).
 */
import { describe, expect, it } from 'vitest'
import { formatValidationDetail } from './api'

describe('formatValidationDetail', () => {
  it('Pydantic hata listesini alan adlarıyla okunur mesaja çevirir', () => {
    const detail = [
      {
        type: 'greater_than_equal',
        loc: ['body', 'max_categories'],
        msg: 'Input should be greater than or equal to 4',
        input: 2,
      },
      {
        type: 'greater_than_equal',
        loc: ['body', 'ideas_per_category'],
        msg: 'Input should be greater than or equal to 3',
        input: 2,
      },
    ]
    expect(formatValidationDetail(detail)).toBe(
      'max_categories: Input should be greater than or equal to 4 · ' +
        'ideas_per_category: Input should be greater than or equal to 3'
    )
  })

  it('loc yalnız body ise alan öneki eklemez', () => {
    const detail = [{ loc: ['body'], msg: 'Field required' }]
    expect(formatValidationDetail(detail)).toBe('Field required')
  })

  it('string detail olduğu gibi döner', () => {
    expect(formatValidationDetail('Geçersiz istek')).toBe('Geçersiz istek')
  })

  it('tanınmayan format için null döner (fallback mesaj devreye girer)', () => {
    expect(formatValidationDetail(undefined)).toBeNull()
    expect(formatValidationDetail({ foo: 'bar' })).toBeNull()
    expect(formatValidationDetail([])).toBeNull()
    expect(formatValidationDetail([{ type: 'x' }])).toBeNull()
  })
})
