/**
 * Analiz ilerleme banner'ı + sağ-üst toast — Claude Design "Anahtar Kelimeler"
 * tasarımından. Tasarımdaki simüle LLM adımları yerine GERÇEK zincire bağlanır:
 * run status (relevance) + kanal atama TaskResult progress'i.
 *
 * Adımlar (gerçek pipeline):
 *   0 Skorlama            — banner göründüğünde zaten bitmiştir (senkron istek)
 *   1 İlgi skoru          — run.status === relevance_computing
 *   2 Aday sıralaması     — server-controlled screening pending/running
 *   3 Kanal atamaları(AI) — channel task pending/running (bar = gerçek %)
 *   4 Sonuçlar            — task completed / run channel_assigned
 */
import { Check, RefreshCw, X, ArrowRight, DollarSign, Search, Share2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

export type ChainStage = 'relevance' | 'screening' | 'assigning' | 'done' | 'failed'

// Tasarımdaki ayrıntılı adım listesi, GERÇEK pipeline'a eşlendi.
// 2+ indeksli adımlar kanal atama task'ının milestone eşiklerinden türetilir
// (engine: 5/15 havuz → 15-50 intent → 50-60 marka filtresi → 60-80 ön
// filtreler → 80-95 havuz/expansion → 95+ bitiş).
const STEPS = [
  'Skorlama hesaplanıyor (ADS · SEO · SOCIAL)',
  'İlgi skoru hesaplanıyor',
  'Aday kelimeler önceliklendiriliyor',
  'Aday havuzları oluşturuluyor',
  'Yapay zekâ · arama niyeti analizi',
  'Yapay zekâ · marka filtresi ve ön eleme',
  'Kanal havuzları dağıtılıyor',
  'Sonuçlar hazırlanıyor',
]

const CHANNEL_LINKS = [
  { path: '/ads', label: 'ADS', icon: DollarSign },
  { path: '/seo-geo', label: 'SEO+GEO', icon: Search },
  { path: '/social', label: 'Social', icon: Share2 },
]

// Task progress'i (0-100) tasarımdaki alt-adım indeksine çevir
function assignSubStep(taskProgress: number): number {
  if (taskProgress < 15) return 0 // aday havuzları
  if (taskProgress < 50) return 1 // niyet analizi
  if (taskProgress < 80) return 2 // marka filtresi + ön eleme
  if (taskProgress < 95) return 3 // havuz dağıtımı
  return 4 // sonuçlar
}

function stageInfo(stage: ChainStage, taskProgress: number) {
  // aktif adım + toplam yüzde (skorlama bitmiş sayılır)
  if (stage === 'relevance') return { active: 1, pct: 18 }
  if (stage === 'screening') return { active: 2, pct: 28 }
  if (stage === 'assigning') {
    return {
      active: 3 + assignSubStep(taskProgress),
      pct: 35 + Math.round(taskProgress * 0.63),
    }
  }
  return { active: STEPS.length, pct: 100 }
}

export function ChannelNav({ compact }: { compact?: boolean }) {
  const navigate = useNavigate()
  return (
    <div className="kwx-channel-nav">
      {CHANNEL_LINKS.map(({ path, label, icon: Icon }) => (
        <button
          key={path}
          type="button"
          className={`kwx-channel-btn${compact ? ' is-compact' : ''}`}
          onClick={() => navigate(path)}
        >
          <Icon size={14} strokeWidth={2} /> {label}
          <ArrowRight size={13} strokeWidth={2.2} />
        </button>
      ))}
    </div>
  )
}

export function AnalysisBanner({
  stage,
  taskProgress,
  errorMessage,
  onDismiss,
}: {
  stage: ChainStage
  taskProgress: number
  errorMessage?: string | null
  onDismiss: () => void
}) {
  const done = stage === 'done'
  const failed = stage === 'failed'
  const { active, pct } = stageInfo(stage, taskProgress)

  return (
    <div className={`kwx-banner${done ? ' is-done' : ''}${failed ? ' is-failed' : ''}`}>
      <div className="kwx-banner-inner">
        <div className="kwx-banner-top">
          <span className="kwx-banner-icon">
            {done ? (
              <Check size={20} strokeWidth={2.6} />
            ) : failed ? (
              <X size={20} strokeWidth={2.6} />
            ) : (
              <span className="kwx-spin">
                <RefreshCw size={19} strokeWidth={2.4} />
              </span>
            )}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="kwx-banner-title">
              {done
                ? 'Analiz tamamlandı — skorlar ve kanal atamaları hazır'
                : failed
                  ? 'Analiz sırasında hata oluştu'
                  : 'Kelimeler yapay zekâ ile işleniyor'}
            </div>
            <div className="kwx-banner-sub">
              {done ? (
                'Skorlama tablosu aşağıda. İçerik üretimini ADS, SEO+GEO ve Social sayfalarından yapabilirsiniz.'
              ) : failed ? (
                errorMessage ||
                'Görevler sayfasından ayrıntıya bakabilir, analizi yeniden başlatabilirsiniz.'
              ) : (
                <>
                  Skorlama bittikten sonra kelimeler yapay zekâ adımlarından geçiyor.{' '}
                  <b className="warn">Bu işlem 5 dakikadan uzun sürebilir.</b> Bu ekranı
                  kapatabilirsiniz — işlem arka planda sürer, durumu{' '}
                  <b className="strong">Görevler</b>'den de izleyebilirsiniz.
                </>
              )}
            </div>
          </div>
          {(done || failed) && (
            <button type="button" className="kwx-banner-close" onClick={onDismiss} title="Kapat">
              <X size={15} />
            </button>
          )}
        </div>

        <div className="kwx-progressbar">
          <div className="kwx-progressbar-fill" style={{ width: `${failed ? 100 : pct}%` }} />
        </div>

        <div className="kwx-steps">
          {STEPS.map((label, i) => {
            const state = done || i < active ? 'done' : i === active && !failed ? 'active' : 'todo'
            return (
              <div key={i} className={`kwx-step is-${state}`}>
                <span className="kwx-step-dot">
                  {state === 'done' ? (
                    <Check size={10} strokeWidth={3} />
                  ) : state === 'active' ? (
                    <span className="kwx-spin">
                      <RefreshCw size={11} strokeWidth={2.6} />
                    </span>
                  ) : null}
                </span>
                {label}
              </div>
            )
          })}
        </div>

        {done && (
          <div className="kwx-banner-links">
            <div className="hint">Kanal çıktılarına git:</div>
            <ChannelNav />
          </div>
        )}
      </div>
    </div>
  )
}

export function AnalysisToast({
  stage,
  taskProgress,
  onDismiss,
  onOpen,
}: {
  stage: ChainStage
  taskProgress: number
  onDismiss: () => void
  onOpen: () => void
}) {
  const done = stage === 'done'
  const { active, pct } = stageInfo(stage, taskProgress)
  const activeLabel = done ? 'Tüm adımlar tamamlandı' : STEPS[Math.min(active, STEPS.length - 1)]

  return (
    <div className={`kwx-toast${done ? ' is-done' : ''}`}>
      <div className="kwx-toast-head">
        <span className="kwx-toast-icon">
          {done ? (
            <Check size={18} strokeWidth={2.6} />
          ) : (
            <span className="kwx-spin">
              <RefreshCw size={16} strokeWidth={2.4} />
            </span>
          )}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="kwx-toast-title">
            {done ? 'Analiz tamamlandı' : 'Yapay zekâ analizi çalışıyor'}
          </div>
          <div className="kwx-toast-sub">{activeLabel}</div>
        </div>
        <button type="button" className="kwx-toast-close" onClick={onDismiss} title="Kapat">
          <X size={13} />
        </button>
      </div>
      <div className="kwx-toast-body">
        <div className="kwx-toast-meta">
          <span>
            {done ? STEPS.length : active}/{STEPS.length} adım
          </span>
          <span className="kwx-mono">{pct}%</span>
        </div>
        <div className="kwx-toast-bar">
          <div className="kwx-progressbar-fill" style={{ width: `${pct}%` }} />
        </div>
        {!done && <div className="kwx-toast-warn">Bu işlem 5 dakikadan uzun sürebilir.</div>}
        <button type="button" className="kwx-toast-open" onClick={onOpen}>
          {done ? 'Skorları gör' : 'Ayrıntıları gör'}
        </button>
        {done && (
          <div className="kwx-toast-links">
            <div className="hint">Kanal çıktılarına git:</div>
            <ChannelNav compact />
          </div>
        )}
      </div>
    </div>
  )
}
