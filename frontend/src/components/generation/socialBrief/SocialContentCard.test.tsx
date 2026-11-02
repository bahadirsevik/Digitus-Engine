import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import SocialContentCard from './SocialContentCard'
import type { SocialContentView } from './SocialContentCard'

const view = (validationWarnings: string[]): SocialContentView => ({
  id: 1,
  platformLabel: 'Instagram',
  formatLabel: 'Reel',
  hooks: [],
  caption: 'Güncel metin',
  hashtags: [],
  validationWarnings,
  isStale: false,
})

describe('SocialContentCard onarım öncesi metin', () => {
  it('current.changed varsa alan etiketi, orijinal ve öneri listelenir; ham JSON gizli kalır', () => {
    const record = {
      current: {
        findings: [],
        applied: [],
        changed: [
          { field: 'hook[0]', original: 'Garantili sonuç', suggestion: 'Güvenilir çözüm' },
          { field: 'cta_text', original: 'Hemen al', suggestion: 'İncele' },
        ],
      },
      history: [],
    }
    const { container } = render(
      <SocialContentCard view={view([`claim_review:${JSON.stringify(record)}`])} />
    )
    expect(screen.getByText('Onarım öncesi metin (2)')).toBeTruthy()
    expect(screen.getByText('Kanca 1')).toBeTruthy()
    expect(screen.getByText('CTA')).toBeTruthy()
    expect(screen.getByText(/Garantili sonuç/)).toBeTruthy()
    expect(container.textContent).not.toContain('claim_review:')
  })

  it('kayıt yoksa liste çizilmez', () => {
    const { container } = render(<SocialContentCard view={view([])} />)
    expect(container.querySelector('.sb-repair-list')).toBeNull()
  })
})
