import { useEffect, useState } from 'react'
import { CheckCircle2, Download, Play, Save, Trash2 } from 'lucide-react'
import {
  digitalResearchApi,
  downloadBlobResponse,
  type DigitalResearchDetail,
  type StrategyContent,
  type StrategyItem,
} from '../services/api'

interface Props {
  detail: DigitalResearchDetail
  workspaceId: number
  running: boolean
  busy: boolean
  guarded: (label: string, action: () => Promise<unknown>) => Promise<void>
  onDetail: (detail: DigitalResearchDetail) => void
  reload: () => Promise<unknown>
}

const STATE_LABEL: Record<string, string> = {
  none: 'Taslak üretilmedi',
  failed: 'Taslak üretilemedi',
  stale: 'Güncel değil: girdiler değişti, yeniden üretilmeli',
  draft: 'Taslak: analist onayı yok',
  approved: 'Analist inceledi',
}

type Field = [string, string, string[]?]
/** Liste yolu ('swot.strengths' gibi), başlık, düzeltilebilir alanlar */
const GROUPS: [string, string, Field[]][] = [
  [
    'summary',
    'Öne çıkan bulgular',
    [
      ['finding', 'Bulgu'],
      ['meaning', 'Ticari anlamı'],
    ],
  ],
  [
    'section_notes',
    'Bölüm yorumları',
    [
      ['finding', 'Bulgu'],
      ['impact', 'Anlam / etki'],
      ['action', 'Aksiyon'],
    ],
  ],
  ['competition', 'Rekabet özeti', [['text', 'Karşılaştırılabilir gözlem']]],
  ['swot.strengths', 'SWOT: güçlü yönler', [['text', 'Madde']]],
  ['swot.weaknesses', 'SWOT: zayıf yönler', [['text', 'Madde']]],
  ['swot.opportunities', 'SWOT: fırsatlar', [['text', 'Madde']]],
  ['swot.threats', 'SWOT: tehditler', [['text', 'Madde']]],
  [
    'opportunities',
    'Fırsatlar',
    [
      ['text', 'Fırsat'],
      ['type', 'Tür'],
      ['contribution', 'Beklenen katkı'],
      ['priority_reason', 'Öncelik gerekçesi'],
    ],
  ],
  [
    'decisions',
    'Karar noktaları',
    [
      ['question', 'Karar sorusu'],
      ['needed_info', 'Beklenen bilgi'],
      ['owner_role', 'Karar sahibi (rol)'],
    ],
  ],
  [
    'actions',
    '90 günlük aksiyon planı',
    [
      ['action', 'Aksiyon'],
      ['kind', 'Tür', ['teknik', 'ölçümleme', 'seo', 'içerik', 'reklam']],
      ['channel', 'Yer', ['', 'website', 'meta', 'youtube', 'tiktok', 'linkedin', 'google_search']],
      ['period', 'Dönem (gün)', ['0-30', '30-60', '60-90']],
      ['priority', 'Öncelik', ['P1', 'P2', 'P3', 'P4']],
      ['priority_reason', 'Öncelik gerekçesi'],
      ['owner_role', 'Sorumlu rol'],
      ['effort', 'Tahmini iş yükü'],
      ['kpi', 'KPI'],
      ['dependency', 'Bağımlılık'],
    ],
  ],
]

const CHANNEL_LABEL: Record<string, string> = {
  '': 'yok',
  website: 'Web sitesi / blog',
  meta: 'Instagram / Facebook',
  youtube: 'YouTube',
  tiktok: 'TikTok',
  linkedin: 'LinkedIn',
  google_search: 'Google Arama Ağı (reklam)',
}

const VIEWS: ['executive' | 'detail' | 'implementation', string][] = [
  ['executive', 'Yönetici raporu'],
  ['detail', 'Detaylı analiz'],
  ['implementation', 'Uygulama belgesi'],
]

const listAt = (content: StrategyContent, path: string): StrategyItem[] => {
  const [head, sub] = path.split('.')
  return (
    (sub ? content.swot?.[sub] : (content[head as keyof StrategyContent] as StrategyItem[])) || []
  )
}

const withList = (
  content: StrategyContent,
  path: string,
  items: StrategyItem[]
): StrategyContent => {
  const [head, sub] = path.split('.')
  return sub
    ? { ...content, swot: { ...content.swot, [sub]: items } }
    : { ...content, [head]: items }
}

export default function DigitalStrategy(props: Props) {
  const { detail, workspaceId, running, busy, guarded, onDetail, reload } = props
  const record = detail.sections.strategy
  const state = detail.strategy_state || 'none'
  const saved = record?.status === 'ok' ? record.edited || record.draft : null
  const [content, setContent] = useState<StrategyContent | null>(saved)
  const [dirty, setDirty] = useState(false)
  const [approver, setApprover] = useState('')
  const [basis, setBasis] = useState<Record<number, string>>({})

  // Kayıt değişince (üretim, kaydetme, başka araştırma) yerel düzeltmeler sıfırlanır
  const stamp = `${detail.id}:${record?.version}:${JSON.stringify(saved)}`
  useEffect(() => {
    setContent(saved)
    setDirty(false)
    setBasis({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp])

  const synthesisReady = detail.sections.synthesis?.status === 'ok' && !detail.synthesis_stale
  const warned = content
    ? GROUPS.flatMap(([path]) => listAt(content, path)).filter((i) => (i.warnings || []).length > 0)
        .length
    : 0
  const editable = !!content && !!record && (state === 'draft' || state === 'approved') && !running
  const refText = (id: string) => record?.catalogue.find((c) => c.id === id)

  const change = (next: StrategyContent) => {
    setContent(next)
    setDirty(true)
  }
  const setField = (path: string, index: number, field: string, value: string) =>
    content &&
    change(
      withList(
        content,
        path,
        listAt(content, path).map((item, i) => (i === index ? { ...item, [field]: value } : item))
      )
    )
  const remove = (path: string, index: number) =>
    content &&
    change(
      withList(
        content,
        path,
        listAt(content, path).filter((_, i) => i !== index)
      )
    )
  // Bekletilen ücretli reklam: analist dayanak yazarsa plana alınır
  const release = (index: number) => {
    if (!content) return
    const held = content.decisions[index]?.held_action
    if (!held) return
    change({
      ...content,
      decisions: content.decisions.filter((_, i) => i !== index),
      actions: [...content.actions, { ...held, ad_status: 'önerilebilir', ad_basis: basis[index] }],
    })
  }

  const generate = () =>
    guarded('Strateji taslağı başlatılamadı', async () => {
      await digitalResearchApi.startStrategy(detail.id, workspaceId)
      await reload()
    })
  const save = () =>
    record &&
    content &&
    guarded('Düzeltmeler kaydedilemedi', async () => {
      const res = await digitalResearchApi.saveStrategy(
        detail.id,
        workspaceId,
        record.version,
        record.revision,
        content
      )
      onDetail(res.data)
    })
  const approve = () =>
    record &&
    guarded('İşaretlenemedi', async () => {
      const res = await digitalResearchApi.approveStrategy(
        detail.id,
        workspaceId,
        record.version,
        record.revision,
        approver.trim()
      )
      onDetail(res.data)
    })
  const download = (view: (typeof VIEWS)[number][0], format: 'docx' | 'pdf') =>
    guarded('Belge indirilemedi', async () => {
      // Belge araştırmanın kendi denetimine dayanır; başka denetim eklenmez
      const res = await digitalResearchApi.exportReport(workspaceId, {
        format,
        research_id: detail.id,
        view,
      })
      downloadBlobResponse(res, `${view}.${format}`)
    })

  return (
    <section className="glass-card audit-section">
      <h2>
        Karar odaklı rapor
        <span className="audit-chip audit-chip-neutral">{STATE_LABEL[state]}</span>
        {record?.version ? (
          <span className="audit-chip audit-chip-muted">v{record.version}</span>
        ) : null}
      </h2>
      <p className="audit-hint">
        Mevcut denetim, araştırma ve brif bilgisinden özet, bölüm yorumu, SWOT, karar noktaları ve
        90 günlük plan taslağı üretir. Yeni ölçüm yapmaz; bir ücretli yapay zekâ çağrısı kullanır.
        Dayanağı olmayan madde taslağa alınmaz.
      </p>
      {record?.status === 'failed' && <p className="audit-status-note">{record.error}</p>}
      {record?.last_error && (
        <p className="audit-status-note">
          Son yenileme başarısız oldu ({record.last_error}); önceki taslak korunuyor.
        </p>
      )}
      {state === 'stale' && (
        <p className="audit-status-note">
          Denetim bulguları, brif/beyan bilgileri, kabul edilen rakamlar ya da segmentler değişti.
          Bu taslak ve varsa analist onayı güncel değil; belgelere girmez.
        </p>
      )}
      {record?.approval && state === 'approved' && (
        <p className="audit-hint">
          {record.approval.approved_by},{' '}
          {new Date(record.approval.approved_at).toLocaleString('tr-TR')} tarihinde bu sürümü
          inceledi. Düzeltme ya da yenileme bu işareti kaldırır.
        </p>
      )}
      <div className="research-actions">
        <button
          className="btn btn-primary"
          onClick={generate}
          disabled={busy || running || !synthesisReady}
        >
          <Play size={18} />{' '}
          {record?.status === 'ok' ? 'Taslağı yeniden üret' : 'Strateji taslağı üret'}
        </button>
        {!synthesisReady && <span className="audit-hint">Önce güncel segmentler gerekli.</span>}
      </div>

      {editable && content && (
        <>
          {(content.dropped || 0) > 0 && (
            <p className="audit-hint">
              Dayanağı olmadığı için taslağa alınmayan madde: {content.dropped}.
            </p>
          )}
          {GROUPS.map(([path, title, fields]) => {
            const items = listAt(content, path)
            return (
              <div key={path} className="strategy-group">
                <h3>{title}</h3>
                {items.length === 0 && (
                  <p className="audit-hint">Yeterli kanıt yok; madde üretilmedi.</p>
                )}
                {items.map((item, index) => (
                  <div key={index} className="strategy-item">
                    {fields.map(([field, label, options]) =>
                      options ? (
                        <select
                          key={field}
                          className="input"
                          aria-label={`${title} ${index + 1} ${label}`}
                          value={(item[field] as string) || ''}
                          onChange={(e) => setField(path, index, field, e.target.value)}
                        >
                          {options.map((o) => (
                            <option key={o} value={o}>
                              {label}: {CHANNEL_LABEL[o] ?? o}
                            </option>
                          ))}
                        </select>
                      ) : (
                        // Çok satırlı: analist onayladığı cümlenin tamamını görür
                        <textarea
                          key={field}
                          className="input strategy-text"
                          rows={Math.min(
                            10,
                            Math.max(2, Math.ceil(String(item[field] || '').length / 26))
                          )}
                          aria-label={`${title} ${index + 1} ${label}`}
                          title={label}
                          placeholder={label}
                          value={(item[field] as string) || ''}
                          onChange={(e) => setField(path, index, field, e.target.value)}
                        />
                      )
                    )}
                    <div className="strategy-meta">
                      {item.refs.map((id) => (
                        <span
                          key={id}
                          className="audit-chip audit-chip-muted"
                          title={refText(id)?.text}
                        >
                          {id} · {refText(id)?.kind}
                        </span>
                      ))}
                      {item.class_note && (
                        <span className="audit-chip audit-chip-neutral">
                          Sınıflandırma notu: {String(item.class_note)}
                        </span>
                      )}
                      {item.publication === 'sponsorlu' && (
                        <span className="audit-chip audit-chip-warning">
                          Sponsorlu · {String(item.ad_status || '')}
                          {item.ad_basis ? ` · analist dayanağı: ${item.ad_basis}` : ''}
                        </span>
                      )}
                      {(item.warnings || []).length > 0 && (
                        <span className="audit-chip audit-chip-warning">
                          Dayanağı olmayan ifade: {(item.warnings || []).join('; ')}
                        </span>
                      )}
                      <button
                        className="btn btn-secondary"
                        aria-label={`${title} ${index + 1} çıkar`}
                        onClick={() => remove(path, index)}
                      >
                        <Trash2 size={14} /> Çıkar
                      </button>
                    </div>
                    {path === 'decisions' && item.held_action && (
                      <div className="strategy-held">
                        <p className="audit-status-note">
                          Bekletilen ücretli reklam ({String(item.ad_status || '')}):{' '}
                          {item.held_action.action as string}. Uygunluk kararı verilmeden plana
                          girmez.
                        </p>
                        <input
                          className="input"
                          aria-label={`Karar ${index + 1} analist dayanağı`}
                          placeholder="Analist kararı ve dayanağı (kaynak, tarih)"
                          value={basis[index] || ''}
                          onChange={(e) => setBasis((cur) => ({ ...cur, [index]: e.target.value }))}
                        />
                        <button
                          className="btn btn-secondary"
                          disabled={!(basis[index] || '').trim()}
                          onClick={() => release(index)}
                        >
                          Plana al
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          })}

          <div className="strategy-group">
            <h3>Açık eksikler ve veri ihtiyaçları</h3>
            <ul className="research-list">
              {record!.gaps.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          </div>

          <div className="research-actions">
            <button className="btn btn-secondary" onClick={save} disabled={busy || !dirty}>
              <Save size={18} /> Düzeltmeleri kaydet
            </button>
            <input
              className="input"
              aria-label="İnceleyen analist"
              placeholder="İnceleyen analistin adı"
              value={approver}
              onChange={(e) => setApprover(e.target.value)}
            />
            <button
              className="btn btn-secondary"
              onClick={approve}
              disabled={busy || dirty || warned > 0 || approver.trim().length < 2}
            >
              <CheckCircle2 size={18} /> İncelendi olarak işaretle
            </button>
          </div>
          {warned > 0 && (
            <p className="audit-status-note">
              Uyarısı duran {warned} madde var. Metni düzeltin ya da maddeyi çıkarın; uyarılar
              kapanmadan taslak incelendi olarak işaretlenemez.
            </p>
          )}
          <p className="audit-hint">
            İşaret yalnız bu sürümün incelendiğini gösterir; kontrol listesindeki bütün maddelerin
            tamamlandığı anlamına gelmez.
          </p>
        </>
      )}

      <h3>Belgeler</h3>
      <p className="audit-hint">
        Üç belge aynı kayıttan üretilir; indirmek yapay zekâyı yeniden çağırmaz. Onay yoksa belgenin
        künyesinde “taslak” yazar.
      </p>
      <div className="research-actions">
        {VIEWS.map(([view, label]) => (
          <span key={view} className="strategy-downloads">
            <button
              className="btn btn-secondary"
              onClick={() => download(view, 'docx')}
              disabled={busy || dirty}
            >
              <Download size={16} /> {label} (Word)
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => download(view, 'pdf')}
              disabled={busy || dirty}
            >
              <Download size={16} /> {label} (PDF)
            </button>
          </span>
        ))}
      </div>
    </section>
  )
}
