/**
 * Kampanya modu form durumu ve istek dönüştürücüleri (plan_sosyal_kampanya_akisi.md §1-§2).
 * Kurallar backend'de (app/core/social/campaign_spec.py); burada yalnız zorunlu alan uyarısı.
 */
import type {
  SocialCampaignSpec,
  SocialGeneratedIdeaResponse,
  SocialOfferTerms,
  SocialOfferType,
} from '../../../services/api'

export const OFFER_TYPES: SocialOfferType[] = [
  'percent_discount',
  'bundle',
  'free_shipping',
  'gift',
]

export interface CampaignFormState {
  enabled: boolean
  mode: 'defined' | 'suggest'
  usage: SocialCampaignSpec['usage']
  objective: SocialCampaignSpec['objective']
  destination: SocialCampaignSpec['destination']
  promotedItem: string
  audience: string
  focusNote: string
  includedFeatures: string
  disallowed: SocialOfferType[]
  draftType: SocialOfferType
  draftValue: string
  draftAppliesTo: string
  draftConditions: string
  draftEnds: string
}

export const EMPTY_CAMPAIGN: CampaignFormState = {
  enabled: false,
  mode: 'suggest',
  usage: 'organic',
  objective: 'sales',
  destination: 'site',
  promotedItem: '',
  audience: '',
  focusNote: '',
  includedFeatures: '',
  disallowed: [],
  draftType: 'percent_discount',
  draftValue: '',
  draftAppliesTo: '',
  draftConditions: '',
  draftEnds: '',
}

const opt = (v: string) => v.trim() || null

/** Kapalıysa null (organik brief); açıksa backend'e giden kampanya bölümü. */
export function toCampaignRequest(s: CampaignFormState): SocialCampaignSpec | null {
  if (!s.enabled) return null
  return {
    mode: s.mode,
    usage: s.usage,
    objective: s.objective,
    destination: s.destination,
    promoted_item: s.promotedItem.trim(),
    audience: opt(s.audience),
    focus_note: opt(s.focusNote),
    included_features: opt(s.includedFeatures),
    disallowed_offer_types: s.mode === 'suggest' ? s.disallowed : [],
    offer_draft:
      s.mode === 'defined'
        ? {
            type: s.draftType,
            value: s.draftValue.trim(),
            applies_to: s.draftAppliesTo.trim(),
            conditions: opt(s.draftConditions),
            ends: s.draftEnds || null,
          }
        : null,
  }
}

export function campaignFormError(s: CampaignFormState): string | null {
  if (!s.enabled) return null
  if (!s.promotedItem.trim()) return 'Kampanyada tanıtılacak ürün veya hizmeti yazın.'
  if (s.mode === 'defined' && (!s.draftValue.trim() || !s.draftAppliesTo.trim()))
    return 'Kampanyanız belliyse teklif detayını ve geçerli olduğu ürünü/kapsamı yazın.'
  if (s.mode === 'suggest' && s.disallowed.length === OFFER_TYPES.length)
    return 'En az bir teklif türüne izin verin.'
  return null
}

// ---- İçerik öncesi teklif netleştirme ----

export interface OfferFormState {
  mode: 'terms' | 'none'
  value: string
  appliesTo: string
  conditions: string
  ends: string
}

/** "Kampanyam belli" yolunda brief taslağı ön-doldurulur (yine burada onaylanır). */
export function initialOfferForm(spec: SocialCampaignSpec | null | undefined): OfferFormState {
  const d = spec?.offer_draft
  return {
    mode: 'terms',
    value: d?.value ?? '',
    appliesTo: d?.applies_to ?? spec?.promoted_item ?? '',
    conditions: d?.conditions ?? '',
    ends: d?.ends ?? '',
  }
}

/** Seçilen fikirlerin ortak teklif türü; farklıysa null (bir istekte tek teklif). */
export function commonOfferType(ideas: (SocialGeneratedIdeaResponse | undefined)[]) {
  const types = new Set(ideas.map((i) => i?.campaign_payload?.offer_type))
  if (types.size !== 1) return null
  return ([...types][0] ?? null) as SocialOfferType | null
}

/** İsteğe giden offer_terms ya da kullanıcıya gösterilecek hata metni. */
export function buildOfferTerms(
  form: OfferFormState,
  offerType: SocialOfferType | null
): { terms: SocialOfferTerms } | { error: string } {
  if (form.mode === 'none') return { terms: { type: 'none' } }
  if (!offerType)
    return {
      error:
        "Seçilen fikirlerin teklif türleri farklı. Aynı türden fikirleri seçin ya da 'Teklifsiz devam et' deyin.",
    }
  if (!form.value.trim() || !form.appliesTo.trim())
    return { error: 'Teklif detayını ve geçerli olduğu ürünü/kapsamı yazın.' }
  return {
    terms: {
      type: offerType,
      value: form.value.trim(),
      applies_to: form.appliesTo.trim(),
      conditions: form.conditions.trim() || null,
      ends: form.ends || null,
    },
  }
}
