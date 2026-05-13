import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface ActiveWorkspace {
  id: number
  name: string
  company_url: string
  status: string
  profile_data: Record<string, unknown> | null
  suggested_keywords: string[] | null
  default_geo_target_id?: string | null
  default_language_id?: string | null
}

interface BrandStore {
  activeWorkspace: ActiveWorkspace | null
  setActiveWorkspace: (ws: ActiveWorkspace | null) => void
  clearWorkspace: () => void
}

export const useBrandStore = create<BrandStore>()(
  persist(
    (set) => ({
      activeWorkspace: null,
      setActiveWorkspace: (ws) => set({ activeWorkspace: ws }),
      clearWorkspace: () => set({ activeWorkspace: null }),
    }),
    {
      name: 'brand-store',
    }
  )
)
