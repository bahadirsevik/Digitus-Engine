import { create } from 'zustand'
import { persist } from 'zustand/middleware'

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
  setActiveWorkspace: (ws: ActiveWorkspace | null) => void
  clearWorkspace: () => void
  setSeedDraft: (workspaceId: number, draft: SeedDraft) => void
}

export const useBrandStore = create<BrandStore>()(
  persist(
    (set) => ({
      activeWorkspace: null,
      seedDrafts: {},
      setActiveWorkspace: (ws) => set({ activeWorkspace: ws }),
      clearWorkspace: () => set({ activeWorkspace: null }),
      setSeedDraft: (workspaceId, draft) =>
        set((s) => ({ seedDrafts: { ...s.seedDrafts, [workspaceId]: draft } })),
    }),
    {
      name: 'brand-store',
    }
  )
)
