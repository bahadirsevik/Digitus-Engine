import { Navigate, useLocation } from 'react-router-dom'

export default function GenerationRedirect() {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const tab = params.get('tab')
  const runId = params.get('run_id')
  const target = tab === 'ads' ? '/ads' : tab === 'social' ? '/social' : '/seo-geo'
  const next = new URLSearchParams()
  if (runId) next.set('run_id', runId)
  const query = next.toString()
  return <Navigate to={`${target}${query ? `?${query}` : ''}`} replace />
}
