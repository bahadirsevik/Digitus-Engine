/**
 * "Rakipler ve Konu Dışlamaları" — onay sonrası / legacy kalıcı düzenleme
 * yüzeyi (plan v13). Onboarding kartıyla AYNI paylaşılan bileşeni kullanır;
 * submit PUT /policy/review'a gider (backend, onboarding approve ile aynı
 * çekirdeği koşar). must_have bu yüzeyde YOK (endpoint sözleşmesi).
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Loader2 } from 'lucide-react'
import { brandProfileApi } from '../../services/api'
import CompetitorReviewCard from './CompetitorReviewCard'
import { useCompetitorReview } from './useCompetitorReview'
import { extractErrorMessage, HARD_EXCLUDE_HELP, HARD_EXCLUDE_LABEL } from '../brandProfileState'
import AiCompetitorDiscovery from './AiCompetitorDiscovery'

export default function PolicyReviewSection({
  workspace,
  onSaved,
}: {
  workspace: {
    id: number
    competitor_urls?: string[] | null
    competitor_url_decisions?: Record<string, unknown> | null
    validation_data?: Record<string, unknown> | null
    excluded_info?: string | null
    status?: string
  }
  onSaved?: () => void
}) {
  const review = useCompetitorReview(workspace)
  const [excluded, setExcluded] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [savedAt, setSavedAt] = useState<number | null>(null)

  useEffect(() => {
    setExcluded(workspace.excluded_info || '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const { competitor_urls, competitor_decisions } = review.buildPayload()
      await brandProfileApi.policyReview(workspace.id, {
        competitor_urls,
        competitor_decisions,
        excluded_info: excluded.trim(),
      })
      setSavedAt(Date.now())
      onSaved?.()
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bpx-section" data-testid="policy-review-section">
      <div className="bpx-section-title">Rakipler ve Konu Dışlamaları</div>
      <CompetitorReviewCard review={review} />
      <AiCompetitorDiscovery
        workspaceId={workspace.id}
        confirmed={workspace.status === 'confirmed'}
        onChanged={onSaved}
      />
      <label className="bpx-label" htmlFor="policy-hard-exclude">
        {HARD_EXCLUDE_LABEL}
      </label>
      <p className="bpx-section-hint" id="policy-hard-exclude-help">
        {HARD_EXCLUDE_HELP}
      </p>
      <textarea
        id="policy-hard-exclude"
        aria-describedby="policy-hard-exclude-help"
        className="bpx-textarea"
        rows={2}
        placeholder='Dışlanacak konuları yazın — örn. "kripto para, temettü takibi"'
        value={excluded}
        onChange={(e) => setExcluded(e.target.value)}
      />
      {error && (
        <p className="bpx-section-hint warning-hint">
          <AlertTriangle size={12} /> {error}
        </p>
      )}
      <div className="crc-footer">
        <button
          type="button"
          className="bpx-btn-primary"
          onClick={handleSave}
          disabled={saving || !review.isValid}
        >
          {saving ? <Loader2 size={14} className="spin-icon" /> : <Check size={14} />} Politikayı
          kaydet
        </button>
        {savedAt && !error && <p className="bpx-section-hint">Kaydedildi.</p>}
      </div>
    </div>
  )
}
