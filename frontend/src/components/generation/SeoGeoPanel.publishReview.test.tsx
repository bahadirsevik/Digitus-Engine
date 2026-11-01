/**
 * Yayın öncesi inceleme: kritik gerekçeler karta tıklamadan görünür.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import SeoGeoPanel from './SeoGeoPanel'
import { useBrandStore } from '../../stores/brandStore'

const mocks = vi.hoisted(() => ({ listSeoGeo: vi.fn() }))

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>()
  return {
    ...actual,
    generationApi: { ...actual.generationApi, listSeoGeo: mocks.listSeoGeo },
  }
})

const DETAIL_A = 'Risk — yayın öncesi düzelt: "en iyi" iddiası kanıtsız'
const DETAIL_B = 'Profilde doğrulanamadı — kontrol gerekli: 15 yıl deneyim'

describe('SeoGeoPanel yayın öncesi inceleme', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.listSeoGeo.mockReset().mockResolvedValue({
      data: {
        items: [
          {
            id: 1,
            keyword: 'implant',
            title: 'İmplant rehberi',
            seo_score: 0.8,
            geo_score: 0.7,
            combined_score: 0.75,
            publish_review: {
              required: true,
              status: 'review_required',
              label: 'Yayın öncesi inceleme gerekli',
              critical_failures: [
                { criterion: 'a', source: 'seo_auto', detail: DETAIL_A },
                { criterion: 'b', source: 'geo_ai', detail: DETAIL_B },
              ],
              manual_checks: ['Yazar bilgisini ekleyin'],
            },
          },
        ],
      },
    })
    useBrandStore.setState({
      activeWorkspace: {
        id: 10,
        name: 'WS',
        company_url: 'https://ws.test',
        status: 'confirmed',
        profile_data: null,
        suggested_keywords: null,
      },
    })
  })

  afterEach(cleanup)

  it('inceleme gerekiyorsa gerekçeler kart üstünde tıklamadan görünür, kutu açık gelir', async () => {
    const { container } = render(<SeoGeoPanel runId={1} />)
    const badge = await screen.findByText('Yayın öncesi inceleme gerekli')
    expect(badge).toHaveAttribute('title', DETAIL_A)

    const top = container.querySelector('.seox-review-top') as HTMLElement
    expect(top).toBeTruthy()
    expect(top).toHaveTextContent(DETAIL_A)
    expect(top).toHaveTextContent(DETAIL_B)
    expect(top).toHaveTextContent('Otomatik kontrol')
    expect(top).toHaveTextContent('AI değerlendirmesi')

    expect((container.querySelector('.seox-review-box') as HTMLDetailsElement).open).toBe(true)
  })
})
