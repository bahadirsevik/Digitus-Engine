// Ekranda ham motor/durum kodu yerine Türkçe metin. API kodları değişmez;
// bilinmeyen kod gelirse ham kod basılmaz, güvenli karşılık döner.

const RUN_STATUS_LABEL: Record<string, string> = {
  draft: 'Taslak',
  pending: 'Bekliyor',
  scoring: 'Skorlanıyor',
  scored: 'Skorlandı',
  relevance_computing: 'İlgi skoru hesaplanıyor',
  relevance_computed: 'İlgi skoru hazır',
  intent_analysis: 'Niyet analizi sürüyor',
  channel_assigning: 'Kanal analizi sürüyor',
  channel_assigned: 'Kanal havuzları hazır',
  completed: 'Tamamlandı',
  failed: 'Başarısız',
}

export function runStatusLabel(status?: string | null): string {
  return (status && RUN_STATUS_LABEL[status]) || 'Durum bilgisi alınamadı'
}

// Kanal seçim gerekçeleri (engine_selections.exclude_reason). Bazıları
// "KOD:ayrıntı" biçiminde gelir (ör. COMPETITOR_TERM:Xero).
// neutral=true: normal seçim sonucu (hata gibi gösterilmez).
const REASON_LABEL: Record<string, { label: string; neutral: boolean }> = {
  CAPACITY_LIMIT: { label: 'Kanal kapasitesi nedeniyle seçilmedi', neutral: true },
  SEO_NOT_PRIMARY: { label: 'SEO’da ana havuza seçilmedi', neutral: true },
  COMPETITOR_TERM: { label: 'Rakip terimi', neutral: false },
  TOPIC_EXCLUDED: { label: 'Hariç tutulan konu', neutral: false },
  LOCATION_CITY_FILTER: { label: 'Konum filtresine takıldı', neutral: false },
  LOCATION_NON_FOCUS_CITY: { label: 'Odak dışı şehir', neutral: false },
  EXCLUDED: { label: 'Filtre nedeniyle seçilmedi', neutral: false },
}

export function selectionReasonLabel(reason: string): { label: string; neutral: boolean } {
  const sep = reason.indexOf(':')
  const code = sep < 0 ? reason : reason.slice(0, sep)
  const detail = sep < 0 ? '' : reason.slice(sep + 1).trim()
  const known = REASON_LABEL[code]
  if (!known) return { label: 'Seçilmedi', neutral: true }
  return { label: detail ? `${known.label}: ${detail}` : known.label, neutral: known.neutral }
}

// Havuz sınıfı kanal bağlamında farklı anlam taşır. ADS'de her aday "primary"
// olduğundan bilgi taşımaz → gösterilmez.
const POOL_CLASS_LABEL: Record<string, Record<string, string>> = {
  ads: {},
  seo: {
    primary: 'Ana içerik adayı',
    secondary: 'Başka bir içerikle aynı sayfada',
    deferred: 'Ertelendi',
  },
  social: {
    PRIMARY: 'Öncelikli',
    TREND_CONTENT: 'Trend içeriği',
    SECONDARY: 'İkincil',
  },
}

export function poolClassLabel(
  channel: 'ads' | 'seo' | 'social',
  poolClass?: string | null
): string | null {
  return (poolClass && POOL_CLASS_LABEL[channel][poolClass]) || null
}
