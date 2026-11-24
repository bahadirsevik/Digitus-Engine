/**
 * Brief içeriklerinin "Revize Et" durumu: en son revizyonlar (süren / hazır öneri / başarısız),
 * revizyon sürerken yoklama, gönderim (aynı talebin tekrarı aynı anahtarla) ve kabul/vazgeç.
 * Öneri "Kabul Et" denene kadar canlı içeriğe yazılmaz (app/core/social/content_revision.py).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  apiErrorStatus,
  socialBriefApi,
  type SocialContentRevisionItem,
  type SocialRevisionScope,
} from '../../../services/api'
import { socialErrorText } from './labels'
import { useAliveRef, usePolling } from './usePolling'

const newKey = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`

export function useSocialRevisions({
  briefId,
  workspaceId,
  pollIntervalMs,
  onAccepted,
}: {
  briefId: number
  workspaceId: number
  pollIntervalMs: number
  onAccepted: () => Promise<void>
}) {
  const alive = useAliveRef()
  const [items, setItems] = useState<SocialContentRevisionItem[]>([])
  const [busy, setBusy] = useState<Record<number, boolean>>({})
  const [errors, setErrors] = useState<Record<number, string>>({})
  // Gönderim anahtarı yalnız AYNI talebin tekrarında yeniden kullanılır
  const keyRef = useRef({ signature: '', key: '' })
  const inFlight = useRef(false)
  // Sıra numarası: geç dönen eski okuma, sonradan yazılmış güncel listeyi ezmesin
  const seq = useRef(0)

  const load = useCallback(async () => {
    const mine = ++seq.current
    try {
      const res = await socialBriefApi.listRevisions(briefId, workspaceId)
      if (alive.current && mine === seq.current) setItems(res.data.items)
    } catch {
      // Revizyon durumu okunamazsa kartlar mevcut içerikle çalışmaya devam eder
    }
  }, [briefId, workspaceId, alive])

  useEffect(() => {
    void load()
  }, [load])

  const generating = items.some((i) => i.status === 'generating')
  usePolling(generating, load, pollIntervalMs)

  const byContent = useMemo(() => new Map(items.map((i) => [i.content_id, i])), [items])

  const submit = async (
    contentId: number,
    feedback: string,
    scope: SocialRevisionScope[]
  ): Promise<string | null> => {
    if (inFlight.current) return null
    inFlight.current = true
    const signature = JSON.stringify([contentId, feedback, scope])
    if (keyRef.current.signature !== signature) keyRef.current = { signature, key: newKey() }
    try {
      const res = await socialBriefApi.reviseContent(
        briefId,
        contentId,
        { feedback, scope, idempotency_key: keyRef.current.key },
        workspaceId
      )
      if (alive.current) {
        seq.current++ // uçuştaki yoklama yanıtı bu listeyi ezmesin
        setItems(res.data.items)
        setErrors((e) => {
          const next = { ...e }
          delete next[contentId]
          return next
        })
      }
      keyRef.current = { signature: '', key: '' }
      return null
    } catch (err) {
      // Anahtar yalnız ağ hatasında korunur (istek sunucuya ulaşmamış olabilir). Sunucu yanıt
      // verdiyse (ör. 503 kuyruğa alınamadı) aynı anahtar bitmiş denemeyi yeniden oynatırdı.
      if (apiErrorStatus(err) !== 0) keyRef.current = { signature: '', key: '' }
      return socialErrorText(err, 'Revizyon başlatılamadı. Yeniden deneyin.')
    } finally {
      inFlight.current = false
    }
  }

  const decide = async (item: SocialContentRevisionItem, action: 'accept' | 'discard') => {
    if (busy[item.id]) return
    setBusy((b) => ({ ...b, [item.id]: true }))
    setErrors((e) => {
      const next = { ...e }
      delete next[item.content_id]
      return next
    })
    try {
      if (action === 'accept') {
        await socialBriefApi.acceptRevision(item.id, workspaceId)
        await onAccepted()
      } else {
        await socialBriefApi.discardRevision(item.id, workspaceId)
      }
      await load()
    } catch (err) {
      if (!alive.current) return
      setErrors((e) => ({
        ...e,
        [item.content_id]: socialErrorText(
          err,
          action === 'accept' ? 'Öneri uygulanamadı.' : 'İşlem tamamlanamadı.'
        ),
      }))
      await load()
    } finally {
      if (alive.current) setBusy((b) => ({ ...b, [item.id]: false }))
    }
  }

  return { byContent, generating, busy, errors, submit, decide }
}
