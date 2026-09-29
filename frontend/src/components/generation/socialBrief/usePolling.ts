import { useEffect, useRef } from 'react'

/** Varsayılan yoklama aralığı; testler bileşen prop'u ile kısaltır. */
export const SOCIAL_POLL_INTERVAL_MS = 3000

/**
 * `active` true olduğu sürece `tick`'i sıralı biçimde (bir önceki bitmeden
 * yenisi başlamaz) aralıklarla çağırır. Bileşen söküldüğünde veya `active`
 * false olduğunda durur; geç gelen yanıtlar çağıranın kapsam korumasıyla atılır.
 */
export function usePolling(
  active: boolean,
  tick: () => Promise<unknown> | void,
  intervalMs: number = SOCIAL_POLL_INTERVAL_MS
) {
  const tickRef = useRef(tick)
  tickRef.current = tick

  useEffect(() => {
    if (!active) return
    let cancelled = false
    let timer: number | undefined
    const loop = async () => {
      if (cancelled) return
      try {
        await tickRef.current()
      } catch {
        // tick kendi hatasını gösterir; yoklama durmaz
      } finally {
        if (!cancelled) timer = window.setTimeout(loop, intervalMs)
      }
    }
    timer = window.setTimeout(loop, intervalMs)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [active, intervalMs])
}

/** Söküldükten sonra state yazımını engellemek için basit canlılık bayrağı. */
export function useAliveRef() {
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  return alive
}
