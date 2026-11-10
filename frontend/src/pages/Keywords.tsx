import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Plus,
  Upload,
  Trash2,
  Search,
  RefreshCw,
  Download,
  FileSpreadsheet,
  FileUp,
  CheckCircle,
  AlertCircle,
  Keyboard,
  Link2,
  Megaphone,
  ArrowRight,
  BarChart3,
  ChevronDown,
  List,
} from 'lucide-react'
import {
  apiErrorMessage,
  apiErrorStatus,
  brandProfileApi,
  downloadBlobResponse,
  keywordsApi,
  scoringApi,
  tasksApi,
  tasksFromListResponse,
  KeywordCreate,
  EnrichedKeywordOut,
  KeywordUploadCsvResponse,
  KeywordImportResponse,
  SkippedKeywordDetail,
} from '../services/api'
import { useBrandStore } from '../stores/brandStore'
import MarketBadge from '../components/MarketBadge'
import type { FetchedMarket } from '../services/market'
import GoogleAdsKeywordSearch from '../components/GoogleAdsKeywordSearch'
import UrlKeywordExtractor from '../components/UrlKeywordExtractor'
import KeywordImportResult from '../components/KeywordImportResult'
import StartAnalysisModal from '../components/StartAnalysisModal'
import { runResultBadge } from '../components/startAnalysis'
import ScoreResults from '../components/ScoreResults'
import { AnalysisBanner, ChainStage } from '../components/AnalysisProgress'
import { UrlSeedIdea } from '../services/api'
import { WORKSPACE_KEYWORD_LIMIT } from '../constants'
import { Users, X } from 'lucide-react'
import type { ScoringRun } from '../types/models'
import {
  getWorkspaceTaskKey,
  MAX_POLL_FAILURES,
  POLL_FAILED_MESSAGE,
  POLL_RECHECK_LABEL,
  useScopedTaskId,
  useTaskPolling,
} from '../hooks/useTaskPolling'
import ErrorBanner from '../components/ErrorBanner'
import TablePager from '../components/TablePager'
import { runStatusLabel } from '../components/statusLabels'
import { useAnalysisToastStore } from '../stores/analysisToastStore'
import './Keywords.css'

type TabId = 'csv' | 'manual' | 'google-ads' | 'url' | 'competitor'
type KeywordView = 'keywords' | 'scores'

const TABS: {
  id: TabId
  label: string
  description: string
  icon: typeof Megaphone
}[] = [
  {
    id: 'google-ads',
    label: 'Başlangıç Kelimeleri',
    description: 'Profildeki 10 kelime hazır gelir; Google Ads ile hacim ve rekabet verisi al',
    icon: Megaphone,
  },
  {
    id: 'url',
    label: "URL'den Çıkar",
    description: "Kendi siteniz ya da herhangi bir URL'den anahtar kelime önerileri üret",
    icon: Link2,
  },
  {
    id: 'competitor',
    label: "Rakip URL'leri",
    description:
      'Profildeki rakip siteler hazır buton olarak; rakiplerin anahtar kelimelerini keşfet',
    icon: Users,
  },
  {
    id: 'manual',
    label: 'Elle Ekle',
    description: 'Tekil kelimeyi hızlıca marka çalışmasına bağla',
    icon: Keyboard,
  },
  {
    id: 'csv',
    label: 'CSV Yükle',
    description: 'Hazır anahtar kelime listesini toplu içe aktar',
    icon: FileSpreadsheet,
  },
]

const SOURCE_BADGES: Record<string, { label: string; className: string }> = {
  csv: { label: 'CSV', className: 'source-badge source-csv' },
  google_ads_api: { label: 'Google Ads', className: 'source-badge source-gads' },
  url_seed: { label: 'URL', className: 'source-badge source-url' },
  manual: { label: 'Elle eklendi', className: 'source-badge source-manual' },
}

interface Keyword {
  id: number
  wk_id?: number
  keyword: string
  sector?: string
  target_market?: string
  monthly_volume?: number
  trend_12m?: number
  trend_3m?: number
  competition_score?: number
  wk_monthly_volume?: number
  wk_trend_12m?: number
  wk_trend_3m?: number
  wk_competition_score?: number
  wk_data_source?: string
  is_active: boolean
  data_source?: string
}

interface RunTask {
  task_id: string
  task_type?: string | null
  scoring_run_id?: number | null
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress?: number
  result_data?: Record<string, unknown> | null
  error_message?: string | null
}

const isActiveTask = (task?: RunTask | null) =>
  task?.status === 'pending' || task?.status === 'running'

// Renkli trend hücresi: pozitif yeşil, negatif kırmızı (tasarım)
function TrendCell({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="kwx-muted">—</span>
  const num = Number(value)
  return (
    <span className={num >= 0 ? 'kwx-trend-up' : 'kwx-trend-down'}>
      {num > 0 ? '+' : ''}
      {num.toFixed(2)}%
    </span>
  )
}

// Rekabet hücresi: mini bar + değer; <0.1 yeşil, <0.3 amber, üstü kırmızı
function CompCell({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="kwx-muted">—</span>
  const num = Number(value)
  const color = num < 0.1 ? '#4ade80' : num < 0.3 ? '#f0b429' : '#f87171'
  return (
    <span className="kwx-comp">
      <span className="kwx-comp-bar">
        <span
          className="kwx-comp-fill"
          style={{ width: `${Math.max(num * 100, 4)}%`, background: color }}
        />
      </span>
      <span className="kwx-comp-val">{num.toFixed(2)}</span>
    </span>
  )
}

// "!" bilgi ipucu — kaynak açıklaması hover'da görünür (tasarım)
function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <span
      className="kwx-infotip"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onClick={(e) => e.stopPropagation()}
    >
      <span className="kwx-infotip-dot">!</span>
      {open && <span className="kwx-infotip-panel">{text}</span>}
    </span>
  )
}

const formatKeywordImportStatus = (result: {
  created: number
  skipped: number
  requested: number
  total_before: number
  total_after: number
  created_new?: number
  linked_existing?: number
  fuzzy_merged_in_batch?: number
}) => {
  const details = [
    `${result.created} eklendi`,
    `${result.skipped} atlandı`,
    `toplam ${result.total_before} → ${result.total_after}`,
  ]

  if ((result.linked_existing || 0) > 0) {
    details.splice(
      1,
      0,
      `${result.created_new || 0} yeni kayıt, ${result.linked_existing || 0} mevcut kelime bağlandı`
    )
  }

  return `✓ ${result.requested} satır işlendi: ${details.join(' · ')}`
}

const KEYWORD_PAGE_SIZE = 50

export default function Keywords() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace)
  const hasBrandSuggestedKeywords =
    (activeWorkspace?.suggested_keywords?.filter((kw) => kw.trim()).length || 0) > 0
  const getDefaultTab = (): TabId => (hasBrandSuggestedKeywords ? 'google-ads' : 'url')

  const initialTab = (searchParams.get('tab') as TabId) || getDefaultTab()
  const [activeTab, setActiveTab] = useState<TabId>(
    TABS.some((t) => t.id === initialTab) ? initialTab : getDefaultTab()
  )

  const [keywords, setKeywords] = useState<Keyword[]>([])
  const [poolTotal, setPoolTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [keywordPage, setKeywordPage] = useState(0)
  const [competitorUrl, setCompetitorUrl] = useState<string | null>(null)
  // Google ile bulunup onaylanan rakipler politika kayıtlarında (competitor_terms) durur;
  // profildeki elle girilen rakip URL'leri (competitor_urls) ile birlikte gösterilir.
  const [discoveredCompetitorUrls, setDiscoveredCompetitorUrls] = useState<string[]>([])
  const [discoveredCompetitorsError, setDiscoveredCompetitorsError] = useState<string | null>(null)
  const [showStartModal, setShowStartModal] = useState(false)
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  const [manualImportResult, setManualImportResult] = useState<
    { kind: 'success'; data: KeywordImportResponse } | { kind: 'error'; message: string } | null
  >(null)
  // Tasarımdaki kalıcı yeşil import özeti — hangi kaynaktan gelirse gelsin son import
  const [lastImport, setLastImport] = useState<KeywordImportResponse | null>(null)
  const [csvDryRun, setCsvDryRun] = useState<KeywordUploadCsvResponse | null>(null)
  const [csvUploading, setCsvUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const initialView = searchParams.get('view') === 'scores' ? 'scores' : 'keywords'
  const initialRunId = Number(searchParams.get('run_id') || '')
  const [view, setView] = useState<KeywordView>(initialView)
  const [scoreRuns, setScoreRuns] = useState<ScoringRun[]>([])
  const [selectedScoreRunId, setSelectedScoreRunId] = useState<number | null>(
    Number.isNaN(initialRunId) || initialRunId <= 0 ? null : initialRunId
  )
  const [scoreRunsLoading, setScoreRunsLoading] = useState(false)
  // Analiz listesi yüklenemedi (boş liste ile karışmasın) ve arka plan durum yoklama uyarısı
  const [scoreRunsError, setScoreRunsError] = useState<string | null>(null)
  const [statusNotice, setStatusNotice] = useState<string | null>(null)
  const workspaceIdRef = useRef(activeWorkspace?.id)
  workspaceIdRef.current = activeWorkspace?.id
  const [taskDiscoveryActive, setTaskDiscoveryActive] = useState(false)
  // Analiz zinciri banner/toast durumu.
  const [chainActive, setChainActive] = useState(false)
  const [chainRunId, setChainRunId] = useState<number | null>(null)
  const [chainDismissed, setChainDismissed] = useState(false)
  const [chainRunStatus, setChainRunStatus] = useState<string | null>(null)
  const assignTaskStorageKey = getWorkspaceTaskKey(
    'channel_assign',
    activeWorkspace?.id,
    selectedScoreRunId
  )
  const [assignTaskId, setAssignTaskId] = useScopedTaskId(assignTaskStorageKey)
  const assignPolling = useTaskPolling(
    assignTaskId,
    assignTaskStorageKey,
    3000,
    activeWorkspace?.id,
    selectedScoreRunId
  )

  const [sourceCollapsed, setSourceCollapsed] = useState(false)
  const [newKeyword, setNewKeyword] = useState<KeywordCreate>({
    keyword: '',
    sector: '',
    monthly_volume: 1000,
    trend_12m: 10,
    trend_3m: 15,
    competition_score: 0.5,
    data_source: 'manual',
  })

  useEffect(() => {
    fetchKeywords()
    setLastImport(null)
    setKeywordPage(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id])

  useEffect(() => {
    setDiscoveredCompetitorUrls([])
    setDiscoveredCompetitorsError(null)
    setCompetitorUrl(null)
    const workspaceId = activeWorkspace?.id
    if (!workspaceId || activeTab !== 'competitor') return
    let cancelled = false
    brandProfileApi
      .getPolicy(workspaceId)
      .then((res) => {
        if (cancelled) return
        const urls = (res.data.competitor_terms || [])
          .filter((t) => t.status === 'approved' && t.discovery_id && t.candidate_url)
          .map((t) => t.candidate_url as string)
        setDiscoveredCompetitorUrls(urls)
      })
      .catch((err) => {
        if (!cancelled)
          setDiscoveredCompetitorsError(
            `Google ile bulunan rakipler yüklenemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`
          )
      })
    return () => {
      cancelled = true
    }
  }, [activeWorkspace?.id, activeTab])

  useEffect(() => {
    if (view === 'scores') {
      void fetchScoreRuns()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id, view])

  useEffect(() => {
    const viewFromUrl = searchParams.get('view') === 'scores' ? 'scores' : 'keywords'
    setView(viewFromUrl)
    if (viewFromUrl === 'scores') {
      const parsedRunId = Number(searchParams.get('run_id') || '')
      setSelectedScoreRunId(Number.isNaN(parsedRunId) || parsedRunId <= 0 ? null : parsedRunId)
    }
  }, [searchParams])

  // Run/workspace değişince önceki bağlamın uyarıları taşınmaz
  useEffect(() => {
    setStatusNotice(null)
  }, [selectedScoreRunId, activeWorkspace?.id])

  useEffect(() => {
    if (!selectedScoreRunId || view !== 'scores' || !activeWorkspace?.id) {
      setTaskDiscoveryActive(false)
      return
    }

    let cancelled = false
    // Üst üste biten istekler çakışmasın: önceki yanıt gelmeden yeni yoklama başlamaz
    let inFlight = false
    let failures = 0
    const repeating = taskDiscoveryActive && !assignTaskId
    const findTask = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const task = await discoverChannelAssignmentTask(selectedScoreRunId)
        if (cancelled) return
        failures = 0
        setStatusNotice(null)
        if (!task) return
        setAssignTaskId(task.task_id)
        if (!isActiveTask(task)) {
          setTaskDiscoveryActive(false)
        }
      } catch {
        if (cancelled) return
        // Tek seferlik arama hemen, tekrarlayan yoklama 3 ardışık hatadan sonra bildirir
        failures += 1
        if (failures >= (repeating ? MAX_POLL_FAILURES : 1)) {
          setTaskDiscoveryActive(false)
          setStatusNotice(
            'Kanal atama durumu alınamadı; görev çalışıyor olabilir. Bağlantıyı kontrol edip durumu tekrar kontrol edin.'
          )
        }
      } finally {
        inFlight = false
      }
    }

    void findTask()
    // Cleanup HER durumda döner: run değişince uçuştaki keşif yanıtı yok sayılır
    const interval = repeating ? window.setInterval(findTask, 3000) : null
    return () => {
      cancelled = true
      if (interval !== null) window.clearInterval(interval)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedScoreRunId, view, activeWorkspace?.id, taskDiscoveryActive, assignTaskId])

  useEffect(() => {
    if (assignPolling.status) {
      setTaskDiscoveryActive(assignPolling.isActive)
    }
    if (assignPolling.isCompleted) {
      void fetchScoreRuns()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assignPolling.status?.status, assignPolling.isCompleted])

  useEffect(() => {
    const tabFromUrl = searchParams.get('tab') as TabId | null
    if (tabFromUrl && TABS.some((tab) => tab.id === tabFromUrl)) {
      setActiveTab(tabFromUrl)
      return
    }
    setActiveTab(getDefaultTab())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspace?.id, activeWorkspace?.suggested_keywords, searchParams])

  const switchTab = (tab: TabId) => {
    setActiveTab(tab)
    setSearchParams({ tab })
  }

  const fetchKeywords = async () => {
    if (!activeWorkspace) {
      setKeywords([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await keywordsApi.list({
        limit: 2000,
        brand_profile_id: activeWorkspace.id,
      })
      setKeywords(res.data.items || [])
      setPoolTotal(res.data.total ?? (res.data.items || []).length)
    } catch (err) {
      setError(
        apiErrorStatus(err) === 0
          ? 'Sunucuya ulaşılamadı; bağlantınızı kontrol edip sayfayı yenileyin.'
          : `Anahtar kelime listesi yüklenemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`
      )
      setKeywords([])
      setPoolTotal(0)
    }
    setLoading(false)
  }

  const fetchScoreRuns = async () => {
    if (!activeWorkspace?.id) {
      setScoreRuns([])
      setSelectedScoreRunId(null)
      return
    }
    const requestWorkspaceId = activeWorkspace.id
    setScoreRunsLoading(true)
    try {
      const res = await scoringApi.listRuns({ brand_profile_id: requestWorkspaceId })
      if (workspaceIdRef.current !== requestWorkspaceId) return
      const runs = (Array.isArray(res.data) ? res.data : []) as ScoringRun[]
      setScoreRuns(runs)
      setScoreRunsError(null)
      if (!selectedScoreRunId && runs.length > 0) {
        setSelectedScoreRunId(runs[0].id)
      }
    } catch (err) {
      if (workspaceIdRef.current !== requestWorkspaceId) return
      setScoreRuns([])
      setScoreRunsError(`Analiz listesi yüklenemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    } finally {
      setScoreRunsLoading(false)
    }
  }

  const setKeywordView = (nextView: KeywordView, runId?: number | null) => {
    setView(nextView)
    const params: Record<string, string> = {}
    if (nextView === 'scores') {
      params.view = 'scores'
      if (runId) params.run_id = String(runId)
    } else if (activeTab) {
      params.tab = activeTab
    }
    setSearchParams(params)
  }

  const selectScoreRun = (runId: number | null) => {
    setSelectedScoreRunId(runId)
    setKeywordView('scores', runId)
    setAssignTaskId(null)
    setTaskDiscoveryActive(Boolean(runId))
  }

  // Zincir aktifken (henüz task görünmeden) relevance aşamasını run status'tan izle
  useEffect(() => {
    if (!chainActive || !selectedScoreRunId || !activeWorkspace?.id) return
    if (assignTaskId && assignPolling.isActive) return // task varken status'a gerek yok
    let cancelled = false
    let inFlight = false
    let failures = 0
    const tick = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const res = await scoringApi.getRun(selectedScoreRunId, activeWorkspace.id)
        if (cancelled) return
        failures = 0
        setStatusNotice(null)
        setChainRunStatus((res.data as ScoringRun).status)
      } catch {
        // Her başarısız yoklamada uyarı basılmaz; ardışık hatadan sonra TEK uyarı
        if (cancelled) return
        failures += 1
        if (failures === MAX_POLL_FAILURES) {
          setStatusNotice(
            'Analiz durumu şu an alınamıyor; yoklama sürüyor, bağlantı düzelince ekran güncellenir.'
          )
        }
      } finally {
        inFlight = false
      }
    }
    void tick()
    const interval = window.setInterval(tick, 3000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [chainActive, selectedScoreRunId, activeWorkspace?.id, assignTaskId, assignPolling.isActive])

  // Gerçek zincir aşaması (V3): hata (task veya run, task bulunmadan da) > bitti > çalışıyor.
  const chainStage: ChainStage | null = (() => {
    if (!chainActive) return null
    const taskStatus = assignPolling.status?.status
    if (taskStatus === 'failed' || chainRunStatus === 'failed') return 'failed'
    if (taskStatus === 'completed' || chainRunStatus === 'channel_assigned') return 'done'
    return 'assigning'
  })()

  // Seçili run'ın açık kanalları (bilinmiyorsa hepsi açık sayılır)
  const chainRun = scoreRuns.find((run) => run.id === selectedScoreRunId)
  const chainChannels = {
    ads: chainRun?.enable_ads,
    seo: chainRun?.enable_seo,
    social: chainRun?.enable_social,
  }
  // Global toast için çalışan/başlatılan görevi paylaşılan store'a yaz
  const trackAnalysisTask = useAnalysisToastStore((st) => st.track)
  useEffect(() => {
    if (!assignTaskId || !selectedScoreRunId || !activeWorkspace?.id) return
    // Bitmiş görev yalnız analizin başlatıldığı run'da izlenir: çalışan A sürerken
    // listeden seçilen tamamlanmış B run'ı takibi devralmasın
    if (!assignPolling.isActive && !(chainActive && selectedScoreRunId === chainRunId)) return
    trackAnalysisTask({
      taskId: assignTaskId,
      runId: selectedScoreRunId,
      workspaceId: activeWorkspace.id,
      channels: chainChannels,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    assignTaskId,
    selectedScoreRunId,
    activeWorkspace?.id,
    assignPolling.isActive,
    chainActive,
    chainRunId,
    chainRun?.enable_ads,
    chainRun?.enable_seo,
    chainRun?.enable_social,
  ])
  const chainMessage =
    typeof assignPolling.resultData?.current_message === 'string'
      ? assignPolling.resultData.current_message
      : null

  const dismissChain = () => {
    setChainDismissed(true)
    setChainActive(false)
  }

  const discoverChannelAssignmentTask = async (runId: number) => {
    if (!activeWorkspace?.id) return null
    const res = await tasksApi.listByRun(runId, activeWorkspace.id)
    const tasks = tasksFromListResponse<RunTask>(res.data)
    const channelTasks = tasks
      .filter((task) => task.task_type === 'channel_assignment')
      .sort((a, b) => String(b.task_id).localeCompare(String(a.task_id)))
    const activeTask = channelTasks.find(isActiveTask)
    return activeTask || channelTasks[0] || null
  }

  // "Geri al": yasaklı temayla elenen satırı force_include ile yeniden import et.
  // Metrikler response'taki skipped_details satırından gelir (payload persist edilmez).
  const handleForceInclude = async (detail: SkippedKeywordDetail) => {
    if (!activeWorkspace) return
    const row: KeywordCreate = {
      keyword: detail.keyword,
      monthly_volume: detail.monthly_volume ?? 0,
      trend_3m: detail.trend_3m ?? 0,
      trend_12m: detail.trend_12m ?? 0,
      competition_score: detail.competition_score ?? 0.5,
      data_source: detail.data_source || 'manual',
      // Pazar alanları yoksa atlanır; sunucu çalışma pazarını damgalar
      geo_target_id: detail.geo_target_id || undefined,
      language_id: detail.language_id || undefined,
      force_include: true,
    }
    try {
      await keywordsApi.import([row], activeWorkspace.id)
    } catch (err) {
      setError(`"${detail.keyword}" geri alınamadı: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
      // Çağıranlar "eklendi" işaretini yalnız başarıda koysun diye hata yeniden fırlatılır
      throw err
    }
    fetchKeywords()
  }

  const handleCreate = async () => {
    if (!newKeyword.keyword.trim()) return
    if (!activeWorkspace) {
      setError('Önce bir marka çalışması seçin')
      return
    }
    try {
      const row: KeywordCreate = {
        ...newKeyword,
        data_source: 'manual',
      }
      const res = await keywordsApi.import([row], activeWorkspace.id)
      setManualImportResult({ kind: 'success', data: res.data })
      setLastImport(res.data)
      setNewKeyword({
        keyword: '',
        sector: '',
        monthly_volume: 1000,
        trend_12m: 10,
        trend_3m: 15,
        competition_score: 0.5,
        data_source: 'manual',
      })
      fetchKeywords()
    } catch (err: unknown) {
      const e = err as { message?: string; detail?: unknown }
      setManualImportResult({
        kind: 'error',
        message: e?.message || String(e?.detail || 'Kelime eklenemedi'),
      })
    }
  }

  const handleDelete = async (wk_id: number) => {
    try {
      await keywordsApi.delete(wk_id, activeWorkspace?.id)
      fetchKeywords()
    } catch (err) {
      // Satır silinemedi: listede KALIR, gerçek hata gösterilir
      setError(`Kelime silinemedi, listede korundu: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    }
  }

  const handleDeleteAll = async () => {
    if (!activeWorkspace) {
      setError('Önce bir marka çalışması seçin')
      return
    }
    if (!confirm('Bu çalışmadaki TÜM anahtar kelimelerin bağlantısı kaldırılacak. Emin misiniz?'))
      return
    try {
      await keywordsApi.deleteAll(activeWorkspace.id)
      fetchKeywords()
    } catch (err) {
      setError(`Kelimeler silinemedi: ${apiErrorMessage(err, 'bilinmeyen hata')}`)
    }
  }

  // Plan v4 (export dağıtımı): aktif kelime havuzunu Excel indir
  const handlePoolExport = async () => {
    if (!activeWorkspace) {
      setError('Önce bir marka çalışması seçin')
      return
    }
    try {
      const res = await keywordsApi.exportPoolXlsx(activeWorkspace.id)
      downloadBlobResponse(res, `kelime_havuzu_ws${activeWorkspace.id}.xlsx`)
    } catch (err) {
      setError(apiErrorMessage(err, 'Kelime havuzu indirilemedi'))
    }
  }

  // Seçili koşunun kimliği ADINDAN ve rozetinden anlaşılmalı: "mevcut SOCIAL"
  // baseline'ı v2.1 sonuçlarıyla karıştırılamaz.
  const selectedRunBadge = runResultBadge(
    scoreRuns.find((run) => run.id === selectedScoreRunId) || {}
  )

  // Plan v4 (export dağıtımı): seçili analizin skorlanan kelimelerini Excel indir
  const handleScoresExport = async () => {
    if (!activeWorkspace || !selectedScoreRunId) return
    try {
      const res = await scoringApi.exportXlsx(selectedScoreRunId, activeWorkspace.id)
      downloadBlobResponse(res, `scoring_run_${selectedScoreRunId}.xlsx`)
    } catch (err) {
      setError(apiErrorMessage(err, 'Skorlar indirilemedi'))
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setUploadedFile(e.target.files[0])
      setUploadStatus(null)
      setCsvDryRun(null)
    }
  }

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setUploadedFile(e.dataTransfer.files[0])
      setUploadStatus(null)
      setCsvDryRun(null)
    }
  }

  const handleUpload = async () => {
    if (!uploadedFile) return
    if (!activeWorkspace) {
      setUploadStatus('Önce bir marka çalışması seçin')
      return
    }
    setUploadStatus('Analiz ediliyor...')
    setCsvUploading(true)
    try {
      const res = await keywordsApi.uploadCsv(uploadedFile, activeWorkspace.id)
      setCsvDryRun(res.data)
      setUploadStatus(
        `✓ ${res.data.parsed} satır analiz edildi: ${res.data.accepted} eklenecek, ${res.data.skipped} atlanacak`
      )
    } catch (err: unknown) {
      const e = err as { message?: string; detail?: unknown }
      setUploadStatus(`⚠ Hata: ${e?.message || String(e?.detail || 'Bilinmeyen hata')}`)
    } finally {
      setCsvUploading(false)
    }
  }

  const handleCommitCsvUpload = async () => {
    if (!csvDryRun?.dry_run_token || !activeWorkspace) return
    setUploadStatus('İçe aktarılıyor...')
    setCsvUploading(true)
    try {
      const res = await keywordsApi.commitCsvUpload(activeWorkspace.id, csvDryRun.dry_run_token)
      setUploadStatus(formatKeywordImportStatus(res.data))
      setLastImport(res.data)
      setUploadedFile(null)
      setCsvDryRun(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      fetchKeywords()
    } catch (err: unknown) {
      const e = err as { message?: string; detail?: unknown }
      setUploadStatus(`⚠ Hata: ${e?.message || String(e?.detail || 'Bilinmeyen hata')}`)
    } finally {
      setCsvUploading(false)
    }
  }

  const handleGoogleAdsImport = async (
    keywords: EnrichedKeywordOut[],
    market?: FetchedMarket
  ): Promise<KeywordImportResponse> => {
    if (!activeWorkspace) {
      throw new Error('Önce bir marka çalışması seçin')
    }
    const mapped: KeywordCreate[] = keywords.map((kw) => ({
      keyword: kw.keyword,
      monthly_volume: kw.avg_monthly_searches,
      trend_3m: kw.trend_3m,
      trend_12m: kw.trend_12m,
      competition_score: kw.competition_score,
      data_source: 'google_ads_api',
      ...(market ? { geo_target_id: market.geo_target_id, language_id: market.language_id } : {}),
    }))
    const res = await keywordsApi.import(mapped, activeWorkspace.id)
    setLastImport(res.data)
    fetchKeywords()
    return res.data
  }

  const handleUrlSeedImport = async (keywords: UrlSeedIdea[], market?: FetchedMarket) => {
    if (!activeWorkspace) {
      setError('Önce bir marka çalışması seçin')
      return
    }
    const mapped: KeywordCreate[] = keywords.map((idea) => ({
      keyword: idea.keyword,
      monthly_volume: idea.monthly_volume,
      trend_3m: idea.trend_3m,
      trend_12m: idea.trend_12m,
      competition_score: idea.competition,
      data_source: 'url_seed',
      ...(market ? { geo_target_id: market.geo_target_id, language_id: market.language_id } : {}),
    }))
    try {
      const res = await keywordsApi.import(mapped, activeWorkspace.id)
      setUploadStatus(formatKeywordImportStatus(res.data))
      setLastImport(res.data)
      fetchKeywords()
      return res.data
    } catch (err: unknown) {
      setError(`Hata: ${apiErrorMessage(err, 'Bilinmeyen hata')}`)
      throw err
    }
  }

  const filteredKeywords = keywords.filter((kw) =>
    kw.keyword.toLowerCase().includes(search.toLowerCase())
  )
  // Silme/yenileme sonrası liste kısalırsa son geçerli sayfada kal
  const keywordLastPage = Math.max(0, Math.ceil(filteredKeywords.length / KEYWORD_PAGE_SIZE) - 1)
  const currentKeywordPage = Math.min(keywordPage, keywordLastPage)
  const pagedKeywords = filteredKeywords.slice(
    currentKeywordPage * KEYWORD_PAGE_SIZE,
    (currentKeywordPage + 1) * KEYWORD_PAGE_SIZE
  )

  const workspaceDisabled = !activeWorkspace
  const competitorHost = (url: string) =>
    url
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, '')
      .replace(/^www\./, '')
      .replace(/\/.*$/, '')
  const profileCompetitorUrls = activeWorkspace?.competitor_urls || []
  const profileHosts = new Set(profileCompetitorUrls.map(competitorHost))
  const competitorChoices = [
    ...profileCompetitorUrls.map((url) => ({ url, discovered: false })),
    ...discoveredCompetitorUrls
      .filter(
        (url, i, all) =>
          !profileHosts.has(competitorHost(url)) &&
          all.findIndex((u) => competitorHost(u) === competitorHost(url)) === i
      )
      .map((url) => ({ url, discovered: true })),
  ]
  const disableMessage = workspaceDisabled ? 'Önce Marka Çalışması seçin' : undefined
  const activeSourceTab = TABS.find((t) => t.id === activeTab)

  return (
    <div className="keywords-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Anahtar Kelimeler</h1>
          <p>
            Anahtar kelime yönetimi ve dışa aktarım
            {activeWorkspace && (
              <>
                {' · '}
                <span className="kwx-subtitle-brand">{activeWorkspace.name}</span>
                <MarketBadge label={activeWorkspace.market_label} />
              </>
            )}
          </p>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className="kwx-btn-raised"
            onClick={fetchKeywords}
            disabled={loading}
          >
            <RefreshCw size={14} />
            Yenile
          </button>
        </div>
      </header>

      {!activeWorkspace && (
        <div className="no-workspace">
          <AlertCircle size={20} />
          <span>Önce Marka Çalışması seçin</span>
        </div>
      )}

      <div className="keyword-view-toggle" role="tablist" aria-label="Anahtar kelime görünümü">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'keywords' ? 'true' : 'false'}
          className={view === 'keywords' ? 'active' : ''}
          onClick={() => setKeywordView('keywords')}
        >
          <List size={16} />
          Kelimeler
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'scores' ? 'true' : 'false'}
          className={view === 'scores' ? 'active' : ''}
          onClick={() => {
            setKeywordView('scores', selectedScoreRunId)
            void fetchScoreRuns()
          }}
          disabled={!activeWorkspace}
        >
          <BarChart3 size={16} />
          Skorlama
        </button>
      </div>

      {/* Analiz zinciri ilerleme banner'ı — her iki görünümde de görünür */}
      {chainStage && !chainDismissed && (
        <AnalysisBanner
          stage={chainStage}
          taskProgress={assignPolling.progress || 0}
          channels={chainChannels}
          message={chainMessage}
          errorMessage={assignPolling.errorMessage}
          onDismiss={dismissChain}
        />
      )}

      {/* Kaynak paneli: sekmeler + form tek kartta (tasarım) */}
      {view === 'keywords' && (
        <section className={`kwx-source-panel${sourceCollapsed ? ' is-collapsed' : ''}`}>
          <div className="kwx-source-tabs">
            {TABS.map((tab) => {
              const Icon = tab.icon
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={`kwx-source-tab${activeTab === tab.id ? ' active' : ''}`}
                  onClick={() => {
                    switchTab(tab.id)
                    setSourceCollapsed(false)
                  }}
                  disabled={workspaceDisabled}
                  title={workspaceDisabled ? disableMessage : undefined}
                >
                  <Icon size={15} /> {tab.label}
                  <InfoTip text={tab.description} />
                </button>
              )
            })}
            <button
              type="button"
              className="kwx-collapse-btn"
              onClick={() => setSourceCollapsed((c) => !c)}
              title={sourceCollapsed ? 'Genişlet' : 'Daralt'}
            >
              <span className={`chev${sourceCollapsed ? ' is-collapsed' : ''}`}>
                <ChevronDown size={16} />
              </span>
            </button>
          </div>

          {sourceCollapsed ? (
            <div className="kwx-collapsed-row">
              {activeSourceTab && <activeSourceTab.icon size={14} />}
              <span className="label">{activeSourceTab?.label}</span>
              <button
                type="button"
                className="kwx-expand-link"
                onClick={() => setSourceCollapsed(false)}
              >
                Düzenle
              </button>
            </div>
          ) : (
            <div className="kwx-source-body">
              <div className="kwx-source-head">
                {activeSourceTab && <activeSourceTab.icon size={15} />}
                <span className="kwx-source-head-label">{activeSourceTab?.label}</span>
                {activeTab === 'google-ads' &&
                  (activeWorkspace?.suggested_keywords?.length || 0) > 0 && (
                    <span className="kwx-loaded-pill">
                      <CheckCircle size={12} strokeWidth={2.4} /> Marka profilinden{' '}
                      {activeWorkspace?.suggested_keywords?.length} başlangıç kelimesi yüklendi
                    </span>
                  )}
              </div>
              {/* CSV Upload */}
              {!workspaceDisabled && activeTab === 'csv' && (
                <div className="kwx-csv-form">
                  <div
                    className={`kwx-dropzone${dragActive ? ' drag-active' : ''}`}
                    onDragEnter={handleDrag}
                    onDragLeave={handleDrag}
                    onDragOver={handleDrag}
                    onDrop={handleDrop}
                    onClick={() => fileInputRef.current?.click()}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click()
                    }}
                  >
                    <FileUp size={22} />
                    <div className="kwx-dropzone-title">
                      {uploadedFile ? uploadedFile.name : 'CSV dosyasını sürükle veya seç'}
                    </div>
                    <div className="kwx-dropzone-sub">
                      {uploadedFile
                        ? 'Dosya hazır — analiz edip içe aktarabilirsiniz'
                        : 'Hazır anahtar kelime listesini toplu içe aktar'}
                    </div>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".csv,.tsv,.txt"
                      onChange={handleFileChange}
                      onClick={(e) => e.stopPropagation()}
                      hidden
                    />
                  </div>

                  {uploadStatus && (
                    <div
                      className={`upload-status ${uploadStatus.startsWith('✓') ? 'success' : 'error'}`}
                    >
                      {uploadStatus.startsWith('✓') && <CheckCircle size={16} />}
                      {uploadStatus}
                    </div>
                  )}

                  {csvDryRun?.market_notice && (
                    <p className="form-hint" role="note">
                      {csvDryRun.market_notice}
                    </p>
                  )}

                  {csvDryRun && (
                    <div className="import-summary">
                      <div>
                        <strong>{csvDryRun.accepted}</strong>
                        <span>Eklenecek</span>
                      </div>
                      {(csvDryRun.exact_duplicate_different_metrics ?? 0) > 0 && (
                        <div>
                          <strong>{csvDryRun.exact_duplicate_different_metrics}</strong>
                          <span>Farklı metrik (yeni kayıt)</span>
                        </div>
                      )}
                      <div>
                        <strong>{csvDryRun.skipped_exact}</strong>
                        <span>Zaten mevcut</span>
                      </div>
                      <div>
                        <strong>{csvDryRun.skipped_fuzzy}</strong>
                        <span>Benzer atlanan</span>
                      </div>
                      {(csvDryRun.skipped_theme ?? 0) > 0 && (
                        <div>
                          <strong>{csvDryRun.skipped_theme}</strong>
                          <span>Yasaklı tema</span>
                        </div>
                      )}
                      <div>
                        <strong>{csvDryRun.junk_rows?.length || 0}</strong>
                        <span>Metadata/boş</span>
                      </div>
                      <small>
                        Parser: {String(csvDryRun.parser_meta?.encoding || '-')} · header satırı{' '}
                        {String(csvDryRun.parser_meta?.header_row || '-')}
                      </small>
                    </div>
                  )}

                  {(csvDryRun?.theme_excluded?.length || 0) > 0 && (
                    <div className="theme-excluded-block">
                      <p className="form-hint">
                        Yasaklı temayla elenen satırlar ({csvDryRun?.theme_excluded?.length}) —
                        isterseniz tek tek geri alın:
                      </p>
                      <ul className="skipped-list themed-list">
                        {csvDryRun?.theme_excluded?.map((d, i) => (
                          <li key={i}>
                            <strong>{d.keyword}</strong>
                            <span className="skipped-reason">
                              - Yasaklı temayla elendi
                              {d.matched_keyword ? `: "${d.matched_keyword}"` : ''}
                            </span>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm restore-btn"
                              onClick={
                                () =>
                                  handleForceInclude({
                                    keyword: d.keyword || '',
                                    reason: 'skipped_theme',
                                    monthly_volume: d.monthly_volume,
                                    trend_3m: d.trend_3m,
                                    trend_12m: d.trend_12m,
                                    competition_score: d.competition_score,
                                    data_source:
                                      (d.data_source as KeywordCreate['data_source']) || 'csv',
                                    geo_target_id: d.geo_target_id,
                                    language_id: d.language_id,
                                  }).catch(() => {}) // hata handleForceInclude'da gösterildi
                              }
                            >
                              Geri al
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div className="kwx-run-row-btn">
                    <button
                      type="button"
                      className="kwx-run-btn"
                      onClick={csvDryRun ? handleCommitCsvUpload : handleUpload}
                      disabled={!uploadedFile || csvUploading}
                    >
                      <Upload size={15} strokeWidth={2.2} />
                      {csvDryRun ? 'İçe Aktar' : 'Analiz Et'}
                    </button>
                  </div>
                </div>
              )}

              {/* Manual Add */}
              {!workspaceDisabled && activeTab === 'manual' && (
                <div className="kwx-manual-form">
                  <label className="kwx-field">
                    <span className="kwx-field-label">Anahtar Kelime</span>
                    <input
                      className="kwx-input"
                      value={newKeyword.keyword}
                      onChange={(e) => setNewKeyword({ ...newKeyword, keyword: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          void handleCreate()
                        }
                      }}
                      placeholder="Tek kelime yaz ve ekle…"
                    />
                  </label>
                  <div className="kwx-run-row-btn">
                    <button
                      type="button"
                      className="kwx-run-btn"
                      onClick={handleCreate}
                      disabled={!newKeyword.keyword.trim()}
                    >
                      <Plus size={15} strokeWidth={2.2} />
                      Ekle
                    </button>
                  </div>
                  <KeywordImportResult
                    result={manualImportResult}
                    onForceInclude={handleForceInclude}
                  />
                </div>
              )}

              {/* Google Ads Search (Seed kaynak kartı) */}
              {!workspaceDisabled && activeTab === 'google-ads' && (
                <GoogleAdsKeywordSearch
                  onImport={handleGoogleAdsImport}
                  onForceInclude={handleForceInclude}
                />
              )}

              {/* URL Seed */}
              {!workspaceDisabled && activeTab === 'url' && (
                <UrlKeywordExtractor
                  onImport={handleUrlSeedImport}
                  onForceInclude={handleForceInclude}
                />
              )}

              {/* Rakip URL'leri */}
              {!workspaceDisabled && activeTab === 'competitor' && (
                <div className="kwx-competitor-form">
                  {discoveredCompetitorsError && (
                    <p className="form-hint">{discoveredCompetitorsError}</p>
                  )}
                  {competitorChoices.length === 0 ? (
                    <p className="form-hint">
                      Bu marka çalışmasında rakip adresi yok. Marka profilinden rakip URL'si
                      ekleyebilir ya da "AI ile Engellenecek Rakipler" bölümünde Google ile rakip
                      bulup onaylayabilirsiniz.
                    </p>
                  ) : (
                    <>
                      <div className="kwx-chips">
                        {competitorChoices.map(({ url, discovered }) => (
                          <button
                            key={url}
                            type="button"
                            className={`kwx-chip is-clickable${competitorUrl === url ? ' is-selected' : ''}`}
                            onClick={() => setCompetitorUrl(url)}
                            title={
                              discovered
                                ? 'Google ile bulunup onaylanan rakip'
                                : 'Marka profilindeki rakip'
                            }
                          >
                            {url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}
                          </button>
                        ))}
                      </div>
                      {competitorUrl && (
                        <UrlKeywordExtractor
                          key={competitorUrl}
                          initialUrl={competitorUrl}
                          onImport={handleUrlSeedImport}
                          onForceInclude={handleForceInclude}
                        />
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {view === 'keywords' && lastImport && (
        <div className="kwx-import-bar">
          <CheckCircle size={15} />
          <span className="kwx-import-text">
            <b>{lastImport.requested}</b> satır işlendi: <b>{lastImport.created} eklendi</b>
            <span className="sep"> · </span>
            {lastImport.created_new} yeni kayıt, {lastImport.linked_existing} mevcut bağlandı
            <span className="sep"> · </span>
            {lastImport.skipped} atlandı
            <span className="sep"> · </span>
            toplam {lastImport.total_before} <span className="arrow">→</span>{' '}
            {lastImport.total_after}
          </span>
          <button
            type="button"
            className="kwx-import-close"
            onClick={() => setLastImport(null)}
            title="Kapat"
          >
            <X size={13} />
          </button>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
      {statusNotice && (
        <ErrorBanner
          error={statusNotice}
          onRetry={() => {
            setStatusNotice(null)
            setTaskDiscoveryActive(true)
          }}
          retryLabel={POLL_RECHECK_LABEL}
          onDismiss={() => setStatusNotice(null)}
        />
      )}
      {assignTaskId && assignPolling.pollFailed && (
        <ErrorBanner
          error={POLL_FAILED_MESSAGE}
          onRetry={assignPolling.recheck}
          retryLabel={POLL_RECHECK_LABEL}
        />
      )}

      {view === 'scores' ? (
        <div className="keyword-list-section">
          <h3 className="kwx-scores-title">Skorlama Sonuçları</h3>
          {scoreRunsError && (
            <ErrorBanner
              error={scoreRunsError}
              onRetry={() => void fetchScoreRuns()}
              retryLabel="Listeyi yeniden yükle"
            />
          )}
          <div className="kwx-run-row">
            <select
              title="Analiz seç"
              value={selectedScoreRunId || ''}
              onChange={(e) => selectScoreRun(e.target.value ? Number(e.target.value) : null)}
              disabled={scoreRunsLoading || scoreRuns.length === 0}
            >
              <option value="">
                {scoreRunsLoading ? 'Analizler yükleniyor...' : 'Analiz seçin'}
              </option>
              {scoreRuns.map((run) => (
                <option key={run.id} value={run.id}>
                  #{run.id} {run.run_name || 'Analiz'} — {runStatusLabel(run.status)}
                </option>
              ))}
            </select>
            {selectedRunBadge && (
              <span className={`kwx-run-badge is-${selectedRunBadge.tone}`}>
                {selectedRunBadge.label}
              </span>
            )}
            <button
              type="button"
              className="kwx-btn-raised"
              onClick={() => void fetchScoreRuns()}
              disabled={scoreRunsLoading}
            >
              <RefreshCw size={14} />
              Yenile
            </button>
            <button
              type="button"
              className="kwx-btn-raised"
              onClick={() => void handleScoresExport()}
              disabled={!selectedScoreRunId}
              title={
                selectedScoreRunId
                  ? 'Skorlanan kelimeleri Excel olarak indir'
                  : 'Önce bir analiz seçin'
              }
            >
              <Download size={14} />
              Skorları İndir
            </button>
          </div>

          <ScoreResults
            runId={selectedScoreRunId}
            workspaceId={activeWorkspace?.id}
            channels={chainChannels}
          />
        </div>
      ) : (
        /* Havuz listesi (tasarım: başlık + pill + araç çubuğu + tablo) */
        <div className="keyword-list-section">
          <div className="kwx-pool-head">
            <h2>
              Anahtar Kelime Listesi ({filteredKeywords.length}/{keywords.length})
            </h2>
            <span
              className={`kwx-pool-pill${poolTotal >= WORKSPACE_KEYWORD_LIMIT ? ' is-full' : ''}`}
              title="Aday havuzu üst limiti; dolunca yeni import atlanır"
            >
              Havuz: {poolTotal}/{WORKSPACE_KEYWORD_LIMIT}
            </span>
          </div>
          {poolTotal >= WORKSPACE_KEYWORD_LIMIT && (
            <div className="field-warning">
              <AlertCircle size={13} />
              Havuz limiti dolu — yeni kelime eklemek için önce mevcutlardan silin.
            </div>
          )}

          <div className="kwx-toolbar">
            <div className="kwx-search">
              <span className="icon">
                <Search size={15} />
              </span>
              <input
                type="text"
                placeholder="Ara…"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setKeywordPage(0)
                }}
              />
            </div>
            <button
              type="button"
              className="kwx-btn-soft"
              onClick={() => void handlePoolExport()}
              disabled={keywords.length === 0}
              title={
                keywords.length === 0
                  ? 'Havuz boş — önce anahtar kelime ekleyin'
                  : 'Aktif kelime havuzunu Excel olarak indir'
              }
            >
              <Download size={15} />
              Havuzu İndir
            </button>
            <button
              type="button"
              className="kwx-btn-soft is-danger"
              onClick={handleDeleteAll}
              disabled={keywords.length === 0}
            >
              <Trash2 size={15} />
              Tümünü Sil
            </button>
            <button
              type="button"
              className="kwx-btn-soft is-accent"
              onClick={() => setShowStartModal(true)}
              disabled={keywords.length === 0}
              title={
                keywords.length === 0 ? 'Önce en az 1 anahtar kelime eklemelisiniz' : undefined
              }
            >
              <BarChart3 size={15} />
              Analizi Başlat
              <ArrowRight size={15} />
            </button>
          </div>

          {loading ? (
            <div className="loading-state">
              <RefreshCw size={24} className="animate-spin" />
              <p>Yükleniyor...</p>
            </div>
          ) : (
            <div className="kwx-table-wrap">
              <table className="kwx-table">
                <thead>
                  <tr>
                    <th className="is-left pad-first">Anahtar Kelime</th>
                    <th className="is-left">Kaynak</th>
                    <th className="is-left">Sektör</th>
                    <th>Aylık Arama</th>
                    <th>12 Aylık Trend</th>
                    <th>3 Aylık Trend</th>
                    <th>Rekabet</th>
                    <th className="is-center">İşlem</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedKeywords.map((kw) => {
                    const monthlyVolume = kw.wk_monthly_volume ?? kw.monthly_volume
                    const trend12m = kw.wk_trend_12m ?? kw.trend_12m
                    const trend3m = kw.wk_trend_3m ?? kw.trend_3m
                    const competition = kw.wk_competition_score ?? kw.competition_score
                    const source = SOURCE_BADGES[kw.wk_data_source || kw.data_source || 'csv']

                    return (
                      <tr key={kw.wk_id ?? kw.id}>
                        <td className="is-left pad-first kwx-kw-cell">{kw.keyword}</td>
                        <td className="is-left">
                          {source ? <span className="kwx-src-badge">{source.label}</span> : '—'}
                        </td>
                        <td className={`is-left${!kw.sector ? ' kwx-muted' : ''}`}>
                          {kw.sector || '—'}
                        </td>
                        <td className="kwx-mono kwx-kw-cell">
                          {monthlyVolume?.toLocaleString('tr-TR') || '—'}
                        </td>
                        <td>
                          <TrendCell value={trend12m} />
                        </td>
                        <td>
                          <TrendCell value={trend3m} />
                        </td>
                        <td>
                          <CompCell value={competition} />
                        </td>
                        <td className="is-center">
                          <button
                            type="button"
                            className="kwx-row-del"
                            title="Havuzdan sil"
                            onClick={() => handleDelete(kw.wk_id ?? kw.id)}
                          >
                            <Trash2 size={15} />
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {!loading && (
            <TablePager
              page={currentKeywordPage}
              pageSize={KEYWORD_PAGE_SIZE}
              total={filteredKeywords.length}
              unit="kelime"
              onPage={setKeywordPage}
            />
          )}
        </div>
      )}

      {activeWorkspace && (
        <StartAnalysisModal
          open={showStartModal}
          brandProfileId={activeWorkspace.id}
          poolCount={poolTotal}
          onClose={() => setShowStartModal(false)}
          onStarted={(runId) => {
            setShowStartModal(false)
            setSelectedScoreRunId(runId)
            setKeywordView('scores', runId)
            setTaskDiscoveryActive(true)
            setChainActive(true)
            setChainRunId(runId)
            setChainDismissed(false)
            setChainRunStatus(null)
            void fetchScoreRuns()
          }}
        />
      )}
    </div>
  )
}
