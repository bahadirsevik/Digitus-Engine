import { Navigate, useLocation } from 'react-router-dom'

function mergeSearch(baseSearch: string, currentSearch: string) {
  const params = new URLSearchParams(baseSearch)
  const current = new URLSearchParams(currentSearch)
  current.forEach((value, key) => {
    if (!params.has(key)) params.set(key, value)
  })
  const query = params.toString()
  return query ? `?${query}` : ''
}

export default function RedirectWithParams({ to }: { to: string }) {
  const location = useLocation()
  const [pathname, search = ''] = to.split('?')
  return <Navigate to={`${pathname}${mergeSearch(search, location.search)}`} replace />
}
