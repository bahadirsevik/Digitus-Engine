import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { workspaceApi, type MarketOptions } from '../services/api'

export interface ActiveWorkspace {
  id: number
  name: string
  company_url: string
  status: string
  profile_data: Record<string, unknown> | null
  suggested_keywords: string[] | null
  competitor_urls?: string[] | null
  default_geo_target_id?: string | null
  default_language_id?: string | null
  market_label?: string | null
  market_location_label?: string | null
  market_locked?: boolean
}

// Keywords sayfasında düzenlenen seed listesi; base = türetildiği profil listesi
// (profil listesi değişirse taslak geçersiz sayılır)
export interface SeedDraft {
  base: string
  words: string[]
}

interface BrandStore {
  activeWorkspace: ActiveWorkspace | null
  seedDrafts: Record<number, SeedDraft>
  // Pazar seçenekleri (ülke/dil/varsayılan): bir kez yüklenir, kalıcı değildir
  marketOptions: MarketOptions | null
  marketOptionsError: string | null
  setActiveWorkspace: (ws: ActiveWorkspace | null) => void
  clearWorkspace: () => void
  setSeedDraft: (workspaceId: number, draft: SeedDraft) => void
  loadMarketOptions: (force?: boolean) => Promise<void>
}

let marketOptionsInFlight: Promise<void> | null = null

export const useBrandStore = create<BrandStore>()(
  persist(
    (set, get) => ({
      activeWorkspace: null,
      seedDrafts: {},
      marketOptions: null,
      marketOptionsError: null,
      setActiveWorkspace: (ws) => set({ activeWorkspace: ws }),
      clearWorkspace: () => set({ activeWorkspace: null }),
      setSeedDraft: (workspaceId, draft) =>
        set((s) => ({ seedDrafts: { ...s.seedDrafts, [workspaceId]: draft } })),
      loadMarketOptions: (force = false) => {
        if (!force && get().marketOptions) return Promise.resolve()
        if (marketOptionsInFlight) return marketOptionsInFlight
        marketOptionsInFlight = (async () => {
          try {
            const res = await workspaceApi.marketOptions()
            set({ marketOptions: res.data, marketOptionsError: null })
          } catch (err: unknown) {
            const msg = (err as { message?: unknown } | null)?.message
            set({
              marketOptionsError:
                typeof msg === 'string' && msg ? msg : 'Pazar seçenekleri yüklenemedi.',
            })
          } finally {
            marketOptionsInFlight = null
          }
        })()
        return marketOptionsInFlight
      },
    }),
    {
      name: 'brand-store',
      partialize: (s) => ({ activeWorkspace: s.activeWorkspace, seedDrafts: s.seedDrafts }),
    }
  )
)
