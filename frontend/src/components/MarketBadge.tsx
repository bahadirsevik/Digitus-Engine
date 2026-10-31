import { MARKET_SOURCE_NOTE } from '../services/market'
import './MarketSelect.css'

// Çalışmanın pazarı (örn. "Berlin, Almanya · Rusça") + hacim kaynağı.
// Etiket yoksa (eski kayıt / sunucu henüz vermedi) hiçbir şey çizilmez.
export default function MarketBadge({ label }: { label?: string | null }) {
  if (!label) return null
  return (
    <span className="market-badge" title="Hacim verisi Google Ads'ten gelir">
      {label} · {MARKET_SOURCE_NOTE}
    </span>
  )
}

// Salt-okunur "Pazar: ..." satırı (arama/çıkarma formlarında)
export function MarketLine({ label }: { label?: string | null }) {
  if (!label) return null
  return (
    <p className="market-line">
      Pazar: {label} · {MARKET_SOURCE_NOTE}
    </p>
  )
}
