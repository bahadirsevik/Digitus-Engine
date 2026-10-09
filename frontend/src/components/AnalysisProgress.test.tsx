import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AnalysisBanner, AnalysisToast } from './AnalysisProgress'
import type { AnalysisChannels, ChainStage } from './AnalysisProgress'

function renderBanner(props: {
  stage?: ChainStage
  taskProgress?: number
  channels?: AnalysisChannels
  message?: string | null
  errorMessage?: string | null
}) {
  return render(
    <MemoryRouter>
      <AnalysisBanner
        stage={props.stage ?? 'assigning'}
        taskProgress={props.taskProgress ?? 0}
        channels={props.channels}
        message={props.message}
        errorMessage={props.errorMessage}
        onDismiss={vi.fn()}
      />
    </MemoryRouter>
  )
}

/** Banner'daki adımları [etiket, durum] sırasıyla döndürür. */
function stepStates(): Array<[string, string]> {
  return Array.from(document.querySelectorAll('.kwx-step')).map((el) => {
    const state = /is-(done|active|todo)/.exec(el.className)?.[1] ?? '?'
    return [(el.textContent ?? '').trim(), state]
  })
}

const ALL = { ads: true, seo: true, social: true }

describe('AnalysisProgress V3 steps', () => {
  it.each([
    [0, 'Analiz başladı'],
    [15, 'Kelime aileleri'],
    [40, 'ADS'],
    [65, 'SEO'],
    [85, 'SOCIAL'],
    [92, 'Havuz teslimi'],
  ])('all channels: progress %i activates "%s" and finishes earlier steps', (progress, label) => {
    renderBanner({ taskProgress: progress, channels: ALL })
    const states = stepStates()
    expect(states.map(([l]) => l)).toEqual([
      'Analiz başladı',
      'Kelime aileleri',
      'ADS',
      'SEO',
      'SOCIAL',
      'Havuz teslimi',
    ])
    const activeIdx = states.findIndex(([l]) => l === label)
    states.forEach(([, state], i) => {
      expect(state).toBe(i < activeIdx ? 'done' : i === activeIdx ? 'active' : 'todo')
    })
    expect(document.querySelector('.kwx-progressbar-fill')).toHaveStyle({ width: `${progress}%` })
  })

  it('shows the engine message and 100% with every step done when finished', () => {
    renderBanner({ stage: 'done', channels: ALL })
    expect(stepStates().every(([, s]) => s === 'done')).toBe(true)
    expect(document.querySelector('.kwx-progressbar-fill')).toHaveStyle({ width: '100%' })
    expect(screen.getByText(/Analiz tamamlandı/)).toBeInTheDocument()
  })

  it('renders result_data.current_message while running', () => {
    renderBanner({ taskProgress: 40, channels: ALL, message: 'ADS Niche motoru çalışıyor' })
    expect(screen.getByTestId('analysis-message')).toHaveTextContent('ADS Niche motoru çalışıyor')
  })

  it('only SOCIAL enabled: no family/ADS/SEO steps and SOCIAL is active at 85', () => {
    renderBanner({
      taskProgress: 85,
      channels: { ads: false, seo: false, social: true },
    })
    expect(stepStates()).toEqual([
      ['Analiz başladı', 'done'],
      ['SOCIAL', 'active'],
      ['Havuz teslimi', 'todo'],
    ])
  })

  it('only SOCIAL enabled: still on "Analiz başladı" before the SOCIAL point', () => {
    renderBanner({ taskProgress: 5, channels: { ads: false, seo: false, social: true } })
    expect(stepStates()[0]).toEqual(['Analiz başladı', 'active'])
  })

  it('ADS+SEO only: family, ADS, SEO and delivery; no SOCIAL step', () => {
    renderBanner({ taskProgress: 65, channels: { ads: true, seo: true, social: false } })
    expect(stepStates()).toEqual([
      ['Analiz başladı', 'done'],
      ['Kelime aileleri', 'done'],
      ['ADS', 'done'],
      ['SEO', 'active'],
      ['Havuz teslimi', 'todo'],
    ])
  })

  it('SEO-only run keeps the family step but drops ADS', () => {
    renderBanner({ taskProgress: 15, channels: { ads: false, seo: true, social: false } })
    expect(stepStates().map(([l]) => l)).toEqual([
      'Analiz başladı',
      'Kelime aileleri',
      'SEO',
      'Havuz teslimi',
    ])
  })

  it('never shows the retired relevance or screening steps', () => {
    renderBanner({ taskProgress: 40, channels: ALL })
    expect(screen.queryByText(/İlgi skoru/)).not.toBeInTheDocument()
    expect(screen.queryByText(/önceliklendiriliyor/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Skorlama hesaplanıyor/)).not.toBeInTheDocument()
  })

  it('failed stage shows the error and marks no step active', () => {
    renderBanner({ stage: 'failed', taskProgress: 40, channels: ALL, errorMessage: 'patladı' })
    expect(screen.getByText('Analiz sırasında hata oluştu')).toBeInTheDocument()
    expect(screen.getByText('patladı')).toBeInTheDocument()
    expect(stepStates().some(([, s]) => s === 'active')).toBe(false)
  })

  it('toast: shows the engine message and step counter', () => {
    render(
      <MemoryRouter>
        <AnalysisToast
          stage="assigning"
          taskProgress={65}
          channels={ALL}
          message="SEO katı-2 motoru çalışıyor"
          onDismiss={vi.fn()}
          onOpen={vi.fn()}
        />
      </MemoryRouter>
    )
    expect(screen.getByText('SEO katı-2 motoru çalışıyor')).toBeInTheDocument()
    expect(screen.getByText('4/6 adım')).toBeInTheDocument()
    expect(screen.getByText('65%')).toBeInTheDocument()
  })

  it('toast: falls back to the active step label without a message', () => {
    render(
      <MemoryRouter>
        <AnalysisToast
          stage="assigning"
          taskProgress={40}
          channels={ALL}
          onDismiss={vi.fn()}
          onOpen={vi.fn()}
        />
      </MemoryRouter>
    )
    expect(screen.getByText('ADS')).toBeInTheDocument()
  })
})
