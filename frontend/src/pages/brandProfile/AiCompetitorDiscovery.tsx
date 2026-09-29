import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, Loader2, RefreshCw, Search, X } from 'lucide-react'
import {
  brandProfileApi,
  type CompetitorDiscoveryDecision,
  type PolicyTermEntry,
  type WorkspacePolicyResponse,
} from '../../services/api'
import { extractErrorMessage } from '../brandProfileState'
import LoadingStep from './LoadingStep'

const POLL_MS = 1500

const DISCOVERY_STEPS = [
  'Google üzerinde rakipler aranıyor',
  'Aday siteler ziyaret edilip doğrulanıyor',
  'Öneriler hazırlanıyor',
]

export default function AiCompetitorDiscovery({
  workspaceId,
  confirmed,
  onChanged,
  variant = 'card',
  autoStart = false,
  onFinish,
}: {
  workspaceId: number
  confirmed: boolean
  onChanged?: () => void
  /** 'card': profil düzenleme yüzeyi; 'wizard': profil onayı sonrası tam ekran adım */
  variant?: 'card' | 'wizard'
  /** true ise mount'ta keşfi kendisi başlatır (sihirbazda "Evet" sonrası) */
  autoStart?: boolean
  /** wizard varyantında akışı bitirir (onay sonrası / geç butonu) */
  onFinish?: () => void
}) {
  const [policy, setPolicy] = useState<WorkspacePolicyResponse | null>(null)
  const [candidates, setCandidates] = useState<PolicyTermEntry[]>([])
  const [approvedAi, setApprovedAi] = useState<PolicyTermEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [terms, setTerms] = useState<Record<string, string>>({})
  const [progress, setProgress] = useState(0)
  const [running, setRunning] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [ranOnce, setRanOnce] = useState(false)
  const timerRef = useRef<number | null>(null)
  const autoStartedRef = useRef(false)

  const applyPolicy = useCallback((data: WorkspacePolicyResponse) => {
    const suggested = data.competitor_terms.filter(
      (entry) => entry.source === 'ai_discovery' && entry.status === 'suggested'
    )
    setPolicy(data)
    setCandidates(suggested)
    // Onaylananlar da görünür kalır — onaydan sonra "kayboluyor" izlenimi
    // vermemek için (kullanıcı geri bildirimi, 22.07)
    setApprovedAi(
      data.competitor_terms.filter(
        (entry) => entry.source === 'ai_discovery' && entry.status === 'approved'
      )
    )
    setTerms(Object.fromEntries(suggested.map((entry) => [entry.discovery_id || '', entry.term])))
    setSelected(
      new Set(
        suggested
          .filter((entry) => entry.confidence === 'high')
          .map((entry) => entry.discovery_id || '')
          .filter(Boolean)
      )
    )
  }, [])

  const loadPolicy = useCallback(async () => {
    const response = await brandProfileApi.getPolicy(workspaceId)
    applyPolicy(response.data)
  }, [applyPolicy, workspaceId])

  useEffect(() => {
    if (!confirmed) return
    void loadPolicy().catch((err: unknown) => setError(extractErrorMessage(err)))
  }, [confirmed, loadPolicy])

  const poll = useCallback(
    async (id: string) => {
      try {
        const { data } = await brandProfileApi.getCompetitorDiscovery(workspaceId, id)
        setProgress(data.progress || 0)
        if (data.status === 'pending' || data.status === 'running') {
          timerRef.current = window.setTimeout(() => void poll(id), POLL_MS)
          return
        }
        setRunning(false)
        setRanOnce(true)
        if (data.code === 'PROFILE_CHANGED') {
          setError('Profil işlem sırasında değişti. Rakip aramasını yeniden başlatın.')
          return
        }
        if (data.status === 'failed') {
          setError(data.error_message || 'Rakip keşfi tamamlanamadı.')
          return
        }
        await loadPolicy()
        setNotice(
          data.candidates.length
            ? `${data.candidates.length} doğrulanmış rakip önerisi bulundu.`
            : 'Güvenilir yeni rakip bulunamadı.'
        )
      } catch (err: unknown) {
        setRunning(false)
        setError(extractErrorMessage(err))
      }
    },
    [loadPolicy, workspaceId]
  )

  useEffect(
    () => () => {
      if (timerRef.current) window.clearTimeout(timerRef.current)
    },
    []
  )

  const startDiscovery = useCallback(
    async (forceRefresh = false) => {
      if (forceRefresh) {
        const accepted = window.confirm(
          'Önerileri yenilemek yeni Google arama ve AI maliyeti oluşturur. Devam edilsin mi?'
        )
        if (!accepted) return
      }
      setError('')
      setNotice('')
      setRunning(true)
      setProgress(0)
      try {
        const { data } = await brandProfileApi.startCompetitorDiscovery(workspaceId, forceRefresh)
        await poll(data.task_id)
      } catch (err: unknown) {
        setRunning(false)
        setError(extractErrorMessage(err))
      }
    },
    [poll, workspaceId]
  )

  // Profil-onayi akisi: kullanici "Evet" dediginde kesif kendisi baslar
  // (StrictMode çift-mount'una karşı ref guard'lı).
  useEffect(() => {
    if (!autoStart || !confirmed || autoStartedRef.current) return
    autoStartedRef.current = true
    void startDiscovery(false)
  }, [autoStart, confirmed, startDiscovery])

  const stale = Boolean(policy?.discovery_impact.has_stale_suggestions)
  const hasDiscoveryHistory = Boolean(
    policy?.competitor_terms.some((entry) => entry.source === 'ai_discovery')
  )
  const canForceRefresh = hasDiscoveryHistory || ranOnce
  const selectedCandidates = useMemo(
    () => candidates.filter((item) => item.discovery_id && selected.has(item.discovery_id)),
    [candidates, selected]
  )

  const submit = async (decisions: CompetitorDiscoveryDecision[]) => {
    setSaving(true)
    setError('')
    try {
      const response = await brandProfileApi.decideDiscoveredCompetitors(workspaceId, decisions)
      applyPolicy(response.data)
      setConfirmOpen(false)
      setNotice('Rakip politikası güncellendi. Mevcut kanal havuzlarını yeniden atayın.')
      onChanged?.()
      // Sihirbazda onay verildiyse yapılacak başka iş kalmadı — akışı bitir.
      if (variant === 'wizard' && decisions.some((d) => d.decision === 'approved')) {
        onFinish?.()
      }
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const reject = (candidate: PolicyTermEntry) => {
    if (!candidate.discovery_id) return
    void submit([
      {
        discovery_id: candidate.discovery_id,
        decision: 'rejected',
        term: terms[candidate.discovery_id] || candidate.term,
      },
    ])
  }

  const removeApproved = (entry: PolicyTermEntry) => {
    if (!entry.discovery_id) return
    const accepted = window.confirm(
      `"${entry.term}" engel listesinden çıkarılacak; güncel kanal havuzları bayatlayabilir. Devam edilsin mi?`
    )
    if (!accepted) return
    void submit([{ discovery_id: entry.discovery_id, decision: 'rejected', term: entry.term }])
  }

  if (!confirmed) {
    return (
      <div className="acd-shell is-disabled">
        <div className="acd-heading">AI Önerilen Rakipler</div>
        <p>Rakip keşfi marka profili onaylandıktan sonra kullanılabilir.</p>
      </div>
    )
  }

  // Sihirbazda koşarken tam ekran yükleme adımı (genel tasarım dili)
  if (variant === 'wizard' && running) {
    return (
      <div data-testid="ai-competitor-discovery">
        <LoadingStep
          title="Rakipleriniz araştırılıyor…"
          sub={`Google araması ve AI doğrulaması sürüyor (%${progress}). Bu 1-2 dakika sürebilir.`}
          steps={DISCOVERY_STEPS}
        />
      </div>
    )
  }

  return (
    <div
      className={`acd-shell${variant === 'wizard' ? ' is-wizard' : ''}`}
      data-testid="ai-competitor-discovery"
    >
      <div className="acd-header">
        <div>
          <div className="acd-heading">AI Önerilen Rakipler</div>
          <p>Doğrulanmış şirketleri inceleyip engellenecek marka adlarını seçin.</p>
        </div>
        <button
          type="button"
          className="bpx-btn-secondary"
          onClick={() => void startDiscovery(canForceRefresh)}
          disabled={running}
        >
          {running ? (
            <Loader2 size={15} className="spin-icon" />
          ) : canForceRefresh ? (
            <RefreshCw size={15} />
          ) : (
            <Search size={15} />
          )}
          {canForceRefresh ? 'Önerileri yenile' : 'AI ile rakip bul'}
        </button>
      </div>

      {running && (
        <div className="acd-progress" aria-live="polite">
          <div className="acd-progress-track">
            <span style={{ width: `${Math.max(progress, 8)}%` }} />
          </div>
          <small>Rakipler araştırılıyor · %{progress}</small>
        </div>
      )}
      {stale && (
        <div className="acd-warning">
          <AlertTriangle size={15} /> Profil değişti, önerileri yenileyin. Onay devre dışı.
        </div>
      )}
      {error && (
        <div className="acd-warning">
          <AlertTriangle size={15} /> {error}
        </div>
      )}
      {notice && <div className="acd-notice">{notice}</div>}

      {candidates.length > 0 && (
        <div className="acd-list">
          {candidates.map((candidate) => {
            const id = candidate.discovery_id || ''
            return (
              <div className="acd-row" key={id}>
                <input
                  type="checkbox"
                  aria-label={`${candidate.term} seç`}
                  checked={selected.has(id)}
                  onChange={(event) => {
                    const next = new Set(selected)
                    event.target.checked ? next.add(id) : next.delete(id)
                    setSelected(next)
                  }}
                />
                <div className="acd-main">
                  <div className="acd-name-line">
                    <input
                      value={terms[id] ?? candidate.term}
                      onChange={(event) => setTerms({ ...terms, [id]: event.target.value })}
                      aria-label="Engellenecek marka adı"
                    />
                    <span className={`acd-confidence ${candidate.confidence}`}>
                      {candidate.confidence === 'high' ? 'Yüksek güven' : 'Orta güven'}
                    </span>
                  </div>
                  <a href={candidate.candidate_url} target="_blank" rel="noreferrer">
                    {candidate.candidate_domain}
                  </a>
                  <p>{candidate.rationale}</p>
                </div>
                <button
                  type="button"
                  className="acd-reject"
                  title="Öneriyi reddet"
                  onClick={() => reject(candidate)}
                  disabled={saving || stale}
                >
                  <X size={16} />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {approvedAi.length > 0 && (
        <div className="acd-approved">
          <div className="acd-approved-title">Engellenen AI rakipleri ({approvedAi.length})</div>
          {approvedAi.map((entry) => (
            <div className="acd-approved-row" key={entry.discovery_id || entry.term}>
              <div className="acd-approved-main">
                <span className="acd-approved-term">{entry.term}</span>
                {entry.candidate_domain && (
                  <a href={entry.candidate_url} target="_blank" rel="noreferrer">
                    {entry.candidate_domain}
                  </a>
                )}
              </div>
              <span className="acd-approved-badge">Engelleniyor</span>
              <button
                type="button"
                className="acd-reject"
                title="Engellemeden çıkar"
                onClick={() => removeApproved(entry)}
                disabled={saving}
              >
                <X size={16} />
              </button>
            </div>
          ))}
        </div>
      )}

      {(selectedCandidates.length > 0 || variant === 'wizard') && (
        <div className="acd-actions">
          {variant === 'wizard' && (
            <button
              type="button"
              className="bpx-btn-ghost"
              onClick={() => onFinish?.()}
              disabled={saving}
            >
              {candidates.length > 0 ? 'Şimdilik geç' : ranOnce ? 'Devam et' : 'Şimdilik geç'}
            </button>
          )}
          {selectedCandidates.length > 0 && (
            <button
              type="button"
              className="bpx-btn-primary"
              onClick={() => setConfirmOpen(true)}
              disabled={saving || stale}
            >
              <Check size={15} /> {selectedCandidates.length} rakibi onayla
            </button>
          )}
        </div>
      )}

      {confirmOpen && (
        <div className="acd-modal-backdrop" role="presentation">
          <div className="acd-modal" role="dialog" aria-modal="true" aria-label="Rakipleri onayla">
            <h3>Rakipleri engelleme politikasına ekle</h3>
            <p>
              {selectedCandidates.length} rakip onaylanacak.{' '}
              <strong>{policy?.discovery_impact.fresh_channel_run_count || 0}</strong> güncel kanal
              çalışması bayatlayacak ve kanal atamasının yeniden çalıştırılması gerekecek.
            </p>
            <div className="acd-modal-actions">
              <button
                type="button"
                className="bpx-btn-secondary"
                onClick={() => setConfirmOpen(false)}
              >
                Vazgeç
              </button>
              <button
                type="button"
                className="bpx-btn-primary"
                disabled={saving}
                onClick={() =>
                  void submit(
                    selectedCandidates.map((candidate) => ({
                      discovery_id: candidate.discovery_id || '',
                      decision: 'approved',
                      term: (terms[candidate.discovery_id || ''] || candidate.term).trim(),
                    }))
                  )
                }
              >
                {saving ? <Loader2 size={15} className="spin-icon" /> : <Check size={15} />}
                Onayla
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
