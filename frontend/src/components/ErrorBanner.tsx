/**
 * ErrorBanner Component
 * Debug-friendly hata gösterimi
 */
import { AlertCircle, RefreshCw, ChevronDown, ChevronUp, Copy, Check } from 'lucide-react'
import { useState } from 'react'
import './ErrorBanner.css'

interface ErrorBannerProps {
  error: string
  details?: string | Record<string, unknown>
  requestId?: string
  onRetry?: () => void
  onDismiss?: () => void
}

export default function ErrorBanner({ 
  error, 
  details, 
  requestId, 
  onRetry, 
  onDismiss 
}: ErrorBannerProps) {
  const [showDetails, setShowDetails] = useState(false)
  const [copied, setCopied] = useState(false)

  const detailsText = typeof details === 'string' 
    ? details 
    : details ? JSON.stringify(details, null, 2) : null

  const copyError = () => {
    const text = `Hata: ${error}\n${requestId ? `Istek ID: ${requestId}\n` : ''}${detailsText ? `Detaylar: ${detailsText}` : ''}`
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="error-banner">
      <div className="error-header">
        <div className="error-icon">
          <AlertCircle size={20} />
        </div>
        <div className="error-content">
          <span className="error-message">{error}</span>
          {requestId && (
            <span className="error-request-id">Istek ID: {requestId}</span>
          )}
        </div>
        <div className="error-actions">
          {detailsText && (
            <button 
              className="btn-icon" 
              onClick={() => setShowDetails(!showDetails)}
              title="Detayları göster"
            >
              {showDetails ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          )}
          <button className="btn-icon" onClick={copyError} title="Hatayı kopyala">
            {copied ? <Check size={16} /> : <Copy size={16} />}
          </button>
          {onRetry && (
            <button className="btn btn-sm btn-secondary" onClick={onRetry}>
              <RefreshCw size={14} />
              Tekrar Dene
            </button>
          )}
          {onDismiss && (
            <button className="btn-icon" onClick={onDismiss} title="Kapat">
              ×
            </button>
          )}
        </div>
      </div>
      
      {showDetails && detailsText && (
        <div className="error-details">
          <pre>{detailsText}</pre>
        </div>
      )}
    </div>
  )
}
