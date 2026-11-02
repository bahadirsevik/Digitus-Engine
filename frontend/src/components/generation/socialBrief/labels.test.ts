/**
 * Sosyal brief etiketleri: kategori tipi, kanca stili ve format adları Türkçe
 * gösterilir. Backend listeleriyle kapsam/sapma kontrolü Python tarafında:
 * tests/unit/test_social_brief_label_parity.py (labels.ts'i kaynak olarak okur).
 */
import { describe, expect, it } from 'vitest'
import {
  categoryTypeLabel,
  claimFieldLabel,
  fallbackFormatLabel,
  hookStyleLabel,
  parseClaimRepairs,
} from './labels'

describe('categoryTypeLabel', () => {
  it.each([
    ['educational', 'Eğitici'],
    ['product_benefit', 'Ürün faydası'],
    ['social_proof', 'Sosyal kanıt'],
    ['brand_story', 'Marka hikâyesi'],
    ['community', 'Topluluk'],
    ['trending', 'Gündem'],
  ])('%s -> %s', (id, label) => {
    expect(categoryTypeLabel(id)).toBe(label)
  })

  it('bilinmeyen değer olduğu gibi, boş değer boş döner', () => {
    expect(categoryTypeLabel('yeni_tip')).toBe('yeni_tip')
    expect(categoryTypeLabel(null)).toBe('')
  })
})

describe('hookStyleLabel', () => {
  it.each([
    ['question', 'Soru'],
    ['shocking', 'Şaşırtıcı'],
    ['relatable', 'Özdeşleşme'],
    ['curiosity', 'Merak'],
  ])('%s -> %s', (id, label) => {
    expect(hookStyleLabel(id)).toBe(label)
  })
})

describe('fallbackFormatLabel', () => {
  it('geçmiş ekranı brief ekranıyla aynı adı kullanır (backend matrisi)', () => {
    expect(fallbackFormatLabel('post')).toBe('Post')
    expect(fallbackFormatLabel('short')).toBe('Short')
    expect(fallbackFormatLabel('thread')).toBe('Thread')
  })
})

describe('claim repair kaydı', () => {
  it.each([
    ['caption', 'Açıklama'],
    ['hook[0]', 'Kanca 1'],
    ['cta_text', 'CTA'],
    ['visual_suggestion', 'Görsel'],
    ['hashtag[2]', 'Etiket 3'],
    ['payload[1]', 'Format metni 2'],
  ])('%s -> %s', (field, label) => {
    expect(claimFieldLabel(field)).toBe(label)
  })

  it('current.changed öğelerini okur, bozuk JSON boş döner', () => {
    const rec = {
      current: {
        findings: [],
        applied: [],
        changed: [{ field: 'hook[1]', original: 'A', suggestion: 'B' }],
      },
    }
    expect(parseClaimRepairs(['x', `claim_review:${JSON.stringify(rec)}`])).toEqual([
      { field: 'hook[1]', original: 'A', suggestion: 'B' },
    ])
    expect(parseClaimRepairs(['claim_review:{bozuk'])).toEqual([])
    expect(parseClaimRepairs([])).toEqual([])
  })

  it('changed yoksa applied kullanılır', () => {
    const rec = { current: { applied: [{ span: 'en iyi', original: 'O', suggestion: 'S' }] } }
    expect(parseClaimRepairs([`claim_review:${JSON.stringify(rec)}`])).toEqual([
      { field: 'en iyi', original: 'O', suggestion: 'S' },
    ])
  })
})
