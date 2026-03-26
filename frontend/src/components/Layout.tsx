import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Key,
  BarChart3,
  Layers,
  Download,
  Sparkles,
  ListTodo,
  Zap,
  Globe2
} from 'lucide-react'
import './Layout.css'

interface LayoutProps {
  children: ReactNode
}

const navItems = [
  { path: '/', icon: LayoutDashboard, label: 'Ana Panel' },
  { path: '/keywords', icon: Key, label: 'Anahtar Kelimeler' },
  { path: '/scoring', icon: BarChart3, label: 'Skorlama' },
  { path: '/brand-profile', icon: Globe2, label: 'Marka Profili' },
  { path: '/channels', icon: Layers, label: 'Kanallar' },
  { path: '/generation', icon: Sparkles, label: 'İçerik Üretimi' },
  { path: '/tasks', icon: ListTodo, label: 'Görevler' },
  { path: '/export', icon: Download, label: 'Dışa Aktarım' },
]

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <Zap className="logo-icon" />
          <div className="logo-text">
            <span className="logo-title">DIGITUS</span>
            <span className="logo-subtitle">ENGINE V2</span>
          </div>
        </div>

        <nav className="sidebar-nav">
          {navItems.map(({ path, icon: Icon, label }) => (
            <Link
              key={path}
              to={path}
              className={`nav-item ${location.pathname === path ? 'active' : ''}`}
            >
              <Icon size={20} />
              <span>{label}</span>
            </Link>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="api-status">
            <div className="status-dot"></div>
            <span>API Bağlı</span>
          </div>
        </div>
      </aside>

      <main className="main-content">
        {children}
      </main>
    </div>
  )
}
