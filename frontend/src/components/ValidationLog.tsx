/**
 * ValidationLog Component
 * Ads validation sonuçlarını gösterir
 */
import { CheckCircle, AlertTriangle, XCircle, ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import './ValidationLog.css'

interface ValidationItem {
  type: 'modified' | 'rejected' | 'passed'
  original: string
  modified?: string
  reason?: string
}

interface ValidationLogProps {
  totalGenerated: number
  modified: number
  rejected: number
  passed: number
  details?: ValidationItem[]
}

export default function ValidationLog({
  totalGenerated,
  modified,
  rejected,
  passed,
  details
}: ValidationLogProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="validation-log">
      <div className="validation-summary">
        <span className="validation-total">
          {totalGenerated} başlık üretildi
        </span>
        <div className="validation-counts">
          <span className="count passed">
            <CheckCircle size={14} />
            {passed} geçti
          </span>
          <span className="count modified">
            <AlertTriangle size={14} />
            {modified} düzeltildi
          </span>
          <span className="count rejected">
            <XCircle size={14} />
            {rejected} elendi
          </span>
        </div>
        {details && details.length > 0 && (
          <button 
            className="btn-details"
            onClick={() => setExpanded(!expanded)}
          >
            Detaylar
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        )}
      </div>

      {expanded && details && (
        <div className="validation-details">
          <table>
            <thead>
              <tr>
                <th>Durum</th>
                <th>Orijinal</th>
                <th>Düzeltilmiş</th>
                <th>Sebep</th>
              </tr>
            </thead>
            <tbody>
              {details.filter(d => d.type !== 'passed').map((item, i) => (
                <tr key={i} className={`row-${item.type}`}>
                  <td>
                    {item.type === 'modified' && <AlertTriangle size={14} />}
                    {item.type === 'rejected' && <XCircle size={14} />}
                  </td>
                  <td>{item.original}</td>
                  <td>{item.modified || '-'}</td>
                  <td>{item.reason || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
