import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Lightbulb, Loader2, Play } from 'lucide-react'
import {
  apiErrorMessage,
  digitalResearchApi,
  downloadBlobResponse,
  type DigitalResearchDetail,
  type DigitalResearchSummary,
  type ResearchClaim,
  type ResearchItem,
  type RestrictionVerification,
  type ResearchTheme,
} from '../services/api'
import ErrorBanner from '../components/ErrorBanner'
import { AUDIT_STATUS_LABEL } from './digitalAuditView'
import DigitalStrategy from './DigitalStrategy'
import {
  claimChip,
  claimDecidable,
  claimInReport,
  numberUnverified,
  yearNote,
} from './digitalResearchView'

const POLL_MS = 4000
const isActive = (status?: string) => status === 'pending' || status === 'running'

interface Props {
  workspaceId: number
  /** Rapor dosyasına eklenecek site denetimi (varsa) */
  auditId?: number
}

function ItemList({ title, items }: { title: string; items?: ResearchItem[] }) {
  if (!items?.length) return null
  return (
    <>
      <h3>{title}</h3>
      <ul className="research-list">
        {items.map((item, i) => (
          <li key={i}>
            {item.text}
            {item.date && <small> ({item.date})</small>}
            {item.source_name && <small> · {item.source_name}</small>}
            {numberUnverified(item) && (
              <span className="audit-chip audit-chip-muted">Rakamı doğrulanamadı</span>
            )}
          </li>
        ))}
      </ul>
    </>
  )
}

function ThemeList({ title, items }: { title: string; items?: ResearchTheme[] }) {
  if (!items?.length) return null
  return (
    <>
      <h3>{title}</h3>
      <ul className="research-list">
        {items.map((item, i) => (
          <li key={i}>
            {item.theme}
            {item.quote_found ? (
              <q className="research-quote">{item.quote}</q>
            ) : (
              <span className="audit-chip audit-chip-muted">Birebir alıntı bulunamadı</span>
            )}
            {item.source_name && <small> · {item.source_name}</small>}
          </li>
        ))}
      </ul>
    </>
  )
}

const VERIFICATION_FIELDS: [keyof RestrictionVerification, string, string][] = [
  ['primary_source_url', 'Birincil kaynak adresi (Resmî Gazete, ilgili kurum)', 'url'],
  ['regulation_date', 'Mevzuat tarihi', 'date'],
  ['verified_by', 'Teyit eden', 'text'],
  ['verified_at', 'Teyit tarihi', 'date'],
]

function RestrictionCheck(props: {
  index: number
  item: ResearchItem
  disabled: boolean
  onSave: (body: Partial<RestrictionVerification>) => void
}) {
  const { index, item, disabled, onSave } = props
  const [form, setForm] = useState<Partial<RestrictionVerification>>(item.verification || {})
  const saved = item.verification
  return (
    <details className="audit-evidence">
      <summary>
        Kısıt {index + 1}:{' '}
        <span className={`audit-chip ${saved ? 'audit-chip-ok' : 'audit-chip-muted'}`}>
          {saved
            ? `Teyit edildi (${saved.verified_by}, ${saved.verified_at})`
            : 'Birincil kaynaktan teyit edilmedi'}
        </span>{' '}
        {item.text.slice(0, 90)}
      </summary>
      {VERIFICATION_FIELDS.map(([field, label, type]) => (
        <input
          key={field}
          className="input"
          type={type}
          aria-label={`Kısıt ${index + 1} ${label}`}
          title={label}
          placeholder={label}
          value={form[field] || ''}
          onChange={(e) => setForm((cur) => ({ ...cur, [field]: e.target.value }))}
        />
      ))}
      <button className="btn btn-secondary" disabled={disabled} onClick={() => onSave(form)}>
        Teyidi kaydet
      </button>
    </details>
  )
}

function SectionFailure({ section }: { section?: { status: string; error?: string } }) {
  if (!section || section.status !== 'failed') return null
  return <p className="audit-status-note">Bu bölüm üretilemedi: {section.error}</p>
}

export default function DigitalResearch({ workspaceId, auditId }: Props) {
  const [reports, setReports] = useState<DigitalResearchSummary[]>([])
  const [detail, setDetail] = useState<DigitalResearchDetail | null>(null)
  const [sector, setSector] = useState('')
  const [geography, setGeography] = useState('Türkiye')
  const [focus, setFocus] = useState('')
  const [segments, setSegments] = useState<number[]>([])
  const [channels, setChannels] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const scopeRef = useRef(workspaceId)
  scopeRef.current = workspaceId

  const loadDetail = useCallback(async (reportId: number, wsId: number) => {
    const res = await digitalResearchApi.get(reportId, wsId)
    if (scopeRef.current !== wsId) return null
    setDetail(res.data)
    return res.data
  }, [])

  const loadList = useCallback(async (wsId: number) => {
    const res = await digitalResearchApi.list(wsId)
    if (scopeRef.current !== wsId) return []
    setReports(res.data.reports)
    return res.data.reports
  }, [])

  useEffect(() => {
    setReports([])
    setDetail(null)
    setSegments([])
    setChannels([])
    setError(null)
    setLoading(true)
    loadList(workspaceId)
      .then((list) => (list.length ? loadDetail(list[0].id, workspaceId) : null))
      .catch((err) =>
        setError(`Araştırmalar alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
      )
      .finally(() => {
        if (scopeRef.current === workspaceId) setLoading(false)
      })
  }, [workspaceId, loadList, loadDetail])

  const activeId = isActive(detail?.status) ? detail?.id : undefined
  useEffect(() => {
    if (!activeId) return
    const timer = setInterval(async () => {
      try {
        const next = await loadDetail(activeId, workspaceId)
        if (next && !isActive(next.status)) await loadList(workspaceId)
      } catch (err) {
        setError(`Araştırma durumu alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [activeId, workspaceId, loadDetail, loadList])

  const guarded = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(`${label}: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    guarded('Araştırma başlatılamadı', async () => {
      const res = await digitalResearchApi.start(workspaceId, {
        sector: sector.trim(),
        geography: geography.trim(),
        focus: focus.trim() || undefined,
      })
      await loadList(workspaceId)
      await loadDetail(res.data.research_id, workspaceId)
    })

  const toggleClaim = (claim: ResearchClaim) => {
    if (!detail) return
    const current = detail.claim_overrides
    const next = current.includes(claim.id)
      ? current.filter((id) => id !== claim.id)
      : [...current, claim.id]
    return guarded('İddia güncellenemedi', async () => {
      const res = await digitalResearchApi.saveClaimOverrides(detail.id, workspaceId, next)
      setDetail((cur) => (cur ? { ...cur, ...res.data } : cur))
    })
  }

  const refreshSynthesis = () =>
    detail &&
    guarded('Segmentler yenilenemedi', async () => {
      await digitalResearchApi.refreshSynthesis(detail.id, workspaceId)
      setSegments([])
      await loadDetail(detail.id, workspaceId)
    })

  const startBriefs = () =>
    detail &&
    guarded('Brief üretimi başlatılamadı', async () => {
      await digitalResearchApi.startBriefs(detail.id, workspaceId, { segments, channels })
      await loadDetail(detail.id, workspaceId)
    })

  const download = (format: 'docx' | 'pdf') =>
    guarded('Rapor indirilemedi', async () => {
      const res = await digitalResearchApi.exportReport(workspaceId, {
        format,
        audit_id: auditId,
        research_id: detail?.id,
      })
      downloadBlobResponse(res, `dijital-varlik-analizi.${format}`)
    })

  const toggle = <T,>(list: T[], value: T) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value]

  const running = isActive(detail?.status)
  const sections = detail?.sections || {}
  const { sector: sec, audience, demand, synthesis } = sections
  const briefs = detail?.briefs
  const overrides = detail?.claim_overrides || []

  const claimTable = (claims?: ResearchClaim[]) =>
    claims?.length ? (
      <div className="audit-table-wrap">
        <table className="audit-table">
          <thead>
            <tr>
              <th>Bilgi</th>
              <th>Değer</th>
              <th>Yıl / coğrafya</th>
              <th>Kaynak kontrolü</th>
              <th>Raporda</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => {
              const chip = claimChip(claim, overrides)
              const note = yearNote(claim)
              return (
                <tr key={claim.id}>
                  <th scope="row">{claim.claim}</th>
                  <td>
                    {claim.value} {claim.unit}
                  </td>
                  <td>
                    {claim.year} / {claim.geography}
                  </td>
                  <td>
                    <span className={`audit-chip audit-chip-${chip.tone}`}>{chip.text}</span>
                    {claim.check.context && (
                      <div className="audit-cell-note">
                        “…{claim.check.context}…”{' '}
                        <a href={claim.check.source_url} target="_blank" rel="noreferrer">
                          kaynak
                        </a>
                      </div>
                    )}
                    {note && <div className="audit-cell-note">{note}</div>}
                  </td>
                  <td>
                    {claimDecidable(claim) ? (
                      <label className="research-check">
                        <input
                          type="checkbox"
                          aria-label={`${claim.claim} rapora girsin`}
                          checked={claimInReport(claim, overrides)}
                          disabled={busy}
                          onChange={() => toggleClaim(claim)}
                        />
                        Rapora al
                      </label>
                    ) : (
                      'Girmez'
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    ) : null

  return (
    <>
      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      <section className="glass-card audit-start">
        <h2>Yeni araştırma</h2>
        <div className="audit-url-grid">
          <label>
            Sektör
            <input
              className="input"
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              placeholder="ör. özel diş kliniği"
            />
          </label>
          <label>
            Coğrafya
            <input
              className="input"
              value={geography}
              onChange={(e) => setGeography(e.target.value)}
            />
          </label>
          <label>
            Odak (isteğe bağlı)
            <input
              className="input"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="ör. implant, sağlık turizmi"
            />
          </label>
        </div>
        <button
          className="btn btn-primary"
          onClick={start}
          disabled={busy || running || sector.trim().length < 3 || geography.trim().length < 2}
        >
          {running ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
          {running ? 'Araştırma sürüyor' : 'Araştırmayı başlat'}
        </button>
        <p className="audit-hint">
          Web araması yapan yapay zekâ kullanılır; yaklaşık 3-6 dakika sürer. Rakamlar yalnız kaynak
          metninde bulunursa rapora girer. Segmentler ve fikirler hipotezdir.
        </p>
      </section>

      {reports.length > 1 && (
        <div className="audit-history">
          {reports.map((r) => (
            <button
              key={r.id}
              className={`filter-btn ${detail?.id === r.id ? 'active' : ''}`}
              onClick={() => guarded('Araştırma alınamadı', () => loadDetail(r.id, workspaceId))}
            >
              {new Date(r.created_at).toLocaleString('tr-TR')} · {r.inputs.sector} ·{' '}
              {AUDIT_STATUS_LABEL[r.status]}
            </button>
          ))}
        </div>
      )}

      {loading && !detail && <div className="loading-state">Yükleniyor...</div>}
      {!loading && !detail && !error && (
        <div className="empty-state">
          <Lightbulb size={32} />
          <p>Bu marka çalışmasında henüz araştırma yok.</p>
        </div>
      )}

      {detail && (
        <>
          <section className={`glass-card audit-status audit-status-${detail.status}`}>
            <div className="audit-status-line">
              {running && <Loader2 size={18} className="animate-spin" />}
              <strong>
                {running && detail.stage === 'strategy'
                  ? 'Strateji taslağı üretiliyor'
                  : running && detail.stage === 'briefs'
                    ? 'Briefler üretiliyor'
                    : running && detail.stage === 'synthesis'
                      ? 'Segmentler yenileniyor'
                      : AUDIT_STATUS_LABEL[detail.status]}
              </strong>
              {running && <span>%{detail.progress}</span>}
              <span>
                {detail.inputs.sector} · {detail.inputs.geography}
              </span>
              <span className="audit-date">
                {new Date(detail.created_at).toLocaleString('tr-TR')}
              </span>
            </div>
            {detail.error_message && <p className="audit-status-note">{detail.error_message}</p>}
            {!running && (
              <div className="research-actions">
                <button
                  className="btn btn-secondary"
                  onClick={() => download('docx')}
                  disabled={busy}
                >
                  <Download size={18} /> Rapor (Word)
                </button>
                <button
                  className="btn btn-secondary"
                  onClick={() => download('pdf')}
                  disabled={busy}
                >
                  <Download size={18} /> Rapor (PDF)
                </button>
              </div>
            )}
          </section>

          {sec && (
            <section className="glass-card audit-section">
              <h2>Sektör analizi</h2>
              <SectionFailure section={sec} />
              {sec.status === 'ok' && sec.verified === false && (
                <p className="audit-status-note">
                  Kaynak doğrulaması tamamlanmadı; hiçbir rakam doğrulanmış sayılmaz.
                </p>
              )}
              <ItemList title="Tanım ve alt kategoriler" items={sec.definition} />
              <ItemList title="Başlıca ürün ve hizmetler" items={sec.services} />
              <ItemList title="Büyüme yönü" items={sec.growth} />
              {sec.claims && sec.claims.length > 0 && <h3>Rakamlar</h3>}
              {claimTable(sec.claims)}
              <ItemList title="Güncel gelişmeler" items={sec.developments} />
              <ItemList title="Yasal ve reklam kısıtları" items={sec.restrictions} />
              {sec.restrictions && sec.restrictions.length > 0 && (
                <>
                  <p className="audit-hint">
                    Tarihli bir özettir; hukuki görüş değildir. Aşağıda her kısıt için birincil
                    kaynak teyidi kaydedilir. Teyidi analist yapar; sistem kaynağı kendisi kontrol
                    etmez. Dört alan birlikte girilmeden kısıt "teyit edildi" görünmez.
                  </p>
                  {sec.restrictions.map((item, index) => (
                    <RestrictionCheck
                      key={`${detail.id}-${index}-${item.verification?.verified_at || ''}`}
                      index={index}
                      item={item}
                      disabled={busy || running}
                      onSave={(body) =>
                        guarded('Teyit kaydedilemedi', async () => {
                          const res = await digitalResearchApi.saveRestrictionVerification(
                            detail.id,
                            workspaceId,
                            index,
                            body
                          )
                          setDetail(res.data)
                        })
                      }
                    />
                  ))}
                </>
              )}
              <ItemList title="Sektörel platform ve dizinler" items={sec.directories} />
              <ItemList
                title="Veri bulunamayan başlıklar"
                items={sec.not_found?.map((text) => ({ text }))}
              />
            </section>
          )}

          {demand && (
            <section className="glass-card audit-section">
              <h2>Arama talebi</h2>
              <SectionFailure section={demand} />
              {demand.status === 'unavailable' && (
                <p className="audit-hint">Veri yok: {demand.reason}</p>
              )}
              {demand.status === 'ok' && (
                <>
                  <p className="audit-hint">
                    Google Ads, {demand.keywords?.length} anahtar kelimenin son 12 aylık arama
                    hacmi. Arama talebidir; satış mevsimselliği değildir.
                    {demand.peak_months?.length
                      ? ` Yüksek aylar: ${demand.peak_months.join(', ')}.`
                      : ' Belirgin yüksek ay görülmedi.'}
                  </p>
                  <div className="audit-table-wrap">
                    <table className="audit-table">
                      <thead>
                        <tr>
                          <th>Ay</th>
                          <th>Toplam arama</th>
                          <th>Ortalamaya oran</th>
                        </tr>
                      </thead>
                      <tbody>
                        {demand.by_month?.map((m) => (
                          <tr key={m.month}>
                            <th scope="row">{m.month}</th>
                            <td>{m.searches.toLocaleString('tr-TR')}</td>
                            <td>{m.index}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {demand.bid_range && (
                    <p className="audit-hint">
                      Sayfa başı teklif aralığı (hesap para birimi): ortanca{' '}
                      {demand.bid_range.low_median} - {demand.bid_range.high_median}. Beklenen
                      tıklama maliyeti değildir.
                    </p>
                  )}
                </>
              )}
            </section>
          )}

          {audience && (
            <section className="glass-card audit-section">
              <h2>Hedef kitle analizi</h2>
              <SectionFailure section={audience} />
              <ThemeList title="İhtiyaç ve problemler" items={audience.needs} />
              <ThemeList title="Tereddüt ve şikayetler" items={audience.hesitations} />
              <ItemList title="Sık sorulan sorular" items={audience.questions} />
              <ItemList
                title="Hacmi olan soru aramaları (gerçek veri)"
                items={audience.question_keywords?.map((q) => ({
                  text: `${q.keyword} · aylık ${q.monthly_volume.toLocaleString('tr-TR')}`,
                }))}
              />
              <ItemList title="Güven beklentileri" items={audience.trust} />
            </section>
          )}

          {synthesis && (
            <section className="glass-card audit-section">
              <h2>
                Olası segmentler <span className="audit-chip audit-chip-neutral">Hipotez</span>
              </h2>
              <SectionFailure section={synthesis} />
              {(detail.synthesis_stale || synthesis.status === 'failed') && (
                <div className="research-actions">
                  {detail.synthesis_stale && (
                    <p className="audit-status-note">
                      Rapora alınan rakamlar ya da denetimdeki brif/beyan bilgileri değişti ya da bu
                      segmentlerin hangi bilgilerle üretildiği doğrulanamadı. Segmentler rapora
                      girmez ve brief üretilemez; araştırmayı tekrarlamak gerekmez. Kararlarınızı
                      bitirince segmentleri yenileyin (mevcut briefler silinir).
                    </p>
                  )}
                  <button
                    className="btn btn-primary"
                    onClick={refreshSynthesis}
                    disabled={busy || running}
                  >
                    <Play size={18} /> Segmentleri yenile
                  </button>
                </div>
              )}
              <div className="research-cards">
                {synthesis.segments?.map((s, i) => (
                  <article key={s.name} className="research-card">
                    <label className="research-check">
                      <input
                        type="checkbox"
                        checked={segments.includes(i)}
                        onChange={() => setSegments((cur) => toggle(cur, i))}
                      />
                      <strong>{s.name}</strong>
                      <small>{s.stage}</small>
                    </label>
                    <p>{s.need}</p>
                    <p>
                      <b>Dayandığı bulgu:</b> {s.evidence}
                    </p>
                    <p>
                      <b>Doğrulanacak varsayım:</b> {s.assumption}
                    </p>
                    <p>
                      <b>Değer teklifi:</b> {s.value_proposition}
                    </p>
                    <ul className="research-list">
                      {s.messages.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                    {s.warnings.length > 0 && (
                      <p className="audit-status-note">
                        Kanıtı olmayan iddia: {s.warnings.join('; ')}
                      </p>
                    )}
                  </article>
                ))}
              </div>
              <ItemList
                title="Fırsat ve farklılaşma"
                items={synthesis.opportunities?.map((o) => ({
                  text: `${o.text} (dayanak: ${o.evidence})`,
                }))}
              />
              <ItemList
                title="Google Ads önerisi"
                items={[
                  ...(synthesis.google_ads?.keyword_groups || []).map((g) => ({
                    text: `${g.group}: ${g.keywords.join(', ')}`,
                  })),
                  ...(synthesis.google_ads?.negatives.length
                    ? [{ text: `Negatif kelimeler: ${synthesis.google_ads.negatives.join(', ')}` }]
                    : []),
                ]}
              />
              <ItemList
                title="Meta hedef kitle önerisi (ilgi alanları platformda doğrulanmadı)"
                items={synthesis.meta_audiences?.map((m) => ({
                  text: `${m.segment}: ${m.location}; yaş hipotezi ${m.age_hypothesis}; ${m.interests.join(', ')}`,
                }))}
              />

              {synthesis.status === 'ok' && !detail.synthesis_stale && (
                <div className="research-brief-form">
                  <h3>Brief üret</h3>
                  <p className="audit-hint">
                    Yukarıdan segment, aşağıdan kanal seçin. Çıktı brieftir; görsel ya da video
                    üretilmez.
                  </p>
                  <div className="research-channels">
                    {Object.entries(detail.channels).map(([key, label]) => (
                      <label key={key} className="research-check">
                        <input
                          type="checkbox"
                          checked={channels.includes(key)}
                          onChange={() => setChannels((cur) => toggle(cur, key))}
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                  <button
                    className="btn btn-primary"
                    onClick={startBriefs}
                    disabled={busy || running || !segments.length || !channels.length}
                  >
                    <Lightbulb size={18} /> Brief üret
                  </button>
                </div>
              )}
            </section>
          )}

          {briefs && briefs.status !== 'pending' && (
            <section className="glass-card audit-section">
              <h2>
                Reklam ve kreatif briefler{' '}
                <span className="audit-chip audit-chip-neutral">Hipotez</span>
              </h2>
              <SectionFailure section={briefs} />
              {detail.briefs_stale && (
                <p className="audit-status-note">
                  Bu brieflerin güncelliği doğrulanamadı; rapora girmez. Briefi yeniden üretin
                  (segmentler de yenileme istiyorsa önce onları yenileyin).
                </p>
              )}
              {[...(briefs.ad_ideas || []), ...(briefs.creative_briefs || [])].some(
                (b) => b.restriction_note
              ) && (
                <p className="audit-status-note research-restriction">
                  {briefs.ad_ideas?.[0]?.restriction_note ||
                    briefs.creative_briefs?.[0]?.restriction_note}
                </p>
              )}
              <div className="research-cards">
                {briefs.ad_ideas?.map((idea, i) => (
                  <article key={`a${i}`} className="research-card">
                    <strong>Reklam fikri</strong>
                    <small>
                      {idea.segment} · {idea.channel}
                    </small>
                    <p>
                      <b>İçgörü:</b> {idea.insight}
                    </p>
                    <p>
                      <b>Teklif:</b> {idea.offer}
                    </p>
                    <p>
                      <b>Mesaj:</b> {idea.message}
                    </p>
                    <p>
                      <b>Çağrı:</b> {idea.cta}
                    </p>
                    <p>
                      <b>Test hipotezi:</b> {idea.test_hypothesis}
                    </p>
                    {idea.warnings.length > 0 && (
                      <p className="audit-status-note">
                        Kanıtı olmayan iddia: {idea.warnings.join('; ')}
                      </p>
                    )}
                  </article>
                ))}
                {briefs.creative_briefs?.map((brief, i) => (
                  <article key={`c${i}`} className="research-card">
                    <strong>Kreatif brief</strong>
                    <small>
                      {brief.segment} · {brief.channel} · {brief.format} · {brief.duration}
                    </small>
                    <p>
                      <b>Açılış:</b> {brief.hook}
                    </p>
                    <p>
                      <b>Görsel yön:</b> {brief.visual_direction}
                    </p>
                    <p>
                      <b>Çekim listesi:</b> {brief.shot_list.join('; ')}
                    </p>
                    <p>
                      <b>Metin:</b> {brief.copy}
                    </p>
                    <p>
                      <b>Gereken gerçek kanıt:</b> {brief.proof_needed.join('; ')}
                    </p>
                    <p>
                      <b>A/B varyantı:</b> {brief.ab_variant}
                    </p>
                    {brief.warnings.length > 0 && (
                      <p className="audit-status-note">
                        Kanıtı olmayan iddia: {brief.warnings.join('; ')}
                      </p>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}

          <DigitalStrategy
            detail={detail}
            workspaceId={workspaceId}
            running={running}
            busy={busy}
            guarded={guarded}
            onDetail={setDetail}
            reload={() => loadDetail(detail.id, workspaceId)}
          />
        </>
      )}
    </>
  )
}
