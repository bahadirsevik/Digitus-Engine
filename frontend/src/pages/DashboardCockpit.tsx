import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BadgeDollarSign,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Clock,
  Download,
  ExternalLink,
  Layers,
  RefreshCw,
  Sparkles,
  XCircle,
} from 'lucide-react'
import {
  apiErrorMessage,
  ChannelProgress,
  DashboardSummary,
  downloadBlobResponse,
  exportApi,
  PipelineStep,
  RunSummary,
  workspaceApi,
} from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import { toActiveWorkspace, WorkspaceListRow } from './brandProfileState'
import { jobTypeLabel } from '../components/exportLabels'

interface Props {
  summary: DashboardSummary
  loading: boolean
  onChangeRun: (runId: number) => void
  onRefresh: () => void
}

const PIPELINE_STATE_ICON: Record<PipelineStep['state'], JSX.Element> = {
  pending: <Circle size={14} />,
  in_progress: <Clock size={14} />,
  complete: <Check size={14} />,
  blocked: <AlertTriangle size={14} />,
  skipped: <ChevronRight size={14} />,
}

const CHANNEL_LABEL: Record<string, string> = {
  ADS: 'Google Ads',
  SEO: 'SEO',
  SOCIAL: 'Sosyal Medya',
}

const CHANNEL_TAB: Record<string, string> = {
  ADS: 'ads',
  SEO: 'seo',
  SOCIAL: 'social',
}

const STATUS_LABEL: Record<string, string> = {
  confirmed: 'Onaylandı',
  pending: 'Bekliyor',
  draft: 'Taslak',
  scored: 'Skorlandı',
  scoring: 'Skorlanıyor',
  failed: 'Hatalı',
  relevance_computing: 'İlgi skoru hesaplanıyor',
  relevance_computed: 'İlgi skoru hazır',
  channel_assigning: 'Kanal atanıyor',
  channel_assigned: 'Kanal atandı',
  completed: 'Tamamlandı',
  processing: 'Hazırlanıyor',
  running: 'Çalışıyor',
  ready: 'Hazır',
  partial: 'Eksik',
  empty: 'Boş',
  all: 'Tüm keywordler',
  top_n: 'En iyi keywordler',
  unknown: 'Kontrol ediliyor…',
}

function humanize(value: string | null | undefined): string {
  if (!value) return '-'
  return STATUS_LABEL[value] || value.replace(/_/g, ' ')
}

function RunRow({ run, active }: { run: RunSummary; active: boolean }) {
  const name = run.run_name || `Run #${run.id}`
  return (
    <option value={run.id}>
      {active ? 'Aktif: ' : ''}
      {name} - {humanize(run.status)}
    </option>
  )
}

function HealthChip({ label, status }: { label: string; status: string | null | undefined }) {
  const tone =
    status === 'ok'
      ? 'ok'
      : status === 'not_configured' || status === 'unknown'
        ? 'muted'
        : status
          ? 'warn'
          : 'muted'
  const text = status === 'ok' ? 'çalışıyor' : humanize(status)
  return (
    <span className={`status-chip status-chip-${tone}`}>
      {label} <strong>{text}</strong>
    </span>
  )
}

function channelStateLabel(channel: ChannelProgress): string {
  if (channel.status === 'complete') return 'Tamamlandı'
  if (channel.status === 'partial') return 'Eksik içerik var'
  if (channel.status === 'ready') return 'Üretime hazır'
  return 'Havuz boş'
}

function ChannelRow({
  name,
  channel,
  runId,
}: {
  name: string
  channel: ChannelProgress
  runId?: number
}) {
  const tab = CHANNEL_TAB[name] || name.toLowerCase()
  const expected = channel.expected_count || channel.pool_count
  const missing = Math.max(expected - channel.generated_count, 0)
  const path = runId ? `/generation?run_id=${runId}&tab=${tab}` : '/generation'

  return (
    <div className={`channel-row channel-${channel.status}`}>
      <div className="channel-copy">
        <strong>{CHANNEL_LABEL[name] || name}</strong>
        <span>{channelStateLabel(channel)}</span>
      </div>
      <div className="channel-progress">
        <strong>
          {channel.generated_count}/{expected}
        </strong>
        <span>{channel.pool_count} havuz</span>
        {missing > 0 && channel.pool_count > 0 && <small>{missing} eksik</small>}
      </div>
      <Link to={path} className="btn btn-secondary btn-sm">
        Aç
      </Link>
    </div>
  )
}

function WorkspaceSwitcher({ currentId }: { currentId?: number }) {
  const setActiveWorkspace = useBrandStore((s) => s.setActiveWorkspace)
  const [workspaces, setWorkspaces] = useState<WorkspaceListRow[]>([])

  useEffect(() => {
    workspaceApi
      .list()
      .then((res) => setWorkspaces((res.data as WorkspaceListRow[]) || []))
      .catch(() => {})
  }, [])

  // Değiştirilecek başka çalışma yoksa seçici gereksiz.
  const hasAlternative =
    workspaces.length > 1 || (workspaces.length === 1 && workspaces[0].id !== currentId)
  if (!hasAlternative) return null

  return (
    <div className="cockpit-workspace-switch">
      <label htmlFor="cockpit-workspace-select">
        <Layers size={14} />
        Aktif Çalışmayı Değiştir
      </label>
      <select
        id="cockpit-workspace-select"
        className="cockpit-workspace-select"
        value={currentId ?? ''}
        onChange={(e) => {
          const ws = workspaces.find((w) => w.id === Number(e.target.value))
          if (ws) setActiveWorkspace(toActiveWorkspace(ws))
        }}
        aria-label="Aktif çalışmayı değiştir"
      >
        {currentId === undefined && <option value="">Çalışma seç…</option>}
        {workspaces.map((ws) => (
          <option key={ws.id} value={ws.id}>
            {ws.name}
            {ws.status !== 'confirmed' ? ` (${STATUS_LABEL[ws.status] || ws.status})` : ''}
          </option>
        ))}
      </select>
    </div>
  )
}

export default function DashboardCockpit({ summary, loading, onChangeRun, onRefresh }: Props) {
  const { workspace, active_run, runs, next_action, channels, latest_export, health } = summary
  const ctaClass = `cockpit-main-action cta-${next_action.severity}`
  const activeTasks = summary.active_tasks.filter((task) => !task.is_blocking)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  // Plan v4: hazır raporu karttan doğrudan indir — sayfa gezinmesi yok
  const downloadExport = async (exportId: string) => {
    if (!workspace?.id) return
    setDownloadError(null)
    try {
      const res = await exportApi.download(exportId, workspace.id)
      downloadBlobResponse(res, latest_export?.file_name || 'digitus_rapor')
    } catch (err) {
      setDownloadError(apiErrorMessage(err, 'Rapor indirilemedi'))
    }
  }

  return (
    <div className="dashboard-page animate-fade-in">
      <header className="cockpit-topbar">
        <div className="cockpit-workspace">
          <span>Workspace</span>
          <h1>{workspace?.name || 'Ana Panel'}</h1>
          {workspace?.company_url && <p>{workspace.company_url}</p>}
          <div className="cockpit-workspace-actions">
            <WorkspaceSwitcher currentId={workspace?.id} />
            {workspace?.id && (
              <Link
                to={`/brand-profile?workspace_id=${workspace.id}`}
                className="btn btn-secondary btn-sm"
              >
                Marka Profilini Duzenle
              </Link>
            )}
          </div>
        </div>

        <div className="cockpit-run-picker">
          <label htmlFor="cockpit-run-selector">Aktif run</label>
          {runs.length > 0 && active_run ? (
            <select
              id="cockpit-run-selector"
              className="input cockpit-run-select"
              value={active_run.id}
              onChange={(e) => onChangeRun(Number(e.target.value))}
            >
              {runs.map((r) => (
                <RunRow key={r.id} run={r} active={r.id === active_run.id} />
              ))}
            </select>
          ) : (
            <span className="cockpit-run-empty">Henüz run yok.</span>
          )}
        </div>

        <div className="cockpit-header-actions">
          <HealthChip label="API" status={health.api} />
          <HealthChip label="Google Ads" status={health.google_ads} />
          {summary.blocking_task && (
            <span className="status-chip status-chip-busy">
              Kritik işlem <strong>%{summary.blocking_task.progress}</strong>
            </span>
          )}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onRefresh}
            disabled={loading}
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            Yenile
          </button>
        </div>
      </header>

      <section className={ctaClass}>
        <div className="action-copy">
          <span className="action-eyebrow">Sıradaki adım</span>
          <h2>{next_action.label}</h2>
          <p>{next_action.reason}</p>
        </div>
        {next_action.export_id ? (
          <button
            type="button"
            className="btn btn-primary btn-lg"
            onClick={() => void downloadExport(next_action.export_id as string)}
          >
            <Download size={16} />
            İndir
          </button>
        ) : (
          <Link to={next_action.path} className="btn btn-primary btn-lg">
            Devam et
            <ArrowRight size={16} />
          </Link>
        )}
      </section>

      <section className="cockpit-timeline" aria-label="Akış durumu">
        {summary.pipeline.map((step, idx) => (
          <Link key={step.key} to={step.path} className={`timeline-step state-${step.state}`}>
            <div className="step-icon">{PIPELINE_STATE_ICON[step.state]}</div>
            <div className="step-body">
              <small>{idx + 1}. adım</small>
              <strong>{step.label}</strong>
              <span>{humanize(step.detail)}</span>
            </div>
          </Link>
        ))}
      </section>

      <section className="cockpit-shell">
        <div className="cockpit-main-column">
          <section className="cockpit-panel content-focus-panel">
            <header className="panel-heading">
              <Sparkles size={16} />
              <h3>İçerik durumu</h3>
            </header>
            <div className="channel-rows">
              <ChannelRow name="ADS" channel={channels.ADS} runId={active_run?.id} />
              <ChannelRow name="SEO" channel={channels.SEO} runId={active_run?.id} />
              <ChannelRow name="SOCIAL" channel={channels.SOCIAL} runId={active_run?.id} />
            </div>
          </section>
        </div>

        <aside className="cockpit-side-column">
          <section className="cockpit-panel">
            <header className="panel-heading">
              <Layers size={16} />
              <h3>Run detayı</h3>
            </header>
            {active_run ? (
              <ul className="kv-list">
                <li>
                  <span>ID</span>
                  <strong>#{active_run.id}</strong>
                </li>
                <li>
                  <span>Durum</span>
                  <strong>{humanize(active_run.status)}</strong>
                </li>
                <li>
                  <span>Seçim modu</span>
                  <strong>{humanize(active_run.keyword_selection_mode || 'all')}</strong>
                </li>
                <li>
                  <span>Kapasite</span>
                  <strong>
                    Ads {active_run.ads_capacity} / SEO {active_run.seo_capacity} / Sosyal{' '}
                    {active_run.social_capacity}
                  </strong>
                </li>
                <li>
                  <span>İlgi skoru</span>
                  <strong>{active_run.skip_relevance ? 'Atlandı' : 'Aktif'}</strong>
                </li>
              </ul>
            ) : (
              <p className="panel-empty">Henüz run yok.</p>
            )}
          </section>

          <section className="cockpit-panel">
            <header className="panel-heading">
              <Activity size={16} />
              <h3>Aktif işler</h3>
            </header>
            {summary.blocking_task ? (
              <p className="panel-empty">
                Kritik işlem sürüyor: {humanize(summary.blocking_task.task_type)} %
                {summary.blocking_task.progress}
              </p>
            ) : activeTasks.length > 0 ? (
              <div className="task-list">
                {activeTasks.slice(0, 3).map((task) => (
                  <span key={task.task_id}>
                    {humanize(task.task_type || 'task')} - %{task.progress}
                  </span>
                ))}
              </div>
            ) : (
              <p className="panel-empty">Çalışan işlem yok.</p>
            )}
          </section>

          <section className="cockpit-panel">
            <header className="panel-heading">
              <Download size={16} />
              <h3>Dışa aktarım</h3>
            </header>
            {latest_export ? (
              <div className="export-summary">
                <div className={`export-status export-${latest_export.status}`}>
                  {latest_export.status === 'completed' ? (
                    <CheckCircle2 size={16} />
                  ) : latest_export.status === 'failed' ? (
                    <XCircle size={16} />
                  ) : (
                    <Clock size={16} />
                  )}
                  <span>{humanize(latest_export.status)}</span>
                </div>
                <div className="export-meta">
                  <span>Tür</span>
                  <strong>{jobTypeLabel(latest_export)}</strong>
                  {latest_export.policy_outdated && (
                    <span
                      className="export-outdated-badge"
                      title="Bu rapor üretiminden sonra politika/profil değişti — güncel rapor için yeniden export alın"
                    >
                      eski politika
                    </span>
                  )}
                </div>
                {latest_export.status === 'completed' ? (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => void downloadExport(latest_export.export_id)}
                  >
                    <Download size={14} />
                    İndir
                  </button>
                ) : (
                  <Link
                    className="btn btn-secondary btn-sm"
                    to={`/seo-geo${active_run ? `?run_id=${active_run.id}` : ''}`}
                  >
                    SEO+GEO sayfasının İndir menüsü
                    <ArrowRight size={14} />
                  </Link>
                )}
                {downloadError && <p className="panel-error-text">{downloadError}</p>}
              </div>
            ) : (
              <p className="panel-empty">Henüz dışa aktarım yok.</p>
            )}
          </section>

          <section
            className={`cockpit-panel ${
              health.google_ads === 'error' ? 'cockpit-panel-warning' : ''
            }`}
          >
            <header className="panel-heading">
              <BadgeDollarSign size={16} />
              <h3>Google Ads</h3>
            </header>
            <p className="panel-empty">
              Durum:{' '}
              <strong>
                {health.google_ads === 'ok' ? 'çalışıyor' : humanize(health.google_ads)}
              </strong>
            </p>
            {health.google_ads_error && (
              <p className="panel-error-text">{health.google_ads_error}</p>
            )}
            <Link to="/google-ads" className="btn btn-secondary btn-sm">
              <ExternalLink size={14} /> Explorer
            </Link>
          </section>
        </aside>
      </section>
    </div>
  )
}
