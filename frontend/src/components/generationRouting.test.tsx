/**
 * Faz 4 yönlendirme + panel davranış testleri:
 * - GenerationRedirect: eski /generation?tab=&run_id= linkleri doğru kanal sayfasına, run_id korunarak
 * - RedirectWithParams: mevcut query paramları kaybolmaz
 * - SocialPanel: sosyal brief akışını klasik sihirbaz olmadan gösterir
 */
import { describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import GenerationRedirect from './GenerationRedirect'
import RedirectWithParams from './RedirectWithParams'
import SocialPanel from './generation/SocialPanel'

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function renderRedirect(initialUrl: string, redirectElement: JSX.Element, redirectPath: string) {
  cleanup() // aynı test içinde birden fazla senaryo render edilebilsin
  render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <Routes>
        <Route path={redirectPath} element={redirectElement} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>
  )
  return screen.getByTestId('location').textContent
}

describe('GenerationRedirect', () => {
  it('tab=ads → /ads, run_id korunur', () => {
    expect(
      renderRedirect('/generation?tab=ads&run_id=9', <GenerationRedirect />, '/generation')
    ).toBe('/ads?run_id=9')
  })

  it('tab=social → /social', () => {
    expect(
      renderRedirect('/generation?tab=social&run_id=3', <GenerationRedirect />, '/generation')
    ).toBe('/social?run_id=3')
  })

  it("tab'sız/tanınmayan tab → /seo-geo", () => {
    expect(renderRedirect('/generation', <GenerationRedirect />, '/generation')).toBe('/seo-geo')
    expect(
      renderRedirect('/generation?tab=bilinmeyen', <GenerationRedirect />, '/generation')
    ).toBe('/seo-geo')
  })
})

describe('RedirectWithParams', () => {
  it('mevcut query paramlarını hedefe taşır', () => {
    expect(
      renderRedirect(
        '/channels?run_id=7&workspace_id=2',
        <RedirectWithParams to="/keywords?view=scores" />,
        '/channels'
      )
    ).toBe('/keywords?view=scores&run_id=7&workspace_id=2')
  })

  it('hedefteki sabit param, gelen aynı isimli paramı ezmez', () => {
    expect(
      renderRedirect(
        '/scoring?view=keywords',
        <RedirectWithParams to="/keywords?view=scores" />,
        '/scoring'
      )
    ).toBe('/keywords?view=scores')
  })
})

describe('SocialPanel', () => {
  it('klasik sihirbazı ve mod seçicisini göstermez', () => {
    render(
      <MemoryRouter>
        <SocialPanel runId={42} />
      </MemoryRouter>
    )
    expect(screen.queryByText('Klasik Sihirbaz (Legacy)')).toBeNull()
    expect(screen.queryByRole('tablist', { name: 'Sosyal üretim modu' })).toBeNull()
  })

  it('enable_social=false ise uyarı görünür', () => {
    render(
      <MemoryRouter>
        <SocialPanel
          runId={42}
          run={{ id: 42, status: 'channel_assigned', enable_social: false } as never}
        />
      </MemoryRouter>
    )
    expect(screen.getByText(/SOCIAL kanalı devre dışı/)).toBeTruthy()
  })
})
