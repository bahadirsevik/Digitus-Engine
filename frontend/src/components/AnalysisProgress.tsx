/**
 * Analiz ilerleme banner'ı + sağ-üst toast — Claude Design "Anahtar Kelimeler"
 * tasarımından. Gerçek V3 kanal atama task'ının ilerlemesine bağlanır.
 *
 * Adımlar run'ın açık kanallarından kurulur (orkestratör ilerleme noktaları,
 * app/core/engine/orchestrator.py; kapalı kanal kendi noktasını atlar):
 *   Analiz başladı (0) → Kelime aileleri (15, yalnız ADS veya SEO açıksa) →
 *   ADS (40) → SEO (65) → SOCIAL (85) → Havuz teslimi (92) → 100 bitti.
 * Aktif adım = ulaşılmış en yüksek bilinen nokta; yüzde = task progress'i;
 * alt başlık = motorun gerçek mesajı (result_data.current_message).
 */
import { Check, RefreshCw, X, ArrowRight, DollarSign, Search, Share2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

export type ChainStage = 'assigning' | 'done' | 'failed'

export interface AnalysisChannels {
  ads?: boolean
  seo?: boolean
  social?: boolean
}

interface StepDef {
  key: string
  label: string
  at: number
}

const CHANNEL_LINKS = [
  { path: '/ads', label: 'ADS', icon: DollarSign },
  { path: '/seo-geo', label: 'SEO+GEO', icon: Search },
  { path: '/social', label: 'Social', icon: Share2 },
]

function buildSteps(channels?: AnalysisChannels): StepDef[] {
  const ads = channels?.ads !== false
  const seo = channels?.seo !== false
  const social = channels?.social !== false
  const steps: StepDef[] = [{ key: 'start', label: 'Analiz başladı', at: 0 }]
  if (ads || seo) steps.push({ key: 'family', label: 'Kelime aileleri', at: 15 })
  if (ads) steps.push({ key: 'ads', label: 'ADS', at: 40 })
  if (seo) steps.push({ key: 'seo', label: 'SEO', at: 65 })
  if (social) steps.push({ key: 'social', label: 'SOCIAL', at: 85 })
  steps.push({ key: 'pool', label: 'Havuz teslimi', at: 92 })
  return steps
}

function stageInfo(stage: ChainStage, taskProgress: number, steps: StepDef[]) {
  if (stage === 'assigning') {
    const pct = Math.max(0, Math.min(100, Math.round(taskProgress)))
    let active = 0
    steps.forEach((step, i) => {
      if (pct >= step.at) active = i
    })
    return { active, pct }
  }
  return { active: steps.length, pct: 100 }
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
  channels,
  message,
  errorMessage,
  onDismiss,
}: {
  stage: ChainStage
  taskProgress: number
  channels?: AnalysisChannels
  message?: string | null
  errorMessage?: string | null
  onDismiss: () => void
}) {
  const done = stage === 'done'
  const failed = stage === 'failed'
  const steps = buildSteps(channels)
  const { active, pct } = stageInfo(stage, taskProgress, steps)

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
                  {message && (
                    <>
                      <b className="strong" data-testid="analysis-message">
                        {message}
                      </b>
                      {' — '}
                    </>
                  )}
                  Kelimeler yapay zekâ adımlarından geçiyor.{' '}
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
          {steps.map(({ key, label }, i) => {
            const state = done || i < active ? 'done' : i === active && !failed ? 'active' : 'todo'
            return (
              <div key={key} className={`kwx-step is-${state}`}>
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
  channels,
  message,
  onDismiss,
  onOpen,
}: {
  stage: ChainStage
  taskProgress: number
  channels?: AnalysisChannels
  message?: string | null
  onDismiss: () => void
  onOpen: () => void
}) {
  const done = stage === 'done'
  const steps = buildSteps(channels)
  const { active, pct } = stageInfo(stage, taskProgress, steps)
  const activeLabel = done
    ? 'Tüm adımlar tamamlandı'
    : message || steps[Math.min(active, steps.length - 1)].label

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
            {done ? steps.length : active + 1}/{steps.length} adım
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
