import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Plus,
  Upload,
  Trash2,
  Search,
  RefreshCw,
  FileSpreadsheet,
  FileUp,
  CheckCircle,
  AlertCircle,
  Keyboard,
  Link2,
  Megaphone,
} from "lucide-react";
import {
  keywordsApi,
  KeywordCreate,
  EnrichedKeywordOut,
} from "../services/api";
import { useBrandStore } from "../stores/brandStore";
import GoogleAdsKeywordSearch from "../components/GoogleAdsKeywordSearch";
import UrlKeywordExtractor from "../components/UrlKeywordExtractor";
import { UrlSeedIdea } from "../services/api";
import "./Keywords.css";

type TabId = "csv" | "manual" | "google-ads" | "url";

const TABS: {
  id: TabId;
  label: string;
  description: string;
  icon: typeof Megaphone;
}[] = [
  {
    id: "google-ads",
    label: "Google Ads Arama",
    description: "Marka profilindeki seed kelimelerle hacim ve rekabet verisi al",
    icon: Megaphone,
  },
  {
    id: "url",
    label: "URL'den Çıkar",
    description: "Sayfa URL'sinden yeni anahtar kelime önerileri üret",
    icon: Link2,
  },
  {
    id: "manual",
    label: "Manuel Ekle",
    description: "Tekil kelimeyi hızlıca çalışma alanına bağla",
    icon: Keyboard,
  },
  {
    id: "csv",
    label: "CSV Yükle",
    description: "Hazır keyword listesini toplu içe aktar",
    icon: FileSpreadsheet,
  },
];

interface Keyword {
  id: number;
  keyword: string;
  sector?: string;
  target_market?: string;
  monthly_volume?: number;
  trend_12m?: number;
  trend_3m?: number;
  competition_score?: number;
  wk_monthly_volume?: number;
  wk_trend_12m?: number;
  wk_trend_3m?: number;
  wk_competition_score?: number;
  wk_data_source?: string;
  is_active: boolean;
  data_source?: string;
}

export default function Keywords() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace);
  const hasBrandSuggestedKeywords =
    (activeWorkspace?.suggested_keywords?.filter((kw) => kw.trim()).length || 0) > 0;
  const getDefaultTab = (): TabId =>
    hasBrandSuggestedKeywords ? "google-ads" : "url";

  const initialTab = (searchParams.get("tab") as TabId) || getDefaultTab();
  const [activeTab, setActiveTab] = useState<TabId>(
    TABS.some((t) => t.id === initialTab) ? initialTab : getDefaultTab(),
  );

  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [newKeyword, setNewKeyword] = useState<KeywordCreate>({
    keyword: "",
    sector: "",
    monthly_volume: 1000,
    trend_12m: 10,
    trend_3m: 15,
    competition_score: 0.5,
    data_source: "manual",
  });

  useEffect(() => {
    fetchKeywords();
  }, [activeWorkspace?.id]);

  useEffect(() => {
    const tabFromUrl = searchParams.get("tab") as TabId | null;
    if (tabFromUrl && TABS.some((tab) => tab.id === tabFromUrl)) {
      setActiveTab(tabFromUrl);
      return;
    }
    setActiveTab(getDefaultTab());
  }, [activeWorkspace?.id, activeWorkspace?.suggested_keywords, searchParams]);

  const switchTab = (tab: TabId) => {
    setActiveTab(tab);
    setSearchParams({ tab });
  };

  const fetchKeywords = async () => {
    if (!activeWorkspace) {
      setKeywords([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await keywordsApi.list({
        limit: 2000,
        brand_profile_id: activeWorkspace.id,
      });
      setKeywords(res.data.items || []);
    } catch (err: any) {
      console.error("Error fetching keywords:", err);
      setError("API bağlantısı kurulamadı. Backend çalışıyor mu?");
      setKeywords([]);
    }
    setLoading(false);
  };

  const handleCreate = async () => {
    if (!newKeyword.keyword.trim()) return;
    if (!activeWorkspace) {
      setError("Önce bir marka çalışması seçin");
      return;
    }
    try {
      await keywordsApi.create(newKeyword, activeWorkspace.id);
      setShowModal(false);
      setNewKeyword({
        keyword: "",
        sector: "",
        monthly_volume: 1000,
        trend_12m: 10,
        trend_3m: 15,
        competition_score: 0.5,
        data_source: "manual",
      });
      fetchKeywords();
    } catch (err) {
      const newItem: Keyword = {
        id: Date.now(),
        ...newKeyword,
        is_active: true,
      };
      setKeywords([...keywords, newItem]);
      setShowModal(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await keywordsApi.delete(id, activeWorkspace?.id);
      fetchKeywords();
    } catch (err) {
      setKeywords(keywords.filter((k) => k.id !== id));
    }
  };

  const handleDeleteAll = async () => {
    if (!activeWorkspace) {
      setError("Önce bir marka çalışması seçin");
      return;
    }
    if (
      !confirm(
        "Bu çalışmadaki TÜM anahtar kelimelerin bağlantısı kaldırılacak. Emin misiniz?",
      )
    )
      return;
    try {
      await keywordsApi.deleteAll(activeWorkspace.id);
      fetchKeywords();
    } catch (err) {
      setError("Silme başarısız oldu");
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setUploadedFile(e.target.files[0]);
      setUploadStatus(null);
    }
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setUploadedFile(e.dataTransfer.files[0]);
      setUploadStatus(null);
    }
  };

  const handleUpload = async () => {
    if (!uploadedFile) return;
    if (!activeWorkspace) {
      setUploadStatus("Önce bir marka çalışması seçin");
      return;
    }
    setUploadStatus("Yükleniyor...");
    try {
      const text = await uploadedFile.text();
      const rows = text
        .trim()
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);

      if (rows.length < 2) {
        setUploadStatus("⚠ CSV en az 2 satır olmalı (başlık + veri)");
        return;
      }

      const headers = rows[0]
        .split(/[;,\t]/)
        .map((h) => h.trim().toLowerCase());
      const dataRows = rows.slice(1);
      const imported: KeywordCreate[] = [];

      for (const row of dataRows) {
        const cols = row.split(/[;,\t]/).map((c) => c.trim());
        const kwData: KeywordCreate = { keyword: cols[0] || "" };

        const sectorIdx = headers.indexOf("sector");
        const volumeIdx = headers.indexOf("monthly_volume");
        const trend12Idx = headers.indexOf("trend_12m");
        const trend3Idx = headers.indexOf("trend_3m");
        const compIdx = headers.indexOf("competition_score");

        if (sectorIdx >= 0 && cols[sectorIdx]) kwData.sector = cols[sectorIdx];
        if (volumeIdx >= 0 && cols[volumeIdx])
          kwData.monthly_volume = parseInt(cols[volumeIdx]) || 1000;
        if (trend12Idx >= 0 && cols[trend12Idx])
          kwData.trend_12m = parseFloat(cols[trend12Idx]) || 0;
        if (trend3Idx >= 0 && cols[trend3Idx])
          kwData.trend_3m = parseFloat(cols[trend3Idx]) || 0;
        if (compIdx >= 0 && cols[compIdx])
          kwData.competition_score = parseFloat(cols[compIdx]) || 0.5;

        imported.push(kwData);
      }

      await keywordsApi.import(imported, activeWorkspace.id);
      setUploadStatus(`✓ ${imported.length} kelime içe aktarıldı`);
      setUploadedFile(null);
      fetchKeywords();
    } catch (err: any) {
      setUploadStatus(`⚠ Hata: ${err?.response?.data?.detail || err.message}`);
    }
  };

  const handleGoogleAdsImport = async (keywords: EnrichedKeywordOut[]) => {
    if (!activeWorkspace) {
      setError("Önce bir marka çalışması seçin");
      return;
    }
    const mapped: KeywordCreate[] = keywords.map((kw) => ({
      keyword: kw.keyword,
      monthly_volume: kw.avg_monthly_searches,
      trend_3m: kw.trend_3m,
      trend_12m: kw.trend_12m,
      competition_score: kw.competition_score,
      data_source: "google_ads_api",
      geo_target_id: activeWorkspace.default_geo_target_id || undefined,
      language_id: activeWorkspace.default_language_id || undefined,
    }));
    try {
      await keywordsApi.import(mapped, activeWorkspace.id);
      setUploadStatus(`✓ ${mapped.length} keyword içe aktarıldı`);
      fetchKeywords();
    } catch (err: any) {
      setError(`Hata: ${err?.response?.data?.detail || err.message}`);
    }
  };

  const handleUrlSeedImport = async (keywords: UrlSeedIdea[]) => {
    if (!activeWorkspace) {
      setError("Önce bir marka çalışması seçin");
      return;
    }
    const mapped: KeywordCreate[] = keywords.map((idea) => ({
      keyword: idea.keyword,
      monthly_volume: idea.monthly_volume,
      trend_3m: idea.trend_3m,
      trend_12m: idea.trend_12m,
      competition_score: idea.competition,
      data_source: "url_seed",
      geo_target_id: activeWorkspace.default_geo_target_id || undefined,
      language_id: activeWorkspace.default_language_id || undefined,
    }));
    try {
      await keywordsApi.import(mapped, activeWorkspace.id);
      setUploadStatus(`✓ ${mapped.length} keyword içe aktarıldı`);
      fetchKeywords();
    } catch (err: any) {
      setError(`Hata: ${err?.response?.data?.detail || err.message}`);
    }
  };

  const filteredKeywords = keywords.filter((kw) =>
    kw.keyword.toLowerCase().includes(search.toLowerCase()),
  );

  const workspaceDisabled = !activeWorkspace;
  const disableMessage = workspaceDisabled
    ? "Önce Marka Çalışması seçin"
    : undefined;

  return (
    <div className="keywords-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Anahtar Kelimeler</h1>
          <p>Anahtar kelime yönetimi ve dışa aktarım</p>
        </div>
        <div className="header-actions">
          <button
            className="btn btn-secondary"
            onClick={fetchKeywords}
            disabled={loading}
          >
            <RefreshCw size={16} />
            Yenile
          </button>
        </div>
      </header>

      {activeWorkspace && (
        <div className="workspace-pill">
          <span className="pill-label">Aktif Marka:</span>
          <span className="pill-value">{activeWorkspace.name}</span>
        </div>
      )}

      {!activeWorkspace && (
        <div className="no-workspace">
          <AlertCircle size={20} />
          <span>Önce Marka Çalışması seçin</span>
        </div>
      )}

      {/* Import Methods */}
      <div className="keyword-methods">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              className={`keyword-method ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => switchTab(tab.id)}
              disabled={workspaceDisabled}
              title={workspaceDisabled ? disableMessage : undefined}
            >
              <span className="keyword-method-icon">
                <Icon size={18} />
              </span>
              <span className="keyword-method-copy">
                <strong>{tab.label}</strong>
                <small>{tab.description}</small>
              </span>
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      <div className="tab-content">
        {/* CSV Upload */}
        {!workspaceDisabled && activeTab === "csv" && (
          <div className="upload-section glass-card">
            <h3>CSV Dosyası Yükle</h3>
            <p>
              CSV formatı: keyword, sector, monthly_volume, trend_12m, trend_3m,
              competition_score
            </p>

            <div
              className={`drop-zone ${dragActive ? "drag-active" : ""}`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
            >
              <FileUp size={32} />
              {uploadedFile ? (
                <span>{uploadedFile.name}</span>
              ) : (
                <span>CSV dosyasını sürükleyip bırakın veya seçin</span>
              )}
              <button
                className="btn btn-secondary"
                onClick={() => fileInputRef.current?.click()}
              >
                Dosya Seç
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.tsv,.txt"
                onChange={handleFileChange}
                hidden
              />
            </div>

            {uploadStatus && (
              <div
                className={`upload-status ${uploadStatus.startsWith("✓") ? "success" : "error"}`}
              >
                {uploadStatus.startsWith("✓") && <CheckCircle size={16} />}
                {uploadStatus}
              </div>
            )}

            <button
              className="btn btn-primary"
              onClick={handleUpload}
              disabled={!uploadedFile}
            >
              <Upload size={16} />
              Yükle
            </button>
          </div>
        )}

        {/* Manual Add */}
        {!workspaceDisabled && activeTab === "manual" && (
          <div className="manual-section glass-card">
            <h3>Manuel Ekle</h3>
            <div className="form-group">
              <label>Anahtar Kelime</label>
              <input
                className="input"
                value={newKeyword.keyword}
                onChange={(e) =>
                  setNewKeyword({ ...newKeyword, keyword: e.target.value })
                }
                placeholder="örn. organik şampuan"
              />
            </div>
            <div className="form-group">
              <label>Sektör</label>
              <input
                className="input"
                value={newKeyword.sector || ""}
                onChange={(e) =>
                  setNewKeyword({ ...newKeyword, sector: e.target.value })
                }
                placeholder="örn. kozmetik"
              />
            </div>
            <button
              className="btn btn-primary"
              onClick={handleCreate}
              disabled={!newKeyword.keyword.trim()}
            >
              <Plus size={16} />
              Ekle
            </button>
          </div>
        )}

        {/* Google Ads Search */}
        {!workspaceDisabled && activeTab === "google-ads" && (
          <GoogleAdsKeywordSearch onImport={handleGoogleAdsImport} />
        )}

        {/* URL Seed */}
        {!workspaceDisabled && activeTab === "url" && (
          <UrlKeywordExtractor onImport={handleUrlSeedImport} />
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* Keyword List */}
      <div className="keyword-list-section">
        <div className="list-header">
          <h2>
            Anahtar Kelime Listesi ({filteredKeywords.length}/{keywords.length})
          </h2>
          <div className="list-controls">
            <div className="search-box">
              <Search size={16} />
              <input
                type="text"
                placeholder="Ara..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <button
              className="btn btn-danger"
              onClick={handleDeleteAll}
              disabled={keywords.length === 0}
            >
              <Trash2 size={16} />
              Tümünü Sil
            </button>
          </div>
        </div>

        {loading ? (
          <div className="loading-state">
            <RefreshCw size={24} className="animate-spin" />
            <p>Yükleniyor...</p>
          </div>
        ) : (
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>Sektör</th>
                  <th>Aylık Arama</th>
                  <th>Trend 12M</th>
                  <th>Trend 3M</th>
                  <th>Rekabet</th>
                  <th>İşlem</th>
                </tr>
              </thead>
              <tbody>
                {filteredKeywords.map((kw) => {
                  const monthlyVolume = kw.wk_monthly_volume ?? kw.monthly_volume;
                  const trend12m = kw.wk_trend_12m ?? kw.trend_12m;
                  const trend3m = kw.wk_trend_3m ?? kw.trend_3m;
                  const competition =
                    kw.wk_competition_score ?? kw.competition_score;

                  return (
                    <tr key={kw.id}>
                      <td>
                        <strong>{kw.keyword}</strong>
                      </td>
                      <td>{kw.sector || "—"}</td>
                      <td>{monthlyVolume?.toLocaleString() || "—"}</td>
                      <td>{trend12m != null ? `${trend12m}%` : "—"}</td>
                      <td>{trend3m != null ? `${trend3m}%` : "—"}</td>
                      <td>
                        {competition != null ? competition.toFixed(2) : "—"}
                      </td>
                      <td>
                        <button
                          className="btn btn-sm btn-danger"
                          title="Sil"
                          onClick={() => handleDelete(kw.id)}
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
