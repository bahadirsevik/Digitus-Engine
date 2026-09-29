// Plan v4 (export dağıtımı): export job tür etiketleri — ExportMenu geçmişi ve
// Dashboard "Son dışa aktarım" kartı aynı etiketi üretir.

export const FORMAT_LABEL: Record<string, string> = {
  excel: 'Excel',
  docx: 'Word',
  pdf: 'PDF',
}

export const SECTION_LABEL: Record<string, string> = {
  all: 'Tam rapor',
  ads: 'ADS içerikleri',
  seo_content: 'SEO+GEO içerikleri',
  social: 'Social içerikleri',
}

// "Tam rapor · Excel" / "ADS içerikleri · Word" — sections+format'tan üretilir
export function jobTypeLabel(job: { format?: string | null; sections?: string[] }): string {
  const sections = job.sections || []
  const section = sections.includes('all') ? 'all' : sections.find((s) => SECTION_LABEL[s])
  const sectionText = section ? SECTION_LABEL[section] : 'Rapor'
  const formatText = job.format ? FORMAT_LABEL[job.format] || job.format : ''
  return formatText ? `${sectionText} · ${formatText}` : sectionText
}
