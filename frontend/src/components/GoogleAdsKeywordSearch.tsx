import { useState } from "react";
import { Search, AlertCircle, X } from "lucide-react";
import { useBrandStore } from "../stores/brandStore";
import { googleAdsApi, EnrichedKeywordOut } from "../services/api";
import "./GoogleAdsKeywordSearch.css";

interface Props {
  onImport: (keywords: EnrichedKeywordOut[]) => void;
}

export default function GoogleAdsKeywordSearch({ onImport }: Props) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace);

  const [customerId, setCustomerId] = useState("");
  const [seeds, setSeeds] = useState("");
  const [maxResults, setMaxResults] = useState(100);
  const [minVolume, setMinVolume] = useState(0);
  const [languageId, setLanguageId] = useState(
    activeWorkspace?.profile_data?.default_language_id || "1055",
  );
  const [geoId, setGeoId] = useState(
    activeWorkspace?.profile_data?.default_geo_target_id || "2792",
  );
  const [ideas, setIdeas] = useState<EnrichedKeywordOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAutoFill, setShowAutoFill] = useState(
    !!activeWorkspace?.suggested_keywords?.length,
  );

  const handleAutoFill = () => {
    if (activeWorkspace?.suggested_keywords?.length) {
      setSeeds(activeWorkspace.suggested_keywords.join("\n"));
      setShowAutoFill(false);
    }
  };

  const handleSearch = async () => {
    if (!customerId || !seeds.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const seedList = seeds
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await googleAdsApi.enrich({
        customer_id: customerId,
        seeds: seedList,
        max_results: maxResults,
        min_volume: minVolume,
        language_id: languageId,
        geo_target_id: geoId,
      });
      setIdeas(res.data.keywords || []);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail || "Google Ads araması başarısız oldu",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleImport = () => {
    onImport(ideas);
  };

  return (
    <div className="google-ads-component">
      {activeWorkspace && showAutoFill && (
        <div className="autofill-info">
          <AlertCircle size={14} />
          <span>
            Marka profilinden {activeWorkspace.suggested_keywords?.length}{" "}
            kelime önerisi yüklendi
          </span>
          <button className="autofill-btn" onClick={handleAutoFill}>
            Seed'e Yükle
          </button>
          <button
            className="autofill-close"
            onClick={() => setShowAutoFill(false)}
          >
            <X size={12} />
          </button>
        </div>
      )}

      <div className="search-form">
        <div className="form-group">
          <label>Müşteri Google Ads Hesabı</label>
          <input
            type="text"
            placeholder="xxx-xxx-xxxx"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
          />
        </div>

        <div className="form-group full-width">
          <label>Seed Keywords</label>
          <textarea
            rows={5}
            placeholder="Her satıra bir kelime..."
            value={seeds}
            onChange={(e) => setSeeds(e.target.value)}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>Dil</label>
            <input
              type="text"
              value={languageId}
              onChange={(e) => setLanguageId(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label>Geo Target</label>
            <input
              type="text"
              value={geoId}
              onChange={(e) => setGeoId(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label>Min Volume</label>
            <input
              type="number"
              value={minVolume}
              onChange={(e) => setMinVolume(Number(e.target.value))}
            />
          </div>
          <div className="form-group">
            <label>Max Sonuç</label>
            <input
              type="number"
              value={maxResults}
              onChange={(e) => setMaxResults(Number(e.target.value))}
            />
          </div>
        </div>

        <button
          className="btn btn-primary"
          onClick={handleSearch}
          disabled={loading || !customerId || !seeds.trim()}
        >
          <Search size={16} />
          {loading ? "Aranıyor..." : "Ara"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {ideas.length > 0 && (
        <>
          <div className="preview-header">
            <span>{ideas.length} keyword bulundu</span>
            <button className="btn btn-primary" onClick={handleImport}>
              Sisteme Aktar
            </button>
          </div>
          <div className="preview-table">
            <table>
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>Aylık Arama</th>
                  <th>Trend 3M</th>
                  <th>Trend 12M</th>
                  <th>Rekabet</th>
                  <th>CPC</th>
                </tr>
              </thead>
              <tbody>
                {ideas.map((idea, idx) => (
                  <tr key={idx}>
                    <td>{idea.keyword}</td>
                    <td>{idea.avg_monthly_searches}</td>
                    <td>{idea.trend_3m}</td>
                    <td>{idea.trend_12m}</td>
                    <td>{idea.competition_score}</td>
                    <td>
                      {idea.cpc_low}-{idea.cpc_high}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
