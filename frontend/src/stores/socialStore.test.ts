/**
 * setScoringRun: run değişince run'a bağlı state (kategori/fikir/içerik +
 * taskId) sıfırlanmalı ve persist edilen localStorage yansıması temizlenmeli;
 * aynı run'da hiçbir şey silinmemeli. (Codex bulgusu: eski kategorilerden
 * üretim sessizce ESKİ run'a yazılıyordu.)
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useSocialStore } from './socialStore'

const CATEGORY = { id: 11, name: 'Kat', description: 'Açıklama', keyword_count: 3 }
const IDEA = {
  id: 21,
  category_id: 11,
  idea_title: 'Fikir',
  idea_description: 'Desc',
  target_platform: 'Instagram',
  content_format: 'Reels',
  trend_alignment: 0.8,
  is_selected: false,
}

function seedRunState(runId: number) {
  const store = useSocialStore.getState()
  store.setFormData({ scoringRunId: runId, workspaceId: 1, brandName: 'Marka' })
  store.setCategories([CATEGORY])
  store.toggleCategory(11)
  store.setIdeas([IDEA])
  store.toggleIdea(21)
  store.setTaskId('task-abc')
  store.setStep(3)
}

describe('socialStore.setScoringRun', () => {
  beforeEach(() => {
    window.localStorage.clear()
    useSocialStore.getState().reset()
  })

  it('run değişince run-bağımlı state ve taskId sıfırlanır, step 1 olur', () => {
    seedRunState(5)
    useSocialStore.getState().setScoringRun(9)

    const s = useSocialStore.getState()
    expect(s.scoringRunId).toBe(9)
    expect(s.step).toBe(1)
    expect(s.categories).toEqual([])
    expect(s.selectedCategoryIds).toEqual([])
    expect(s.ideas).toEqual([])
    expect(s.selectedIdeaIds).toEqual([])
    expect(s.contents).toEqual([])
    expect(s.taskId).toBeNull()
    // Form alanları korunur (run'a bağlı değil)
    expect(s.brandName).toBe('Marka')
  })

  it('persist edilen localStorage yansıması da temizlenir', () => {
    seedRunState(5)
    useSocialStore.getState().setScoringRun(9)

    const raw = window.localStorage.getItem('social-stepper-storage')
    expect(raw).toBeTruthy()
    const persisted = JSON.parse(raw as string).state
    expect(persisted.scoringRunId).toBe(9)
    expect(persisted.categories).toEqual([])
    expect(persisted.taskId).toBeNull()
  })

  it('aynı run tekrar set edilirse state korunur (no-op)', () => {
    seedRunState(5)
    useSocialStore.getState().setScoringRun(5)

    const s = useSocialStore.getState()
    expect(s.categories).toHaveLength(1)
    expect(s.selectedCategoryIds).toEqual([11])
    expect(s.taskId).toBe('task-abc')
    expect(s.step).toBe(3)
  })

  it('mevcut workspace-reset davranışı bozulmaz', () => {
    seedRunState(5)
    useSocialStore.getState().reset()

    const s = useSocialStore.getState()
    expect(s.scoringRunId).toBeNull()
    expect(s.categories).toEqual([])
    expect(s.brandName).toBe('')
  })
})

describe('socialStore persist v2 migrate (brief akışı, plan §8)', () => {
  it('v1 akış durumunu sıfırlar, marka alanlarını korur', async () => {
    const migrate = useSocialStore.persist.getOptions().migrate
    expect(useSocialStore.persist.getOptions().version).toBe(2)
    const migrated = (await migrate!(
      {
        brandName: 'Eski Marka',
        brandContext: 'Bağlam',
        scoringRunId: 7,
        step: 3,
        categories: [CATEGORY],
        ideas: [IDEA],
        taskId: 'task-old',
      },
      0
    )) as unknown as Record<string, unknown>
    expect(migrated.brandName).toBe('Eski Marka')
    expect(migrated.brandContext).toBe('Bağlam')
    expect(migrated.scoringRunId).toBeNull()
    expect(migrated.step).toBe(1)
    expect(migrated.categories).toEqual([])
    expect(migrated.ideas).toEqual([])
    expect(migrated.taskId).toBeNull()
  })
})
