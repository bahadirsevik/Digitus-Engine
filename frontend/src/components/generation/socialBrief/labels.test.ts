/**
 * Sosyal brief etiketleri: kategori tipi, kanca stili ve format adları Türkçe
 * gösterilir. Backend listeleriyle kapsam/sapma kontrolü Python tarafında:
 * tests/unit/test_social_brief_label_parity.py (labels.ts'i kaynak olarak okur).
 */
import { describe, expect, it } from 'vitest'
import { categoryTypeLabel, fallbackFormatLabel, hookStyleLabel } from './labels'

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
