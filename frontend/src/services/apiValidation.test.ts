/**
 * formatValidationDetail — 422 Pydantic detayının okunur mesaja çevrilmesi.
 * Interceptor bu yardımcıyı kullanır; hem {detail:[...]}ten çıkarılan array
 * hem string detail formu kapsanır (codex plan-review şartı).
 */
import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, describe, expect, it } from 'vitest'
import api, { formatValidationDetail } from './api'

describe('formatValidationDetail', () => {
  it('Pydantic hata listesini Türkçe alan adı ve sınırla çevirir', () => {
    const detail = [
      {
        type: 'greater_than_equal',
        loc: ['body', 'max_categories'],
        msg: 'Input should be greater than or equal to 4',
        input: 2,
        ctx: { ge: 4 },
      },
      {
        type: 'greater_than_equal',
        loc: ['body', 'ideas_per_category'],
        msg: 'Input should be greater than or equal to 3',
        input: 2,
        ctx: { ge: 3 },
      },
    ]
    expect(formatValidationDetail(detail)).toBe(
      'Kategori sayısı en az 4 olmalı. · Kategori başına fikir sayısı en az 3 olmalı.'
    )
  })

  it('elle kelime girişindeki karakter sınırı alan adıyla söylenir', () => {
    expect(
      formatValidationDetail([
        {
          type: 'string_too_long',
          loc: ['body', 'keyword'],
          msg: 'String should have at most 500 characters',
          ctx: { max_length: 500 },
        },
      ])
    ).toBe('Anahtar kelime en fazla 500 karakter olabilir.')
  })

  it('eşlemesi olmayan iç alan adı gösterilmez', () => {
    expect(
      formatValidationDetail([
        { type: 'greater_than_equal', loc: ['body', 'internal_x'], msg: 'x', ctx: { ge: 2 } },
      ])
    ).toBe('Bir değer en az 2 olmalı.')
  })

  it('zorunlu alan ve backend Türkçe doğrulama metni', () => {
    expect(
      formatValidationDetail([{ type: 'missing', loc: ['body'], msg: 'Field required' }])
    ).toBe('Zorunlu bir alan boş bırakılmış.')
    expect(
      formatValidationDetail([
        {
          type: 'value_error',
          loc: ['body', 'x'],
          msg: 'Value error, Video formatları için süre seçin.',
        },
      ])
    ).toBe('Video formatları için süre seçin.')
  })

  it('tanınmayan İngilizce mesajı ham göstermez', () => {
    expect(
      formatValidationDetail([
        { type: 'int_parsing', loc: ['body', 'n'], msg: 'Input should be a valid integer' },
      ])
    ).toBe('Girilen değerlerden biri geçersiz.')
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

describe('response interceptor (5xx / ağ hatası)', () => {
  const originalAdapter = api.defaults.adapter
  afterEach(() => {
    api.defaults.adapter = originalAdapter
  })

  function failWith(status: number, data: unknown, code?: string) {
    const adapter: AxiosAdapter = (config: InternalAxiosRequestConfig) =>
      Promise.reject(
        new AxiosError(
          'raw axios message',
          code,
          config,
          {},
          status ? { status, statusText: '', headers: {}, config, data } : undefined
        )
      )
    api.defaults.adapter = adapter
    return api.get('/x').then(
      () => null,
      (e: { status: number; message: string }) => e
    )
  }

  it('5xx string detail mesajı korunur', async () => {
    const e = await failWith(500, { detail: 'Skorlama sırasında hata oluştu; tekrar deneyin.' })
    expect(e?.message).toBe('Skorlama sırasında hata oluştu; tekrar deneyin.')
  })

  it('5xx detail boş/nesne ise jenerik Türkçe metin', async () => {
    expect((await failWith(500, { detail: '  ' }))?.message).toMatch(/^Sunucu hatası oluştu/)
    expect((await failWith(502, {}))?.message).toMatch(/^Sunucu hatası oluştu/)
  })

  it('5xx nesne detayında {code, message} mesajı kullanılır', async () => {
    const e = await failWith(502, {
      detail: { code: 'BROKER_ERROR', message: 'Kuyruk servisine ulaşılamadı.' },
    })
    expect(e?.message).toBe('Kuyruk servisine ulaşılamadı.')
  })

  it('ağ hatası Türkçe metne çevrilir', async () => {
    const e = await failWith(0, undefined, 'ERR_NETWORK')
    expect(e?.status).toBe(0)
    expect(e?.message).toBe('Sunucuya ulaşılamadı; bağlantınızı kontrol edip tekrar deneyin.')
  })

  it('zaman aşımı Türkçe metne çevrilir', async () => {
    const e = await failWith(0, undefined, 'ECONNABORTED')
    expect(e?.message).toMatch(/zaman aşımına uğradı/)
  })
})
