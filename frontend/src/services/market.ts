// Pazar (hedef konum + çıktı dili) yardımcıları — plan_pazar_dil_secimi.md rev.4

export const MARKET_LOCKED_MESSAGE =
  'Bu çalışmada veri var; hedef konum/dil değiştirilemez. Yeni pazar çalışması açın.'

export const MARKET_SOURCE_NOTE = 'Hacim: Google Ads'

// "Berlin, Almanya · Rusça" → "Berlin, Almanya" (son " · " dil kısmıdır)
export function locationPartOfMarketLabel(label?: string | null): string | null {
  if (!label) return null
  const idx = label.lastIndexOf(' · ')
  return idx > 0 ? label.slice(0, idx) : label
}

export interface MarketValue {
  geo_target_id: string | null
  language_id: string | null
}

// Yanıtla dönen, Google'a gerçekten gönderilen pazar
export interface FetchedMarket {
  geo_target_id: string
  language_id: string
  market_label?: string
}

export function fetchedMarketFrom(data: {
  geo_target_id?: string | null
  language_id?: string | null
  market_label?: string | null
}): FetchedMarket | null {
  if (!data.geo_target_id || !data.language_id) return null
  return {
    geo_target_id: String(data.geo_target_id),
    language_id: String(data.language_id),
    market_label: data.market_label || undefined,
  }
}

export function marketWorkspaceLink(workspaceId: number): string {
  return `/brand-profile?workspace_id=${workspaceId}`
}
