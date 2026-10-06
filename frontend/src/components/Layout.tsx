import { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  BadgeDollarSign,
  Globe2,
  Key,
  LayoutDashboard,
  ListTodo,
  Search,
  Share2,
} from 'lucide-react'
import logoFull from '../assets/optimice-logo.png'
import logoIcon from '../assets/optimice-icon.png'
import './Layout.css'

interface LayoutProps {
  children: ReactNode
}

const navItems = [
  { path: '/', icon: LayoutDashboard, label: 'Ana Panel' },
  { path: '/brand-profile', icon: Globe2, label: 'Marka Çalışmaları' },
  { path: '/keywords', icon: Key, label: 'Anahtar Kelimeler' },
  { path: '/ads', icon: BadgeDollarSign, label: 'ADS' },
  { path: '/seo-geo', icon: Search, label: 'SEO+GEO' },
  { path: '/social', icon: Share2, label: 'Social' },
  { path: '/tasks', icon: ListTodo, label: 'Görevler' },
]

export default function Layout({ children }: LayoutProps) {
  const location = useLocation()

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <img className="logo-full" src={logoFull} alt="Optimice" />
          <img className="logo-icon" src={logoIcon} alt="Optimice" />
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
        </div>
      </aside>

      <main className="main-content">{children}</main>
    </div>
  )
}
