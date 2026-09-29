import { Link } from 'react-router-dom'
import { ArrowRight, BadgeDollarSign, Briefcase, KeyRound, Sparkles } from 'lucide-react'
import { DashboardNextAction } from '../services/api'

interface Props {
  nextAction: DashboardNextAction | null
}

export default function DashboardEmpty({ nextAction }: Props) {
  const action = nextAction ?? {
    key: 'create_workspace',
    label: 'Marka Çalışması Oluştur',
    path: '/brand-profile',
    severity: 'primary' as const,
    reason: 'Önce bir marka çalışması oluşturmalısın.',
  }

  return (
    <div className="dashboard-page dashboard-empty animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Ana Panel</h1>
          <p>Aktif marka çalışması seçilmedi. Başlamak için bir çalışma oluştur veya seç.</p>
        </div>
      </header>

      <section className="empty-start">
        <div className="empty-start-copy">
          <Sparkles size={34} />
          <span>Başlangıç</span>
          <h2>{action.label}</h2>
          <p>{action.reason}</p>
          <Link to={action.path} className="btn btn-primary">
            <Briefcase size={16} />
            Devam et
          </Link>
        </div>

        <div className="empty-start-grid">
          <Link to="/brand-profile" className="empty-start-card">
            <Briefcase size={18} />
            <strong>Marka çalışmaları</strong>
            <span>Çalışma seç, profil oluştur veya onay durumunu kontrol et.</span>
            <ArrowRight size={14} />
          </Link>
          <Link to="/keywords" className="empty-start-card">
            <KeyRound size={18} />
            <strong>Anahtar kelimeler</strong>
            <span>Aktif çalışma seçildikten sonra keyword havuzunu yönet.</span>
            <ArrowRight size={14} />
          </Link>
          <Link to="/google-ads" className="empty-start-card">
            <BadgeDollarSign size={18} />
            <strong>Google Ads Explorer</strong>
            <span>Google Ads bağlantısını ve kampanya verilerini read-only incele.</span>
            <ArrowRight size={14} />
          </Link>
        </div>
      </section>
    </div>
  )
}
