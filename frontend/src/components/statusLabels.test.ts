import { describe, expect, it } from 'vitest'
import { poolClassLabel, runStatusLabel, selectionReasonLabel } from './statusLabels'

describe('statusLabels', () => {
  it('analiz durumunu Türkçe verir; bilinmeyen kodu ham basmaz', () => {
    expect(runStatusLabel('channel_assigned')).toBe('Kanal havuzları hazır')
    expect(runStatusLabel('weird_code')).toBe('Durum bilgisi alınamadı')
    expect(runStatusLabel(undefined)).toBe('Durum bilgisi alınamadı')
  })

  it('seçim gerekçesi: normal sonuç nötr, filtre elemesi ayrıntısıyla', () => {
    expect(selectionReasonLabel('CAPACITY_LIMIT')).toEqual({
      label: 'Kanal kapasitesi nedeniyle seçilmedi',
      neutral: true,
    })
    expect(selectionReasonLabel('SEO_NOT_PRIMARY').neutral).toBe(true)
    expect(selectionReasonLabel('COMPETITOR_TERM:Xero')).toEqual({
      label: 'Rakip terimi: Xero',
      neutral: false,
    })
    expect(selectionReasonLabel('SOMETHING_NEW').label).toBe('Seçilmedi')
  })

  it('havuz sınıfı kanala göre okunur; ADS ve bilinmeyen sınıf gösterilmez', () => {
    expect(poolClassLabel('seo', 'secondary')).toBe('Başka bir içerikle aynı sayfada')
    expect(poolClassLabel('social', 'TREND_CONTENT')).toBe('Trend içeriği')
    expect(poolClassLabel('ads', 'primary')).toBeNull()
    expect(poolClassLabel('seo', 'mystery')).toBeNull()
  })
})
