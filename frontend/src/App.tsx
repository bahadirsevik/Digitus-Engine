import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Keywords from './pages/Keywords'
import Scoring from './pages/Scoring'
import Channels from './pages/Channels'
import Generation from './pages/Generation'
import Tasks from './pages/Tasks'
import Export from './pages/Export'
import BrandProfile from './pages/BrandProfile'

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/keywords" element={<Keywords />} />
          <Route path="/scoring" element={<Scoring />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/generation" element={<Generation />} />
          <Route path="/brand-profile" element={<BrandProfile />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/export" element={<Export />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}

export default App
