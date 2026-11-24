import { useCallback, useEffect, useRef, useState } from 'react'
import { ClipboardCheck, Download, Loader2, Play, Save } from 'lucide-react'
import {
  apiErrorMessage,
  digitalAuditApi,
  digitalResearchApi,
  downloadBlobResponse,
  type AuditAnswers,
  type AuditEvidence,
  type AuditQuestion,
  type DigitalAuditDetail,
  type DigitalAuditSummary,
} from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import ErrorBanner from '../components/ErrorBanner'
import {
  AUDIT_STATUS_LABEL,
  SECTION_LABEL,
  TARGET_STATUS_LABEL,
  buildSections,
  findingChip,
  findingSummary,
  hostOf,
} from './digitalAuditView'
import DigitalResearch from './DigitalResearch'
import DigitalVisibility from './DigitalVisibility'
import './DigitalAudit.css'

const POLL_MS = 3000
const MAX_COMPETITORS = 3
const MAX_KEYWORDS = 10
const isActive = (status?: string) => status === 'pending' || status === 'running'

const EVIDENCE_FIELDS: [keyof AuditEvidence, string, string][] = [
  ['source', 'Kaynak (ör. GA4 paneli, Search Console dışa aktarımı)', 'text'],
  ['checked_at', 'Kontrol tarihi', 'date'],
  ['verified_by', 'Doğrulayan kişi', 'text'],
  ['period', 'Dönem (ör. son 28 gün)', 'text'],
  ['account', 'Hesap / property (varsa)', 'text'],
]

export default function DigitalAudit() {
  const workspace = useBrandStore((s) => s.activeWorkspace)
  const workspaceId = workspace?.id
  const [audits, setAudits] = useState<DigitalAuditSummary[]>([])
  const [detail, setDetail] = useState<DigitalAuditDetail | null>(null)
  const [ownUrl, setOwnUrl] = useState('')
  const [competitors, setCompetitors] = useState<string[]>(['', '', ''])
  const [answers, setAnswers] = useState<AuditAnswers>({})
  const [measure, setMeasure] = useState(false)
  const [keywordText, setKeywordText] = useState('')
  const [location, setLocation] = useState('Istanbul, Turkey')
  const [brandName, setBrandName] = useState('')
  const [loading, setLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'audit' | 'research'>('audit')
  // Workspace değişince uçuştaki eski yanıt yeni ekrana yazılmaz
  const scopeRef = useRef<number | undefined>(workspaceId)
  scopeRef.current = workspaceId

  const loadDetail = useCallback(async (auditId: number, wsId: number, resetAnswers: boolean) => {
    const res = await digitalAuditApi.get(auditId, wsId)
    if (scopeRef.current !== wsId) return null
    setDetail(res.data)
    if (resetAnswers) setAnswers(res.data.answers || {})
    return res.data
  }, [])

  const loadList = useCallback(async (wsId: number) => {
    const res = await digitalAuditApi.list(wsId)
    if (scopeRef.current !== wsId) return []
    setAudits(res.data.audits)
    return res.data.audits
  }, [])

  useEffect(() => {
    setAudits([])
    setDetail(null)
    setAnswers({})
    setError(null)
    setOwnUrl(workspace?.company_url || '')
    setBrandName(workspace?.name || '')
    setMeasure(false)
    const known = (workspace?.competitor_urls || []).slice(0, MAX_COMPETITORS)
    setCompetitors([...known, '', '', ''].slice(0, MAX_COMPETITORS))
    if (!workspaceId) return
    setLoading(true)
    loadList(workspaceId)
      .then((list) => (list.length ? loadDetail(list[0].id, workspaceId, true) : null))
      .catch((err) => setError(`Denetimler alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`))
      .finally(() => {
        if (scopeRef.current === workspaceId) setLoading(false)
      })
  }, [workspaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  const activeId = isActive(detail?.status) ? detail?.id : undefined
  useEffect(() => {
    if (!activeId || !workspaceId) return
    const timer = setInterval(async () => {
      try {
        const next = await loadDetail(activeId, workspaceId, false)
        if (next && !isActive(next.status)) await loadList(workspaceId)
      } catch (err) {
        setError(`Denetim durumu alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [activeId, workspaceId, loadDetail, loadList])

  const start = async () => {
    if (!workspaceId) return
    setStarting(true)
    setError(null)
    try {
      const res = await digitalAuditApi.start(workspaceId, {
        own_url: ownUrl.trim() || undefined,
        competitor_urls: competitors.map((c) => c.trim()).filter(Boolean),
        visibility: measure
          ? { keywords, location: location.trim(), brand_name: brandName.trim() }
          : undefined,
      })
      await loadList(workspaceId)
      await loadDetail(res.data.audit_id, workspaceId, true)
    } catch (err) {
      setError(`Denetim başlatılamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    } finally {
      setStarting(false)
    }
  }

  const select = async (auditId: number) => {
    if (!workspaceId) return
    try {
      await loadDetail(auditId, workspaceId, true)
    } catch (err) {
      setError(`Denetim alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    }
  }

  const saveAnswers = async () => {
    if (!workspaceId || !detail) return
    setSaving(true)
    try {
      const res = await digitalAuditApi.saveAnswers(detail.id, workspaceId, answers)
      setAnswers(res.data.answers)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (err) {
      setError(`Cevaplar kaydedilemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    } finally {
      setSaving(false)
    }
  }

  const download = async (format: 'docx' | 'pdf', view?: 'pitch') => {
    if (!workspaceId || !detail) return
    try {
      const res = await digitalResearchApi.exportReport(workspaceId, {
        format,
        audit_id: detail.id,
        ...(view ? { view } : {}),
      })
      downloadBlobResponse(res, `${view ? 'musteri-sunumu' : 'dijital-denetim'}.${format}`)
    } catch (err) {
      setError(`Rapor indirilemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    }
  }

  const setAnswer = (key: string, field: 'value' | 'note', text: string) =>
    setAnswers((cur) => ({ ...cur, [key]: { ...cur[key], [field]: text } }))
  const setEvidence = (key: string, field: keyof AuditEvidence, text: string) =>
    setAnswers((cur) => ({
      ...cur,
      [key]: { ...cur[key], evidence: { ...cur[key]?.evidence, [field]: text } },
    }))

  const questionField = (q: AuditQuestion) => (
    <div key={q.key} className="audit-question">
      <label htmlFor={`q-${q.key}`}>
        {q.label}
        {q.group !== 'brief' && answers[q.key]?.value && (
          <span className="audit-answer-tag">
            {answers[q.key]?.evidence?.source ? 'Analist doğruladı' : 'Beyan'}
          </span>
        )}
      </label>
      {q.options ? (
        <select
          id={`q-${q.key}`}
          className="input"
          value={answers[q.key]?.value || ''}
          onChange={(e) => setAnswer(q.key, 'value', e.target.value)}
        >
          <option value="">Cevaplanmadı</option>
          {q.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={`q-${q.key}`}
          className="input"
          value={answers[q.key]?.value || ''}
          onChange={(e) => setAnswer(q.key, 'value', e.target.value)}
        />
      )}
      <input
        className="input"
        aria-label={`${q.label} notu`}
        placeholder="Not (isteğe bağlı)"
        value={answers[q.key]?.note || ''}
        onChange={(e) => setAnswer(q.key, 'note', e.target.value)}
      />
      {q.group !== 'brief' && (
        <details className="audit-evidence" open={!!answers[q.key]?.evidence?.source}>
          <summary>Analist kanıtı</summary>
          {EVIDENCE_FIELDS.map(([field, label, type]) => (
            <input
              key={field}
              className="input"
              type={type}
              aria-label={`${q.label} ${label}`}
              title={label}
              placeholder={label}
              value={answers[q.key]?.evidence?.[field] || ''}
              onChange={(e) => setEvidence(q.key, field, e.target.value)}
            />
          ))}
        </details>
      )}
    </div>
  )

  if (!workspaceId) {
    return (
      <div className="audit-page animate-fade-in">
        <header className="page-header">
          <div>
            <h1>Dijital Denetim</h1>
          </div>
        </header>
        <div className="empty-state">Denetim için önce bir marka çalışması seçin.</div>
      </div>
    )
  }

  const running = isActive(detail?.status)
  const keywords = keywordText
    .split('\n')
    .map((k) => k.trim())
    .filter(Boolean)
  const tooManyKeywords = keywords.length > MAX_KEYWORDS
  const targets = detail?.targets || []
  const sections = buildSections(targets)

  return (
    <div className="audit-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Dijital Denetim</h1>
          <p>Markanın ve rakiplerin web sitelerinde gözlenen durum, kaynağı ve sınırlarıyla</p>
        </div>
      </header>

      <div className="audit-history" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'audit'}
          className={`filter-btn ${tab === 'audit' ? 'active' : ''}`}
          onClick={() => setTab('audit')}
        >
          Site denetimi
        </button>
        <button
          role="tab"
          aria-selected={tab === 'research'}
          className={`filter-btn ${tab === 'research' ? 'active' : ''}`}
          onClick={() => setTab('research')}
        >
          Araştırma ve fikir
        </button>
      </div>

      {tab === 'research' && (
        <DigitalResearch
          workspaceId={workspaceId}
          auditId={detail && !running ? detail.id : undefined}
        />
      )}
      {tab === 'audit' && renderAudit()}
    </div>
  )

  function renderAudit() {
    return (
      <>
        {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

        <section className="glass-card audit-start">
          <h2>Yeni denetim</h2>
          <div className="audit-url-grid">
            <label>
              Marka sitesi
              <input
                className="input"
                value={ownUrl}
                onChange={(e) => setOwnUrl(e.target.value)}
                placeholder="https://ornek.com"
              />
            </label>
            {competitors.map((value, i) => (
              <label key={i}>
                Rakip {i + 1}
                <input
                  className="input"
                  value={value}
                  onChange={(e) =>
                    setCompetitors((cur) => cur.map((c, j) => (j === i ? e.target.value : c)))
                  }
                  placeholder="https://rakip.com (isteğe bağlı)"
                />
              </label>
            ))}
          </div>
          <label className="research-check">
            <input
              type="checkbox"
              checked={measure}
              onChange={(e) => setMeasure(e.target.checked)}
            />
            Arama görünürlüğü, YouTube ve Google profilini de ölç (ücretli arama sorgusu kullanır)
          </label>
          {measure && (
            <>
              <div className="audit-url-grid">
                <label>
                  Anahtar kelimeler (her satıra bir tane, en fazla {MAX_KEYWORDS})
                  <textarea
                    className="input"
                    rows={4}
                    value={keywordText}
                    onChange={(e) => setKeywordText(e.target.value)}
                    placeholder={'ataköy diş hekimi\nbakırköy implant'}
                  />
                </label>
                <label>
                  Arama konumu
                  <input
                    className="input"
                    value={location}
                    onChange={(e) => setLocation(e.target.value)}
                    placeholder="Istanbul, Turkey"
                  />
                </label>
                <label>
                  Marka adı (marka ve itibar araması için)
                  <input
                    className="input"
                    value={brandName}
                    onChange={(e) => setBrandName(e.target.value)}
                  />
                </label>
              </div>
              <p className="audit-hint">
                Kelime başına en fazla 2, marka adı için 3 arama sorgusu harcanır (bu denetimde en
                fazla {keywords.length * 2 + (brandName.trim() ? 3 : 0)}; üst sınır 23). Masaüstü,
                Google Türkiye, ilk 20 sonuç.
                {tooManyKeywords && ` En fazla ${MAX_KEYWORDS} kelime girilebilir.`}
              </p>
            </>
          )}
          <button
            className="btn btn-primary"
            onClick={start}
            disabled={starting || running || (measure && tooManyKeywords)}
          >
            {starting || running ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Play size={18} />
            )}
            {running ? 'Denetim sürüyor' : 'Denetimi başlat'}
          </button>
          <p className="audit-hint">
            Site başına en fazla 10 sayfa ve 40 site içi bağlantı incelenir; bir marka ve üç rakip
            yaklaşık 5-8 dakika sürer.
          </p>
        </section>

        {audits.length > 1 && (
          <div className="audit-history">
            {audits.map((a) => (
              <button
                key={a.id}
                className={`filter-btn ${detail?.id === a.id ? 'active' : ''}`}
                onClick={() => select(a.id)}
              >
                {new Date(a.created_at).toLocaleString('tr-TR')} · {AUDIT_STATUS_LABEL[a.status]}
              </button>
            ))}
          </div>
        )}

        {loading && !detail && <div className="loading-state">Yükleniyor...</div>}
        {!loading && !detail && !error && (
          <div className="empty-state">
            <ClipboardCheck size={32} />
            <p>Bu marka çalışmasında henüz denetim yok.</p>
          </div>
        )}

        {detail && (
          <>
            <section className={`glass-card audit-status audit-status-${detail.status}`}>
              <div className="audit-status-line">
                {running && <Loader2 size={18} className="animate-spin" />}
                <strong>{AUDIT_STATUS_LABEL[detail.status]}</strong>
                {running && <span>%{detail.progress}</span>}
                <span className="audit-date">
                  {new Date(detail.created_at).toLocaleString('tr-TR')}
                </span>
              </div>
              {detail.error_message && <p className="audit-status-note">{detail.error_message}</p>}
              <ul className="audit-targets">
                {targets.map((t) => (
                  <li key={t.id}>
                    <span className="audit-target-kind">
                      {t.kind === 'own' ? 'Marka' : 'Rakip'}
                    </span>
                    <span className="audit-target-host">{hostOf(t.url)}</span>
                    <span
                      className={`audit-chip audit-chip-${t.status === 'completed' ? 'ok' : 'muted'}`}
                    >
                      {TARGET_STATUS_LABEL[t.status]}
                    </span>
                    {t.status === 'completed' && <span>{t.pages_checked} sayfa incelendi</span>}
                  </li>
                ))}
              </ul>
              {!running && (
                <>
                  <div className="research-actions">
                    <button className="btn btn-secondary" onClick={() => download('docx')}>
                      <Download size={18} /> Denetim raporu (Word)
                    </button>
                    <button className="btn btn-secondary" onClick={() => download('pdf')}>
                      <Download size={18} /> Denetim raporu (PDF)
                    </button>
                    <button className="btn btn-primary" onClick={() => download('pdf', 'pitch')}>
                      <Download size={18} /> Müşteri sunumu (PDF)
                    </button>
                  </div>
                  <p className="audit-hint">
                    Müşteri sunumu, toplantıda gösterilecek kısa özettir: öncelikle düzeltilecekler
                    ve 90 günlük plan. Denetim raporu yalnızca site denetimini içerir. Araştırma,
                    segment ve fikirlerle birleşik rapor "Araştırma ve fikir" sekmesinden indirilir.
                  </p>
                </>
              )}
            </section>

            {sections.map(({ section, rows }) => (
              <section key={section} className="glass-card audit-section">
                <h2>{SECTION_LABEL[section] || section}</h2>
                <div className="audit-table-wrap">
                  <table className="audit-table">
                    <thead>
                      <tr>
                        <th>Kontrol</th>
                        {targets.map((t) => (
                          <th key={t.id}>
                            {hostOf(t.url)}
                            <small>{t.kind === 'own' ? 'Marka' : 'Rakip'}</small>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr key={row.check}>
                          <th scope="row">
                            {row.label}
                            {row.limits.length > 0 && (
                              <details className="audit-limits">
                                <summary>Sınırlar</summary>
                                <ul>
                                  {row.limits.map((l) => (
                                    <li key={l}>{l}</li>
                                  ))}
                                </ul>
                              </details>
                            )}
                          </th>
                          {row.cells.map((f, i) => {
                            const chip = findingChip(f)
                            const summary = findingSummary(f)
                            return (
                              <td key={targets[i].id}>
                                <span className={`audit-chip audit-chip-${chip.tone}`}>
                                  {chip.text}
                                </span>
                                {summary && <div className="audit-cell-summary">{summary}</div>}
                                {f?.note && <div className="audit-cell-note">{f.note}</div>}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}

            {detail.visibility && (
              <DigitalVisibility
                auditId={detail.id}
                workspaceId={workspaceId!}
                visibility={detail.visibility}
              />
            )}

            <section className="glass-card audit-section">
              {detail.questions.some((q) => q.group === 'brief') && (
                <>
                  <h2>Analist brifi</h2>
                  <p className="audit-hint">
                    Segment ve brief üretiminin bağlamı. Burada bir şey değişirse önceki segment ve
                    briefler güncel sayılmaz; yeniden üretilmeleri gerekir.
                  </p>
                  <div className="audit-questions">
                    {detail.questions.filter((q) => q.group === 'brief').map(questionField)}
                  </div>
                </>
              )}
              {detail.questions.some((q) => q.group !== 'brief') && (
                <>
                  <h2>Müşteriye sorulacaklar</h2>
                  <p className="audit-hint">
                    Dışarıdan görülemeyen maddeler. Cevaplar otomatik bulgulardan ayrı saklanır ve
                    onları değiştirmez. Kanıt girilmeyen cevap raporda "Beyan" olarak, kaynağı,
                    kontrol tarihi ve doğrulayan kişi girilen cevap "Analist doğruladı" olarak
                    etiketlenir; sistem kaynağı kendisi kontrol etmez.
                  </p>
                  <div className="audit-questions">
                    {detail.questions.filter((q) => q.group !== 'brief').map(questionField)}
                  </div>
                </>
              )}
              <button className="btn btn-secondary" onClick={saveAnswers} disabled={saving}>
                <Save size={18} />
                {saved ? 'Kaydedildi' : 'Cevapları kaydet'}
              </button>
            </section>
          </>
        )}
      </>
    )
  }
}
