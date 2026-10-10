"""Dry-run and commit planning for Google Ads CSV keyword imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.keyword_dedup import (
    FUZZY_THRESHOLD,
    are_metrics_equal,
    has_meaningful_metrics,
    strip_turkish_suffixes,
)
from app.core.keyword_junk import junk_reason
from app.core.keyword_normalize import normalize_keyword
from app.database.crud import _keyword_insert_data, _legacy_keyword_data_source
from app.database.models import Keyword, WorkspaceKeyword

try:
    from rapidfuzz import fuzz
except ImportError:
    try:
        from thefuzz import fuzz
    except ImportError:
        fuzz = None


@dataclass
class ImportPlan:
    brand_profile_id: int
    parser_meta: dict[str, Any]
    source_file_name: str
    total_before: int
    pool_limit: int | None = None
    accepted_plan: list[dict[str, Any]] = field(default_factory=list)
    accepted_keywords: list[dict[str, Any]] = field(default_factory=list)
    excluded_keywords: list[dict[str, Any]] = field(default_factory=list)
    exact_duplicates: list[dict[str, Any]] = field(default_factory=list)
    exact_duplicates_different_metrics: list[dict[str, Any]] = field(default_factory=list)
    fuzzy_skipped: list[dict[str, Any]] = field(default_factory=list)
    fuzzy_kept: list[dict[str, Any]] = field(default_factory=list)
    junk_rows: list[dict[str, Any]] = field(default_factory=list)
    theme_excluded: list[dict[str, Any]] = field(default_factory=list)
    # P1.6: AI kaynakli (onaysiz) dislama temasina takilan AMA KABUL EDILEN
    # satirlar. Eleme DEGIL uyaridir; `excluded_keywords`'e GIRMEZ.
    theme_warned: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "requested": len(self.accepted_plan) + len(self.excluded_keywords) + len(self.junk_rows),
            "parsed": len(self.accepted_plan) + len(self.excluded_keywords),
            "accepted": len(self.accepted_plan),
            "skipped": len(self.excluded_keywords),
            "skipped_exact": len(self.exact_duplicates),
            "skipped_fuzzy": len(self.fuzzy_skipped),
            "skipped_theme": len(self.theme_excluded),
            "warned_theme": len(self.theme_warned),
            "kept_fuzzy_different_metrics": sum(
                1 for item in self.fuzzy_kept if item.get("reason") == "fuzzy_different_metrics_keep"
            ),
            "kept_fuzzy_default_metrics": sum(
                1 for item in self.fuzzy_kept if item.get("reason") == "fuzzy_default_metrics_keep"
            ),
            "exact_duplicate_different_metrics": len(self.exact_duplicates_different_metrics),
        }


def build_import_plan(
    db: Session,
    *,
    brand_profile_id: int,
    keyword_rows: list[dict[str, Any]],
    parser_meta: dict[str, Any],
    source_file_name: str,
    junk_rows: list[dict[str, Any]] | None = None,
    theme_policy: Any = None,
    pool_limit: int | None = None,
) -> ImportPlan:
    """CSV dry-run plani.

    P1.6: `exclude_themes` listesi yerine `ImportThemePolicy` alir — provenance
    (kullanici onayli / AI kaynakli / korunan) duz listeden turetilemez.
    `theme_policy=None` ise tema kapisi hic calismaz.
    """
    plan = ImportPlan(
        brand_profile_id=brand_profile_id,
        parser_meta=parser_meta,
        source_file_name=source_file_name,
        total_before=_workspace_count(db, brand_profile_id),
        pool_limit=pool_limit,
        junk_rows=junk_rows or [],
    )

    gate_active = theme_policy is not None and not theme_policy.is_empty
    if gate_active:
        from app.core.policy.import_gate import decide_keyword_theme

    workspace_rows = _workspace_rows(db, brand_profile_id)

    # snapshots: normalized → list of metric snapshots (multiple allowed per keyword)
    # unique_rep: normalized → one representative for fuzzy text matching
    workspace_snapshots: dict[str, list[dict[str, Any]]] = {}
    workspace_unique_rep: dict[str, dict[str, Any]] = {}
    for row in workspace_rows:
        norm = row["normalized_keyword"]
        workspace_snapshots.setdefault(norm, []).append(row)
        workspace_unique_rep.setdefault(norm, row)

    # in-batch tracking (same two structures for newly accepted rows)
    virtual_snapshots: dict[str, list[dict[str, Any]]] = {}
    virtual_unique_rep: dict[str, dict[str, Any]] = {}

    # Fuzzy adaylari sabittir (yalnizca committed workspace satirlari, virtual
    # eklenmez — asagidaki yorum). Dongu disina alinir ve stem'ler BIR KEZ
    # hesaplanir: onceki desen her satir icin TUM adaylari yeniden stem'liyordu
    # (O(n*m) stem; 2000x2000'de ~35 sn). Ayni dongu sirasi ve ilk-eslesme
    # semantigi korunur — yalnizca tekrar hesaplama kaldirilir.
    fuzzy_candidates = list(workspace_unique_rep.values())
    for _cand in fuzzy_candidates:
        _cand["stemmed_keyword"] = strip_turkish_suffixes(
            (_cand.get("keyword") or "").lower().strip()
        )

    # Global Keyword lookup'larini tek sorguda on-yukle (satir basina SELECT
    # yerine). Anahtar: (normalized, volume, competition) — eski per-row
    # sorgusuyla ayni eslesme kriteri; .first() gibi ilk bulunan id tutulur.
    incoming_norms = {
        normalize_keyword((r.get("keyword") or "").strip())
        for r in keyword_rows
        if (r.get("keyword") or "").strip()
    }
    global_keyword_map: dict[tuple, int] = {}
    if incoming_norms:
        for gk in (
            db.query(Keyword)
            .filter(Keyword.normalized_keyword.in_(incoming_norms))
            .all()
        ):
            global_keyword_map.setdefault(
                (gk.normalized_keyword, gk.monthly_volume, gk.competition_score),
                gk.id,
            )

    for row in keyword_rows:
        keyword = (row.get("keyword") or "").strip()
        # Savunma katmani: parser disindaki cagiranlardan gelen cop satirlar da
        # ayni kaynaktan (junk_reason) elenir ve junk_rows'ta raporlanir.
        _junk = junk_reason(keyword)
        if _junk is not None:
            plan.junk_rows.append({
                "keyword": keyword,
                "source_file": row.get("_source_file") or source_file_name,
                "source_row": row.get("_source_row"),
                "reason": "junk_row",
                "matched": _junk,
            })
            continue
        normalized = normalize_keyword(keyword)
        detail = _detail(row, reason="accepted")
        detail["normalized_keyword"] = normalized

        # ── Tema kapısı (crud yoluyla AYNI karar fonksiyonu — P1.6) ──
        if gate_active:
            decision = decide_keyword_theme(keyword, theme_policy)
            if decision.is_excluded:
                rejected = {
                    **detail,
                    "reason": "skipped_theme",
                    "matched_keyword": decision.matched_exclude_theme,
                }
                plan.theme_excluded.append(rejected)
                plan.excluded_keywords.append(rejected)
                continue
            if decision.has_advisory_warning:
                # ELEME DEGIL: satir normal akisa devam eder, yalniz
                # kullaniciya "AI temasina takildi" diye gosterilir.
                plan.theme_warned.append({
                    **detail,
                    "reason": "theme_warning",
                    "matched_keyword": decision.matched_advisory_theme,
                })

        # ── Exact-duplicate check ──────────────────────────────────────────
        existing_snapshots = (
            workspace_snapshots.get(normalized, []) + virtual_snapshots.get(normalized, [])
        )
        if existing_snapshots:
            exact_match = next(
                (ex for ex in existing_snapshots if not _metrics_differ(row, ex)),
                None,
            )
            if exact_match:
                # Identical keyword + identical metrics already in workspace → skip
                rejected = _with_match(detail, exact_match, reason="aynisi_onceden_eklendi", match_ratio=100)
                plan.exact_duplicates.append(rejected)
                plan.excluded_keywords.append(rejected)
                continue

            # Same text but ALL existing snapshots have different metrics → new record
            # (record which existing snapshot it differs from, for reporting)
            diff_report = _with_match(
                detail, existing_snapshots[0],
                reason="exact_duplicate_different_metrics",
                match_ratio=100,
            )
            plan.exact_duplicates_different_metrics.append(diff_report)
            # fall through → accept as a new workspace_keyword row

        # ── Fuzzy check ───────────────────────────────────────────────────
        # Only compare against committed workspace rows (not virtual batch-local
        # candidates). Virtual candidates were never committed so cannot reliably
        # prevent the same keyword from being accepted on a re-import.
        fuzzy_match = _find_fuzzy_match(row, fuzzy_candidates)
        if fuzzy_match:
            matched, ratio, reason, should_skip = fuzzy_match
            matched_detail = _with_match(detail, matched, reason=reason, match_ratio=ratio)
            if should_skip:
                plan.fuzzy_skipped.append(matched_detail)
                plan.excluded_keywords.append(matched_detail)
                continue
            plan.fuzzy_kept.append(matched_detail)

        # ── Accept ────────────────────────────────────────────────────────
        # Option B: each unique (text, metrics) combo links to a distinct Keyword row.
        # This keeps score_engine's combined[kw.id] dict collision-free.
        target_vol = max(1, int(row.get("monthly_volume") or 1))
        target_cs = _safe_competition(row.get("competition_score"))
        global_keyword_id = global_keyword_map.get((normalized, target_vol, target_cs))
        action = "link_existing_keyword" if global_keyword_id else "create_keyword"
        accepted = {
            **detail,
            "reason": action,
            "action": action,
            "keyword_id": global_keyword_id,
            "keyword_data": _keyword_data_for_write(row, normalized),
        }
        plan.accepted_plan.append(accepted)
        plan.accepted_keywords.append(accepted)

        new_snapshot = {
            "keyword": keyword,
            "normalized_keyword": normalized,
            "keyword_id": global_keyword_id,
            "monthly_volume": row.get("monthly_volume", 0),
            "competition_score": float(row.get("competition_score") or 0.5),
            "matched_in_workspace": True,
        }
        virtual_snapshots.setdefault(normalized, []).append(new_snapshot)
        virtual_unique_rep.setdefault(normalized, new_snapshot)

    return plan


# Chunk basina tek gercek commit; kelime basina izolasyon SAVEPOINT ile saglanir.
IMPORT_COMMIT_CHUNK_SIZE = 200


def apply_import_plan(
    db: Session,
    plan_payload: dict[str, Any],
    pool_limit: int | None = None,
) -> dict[str, Any]:
    brand_profile_id = int(plan_payload["brand_profile_id"])
    accepted_plan = plan_payload.get("accepted_plan") or []
    total_before = _workspace_count(db, brand_profile_id)
    created_new = 0
    linked_existing = 0
    skipped_due_to_race = 0
    skipped_limit = 0
    # Havuz limiti hard-guard: dry-run ile commit arasinda havuz degismis
    # olabilir; sayim transaction icindeki gercek satir sayisiyla baslar.
    current_total = total_before

    # ── On-yuklemeler (item basina SELECT'leri kaldirir) ────────────────
    # 1) Workspace exact-snapshot seti: (keyword_id, vol, cs).
    #    Eski per-item SELECT ile ayni app-seviyesi kontrol; islem icinde
    #    eklenenler set'e islenerek ayni-batch duplicate davranisi korunur.
    snapshot_set: set[tuple] = {
        (kid, vol, cs)
        for kid, vol, cs in db.query(
            WorkspaceKeyword.keyword_id,
            WorkspaceKeyword.monthly_volume,
            WorkspaceKeyword.competition_score,
        ).filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
    }
    # 2) Global Keyword haritasi: (normalized, vol, cs) → id.
    #    Yeni yaratilanlar haritaya eklenir; boylece ayni batch'teki sonraki
    #    duplicate'ler eski per-item-commit davranisindaki gibi LINK yoluna girer.
    plan_norms = {
        (item.get("keyword_data") or {}).get("normalized_keyword")
        or normalize_keyword((item.get("keyword_data") or {}).get("keyword", ""))
        for item in accepted_plan
    }
    plan_norms.discard("")
    global_map: dict[tuple, int] = {}
    if plan_norms:
        for gk in (
            db.query(Keyword)
            .filter(Keyword.normalized_keyword.in_(plan_norms))
            .all()
        ):
            global_map.setdefault(
                (gk.normalized_keyword, gk.monthly_volume, gk.competition_score),
                gk.id,
            )

    pending_in_chunk = 0
    for item in accepted_plan:
        if pool_limit is not None and current_total >= pool_limit:
            skipped_limit += 1
            continue
        keyword_data = item.get("keyword_data") or {}
        normalized = keyword_data.get("normalized_keyword") or normalize_keyword(keyword_data.get("keyword", ""))
        action = item.get("action")
        target_vol = max(1, int(keyword_data.get("monthly_volume") or 1))
        target_cs = _safe_competition(keyword_data.get("competition_score"))

        try:
            # SAVEPOINT: bozuk/yarisan tek kelime yalnizca kendi degisikligini
            # geri alir; chunk'taki diger kelimeler etkilenmez.
            with db.begin_nested():
                if action == "link_existing_keyword" and item.get("keyword_id"):
                    keyword_id = int(item["keyword_id"])
                    if (keyword_id, target_vol, target_cs) in snapshot_set:
                        skipped_due_to_race += 1
                        continue
                    _create_workspace_link(
                        db,
                        brand_profile_id=brand_profile_id,
                        keyword_id=keyword_id,
                        keyword_data=keyword_data,
                        commit=False,
                    )
                    db.flush()
                    snapshot_set.add((keyword_id, target_vol, target_cs))
                    linked_existing += 1
                    current_total += 1
                else:
                    existing_id = global_map.get((normalized, target_vol, target_cs))
                    if existing_id is not None:
                        if (existing_id, target_vol, target_cs) in snapshot_set:
                            skipped_due_to_race += 1
                            continue
                        _create_workspace_link(
                            db,
                            brand_profile_id=brand_profile_id,
                            keyword_id=existing_id,
                            keyword_data=keyword_data,
                            commit=False,
                        )
                        db.flush()
                        snapshot_set.add((existing_id, target_vol, target_cs))
                        linked_existing += 1
                        current_total += 1
                    else:
                        keyword = Keyword(**_keyword_insert_data(keyword_data))
                        db.add(keyword)
                        db.flush()
                        _create_workspace_link(
                            db,
                            brand_profile_id=brand_profile_id,
                            keyword_id=keyword.id,
                            keyword_data=keyword_data,
                            commit=False,
                        )
                        db.flush()
                        global_map.setdefault(
                            (normalized, target_vol, target_cs), keyword.id
                        )
                        snapshot_set.add((keyword.id, target_vol, target_cs))
                        created_new += 1
                        current_total += 1
        except IntegrityError:
            # begin_nested savepoint'i otomatik geri alindi; dis islem saglam.
            skipped_due_to_race += 1
            continue

        pending_in_chunk += 1
        if pending_in_chunk >= IMPORT_COMMIT_CHUNK_SIZE:
            db.commit()
            pending_in_chunk = 0

    db.commit()

    total_after = _workspace_count(db, brand_profile_id)
    created = created_new + linked_existing
    skipped = len(accepted_plan) - created
    limit_note = (
        f" | {skipped_limit} satır havuz limiti ({pool_limit}) nedeniyle atlandı"
        if skipped_limit else ""
    )
    return {
        "created": created,
        "skipped": skipped,
        "requested": len(accepted_plan),
        "parsed": len(accepted_plan),
        "accepted": created,
        "created_new": created_new,
        "linked_existing": linked_existing,
        "skipped_due_to_race": skipped_due_to_race,
        "skipped_limit": skipped_limit,
        "pool_limit": pool_limit or 0,
        "pool_total": total_after,
        "total_before": total_before,
        "total_after": total_after,
        "message": (
            f"{created} keyword eklendi/bağlandı, {skipped} atlandı "
            f"(toplam: {total_before} -> {total_after}){limit_note}"
        ),
    }


def import_plan_to_payload(plan: ImportPlan) -> dict[str, Any]:
    summary = plan.summary
    projected_total = plan.total_before + summary["accepted"]
    limit_warning = ""
    if plan.pool_limit and projected_total > plan.pool_limit:
        limit_warning = (
            f" | UYARI: bu dosya havuz limitini aşacak "
            f"({projected_total}/{plan.pool_limit}); commit'te fazlası atlanır"
        )
    theme_note = (
        f" | {summary['skipped_theme']} satır yasaklı temayla elendi"
        if summary.get("skipped_theme") else ""
    )
    # P1.6: uyarı ELEME DEĞİL — satırlar eklendi, kullanıcı gözden geçirsin.
    warn_note = (
        f" | {summary['warned_theme']} satır AI dışlama temasına takıldı "
        f"(eklendi, gözden geçirin)"
        if summary.get("warned_theme") else ""
    )
    return {
        **summary,
        "created": 0,
        "created_new": 0,
        "linked_existing": 0,
        "skipped_due_to_race": 0,
        "pool_limit": plan.pool_limit or 0,
        "pool_total": projected_total,
        "total_before": plan.total_before,
        "total_after": plan.total_before,
        "dry_run_token": None,
        "parser_meta": plan.parser_meta,
        "accepted_keywords": plan.accepted_keywords[:50],
        "excluded_keywords": plan.excluded_keywords[:50],
        "exact_duplicates": plan.exact_duplicates[:50],
        "exact_duplicates_different_metrics": plan.exact_duplicates_different_metrics[:50],
        "fuzzy_skipped": plan.fuzzy_skipped[:50],
        "fuzzy_kept": plan.fuzzy_kept[:50],
        "junk_rows": plan.junk_rows[:50],
        "theme_excluded": plan.theme_excluded[:50],
        "theme_warned": plan.theme_warned[:50],
        "message": (
            f"{summary['parsed']} satır analiz edildi: "
            f"{summary['accepted']} eklenecek, {summary['skipped']} atlanacak"
            f"{theme_note}{warn_note}{limit_warning}"
        ),
    }


def compact_plan_payload(plan: ImportPlan) -> dict[str, Any]:
    return {
        "brand_profile_id": plan.brand_profile_id,
        "source_file_name": plan.source_file_name,
        "parser_meta": plan.parser_meta,
        "summary": plan.summary,
        "accepted_plan": [
            {
                "action": item["action"],
                "keyword_id": item.get("keyword_id"),
                "keyword_data": item["keyword_data"],
            }
            for item in plan.accepted_plan
        ],
    }


def _workspace_count(db: Session, brand_profile_id: int) -> int:
    return db.query(WorkspaceKeyword).filter(WorkspaceKeyword.brand_profile_id == brand_profile_id).count()


def _workspace_rows(db: Session, brand_profile_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .all()
    )
    return [
        {
            "keyword": kw.keyword,
            "normalized_keyword": kw.normalized_keyword or normalize_keyword(kw.keyword),
            "keyword_id": kw.id,
            "monthly_volume": wk.monthly_volume or 0,
            "competition_score": float(wk.competition_score or 0.5),
            "matched_in_workspace": True,
        }
        for kw, wk in rows
    ]


def _workspace_has_exact_snapshot(
    db: Session,
    brand_profile_id: int,
    keyword_id: int,
    keyword_data: dict[str, Any],
) -> bool:
    """Return True if a WorkspaceKeyword row with identical keyword_id + metrics already exists."""
    vol = max(1, int(keyword_data.get("monthly_volume") or 1))
    cs = _safe_competition(keyword_data.get("competition_score"))
    return (
        db.query(WorkspaceKeyword.id)
        .filter(
            WorkspaceKeyword.brand_profile_id == brand_profile_id,
            WorkspaceKeyword.keyword_id == keyword_id,
            WorkspaceKeyword.monthly_volume == vol,
            WorkspaceKeyword.competition_score == cs,
        )
        .first()
        is not None
    )


def _find_fuzzy_match(
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], int, str, bool] | None:
    if fuzz is None:
        return None
    keyword_text = (row.get("keyword") or "").lower().strip()
    stemmed_new = strip_turkish_suffixes(keyword_text)
    for candidate in candidates:
        # Stem, build_import_plan tarafindan bir kez hesaplanip cache'lenir;
        # cache'siz cagrilar icin (testler/dis kullanim) eski davranisa duser.
        stemmed_cand = candidate.get("stemmed_keyword")
        if stemmed_cand is None:
            stemmed_cand = strip_turkish_suffixes(candidate["keyword"].lower().strip())
        ratio = int(round(fuzz.ratio(stemmed_new, stemmed_cand)))
        if ratio < FUZZY_THRESHOLD:
            continue
        if has_meaningful_metrics(row) and has_meaningful_metrics(candidate):
            if are_metrics_equal(row, candidate):
                return candidate, ratio, "fuzzy_same_metrics", True
            return candidate, ratio, "fuzzy_different_metrics_keep", False
        return candidate, ratio, "fuzzy_default_metrics_keep", False
    return None


def _metrics_differ(row: dict[str, Any], existing: dict[str, Any]) -> bool:
    return not are_metrics_equal(row, existing)


def _detail(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "keyword": row.get("keyword"),
        "source_file": row.get("_source_file"),
        "source_row": row.get("_source_row"),
        "reason": reason,
        "matched_keyword": None,
        "matched_keyword_id": None,
        "matched_in_workspace": False,
        "match_ratio": None,
        "monthly_volume": row.get("monthly_volume"),
        "trend_3m": _float_or_none(row.get("trend_3m")),
        "trend_12m": _float_or_none(row.get("trend_12m")),
        "competition_score": float(row.get("competition_score", 0.5)),
        "data_source": row.get("data_source") or "csv",
        "geo_target_id": row.get("geo_target_id"),
        "language_id": row.get("language_id"),
        "competition_index": row.get("_competition_index"),
        "top_bid_high": _float_or_none(row.get("_top_bid_high")),
    }


def _with_match(
    detail: dict[str, Any],
    matched: dict[str, Any],
    *,
    reason: str,
    match_ratio: int,
) -> dict[str, Any]:
    return {
        **detail,
        "reason": reason,
        "matched_keyword": matched.get("keyword"),
        "matched_keyword_id": matched.get("keyword_id"),
        "matched_in_workspace": bool(matched.get("matched_in_workspace")),
        "match_ratio": match_ratio,
    }


def _keyword_data_for_write(row: dict[str, Any], normalized: str) -> dict[str, Any]:
    return {
        "keyword": row.get("keyword"),
        "normalized_keyword": normalized,
        "monthly_volume": max(1, int(row.get("monthly_volume") or 1)),
        "trend_3m": row.get("trend_3m") or Decimal("0"),
        "trend_12m": row.get("trend_12m") or Decimal("0"),
        "competition_score": _safe_competition(row.get("competition_score")),
        "data_source": row.get("data_source") or "csv",
        "sector": row.get("sector"),
        "target_market": row.get("target_market"),
        "geo_target_id": row.get("geo_target_id"),
        "language_id": row.get("language_id"),
    }


def _create_workspace_link(
    db: Session,
    *,
    brand_profile_id: int,
    keyword_id: int,
    keyword_data: dict[str, Any],
    commit: bool = True,
) -> None:
    wk = WorkspaceKeyword(
        brand_profile_id=brand_profile_id,
        keyword_id=keyword_id,
        monthly_volume=max(1, int(keyword_data.get("monthly_volume") or 1)),
        trend_3m=keyword_data.get("trend_3m") or Decimal("0"),
        trend_12m=keyword_data.get("trend_12m") or Decimal("0"),
        competition_score=_safe_competition(keyword_data.get("competition_score")),
        data_source=keyword_data.get("data_source") or "csv",
        sector=keyword_data.get("sector"),
        target_market=keyword_data.get("target_market"),
        geo_target_id=keyword_data.get("geo_target_id"),
        language_id=keyword_data.get("language_id"),
    )
    db.add(wk)
    if commit:
        db.commit()


def _safe_competition(value: Any) -> Decimal:
    try:
        comp = Decimal(str(value if value is not None else "0.5"))
    except Exception:
        comp = Decimal("0.5")
    if comp <= 0:
        return Decimal("0.01")
    if comp > 1:
        return Decimal("1")
    return comp


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None
