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

function App() {
  return (
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
  )
}

export default App
