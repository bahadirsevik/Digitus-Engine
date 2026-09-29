/**
 * Sosyal brief üretim istekleri için idempotency anahtarı yönetimi.
 *
 * Sözleşme (plan §6): arayüz anahtarı İŞLEM başına bir kez üretir. Aynı niyetin
 * tekrarı (ör. ağ yanıtı kaybolduktan sonra yeniden tıklama) AYNI anahtarla
 * gider ve backend aynı denemeyi döndürür. Anahtar yalnız istek kesin bir
 * sonuca ulaşınca (başarı veya "yeni anahtarla dene" diyen ret) bırakılır.
 *
 * Anahtar workspace + brief + aşama kapsamında tutulur; `fingerprint` niyetin
 * parametrelerini temsil eder — parametre değişirse yeni niyet = yeni anahtar.
 * Depolama yalnız kolaylıktır: localStorage erişilemezse bellek içi haritaya düşer.
 * Gerçek kaynak her zaman sunucudaki attempt kaydıdır.
 */
import { apiErrorStatus } from '../../../services/api'

export type SocialIdemStage = 'categories' | 'ideas' | 'ideas_retry' | 'contents'

export interface SocialIdemScope {
  workspaceId: number
  briefId: number
  stage: SocialIdemStage
}

interface StoredKey {
  fp: string
  key: string
  at: number
}

const PREFIX = 'digitus.socialIdem.v1'
const MAX_AGE_MS = 24 * 60 * 60 * 1000
const memory = new Map<string, StoredKey>()

function storageKey(scope: SocialIdemScope): string {
  return `${PREFIX}:${scope.workspaceId}:${scope.briefId}:${scope.stage}`
}

function read(scope: SocialIdemScope): StoredKey | null {
  const k = storageKey(scope)
  try {
    const raw = window.localStorage.getItem(k)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<StoredKey>
      if (
        typeof parsed.fp === 'string' &&
        typeof parsed.key === 'string' &&
        typeof parsed.at === 'number'
      ) {
        return { fp: parsed.fp, key: parsed.key, at: parsed.at }
      }
    }
  } catch {
    // localStorage erişilemez → bellek içi yedek
  }
  return memory.get(k) ?? null
}

function write(scope: SocialIdemScope, value: StoredKey) {
  const k = storageKey(scope)
  memory.set(k, value)
  try {
    window.localStorage.setItem(k, JSON.stringify(value))
  } catch {
    // yok say: bellek içi kopya yeterli
  }
}

function newKey(stage: SocialIdemStage): string {
  const rand =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
  return `${stage}-${rand}`.slice(0, 128)
}

/** Aynı niyet için bekleyen anahtarı döndürür; yoksa yenisini üretip saklar. */
export function getOrCreateIdemKey(scope: SocialIdemScope, fingerprint: string): string {
  const existing = read(scope)
  if (existing && existing.fp === fingerprint && Date.now() - existing.at < MAX_AGE_MS) {
    return existing.key
  }
  const created: StoredKey = { fp: fingerprint, key: newKey(scope.stage), at: Date.now() }
  write(scope, created)
  return created.key
}

/** Bekleyen (yanıtı alınmamış) bir niyet var mı? UI "tekrar dene" metni için. */
export function hasPendingIdemKey(scope: SocialIdemScope, fingerprint?: string): boolean {
  const existing = read(scope)
  if (!existing || Date.now() - existing.at >= MAX_AGE_MS) return false
  return fingerprint === undefined || existing.fp === fingerprint
}

export function clearIdemKey(scope: SocialIdemScope) {
  const k = storageKey(scope)
  memory.delete(k)
  try {
    window.localStorage.removeItem(k)
  } catch {
    // yok say
  }
}

/** USP gibi serbest metni anahtar parmak izine düz metin koymadan katmak için. */
export function textFingerprint(text: string): string {
  let h = 5381
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0
  return `${text.length}:${(h >>> 0).toString(36)}`
}

/**
 * Bir hatanın ardından anahtar bırakılmalı mı?
 * - Yanıt yok (ağ hatası) veya genel 5xx: istek sunucuda işlenmiş olabilir →
 *   anahtarı KORU, aynı niyetin tekrarı aynı denemeyi bulsun.
 * - 4xx, 503 (kuyruk hatası → deneme failed yapıldı) veya
 *   `retry_with_new_idempotency_key`: sonuç kesin → anahtarı BIRAK.
 */
export function shouldReleaseIdemKey(err: unknown): boolean {
  const status = apiErrorStatus(err)
  if (status === 0) return false
  if (hasRetryWithNewKeyFlag(err)) return true
  if (status === 503) return true
  if (status >= 500) return false
  return status >= 400
}

export function hasRetryWithNewKeyFlag(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false
  const detail =
    (err as { detail?: unknown }).detail ??
    (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  return Boolean(
    detail &&
    typeof detail === 'object' &&
    (detail as { retry_with_new_idempotency_key?: unknown }).retry_with_new_idempotency_key === true
  )
}
