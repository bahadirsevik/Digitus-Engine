/**
 * SectionSelect Component
 * Export için bölüm seçimi
 */
import { Check } from 'lucide-react'
import './SectionSelect.css'

interface Section {
  id: string
  label: string
  description?: string
}

interface SectionSelectProps {
  sections: Section[]
  selected: string[]
  onChange: (selected: string[]) => void
}

const defaultSections: Section[] = [
  { id: 'summary', label: 'Ozet', description: 'Genel istatistikler' },
  { id: 'scoring', label: 'Skorlama', description: 'Kelime skorları' },
  { id: 'channels', label: 'Kanallar', description: 'Kanal atamaları' },
  { id: 'seo_content', label: 'SEO+GEO İçerikler', description: 'Blog içerikleri' },
  { id: 'ads', label: 'Google Ads', description: 'Reklam grupları ve başlıklar' },
  { id: 'social', label: 'Sosyal Medya', description: 'İçerik ve fikirler' },
]

export default function SectionSelect({
  sections = defaultSections,
  selected,
  onChange
}: SectionSelectProps) {
  const toggleSection = (id: string) => {
    if (selected.includes(id)) {
      onChange(selected.filter(s => s !== id))
    } else {
      onChange([...selected, id])
    }
  }

  const selectAll = () => {
    onChange(sections.map(s => s.id))
  }

  const selectNone = () => {
    onChange([])
  }

  return (
    <div className="section-select">
      <div className="section-select-header">
        <span className="label">Disa Aktarim Bolumleri</span>
        <div className="quick-actions">
          <button onClick={selectAll}>Tümünü Seç</button>
          <button onClick={selectNone}>Temizle</button>
        </div>
      </div>

      <div className="section-list">
        {sections.map(section => (
          <label
            key={section.id}
            className={`section-item ${selected.includes(section.id) ? 'selected' : ''}`}
          >
            <div className="checkbox">
              {selected.includes(section.id) && <Check size={14} />}
            </div>
            <input
              type="checkbox"
              checked={selected.includes(section.id)}
              onChange={() => toggleSection(section.id)}
            />
            <div className="section-info">
              <span className="section-label">{section.label}</span>
              {section.description && (
                <span className="section-desc">{section.description}</span>
              )}
            </div>
          </label>
        ))}
      </div>
    </div>
  )
}

