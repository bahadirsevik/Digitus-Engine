import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AnalysisChannels } from '../components/AnalysisProgress'

export interface TrackedAnalysisTask {
  taskId: string
  runId: number
  workspaceId: number
  channels: AnalysisChannels
}

interface AnalysisToastStore {
  task: TrackedAnalysisTask | null
  /** Kapatılan görevin kimliği; yeni görev kimliği toast'ı yeniden görünür kılar. */
  dismissedTaskId: string | null
  track: (task: TrackedAnalysisTask) => void
  dismiss: () => void
}

const same = (a: TrackedAnalysisTask | null, b: TrackedAnalysisTask) =>
  !!a &&
  a.taskId === b.taskId &&
  a.runId === b.runId &&
  a.workspaceId === b.workspaceId &&
  a.channels.ads === b.channels.ads &&
  a.channels.seo === b.channels.seo &&
  a.channels.social === b.channels.social

export const useAnalysisToastStore = create<AnalysisToastStore>()(
  persist(
    (set, get) => ({
      task: null,
      dismissedTaskId: null,
      track: (task) => {
        if (!same(get().task, task)) set({ task })
      },
      dismiss: () => set((s) => ({ dismissedTaskId: s.task?.taskId ?? s.dismissedTaskId })),
    }),
    { name: 'analysis-toast-store' }
  )
)
