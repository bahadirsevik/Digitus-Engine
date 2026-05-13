import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Play,
  Eye,
  TrendingUp,
  Award,
  Trash2,
  Download,
  ArrowRight,
  ChevronUp,
  ChevronDown,
  AlertCircle,
} from "lucide-react";
import { scoringApi, ScoringRunCreate } from "../services/api";
import type { ScoringRun } from "../types/models";
import { useBrandStore } from "../stores/brandStore";
import "./Scoring.css";


interface KeywordScore {
  keyword_id: number;
  keyword: string;
  ads_score: number | null;
  seo_score: number | null;
  social_score: number | null;
  ads_rank?: number | null;
  seo_rank?: number | null;
  social_rank?: number | null;
}

type SortField =
  | "keyword"
  | "ads_score"
  | "seo_score"
  | "social_score"
  | "ads_rank"
  | "seo_rank"
  | "social_rank";
type SortDirection = "asc" | "desc";

export default function Scoring() {
  const navigate = useNavigate();
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace);

  const [runs, setRuns] = useState<ScoringRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const [scores, setScores] = useState<KeywordScore[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [sortField, setSortField] = useState<SortField>("ads_score");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const [newRun, setNewRun] = useState<
    ScoringRunCreate & {
      enable_ads: boolean;
      enable_seo: boolean;
      enable_social: boolean;
      keyword_selection_mode: "all" | "top_n" | "specific";
      keyword_limit: number;
      skip_relevance: boolean;
    }
  >({
    run_name: "",
    brand_profile_id: undefined,
    ads_capacity: 20,
    seo_capacity: 30,
    social_capacity: 25,
    default_relevance_coefficient: 1.0,
    keyword_source_filter: null,
    enable_ads: true,
    enable_seo: true,
    enable_social: true,
    keyword_selection_mode: "top_n",
    keyword_limit: 200,
    selected_keyword_ids: undefined,
    skip_relevance: false,
  });

  useEffect(() => {
    fetchRuns();
  }, [activeWorkspace?.id]);

  useEffect(() => {
    if (activeWorkspace?.id) {
      setNewRun((prev) => ({ ...prev, brand_profile_id: activeWorkspace.id }));
    }
  }, [activeWorkspace?.id]);

  const fetchRuns = async () => {
    if (!activeWorkspace?.id) {
      setRuns([]);
      return;
    }
    try {
      const res = await scoringApi.listRuns({ brand_profile_id: activeWorkspace.id });
      const data = res.data || [];
      setRuns(data);
    } catch (error) {
      console.error("Error fetching runs:", error);
    }
  };

  const createRun = async () => {
    if (!activeWorkspace?.id) {
      setErrorMsg("Önce Marka Çalışması seçin");
      return;
    }
    if (!newRun.enable_ads && !newRun.enable_seo && !newRun.enable_social) {
      setErrorMsg("En az bir kanal seçilmelidir");
      return;
    }
    setErrorMsg(null);
    try {
      const res = await scoringApi.createRun({
        ...newRun,
        brand_profile_id: activeWorkspace.id,
      });
      setShowModal(false);
      setNewRun((prev) => ({
        ...prev,
        run_name: "",
        brand_profile_id: activeWorkspace?.id,
        ads_capacity: 20,
        seo_capacity: 30,
        social_capacity: 25,
        default_relevance_coefficient: 1.0,
        keyword_source_filter: null,
        enable_ads: true,
        enable_seo: true,
        enable_social: true,
        keyword_selection_mode: "top_n",
        keyword_limit: 200,
        selected_keyword_ids: undefined,
        skip_relevance: false,
      }));
      fetchRuns();
      setSelectedRun(res.data.id);
    } catch (error) {
      console.error("Error creating run:", error);
    }
  };

  const executeRun = async (runId: number) => {
    if (!activeWorkspace?.id) return;
    setLoading(true);
    try {
      await scoringApi.executeRun(runId, activeWorkspace.id);
      fetchRuns();
      viewScores(runId);
    } catch (error) {
      console.error("Error executing run:", error);
    }
    setLoading(false);
  };

  const viewScores = async (runId: number) => {
    if (!activeWorkspace?.id) return;
    setSelectedRun(runId);
    try {
      const res = await scoringApi.getScores(runId, 50, activeWorkspace.id);
      setScores(res.data.scores || []);
    } catch (error) {
      console.error("Error fetching scores:", error);
    }
  };

  const formatScore = (value: number | string | null | undefined) => {
    if (value === null || value === undefined || value === "") return "-";
    return Number(value).toFixed(4);
  };

  return (
    <div className="scoring-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Skorlama</h1>
          <p>Anahtar kelime skorlama işlemleri</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>
          <TrendingUp size={18} />
          Yeni Skorlama
        </button>
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

      <div className="runs-section">
        <h2>Skorlama Çalışmaları</h2>
        <div className="runs-grid">
          {runs.length === 0 ? (
            <p className="empty-text">Henüz skorlama çalışması yok</p>
          ) : (
            runs.map((run) => (
              <div
                key={run.id}
                className={`run-card glass-card ${selectedRun === run.id ? "active" : ""}`}
              >
                <div className="run-header">
                  <span className="run-name">
                    {run.run_name || `Çalışma #${run.id}`}
                    {run.keyword_source_filter && (
                      <span
                        className={`source-badge source-badge--${run.keyword_source_filter === "google_ads_api" ? "ads" : "csv"}`}
                      >
                        {run.keyword_source_filter === "google_ads_api"
                          ? "Google Ads"
                          : "CSV"}
                      </span>
                    )}
                  </span>
                  <span className={`status status-${run.status}`}>
                    {run.status}
                  </span>
                </div>
                <div className="run-capacities">
                  <span className={`badge badge-ads ${run.enable_ads === false ? "badge-muted" : ""}`}>
                    ADS: {run.ads_capacity}
                  </span>
                  <span className={`badge badge-seo ${run.enable_seo === false ? "badge-muted" : ""}`}>
                    SEO: {run.seo_capacity}
                  </span>
                  <span className={`badge badge-social ${run.enable_social === false ? "badge-muted" : ""}`}>
                    SOCIAL: {run.social_capacity}
                  </span>
                  <span className="badge badge-social">
                    Katsayı:{" "}
                    {typeof run.default_relevance_coefficient === "number"
                      ? run.default_relevance_coefficient.toFixed(2)
                      : Number(run.default_relevance_coefficient || 1).toFixed(
                          2,
                        )}
                  </span>
                </div>
                <div className="run-actions">
                  {run.status === "pending" && (
                    <button
                      className="btn btn-success"
                      onClick={() => executeRun(run.id)}
                      disabled={loading}
                    >
                      <Play size={16} />
                      {loading ? "Çalışıyor..." : "Çalıştır"}
                    </button>
                  )}

                  {[
                    "scored",
                    "relevance_computing",
                    "relevance_computed",
                    "channel_assigned",
                    "completed",
                  ].includes(run.status) && (
                    <>
                      <button
                        className="btn btn-secondary"
                        onClick={() => viewScores(run.id)}
                      >
                        <Eye size={16} />
                        Görüntüle
                      </button>
                      <button
                        className="btn btn-success"
                        onClick={async () => {
                          try {
                            const res = await scoringApi.exportXlsx(run.id, activeWorkspace?.id);
                            const url = window.URL.createObjectURL(
                              new Blob([res.data]),
                            );
                            const link = document.createElement("a");
                            link.href = url;
                            link.setAttribute(
                              "download",
                              `scoring_run_${run.id}.xlsx`,
                            );
                            document.body.appendChild(link);
                            link.click();
                            link.remove();
                          } catch (err) {
                            console.error("Export error:", err);
                          }
                        }}
                      >
                        <Download size={16} />
                        Excel
                      </button>
                      <button
                        className="btn btn-primary"
                        onClick={() =>
                          navigate(`/brand-profile?run_id=${run.id}`)
                        }
                      >
                        Sonraki
                        <ArrowRight size={16} />
                      </button>
                    </>
                  )}

                  <button
                    className="btn btn-danger"
                    onClick={async () => {
                      if (
                        !confirm(
                          "Bu skorlama çalışmasını silmek istediğinize emin misiniz?",
                        )
                      )
                        return;
                      try {
                        await scoringApi.deleteRun(run.id, activeWorkspace?.id);
                        fetchRuns();
                        if (selectedRun === run.id) {
                          setSelectedRun(null);
                          setScores([]);
                        }
                      } catch (err) {
                        console.error("Delete error:", err);
                      }
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {selectedRun && scores.length > 0 && (
        <div className="scores-section">
          <h2>Skorlar - Çalışma #{selectedRun}</h2>
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Anahtar Kelime</th>
                  <th>ADS Skor</th>
                  <th>SEO Skor</th>
                  <th>SOCIAL Skor</th>
                  <th>ADS Sira</th>
                  <th>SEO Sira</th>
                  <th>SOCIAL Sira</th>
                </tr>
              </thead>
              <tbody>
                {scores.map((score) => (
                  <tr key={score.keyword_id}>
                    <td>
                      <strong>{score.keyword}</strong>
                    </td>
                    <td>{formatScore(score.ads_score)}</td>
                    <td>{formatScore(score.seo_score)}</td>
                    <td>{formatScore(score.social_score)}</td>
                    <td>
                      {score.ads_rank && (
                        <span className={score.ads_rank <= 3 ? "top-rank" : ""}>
                          {score.ads_rank <= 3 && <Award size={14} />}#
                          {score.ads_rank}
                        </span>
                      )}
                    </td>
                    <td>
                      {score.seo_rank && (
                        <span className={score.seo_rank <= 3 ? "top-rank" : ""}>
                          {score.seo_rank <= 3 && <Award size={14} />}#
                          {score.seo_rank}
                        </span>
                      )}
                    </td>
                    <td>
                      {score.social_rank && (
                        <span
                          className={score.social_rank <= 3 ? "top-rank" : ""}
                        >
                          {score.social_rank <= 3 && <Award size={14} />}#
                          {score.social_rank}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div
            className="modal glass-card"
            onClick={(e) => e.stopPropagation()}
          >
            <h2>Yeni Skorlama Çalışması</h2>

            <div className="form-group">
              <label>Skorlanacak Kanallar</label>
              <div className="channel-toggle-grid">
                {[
                  ["enable_ads", "ADS"],
                  ["enable_seo", "SEO"],
                  ["enable_social", "SOCIAL"],
                ].map(([key, label]) => (
                  <label
                    key={key}
                    className={`channel-toggle ${newRun[key as "enable_ads" | "enable_seo" | "enable_social"] ? "active" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={
                        newRun[
                          key as "enable_ads" | "enable_seo" | "enable_social"
                        ]
                      }
                      onChange={(e) =>
                        setNewRun({
                          ...newRun,
                          [key]: e.target.checked,
                        })
                      }
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label>Çalışma Adı</label>
              <input
                className="input"
                value={newRun.run_name}
                onChange={(e) =>
                  setNewRun({ ...newRun, run_name: e.target.value })
                }
                placeholder="Mart 2026 Skorlama"
              />
            </div>

            <div className="form-group">
              <label>ADS Kapasitesi</label>
              <input
                type="number"
                className="input"
                title="ADS için İstenen Keyword Sayısı"
                placeholder="20"
                value={newRun.ads_capacity}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    ads_capacity: parseInt(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>SEO Kapasitesi</label>
              <input
                type="number"
                className="input"
                title="SEO için İstenen Keyword Sayısı"
                placeholder="30"
                value={newRun.seo_capacity}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    seo_capacity: parseInt(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>SOCIAL Kapasitesi</label>
              <input
                type="number"
                className="input"
                title="SOCIAL için İstenen Keyword Sayısı"
                placeholder="25"
                value={newRun.social_capacity}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    social_capacity: parseInt(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>Varsayılan İlgi Katsayısı (0.1 - 3.0)</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="3"
                className="input"
                title="İlgi skoru ile kanal skorunu birleştirmede kullanılır"
                placeholder="1.0"
                value={newRun.default_relevance_coefficient ?? 1.0}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    default_relevance_coefficient: parseFloat(e.target.value),
                  })
                }
              />
            </div>

            <div className="form-group">
              <label>Keyword Kaynağı</label>
              <select
                className="input"
                title="Keyword Kaynağı"
                value={newRun.keyword_source_filter ?? ""}
                onChange={(e) =>
                  setNewRun({
                    ...newRun,
                    keyword_source_filter:
                      e.target.value === ""
                        ? null
                        : (e.target.value as "csv" | "google_ads_api"),
                  })
                }
              >
                <option value="">Tüm Kaynaklar (CSV + Google Ads)</option>
                <option value="csv">Sadece CSV</option>
                <option value="google_ads_api">Sadece Google Ads API</option>
              </select>
            </div>

            {errorMsg && <div className="modal-error">{errorMsg}</div>}
            <div className="modal-actions">
              <button
                className="btn btn-secondary"
                onClick={() => setShowModal(false)}
              >
                İptal
              </button>
              <button className="btn btn-primary" onClick={createRun}>
                Oluştur
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
