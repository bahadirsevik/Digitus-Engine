import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import {
  Brain,
  CheckCircle2,
  Globe,
  RefreshCw,
  ArrowRight,
  Plus,
  Archive,
  RotateCcw,
  Settings2,
  AlertCircle,
} from "lucide-react";
import { useBrandStore } from "../stores/brandStore";
import {
  workspaceApi,
  brandProfileApi,
  WorkspaceResponse,
  ProfileConfirmRequest,
} from "../services/api";
import "./BrandProfile.css";

interface ProfileFormState {
  company_name: string;
  sector: string;
  target_audience: string;
  products: string;
  services: string;
  use_cases: string;
  problems_solved: string;
  brand_terms: string;
  exclude_themes: string;
  anchor_texts: string;
}

const ADVANCED_ANCHOR_MODE_KEY = "brand_profile_anchor_advanced";

const LIST_FIELD_CONFIG = [
  { key: "products", label: "Ürünler" },
  { key: "services", label: "Hizmetler" },
  { key: "use_cases", label: "Kullanım Alanları" },
  { key: "problems_solved", label: "Çözülen Problemler" },
  { key: "brand_terms", label: "Marka Terimleri" },
  { key: "exclude_themes", label: "Dışlanacak Temalar" },
] as const;

function emptyProfileForm(): ProfileFormState {
  return {
    company_name: "",
    sector: "",
    target_audience: "",
    products: "",
    services: "",
    use_cases: "",
    problems_solved: "",
    brand_terms: "",
    exclude_themes: "",
    anchor_texts: "",
  };
}

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item)
            return String((item as any).msg);
          return JSON.stringify(item);
        })
        .join(" | ");
    }
    return error.message;
  }
  return "Beklenmeyen hata";
}

function listToTextarea(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean)
    .join("\n");
}

function splitNewlineItems(raw: string): string[] {
  return raw
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function profileToForm(
  profileData?: Record<string, unknown>,
): ProfileFormState {
  const form = emptyProfileForm();
  if (!profileData) return form;

  const readString = (key: string): string => {
    const value = profileData[key];
    return typeof value === "string" ? value : "";
  };

  return {
    company_name: readString("company_name"),
    sector: readString("sector"),
    target_audience: readString("target_audience"),
    products: listToTextarea(profileData.products),
    services: listToTextarea(profileData.services),
    use_cases: listToTextarea(profileData.use_cases),
    problems_solved: listToTextarea(profileData.problems_solved),
    brand_terms: listToTextarea(profileData.brand_terms),
    exclude_themes: listToTextarea(profileData.exclude_themes),
    anchor_texts: listToTextarea(profileData.anchor_texts),
  };
}

function buildProfilePayload(
  form: ProfileFormState,
  includeAnchorOverride: boolean,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};

  LIST_FIELD_CONFIG.forEach(({ key }) => {
    payload[key] = splitNewlineItems(form[key]);
  });

  if (includeAnchorOverride) {
    payload.anchor_texts = splitNewlineItems(form.anchor_texts);
  }

  return payload;
}

function hasGeneratedProfile(workspace: { profile_data: any; status: string }) {
  const profile = workspace.profile_data;
  if (!profile || typeof profile !== "object") return false;
  const anchors = Array.isArray(profile.anchor_texts)
    ? profile.anchor_texts.filter((item: unknown) => String(item || "").trim())
    : [];
  return workspace.status === "confirmed" && anchors.length > 0;
}

interface WorkspaceListRow {
  id: number;
  name: string;
  company_url: string;
  status: string;
  profile_data: any;
  suggested_keywords?: string[] | null;
  default_geo_target_id?: string | null;
  default_language_id?: string | null;
  deleted_at: string | null;
  created_at: string;
  run_count: number;
}

// ─── New Workspace Modal ──────────────────────────────

function CreateWorkspaceModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: () => void;
}) {
  const [name, setName] = useState("");
  const [companyUrl, setCompanyUrl] = useState("");
  const [competitor1, setCompetitor1] = useState("");
  const [competitor2, setCompetitor2] = useState("");
  const [competitor3, setCompetitor3] = useState("");
  const [preliminaryInfo, setPreliminaryInfo] = useState("");
  const [geoId, setGeoId] = useState("2792");
  const [languageId, setLanguageId] = useState("1037");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async () => {
    if (!name.trim() || !companyUrl.trim()) return;
    setLoading(true);
    setError("");
    try {
      const competitorUrls = [competitor1, competitor2, competitor3]
        .map((c) => c.trim())
        .filter(Boolean);
      await workspaceApi.create({
        name: name.trim(),
        company_url: companyUrl.trim(),
        competitor_urls: competitorUrls.length > 0 ? competitorUrls : undefined,
        preliminary_info: preliminaryInfo.trim() || undefined,
        default_geo_target_id: geoId,
        default_language_id: languageId,
      });
      onCreate();
      onClose();
    } catch (err: unknown) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <h2>Yeni Çalışma Oluştur</h2>

        <div className="form-group">
          <label>Çalışma adı *</label>
          <input
            type="text"
            placeholder='örn. "Vepa Saç Bakım Q1 2026"'
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Şirket URL *</label>
          <input
            type="text"
            placeholder="https://..."
            value={companyUrl}
            onChange={(e) => setCompanyUrl(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Rakip URL'leri (max 3)</label>
          <input
            type="text"
            placeholder="Rakip 1"
            value={competitor1}
            onChange={(e) => setCompetitor1(e.target.value)}
          />
          <input
            type="text"
            placeholder="Rakip 2"
            value={competitor2}
            onChange={(e) => setCompetitor2(e.target.value)}
          />
          <input
            type="text"
            placeholder="Rakip 3"
            value={competitor3}
            onChange={(e) => setCompetitor3(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Ön bilgi</label>
          <textarea
            rows={3}
            placeholder="Markanızın vizyonu, hedef kitlesi, benzersiz satış noktaları..."
            value={preliminaryInfo}
            onChange={(e) => setPreliminaryInfo(e.target.value)}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>Geo Target</label>
            <select value={geoId} onChange={(e) => setGeoId(e.target.value)}>
              <option value="2792">Türkiye (2792)</option>
              <option value="2276">Almanya (2276)</option>
            </select>
          </div>
          <div className="form-group">
            <label>Dil</label>
            <select
              value={languageId}
              onChange={(e) => setLanguageId(e.target.value)}
            >
              <option value="1037">Türkçe (1037)</option>
              <option value="1001">Almanca (1001)</option>
              <option value="1000">İngilizce (1000)</option>
            </select>
          </div>
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>
            İptal
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={loading || !name.trim() || !companyUrl.trim()}
          >
            {loading ? "Oluşturuluyor..." : "Oluştur"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Workspace Detail Panel ──────────────────────────

function WorkspaceDetail({
  workspace,
  onArchive,
  onRestore,
  onConfirm,
  onRefresh,
}: {
  workspace: WorkspaceListRow & {
    suggested_keywords?: string[] | null;
    preliminary_info?: string;
  };
  onArchive: () => void;
  onRestore: () => void;
  onConfirm: () => void;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const setActiveWorkspace = useBrandStore((s) => s.setActiveWorkspace);

  const [profileForm, setProfileForm] =
    useState<ProfileFormState>(emptyProfileForm());
  const [advancedAnchorMode, setAdvancedAnchorMode] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(ADVANCED_ANCHOR_MODE_KEY) === "true";
  });
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (workspace.profile_data) {
      setProfileForm(profileToForm(workspace.profile_data));
    }
  }, [workspace.profile_data]);

  const handleConfirm = async () => {
    setConfirming(true);
    setError("");
    try {
      const payload = buildProfilePayload(profileForm, advancedAnchorMode);
      await workspaceApi.confirm(workspace.id, {
        profile_data: payload,
      } as ProfileConfirmRequest);
      onConfirm();
    } catch (err: unknown) {
      setError(extractErrorMessage(err));
    } finally {
      setConfirming(false);
    }
  };

  const handleGoKeywords = () => {
    if (!hasGeneratedProfile(workspace)) {
      setError(
        "Anahtar kelimelere gecmeden once sirket ozellikleri cikarilmali ve profil onaylanmali.",
      );
      return;
    }
    setActiveWorkspace({
      id: workspace.id,
      name: workspace.name,
      company_url: workspace.company_url,
      status: workspace.status,
      profile_data: workspace.profile_data,
      suggested_keywords: workspace.suggested_keywords || null,
      default_geo_target_id: workspace.default_geo_target_id || null,
      default_language_id: workspace.default_language_id || null,
    });
    navigate("/keywords?tab=google-ads");
  };

  const isArchived = !!workspace.deleted_at;
  const isDraft = workspace.status === "draft";
  const isConfirmed = workspace.status === "confirmed";
  const canContinueToKeywords = hasGeneratedProfile(workspace);

  return (
    <div className="workspace-detail">
      <div className="detail-header">
        <div>
          <h2>{workspace.name}</h2>
          <p className="detail-url">{workspace.company_url}</p>
        </div>
        <div className="detail-actions">
          <span className={`status-badge status-${workspace.status}`}>
            {workspace.status}
          </span>
          {!isArchived && (
            <>
              <button
                className="btn btn-secondary"
                onClick={handleGoKeywords}
                disabled={!canContinueToKeywords}
                title={
                  canContinueToKeywords
                    ? ""
                    : "Devam etmek icin once sirket ozellikleri cikarilip profil onaylanmali"
                }
              >
                Anahtar Kelimelere Geç <ArrowRight size={14} />
              </button>
              <button
                className="btn btn-secondary"
                onClick={onArchive}
                title="Arşivle"
              >
                <Archive size={14} />
              </button>
            </>
          )}
          {isArchived && (
            <button className="btn btn-secondary" onClick={onRestore}>
              <RotateCcw size={14} /> Arşivden Çıkar
            </button>
          )}
        </div>
      </div>

      {workspace.suggested_keywords &&
        workspace.suggested_keywords.length > 0 && (
          <div className="suggested-keywords">
            <h4>AI Önerdiği 10 Keyword</h4>
            <div className="chip-list">
              {workspace.suggested_keywords.map((kw, idx) => (
                <span key={idx} className="chip">
                  {kw}
                </span>
              ))}
            </div>
          </div>
        )}

      {error && <div className="error-banner">{error}</div>}

      {!isArchived && !canContinueToKeywords && (
        <div className="disabled-hint">
          <AlertCircle size={14} />
          Anahtar kelimelere gecmeden once sirket ozellikleri cikarilmali ve
          profil onaylanmali.
        </div>
      )}

      {(isDraft || isConfirmed) && workspace.profile_data && (
        <div className="profile-form">
          <div className="form-group">
            <label>Firma Adı</label>
            <input type="text" value={profileForm.company_name} readOnly />
          </div>
          <div className="form-group">
            <label>Sektör</label>
            <input type="text" value={profileForm.sector} readOnly />
          </div>
          <div className="form-group">
            <label>Hedef Kitle</label>
            <input type="text" value={profileForm.target_audience} readOnly />
          </div>

          {LIST_FIELD_CONFIG.map(({ key, label }) => (
            <div className="form-group" key={key}>
              <label>{label}</label>
              <textarea
                rows={3}
                value={profileForm[key as keyof ProfileFormState] as string}
                onChange={(e) =>
                  setProfileForm((f) => ({
                    ...f,
                    [key]: e.target.value,
                  }))
                }
              />
            </div>
          ))}

          <div className="form-group anchor-mode-toggle">
            <button
              className="btn btn-text"
              onClick={() => {
                const next = !advancedAnchorMode;
                setAdvancedAnchorMode(next);
                window.localStorage.setItem(
                  ADVANCED_ANCHOR_MODE_KEY,
                  String(next),
                );
              }}
            >
              <Settings2 size={14} />
              {advancedAnchorMode
                ? "Anchor metinleri (manuel)"
                : "Anchor metinleri (otomatik)"}
            </button>
          </div>

          {advancedAnchorMode && (
            <div className="form-group">
              <label>Anchor Metinleri</label>
              <textarea
                rows={3}
                value={profileForm.anchor_texts}
                onChange={(e) =>
                  setProfileForm((f) => ({
                    ...f,
                    anchor_texts: e.target.value,
                  }))
                }
              />
            </div>
          )}

          {isDraft && (
            <button
              className="btn btn-primary"
              onClick={handleConfirm}
              disabled={confirming}
            >
              <CheckCircle2 size={14} />
              {confirming ? "Onaylanıyor..." : "Profili Onayla"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────

export default function BrandProfile() {
  const [workspaces, setWorkspaces] = useState<WorkspaceListRow[]>([]);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [archivedCount, setArchivedCount] = useState(0);
  const [selectedWs, setSelectedWs] = useState<WorkspaceListRow | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchWorkspaces = useCallback(async () => {
    setLoading(true);
    try {
      const res = await workspaceApi.list(true);
      const allWorkspaces = res.data || [];
      setArchivedCount(
        allWorkspaces.filter((ws: WorkspaceListRow) => !!ws.deleted_at).length,
      );
      setWorkspaces(
        includeArchived
          ? allWorkspaces
          : allWorkspaces.filter((ws: WorkspaceListRow) => !ws.deleted_at),
      );
    } catch (err: unknown) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [includeArchived]);

  useEffect(() => {
    fetchWorkspaces();
  }, [fetchWorkspaces]);

  const handleArchive = async (id: number) => {
    try {
      await workspaceApi.archive(id);
      fetchWorkspaces();
    } catch (err: unknown) {
      setError(extractErrorMessage(err));
    }
  };

  const handleRestore = async (id: number) => {
    try {
      await workspaceApi.restore(id);
      fetchWorkspaces();
    } catch (err: unknown) {
      setError(extractErrorMessage(err));
    }
  };

  const handleSelect = async (id: number) => {
    try {
      const res = await workspaceApi.get(id);
      setSelectedWs({ ...res.data, run_count: 0 });
    } catch (err: unknown) {
      setError(extractErrorMessage(err));
    }
  };

  return (
    <div className="brand-profile-page">
      <div className="page-header">
        <h1>Marka Çalışmaları</h1>
        <button
          className="btn btn-primary"
          onClick={() => setShowCreateModal(true)}
        >
          <Plus size={16} /> Yeni Çalışma Oluştur
        </button>
      </div>

      <div className="filter-bar">
        <label className={`archive-toggle ${includeArchived ? "active" : ""}`}>
          <input
            type="checkbox"
            checked={includeArchived}
            disabled={archivedCount === 0}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          <span className="archive-toggle-track" />
          <span>Arşivlenmiş Çalışmaları Göster</span>
          <strong>{archivedCount}</strong>
        </label>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {loading && <div className="loading-indicator">Yükleniyor...</div>}

      {!loading && workspaces.length === 0 && (
        <div className="empty-state">
          <AlertCircle size={24} />
          <span>Henüz marka çalışması yok. Yeni bir çalışma oluşturun.</span>
        </div>
      )}

      <div className="workspace-grid">
        {workspaces.map((ws) => (
          <div
            key={ws.id}
            className={`workspace-card ${ws.deleted_at ? "archived" : ""}`}
            onClick={() => handleSelect(ws.id)}
          >
            <div className="card-header">
              <h3>{ws.name}</h3>
              <span className={`status-badge status-${ws.status}`}>
                {ws.status}
              </span>
            </div>
            <p className="card-url">
              <Globe size={12} /> {ws.company_url}
            </p>
            <p className="card-sector">
              {ws.profile_data?.sector || "Sektör bilgisi yok"}
            </p>
            <div className="card-footer">
              <span>{ws.run_count} analiz</span>
              <span>{new Date(ws.created_at).toLocaleDateString("tr")}</span>
            </div>
          </div>
        ))}
      </div>

      {selectedWs && (
        <>
          <div className="detail-overlay" onClick={() => setSelectedWs(null)} />
          <div className="detail-panel">
            <button
              className="detail-close"
              onClick={() => setSelectedWs(null)}
            >
              ✕
            </button>
            <WorkspaceDetail
              workspace={selectedWs}
              onArchive={() => {
                handleArchive(selectedWs.id);
                setSelectedWs(null);
              }}
              onRestore={() => {
                handleRestore(selectedWs.id);
                setSelectedWs(null);
              }}
              onConfirm={fetchWorkspaces}
              onRefresh={fetchWorkspaces}
            />
          </div>
        </>
      )}

      <CreateWorkspaceModal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreate={fetchWorkspaces}
      />
    </div>
  );
}
