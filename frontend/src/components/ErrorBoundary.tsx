import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Değişince (ör. rota) hata durumu sıfırlanır; alt ağaç gereksiz yere yeniden kurulmaz */
  resetKey?: string
}

interface State {
  failed: boolean
}

/**
 * Render sırasında fırlayan beklenmeyen hatayı yakalar; kullanıcıya yığın izi
 * göstermez (yalnız console). Layout'ta rota çıktısını sarar ve rota değişince
 * `resetKey` ile sıfırlanır — böylece gezinti menüsü çalışmaya devam eder.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidUpdate(prev: Props) {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false })
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Sayfa render hatası:', error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="error-banner" role="alert">
        <p>Bu sayfa beklenmedik bir hata nedeniyle görüntülenemedi.</p>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => window.location.reload()}
        >
          Sayfayı yenile
        </button>
      </div>
    )
  }
}
