/**
 * Paylaşılan "Rakipler" kartı (plan v13).
 *
 * İki bağlam: onboarding profil onayı (ProfileReviewCards) ve onay-sonrası
 * kalıcı düzenleme (PolicyReviewSection). State ve kanonik URL mantığı
 * useCompetitorReview hook'unda (ayrı dosya — react-refresh kuralı).
 * "Onayla", dokunulmayan satırın varsayılan 'engelle' kararını da GERÇEK
 * onay olarak gönderir; alttaki not bunu açıkça söyler. mismatch yalnız
 * görünür uyarıdır, kararı değiştirmez.
 */
import { AlertTriangle, Ban, Check, Loader2 } from 'lucide-react'
import type { UseCompetitorReview } from './useCompetitorReview'

export default function CompetitorReviewCard({ review }: { review: UseCompetitorReview }) {
  const filled = review.urls.map((u) => u.trim())
  return (
    <div className="bpx-stack" data-testid="competitor-review-card">
      {review.urls.map((value, idx) => {
        const url = filled[idx]
        const row = url ? review.rowFor(url) : null
        const comp = url ? review.validationFor(url) : null
        return (
          <div key={idx} className="crc-row">
            <input
              className="bpx-input"
              type="text"
              placeholder={`Rakip ${idx + 1} (https://...)`}
              value={value}
              onChange={(e) => review.setUrl(idx, e.target.value)}
            />
            {url && row && (
              <div className="crc-decision">
                <div className="crc-name">
                  <label className="bpx-label">Tespit edilen marka</label>
                  <input
                    className="bpx-input"
                    type="text"
                    placeholder="Marka adını girin"
                    value={row.term}
                    onChange={(e) => review.setTerm(url, e.target.value)}
                  />
                  {row.decision === 'block' && !row.term.trim() && (
                    <p className="bpx-section-hint warning-hint">
                      <AlertTriangle size={12} /> Engelleme için marka adı gerekli
                    </p>
                  )}
                </div>
                <div className="crc-actions">
                  <button
                    type="button"
                    className={`crc-btn ${row.decision === 'block' ? 'is-active' : ''}`}
                    onClick={() => review.setDecision(url, 'block')}
                  >
                    <Ban size={13} /> Rakip olarak engelle
                  </button>
                  <button
                    type="button"
                    className={`crc-btn ${row.decision === 'not_competitor' ? 'is-active' : ''}`}
                    onClick={() => review.setDecision(url, 'not_competitor')}
                  >
                    <Check size={13} /> Rakip değil
                  </button>
                </div>
                {comp?.status === 'mismatch' && (
                  <p className="bpx-section-hint warning-hint">
                    <AlertTriangle size={12} /> Bu site farklı bir sektörde görünüyor — kontrol
                    edin.
                  </p>
                )}
                {comp?.summary && <p className="bpx-section-hint">{comp.summary}</p>}
              </div>
            )}
          </div>
        )
      })}
      {review.duplicateError && (
        <p className="bpx-section-hint warning-hint">
          <AlertTriangle size={12} /> {review.duplicateError}
        </p>
      )}
      <div className="crc-footer">
        <button
          type="button"
          className="bpx-btn-raised"
          onClick={review.runDetection}
          disabled={review.detecting || !filled.some(Boolean)}
        >
          {review.detecting ? <Loader2 size={13} className="spin-icon" /> : null} Marka adlarını
          tespit et
        </button>
        <p className="bpx-section-hint">
          Rakip olarak işaretlenenler onayla birlikte ADS, SEO ve SOCIAL kanallarında otomatik
          engellenecek — yanlışsa &quot;Rakip değil&quot; seçin.
        </p>
      </div>
    </div>
  )
}
