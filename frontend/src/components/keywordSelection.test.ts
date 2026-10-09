import { describe, expect, it } from 'vitest'
import { selectMinVolume, selectTopN, sortByVolumeDesc, splitLowVolume } from './keywordSelection'
import { buildStartAnalysisPayload, hasAtLeastOnePurpose } from './startAnalysis'
import { reasonLabel } from './importReasons'
import { matchExcludeTheme, previewThemeExclusions } from './themePreview'

interface Item {
  keyword: string
  volume: number
}

const items: Item[] = [
  { keyword: 'düşük', volume: 3 },
  { keyword: 'yüksek', volume: 5000 },
  { keyword: 'orta', volume: 150 },
  { keyword: 'sıfır', volume: 0 },
]

const getVol = (i: Item) => i.volume
const getKey = (i: Item) => i.keyword

describe('keywordSelection', () => {
  it('sortByVolumeDesc hacme göre azalan sıralar (girdiyi bozmaz)', () => {
    const sorted = sortByVolumeDesc(items, getVol)
    expect(sorted.map(getKey)).toEqual(['yüksek', 'orta', 'düşük', 'sıfır'])
    expect(items[0].keyword).toBe('düşük')
  })

  it('splitLowVolume eşik altını ayırır (default 10)', () => {
    const { visible, low } = splitLowVolume(items, getVol)
    expect(visible.map(getKey)).toEqual(['yüksek', 'orta'])
    expect(low.map(getKey)).toEqual(['düşük', 'sıfır'])
  })

  it('selectTopN hacme göre ilk N anahtarı seçer', () => {
    expect(selectTopN(items, getKey, getVol, 2)).toEqual(new Set(['yüksek', 'orta']))
  })

  it('selectMinVolume eşik ve üzerini seçer', () => {
    expect(selectMinVolume(items, getKey, getVol, 100)).toEqual(new Set(['yüksek', 'orta']))
    expect(selectMinVolume(items, getKey, getVol, 10000)).toEqual(new Set())
  })
})

describe('startAnalysis payload builder', () => {
  it('amaç bayraklarını ve top_n/200 seçimini kurar', () => {
    const payload = buildStartAnalysisPayload({
      brandProfileId: 7,
      ads: true,
      seo: false,
      social: true,
      adsCapacity: 30,
      seoCapacity: 50,
      socialCapacity: 20,
    })
    expect(payload.brand_profile_id).toBe(7)
    expect(payload.enable_ads).toBe(true)
    expect(payload.enable_seo).toBe(false)
    expect(payload.enable_social).toBe(true)
    expect(payload.keyword_selection_mode).toBe('all')
    expect(payload.keyword_limit).toBeUndefined()
    expect(payload.auto_assign_channels).toBe(true)
    expect(payload.algorithm_version).toBe('v3')
    expect(payload.seo_capacity).toBe(50) // kapalı kanal için de >0 (backend şartı)
  })

  it('seçili kanalda sıfırı varsayılan kapasiteye çevirmek yerine reddeder', () => {
    expect(() =>
      buildStartAnalysisPayload({
        brandProfileId: 1,
        ads: true,
        seo: false,
        social: false,
        adsCapacity: 0,
        seoCapacity: -5,
        socialCapacity: 20,
      })
    ).toThrow(RangeError)
  })

  it('hasAtLeastOnePurpose en az bir amaç ister', () => {
    expect(hasAtLeastOnePurpose({ ads: false, seo: false, social: false })).toBe(false)
    expect(hasAtLeastOnePurpose({ ads: false, seo: true, social: false })).toBe(true)
  })
})

describe('KeywordImportResult reasonLabel', () => {
  it('yeni skip nedenlerini Türkçe etiketler', () => {
    expect(reasonLabel({ keyword: 'x', reason: 'skipped_theme', matched: 'temettü takibi' })).toBe(
      'Yasakli temayla elendi: "temettü takibi"'
    )
    expect(reasonLabel({ keyword: 'x', reason: 'limit_exceeded' })).toBe('Havuz limiti doldu')
    expect(reasonLabel({ keyword: 'x', reason: 'skipped_exact' })).toBe(
      "Workspace'te ayni metriklerle zaten var"
    )
  })
})

describe('theme preview', () => {
  it('cok kelimeli temada tum kokleri ister', () => {
    const themes = ['temettu takibi']

    expect(matchExcludeTheme('temettu takip programi', themes)).toBe('temettu takibi')
    expect(matchExcludeTheme('portfoy takip programi', themes)).toBeNull()
  })

  it('url fikirleri icin elenme nedenlerini onizler', () => {
    const ideas = [
      { keyword: 'kripto para sinyalleri' },
      { keyword: 'borsa takip programi' },
      { keyword: 'temettu takip programi' },
    ]

    expect(previewThemeExclusions(ideas, (idea) => idea.keyword, ['kripto para'])).toEqual([
      { keyword: 'kripto para sinyalleri', matched: 'kripto para' },
    ])
  })
})
