"""Aday-üretim erişim replay çekirdeği (plan_ai.md §0 kanıtının üreticisi).

Codex denetim sözleşmeleri (2. tur):
- ÜÇ bütçe AYRI raporlanır: B_initial = min(POOL_SIZE, 3*K) başlangıç
  penceresi; B_actual_examined = run'da GERÇEKTEN incelenen aday sayısı
  (expansion dahil, DB ChannelCandidate); B_cap = expansion tavanı.
- Gerçek üretim baseline'ı hesapla DEĞİL, doğrudan DB'deki ChannelCandidate
  kümesinden okunur.
- Kimlik keyword_id'dir (metin değil — aynı metinli farklı ID'ler ezilmez).
- "raw" sıralama kanal rank alanından gelir (üretim non-relevance yolu);
  adjusted sıralama üretim tie-break'iyle BİREBİRDİR:
  (-adjusted, kanal_rank|999999, keyword_id), adjusted = max(skor,0)*rel*katsayı.
- Parite kapısı: hesaplanan adjusted top-B_initial, DB'deki ilk B_initial
  ChannelCandidate ile SIRALI birebir eşleşmezse rapor ÜRETİLMEZ (fail-closed).
- RRF rank'leri 1'den başlar: RRF(d) = Σ 1/(k + rank_r(d)), rank_r ∈ {1..N}.
- Fail-closed girdi doğrulamaları: run sahipliği, algorithm_version=v2,
  candidate_pool_multiplier=1, relevance_enabled=true, skor kimlik kümesi ==
  relevance kimlik kümesi, tüm kanonik pozitifler DB evreninde.
- Provenance: kanonik girdi hash'i (id/metin/skor/rank/relevance), run
  manifest hash'i, artifact payload SHA (öz-bütünlük, v21_dataset kalıbı).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.core.constants import (
    ADS_MAX_EXPANSION_POOL_SIZE,
    ADS_POOL_SIZE,
    SEO_MAX_EXPANSION_POOL_SIZE,
    SEO_POOL_SIZE,
    SOCIAL_MAX_EXPANSION_POOL_SIZE,
    SOCIAL_POOL_SIZE,
)
from app.database.models import (
    ChannelCandidate,
    Keyword,
    KeywordRelevance,
    KeywordScore,
    ScoringRun,
    TaskResult,
)

# Aday havuzu bu durumlarda finaldir; öncesinde (scoring/assigning) eksik
# aday listesi parite kapısından kısa prefix'le sızabilirdi (Codex 3. tur #1)
COMPLETED_RUN_STATUSES = ("channel_assigned", "completed")
CAPACITY_ATTRS = {"ADS": "ads_capacity", "SEO": "seo_capacity",
                  "SOCIAL": "social_capacity"}

POOL_CAPS = {"ADS": ADS_POOL_SIZE, "SEO": SEO_POOL_SIZE, "SOCIAL": SOCIAL_POOL_SIZE}
EXPANSION_CAPS = {
    "ADS": ADS_MAX_EXPANSION_POOL_SIZE,
    "SEO": SEO_MAX_EXPANSION_POOL_SIZE,
    "SOCIAL": SOCIAL_MAX_EXPANSION_POOL_SIZE,
}
SCORE_ATTRS = {"ADS": "ads_score", "SEO": "seo_score", "SOCIAL": "social_score"}
RANK_ATTRS = {"ADS": "ads_rank", "SEO": "seo_rank", "SOCIAL": "social_rank"}
RRF_KS = (20, 40, 60)
BUDGET_MULTIPLIERS = (1.0, 1.5, 2.0)
DEFAULT_RELEVANCE = 0.5
PAYLOAD_HASH_EXCLUDED = ("artifact_payload_sha256", "generated_at")


class ReplayInputError(RuntimeError):
    """Fail-closed girdi/parite ihlali — rapor üretilmez."""


def _canonical_hash(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_payload_sha(doc: Dict[str, Any]) -> str:
    """Artifact öz-bütünlük hash'i (hariç tutulanlar: kendisi + zaman)."""
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items()
                    if k not in PAYLOAD_HASH_EXCLUDED}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o

    return _canonical_hash(strip(doc))


def validate_artifact(doc: Dict[str, Any]) -> None:
    expected = doc.get("provenance", {}).get("artifact_payload_sha256")
    if not expected:
        raise ReplayInputError("artifact_payload_sha256 yok — artifact geçersiz")
    actual = compute_payload_sha(doc)
    if actual != expected:
        raise ReplayInputError(
            f"artifact payload SHA uyuşmuyor: {actual} != {expected}"
        )


def budget_initial(channel: str, capacity: int) -> int:
    return min(POOL_CAPS[channel], 3 * capacity)


def load_run_inputs(db, run_id: int, expected_workspace_id: int,
                    expected_capacities: Optional[Dict[str, int]] = None
                    ) -> Dict[str, Any]:
    """Run girdilerini fail-closed doğrulamalarla yükler."""
    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if run is None:
        raise ReplayInputError(f"run {run_id} yok")
    if run.brand_profile_id != expected_workspace_id:
        raise ReplayInputError(
            f"run {run_id} workspace {run.brand_profile_id} — beklenen "
            f"{expected_workspace_id}"
        )
    algo = getattr(run, "algorithm_version", "v2") or "v2"
    if algo != "v2":
        raise ReplayInputError(f"run {run_id} algorithm_version={algo}, v2 değil")
    # Codex 3. tur #1: yarım kalmış run'ın kısa aday listesi parite
    # kapısından geçemez — run bitmiş, atama görevi de completed olmalı
    if run.status not in COMPLETED_RUN_STATUSES:
        raise ReplayInputError(
            f"run {run_id} status={run.status!r} — aday havuzu final değil "
            f"(beklenen: {COMPLETED_RUN_STATUSES})"
        )
    completed_assignment = (
        db.query(TaskResult)
        .filter(
            TaskResult.scoring_run_id == run_id,
            TaskResult.task_type == "channel_assignment",
            TaskResult.status == "completed",
        )
        .first()
    )
    if completed_assignment is None:
        raise ReplayInputError(
            f"run {run_id} için completed channel_assignment TaskResult yok"
        )
    # Codex 3. tur #2: kapasiteler run'dan OKUNUR; beklenen değer verilmişse
    # eşitlik zorlanır (hardcode kapasiteyle yanlış B_initial hesaplanamaz)
    run_capacities = {
        ch: int(getattr(run, attr) or 0) for ch, attr in CAPACITY_ATTRS.items()
    }
    if expected_capacities is not None:
        for ch, expected in expected_capacities.items():
            if run_capacities.get(ch) != expected:
                raise ReplayInputError(
                    f"run {run_id} {ch} kapasitesi {run_capacities.get(ch)} — "
                    f"beklenen {expected}"
                )
    manifest = run.execution_manifest or {}
    if int(manifest.get("candidate_pool_multiplier") or 0) != 1:
        raise ReplayInputError(
            f"run {run_id} candidate_pool_multiplier != 1 "
            f"({manifest.get('candidate_pool_multiplier')!r})"
        )
    if manifest.get("relevance_enabled") is not True:
        raise ReplayInputError(f"run {run_id} relevance_enabled != true")

    rel = {
        r.keyword_id: float(r.relevance_score)
        for r in db.query(KeywordRelevance)
        .filter(KeywordRelevance.scoring_run_id == run_id)
        .all()
    }
    rows: List[Dict[str, Any]] = []
    score_ids = set()
    for ks, text in (
        db.query(KeywordScore, Keyword.keyword)
        .join(Keyword, Keyword.id == KeywordScore.keyword_id)
        .filter(KeywordScore.scoring_run_id == run_id)
        .all()
    ):
        score_ids.add(ks.keyword_id)
        rows.append({
            "keyword_id": ks.keyword_id,
            "text": text,
            "scores": {ch: float(getattr(ks, SCORE_ATTRS[ch]) or 0.0)
                       for ch in SCORE_ATTRS},
            "ranks": {ch: getattr(ks, RANK_ATTRS[ch]) for ch in RANK_ATTRS},
            "relevance": rel.get(ks.keyword_id),
        })
    if score_ids != set(rel):
        missing = sorted(score_ids - set(rel))[:5]
        extra = sorted(set(rel) - score_ids)[:5]
        raise ReplayInputError(
            f"run {run_id} skor/relevance kimlik kümeleri farklı "
            f"(skorda olup relevance'ta olmayan örn: {missing}; tersi: {extra})"
        )
    rows.sort(key=lambda r: r["keyword_id"])
    input_hash = _canonical_hash(rows)
    manifest_hash = _canonical_hash(manifest)
    return {
        "run": run,
        "rows": rows,
        "capacities": run_capacities,
        "coefficient": float(run.default_relevance_coefficient or 1.0),
        "input_hash": input_hash,
        "manifest_hash": manifest_hash,
    }


def verify_universe_lock(rows: List[Dict[str, Any]], map_doc: Dict[str, Any],
                         slug: str) -> None:
    """Codex 3. tur #3: import haritasının non-null canonical survivor kümesi
    ile DB skor evreni İKİ YÖNLÜ eşit olmalı — etiketsiz bir survivor DB'den
    kaybolsa da (veya DB'de haritada olmayan kelime belirse de) fail-closed."""
    map_canonicals = {
        row["canonical"] for row in map_doc[slug]["mapping"] if row["canonical"]
    }
    # Codex 4. tur sertleştirmesi: kanonik evrende aynı exact metni taşıyan
    # birden fazla keyword ID olamaz — set eşitliği bu çoğalmayı göremezdi
    seen: Dict[str, int] = {}
    dupes = []
    for r in rows:
        if r["text"] in seen:
            dupes.append(r["text"])
        seen[r["text"]] = r["keyword_id"]
    if dupes:
        raise ReplayInputError(
            f"{slug}: kanonik evrende exact-metin çoğalması — "
            f"{len(dupes)} metin birden fazla keyword ID taşıyor "
            f"(örn: {sorted(set(dupes))[:3]})"
        )
    db_texts = set(seen)
    only_map = sorted(map_canonicals - db_texts)
    only_db = sorted(db_texts - map_canonicals)
    if only_map or only_db:
        raise ReplayInputError(
            f"{slug}: evren kilidi ihlali — yalnız haritada {len(only_map)} "
            f"(örn: {only_map[:3]}), yalnız DB'de {len(only_db)} "
            f"(örn: {only_db[:3]})"
        )


def resolve_positive_ids(rows: List[Dict[str, Any]], map_doc: Dict[str, Any],
                         slug: str) -> Dict[str, set]:
    """Kanonik pozitif METİNLERİNİ evrendeki keyword_id'lere bağlar.

    Aynı metinli farklı ID'ler ezilmez (hepsi pozitif sayılır). Evrende
    bulunmayan kanonik pozitif → fail-closed.
    """
    by_text: Dict[str, set] = {}
    for r in rows:
        by_text.setdefault(r["text"], set()).add(r["keyword_id"])
    positives: Dict[str, set] = {"ADS": set(), "SEO": set(), "SOCIAL": set()}
    missing: List[str] = []
    for row in map_doc[slug]["mapping"]:
        canonical = row.get("canonical")
        if not canonical or not row.get("labels"):
            continue
        ids = by_text.get(canonical)
        if not ids:
            missing.append(canonical)
            continue
        for ch in row["labels"]:
            positives[ch].update(ids)
    if missing:
        raise ReplayInputError(
            f"{slug}: {len(missing)} kanonik pozitif DB evreninde yok "
            f"(örn: {missing[:5]})"
        )
    return positives


def adjusted_ordering(rows: List[Dict[str, Any]], channel: str,
                      coefficient: float) -> List[int]:
    """ÜRETİM sıralaması (pool_builder relevance yolu ile birebir):
    (-adjusted, kanal_rank|999999, keyword_id)."""
    def key(r):
        relevance = (r["relevance"] if r["relevance"] is not None
                     else DEFAULT_RELEVANCE)
        adjusted = max(r["scores"][channel], 0.0) * relevance * coefficient
        return (-adjusted, r["ranks"][channel] or 999999, r["keyword_id"])

    return [r["keyword_id"] for r in sorted(rows, key=key)]


def raw_rank_ordering(rows: List[Dict[str, Any]], channel: str) -> List[int]:
    """Üretim non-relevance yolu: kanal rank alanı (skor->hacim->alfabetik
    zinciri skorlamada üretilmiştir), eşitlikte keyword_id."""
    return [
        r["keyword_id"]
        for r in sorted(rows, key=lambda r: (r["ranks"][channel] or 999999,
                                             r["keyword_id"]))
    ]


def relevance_ordering(rows: List[Dict[str, Any]]) -> List[int]:
    """Replay'e özgü karşılaştırma sıralaması (üretimde birebir karşılığı
    yok): (-relevance, keyword_id)."""
    return [
        r["keyword_id"]
        for r in sorted(rows, key=lambda r: (
            -(r["relevance"] if r["relevance"] is not None else DEFAULT_RELEVANCE),
            r["keyword_id"],
        ))
    ]


def rrf_ordering(primary: List[int], secondary: List[int], k: int) -> List[int]:
    """RRF, 1-based rank'lerle: RRF(d) = 1/(k+rank1) + 1/(k+rank2)."""
    r1 = {kid: i for i, kid in enumerate(primary, start=1)}
    r2 = {kid: i for i, kid in enumerate(secondary, start=1)}
    return sorted(
        r1,
        key=lambda kid: (-(1.0 / (k + r1[kid]) + 1.0 / (k + r2[kid])), kid),
    )


def db_candidate_sequence(db, run_id: int, channel: str):
    """DB aday dizisi + rank sağlık istatistikleri.

    Kapı (Codex 3. tur #1, üretim gerçeğiyle netleştirilmiş): rank'ler
    BENZERSİZ ve KESİN ARTAN olmalı — ihlal bozulma sinyalidir, rapor
    üretilmez. Yoğunluk (1..Ba kesintisizlik) ZORLANMAZ: üretimin transfer/
    expansion yolu rank'leri pencere-offset'iyle yazar (örn. run 25 SEO
    rank'leri 1..334 aralığında 122 benzersiz değer; boşluklar 60→87 ve
    87→274 offset mimarisinden). Boşuk sayısı şeffaflık için raporlanır.
    """
    rows = (
        db.query(ChannelCandidate.keyword_id, ChannelCandidate.rank_in_channel)
        .filter(ChannelCandidate.scoring_run_id == run_id,
                ChannelCandidate.channel == channel)
        .order_by(ChannelCandidate.rank_in_channel)
        .all()
    )
    ranks = [r for _, r in rows]
    strictly_increasing = all(b > a for a, b in zip(ranks, ranks[1:]))
    if not ranks or ranks[0] < 1 or not strictly_increasing:
        raise ReplayInputError(
            f"run {run_id} {channel}: candidate rank'leri benzersiz/kesin "
            f"artan değil (ilk 5: {ranks[:5]}) — bozulma sinyali"
        )
    rank_stats = {
        "rank_min": ranks[0],
        "rank_max": ranks[-1],
        "rank_gap_count": sum(
            1 for a, b in zip(ranks, ranks[1:]) if b - a > 1
        ) + (1 if ranks[0] > 1 else 0),
        "dense_1_to_Ba": ranks == list(range(1, len(ranks) + 1)),
    }
    return [kid for kid, _ in rows], rank_stats


def verify_production_parity(computed: List[int], db_sequence: List[int],
                             b_initial: int, *, run_id: int,
                             channel: str) -> None:
    """Hesaplanan adjusted top-B_initial, DB'deki ilk B_initial aday ile
    SIRALI birebir eşleşmeli — değilse rapor üretilmez."""
    expected = db_sequence[:b_initial]
    got = computed[:len(expected)]
    if got != expected:
        first_bad = next(
            (i for i, (a, b) in enumerate(zip(got, expected)) if a != b),
            min(len(got), len(expected)),
        )
        raise ReplayInputError(
            f"run {run_id} {channel}: üretim parite ihlali — hesaplanan "
            f"sıralama DB ChannelCandidate ile eşleşmiyor (ilk fark idx "
            f"{first_bad}: hesap={got[first_bad] if first_bad < len(got) else None} "
            f"db={expected[first_bad] if first_bad < len(expected) else None})"
        )


def _positive_ranks(ordering: List[int], positives: set) -> List[int]:
    """1-based pozitif rank listesi (tam evren eğrisi)."""
    return sorted(i for i, kid in enumerate(ordering, start=1)
                  if kid in positives)


def _recall_block(pos_ranks: List[int], p: int, budgets: Dict[str, int]) -> Dict:
    out = {}
    for name, b in budgets.items():
        reached = sum(1 for r in pos_ranks if r <= b)
        out[name] = {
            "budget": b,
            "reached": reached,
            "recall": round(reached / p, 4) if p else None,
        }
    return out


def build_channel_replay(db, run_id: int, channel: str, capacity: int,
                         rows: List[Dict[str, Any]], positives: set,
                         coefficient: float) -> Dict[str, Any]:
    b_initial = budget_initial(channel, capacity)
    db_seq, rank_stats = db_candidate_sequence(db, run_id, channel)
    b_actual = len(db_seq)
    # Codex 3. tur #1: eksik aday listesi kısa prefix'le kapıdan geçemez
    if b_actual < b_initial:
        raise ReplayInputError(
            f"run {run_id} {channel}: DB'de {b_actual} aday var, B_initial "
            f"{b_initial} — aday havuzu eksik/yarım"
        )

    computed_adjusted = adjusted_ordering(rows, channel, coefficient)
    verify_production_parity(computed_adjusted, db_seq, b_initial,
                             run_id=run_id, channel=channel)

    # Codex 3. tur #4: Ba seviyesi parite İSPAT DEĞİL — adjusted, Ba'da
    # "aynı sayıda adayla counterfactual ordering"dır (transferler DB
    # kümesini değiştirebilir). Fark dürüstçe raporlanır.
    adjusted_at_ba = computed_adjusted[:b_actual]
    adj_set, db_set = set(adjusted_at_ba), set(db_seq)
    inter = len(adj_set & db_set)
    union = len(adj_set | db_set)
    production_parity = {
        "ordered_equal_at_B_initial": True,  # kapı geçildi (aksi halde raise)
        "rank_stats": rank_stats,
        "actual_ordered_equal": adjusted_at_ba == db_seq,
        "actual_set_equal": adj_set == db_set,
        "actual_set_jaccard": round(inter / union, 4) if union else None,
        "only_in_adjusted_at_Ba": len(adj_set - db_set),
        "only_in_db_at_Ba": len(db_set - adj_set),
        # DB'de olup counterfactual sıralamada olmayanlar: tipik kaynak
        # cross-channel transfer enjeksiyonu
        "transfer_difference_count": len(db_set - adj_set),
    }

    raw = raw_rank_ordering(rows, channel)
    rel = relevance_ordering(rows)
    orderings: Dict[str, List[int]] = {
        "raw_rank": raw,
        "relevance": rel,
        "adjusted_production": computed_adjusted,
    }
    for k in RRF_KS:
        orderings[f"rrf_k{k}"] = rrf_ordering(raw, rel, k)

    budgets = {"B_initial": b_initial}
    for mult in BUDGET_MULTIPLIERS[1:]:
        budgets[f"{mult:g}xB_initial"] = int(b_initial * mult)
    budgets["B_actual_examined"] = b_actual

    p = len(positives)
    per_ordering = {}
    for name, ordering in orderings.items():
        pos_ranks = _positive_ranks(ordering, positives)
        per_ordering[name] = {
            "positive_ranks": pos_ranks,
            "recall_at": _recall_block(pos_ranks, p, budgets),
        }

    examined_set = set(db_seq)
    production_reach = len(positives & examined_set)
    return {
        "capacity_K": capacity,
        "pool_cap": POOL_CAPS[channel],
        "expansion_cap_B_cap": EXPANSION_CAPS[channel],
        "B_initial": b_initial,
        "B_actual_examined": b_actual,
        "P": p,
        "production_baseline": {
            "source": "DB ChannelCandidate kümesi (expansion dahil)",
            "examined": b_actual,
            "reached": production_reach,
            "recall": round(production_reach / p, 4) if p else None,
        },
        "production_parity": production_parity,
        "orderings": per_ordering,
    }
