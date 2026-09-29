/**
 * Sihirbaz yükleme ekranı (Claude Design "Marka Profili" tasarımından):
 * dönen halka + sparkle ikonu + adım checklist'i.
 *
 * Backend yalnız statü döndürür; adımlar görsel ritim için istemci tarafında
 * zamanlayıcıyla ilerler ve SON adımda bekler — faz değişince ekran zaten
 * bir sonraki içeriğe geçer.
 */
import { useEffect, useState } from 'react'
import { Sparkles, Check } from 'lucide-react'

const STEP_ADVANCE_MS = 2200

export default function LoadingStep({
  title,
  sub,
  steps,
}: {
  title: string
  sub: string
  steps: string[]
}) {
  const [activeStep, setActiveStep] = useState(0)

  useEffect(() => {
    setActiveStep(0)
    const interval = window.setInterval(() => {
      setActiveStep((current) => Math.min(current + 1, steps.length - 1))
    }, STEP_ADVANCE_MS)
    return () => window.clearInterval(interval)
  }, [steps.length, title])

  return (
    <div className="bpx-loading">
      <div className="bpx-loading-ring">
        <div className="bpx-loading-track" />
        <div className="bpx-loading-spinner" />
        <div className="bpx-loading-icon">
          <Sparkles size={24} />
        </div>
      </div>
      <div className="bpx-loading-title">{title}</div>
      <div className="bpx-loading-sub">{sub}</div>
      <div className="bpx-loading-steps">
        {steps.map((step, i) => {
          const state = i < activeStep ? 'done' : i === activeStep ? 'active' : 'idle'
          return (
            <div key={i} className={`bpx-loading-step is-${state}`}>
              <span className="bpx-loading-step-dot">
                {state === 'done' ? (
                  <Check size={12} strokeWidth={2.6} />
                ) : (
                  <span className={state === 'active' ? 'bpx-pulse-dot' : 'bpx-idle-dot'} />
                )}
              </span>
              {step}
            </div>
          )
        })}
      </div>
    </div>
  )
}
