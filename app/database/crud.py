"""
CRUD operations for database models.
"""
from typing import List, Optional, Dict, Any, Union, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_
from loguru import logger

from app.database.models import (
    Keyword, ScoringRun, KeywordScore, 
    ChannelCandidate, IntentAnalysis, ChannelPool,
    ContentOutput, ComplianceCheck,
    BrandProfile, WorkspaceKeyword,
)
from app.core.keyword_dedup import deduplicate_keywords
from app.core.keyword_junk import junk_reason
from app.core.keyword_normalize import normalize_keyword


_KEYWORD_MODEL_FIELDS = {
    "keyword",
    "normalized_keyword",
    "monthly_volume",
    "trend_12m",
    "trend_3m",
    "competition_score",
    "sector",
    "target_market",
    "is_active",
    "data_source",
}
_LEGACY_KEYWORD_DATA_SOURCES = {"csv", "google_ads_api"}


def _legacy_keyword_data_source(source: Optional[str]) -> str:
    """
    Keyword.data_source has a legacy DB check constraint.
    New source labels live on WorkspaceKeyword.data_source.
    """
    return source if source in _LEGACY_KEYWORD_DATA_SOURCES else "csv"


def _keyword_insert_data(kw_data: Dict[str, Any]) -> Dict[str, Any]:
    data = {k: v for k, v in kw_data.items() if k in _KEYWORD_MODEL_FIELDS}
    data["data_source"] = _legacy_keyword_data_source(data.get("data_source"))
    return data


# ==================== KEYWORD CRUD ====================

def get_keyword(db: Session, keyword_id: int) -> Optional[Keyword]:
    """Get a keyword by ID."""
    return db.query(Keyword).filter(Keyword.id == keyword_id).first()


def get_keyword_by_text(db: Session, keyword: str) -> Optional[Keyword]:
    """Get a keyword by text."""
    return db.query(Keyword).filter(Keyword.keyword == keyword).first()


def get_keywords(
    db: Session, 
    skip: int = 0, 
    limit: int = 100,
    active_only: bool = True,
    sector: Optional[str] = None
) -> List[Keyword]:
    """Get list of keywords with optional filters."""
    query = db.query(Keyword)
    
    if active_only:
        query = query.filter(Keyword.is_active == True)
    
    if sector:
        query = query.filter(Keyword.sector == sector)
    
    return query.offset(skip).limit(limit).all()


def get_all_active_keywords(db: Session) -> List[Keyword]:
    """Get all active keywords."""
    return db.query(Keyword).filter(Keyword.is_active == True).all()


def create_keyword(db: Session, keyword_data: Dict[str, Any]) -> Keyword:
    """Create a new keyword."""
    db_keyword = Keyword(**_keyword_insert_data(keyword_data))
    db.add(db_keyword)
    db.commit()
    db.refresh(db_keyword)
    return db_keyword


def create_keywords_bulk(
    db: Session,
    keywords_data: List[Dict[str, Any]],
    brand_profile_id: Optional[int] = None,
    return_details: bool = False,
    theme_policy: Any = None,
    pool_limit: Optional[int] = None,
) -> Union[int, Dict[str, int]]:
    """
    Create multiple keywords at once.

    Args:
        brand_profile_id: workspace scope (None = legacy global, backward compatibility)
        return_details: True → dict with {created, linked, skipped_exact, skipped_fuzzy};
                       False → int (legacy semantik: created + linked)
        theme_policy: `ImportThemePolicy` (P1.6) — hard/advisory/protected ayrımı.
                      YALNIZ kullanıcı onaylı (hard) temalar eler; AI kaynaklı
                      temalar `theme_warnings` üretir ve satır KABUL edilir.
                      Satırdaki force_include=True YALNIZ hard elemeyi atlar —
                      duplicate ve limit kontrollerini ATLAMAZ.
                      None → tema kapısı hiç çalışmaz.
        pool_limit: workspace havuz üst limiti; yalnız havuza gerçekten yeni satır
                    ekleyecek adaylara uygulanır (duplicate satırlar kendi skip
                    nedenleriyle raporlanır).

    Filtre sırası: çöp kelime (junk_reason; force_include atlamaz, her iki yol)
    → batch dedup. Workspace yolu devamı: tema → exact/fuzzy duplicate → limit.
    Legacy (brand_profile_id=None) yol `return_details=True` ile de dict döner
    (skipped_junk dahil); aksi halde int.

    Returns:
        int (legacy): created + linked (workspace mode'da "ne kadar kw geldi" semantiği)
        dict: {created, linked, skipped_exact, skipped_fuzzy, skipped_theme,
               skipped_limit, pool_total, theme_warnings}
    """
    created = 0
    linked = 0
    skipped_exact = 0
    skipped_fuzzy = 0
    skipped_theme = 0
    skipped_limit = 0
    skipped_global = 0
    skipped_junk = 0
    skipped_details: List[Dict[str, str]] = []
    # P1.6: elenmeyen ama AI dislama temasina takilan satirlar (uyari).
    theme_warnings: List[Dict[str, Any]] = []

    # Sanitize data before inserting
    sanitized_keywords = []
    for kw_data in keywords_data:
        # Ortak cop filtresi (plan 3.3): bos / yalniz sembol / yalniz sayi.
        # Batch dedup'tan ONCE; force_include BUNU ATLAMAZ.
        _junk = junk_reason(kw_data.get('keyword'))
        if _junk is not None:
            skipped_junk += 1
            skipped_details.append({
                'keyword': (kw_data.get('keyword') or '').strip(),
                'reason': 'skipped_junk',
                'matched': _junk,
            })
            continue

        # Sanitize trend values to prevent overflow (Numeric(7,2) max is 99999.99)
        if 'trend_12m' in kw_data:
            try:
                val = float(kw_data['trend_12m'])
                if not (-9999 <= val <= 9999):
                    kw_data['trend_12m'] = 0
            except (TypeError, ValueError):
                kw_data['trend_12m'] = 0

        if 'trend_3m' in kw_data:
            try:
                val = float(kw_data['trend_3m'])
                if not (-9999 <= val <= 9999):
                    kw_data['trend_3m'] = 0
            except (TypeError, ValueError):
                kw_data['trend_3m'] = 0

        sanitized_keywords.append(kw_data)

    # Fuzzy deduplication: merge within-batch duplicates (Turkish stemming + Levenshtein)
    before_dedup = len(sanitized_keywords)
    sanitized_keywords, batch_merge_details = deduplicate_keywords(
        sanitized_keywords, return_merge_details=True
    )
    fuzzy_merged = before_dedup - len(sanitized_keywords)
    if fuzzy_merged > 0:
        logger.info(f"Fuzzy dedup: {fuzzy_merged} within-batch duplicate(s) merged")
        for merge in batch_merge_details:
            skipped_details.append({
                'keyword': merge['dropped'],
                'reason': 'batch_duplicate',
                'matched': merge['kept'],
            })

    # ── Workspace-scoped import path ──────────────────────────────────
    if brand_profile_id is not None:
        # Pre-load workspace snapshot once — avoids N DB queries inside the loop.
        try:
            from rapidfuzz import fuzz as _fuzz_mod
        except ImportError:
            try:
                from thefuzz import fuzz as _fuzz_mod
            except ImportError:
                _fuzz_mod = None

        _ws_cache = (
            db.query(Keyword, WorkspaceKeyword)
            .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
            .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
            .all()
        ) if _fuzz_mod else []

        # Pre-build exact-snapshot set: (normalized_keyword, monthly_volume, competition_score)
        # Used for O(1) duplicate detection instead of a per-keyword DB query.
        from decimal import Decimal, InvalidOperation

        def _norm_vol_fast(v) -> int:
            try:
                return max(1, int(v or 1))
            except (TypeError, ValueError):
                return 1

        def _norm_cs_fast(v) -> Decimal:
            try:
                cs = Decimal(str(v)) if v is not None else Decimal("0.5")
            except (InvalidOperation, TypeError, ValueError):
                cs = Decimal("0.5")
            if cs <= 0:
                return Decimal("0.01")
            return min(cs, Decimal("1"))

        _exact_snap_set = {
            (
                normalize_keyword(kw.keyword),
                _norm_vol_fast(wk.monthly_volume),
                _norm_cs_fast(wk.competition_score),
            )
            for kw, wk in _ws_cache
        }

        # Stem'ler BIR KEZ hesaplanir (keyword_id → stem); onceki desen her
        # gelen kelime icin tum workspace kelimelerini yeniden stem'liyordu.
        from app.core.keyword_dedup import strip_turkish_suffixes as _strip_tr
        _ws_stem_cache = {
            kw.id: _strip_tr(kw.keyword.lower().strip())
            for kw, _wk in _ws_cache
        }

        # Global Keyword lookup'lari TEK sorguda on-yukle (kelime basina SELECT
        # yerine). create sonrasi harita guncellenir; ayni-batch duplicate'ler
        # eski per-kw-commit gorunurluk davranisindaki gibi LINK yoluna girer.
        _incoming_norms = {
            normalize_keyword((k.get('keyword') or '').strip())
            for k in sanitized_keywords
            if (k.get('keyword') or '').strip()
        }
        _global_kw_map: Dict[tuple, int] = {}
        if _incoming_norms:
            for _gk in (
                db.query(Keyword)
                .filter(Keyword.normalized_keyword.in_(_incoming_norms))
                .all()
            ):
                _global_kw_map.setdefault(
                    (_gk.normalized_keyword, _gk.monthly_volume, _gk.competition_score),
                    _gk.id,
                )

        # Kelime basina COMMIT yerine chunk basina tek commit: 2000+ importta
        # fsync sayisi ~2000 → ~10'a iner. Yavas disk/contention altinda 4dk'ya
        # sisen importlarin kok nedeni per-kw fsync carpaniydi.
        _CRUD_IMPORT_COMMIT_CHUNK = 200
        _pending_in_chunk = 0

        # Havuz doluluk sayaci: transaction icinde gercek satir sayisiyla baslar
        # (concurrent import'ta _ws_cache'e guvenilmez; fuzz yoksa cache zaten bos).
        _gate_active = theme_policy is not None and not theme_policy.is_empty
        _current_total = (
            db.query(WorkspaceKeyword)
            .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
            .count()
        ) if pool_limit is not None else 0

        if _gate_active:
            from app.core.policy.import_gate import decide_keyword_theme

        def _detail_metrics(kw_data: Dict[str, Any]) -> Dict[str, Any]:
            """'Geri al' minimum payload'ı response'tan kurulabilsin diye metrik taşı."""
            def _f(v):
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None
            return {
                'monthly_volume': kw_data.get('monthly_volume'),
                'trend_3m': _f(kw_data.get('trend_3m')),
                'trend_12m': _f(kw_data.get('trend_12m')),
                'competition_score': _f(kw_data.get('competition_score')),
                'data_source': kw_data.get('data_source'),
                'geo_target_id': kw_data.get('geo_target_id'),
                'language_id': kw_data.get('language_id'),
            }

        for kw_data in sanitized_keywords:
            keyword_text = (kw_data.get('keyword') or '').strip()

            # 1) Tema kapisi — import_plan ile AYNI karar fonksiyonu (P1.6).
            #    force_include YALNIZ hard elemeyi atlar (duplicate/limit degil).
            if _gate_active:
                _decision = decide_keyword_theme(
                    keyword_text,
                    theme_policy,
                    force_include=bool(kw_data.get('force_include')),
                )
                if _decision.is_excluded:
                    skipped_theme += 1
                    skipped_details.append({
                        'keyword': keyword_text,
                        'reason': 'skipped_theme',
                        'matched': _decision.matched_exclude_theme,
                        **_detail_metrics(kw_data),
                    })
                    continue
                if _decision.has_advisory_warning:
                    # ELEME DEGIL: AI kaynakli tema yalniz uyari uretir.
                    theme_warnings.append({
                        'keyword': keyword_text,
                        'reason': 'theme_warning',
                        'matched': _decision.matched_advisory_theme,
                        **_detail_metrics(kw_data),
                    })

            # 2) Havuz limiti dolduysa: duplicate mi yeni mi ayir (duplicate satir
            #    kendi nedeniyle raporlanir, yalniz YENI adaylar limit_exceeded olur)
            if pool_limit is not None and _current_total >= pool_limit:
                status, _kw_id, matched_text = _import_with_workspace_link(
                    db, kw_data, brand_profile_id,
                    ws_cache=_ws_cache,
                    exact_snap_set=_exact_snap_set,
                    ws_stem_cache=_ws_stem_cache,
                    global_kw_map=_global_kw_map,
                    defer_commit=True,
                    check_only=True,
                )
                if status == 'skipped_exact':
                    skipped_exact += 1
                    skipped_details.append({
                        'keyword': keyword_text,
                        'reason': 'skipped_exact',
                        'matched': matched_text or keyword_text,
                    })
                elif status == 'skipped_fuzzy':
                    skipped_fuzzy += 1
                    skipped_details.append({
                        'keyword': keyword_text,
                        'reason': 'skipped_fuzzy',
                        'matched': matched_text or '',
                    })
                else:
                    skipped_limit += 1
                    skipped_details.append({
                        'keyword': keyword_text,
                        'reason': 'limit_exceeded',
                        'matched': None,
                        **_detail_metrics(kw_data),
                    })
                continue

            result = _import_with_workspace_link(
                db, kw_data, brand_profile_id,
                ws_cache=_ws_cache,
                exact_snap_set=_exact_snap_set,
                ws_stem_cache=_ws_stem_cache,
                global_kw_map=_global_kw_map,
                defer_commit=True,
            )
            status, _kw_id, matched_text = result
            if status in ('created', 'linked'):
                _current_total += 1
                _pending_in_chunk += 1
                if _pending_in_chunk >= _CRUD_IMPORT_COMMIT_CHUNK:
                    db.commit()
                    _pending_in_chunk = 0
            if status == 'created':
                created += 1
            elif status == 'linked':
                linked += 1
            elif status == 'skipped_exact':
                skipped_exact += 1
                skipped_details.append({
                    'keyword': kw_data.get('keyword', ''),
                    'reason': 'skipped_exact',
                    'matched': matched_text or kw_data.get('keyword', ''),
                })
            elif status == 'skipped_fuzzy':
                skipped_fuzzy += 1
                skipped_details.append({
                    'keyword': kw_data.get('keyword', ''),
                    'reason': 'skipped_fuzzy',
                    'matched': matched_text or '',
                })

        # Son (kismi) chunk'in flush'lanmis yazimlarini kalici yap.
        db.commit()

        if return_details:
            return {
                'created': created,
                'linked': linked,
                'skipped_exact': skipped_exact,
                'skipped_fuzzy': skipped_fuzzy,
                'skipped_theme': skipped_theme,
                'skipped_limit': skipped_limit,
                'skipped_junk': skipped_junk,
                'fuzzy_merged_in_batch': fuzzy_merged,
                'skipped_details': skipped_details,
                # P1.6: eleme DEGIL — kabul edilmis ama AI temasina takilan satirlar
                'theme_warnings': theme_warnings,
            }
        return created + linked

    # ── Legacy global import path (backward-compat) ───────────────────
    # Cross-batch fuzzy dedup: check new keywords against existing DB keywords
    from app.core.keyword_dedup import (
        FUZZY_THRESHOLD,
        are_metrics_equal,
        has_meaningful_metrics,
        strip_turkish_suffixes,
    )
    try:
        from thefuzz import fuzz
    except ImportError:
        fuzz = None

    existing_keywords = db.query(Keyword).filter(Keyword.is_active == True).all()
    # Stem'ler BIR KEZ hesaplanir; onceki desen her yeni kelime icin tum mevcut
    # kelimeleri yeniden stem'liyordu (O(n*m) stem maliyeti).
    existing_stemmed = [
        (ex_kw, strip_turkish_suffixes(ex_kw.keyword.lower().strip()))
        for ex_kw in existing_keywords
    ]

    unique_keywords = []
    cross_batch_merged = 0

    for kw_data in sanitized_keywords:
        keyword_text = kw_data.get('keyword', '')
        if not keyword_text:
            unique_keywords.append(kw_data)
            continue

        # 1) Exact match check (fast path)
        existing = get_keyword_by_text(db, keyword_text)
        if existing:
            skipped_global += 1
            continue

        # 2) Fuzzy match against existing DB keywords (if thefuzz available)
        is_fuzzy_dup = False
        if fuzz and existing_stemmed:
            stemmed_new = strip_turkish_suffixes(keyword_text.lower().strip())
            for ex_kw, stemmed_ex in existing_stemmed:
                ratio = fuzz.ratio(stemmed_new, stemmed_ex)
                if ratio >= FUZZY_THRESHOLD:
                    new_metrics = {
                        'monthly_volume': kw_data.get('monthly_volume', 0),
                        'competition_score': kw_data.get('competition_score', 0)
                    }
                    ex_metrics = {
                        'monthly_volume': ex_kw.monthly_volume,
                        'competition_score': ex_kw.competition_score
                    }
                    if (
                        has_meaningful_metrics(new_metrics)
                        and has_meaningful_metrics(ex_metrics)
                        and are_metrics_equal(new_metrics, ex_metrics)
                    ):
                        is_fuzzy_dup = True
                        cross_batch_merged += 1
                        logger.debug(
                            f"Cross-batch fuzzy dup skipped: '{keyword_text}' ≈ '{ex_kw.keyword}' "
                            f"(ratio={ratio}%)"
                        )
                        break

        if is_fuzzy_dup:
            skipped_global += 1
            continue

        unique_keywords.append(kw_data)

    if cross_batch_merged > 0:
        logger.info(f"Cross-batch fuzzy dedup: {cross_batch_merged} duplicate(s) skipped")

    # Insert in batches
    batch_size = 50
    for i in range(0, len(unique_keywords), batch_size):
        batch = unique_keywords[i:i + batch_size]
        try:
            for kw_data in batch:
                db_keyword = Keyword(**_keyword_insert_data(kw_data))
                db.add(db_keyword)
            db.commit()
            created += len(batch)
        except Exception as e:
            db.rollback()
            # Try one by one if batch failed
            for kw_data in batch:
                try:
                    db_keyword = Keyword(**_keyword_insert_data(kw_data))
                    db.add(db_keyword)
                    db.commit()
                    created += 1
                except Exception as inner_e:
                    db.rollback()
                    skipped_global += 1
                    print(f"Keyword import error: {kw_data.get('keyword', 'unknown')}: {str(inner_e)[:100]}")

    if return_details:
        # Legacy yol artik detay da verebilir: cop sayisi fuzzy olarak YANLIS
        # sayilmasin diye cagiranlar (google_ads service) ayri okur.
        return {
            'created': created,
            'linked': 0,
            'skipped_exact': 0,
            'skipped_fuzzy': 0,
            'skipped_theme': 0,
            'skipped_limit': 0,
            'skipped_junk': skipped_junk,
            'skipped_global': skipped_global,
            'fuzzy_merged_in_batch': fuzzy_merged,
            'skipped_details': skipped_details,
            'theme_warnings': [],
        }
    return created


def _import_with_workspace_link(
    db: Session,
    kw_data: Dict[str, Any],
    brand_profile_id: int,
    ws_cache: Optional[List] = None,
    exact_snap_set: Optional[set] = None,
    ws_stem_cache: Optional[Dict[int, str]] = None,
    global_kw_map: Optional[Dict[tuple, int]] = None,
    defer_commit: bool = False,
    check_only: bool = False,
) -> Tuple[str, Optional[int], Optional[str]]:
    """
    Import single keyword with workspace link (Option B snapshot semantics).

    Sıra:
      1. Workspace snapshot EXACT lookup (same normalized + same metrics → skipped_exact)
      2. Workspace fuzzy dedup (farklı text, aynı metrik → skipped_fuzzy)
      3. Global Option B lookup (normalized + vol + cs)
      4. Hiç yoksa: yeni Keyword + WorkspaceKeyword

    ws_cache / exact_snap_set: caller tarafından pre-load edilmiş snapshot'lar.
    Sağlanırsa per-keyword DB sorguları yapılmaz (O(N) → O(1) / O(M)).

    check_only: yalnız duplicate tespiti yapar, DB'ye YAZMAZ; havuza yeni satır
    ekleyecek adaylar için ('would_add', None, None) döner. Havuz limiti dolunca
    kalan satırların duplicate mi yeni mi olduğunu raporlamak için kullanılır
    (duplicate satır 'limit_exceeded' DEĞİL kendi skip nedeniyle raporlanmalı).
    """
    from decimal import Decimal, InvalidOperation

    def _norm_vol(v) -> int:
        try:
            return max(1, int(v or 1))
        except (TypeError, ValueError):
            return 1

    def _norm_cs(v) -> Decimal:
        try:
            cs = Decimal(str(v)) if v is not None else Decimal("0.5")
        except (InvalidOperation, Exception):
            cs = Decimal("0.5")
        if cs <= 0:
            return Decimal("0.01")
        return min(cs, Decimal("1"))

    keyword_text = kw_data.get('keyword', '')
    if not keyword_text:
        return ('skipped_fuzzy', None, None)

    normalized = normalize_keyword(keyword_text)
    target_vol = _norm_vol(kw_data.get('monthly_volume'))
    target_cs = _norm_cs(kw_data.get('competition_score'))

    # 1. Exact snapshot: O(1) set lookup when cache available, DB query otherwise.
    if exact_snap_set is not None:
        if (normalized, target_vol, target_cs) in exact_snap_set:
            return ('skipped_exact', None, keyword_text)
    else:
        exact_snap = (
            db.query(WorkspaceKeyword)
            .join(Keyword, Keyword.id == WorkspaceKeyword.keyword_id)
            .filter(
                WorkspaceKeyword.brand_profile_id == brand_profile_id,
                Keyword.normalized_keyword == normalized,
                WorkspaceKeyword.monthly_volume == target_vol,
                WorkspaceKeyword.competition_score == target_cs,
            )
            .first()
        )
        if exact_snap:
            return ('skipped_exact', exact_snap.keyword_id, keyword_text)

    # 2. Workspace fuzzy dedup (different text, same metrics → skip)
    from app.core.keyword_dedup import (
        FUZZY_THRESHOLD,
        are_metrics_equal,
        has_meaningful_metrics,
        strip_turkish_suffixes,
    )
    try:
        from rapidfuzz import fuzz
    except ImportError:
        try:
            from thefuzz import fuzz
        except ImportError:
            fuzz = None

    if fuzz:
        # Use pre-loaded cache; fall back to DB query only if cache was not provided.
        workspace_kws = ws_cache if ws_cache is not None else (
            db.query(Keyword, WorkspaceKeyword)
            .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
            .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
            .all()
        )
        stemmed_new = strip_turkish_suffixes(keyword_text.lower().strip())
        new_metrics = {
            'monthly_volume': target_vol,
            'competition_score': float(target_cs),
        }
        for ex_kw, ex_wk in workspace_kws:
            # Stem cache: bulk import yolunda bir kez hesaplanir; cache'siz
            # (tekil) cagrilarda eski davranisa duser.
            stemmed_ex = (
                ws_stem_cache.get(ex_kw.id)
                if ws_stem_cache is not None else None
            )
            if stemmed_ex is None:
                stemmed_ex = strip_turkish_suffixes(ex_kw.keyword.lower().strip())
            ratio = fuzz.ratio(stemmed_new, stemmed_ex)
            if ratio >= FUZZY_THRESHOLD:
                ex_metrics = {
                    'monthly_volume': _norm_vol(ex_wk.monthly_volume),
                    'competition_score': float(_norm_cs(ex_wk.competition_score)),
                }
                if (
                    has_meaningful_metrics(new_metrics)
                    and has_meaningful_metrics(ex_metrics)
                    and are_metrics_equal(new_metrics, ex_metrics)
                ):
                    return ('skipped_fuzzy', None, ex_kw.keyword)

    if check_only:
        return ('would_add', None, None)

    # 3. Global Option B lookup: normalized + vol + cs
    # Bulk yolunda on-yuklenmis harita kullanilir (kelime basina SELECT yerine);
    # harita verilmemisse (tekil cagri) eski per-kw sorgu davranisi korunur.
    if global_kw_map is not None:
        existing_kw_id = global_kw_map.get((normalized, target_vol, target_cs))
    else:
        _row = (
            db.query(Keyword)
            .filter(
                Keyword.normalized_keyword == normalized,
                Keyword.monthly_volume == target_vol,
                Keyword.competition_score == target_cs,
            )
            .first()
        )
        existing_kw_id = _row.id if _row else None
    if existing_kw_id is not None:
        wk = WorkspaceKeyword(
            brand_profile_id=brand_profile_id,
            keyword_id=existing_kw_id,
            monthly_volume=target_vol,
            trend_3m=kw_data.get('trend_3m', 0),
            trend_12m=kw_data.get('trend_12m', 0),
            competition_score=target_cs,
            data_source=kw_data.get('data_source', 'csv'),
            sector=kw_data.get('sector'),
            target_market=kw_data.get('target_market'),
            geo_target_id=kw_data.get('geo_target_id'),
            language_id=kw_data.get('language_id'),
        )
        db.add(wk)
        if defer_commit:
            db.flush()
        else:
            db.commit()
        return ('linked', existing_kw_id, None)

    # 4. New Keyword + WorkspaceKeyword
    new_kw = Keyword(
        keyword=keyword_text,
        normalized_keyword=normalized,
        is_active=True,
        monthly_volume=target_vol,
        trend_3m=kw_data.get('trend_3m', 0),
        trend_12m=kw_data.get('trend_12m', 0),
        competition_score=target_cs,
        data_source=_legacy_keyword_data_source(kw_data.get('data_source', 'csv')),
        sector=kw_data.get('sector'),
        target_market=kw_data.get('target_market'),
    )
    db.add(new_kw)
    db.flush()

    wk = WorkspaceKeyword(
        brand_profile_id=brand_profile_id,
        keyword_id=new_kw.id,
        monthly_volume=target_vol,
        trend_3m=kw_data.get('trend_3m', 0),
        trend_12m=kw_data.get('trend_12m', 0),
        competition_score=target_cs,
        data_source=kw_data.get('data_source', 'csv'),
        sector=kw_data.get('sector'),
        target_market=kw_data.get('target_market'),
        geo_target_id=kw_data.get('geo_target_id'),
        language_id=kw_data.get('language_id'),
    )
    db.add(wk)
    if defer_commit:
        db.flush()
    else:
        db.commit()
    # Ayni batch'te ayni (norm, vol, cs) tekrar gelirse duplicate CREATE yerine
    # LINK yoluna girsin — eski per-kw-commit gorunurluk davranisiyla ayni.
    if global_kw_map is not None:
        global_kw_map.setdefault((normalized, target_vol, target_cs), new_kw.id)
    return ('created', new_kw.id, None)


def get_keywords_by_workspace(
    db: Session,
    brand_profile_id: int,
    skip: int = 0,
    limit: int = 100,
) -> List[Keyword]:
    """Get keywords linked to a workspace via WorkspaceKeyword."""
    return (
        db.query(Keyword)
            .join(WorkspaceKeyword)
            .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
            .filter(Keyword.is_active == True)
            .offset(skip)
            .limit(limit)
            .all()
    )


def update_keyword(db: Session, keyword_id: int, update_data: Dict[str, Any]) -> Optional[Keyword]:
    """Update a keyword."""
    db_keyword = get_keyword(db, keyword_id)
    if db_keyword:
        for key, value in update_data.items():
            setattr(db_keyword, key, value)
        db.commit()
        db.refresh(db_keyword)
    return db_keyword


def delete_keyword(db: Session, keyword_id: int) -> bool:
    """Delete a keyword."""
    db_keyword = get_keyword(db, keyword_id)
    if db_keyword:
        db.delete(db_keyword)
        db.commit()
        return True
    return False


def remove_keyword_from_workspace(
    db: Session,
    keyword_id: int,
    brand_profile_id: int,
) -> bool:
    """
    Remove a keyword link from a workspace (WorkspaceKeyword soft-remove).
    Does NOT delete the global Keyword record — other workspaces may still use it.
    Returns True if link existed and was removed, False if no link found.
    """
    wk = (
        db.query(WorkspaceKeyword)
        .filter(
            WorkspaceKeyword.keyword_id == keyword_id,
            WorkspaceKeyword.brand_profile_id == brand_profile_id,
        )
        .first()
    )
    if wk:
        db.delete(wk)
        db.commit()
        return True
    return False


def remove_workspace_keyword_by_id(
    db: Session,
    wk_id: int,
    brand_profile_id: int,
) -> bool:
    """Delete a single WorkspaceKeyword row by its own PK (wk_id).

    Enforces brand_profile_id so a client can't delete another workspace's row.
    """
    wk = (
        db.query(WorkspaceKeyword)
        .filter(
            WorkspaceKeyword.id == wk_id,
            WorkspaceKeyword.brand_profile_id == brand_profile_id,
        )
        .first()
    )
    if wk:
        db.delete(wk)
        db.commit()
        return True
    return False


def delete_all_keywords(db: Session) -> int:
    """Delete all keywords. Returns number of deleted items."""
    count = db.query(Keyword).delete()
    db.commit()
    return count


# ==================== SCORING RUN CRUD ====================

def get_scoring_run(db: Session, run_id: int) -> Optional[ScoringRun]:
    """Get a scoring run by ID."""
    return db.query(ScoringRun).filter(ScoringRun.id == run_id).first()


def get_scoring_runs(db: Session, skip: int = 0, limit: int = 20) -> List[ScoringRun]:
    """Get list of scoring runs."""
    return db.query(ScoringRun).order_by(ScoringRun.created_at.desc()).offset(skip).limit(limit).all()


def create_scoring_run(db: Session, run_data: Dict[str, Any]) -> ScoringRun:
    """Create a new scoring run."""
    db_run = ScoringRun(**run_data)
    db.add(db_run)
    db.commit()
    db.refresh(db_run)
    return db_run


def update_scoring_run_status(db: Session, run_id: int, status: str) -> Optional[ScoringRun]:
    """Update scoring run status."""
    db_run = get_scoring_run(db, run_id)
    if db_run:
        from app.core.scoring.state_machine import transition
        transition(db, db_run, status)
        db.refresh(db_run)
    return db_run


def delete_scoring_run(db: Session, run_id: int) -> bool:
    """Delete a scoring run."""
    db_run = get_scoring_run(db, run_id)
    if db_run:
        db.delete(db_run)
        db.commit()
        return True
    return False


# ==================== KEYWORD SCORE CRUD ====================

def get_keyword_scores_by_run(
    db: Session, 
    scoring_run_id: int,
    limit: int = 1000
) -> List[KeywordScore]:
    """Get all keyword scores for a scoring run."""
    return db.query(KeywordScore).filter(
        KeywordScore.scoring_run_id == scoring_run_id
    ).limit(limit).all()


def create_keyword_score(db: Session, score_data: Dict[str, Any]) -> KeywordScore:
    """Create a keyword score."""
    db_score = KeywordScore(**score_data)
    db.add(db_score)
    db.commit()
    db.refresh(db_score)
    return db_score


def create_keyword_scores_bulk(db: Session, scores_data: List[Dict[str, Any]]) -> int:
    """Create multiple keyword scores at once."""
    for score_data in scores_data:
        db.add(KeywordScore(**score_data))
    db.commit()
    return len(scores_data)


# ==================== CHANNEL POOL CRUD ====================

def get_channel_pool(
    db: Session, 
    scoring_run_id: int, 
    channel: str
) -> List[ChannelPool]:
    """Get channel pool for a specific channel."""
    return db.query(ChannelPool).filter(
        and_(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.channel == channel
        )
    ).order_by(ChannelPool.final_rank).all()


def get_strategic_keywords(db: Session, scoring_run_id: int) -> List[ChannelPool]:
    """Get strategic keywords (appear in multiple channels)."""
    return db.query(ChannelPool).filter(
        and_(
            ChannelPool.scoring_run_id == scoring_run_id,
            ChannelPool.is_strategic == True
        )
    ).all()


# ==================== CONTENT OUTPUT CRUD ====================

def get_content_outputs_by_keyword(
    db: Session, 
    keyword_id: int
) -> List[ContentOutput]:
    """Get all content outputs for a keyword."""
    return db.query(ContentOutput).filter(
        ContentOutput.keyword_id == keyword_id
    ).all()


def create_content_output(db: Session, content_data: Dict[str, Any]) -> ContentOutput:
    """Create a content output."""
    db_content = ContentOutput(**content_data)
    db.add(db_content)
    db.commit()
    db.refresh(db_content)
    return db_content
