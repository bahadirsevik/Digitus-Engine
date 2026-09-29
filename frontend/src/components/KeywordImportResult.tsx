import { AlertCircle, CheckCircle, ChevronDown, ChevronUp, RotateCcw, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { KeywordImportResponse, SkippedKeywordDetail } from '../services/api'
import { reasonLabel } from './importReasons'

type ImportResult =
  | { kind: 'success'; data: KeywordImportResponse }
  | { kind: 'error'; message: string }
  | null

const formatImportSummary = (r: KeywordImportResponse): string => {
  const parts = [
    `${r.created} eklendi`,
    `${r.skipped} atlandi`,
    `toplam ${r.total_before} -> ${r.total_after}`,
  ]
  if ((r.linked_existing || 0) > 0) {
    parts.splice(1, 0, `${r.created_new || 0} yeni kayit, ${r.linked_existing} mevcut baglandi`)
  }
  if ((r.skipped_limit || 0) > 0) {
    parts.push(`${r.skipped_limit} satir havuz limiti nedeniyle atlandi`)
  }
  return `${r.requested} satir islendi: ${parts.join(' | ')}`
}

export default function KeywordImportResult({
  result,
  onForceInclude,
}: {
  result: ImportResult
  // Yasakli temayla elenen satiri force_include=true ile yeniden import eder
  // (response-tabanli geri al; persist yok)
  onForceInclude?: (detail: SkippedKeywordDetail) => Promise<void>
}) {
  const [showSkipped, setShowSkipped] = useState(false)
  const [showThemed, setShowThemed] = useState(false)
  const [restoring, setRestoring] = useState<string | null>(null)
  const [restored, setRestored] = useState<Set<string>>(new Set())

  if (!result) return null

  if (result.kind === 'error') {
    return (
      <div className="import-result import-result--error">
        <AlertCircle size={15} />
        {result.message}
      </div>
    )
  }

  const allSkipped = result.data.skipped_details || []
  const themed = allSkipped.filter((d) => d.reason === 'skipped_theme')
  const skipped = allSkipped.filter((d) => d.reason !== 'skipped_theme')

  const handleRestore = async (detail: SkippedKeywordDetail) => {
    if (!onForceInclude) return
    setRestoring(detail.keyword)
    try {
      await onForceInclude(detail)
      setRestored((prev) => new Set(prev).add(detail.keyword))
    } finally {
      setRestoring(null)
    }
  }

  return (
    <div className="import-result import-result--success">
      <div className="import-result-row">
        <CheckCircle size={15} />
        <span>{formatImportSummary(result.data)}</span>
      </div>

      {themed.length > 0 && (
        <>
          <button type="button" className="skipped-toggle" onClick={() => setShowThemed((v) => !v)}>
            {showThemed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            Yasakli temayla elendi ({themed.length}) — {showThemed ? 'gizle' : 'goruntule'}
          </button>
          {showThemed && (
            <ul className="skipped-list themed-list">
              {themed.map((d, i) => (
                <li key={i}>
                  <strong>{d.keyword}</strong>
                  <span className="skipped-reason">- {reasonLabel(d)}</span>
                  {onForceInclude &&
                    (restored.has(d.keyword) ? (
                      <span className="restored-note">
                        <CheckCircle size={12} /> eklendi
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm restore-btn"
                        onClick={() => handleRestore(d)}
                        disabled={restoring !== null}
                      >
                        {restoring === d.keyword ? (
                          <Loader2 size={12} className="spin-icon" />
                        ) : (
                          <RotateCcw size={12} />
                        )}
                        Geri al
                      </button>
                    ))}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {skipped.length > 0 && (
        <>
          <button
            type="button"
            className="skipped-toggle"
            onClick={() => setShowSkipped((v) => !v)}
          >
            {showSkipped ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            Atlanan kelimeleri {showSkipped ? 'gizle' : 'goster'} ({skipped.length})
          </button>
          {showSkipped && (
            <ul className="skipped-list">
              {skipped.map((d, i) => (
                <li key={i}>
                  <strong>{d.keyword}</strong>
                  <span className="skipped-reason">- {reasonLabel(d)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
