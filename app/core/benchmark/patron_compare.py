"""Patron-veri resmî karşılaştırmasının çekirdeği (Codex v17+v18).

Script (scripts/compare_patron_run.py) ince CLI'dır; kapı kontrolleri,
metrik hesapları, neden teşhisi ve rapor kurulumu BURADA yaşar ki canned
testlerle kilitlenebilsin (drift_metrics/annotation_io emsali).

İki kapı katmanı:
- artifact_gate_problems: veri bütünlüğü (ws, SHA, 540/540 evren, manifest
  varlığı, tarih sırası).
- run_design_problems (v18-1): KİLİTLİ koşu tasarımı — status, kapasiteler
  (artefakttaki patron sayıları), selection mode 'all', üç kanal açık,
  skip_relevance=false, TÜM evrende relevance, dispatch-anı policy
  snapshot'ı (exclude_themes boş, topic boş, rakip koruması açık ve
  fintables onaylı), prompt_config_version mevcut.
Resmî rapor, İKİ liste de boş olmadan üretilmez.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Set

CHANNELS = {"ADS": "ads", "SEO": "seo", "SOCIAL": "social"}
TERMINAL = ("COMPETITOR_TERM", "BRAND_EXCLUDED_THEME", "PRICE_TERM")


# ── Metrikler ──

def compute_metrics(positives: Set[str], negatives: Set[str],
                    system: Set[str]) -> Dict[str, float]:
    """TN dahil tam ikili metrik seti (Codex v18-2)."""
    tp = len(system & positives)
    fp = len(system & negatives)
    fn = len(positives - system)
    tn = len(negatives - system)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None
          and (precision + recall) else None)
    union = system | positives
    jaccard = (len(system & positives) / len(union)) if union else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1,
            "jaccard": jaccard}


# ── Kapılar ──

def artifact_gate_problems(run, doc: dict, run_norms: Set[str],
                           universe_norms: Set[str],
                           final_xlsx_sha: str) -> List[str]:
    problems = []
    if run.brand_profile_id != doc["workspace_id"]:
        problems.append(f"workspace: run={run.brand_profile_id} "
                        f"artefakt={doc['workspace_id']}")
    if final_xlsx_sha != doc["source_final_xlsx_sha256"]:
        problems.append("kaynak xlsx SHA değişmiş — artefakt bayat")
    missing = universe_norms - run_norms
    extra = run_norms - universe_norms
    if missing or extra:
        problems.append(f"evren uyuşmazlığı: run'da eksik={len(missing)} "
                        f"fazla={len(extra)} "
                        f"({len(universe_norms)}/{len(universe_norms)} şart)")
    manifest = run.execution_manifest or {}
    if not manifest.get("git_sha") or not manifest.get("model"):
        problems.append("run manifest'i eksik (git_sha/model)")
    imported_at = doc.get("imported_at", "")
    if run.created_at is None or run.created_at.isoformat() < imported_at:
        problems.append(f"run ({run.created_at}) veri setinden "
                        f"({imported_at}) ESKİ")
    return problems


def run_design_problems(run, doc: dict,
                        relevance_kids: Set[int],
                        score_kids: Set[int]) -> List[str]:
    """Kilitli baseline tasarımının doğrulanması (Codex v18-1 + v19)."""
    problems = []
    counts = doc.get("counts", {})
    expected_caps = {"ADS": counts.get("ads"), "SEO": counts.get("seo"),
                     "SOCIAL": counts.get("social")}
    universe_size = counts.get("clean_universe_units", 0)

    if run.status != "channel_assigned":
        problems.append(f"status={run.status!r} (channel_assigned şart)")
    actual_caps = {"ADS": run.ads_capacity, "SEO": run.seo_capacity,
                   "SOCIAL": run.social_capacity}
    for ch, expected in expected_caps.items():
        if actual_caps[ch] != expected:
            problems.append(
                f"{ch} kapasitesi {actual_caps[ch]} (patron sayısı "
                f"{expected} şart)")
    mode = getattr(run, "keyword_selection_mode", None)
    if mode != "all":  # v19-2: None da GEÇEMEZ — sözleşme açıkça 'all'
        problems.append(f"keyword_selection_mode={mode!r} ('all' şart)")
    for ch, flag in (("ADS", run.enable_ads), ("SEO", run.enable_seo),
                     ("SOCIAL", run.enable_social)):
        if not flag:
            problems.append(f"{ch} kanalı kapalı (üçü de açık şart)")
    if getattr(run, "skip_relevance", False):
        problems.append("skip_relevance=true (false şart)")
    # v19-4: SAYI değil KİMLİK eşitliği — ilgisiz relevance satırı eksik
    # benchmark kelimesini maskeleyemez
    if relevance_kids != score_kids:
        missing = len(score_kids - relevance_kids)
        extra = len(relevance_kids - score_kids)
        problems.append(
            f"relevance kimlik kapsaması bozuk: eksik={missing} "
            f"fazla={extra} (skorlanan {len(score_kids)} kelimenin "
            f"TAMAMI, yalnız onlar şart)")
    if len(score_kids) != universe_size:
        problems.append(f"skorlanan kelime {len(score_kids)} != evren "
                        f"{universe_size}")

    manifest = run.execution_manifest or {}
    snapshot = manifest.get("policy_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        problems.append("dispatch-anı policy_snapshot yok "
                        "(manifest'e kaydedilmeli)")
        return problems
    if snapshot.get("profile_exclude_themes"):
        problems.append(f"dispatch anında exclude_themes DOLU: "
                        f"{snapshot['profile_exclude_themes'][:3]}")
    if snapshot.get("topic_terms_approved"):
        problems.append(f"dispatch anında topic exclusion DOLU: "
                        f"{snapshot['topic_terms_approved'][:3]}")
    competitors = snapshot.get("competitor_terms_approved") or []
    if not any("fintables" in t for t in competitors):
        problems.append("rakip koruması: 'fintables' onaylı terimlerde YOK")
    # v19-3: boş/eksik politika GEÇEMEZ — üç kanal anahtarı da mevcut ve
    # 'block' olmak ZORUNDA (boş {} sahte-temiz görünüyordu)
    policy = snapshot.get("competitor_channel_policy") or {}
    bad = [ch for ch in ("ads", "seo", "social")
           if policy.get(ch) != "block"]
    if bad:
        problems.append(f"rakip politikası eksik/block-değil: {bad}")
    if not manifest.get("prompt_config_version"):
        problems.append("prompt_config_version manifest'te yok")
    return problems


# ── Neden teşhisi ──

def diagnose(kid: Optional[int], norm: str, channel: str, state,
             adj_rank: Dict[int, int], detail: Dict[int, dict],
             capacity: int, pool_size: int) -> tuple:
    d = detail.get(kid, {})
    base = (f"skor={d.get('raw', 0):.1f} raw_rank={d.get('raw_rank')} "
            f"rel={d.get('rel', 0):.2f} adj_rank={adj_rank.get(kid)}")
    if norm in state.pools[channel]:
        return "final_havuzda", base
    window = min(pool_size, capacity * 3)
    if kid not in state.candidates[channel]:
        ar = adj_rank.get(kid, 10 ** 6)
        rr = d.get("raw_rank") or 10 ** 6
        if ar <= window:
            return "pencere_ici_anomali", base
        if rr <= window:
            return "relevance_sira_kaybi", base
        return "ham_skor_dusuk", base
    pf = state.prefilters[channel].get(kid)
    reason = ((pf.extra_data or {}).get("reason_code") if pf else None)
    if reason == "COMPETITOR_TERM":
        return "rakip_hard_block", base
    if reason in TERMINAL:
        return f"policy_blok({reason})", base
    ia = state.intents[channel].get(kid)
    if ia is not None and not ia.is_passed:
        fb = " +fallback" if (getattr(ia, "source", "") or "") == "fallback" else ""
        return f"intent_reddi{fb}", base + f" intent={ia.intent_type}"
    if pf is not None and not pf.is_kept:
        if getattr(pf, "transfer_channel", None):
            return f"transfer_yonlendi({pf.transfer_channel})", base
        tag = (f"sinif={pf.ai_class}" if pf.ai_class is not None
               else (reason or "karantina"))
        if getattr(pf, "is_fallback", False):
            tag += " +fallback"
        return f"prefilter_eledi({tag})", base
    return "kapasite_alti", base + f" K={capacity}"


# ── Ham 570-satır görünümü (Codex v18-3: FP/TN dahil TAM tablo) ──

def raw_view_metrics(doc: dict, pools_norms: Dict[str, Set[str]],
                     normalize) -> Dict[str, Dict[str, float]]:
    """570 kaynak satırın TAMAMI üzerinden kanal metrikleri.

    v19-1: pozitifler SATIR BAĞLANTISIYLA sayılır — her raw_label,
    importer'ın kaydettiği kendi clean_row_no'suna işaret eder (norm-anahtarlı
    sözlük ikizleri eziyordu: ADS 128≠125 sayımı bu bug'dandı). Sistem
    üyeliği norm üzerinden gelir (ikiz satırlar norm sonucunu paylaşır —
    bilinçli ve raporda görünür).
    """
    out = {}
    positives_by_channel = {ch: set() for ch in CHANNELS}
    for row in doc["raw_labels"]:
        clean_no = row.get("clean_row_no")
        if clean_no is None:
            raise ValueError(
                f"raw_label satır bağlantısı yok: {row['keyword']!r} — "
                f"importer v4 ile artefakt yeniden üretilmeli")
        for channel, key in CHANNELS.items():
            if row[key]:
                positives_by_channel[channel].add(f"r{clean_no}")

    all_rows = {f"r{row_no}" for row_no, _ in _iter_clean_rows(doc)}
    system_by_channel = {ch: set() for ch in CHANNELS}
    for row_no, keyword in _iter_clean_rows(doc):
        norm = normalize(keyword)
        for channel in CHANNELS:
            if norm in pools_norms[channel]:
                system_by_channel[channel].add(f"r{row_no}")

    for channel in CHANNELS:
        positives = positives_by_channel[channel]
        negatives = all_rows - positives
        out[channel] = compute_metrics(positives, negatives,
                                       system_by_channel[channel])
        out[channel]["rows"] = len(all_rows)
    return out


def _iter_clean_rows(doc: dict):
    for item in doc.get("clean_source_rows", []):
        yield item["row_no"], item["keyword"]


def aggregate_usage(events: List[dict]) -> dict:
    """Tam-run maliyeti: AiUsageEvent satırlarından stage+model kırılımı
    (Codex v19-5 — yalnız assignment task'ının usage özeti embedding/
    relevance olaylarını kaçırıyordu)."""
    from app.core.telemetry.usage import estimate_cost

    by_stage: Dict[str, dict] = {}
    for e in events:
        stage_key = f"{e.get('stage')}|{e.get('model')}"
        bucket = by_stage.setdefault(stage_key, {
            "stage": e.get("stage"), "model": e.get("model"),
            "requests": 0, "prompt_tokens": 0, "candidates_tokens": 0,
            "thoughts_tokens": 0, "failed_requests": 0, "_events": [],
        })
        bucket["requests"] += 1
        bucket["prompt_tokens"] += e.get("prompt_tokens") or 0
        bucket["candidates_tokens"] += e.get("candidates_tokens") or 0
        bucket["thoughts_tokens"] += e.get("thoughts_tokens") or 0
        bucket["failed_requests"] += 1 if e.get("retry_reason") else 0
        bucket["_events"].append(e)
    stages = []
    total_usd = 0.0
    for bucket in by_stage.values():
        cost = estimate_cost(bucket.pop("_events"))
        bucket["estimated_usd"] = cost["estimated_usd"]
        total_usd += cost["estimated_usd"]
        stages.append(bucket)
    stages.sort(key=lambda b: -b["estimated_usd"])
    return {
        "stages": stages,
        "total_requests": sum(b["requests"] for b in stages),
        "total_estimated_usd": round(total_usd, 4),
    }


# ── Rapor kurulumu (Codex v18-2: JSON her şeyi taşır) ──

def build_channel_block(patron_metrics: dict, prod_metrics: dict,
                        reasons: Counter, fn_detail: list,
                        fp_detail: list, patron_k: int) -> dict:
    return {
        "patron_K": patron_k,
        "patron_view": patron_metrics,
        "production_counterfactual_view": prod_metrics,
        "miss_reasons": dict(reasons),
        "fn": fn_detail,
        "fp": fp_detail,
    }
