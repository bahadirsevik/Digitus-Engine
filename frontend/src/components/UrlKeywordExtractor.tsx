import { useState } from "react";
import { Globe, RefreshCw, Info } from "lucide-react";
import { useBrandStore } from "../stores/brandStore";
import { googleAdsUrlSeedApi, UrlSeedIdea } from "../services/api";

interface Props {
  onImport: (keywords: UrlSeedIdea[]) => void;
}

export default function UrlKeywordExtractor({ onImport }: Props) {
  const activeWorkspace = useBrandStore((s) => s.activeWorkspace);

  const [url, setUrl] = useState(activeWorkspace?.company_url || "");
  const [maxResults, setMaxResults] = useState(300);
  const [minVolume, setMinVolume] = useState(0);
  const [includeSeed, setIncludeSeed] = useState(false);
  const [ideas, setIdeas] = useState<UrlSeedIdea[]>([]);
  const [total, setTotal] = useState(0);
  const [cached, setCached] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExtract = async () => {
    if (!url.trim() || !activeWorkspace?.id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await googleAdsUrlSeedApi.keywordIdeasByUrl({
        url: url.trim(),
        brand_profile_id: activeWorkspace.id,
        max_results: maxResults,
        min_volume: minVolume,
        include_keyword_seed: includeSeed,
        language_id: activeWorkspace.default_language_id || "1055",
        geo_target_id: activeWorkspace.default_geo_target_id || "2792",
      });
      const data = res.data;
      setIdeas(data.ideas || []);
      setTotal(data.total);
      setCached(data.cached);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail || "URL'den keyword çıkarma başarısız oldu",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleImport = () => {
    onImport(ideas);
  };

  return (
    <div className="url-extractor">
      <div className="search-form">
        <div className="form-group full-width">
          <label>URL</label>
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={activeWorkspace?.company_url || "https://..."}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>Max Sonuç</label>
            <input
              type="number"
              value={maxResults}
              onChange={(e) => setMaxResults(Number(e.target.value))}
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
          <div className="form-group checkbox-group">
            <label>
              <input
                type="checkbox"
                checked={includeSeed}
                onChange={(e) => setIncludeSeed(e.target.checked)}
              />
              Sayfadaki kelimeler de seed olsun
            </label>
          </div>
        </div>

        <button
          className="btn btn-primary"
          onClick={handleExtract}
          disabled={loading || !url.trim() || !activeWorkspace?.id}
        >
          <Globe size={16} />
          {loading ? "Çıkarılıyor..." : "URL'den Çıkar"}
        </button>
      </div>

      {cached && (
        <div className="cache-badge">
          <Info size={14} />
          Cache'den geldi — 24 saat içinde yenilenmez
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {ideas.length > 0 && (
        <>
          <div className="preview-header">
            <span>
              {ideas.length} / {total} keyword bulundu
              {cached && " (cache)"}
            </span>
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
                </tr>
              </thead>
              <tbody>
                {ideas.map((idea, idx) => (
                  <tr key={idx}>
                    <td>{idea.keyword}</td>
                    <td>{idea.monthly_volume}</td>
                    <td>{idea.trend_3m}</td>
                    <td>{idea.trend_12m}</td>
                    <td>{idea.competition}</td>
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
