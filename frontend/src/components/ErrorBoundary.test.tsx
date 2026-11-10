import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ErrorBoundary from './ErrorBoundary'

function Boom({ explode }: { explode: boolean }) {
  if (explode) throw new Error('gizli yığın izi')
  return <p>sağlam sayfa</p>
}

describe('ErrorBoundary', () => {
  afterEach(() => vi.restoreAllMocks())

  it('render hatasında yedek ekranı gösterir, hata ayrıntısını kullanıcıya sızdırmaz', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Boom explode />
      </ErrorBoundary>
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Bu sayfa beklenmedik bir hata nedeniyle görüntülenemedi.'
    )
    expect(screen.getByRole('button', { name: 'Sayfayı yenile' })).toBeInTheDocument()
    expect(screen.queryByText(/gizli yığın izi/)).toBeNull()
  })

  it('resetKey değişince (rota değişimi) tekrar çocuğu gösterir', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(
      <ErrorBoundary resetKey="/a">
        <Boom explode />
      </ErrorBoundary>
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    rerender(
      <ErrorBoundary resetKey="/b">
        <Boom explode={false} />
      </ErrorBoundary>
    )
    expect(screen.getByText('sağlam sayfa')).toBeInTheDocument()
  })
})
