/**
 * Social Media Stepper Store
 * 3-aşamalı akış için form verisi koruma
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface Category {
  id: number
  name: string
  description: string
  keyword_count: number
}

interface Idea {
  id: number
  category_id: number
  idea_title: string
  idea_description: string
  target_platform: string
  content_format: string
  trend_alignment: number
  related_keyword?: string
  is_selected: boolean
}

interface SocialContent {
  id: number
  idea_id: number
  hooks: { text: string; style: string }[]
  caption: string
  scenario?: string
  visual_suggestion?: string
  video_concept?: string
  cta_text: string
  hashtags: string[]
  industry_posting_suggestion?: string
  platform_notes?: string
}

interface SocialStore {
  // Step
  step: number
  setStep: (step: number) => void
  
  // Form data
  scoringRunId: number | null
  brandName: string
  brandContext: string
  maxCategories: number
  ideasPerCategory: number
  maxContents: number
  
  // Results
  categories: Category[]
  selectedCategoryIds: number[]
  ideas: Idea[]
  selectedIdeaIds: number[]
  contents: SocialContent[]
  
  // Task
  taskId: string | null
  
  // Actions
  setFormData: (data: Partial<SocialStore>) => void
  setCategories: (categories: Category[]) => void
  toggleCategory: (id: number) => void
  setIdeas: (ideas: Idea[]) => void
  toggleIdea: (id: number) => void
  setContents: (contents: SocialContent[]) => void
  setTaskId: (taskId: string | null) => void
  reset: () => void
}

const initialState = {
  step: 1,
  scoringRunId: null,
  brandName: '',
  brandContext: '',
  maxCategories: 6,
  ideasPerCategory: 5,
  maxContents: 10,
  categories: [],
  selectedCategoryIds: [],
  ideas: [],
  selectedIdeaIds: [],
  contents: [],
  taskId: null,
}

export const useSocialStore = create<SocialStore>()(
  persist(
    (set) => ({
      ...initialState,
      
      setStep: (step) => set({ step }),
      
      setFormData: (data) => set((state) => ({ ...state, ...data })),
      
      setCategories: (categories) => set({ categories, selectedCategoryIds: [] }),
      
      toggleCategory: (id) => set((state) => ({
        selectedCategoryIds: state.selectedCategoryIds.includes(id)
          ? state.selectedCategoryIds.filter(cid => cid !== id)
          : [...state.selectedCategoryIds, id]
      })),
      
      setIdeas: (ideas) => set({ ideas, selectedIdeaIds: [] }),
      
      toggleIdea: (id) => set((state) => ({
        selectedIdeaIds: state.selectedIdeaIds.includes(id)
          ? state.selectedIdeaIds.filter(iid => iid !== id)
          : [...state.selectedIdeaIds, id]
      })),
      
      setContents: (contents) => set({ contents }),
      
      setTaskId: (taskId) => set({ taskId }),
      
      reset: () => set(initialState),
    }),
    {
      name: 'social-stepper-storage',
    }
  )
)
