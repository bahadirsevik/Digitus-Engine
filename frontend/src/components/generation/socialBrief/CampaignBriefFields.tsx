/**
 * "Reklam Kampanyası" brief'inin kampanya bölümü (plan_sosyal_kampanya_akisi.md §1, K2).
 * Tür seçimi formdan önce yapılır (BriefKindChooser); bu bileşen yalnız kampanya türünde görünür.
 * Kurallar backend'de (app/core/social/campaign_spec.py); burada yalnız zorunlu alan uyarısı.
 */
import type { SocialOfferType } from '../../../services/api'
import { DESTINATION_LABEL, OBJECTIVE_LABEL, OFFER_TYPE_LABEL, USAGE_LABEL } from './labels'
import { OFFER_TYPES, type CampaignFormState } from './campaignForm'

interface Props {
  value: CampaignFormState
  onChange: (next: CampaignFormState) => void
}

export default function CampaignBriefFields({ value: s, onChange }: Props) {
  const set = <K extends keyof CampaignFormState>(key: K, v: CampaignFormState[K]) =>
    onChange({ ...s, [key]: v })
  const select = <K extends 'usage' | 'objective' | 'destination'>(
    key: K,
    label: string,
    labels: Record<string, string>
  ) => (
    <div className="sb-target-builder-field">
      <label htmlFor={`campaign-${key}`}>{label}</label>
      <select
        id={`campaign-${key}`}
        className="sb-select"
        value={s[key]}
        onChange={(e) => set(key, e.target.value as CampaignFormState[K])}
      >
        {Object.entries(labels).map(([id, text]) => (
          <option key={id} value={id}>
            {text}
          </option>
        ))}
      </select>
    </div>
  )

  return (
    <div className="sb-form-group sb-campaign-section">
      <div className="sb-form-heading">3. Kampanya Bilgileri</div>
      <p className="sb-form-desc">
        Teklif koşullarını (oran, tutar, şart) yalnız siz verirsiniz; içerik üretmeden önce bir kez
        daha onaylanır.
      </p>

      <div className="sb-campaign-fields">
        <div className="sb-target-builder">
          {select('usage', 'Nerede yayınlanacak?', USAGE_LABEL)}
          {select('objective', 'Hedef', OBJECTIVE_LABEL)}
          {select('destination', 'Yönlendirme', DESTINATION_LABEL)}
        </div>

        <label htmlFor="campaign-promoted">Tanıtılacak ürün / hizmet</label>
        <input
          id="campaign-promoted"
          className="sb-input"
          maxLength={200}
          value={s.promotedItem}
          onChange={(e) => set('promotedItem', e.target.value)}
          placeholder="Örn: Başlangıç cilt bakım seti"
        />
        <label htmlFor="campaign-audience">Hedef kitle (opsiyonel)</label>
        <input
          id="campaign-audience"
          className="sb-input"
          maxLength={200}
          value={s.audience}
          onChange={(e) => set('audience', e.target.value)}
        />
        <label htmlFor="campaign-focus">Reklamda neye odaklanılsın? (opsiyonel)</label>
        <input
          id="campaign-focus"
          className="sb-input"
          maxLength={300}
          value={s.focusNote}
          onChange={(e) => set('focusNote', e.target.value)}
        />
        <label htmlFor="campaign-features">Bu pakete dahil özellikler (opsiyonel)</label>
        <textarea
          id="campaign-features"
          className="sb-input"
          rows={3}
          maxLength={500}
          value={s.includedFeatures}
          onChange={(e) => set('includedFeatures', e.target.value)}
          placeholder="Örn: Sınırsız portföy takibi, günlük temel analiz raporu"
          aria-describedby="campaign-features-hint"
        />
        <p id="campaign-features-hint" className="sb-muted-note">
          Yalnız bu pakette gerçekten bulunan özellikleri yazın. Reklamda yalnız bunlar “pakete
          dahil” diye anılır; boş bırakırsanız özellikler markanın genel özellikleri olarak geçer.
        </p>

        <fieldset className="sb-campaign-mode">
          <legend>Teklif</legend>
          <label>
            <input
              type="radio"
              name="campaign-mode"
              checked={s.mode === 'suggest'}
              onChange={() => set('mode', 'suggest')}
            />
            Teklifi sistem önersin
          </label>
          <label>
            <input
              type="radio"
              name="campaign-mode"
              checked={s.mode === 'defined'}
              onChange={() => set('mode', 'defined')}
            />
            Teklifim hazır, kendim gireceğim
          </label>
        </fieldset>

        {s.mode === 'suggest' ? (
          <fieldset className="sb-campaign-disallowed">
            <legend>Önerilmesin (opsiyonel)</legend>
            {OFFER_TYPES.map((t) => (
              <label key={t}>
                <input
                  type="checkbox"
                  checked={s.disallowed.includes(t)}
                  onChange={(e) =>
                    set(
                      'disallowed',
                      e.target.checked ? [...s.disallowed, t] : s.disallowed.filter((x) => x !== t)
                    )
                  }
                />
                {OFFER_TYPE_LABEL[t]}
              </label>
            ))}
          </fieldset>
        ) : (
          <fieldset className="sb-campaign-draft">
            <legend>Teklif taslağı (içerik üretmeden önce yeniden onaylanır)</legend>
            <label htmlFor="campaign-draft-type">Teklif türü</label>
            <select
              id="campaign-draft-type"
              className="sb-select"
              value={s.draftType}
              onChange={(e) => set('draftType', e.target.value as SocialOfferType)}
            >
              {OFFER_TYPES.map((t) => (
                <option key={t} value={t}>
                  {OFFER_TYPE_LABEL[t]}
                </option>
              ))}
            </select>
            <label htmlFor="campaign-draft-value">Teklif detayı</label>
            <input
              id="campaign-draft-value"
              className="sb-input"
              maxLength={120}
              value={s.draftValue}
              onChange={(e) => set('draftValue', e.target.value)}
              placeholder="Örn: %20 · 2 al 1 öde · ücretsiz kargo"
            />
            <label htmlFor="campaign-draft-applies">Geçerli olduğu ürün / kapsam</label>
            <input
              id="campaign-draft-applies"
              className="sb-input"
              maxLength={200}
              value={s.draftAppliesTo}
              onChange={(e) => set('draftAppliesTo', e.target.value)}
            />
            <label htmlFor="campaign-draft-conditions">Şartlar (opsiyonel)</label>
            <input
              id="campaign-draft-conditions"
              className="sb-input"
              maxLength={300}
              value={s.draftConditions}
              onChange={(e) => set('draftConditions', e.target.value)}
              placeholder="Örn: 500 TL üzeri, kod: YAZ20"
            />
            <label htmlFor="campaign-draft-ends">Bitiş tarihi (opsiyonel)</label>
            <input
              id="campaign-draft-ends"
              type="date"
              className="sb-input"
              value={s.draftEnds}
              onChange={(e) => set('draftEnds', e.target.value)}
            />
          </fieldset>
        )}
      </div>
    </div>
  )
}
