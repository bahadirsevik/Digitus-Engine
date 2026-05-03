import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ActiveWorkspace {
  id: number;
  name: string;
  company_url: string;
  status: string;
  profile_data: any;
  suggested_keywords: string[] | null;
}

interface BrandStore {
  activeWorkspace: ActiveWorkspace | null;
  setActiveWorkspace: (ws: ActiveWorkspace | null) => void;
  clearWorkspace: () => void;
}

export const useBrandStore = create<BrandStore>()(
  persist(
    (set) => ({
      activeWorkspace: null,
      setActiveWorkspace: (ws) => set({ activeWorkspace: ws }),
      clearWorkspace: () => set({ activeWorkspace: null }),
    }),
    {
      name: "brand-store",
    },
  ),
);
