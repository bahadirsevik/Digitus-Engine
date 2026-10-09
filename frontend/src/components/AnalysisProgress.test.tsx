import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AnalysisBanner, AnalysisToast } from './AnalysisProgress'

describe('AnalysisProgress server-controlled screening stage', () => {
  it('shows candidate prioritization inside the existing analysis banner', () => {
    render(
      <MemoryRouter>
        <AnalysisBanner stage="screening" taskProgress={0} onDismiss={vi.fn()} />
      </MemoryRouter>
    )

    expect(screen.getByText('Aday kelimeler önceliklendiriliyor')).toBeInTheDocument()
    expect(screen.queryByText(/DeepSeek/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /üst sınır/i })).not.toBeInTheDocument()
  })

  it('uses the same stage in the existing compact progress toast', () => {
    render(
      <MemoryRouter>
        <AnalysisToast stage="screening" taskProgress={0} onDismiss={vi.fn()} onOpen={vi.fn()} />
      </MemoryRouter>
    )

    expect(screen.getByText('Aday kelimeler önceliklendiriliyor')).toBeInTheDocument()
    expect(screen.getByText('2/8 adım')).toBeInTheDocument()
  })
})
