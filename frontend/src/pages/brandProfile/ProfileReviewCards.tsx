/**
 * 5-kart profil onayı — Claude Design "Marka Profili" tasarımına göre
 * numaralı ProfileSection düzeni. İşleyiş değişmedi: kartlar düzenlenir,
 * "Onayla ve devam et" → profili kaydet → rakip keşfi teklifi.
 * company_name/sector backend'de kilitli (readonly gösterilir).
 */
import { useEffect, useState } from 'react'
import { Check, Loader2, AlertTriangle } from 'lucide-react'
import { workspaceApi, WorkspaceResponse } from '../../services/api'
import CompetitorReviewCard from './CompetitorReviewCard'
import { useCompetitorReview } from './useCompetitorReview'
import {
  WorkspaceListRow,
  listToTextarea,
  splitNewlineItems,
  extractErrorMessage,
  GEO_OPTIONS,
  HARD_EXCLUDE_HELP,
  HARD_EXCLUDE_LABEL,
  LANGUAGE_OPTIONS,
} from '../brandProfileState'
import LocationPolicyControl from '../../components/LocationPolicyControl'
import {
  LocationFilterMode,
  readFocusCities,
  readLocationExemptTerms,
  readLocationFilterMode,
} from '../../services/locationPolicy'

// Tek kelimelik dışlama temaları geniş eleme yapabilir — kullanıcıyı uyar.
function singleWordThemes(raw: string): string[] {
  return raw
    .split(/[,\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => item.split(/\s+/).length === 1)
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="bpx-section">
      <div className="bpx-section-title">
        <span className="num">{n}.</span> {title}
      </div>
      {children}
    </div>
  )
}

export default function ProfileReviewCards({
  workspace,
  onApproved,
}: {
  workspace: WorkspaceListRow
  onApproved: (workspace: WorkspaceResponse) => void
}) {
  const profile = (workspace.profile_data || {}) as Record<string, unknown>

  const [brandSummary, setBrandSummary] = useState('')
  const review = useCompetitorReview(workspace)
  const [products, setProducts] = useState('')
  const [services, setServices] = useState('')
  const [targetAudience, setTargetAudience] = useState('')
  const [geoId, setGeoId] = useState('2792')
  const [languageId, setLanguageId] = useState('1037')
  const [mustHave, setMustHave] = useState('')
  const [excluded, setExcluded] = useState('')
  const [protectedThemes, setProtectedThemes] = useState('')
  const [locationMode, setLocationMode] = useState<LocationFilterMode>('none')
  const [focusCities, setFocusCities] = useState<string[]>([])
  const [locationExemptTerms, setLocationExemptTerms] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setBrandSummary(typeof profile.brand_summary === 'string' ? profile.brand_summary : '')
    setProducts(listToTextarea(profile.products))
    setServices(listToTextarea(profile.services))
    setTargetAudience(typeof profile.target_audience === 'string' ? profile.target_audience : '')
    setGeoId(workspace.default_geo_target_id || '2792')
    setLanguageId(workspace.default_language_id || '1037')
    // Plan v13 düzeltmesi: mevcut değerler forma YÜKLENİR — boş textarea ile
    // tekrar onay, kaydedilmiş must-have/konu dışlamalarını sessizce silmesin
    setMustHave(workspace.preliminary_info || '')
    setExcluded(workspace.excluded_info || '')
    // P1.6: liste BOŞ olsa bile alan görünür ve düzenlenebilir. Eskiden yalnız
    // anchor grubu üzerinden düzenlenebiliyordu; grup boş listede hiç
    // oluşmadığı için ilk onay + ilk kanal ataması DAİMA korumasız koşuyordu.
    setProtectedThemes(listToTextarea(profile.protected_themes))
    setLocationMode(readLocationFilterMode(profile))
    setFocusCities(readFocusCities(profile))
    setLocationExemptTerms(readLocationExemptTerms(profile))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id])

  const singleWords = singleWordThemes(excluded)

  const handleApprove = async () => {
    setSubmitting(true)
    setError('')
    try {
      const { competitor_urls, competitor_decisions } = review.buildPayload()
      const res = await workspaceApi.approveProfile(workspace.id, {
        profile_data: {
          brand_summary: brandSummary.trim(),
          target_audience: targetAudience.trim(),
          products: splitNewlineItems(products),
          services: splitNewlineItems(services),
          protected_themes: splitNewlineItems(protectedThemes),
          location_filter_mode: locationMode,
          focus_cities: focusCities,
          location_exempt_terms: locationExemptTerms,
        },
        competitor_urls,
        competitor_decisions,
        must_have_info: mustHave.trim(),
        excluded_info: excluded.trim(),
        default_geo_target_id: geoId,
        default_language_id: languageId,
        rerun_keywords: false,
      })
      onApproved(res.data)
    } catch (err: unknown) {
      setError(extractErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="bpx-fade">
      <h3 className="bpx-h3" style={{ fontSize: 18, fontWeight: 700 }}>
        Markanızı böyle anladık
      </h3>
      <p className="bpx-sub" style={{ marginBottom: 20 }}>
        Kartları kontrol edip düzeltin. Onayladığınızda size özel keyword önerileri hazırlanır.
      </p>

      <Section n={1} title="Marka Özeti">
        <div className="bpx-grid-2" style={{ marginBottom: 12 }}>
          <div>
            <label className="bpx-label">Marka adı</label>
            <input
              className="bpx-input"
              type="text"
              value={String(profile.company_name || '')}
              readOnly
              title="Siteden çıkarıldı"
            />
          </div>
          <div>
            <label className="bpx-label">Sektör</label>
            <input
              className="bpx-input"
              type="text"
              value={String(profile.sector || '')}
              readOnly
              title="Siteden çıkarıldı"
            />
          </div>
        </div>
        <label className="bpx-label">Açıklama</label>
        <textarea
          className="bpx-textarea"
          rows={3}
          value={brandSummary}
          placeholder="Markayı 1-2 cümleyle özetleyin"
          onChange={(e) => setBrandSummary(e.target.value)}
        />
      </Section>

      <Section n={2} title="Rakipler">
        <p className="bpx-section-hint">
          En fazla 3 rakip URL'si ekleyebilirsiniz. Tespit edilen marka adını düzeltebilir veya
          "Rakip değil" diyebilirsiniz.
        </p>
        <CompetitorReviewCard review={review} />
      </Section>

      <Section n={3} title="Ürün ve Hizmetler">
        <div className="bpx-grid-2">
          <div>
            <label className="bpx-label">Ürünler (her satıra bir tane)</label>
            <textarea
              className="bpx-textarea"
              rows={5}
              placeholder="Her satıra bir ürün"
              value={products}
              onChange={(e) => setProducts(e.target.value)}
            />
          </div>
          <div>
            <label className="bpx-label">Hizmetler</label>
            <textarea
              className="bpx-textarea"
              rows={5}
              placeholder="Her satıra bir hizmet"
              value={services}
              onChange={(e) => setServices(e.target.value)}
            />
          </div>
        </div>
      </Section>

      <Section n={4} title="Hedef Kitle ve Bölge">
        <label className="bpx-label">Hedef kitle</label>
        <input
          className="bpx-input"
          type="text"
          placeholder="örn. BIST yatırımcıları"
          value={targetAudience}
          onChange={(e) => setTargetAudience(e.target.value)}
        />
        <div className="bpx-grid-2" style={{ marginTop: 12 }}>
          <div>
            <label className="bpx-label">Bölge</label>
            <select
              className="bpx-select"
              title="Bölge"
              value={geoId}
              onChange={(e) => setGeoId(e.target.value)}
            >
              {GEO_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="bpx-label">Dil</label>
            <select
              className="bpx-select"
              title="Dil"
              value={languageId}
              onChange={(e) => setLanguageId(e.target.value)}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <LocationPolicyControl
          workspaceId={workspace.id}
          companyName={String(profile.company_name || '')}
          brandTerms={Array.isArray(profile.brand_terms) ? (profile.brand_terms as string[]) : []}
          mode={locationMode}
          focusCities={focusCities}
          exemptTerms={locationExemptTerms}
          onModeChange={setLocationMode}
          onFocusCitiesChange={setFocusCities}
          onExemptTermsChange={setLocationExemptTerms}
          disabled={submitting}
        />
      </Section>

      <Section n={5} title="İstenen & İstenmeyen Konular">
        <label className="bpx-label">Mutlaka olması gerekenler</label>
        <textarea
          className="bpx-textarea"
          rows={2}
          placeholder='örn. "günlük hisse önerileri, canlı destek"'
          value={mustHave}
          onChange={(e) => setMustHave(e.target.value)}
        />
        <div style={{ height: 12 }} />
        <label className="bpx-label" htmlFor="review-hard-exclude">
          {HARD_EXCLUDE_LABEL}
        </label>
        <p className="bpx-section-hint" id="review-hard-exclude-help">
          {HARD_EXCLUDE_HELP}
        </p>
        <textarea
          id="review-hard-exclude"
          aria-describedby="review-hard-exclude-help"
          className="bpx-textarea"
          rows={2}
          placeholder='Dışlanacak konuları yazın — örn. "kripto para, temettü takibi"'
          value={excluded}
          onChange={(e) => setExcluded(e.target.value)}
        />
        {singleWords.length > 0 && (
          <p className="bpx-section-hint warning-hint" style={{ marginTop: 8 }}>
            <AlertTriangle size={12} /> Tek kelimelik dışlama temaları ({singleWords.join(', ')})
            geniş eleme yapabilir. Daha net yazın: "temettü takibi" gibi.
          </p>
        )}
        <div style={{ height: 12 }} />
        <label className="bpx-label">Korunacak konular</label>
        <textarea
          className="bpx-textarea"
          rows={2}
          placeholder='Dışlamaya rağmen kapsamda kalsın — örn. "saç boyası bakımı"'
          value={protectedThemes}
          onChange={(e) => setProtectedThemes(e.target.value)}
        />
        <p className="bpx-section-hint" style={{ marginTop: 8 }}>
          Buraya yazdığınız konular, geniş bir dışlama temasıyla çakışsa bile elenmez.
        </p>
      </Section>

      {error && <div className="error-banner">{error}</div>}

      <div className="bpx-modal-footer is-end bpx-inline-footer">
        <button
          type="button"
          className="bpx-btn-primary"
          onClick={handleApprove}
          disabled={submitting || !review.isValid}
        >
          {submitting ? (
            <Loader2 size={16} className="spin-icon" />
          ) : (
            <Check size={16} strokeWidth={2.4} />
          )}
          Onayla ve devam et
        </button>
      </div>
    </div>
  )
}
