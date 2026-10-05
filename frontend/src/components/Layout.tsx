import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  BadgeDollarSign,
  Globe2,
  Key,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Search,
  Share2,
  Zap,
} from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import './Layout.css'

interface LayoutProps {
  children: ReactNode
}

const navItems = [
  { path: '/', icon: LayoutDashboard, label: 'Ana Panel' },
  { path: '/brand-profile', icon: Globe2, label: 'Marka Calismalari' },
  { path: '/keywords', icon: Key, label: 'Anahtar Kelimeler' },
  { path: '/ads', icon: BadgeDollarSign, label: 'ADS' },
  { path: '/seo-geo', icon: Search, label: 'SEO+GEO' },
  { path: '/social', icon: Share2, label: 'Social' },
  { path: '/tasks', icon: ListTodo, label: 'Gorevler' },
]

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const user = useAuthStore((s) => s.user)
  const loginRequired = useAuthStore((s) => s.loginRequired)
  const logout = useAuthStore((s) => s.logout)

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
            <span>API Bagli</span>
          </div>

          {/* Giris kapali ise (LOGIN_ENABLED=false) kullanici satiri hic
              gosterilmez — o modda oturum kavrami yok. */}
          {loginRequired && user && (
            <div className="sidebar-user">
              <span className="sidebar-user-name" title={user.email}>
                {user.full_name || user.email}
              </span>
              <button
                type="button"
                className="sidebar-logout"
                onClick={() => void logout()}
                title="Cikis yap"
                aria-label="Cikis yap"
              >
                <LogOut size={16} />
              </button>
            </div>
          )}
        </div>
      </aside>

      <main className="main-content">{children}</main>
    </div>
  )
}
