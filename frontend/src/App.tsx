import { useEffect, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Keywords from './pages/Keywords'
import GenerationRedirect from './components/GenerationRedirect'
import Tasks from './pages/Tasks'
import Export from './pages/Export'
import BrandProfile from './pages/BrandProfile'
import GoogleAdsExplorer from './pages/GoogleAdsExplorer'
import ChannelAds from './pages/ChannelAds'
import ChannelSeoGeo from './pages/ChannelSeoGeo'
import ChannelSocial from './pages/ChannelSocial'
import RedirectWithParams from './components/RedirectWithParams'
import Login from './pages/Login'
import ChangePassword from './pages/ChangePassword'
import { useAuthStore } from './stores/authStore'

/**
 * Giriş kapısı.
 *
 * Uygulama ağacının TAMAMINI sarar: girişsiz kullanıcı hiçbir sayfayı ve
 * dolayısıyla hiçbir veri isteğini tetikleyemez. Rota bazlı koruma yerine
 * bunun seçilmesi bilinçli — tek bir rotayı korumayı unutma riski kalmıyor.
 *
 * Backend LOGIN_ENABLED=false ise store 'authenticated' döner ve bu kapı
 * şeffaf olur (giriş öncesi davranış).
 */
function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status)
  const user = useAuthStore((s) => s.user)
  const bootstrap = useAuthStore((s) => s.bootstrap)

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  if (status === 'unknown') {
    // Sunucuya sorulurken boş ekran: burada uygulamayı göstermek girişsiz
    // kullanıcıya bir an için arayüzü sızdırırdı.
    return <div className="login-page" aria-busy="true" />
  }

  if (status === 'anonymous') {
    return <Login />
  }

  // Geçici parola: uygulamaya hiç girilmez, parola ekranı gösterilir.
  // Atlatılsa bile backend veri uçlarında 403 döner.
  if (user?.must_change_password) {
    return <ChangePassword />
  }

  return <>{children}</>
}

function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/brand-profile" element={<BrandProfile />} />
            <Route path="/keywords" element={<Keywords />} />
            <Route path="/scoring" element={<RedirectWithParams to="/keywords?view=scores" />} />
            <Route path="/relevance" element={<RedirectWithParams to="/keywords?view=scores" />} />
            <Route path="/channels" element={<RedirectWithParams to="/keywords?view=scores" />} />
            <Route path="/ads" element={<ChannelAds />} />
            <Route path="/seo-geo" element={<ChannelSeoGeo />} />
            <Route path="/social" element={<ChannelSocial />} />
            <Route path="/generation" element={<GenerationRedirect />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/export" element={<Export />} />
            <Route path="/google-ads" element={<GoogleAdsExplorer />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </AuthGate>
  )
}

export default App
