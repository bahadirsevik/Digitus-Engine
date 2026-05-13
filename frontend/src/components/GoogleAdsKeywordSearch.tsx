import { useEffect, useState } from "react";
import { CheckCircle, Search } from "lucide-react";
import { useBrandStore } from "../stores/brandStore";
import { googleAdsApi, EnrichedKeywordOut } from "../services/api";
import "./GoogleAdsKeywordSearch.css";

interface Props {
  onImport: (keywords: EnrichedKeywordOut[]) => void;
}

const normalizeGoogleAdsLanguageId = (languageId?: string | null) =>
  languageId === "1055" ? "1037" : languageId || "1037";

export default function GoogleAdsKeywordSearch({ onImport }: Props) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace);

  const [customerId, setCustomerId] = useState("");
  const [customers, setCustomers] = useState<string[]>([]);
  const [seeds, setSeeds] = useState("");
  const [maxResults, setMaxResults] = useState(100);
  const [minVolume, setMinVolume] = useState(0);
  const [languageId, setLanguageId] = useState(
    normalizeGoogleAdsLanguageId(activeWorkspace?.default_language_id),
  );
  const [geoId, setGeoId] = useState(
    activeWorkspace?.default_geo_target_id || "2792",
  );
  const [ideas, setIdeas] = useState<EnrichedKeywordOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const suggestedSeeds =
    activeWorkspace?.suggested_keywords
      ?.map((kw) => kw.trim())
      .filter(Boolean)
      .slice(0, 10) || [];
  const suggestedSeedText = suggestedSeeds.join("\n");

  useEffect(() => {
    const loadCustomers = async () => {
      try {
        const res = await googleAdsApi.listCustomers();
        const ids = (res.data || []).map((item) => item.customer_id);
        setCustomers(ids);
        if (!customerId && ids.length === 1) setCustomerId(ids[0]);
      } catch {
        setCustomers([]);
      }
    };
    loadCustomers();
  }, []);

  useEffect(() => {
    setLanguageId(normalizeGoogleAdsLanguageId(activeWorkspace?.default_language_id));
    setGeoId(activeWorkspace?.default_geo_target_id || "2792");
    setSeeds(suggestedSeedText);
    setIdeas([]);
    setError(null);
  }, [
    activeWorkspace?.id,
    activeWorkspace?.default_language_id,
    activeWorkspace?.default_geo_target_id,
    suggestedSeedText,
  ]);

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
      {activeWorkspace && suggestedSeeds.length > 0 && (
        <div className="autofill-info">
          <CheckCircle size={15} />
          <span>
            Marka profilinden {suggestedSeeds.length} seed kelime yüklendi
          </span>
        </div>
      )}

      <div className="search-form">
        <div className="form-group">
          <label>Müşteri Google Ads Hesabı</label>
          {customers.length > 0 ? (
            <select
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
            >
              <option value="">Hesap seçin</option>
              {customers.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder="xxx-xxx-xxxx"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
            />
          )}
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
