/**
 * "Gelişmiş ayarlar" (plan v13 — eski "Rakip ve Konu Politikası" panelinin
 * küçültülmüş hali).
 *
 * - Rakip tespiti/onayı artık Rakipler kartında (profil onayı + kalıcı
 *   düzenleme bölümü) — "URL'lerden öner" ve konu serbest-ekleme KALKTI.
 * - KALAN: onaylı rakip terim chip'leri (provenance etiketli), kanal bazlı
 *   block/allow (conquest istisnaları), kompakt manuel terim ekle/kaldır,
 *   salt-görünür eski alias kuralları, havuz-bayat bandı.
 * - Chip mesajları HTTP işlem sonucundan değil provenance alanlarından
 *   (manual_approved / source_urls / legacy) türetilir: URL destekli terim
 *   manuel kaldırılınca approved kalabilir — bu sürpriz değil, açıklanır.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Ban, ChevronDown, ChevronRight, Plus, Settings2, X } from 'lucide-react'
import {
  brandProfileApi,
  dashboardApi,
  PolicyTermEntry,
  PoolFreshness,
  poolFreshnessLabel,
  WorkspacePolicyResponse,
} from '../services/api'
import './PolicyPanel.css'

const CHANNELS: Array<'ads' | 'seo' | 'social'> = ['ads', 'seo', 'social']

function provenanceLabel(term: PolicyTermEntry): string {
  if (term.legacy) return 'eski kayıt'
  const fromUrl = term.source_urls.length > 0
  if (fromUrl && term.manual_approved) return 'Rakip URL + Manuel'
  if (fromUrl) return 'Rakip URL'
  return 'Manuel'
}

export default function PolicyPanel({
  workspaceId,
  workspaceName,
  freshness,
}: {
  workspaceId: number
  workspaceName?: string
  freshness?: PoolFreshness | null
}) {
  const [policy, setPolicy] = useState<WorkspacePolicyResponse | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [newTerm, setNewTerm] = useState('')
  const [fetchedFreshness, setFetchedFreshness] = useState<PoolFreshness | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await brandProfileApi.getPolicy(workspaceId)
      setPolicy(res.data)
    } catch {
      setError('Politika yüklenemedi')
    }
    // Freshness prop'la gelmediyse dashboard özetinden alınır — band yalnız
    // testte değil gerçek ekranda da görünmeli. refresh:true ile cache bypass:
    // load() her mutasyondan sonra da çağrılır, band 15sn'lik Redis cache'e
    // takılıp bayat kalmasın (Codex notu).
    if (freshness === undefined) {
      try {
        const summary = await dashboardApi.workspaceSummary({
          brand_profile_id: workspaceId,
          refresh: true,
        })
        setFetchedFreshness(summary.data.pool_freshness ?? null)
      } catch {
        setFetchedFreshness(null) // fail-open: band gösterilmez, panel çalışır
      }
    }
  }, [workspaceId, freshness])

  useEffect(() => {
    void load()
  }, [load])

  const effectiveFreshness = freshness !== undefined ? freshness : fetchedFreshness

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    setInfo(null)
    try {
      await fn()
      await load()
    } catch {
      setError('İşlem başarısız')
    } finally {
      setBusy(false)
    }
  }

  const removeManual = (term: PolicyTermEntry) => {
    const stillBlockedByUrl = term.source_urls.length > 0
    void act(() =>
      brandProfileApi.upsertPolicyTerm(workspaceId, term.term, 'rejected', 'competitor')
    ).then(() => {
      if (stillBlockedByUrl) {
        setInfo(
          `"${term.term}" rakip URL kararından dolayı engellenmeye devam ediyor — ` +
            'tamamen kaldırmak için Rakipler kartını kullanın.'
        )
      }
    })
  }

  const setChannel = (channel: 'ads' | 'seo' | 'social', value: string) => {
    if (!policy) return
    const next = { ...policy.competitor_policy, [channel]: value }
    void act(() =>
      brandProfileApi.setCompetitorPolicy(
        workspaceId,
        next as { ads: string; seo: string; social: string }
      )
    )
  }

  if (!policy) return null

  const approvedTerms = policy.competitor_terms.filter((t) => t.status === 'approved')
  const approvedAliases = (policy.topic_policy?.excluded_aliases || []).filter(
    (t) => t.status === 'approved'
  )

  return (
    <section className="polx-card">
      <button type="button" className="polx-head polx-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        <Settings2 size={16} />
        <h3>Gelişmiş ayarlar{workspaceName ? ` — ${workspaceName}` : ''}</h3>
      </button>

      {effectiveFreshness?.channel_pool_stale && (
        <div className="polx-stale" data-testid="policy-stale-banner">
          <AlertTriangle size={14} />
          {poolFreshnessLabel(effectiveFreshness) + ' — kanal atamasını yenileyin.'}
        </div>
      )}

      {open && (
        <>
          {error && <div className="polx-error">{error}</div>}
          {info && <div className="polx-info">{info}</div>}

          <div className="polx-block">
            <span className="polx-label">
              <Ban size={13} /> Engellenen rakip terimleri
            </span>
            <p className="polx-hint">
              Rakip tespiti ve onayı Rakipler kartından yapılır; buradan yalnız manuel terim
              yönetilir. X = "Manuel onayı kaldır".
            </p>
            <div className="polx-terms">
              {approvedTerms.length === 0 && <span className="polx-empty">Terim yok</span>}
              {approvedTerms.map((t) => (
                <span key={t.term} className="polx-term is-approved">
                  {t.term}
                  <em
                    className="polx-prov"
                    title={
                      t.legacy
                        ? 'Eski kayıt — URL ile otomatik bağlanamıyor, elle kaldırılabilir'
                        : undefined
                    }
                  >
                    {provenanceLabel(t)}
                  </em>
                  <button
                    type="button"
                    title="Manuel onayı kaldır"
                    onClick={() => removeManual(t)}
                    disabled={busy}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
            <div className="polx-add">
              <input
                value={newTerm}
                onChange={(e) => setNewTerm(e.target.value)}
                placeholder="Manuel rakip terimi ekle"
              />
              <button
                type="button"
                disabled={busy || !newTerm.trim()}
                onClick={() => {
                  const v = newTerm.trim()
                  setNewTerm('')
                  void act(() =>
                    brandProfileApi.upsertPolicyTerm(workspaceId, v, 'approved', 'competitor')
                  )
                }}
              >
                <Plus size={13} /> Ekle
              </button>
            </div>
            <div className="polx-channels">
              {CHANNELS.map((ch) => (
                <label key={ch} className="polx-channel">
                  <span>{ch.toUpperCase()}</span>
                  <select
                    value={policy.competitor_policy[ch]}
                    onChange={(e) => setChannel(ch, e.target.value)}
                    disabled={busy}
                  >
                    <option value="block">Engelle</option>
                    <option value="allow">İzin ver (conquest)</option>
                  </select>
                </label>
              ))}
            </div>
          </div>

          {approvedAliases.length > 0 && (
            <div className="polx-block">
              <span className="polx-label">
                <Ban size={13} /> Eski alias kuralları
              </span>
              <p className="polx-hint">
                Önceki panelden kalan yazım-varyantı engelleri — görünür kalır, kaldırılabilir; yeni
                alias eklenmez (konu dışlamaları "Kesin dışlama"dan yönetilir).
              </p>
              <div className="polx-terms">
                {approvedAliases.map((t) => (
                  <span key={t.term} className="polx-term is-approved">
                    {t.term}
                    <button
                      type="button"
                      title="Kaldır"
                      onClick={() =>
                        act(() =>
                          brandProfileApi.upsertPolicyTerm(
                            workspaceId,
                            t.term,
                            'rejected',
                            'topic_alias'
                          )
                        )
                      }
                      disabled={busy}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
