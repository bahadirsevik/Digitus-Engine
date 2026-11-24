/**
 * Kampanya içeriği öncesi teklif netleştirme (plan_sosyal_kampanya_akisi.md §2, K3/K4).
 * Tür seçilen fikirlerden gelir; değer/kapsam/şart/bitişi kullanıcı verir ya da "teklifsiz devam".
 */
import type { SocialOfferType } from '../../../services/api'
import { OFFER_TYPE_LABEL } from './labels'
import type { OfferFormState } from './campaignForm'

interface Props {
  briefId: number
  form: OfferFormState
  offerType: SocialOfferType | null
  selectedCount: number
  onChange: (next: OfferFormState) => void
}

export default function OfferTermsPanel({
  briefId,
  form,
  offerType,
  selectedCount,
  onChange,
}: Props) {
  const set = <K extends keyof OfferFormState>(k: K, v: OfferFormState[K]) =>
    onChange({ ...form, [k]: v })
  const id = (name: string) => `sb-offer-${name}-${briefId}`
  return (
    <fieldset className="sb-offer-panel">
      <legend>Teklif koşulları</legend>
      <p className="sb-form-desc">
        İçerikte yalnız burada yazdığınız teklif kullanılır; uymayan içerik kaydedilmez.
      </p>
      <label>
        <input
          type="radio"
          name={id('mode')}
          checked={form.mode === 'terms'}
          onChange={() => set('mode', 'terms')}
        />
        Teklif koşullarını gir
      </label>
      <label>
        <input
          type="radio"
          name={id('mode')}
          checked={form.mode === 'none'}
          onChange={() => set('mode', 'none')}
        />
        Teklifsiz devam et (içerikte hiçbir teklif dili olmaz)
      </label>

      {form.mode === 'terms' && (
        <div className="sb-offer-fields">
          <p className="sb-muted" role="status">
            Teklif türü:{' '}
            {selectedCount === 0 ? (
              'fikir seçince belirlenir'
            ) : offerType ? (
              <b>{OFFER_TYPE_LABEL[offerType]}</b>
            ) : (
              <b>seçilen fikirlerde farklı — aynı türden fikir seçin</b>
            )}
          </p>
          <label htmlFor={id('value')}>Teklif detayı</label>
          <input
            id={id('value')}
            className="sb-input"
            maxLength={120}
            value={form.value}
            onChange={(e) => set('value', e.target.value)}
            placeholder="Örn: %20 · 2 al 1 öde · ücretsiz kargo"
          />
          <label htmlFor={id('applies')}>Geçerli olduğu ürün / kapsam</label>
          <input
            id={id('applies')}
            className="sb-input"
            maxLength={200}
            value={form.appliesTo}
            onChange={(e) => set('appliesTo', e.target.value)}
          />
          <label htmlFor={id('conditions')}>Şartlar (opsiyonel)</label>
          <input
            id={id('conditions')}
            className="sb-input"
            maxLength={300}
            value={form.conditions}
            onChange={(e) => set('conditions', e.target.value)}
            placeholder="Örn: 500 TL üzeri, kod: YAZ20"
          />
          <label htmlFor={id('ends')}>Bitiş tarihi (opsiyonel)</label>
          <input
            id={id('ends')}
            type="date"
            className="sb-input"
            value={form.ends}
            onChange={(e) => set('ends', e.target.value)}
          />
        </div>
      )}
    </fieldset>
  )
}
