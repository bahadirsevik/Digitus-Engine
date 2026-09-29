import { useCallback, useEffect, useMemo, useState } from 'react'

/** Brief anahtar kelime seçimi için API sınırı (format matrisi yüklenene kadar varsayılan). */
export const DEFAULT_MAX_BRIEF_KEYWORDS = 5

export interface BriefKeywordSelection {
  /** Bu seçimin ait olduğu workspace+run kapsamı. */
  scopeKey: string
  selectedIds: number[]
  /** "Yeni brief" formu açık mı? Üst havuz kartları yalnız bu durumda seçilebilir. */
  composing: boolean
  /** Sınır aşımı gibi kullanıcıya gösterilecek son uyarı. */
  notice: string | null
  maxKeywords: number
  isSelected: (keywordId: number) => boolean
  /** Ekler/çıkarır; sınır aşılırsa değiştirmez ve false döner. */
  toggle: (keywordId: number) => boolean
  remove: (keywordId: number) => void
  /** Yalnız verilen kimlikleri tutar (havuzdan düşen kelimeleri temizlemek için). */
  retainOnly: (keywordIds: number[]) => void
  clear: () => void
  setComposing: (composing: boolean) => void
  setMaxKeywords: (max: number) => void
}

interface State {
  scopeKey: string
  ids: number[]
  composing: boolean
  notice: string | null
}

const empty = (scopeKey: string): State => ({ scopeKey, ids: [], composing: false, notice: null })

/**
 * Brief kelime seçiminin TEK durumu. Üst "Social Havuzu" kartları ve brief formundaki
 * liste aynı nesneyi okur/yazar. Kapsam (workspace+run) değişince durum render anında
 * boş sayılır — eski kapsamın seçimi yeni ekranda bir kare bile görünmez.
 */
export function useBriefKeywordSelection(scopeKey: string): BriefKeywordSelection {
  const [raw, setRaw] = useState<State>(() => empty(scopeKey))
  const [maxKeywords, setMaxKeywordsState] = useState(DEFAULT_MAX_BRIEF_KEYWORDS)
  const state = raw.scopeKey === scopeKey ? raw : empty(scopeKey)
  // Render anında boş sayılan eski kapsam durumu KALICI olarak atılır: aynı kapsama
  // geri dönüldüğünde eski seçim yeniden görünmez.
  useEffect(() => {
    setRaw((prev) => (prev.scopeKey === scopeKey ? prev : empty(scopeKey)))
  }, [scopeKey])

  const update = useCallback(
    (fn: (s: State) => State) =>
      setRaw((prev) => fn(prev.scopeKey === scopeKey ? prev : empty(scopeKey))),
    [scopeKey]
  )

  const toggle = useCallback(
    (keywordId: number) => {
      // Karar render edilmiş duruma göre verilir (dönüş değeri güvenilir olsun);
      // güncelleme yine fonksiyonel uygulanır.
      const has = state.ids.includes(keywordId)
      if (!has && state.ids.length >= maxKeywords) {
        update((s) => ({ ...s, notice: `En fazla ${maxKeywords} anahtar kelime seçebilirsiniz.` }))
        return false
      }
      update((s) => {
        if (s.ids.includes(keywordId)) {
          return { ...s, ids: s.ids.filter((id) => id !== keywordId), notice: null }
        }
        if (s.ids.length >= maxKeywords) return s
        return { ...s, ids: [...s.ids, keywordId], notice: null }
      })
      return true
    },
    [update, maxKeywords, state.ids]
  )

  const remove = useCallback(
    (keywordId: number) =>
      update((s) => ({ ...s, ids: s.ids.filter((id) => id !== keywordId), notice: null })),
    [update]
  )

  const retainOnly = useCallback(
    (keywordIds: number[]) =>
      update((s) => {
        const keep = new Set(keywordIds)
        const ids = s.ids.filter((id) => keep.has(id))
        return ids.length === s.ids.length ? s : { ...s, ids }
      }),
    [update]
  )

  const clear = useCallback(() => update((s) => ({ ...s, ids: [], notice: null })), [update])

  const setComposing = useCallback(
    (composing: boolean) =>
      update((s) => (s.composing === composing ? s : { ...s, composing, notice: null })),
    [update]
  )

  const setMaxKeywords = useCallback((max: number) => {
    if (Number.isInteger(max) && max > 0) setMaxKeywordsState(max)
  }, [])

  return useMemo(
    () => ({
      scopeKey,
      selectedIds: state.ids,
      composing: state.composing,
      notice: state.notice,
      maxKeywords,
      isSelected: (keywordId: number) => state.ids.includes(keywordId),
      toggle,
      remove,
      retainOnly,
      clear,
      setComposing,
      setMaxKeywords,
    }),
    [scopeKey, state, maxKeywords, toggle, remove, retainOnly, clear, setComposing, setMaxKeywords]
  )
}
