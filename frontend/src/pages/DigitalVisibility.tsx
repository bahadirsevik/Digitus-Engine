import { useEffect, useState } from 'react'
import { MapPin } from 'lucide-react'
import {
  apiErrorMessage,
  digitalAuditApi,
  type AuditVisibility,
  type LivePlace,
} from '../services/api'
import { rankText } from './digitalAuditView'

interface Props {
  auditId: number
  workspaceId: number
  visibility: AuditVisibility
}

function Unavailable({ section }: { section?: { status: string; reason?: string | null } }) {
  if (!section || section.status === 'ok') return null
  return (
    <p className="audit-hint">
      {section.status === 'unavailable' ? 'Veri yok' : 'Alınamadı'}: {section.reason}
    </p>
  )
}

export default function DigitalVisibility({ auditId, workspaceId, visibility }: Props) {
  const [live, setLive] = useState<Record<string, LivePlace> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { serp, youtube, places, sites } = visibility

  useEffect(() => {
    setLive(null)
    setError(null)
  }, [auditId])

  const loadLive = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await digitalAuditApi.livePlaces(auditId, workspaceId)
      setLive(res.data.profiles)
    } catch (err) {
      setError(`Profil bilgisi alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    } finally {
      setLoading(false)
    }
  }

  const channels = Object.entries(youtube?.channels || {})
  const profiles = Object.entries(places?.profiles || {})
  const matched = profiles.some(([, p]) => p.place_id)

  return (
    <>
      {serp && (
        <section className="glass-card audit-section">
          <h2>Arama görünürlüğü</h2>
          <Unavailable section={serp} />
          {serp.status === 'ok' && (
            <>
              <p className="audit-hint">
                Ölçüm: {new Date(visibility.measured_at).toLocaleString('tr-TR')} · konum{' '}
                {serp.location_used || visibility.location} · masaüstü · Google Türkiye. Anlık
                ölçümdür; sıra konuma, cihaza ve güne göre değişir. Kullanılan sorgu:{' '}
                {serp.queries_used} / {serp.query_limit}.
              </p>
              {serp.keywords.length > 0 && (
                <div className="audit-table-wrap">
                  <table className="audit-table">
                    <thead>
                      <tr>
                        <th>Kelime</th>
                        {sites.map((site) => (
                          <th key={site}>{site}</th>
                        ))}
                        <th>Reklamı görülen siteler</th>
                        <th>Harita kutusu</th>
                      </tr>
                    </thead>
                    <tbody>
                      {serp.keywords.map((row) => (
                        <tr key={row.keyword}>
                          <th scope="row">{row.keyword}</th>
                          {sites.map((site) => (
                            <td key={site}>
                              <span
                                className={`audit-chip audit-chip-${
                                  row.error ? 'muted' : row.positions[site] ? 'ok' : 'neutral'
                                }`}
                              >
                                {rankText(row, site)}
                              </span>
                            </td>
                          ))}
                          <td>
                            {row.error
                              ? 'Ölçülmedi'
                              : row.ads.join(', ') || 'Bu sorguda reklam görülmedi'}
                          </td>
                          <td>
                            {row.error ? 'Ölçülmedi' : row.local_pack ? 'Görüldü' : 'Görülmedi'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {serp.keywords.length > 0 && (
                <p className="audit-hint">
                  Reklamın görülmemesi kampanyanın kapalı olduğu anlamına gelmez.
                </p>
              )}
              {serp.brand &&
                (serp.brand.error ? (
                  <p className="audit-hint">
                    Marka araması ({serp.brand.query}): {serp.brand.error}
                  </p>
                ) : (
                  <>
                    <h3>Marka araması: {serp.brand.query}</h3>
                    <ul className="research-list">
                      <li>
                        Marka sitesinin sırası:{' '}
                        {serp.brand.own_position || 'ilk 10 sonuçta gözlenmedi'}
                      </li>
                      <li>Bilgi paneli: {serp.brand.knowledge_panel ? 'görüldü' : 'görülmedi'}</li>
                      <li>
                        Marka adında reklamı görülen başka siteler:{' '}
                        {serp.brand.other_ads?.join(', ') || 'görülmedi'}
                      </li>
                    </ul>
                  </>
                ))}
              {serp.reputation.map((entry) => (
                <div key={entry.query}>
                  <h3>Arama: {entry.query}</h3>
                  {entry.error ? (
                    <p className="audit-hint">{entry.error}</p>
                  ) : (
                    <ul className="research-list">
                      {entry.results.map((r) => (
                        <li key={r.position}>
                          {r.position}. {r.title} <small>· {r.domain || r.shown}</small>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
              {serp.reputation.length > 0 && (
                <p className="audit-hint">
                  İlk sonuç sayfasında çıkan başlıklardır; tüm internetin sayımı değildir ve
                  içerikleri okunmamıştır.
                </p>
              )}
            </>
          )}
        </section>
      )}

      {channels.length > 0 && (
        <section className="glass-card audit-section">
          <h2>YouTube</h2>
          <div className="audit-table-wrap">
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Site</th>
                  <th>Kanal</th>
                  <th>Abone</th>
                  <th>Video</th>
                  <th>Son yükleme</th>
                  <th>Son 90 gün</th>
                  <th>Son videolarda ort. izlenme</th>
                </tr>
              </thead>
              <tbody>
                {channels.map(([site, c]) => (
                  <tr key={site}>
                    <th scope="row">{site}</th>
                    {c.status === 'ok' ? (
                      <>
                        <td>{c.title}</td>
                        <td>
                          {c.subscribers == null ? 'gizli' : c.subscribers.toLocaleString('tr-TR')}
                        </td>
                        <td>{c.video_count}</td>
                        <td>{c.last_upload || '-'}</td>
                        <td>{c.uploads_90d} video</td>
                        <td>
                          {c.recent_avg_views != null
                            ? `${c.recent_avg_views.toLocaleString('tr-TR')} (${c.recent_sample} video)`
                            : '-'}
                        </td>
                      </>
                    ) : (
                      <td colSpan={6}>
                        <span className="audit-chip audit-chip-muted">
                          {c.status === 'not_observed' ? 'Gözlenmedi' : 'Bilinmiyor'}
                        </span>{' '}
                        {c.reason}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="audit-hint">
            Abone sayısı YouTube'un gösterdiği yuvarlanmış değerdir. Ortalama izlenme, son
            videoların izlenme ortalamasıdır.
          </p>
        </section>
      )}

      {places && (
        <section className="glass-card audit-section">
          <h2>Google İşletme Profili</h2>
          <Unavailable section={places} />
          {error && <p className="audit-status-note">{error}</p>}
          {profiles.length > 0 && (
            <div className="audit-table-wrap">
              <table className="audit-table">
                <thead>
                  <tr>
                    <th>Site</th>
                    <th>Profil</th>
                    <th>Güncel bilgi (Google Haritalar)</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map(([site, p]) => {
                    const info = live?.[site]
                    return (
                      <tr key={site}>
                        <th scope="row">{site}</th>
                        <td>
                          {p.maps_url ? (
                            <a href={p.maps_url} target="_blank" rel="noreferrer">
                              Haritada aç
                            </a>
                          ) : (
                            p.reason
                          )}
                        </td>
                        <td>
                          {!p.place_id && '-'}
                          {p.place_id && !info && 'Getirilmedi'}
                          {info && !info.available && `Alınamadı (${info.reason})`}
                          {info?.available && (
                            <>
                              <strong>{info.name}</strong>
                              <div className="audit-cell-summary">
                                {info.rating != null
                                  ? `${info.rating} puan · ${info.rating_count} yorum`
                                  : 'Puan yok'}
                                {info.category ? ` · ${info.category}` : ''}
                              </div>
                              <div className="audit-cell-note">
                                {[info.address, info.phone].filter(Boolean).join(' · ')}
                              </div>
                            </>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {matched && (
            <>
              <button className="btn btn-secondary" onClick={loadLive} disabled={loading}>
                <MapPin size={18} /> {live ? 'Bilgileri yenile' : 'Güncel profil bilgisini getir'}
              </button>
              <p className="audit-hint">
                Google'ın kullanım koşulları gereği puan, yorum sayısı ve adres saklanmaz, rapora
                girmez ve yapay zekâya gönderilmez; her seferinde Google'dan taze çekilir. Profil,
                web sitesi adresi eşleşen işletmedir; sahiplenilmiş olup olmadığı dışarıdan
                görülmez.
              </p>
            </>
          )}
        </section>
      )}
    </>
  )
}
