/**
 * SocialStepper Component
 * 3-aşamalı sosyal medya içerik üretimi
 */
import { useState, useEffect } from 'react'
import { ChevronRight, ChevronLeft, Sparkles, Check } from 'lucide-react'
import { useSocialStore } from '../stores/socialStore'
import ErrorBanner from './ErrorBanner'
import './SocialStepper.css'

interface ScoringRun {
  id: number
  run_name?: string
  status: string
  created_at: string
}

export default function SocialStepper() {
  const store = useSocialStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<ScoringRun[]>([])

  useEffect(() => {
    const fetchRuns = async () => {
      try {
        const res = await fetch('/api/v1/scoring/runs')
        if (res.ok) {
          const data = await res.json()
          setRuns(data)
        }
      } catch (e) {
        console.error('Failed to fetch runs:', e)
      }
    }
    fetchRuns()
  }, [])

  // Auto-fill brand info from confirmed profile when scoring run changes
  useEffect(() => {
    if (!store.scoringRunId) return
    const fetchProfile = async () => {
      try {
        const res = await fetch(`/api/v1/brand-profile/runs/${store.scoringRunId}/profile`)
        if (res.ok) {
          const profile = await res.json()
          if (profile.status === 'confirmed' && profile.profile_data) {
            const pd = profile.profile_data
            const updates: Record<string, any> = {}
            if (!store.brandName && pd.company_name) updates.brandName = pd.company_name
            if (!store.brandContext) {
              const parts: string[] = []
              if (pd.company_name) parts.push(`Marka: ${pd.company_name}`)
              if (pd.products?.length) parts.push(`Ürünler: ${pd.products.join(', ')}`)
              if (pd.use_cases?.length) parts.push(`Kullanım: ${pd.use_cases.join(', ')}`)
              if (parts.length) updates.brandContext = parts.join('\n')
            }
            if (Object.keys(updates).length) store.setFormData(updates)
          }
        }
      } catch { /* profile not found — ok */ }
    }
    fetchProfile()
  }, [store.scoringRunId]) // eslint-disable-line react-hooks/exhaustive-deps

  const steps = [
    { num: 1, title: 'Marka Bilgisi', desc: 'Marka ve bağlam girin' },
    { num: 2, title: 'Kategori Seçimi', desc: 'Üretilen kategorileri seçin' },
    { num: 3, title: 'Fikir Seçimi', desc: 'İçerik üretilecek fikirleri seçin' },
  ]

  const generateCategories = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/v1/generation/social/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scoring_run_id: store.scoringRunId,
          brand_name: store.brandName,
          brand_context: store.brandContext,
          max_categories: store.maxCategories,
        })
      })
      
      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      store.setCategories(data.categories || [])
      store.setStep(2)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kategori üretimi başarısız')
    } finally {
      setLoading(false)
    }
  }

  const generateIdeas = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/generation/social/ideas?brand_name=${encodeURIComponent(store.brandName)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category_ids: store.selectedCategoryIds,
          ideas_per_category: store.ideasPerCategory,
        })
      })
      
      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      // Response is List[SocialIdeasResponse], flatten all ideas from all categories
      const allIdeas = Array.isArray(data) 
        ? data.flatMap((cat: any) => cat.ideas || []) 
        : data.ideas || []
      store.setIdeas(allIdeas)
      store.setStep(3)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Fikir üretimi başarısız')
    } finally {
      setLoading(false)
    }
  }

  const generateContents = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/v1/generation/social/contents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idea_ids: store.selectedIdeaIds,
          brand_name: store.brandName,
        })
      })
      
      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      // Response is SocialContentsResponse { idea_ids, total_contents, contents }
      store.setContents(data.contents || [])
      store.setStep(4) // Move to results view
    } catch (err) {
      setError(err instanceof Error ? err.message : 'İçerik üretimi başarısız')
    } finally {
      setLoading(false)
    }
  }

  const goBack = () => {
    if (store.step > 1) {
      store.setStep(store.step - 1)
    }
  }

  return (
    <div className="social-stepper">
      {/* Stepper Header */}
      <div className="stepper-header">
        {steps.map((s, i) => (
          <div key={s.num} className="step-container">
            <div className={`step-circle ${store.step >= s.num ? 'active' : ''} ${store.step > s.num ? 'completed' : ''}`}>
              {store.step > s.num ? <Check size={16} /> : s.num}
            </div>
            <div className="step-text">
              <span className="step-title">{s.title}</span>
              <span className="step-desc">{s.desc}</span>
            </div>
            {i < steps.length - 1 && <div className="step-line" />}
          </div>
        ))}
      </div>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {/* Step Content */}
      <div className="step-content glass-card">
        {store.step === 1 && (
          <div className="step-form">
            <div className="form-group">
              <label>Skorlama Çalışması *</label>
              <select
                value={store.scoringRunId || ''}
                onChange={e => store.setFormData({ scoringRunId: Number(e.target.value) })}
              >
                <option value="">Seçin...</option>
                {runs.map(run => (
                  <option key={run.id} value={run.id}>
                    {run.run_name || `Çalışma #${run.id}`}{run.created_at ? ` — ${new Date(run.created_at).toLocaleDateString('tr-TR')}` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>Marka Adı</label>
              <input
                type="text"
                value={store.brandName}
                onChange={e => store.setFormData({ brandName: e.target.value })}
                placeholder="Profilden otomatik dolar veya manuel girin"
              />
            </div>
            <div className="form-group">
              <label>Marka Bağlamı</label>
              <textarea
                value={store.brandContext}
                onChange={e => store.setFormData({ brandContext: e.target.value })}
                placeholder="Markanın sektörü, tonu, hedef kitlesi..."
                rows={3}
              />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>Maks Kategori</label>
                <input
                  type="number"
                  value={store.maxCategories}
                  onChange={e => store.setFormData({ maxCategories: Number(e.target.value) })}
                />
              </div>
              <div className="form-group">
                <label>Kategori Başına Fikir</label>
                <input
                  type="number"
                  value={store.ideasPerCategory}
                  onChange={e => store.setFormData({ ideasPerCategory: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
        )}

        {store.step === 2 && (
          <div className="category-list">
            <p className="list-hint">Fikir üretmek istediğiniz kategorileri seçin:</p>
            {store.categories.length === 0 ? (
              <p className="empty-state">Henüz kategori üretilmedi</p>
            ) : (
              store.categories.map(cat => (
                <label
                  key={cat.id}
                  className={`category-item ${store.selectedCategoryIds.includes(cat.id) ? 'selected' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={store.selectedCategoryIds.includes(cat.id)}
                    onChange={() => store.toggleCategory(cat.id)}
                  />
                  <div className="category-info">
                    <span className="category-name">{cat.name}</span>
                    <span className="category-desc">{cat.description}</span>
                  </div>
                  <span className="category-count">{cat.keyword_count} kelime</span>
                </label>
              ))
            )}
          </div>
        )}

        {store.step === 3 && (
          <div className="idea-list">
            <p className="list-hint">İçerik üretmek istediğiniz fikirleri seçin:</p>
            {store.ideas.length === 0 ? (
              <p className="empty-state">Henüz fikir üretilmedi</p>
            ) : (
              store.ideas.map(idea => (
                <label
                  key={idea.id}
                  className={`idea-item ${store.selectedIdeaIds.includes(idea.id) ? 'selected' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={store.selectedIdeaIds.includes(idea.id)}
                    onChange={() => store.toggleIdea(idea.id)}
                  />
                  <div className="idea-info">
                    <span className="idea-title">{idea.idea_title}</span>
                    <span className="idea-hook">{idea.idea_description}</span>
                  </div>
                  <div className="idea-meta">
                    <span className="viral-score">📊 {(idea.trend_alignment * 100).toFixed(0)}%</span>
                    <span className="platform">{idea.target_platform} • {idea.content_format}</span>
                  </div>
                </label>
              ))
            )}
          </div>
        )}

        {/* Step 4: Content Results */}
        {store.step === 4 && (
          <div className="content-results">
            <h3 style={{ marginBottom: '16px' }}>
              <Check size={20} style={{ color: '#10b981', marginRight: '8px' }} />
              {store.contents.length} İçerik Üretildi!
            </h3>
            {store.contents.length === 0 ? (
              <p className="empty-state">İçerik üretilemedi</p>
            ) : (
              store.contents.map((content, idx) => (
                <div key={content.id || idx} className="content-card glass-card" style={{ marginBottom: '16px', padding: '16px' }}>
                  <div className="content-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <span style={{ fontWeight: 600, fontSize: '14px', color: 'var(--accent-primary)' }}>İçerik #{idx + 1}</span>
                  </div>
                  
                  {/* Hooks */}
                  {content.hooks && content.hooks.length > 0 && (
                    <div style={{ marginBottom: '12px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>🎣 Hook'lar</span>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '4px' }}>
                        {content.hooks.map((hook, hi) => (
                          <div key={hi} style={{ fontSize: '13px', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: '6px' }}>
                            <span style={{ color: 'var(--text-secondary)', fontSize: '11px' }}>[{hook.style}]</span>{' '}
                            {hook.text}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Caption */}
                  <div style={{ marginBottom: '12px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>📝 Caption</span>
                    <p style={{ fontSize: '13px', marginTop: '4px', lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>{content.caption}</p>
                  </div>

                  {/* Scenario */}
                  {content.scenario && (
                    <div style={{ marginBottom: '12px' }}>
                      <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>🎬 Senaryo</span>
                      <p style={{ fontSize: '13px', marginTop: '4px', lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>{content.scenario}</p>
                    </div>
                  )}

                  {/* CTA */}
                  <div style={{ marginBottom: '8px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>🎯 CTA</span>
                    <p style={{ fontSize: '13px', marginTop: '4px' }}>{content.cta_text}</p>
                  </div>

                  {/* Hashtags */}
                  {content.hashtags && content.hashtags.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px' }}>
                      {content.hashtags.map((tag, ti) => (
                        <span key={ti} style={{ fontSize: '12px', padding: '2px 8px', background: 'rgba(99, 102, 241, 0.15)', color: '#818cf8', borderRadius: '4px' }}>
                          {tag.startsWith('#') ? tag : `#${tag}`}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}

            <div className="completion-banner" style={{ marginTop: '16px' }}>
              <Check size={20} />
              İçerik üretimi tamamlandı! <a href="/export">Dışa Aktarım</a> sayfasından indirebilirsiniz.
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="stepper-actions">
        {store.step > 1 && store.step < 4 && (
          <button className="btn btn-secondary" onClick={goBack} disabled={loading}>
            <ChevronLeft size={18} />
            Geri
          </button>
        )}
        
        <div className="action-spacer" />
        
        {store.step === 1 && (
          <button
            className="btn btn-primary"
            onClick={generateCategories}
            disabled={loading || !store.scoringRunId}
          >
            {loading ? 'Üretiliyor...' : 'Kategorileri Üret'}
            <Sparkles size={18} />
          </button>
        )}
        
        {store.step === 2 && (
          <button
            className="btn btn-primary"
            onClick={generateIdeas}
            disabled={loading || store.selectedCategoryIds.length === 0}
          >
            {loading ? 'Üretiliyor...' : 'Fikirleri Üret'}
            <ChevronRight size={18} />
          </button>
        )}
        
        {store.step === 3 && (
          <button
            className="btn btn-primary"
            onClick={generateContents}
            disabled={loading || store.selectedIdeaIds.length === 0}
          >
            {loading ? 'Üretiliyor...' : 'İçerikleri Üret'}
            <Sparkles size={18} />
          </button>
        )}

        {store.step === 4 && (
          <button className="btn btn-secondary" onClick={() => store.reset()}>
            Yeni Üretim Başlat
          </button>
        )}
      </div>
    </div>
  )
}
