import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { 
  LayoutDashboard, 
  Key, 
  BarChart3, 
  Layers, 
  Download,
  Settings,
  Zap
} from 'lucide-react'
import './Layout.css'

interface LayoutProps {
  children: ReactNode
}

const navItems = [
  { path: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { path: '/keywords', icon: Key, label: 'Keywords' },
  { path: '/scoring', icon: BarChart3, label: 'Scoring' },
  { path: '/channels', icon: Layers, label: 'Channels' },
  { path: '/export', icon: Download, label: 'Export' },
]

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()

  return (
    <div className="layout">
      {/* Sidebar */}
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
            <span>API Connected</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {children}
      </main>
    </div>
  )
}
