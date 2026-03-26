import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Play, Eye, TrendingUp, Award, Trash2, Download, ArrowRight } from 'lucide-react'
import { scoringApi, ScoringRunCreate } from '../services/api'
import './Scoring.css'

interface ScoringRun {
  id: number
  run_name?: string
  status: string
  ads_capacity: number
  seo_capacity: number
  social_capacity: number
  default_relevance_coefficient?: number
  created_at: string
}

interface KeywordScore {
  keyword_id: number
  keyword: string
  ads_score: number
  seo_score: number
  social_score: number
  ads_rank?: number
  seo_rank?: number
  social_rank?: number
}

export default function Scoring() {
  const navigate = useNavigate()

  const [runs, setRuns] = useState<ScoringRun[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const [scores, setScores] = useState<KeywordScore[]>([])
  const [loading, setLoading] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [newRun, setNewRun] = useState<ScoringRunCreate>({
    run_name: '',
    ads_capacity: 20,
    seo_capacity: 30,
    social_capacity: 25,
    default_relevance_coefficient: 1.0
  })

  useEffect(() => {
    fetchRuns()
  }, [])

  const fetchRuns = async () => {
    try {
      const res = await scoringApi.listRuns()
      setRuns(res.data || [])
    } catch (error) {
      console.error('Error fetching runs:', error)
    }
  }

  const createRun = async () => {
    try {
      const res = await scoringApi.createRun(newRun)
      setShowModal(false)
      setNewRun({
        run_name: '',
        ads_capacity: 20,
        seo_capacity: 30,
        social_capacity: 25,
        default_relevance_coefficient: 1.0
      })
      fetchRuns()
      setSelectedRun(res.data.id)
    } catch (error) {
      console.error('Error creating run:', error)
    }
  }

  const executeRun = async (runId: number) => {
    setLoading(true)
    try {
      await scoringApi.executeRun(runId)
      fetchRuns()
      viewScores(runId)
    } catch (error) {
      console.error('Error executing run:', error)
    }
    setLoading(false)
  }

  const viewScores = async (runId: number) => {
    setSelectedRun(runId)
    try {
      const res = await scoringApi.getScores(runId, 50)
      setScores(res.data.scores || [])
    } catch (error) {
      console.error('Error fetching scores:', error)
    }
  }

  return (
    <div className="scoring-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Skorlama</h1>
          <p>Anahtar kelime skorlama işlemleri</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>
          <TrendingUp size={18} />
          Yeni Skorlama
        </button>
      </header>

      <div className="runs-section">
        <h2>Skorlama Çalışmaları</h2>
        <div className="runs-grid">
          {runs.length === 0 ? (
            <p className="empty-text">Henüz skorlama çalışması yok</p>
          ) : (
            runs.map(run => (
              <div
                key={run.id}
                className={`run-card glass-card ${selectedRun === run.id ? 'active' : ''}`}
              >
                <div className="run-header">
                  <span className="run-name">{run.run_name || `Çalışma #${run.id}`}</span>
                  <span className={`status status-${run.status}`}>{run.status}</span>
                </div>
                <div className="run-capacities">
                  <span className="badge badge-ads">ADS: {run.ads_capacity}</span>
                  <span className="badge badge-seo">SEO: {run.seo_capacity}</span>
                  <span className="badge badge-social">SOCIAL: {run.social_capacity}</span>
                  <span className="badge badge-social">
                    Katsayı: {typeof run.default_relevance_coefficient === 'number'
                      ? run.default_relevance_coefficient.toFixed(2)
                      : Number(run.default_relevance_coefficient || 1).toFixed(2)}
                  </span>
                </div>
                <div className="run-actions">
                  {run.status === 'pending' && (
                    <button
                      className="btn btn-success"
                      onClick={() => executeRun(run.id)}
                      disabled={loading}
                    >
                      <Play size={16} />
                      {loading ? 'Çalışıyor...' : 'Çalıştır'}
                    </button>
                  )}

                  {run.status === 'completed' && (
                    <>
                      <button
                        className="btn btn-secondary"
                        onClick={() => viewScores(run.id)}
                      >
                        <Eye size={16} />
                        Görüntüle
                      </button>
                      <button
                        className="btn btn-success"
                        onClick={async () => {
                          try {
                            const res = await scoringApi.exportXlsx(run.id)
                            const url = window.URL.createObjectURL(new Blob([res.data]))
                            const link = document.createElement('a')
                            link.href = url
                            link.setAttribute('download', `scoring_run_${run.id}.xlsx`)
                            document.body.appendChild(link)
                            link.click()
                            link.remove()
                          } catch (err) {
                            console.error('Export error:', err)
                          }
                        }}
                      >
                        <Download size={16} />
                        Excel
                      </button>
                      <button
                        className="btn btn-primary"
                        onClick={() => navigate(`/brand-profile?run_id=${run.id}`)}
                      >
                        Sonraki
                        <ArrowRight size={16} />
                      </button>
                    </>
                  )}

                  <button
                    className="btn btn-danger"
                    onClick={async () => {
                      if (!confirm('Bu skorlama çalışmasını silmek istediğinize emin misiniz?')) return
                      try {
                        await scoringApi.deleteRun(run.id)
                        fetchRuns()
                        if (selectedRun === run.id) {
                          setSelectedRun(null)
                          setScores([])
                        }
                      } catch (err) {
                        console.error('Delete error:', err)
                      }
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {selectedRun && scores.length > 0 && (
        <div className="scores-section">
          <h2>Skorlar - Çalışma #{selectedRun}</h2>
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Anahtar Kelime</th>
                  <th>ADS Skor</th>
                  <th>SEO Skor</th>
                  <th>SOCIAL Skor</th>
                  <th>ADS Sira</th>
                  <th>SEO Sira</th>
                  <th>SOCIAL Sira</th>
                </tr>
              </thead>
              <tbody>
                {scores.map(score => (
                  <tr key={score.keyword_id}>
                    <td><strong>{score.keyword}</strong></td>
                    <td>{typeof score.ads_score === 'number' ? score.ads_score.toFixed(4) : (Number(score.ads_score) || 0).toFixed(4)}</td>
                    <td>{typeof score.seo_score === 'number' ? score.seo_score.toFixed(4) : (Number(score.seo_score) || 0).toFixed(4)}</td>
                    <td>{typeof score.social_score === 'number' ? score.social_score.toFixed(4) : (Number(score.social_score) || 0).toFixed(4)}</td>
                    <td>
                      {score.ads_rank && (
                        <span className={score.ads_rank <= 3 ? 'top-rank' : ''}>
                          {score.ads_rank <= 3 && <Award size={14} />}
                          #{score.ads_rank}
                        </span>
                      )}
                    </td>
                    <td>
                      {score.seo_rank && (
                        <span className={score.seo_rank <= 3 ? 'top-rank' : ''}>
                          {score.seo_rank <= 3 && <Award size={14} />}
                          #{score.seo_rank}
                        </span>
                      )}
                    </td>
                    <td>
                      {score.social_rank && (
                        <span className={score.social_rank <= 3 ? 'top-rank' : ''}>
                          {score.social_rank <= 3 && <Award size={14} />}
                          #{score.social_rank}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal glass-card" onClick={e => e.stopPropagation()}>
            <h2>Yeni Skorlama Çalışması</h2>

            <div className="form-group">
              <label>Çalışma Adı</label>
              <input
                className="input"
                value={newRun.run_name}
                onChange={e => setNewRun({ ...newRun, run_name: e.target.value })}
                placeholder="Mart 2026 Skorlama"
              />
            </div>

            <div className="form-group">
              <label>ADS Kapasitesi</label>
              <input
                type="number"
                className="input"
                value={newRun.ads_capacity}
                onChange={e => setNewRun({ ...newRun, ads_capacity: parseInt(e.target.value) })}
              />
            </div>

            <div className="form-group">
              <label>SEO Kapasitesi</label>
              <input
                type="number"
                className="input"
                value={newRun.seo_capacity}
                onChange={e => setNewRun({ ...newRun, seo_capacity: parseInt(e.target.value) })}
              />
            </div>

            <div className="form-group">
              <label>SOCIAL Kapasitesi</label>
              <input
                type="number"
                className="input"
                value={newRun.social_capacity}
                onChange={e => setNewRun({ ...newRun, social_capacity: parseInt(e.target.value) })}
              />
            </div>

            <div className="form-group">
              <label>Varsayılan İlgi Katsayısı (0.1 - 3.0)</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="3"
                className="input"
                value={newRun.default_relevance_coefficient ?? 1.0}
                onChange={e => setNewRun({ ...newRun, default_relevance_coefficient: parseFloat(e.target.value) })}
              />
            </div>

            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setShowModal(false)}>
                İptal
              </button>
              <button className="btn btn-primary" onClick={createRun}>
                Oluştur
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
