import { useState, useEffect } from 'react'
import { Zap, RefreshCw } from 'lucide-react'
import { scoringApi, channelsApi } from '../services/api'
import './Channels.css'

interface PoolKeyword {
  keyword_id: number
  keyword: string
  rank: number
  is_strategic: boolean
  volume?: number
  score?: number
}

export default function Channels() {
  const [runs, setRuns] = useState<any[]>([])
  const [selectedRun, setSelectedRun] = useState<number | null>(null)
  const [pools, setPools] = useState<{ [key: string]: PoolKeyword[] }>({})
  const [loading, setLoading] = useState(false)
  const [assigning, setAssigning] = useState(false)
  
  useEffect(() => {
    fetchRuns()
  }, [])
  
  const fetchRuns = async () => {
    try {
      const res = await scoringApi.listRuns()
      setRuns(res.data.filter((r: any) => r.status === 'completed') || [])
    } catch (error) {
      console.error('Error fetching runs:', error)
    }
  }
  
  const runAssignment = async (runId: number) => {
    setAssigning(true)
    try {
      await channelsApi.assign(runId)
      fetchPools(runId)
    } catch (error) {
      console.error('Error running assignment:', error)
    }
    setAssigning(false)
  }
  
  const fetchPools = async (runId: number) => {
    setLoading(true)
    setSelectedRun(runId)
    try {
      const poolsRes = await channelsApi.getPools(runId)
      setPools(poolsRes.data.channels || {})
    } catch (error) {
      console.error('Error fetching pools:', error)
    }
    setLoading(false)
  }
  
  const channels = ['ADS', 'SEO', 'SOCIAL']
  const channelColors = {
    ADS: '#ef4444',
    SEO: '#10b981',
    SOCIAL: '#3b82f6'
  }
  
  return (
    <div className="channels-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Channel Assignment</h1>
          <p>AI destekli kanal ataması</p>
        </div>
      </header>
      
      {/* Run Selection */}
      <div className="run-selection glass-card">
        <h3>Skorlama Çalışması Seçin</h3>
        <div className="run-buttons">
          {runs.length === 0 ? (
            <p className="empty-text">Tamamlanmış skorlama çalışması yok</p>
          ) : (
            runs.map(run => (
              <button 
                key={run.id}
                className={`btn ${selectedRun === run.id ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => fetchPools(run.id)}
              >
                {run.run_name || `Run #${run.id}`}
              </button>
            ))
          )}
        </div>
        
        {selectedRun && (
          <button 
            className="btn btn-success" 
            onClick={() => runAssignment(selectedRun)}
            disabled={assigning}
            style={{ marginTop: 'var(--space-md)' }}
          >
            <Zap size={18} />
            {assigning ? 'Atama Yapılıyor...' : 'Kanal Ataması Yap'}
          </button>
        )}
      </div>
      

      
      {/* Channel Pools */}
      {selectedRun && !loading && (
        <div className="pools-grid">
          {channels.map(channel => (
            <div key={channel} className="pool-card glass-card">
              <div className="pool-header" style={{ borderColor: channelColors[channel as keyof typeof channelColors] }}>
                <h3>{channel}</h3>
                <span className="pool-count">{(pools[channel] || []).length} keyword</span>
              </div>
              <div className="pool-list">
                {(pools[channel] || []).length === 0 ? (
                  <p className="empty-text">Havuz boş</p>
                ) : (
                  (pools[channel] || []).map((kw, idx) => (
                    <div key={kw.keyword_id} className="pool-item">
                      <span className="pool-rank">#{idx + 1}</span>
                      <span className="pool-keyword">{kw.keyword}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      
      {loading && (
        <div className="loading-state">
          <RefreshCw size={32} className="animate-spin" />
          <p>Yükleniyor...</p>
        </div>
      )}
    </div>
  )
}
