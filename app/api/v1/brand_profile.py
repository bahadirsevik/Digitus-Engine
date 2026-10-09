"""
Brand Profile API endpoints.
Site analysis, profile management, and relevance scoring.
"""
import logging
from datetime import datetime, timedelta, timezone
import re
import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.generators.ai_service import AIService
from app.config import settings
from app.core.error_responses import safe_500_detail
from app.database.models import (
    ScoringRun, BrandProfile, KeywordRelevance, Keyword, KeywordScore,
    TaskResult, WorkspaceKeyword,
)
from app.schemas.brand_profile import (
    ProfileConfirmRequest,
    BrandProfileResponse, ChannelSeedInput, ChannelSeedResponse,
    KeywordRelevanceResponse, RelevanceComputeResponse,
    WorkspaceCreateRequest, WorkspaceResponse, WorkspaceListResponse,
    WorkspaceKeywordApproveRequest, WorkspaceKeywordRefreshRequest,
    WorkspaceKeywordRefreshResponse,
    WorkspaceProfileApproveRequest, AnchorPreviewRequest, AnchorPreviewResponse,
    AnchorGroupResponse,
    LocationFilterPreviewRequest, LocationFilterPreviewResponse,
    LocationPreviewCityCount, LocationPreviewSample, LocationPreviewExemptSample,
)
from app.schemas.location_gate import LocationAuditResponse, LocationAuditRow
from app.core.channel_seed import (
    ChannelSeedError, list_channel_seeds, replace_channel_seeds,
)
from app.core.engine_version_gate import (
    LEGACY_RUN_READ_ONLY,
    is_legacy_run,
    require_non_legacy_run,
    require_relevance_not_v3,
    run_algorithm_version,
)
from app.core.workspace import verify_scoring_run, verify_workspace
from app.core.site_analyzer.stuck_janitor import fail_if_stuck
from app.core.site_analyzer.analysis_attempt import (
    lock_if_current,
    lock_workspace_row,
    mark_failed_if_current,
    new_attempt_id,
    start_attempt,
)
from app.core.policy import (
    PolicyValidationError,
    apply_competitor_review,
    apply_profile_data_update,
)
from app.core.policy.guards import find_active_workspace_work
from app.core.policy.review import policy_effect_snapshot
from app.core.scoring.state_machine import (
    invalidate_workspace_outputs,
    transition,
)
from app.core.telemetry import UsageCollector
from app.core.keyword_normalize import normalize_keyword
from app.core.workspace_refresh import (
    ExistingWorkspaceKeyword,
    build_refreshed_workspace_keywords,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _close_ai_quietly(ai) -> None:
    """Background profil akışları: root AI servisinin client'ını kapat
    (Codex v9-3 — plan G sözleşmesinin BackgroundTasks tarafı). Fail-open."""
    closer = getattr(ai, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


LOCKED_PROFILE_FIELDS = {"company_name", "sector", "target_audience"}
EDITABLE_LIST_FIELDS = {
    "products",
    "services",
    "use_cases",
    "problems_solved",
    "brand_terms",
    # Korunacak temalar (firma profili düzeltmesi Faz B): dışlama temasıyla
    # çakışan sorguyu KORUYAN kanonik alan. Allowlist'te olmazsa istemcinin
    # gönderdiği değer sessizce yutulurdu (sanitize yalnız bu kümeyi okur).
    "protected_themes",
    "exclude_themes",
    # Lokasyon politikası (plan_v3_lokasyon_filtresi.md §5.2). Liste
    # semantiği ortaktır; `focus_cities` ayrıca kanonik il adına çevrilir ve
    # desteklenmeyen girdi 400 ile REDDEDİLİR (bkz. _apply_location_policy).
    "focus_cities",
    "location_exempt_terms",
}

# Skaler (liste olmayan) lokasyon alanı — enum, ayrı doğrulanır.
LOCATION_MODE_FIELD = "location_filter_mode"

# Onizleme yanitindaki ornek keyword listeleri icin ust sinir (plan §5.7:
# "sinirli sayida ornek" — tam keyword metinleri toplu cogaltilmaz).
LOCATION_PREVIEW_SAMPLE_LIMIT = 20


def _apply_location_policy(
    sanitized: Dict[str, Any],
    incoming: Dict[str, Any],
    existing: Dict[str, Any],
) -> None:
    """Lokasyon alanlarını doğrular ve kanonikleştirir (yerinde yazar).

    - `location_filter_mode` YALNIZ enum kabul eder; gönderilmezse mevcut
      değer korunur, yeni profilde `none` olur.
    - `focus_cities` kanonik il adına çevrilir; desteklenmeyen girdi sessizce
      SAKLANMAZ, 400 döner (plan §4).
    - `focus_only` modu en az bir odak şehri ZORUNLU kılar.
    """
    from app.core.policy.location_policy import (
        MAX_EXEMPT_TERM_LENGTH, MAX_EXEMPT_TERMS, MAX_FOCUS_CITIES,
        MODE_FOCUS_ONLY, MODE_NONE, VALID_MODES, canonical_city,
        normalize_location_text,
    )

    # Liste alanları GERÇEKTEN liste olmalı. `_normalize_list_items` string'i
    # satırlara bölüp tek elemanlı listeye çevirir; bu alanlarda o sessiz
    # dönüşüm İSTENMEZ ("İstanbul" tek şehir sanılmasın) — tipli 400.
    for field in ("focus_cities", "location_exempt_terms"):
        if field in incoming and not isinstance(incoming.get(field), list):
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_LOCATION_LIST",
                "message": (
                    f"'{field}' bir liste olmalidir; "
                    f"{type(incoming.get(field)).__name__} gonderildi."),
            })

    if LOCATION_MODE_FIELD in incoming:
        raw_mode = incoming.get(LOCATION_MODE_FIELD)
        # `null` SESSİZCE `none`a düşmez: alan açıkça gönderildiyse yalnız üç
        # enum string kabul edilir.
        mode = raw_mode.strip().lower() if isinstance(raw_mode, str) else raw_mode
        if mode not in VALID_MODES:
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_LOCATION_FILTER_MODE",
                "message": (
                    f"Gecersiz lokasyon filtresi modu: {raw_mode!r}. "
                    f"Gecerli degerler: {', '.join(VALID_MODES)}."),
            })
    else:
        mode = str(existing.get(LOCATION_MODE_FIELD) or MODE_NONE).strip().lower()
        if mode not in VALID_MODES:
            mode = MODE_NONE
    sanitized[LOCATION_MODE_FIELD] = mode

    canonical: List[str] = []
    unsupported: List[str] = []
    for raw in sanitized.get("focus_cities", []) or []:
        city = canonical_city(raw)
        if city is None:
            unsupported.append(str(raw))
        elif city not in canonical:
            canonical.append(city)
    if unsupported:
        raise HTTPException(status_code=400, detail={
            "code": "UNSUPPORTED_FOCUS_CITY",
            "message": (
                "Yalnizca 81 resmi il adi secilebilir; ilce/mahalle "
                f"desteklenmez. Taninmayan: {', '.join(unsupported[:5])}"),
        })
    if len(canonical) > MAX_FOCUS_CITIES:
        raise HTTPException(status_code=400, detail={
            "code": "TOO_MANY_FOCUS_CITIES",
            "message": f"En fazla {MAX_FOCUS_CITIES} odak sehri secilebilir.",
        })
    if mode == MODE_FOCUS_ONLY and not canonical:
        raise HTTPException(status_code=400, detail={
            "code": "FOCUS_CITIES_REQUIRED",
            "message": (
                "'Yalniz sectigim sehirler kalsin' modu en az bir odak sehri "
                "gerektirir."),
        })
    sanitized["focus_cities"] = canonical

    # Muafiyetler: NORMALIZE tekrar temizliği (görünen metin korunur) +
    # terim başına ve toplam sınır.
    exempt: List[str] = []
    seen_exempt = set()
    for raw in sanitized.get("location_exempt_terms", []) or []:
        text = str(raw).strip()
        if len(text) > MAX_EXEMPT_TERM_LENGTH:
            raise HTTPException(status_code=400, detail={
                "code": "EXEMPT_TERM_TOO_LONG",
                "message": (
                    f"Muafiyet terimi en fazla {MAX_EXEMPT_TERM_LENGTH} "
                    f"karakter olabilir: {text[:40]}..."),
            })
        key = normalize_location_text(text)
        if not key or key in seen_exempt:
            continue
        seen_exempt.add(key)
        exempt.append(text)
    if len(exempt) > MAX_EXEMPT_TERMS:
        raise HTTPException(status_code=400, detail={
            "code": "TOO_MANY_EXEMPT_TERMS",
            "message": f"En fazla {MAX_EXEMPT_TERMS} muafiyet terimi girilebilir.",
        })
    sanitized["location_exempt_terms"] = exempt


def _sanitize_location_draft(
    incoming: Dict[str, Any],
    existing: Dict[str, Any],
) -> Dict[str, Any]:
    """Onizleme (Faz 4) draft'ini Faz 2 validator'i ile dogrular.

    `_apply_location_policy` TEK dogrulama/kanonikleştirme kaynagidir; bu
    fonksiyon onun kurallarini KOPYALAMAZ, yalniz `_sanitize_profile_data` /
    `_apply_profile_review_data`'daki AYNI iki-alan hazirligini tekrarlar
    (liste alanlari normalize edilir, alan gonderilmediyse `existing`teki
    kayitli deger korunur) ve ardindan tek gercek dogrulamayi cagirir.
    """
    incoming = incoming or {}
    existing = existing or {}
    sanitized: Dict[str, Any] = {}
    for field in ("focus_cities", "location_exempt_terms"):
        if field in incoming:
            sanitized[field] = _normalize_list_items(incoming.get(field))
        else:
            sanitized[field] = _normalize_list_items(existing.get(field, []))
    _apply_location_policy(sanitized, incoming, existing)
    return sanitized


def _normalize_list_items(value: Any) -> List[str]:
    """Normalize list-like input while preserving commas inside items."""
    raw_items: List[str] = []
    if value is None:
        raw_items = []
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        # Newline-only split. Commas are treated as part of the item text.
        raw_items = [line.strip() for line in value.splitlines() if line.strip()]
    else:
        raw_items = [str(value).strip()] if str(value).strip() else []

    seen = set()
    normalized: List[str] = []
    for item in raw_items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _parse_excluded_info(value: Any) -> List[str]:
    """Parse user-entered must-not text — core'a delege (plan v13 katmanlama)."""
    from app.core.policy.topic_policy import parse_excluded_info

    return parse_excluded_info(value)


def _merge_exclude_themes(user_excluded: Any, ai_excluded: Any) -> List[str]:
    """İLK profil üretiminde kullanıcı must-not + AI exclude_themes birleşimi.

    DİKKAT (plan v13): excluded_info DEĞİŞİKLİKLERİ bu fonksiyonla İŞLENMEZ —
    silinen terim birleşik listede sıkışır. Değişiklikler apply_competitor_review
    içindeki provenance'lı yeniden birleşimden geçer; burası yalnız sıfırdan
    profil kuran background üretim yolları içindir.
    """
    from app.core.policy.topic_policy import merge_normalized

    return merge_normalized(
        _parse_excluded_info(user_excluded),
        _normalize_list_items(ai_excluded),
    )


def _normalize_seed_keywords(keywords: List[str]) -> List[str]:
    """Normalize and de-duplicate user-approved seed keywords; preserve display text."""
    seen = set()
    result: List[str] = []
    for keyword in keywords or []:
        text = str(keyword).strip()
        key = normalize_keyword(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= 20:
            break
    return result


def _generate_anchor_texts(profile: Dict[str, Any]) -> List[str]:
    """Generate derived relevance anchors from profile fields."""
    from app.core.site_analyzer.anchor_builder import build_anchor_texts

    return build_anchor_texts(profile)


def _apply_profile_review_data(
    incoming: Dict[str, Any],
    existing: Dict[str, Any],
) -> Dict[str, Any]:
    """Profil-önce akışın 5-kart onayı: kart alanları düzenlenebilir.

    `_sanitize_profile_data`'dan farkı: target_audience ve brand_summary bu
    akışta kullanıcı tarafından DÜZENLENEBİLİR (kart 1 ve 4). company_name ve
    sector kilitli kalır. anchor_texts her zaman yeniden türetilir.
    """
    sanitized: Dict[str, Any] = dict(existing or {})
    incoming = incoming or {}

    for field in ("company_name", "sector"):
        if field in existing:
            sanitized[field] = existing[field]

    for field in ("target_audience", "brand_summary"):
        if field in incoming and incoming.get(field) is not None:
            sanitized[field] = str(incoming[field]).strip()

    for field in EDITABLE_LIST_FIELDS:
        if field in incoming:
            sanitized[field] = _normalize_list_items(incoming.get(field))
        else:
            sanitized[field] = _normalize_list_items(existing.get(field, []))

    _apply_location_policy(sanitized, incoming, existing)
    sanitized["anchor_texts"] = _generate_anchor_texts(sanitized)
    return sanitized


def _sanitize_profile_data(
    incoming: Dict[str, Any],
    existing: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Protect locked fields and normalize editable lists.

    ``anchor_texts`` is derived technical data for embedding relevance. Even if
    a client sends it, ignore the override and rebuild it from sanitized profile
    fields so stale/manual anchors cannot contradict user-visible profile data.
    """
    sanitized: Dict[str, Any] = dict(existing or {})
    incoming = incoming or {}

    # Locked fields always stay as-is from existing profile.
    for field in LOCKED_PROFILE_FIELDS:
        if field in existing:
            sanitized[field] = existing[field]

    # Editable list fields are normalized with newline/list semantics.
    for field in EDITABLE_LIST_FIELDS:
        if field in incoming:
            sanitized[field] = _normalize_list_items(incoming.get(field))
        else:
            sanitized[field] = _normalize_list_items(existing.get(field, []))

    _apply_location_policy(sanitized, incoming, existing)
    sanitized["anchor_texts"] = _generate_anchor_texts(sanitized)

    return sanitized


def _trigger_latest_run_relevance(
    db: Session,
    background_tasks: BackgroundTasks,
    workspace: BrandProfile,
) -> None:
    """Path A: otomatik relevance (embedding) tetikleyicisi — KAPALI (no-op).

    Eski motor (v2/v2_1) run'ları salt-okunurdur (embedding çağrısı yok);
    v3 motoru embedding relevance'ı hiç okumaz (kendi seo_rel/social_rel AI
    aşamaları vardır) ve v3 execute zaten `scored` -> kanal ataması zincirine
    relevance'sız geçer. Dolayısıyla hiçbir run için otomatik relevance
    seçilmez/dispatch edilmez; çağıranlar (profil onayı, keyword onayı,
    refresh) değişmeden bu fonksiyonu çağırmaya devam eder.
    """
    return


def _ensure_confirmable_profile(profile_data: Dict[str, Any]):
    if not isinstance(profile_data, dict) or not profile_data:
        raise HTTPException(
            status_code=400,
            detail="Şirket özellikleri henüz çıkarılmadı. Profil onaylanmadan önce analiz tamamlanmalı.",
        )

    anchor_texts = _normalize_list_items(profile_data.get("anchor_texts", []))
    if not anchor_texts:
        raise HTTPException(
            status_code=400,
            detail="Profil onaylanamaz: anchor text olusmadi. Once profil analizini tamamlayin veya profil alanlarini doldurun.",
        )


@router.get(
    "/runs/{run_id}/profile",
    response_model=BrandProfileResponse,
)
def get_profile(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get the brand profile for a scoring run."""
    run = verify_scoring_run(db, run_id, brand_profile_id)

    profile = (
        db.query(BrandProfile).filter(BrandProfile.id == run.brand_profile_id).first()
        if run.brand_profile_id else None
    )

    if not profile:
        raise HTTPException(status_code=404, detail="Bu run için profil bulunamadı")

    # Lazy takılı-profil kontrolü: startup janitörü tek seferlik olduğu için
    # restart sonrası taze görünüp sonra sonsuza dek running kalan satırları
    # bu OKUMA anında yakalar (bkz. stuck_janitor.py modül docstring'i).
    if fail_if_stuck(db, profile):
        db.refresh(profile)

    return BrandProfileResponse.model_validate(profile)


@router.post(
    "/runs/{run_id}/relevance/compute",
    response_model=RelevanceComputeResponse,
)
def compute_relevance(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """
    Compute keyword relevance scores using confirmed brand profile.
    Must be called after profile is confirmed and scoring is done.
    """
    if not settings.ENABLE_RELEVANCE_RERANK:
        raise HTTPException(
            status_code=400,
            detail="Relevance rerank devre dışı (ENABLE_RELEVANCE_RERANK=false)"
        )

    # Eski motor run'ı salt-okunur: embedding çağrısından ÖNCE tipli 409
    _gated_run = verify_scoring_run(db, run_id, brand_profile_id)
    require_non_legacy_run(_gated_run)
    # V3 motoru embedding relevance'ı okumaz: ücretli çağrı + DB yazımı YOK
    require_relevance_not_v3(_gated_run)

    # Yeni mimari: ScoringRun.brand_profile_id üzerinden; eski 1:1 fallback
    scoring_run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    profile = None

    if scoring_run and scoring_run.brand_profile_id:
        profile = db.query(BrandProfile).filter(
            BrandProfile.id == scoring_run.brand_profile_id,
            BrandProfile.status == "confirmed",
            BrandProfile.deleted_at.is_(None),
        ).first()

    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Onaylanmış profil bulunamadı. Önce profili onaylayın."
        )

    # Sürümlü ortak core servis (plan v13): snapshot işin başında alınır;
    # servis yazmadan hemen önce kilitli doğrular — uzun embedding işi
    # sırasında profil değiştiyse eski sonuç 'yeni' diye kaydedilmez.
    from app.core.relevance import RelevanceRefreshError, refresh_keyword_relevance
    from app.core.telemetry import UsageCollector

    requested_anchor_version = int(profile.anchor_version or 1)
    usage_collector = UsageCollector(
        scoring_run_id=run_id, brand_profile_id=scoring_run.brand_profile_id
    )
    try:
        result = refresh_keyword_relevance(
            db,
            scoring_run,
            profile,
            requested_anchor_version=requested_anchor_version,
            collector=usage_collector,
        )
    except RelevanceRefreshError as exc:
        if exc.version_changed:
            raise HTTPException(status_code=409, detail=str(exc))
        status_code = 404 if "keyword skoru bulunamadı" in str(exc) else 502
        if "anchor text bulunamadı" in str(exc):
            status_code = 400
        raise HTTPException(status_code=status_code, detail=str(exc))
    finally:
        usage_collector.finalize()

    return RelevanceComputeResponse(
        scoring_run_id=run_id,
        total_keywords=result.total_keywords,
        computed=result.computed,
        failed=result.failed,
        average_relevance=result.average_relevance,
    )


@router.get(
    "/runs/{run_id}/relevance",
    response_model=List[KeywordRelevanceResponse],
)
def get_relevance_scores(
    run_id: int,
    min_score: float = 0.0,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get keyword relevance scores for a run."""
    verify_scoring_run(db, run_id, brand_profile_id)

    results = (
        db.query(KeywordRelevance, Keyword)
        .join(Keyword, KeywordRelevance.keyword_id == Keyword.id)
        .filter(KeywordRelevance.scoring_run_id == run_id)
        .filter(KeywordRelevance.relevance_score >= min_score)
        .order_by(KeywordRelevance.relevance_score.desc())
        .all()
    )

    return [
        KeywordRelevanceResponse(
            keyword_id=kr.keyword_id,
            keyword=kw.keyword,
            relevance_score=float(kr.relevance_score),
            matched_anchor=kr.matched_anchor,
            method=kr.method,
        )
        for kr, kw in results
    ]


# ==================== WORKSPACE CRUD ENDPOINTS ====================

@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=201,
)
def create_workspace(
    request: WorkspaceCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """
    Yeni marka çalışması oluştur.
    Background task'ta site crawl + AI profile extraction başlatılır.
    """
    # Erken URL doğrulaması (plan v13): geçersiz/tekrarlı rakip URL'si crawl
    # ve AI süreci hiç başlamadan reddedilir.
    if request.competitor_urls:
        from app.core.policy import normalize_competitor_url

        seen_keys: set = set()
        for url in request.competitor_urls:
            key = normalize_competitor_url(url)
            if not key:
                raise HTTPException(
                    status_code=400, detail=f"Geçersiz rakip URL: {url}"
                )
            if key in seen_keys:
                raise HTTPException(
                    status_code=400,
                    detail="Aynı rakip URL birden fazla kez girilemez",
                )
            seen_keys.add(key)

    must_have_info = request.must_have_info or request.preliminary_info
    name = (request.name or "").strip()
    if not name:
        # Sihirbaz akışında tek zorunlu alan URL — adı domain'den türet
        from urllib.parse import urlparse
        parsed = urlparse(
            request.company_url if "//" in request.company_url else f"https://{request.company_url}"
        )
        name = (parsed.hostname or request.company_url).removeprefix("www.")

    # Yeni satır henüz kimseye görünmediği için kilit gerekmez: attempt token'ı
    # satırla AYNI insert'te yazılır (analiz kapalıysa dispatch yok → token yok).
    attempt_id = new_attempt_id() if settings.ENABLE_SITE_PROFILE_ANALYSIS else None
    workspace = BrandProfile(
        name=name,
        company_url=request.company_url,
        competitor_urls=request.competitor_urls,
        preliminary_info=must_have_info,
        excluded_info=request.excluded_info,
        default_geo_target_id=request.default_geo_target_id,
        default_language_id=request.default_language_id,
        status="pending",
        onboarding_flow=request.flow_version,
        analysis_attempt_id=attempt_id,
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    if settings.ENABLE_SITE_PROFILE_ANALYSIS:
        if request.flow_version == "profile_first":
            # Profil-önce sihirbaz: önce profil çıkar, keyword önerisi onaydan sonra
            background_tasks.add_task(
                _run_profile_analysis_first,
                workspace_id=workspace.id,
                company_url=request.company_url,
                competitor_urls=request.competitor_urls,
                attempt_id=attempt_id,
            )
        else:
            # Legacy akış: ilk aşama keyword önerisi
            background_tasks.add_task(
                _run_keyword_suggestion,
                workspace_id=workspace.id,
                company_url=request.company_url,
                competitor_urls=request.competitor_urls,
                must_have_info=must_have_info,
                excluded_info=request.excluded_info,
                attempt_id=attempt_id,
            )

    return WorkspaceResponse.model_validate(workspace)


@router.get(
    "/workspaces",
    response_model=List[WorkspaceListResponse],
)
def list_workspaces(
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    """
    Marka çalışmaları listesi.
    Arşivlenenler default hariç.
    """
    query = db.query(BrandProfile)
    if not include_archived:
        query = query.filter(BrandProfile.deleted_at.is_(None))
    workspaces = query.order_by(BrandProfile.created_at.desc()).all()

    result = []
    for ws in workspaces:
        run_count = db.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id
        ).count()
        result.append(WorkspaceListResponse(
            id=ws.id,
            name=ws.name,
            company_url=ws.company_url,
            status=ws.status,
            onboarding_flow=ws.onboarding_flow or "legacy",
            profile_approved_at=ws.profile_approved_at,
            validation_data=ws.validation_data,
            competitor_urls=ws.competitor_urls,
            competitor_url_decisions=ws.competitor_url_decisions,
            profile_data=ws.profile_data,
            suggested_keywords=ws.suggested_keywords,
            excluded_info=ws.excluded_info,
            default_geo_target_id=ws.default_geo_target_id,
            default_language_id=ws.default_language_id,
            deleted_at=ws.deleted_at,
            created_at=ws.created_at,
            run_count=run_count,
        ))
    return result


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
)
def get_workspace(workspace_id: int, db: Session = Depends(get_db)):
    """Marka çalışması detay."""
    workspace = verify_workspace(db, workspace_id)
    # Lazy takılı-profil kontrolü: frontend loading statülerinde bu endpoint'i
    # poll eder (BrandProfile.tsx) — restart sonrası kalıcı takılan running/
    # pending satırları burada failed'a çevirip kullanıcıya "yeniden dene"
    # yolunu açar (bkz. stuck_janitor.py modül docstring'i).
    if fail_if_stuck(db, workspace):
        db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.get(
    "/workspaces/{workspace_id}/channel-seeds",
    response_model=List[ChannelSeedResponse],
)
def get_channel_seeds(workspace_id: int, db: Session = Depends(get_db)):
    """Workspace'in kanal seed'leri (yalniz kendi workspace'i)."""
    workspace = verify_workspace(db, workspace_id)
    return [ChannelSeedResponse.model_validate(seed)
            for seed in list_channel_seeds(db, workspace.id)]


@router.put(
    "/workspaces/{workspace_id}/channel-seeds",
    response_model=List[ChannelSeedResponse],
)
def put_channel_seeds(
    workspace_id: int,
    seeds: List[ChannelSeedInput],
    db: Session = Depends(get_db),
):
    """Kanal seed'lerini TAM OLARAK verilen kumeye esitler.

    Onboarding onayindan BAGIMSIZ olarak da guncellenebilsin diye ayri
    endpoint; kısmi guncelleme yoktur, form bir butun olarak gonderilir.
    """
    workspace = verify_workspace(db, workspace_id)
    try:
        saved = replace_channel_seeds(
            db, workspace.id, [seed.model_dump() for seed in seeds])
    except ChannelSeedError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return [ChannelSeedResponse.model_validate(seed) for seed in saved]


@router.put(
    "/workspaces/{workspace_id}/keywords/approve",
    response_model=WorkspaceResponse,
)
def approve_workspace_keywords(
    workspace_id: int,
    request: WorkspaceKeywordApproveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Approve/edit the suggested seed keywords and start profile generation."""
    verify_workspace(db, workspace_id)
    # Row lock + TAZE okuma (2.2): durum kontrolü ile attempt token'ı yazımı
    # aynı kilit altında; eşzamanlı iki onay ikincisinde 400 alır.
    workspace = lock_workspace_row(db, workspace_id)

    can_approve = workspace.status == "keywords_review" or (
        workspace.status == "failed" and bool(workspace.suggested_keywords)
    )
    if not can_approve:
        detail_status = workspace.status
        db.rollback()  # satır kilidini hemen bırak
        raise HTTPException(
            status_code=400,
            detail=(
                f"Keyword onayi icin durum '{detail_status}' uygun degil "
                "(keywords_review veya failed+keyword gerekli)"
            ),
        )

    keywords = _normalize_seed_keywords(request.keywords)
    if not keywords:
        db.rollback()
        raise HTTPException(status_code=400, detail="En az 1 keyword gerekli")

    # Kanal tercihleri OPSIYONEL: gonderilmezse bugunku davranis birebir
    # korunur (plan9 §10). Gonderilirse ayri yapisal tabloya yazilir.
    if request.channel_seeds is not None:
        try:
            replace_channel_seeds(
                db, workspace.id,
                [seed.model_dump() for seed in request.channel_seeds],
            )
        except ChannelSeedError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if workspace.onboarding_flow == "profile_first":
        # Profil-önce akış: profil zaten onaylı; seed kaydet ve tamamla.
        _ensure_confirmable_profile(workspace.profile_data)
        workspace.suggested_keywords = keywords
        workspace.status = "confirmed"
        workspace.error_message = None
        db.commit()
        db.refresh(workspace)

        _trigger_latest_run_relevance(db, background_tasks, workspace)
        return WorkspaceResponse.model_validate(workspace)

    workspace.suggested_keywords = keywords
    workspace.status = "running"
    workspace.error_message = None
    attempt_id = start_attempt(workspace)
    db.commit()
    db.refresh(workspace)

    background_tasks.add_task(
        _run_profile_from_keywords,
        workspace_id=workspace.id,
        keywords=keywords,
        attempt_id=attempt_id,
    )

    return WorkspaceResponse.model_validate(workspace)


@router.put(
    "/workspaces/{workspace_id}/profile/approve",
    response_model=WorkspaceResponse,
)
def approve_workspace_profile(
    workspace_id: int,
    request: WorkspaceProfileApproveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Profil-önce akış: 5-kart profil onayı (profile_review), rakip incelemeden
    keyword üretimine geçiş (competitor_review) veya adım-4 inline profil
    düzeltmesi / keyword yeniden üretimi (keywords_review).

    Plan v13: row lock EN BAŞTA (koşulsuz); rakip kararları + excluded_info
    işleme apply_competitor_review çekirdeğine delege; profil yazımı
    apply_profile_data_update kapısından (anchor sürümlemesi).
    """
    verify_workspace(db, workspace_id)
    # Row lock (plan v13): status/profile_data dahil her okuma kilitli satırdan —
    # eşzamanlı politika mutasyonu / background analiz yarışları kapanır.
    # populate_existing: verify_workspace satırı identity map'e zaten yükledi;
    # bayat status ile karar verilmesin (2.2: token yazımı bu kilitle yapılır).
    workspace = lock_workspace_row(db, workspace_id)

    if workspace.onboarding_flow != "profile_first":
        raise HTTPException(
            status_code=400,
            detail="Bu çalışma profil-önce akışta değil (onboarding_flow=legacy)",
        )

    initial_status = workspace.status
    allowed = workspace.status in (
        "profile_review", "competitor_review", "keywords_review"
    ) or (
        workspace.status == "failed" and bool(workspace.profile_data)
    )
    if not allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Profil onayı için durum '{workspace.status}' uygun değil "
                "(profile_review, competitor_review veya keywords_review gerekli)"
            ),
        )

    active_work = find_active_workspace_work(db, workspace_id)
    if active_work:
        raise HTTPException(
            status_code=409,
            detail=f"Şu an {active_work} — bitince tekrar deneyin.",
        )

    existing_data = workspace.profile_data if isinstance(workspace.profile_data, dict) else {}
    incoming_data = request.profile_data if isinstance(request.profile_data, dict) else {}
    profile = _apply_profile_review_data(incoming_data, existing_data)
    # Kart düzenlemeleri TEK yazma kapısından (anchor diff + sürümleme)
    apply_profile_data_update(db, workspace, profile)

    if request.must_have_info is not None:
        workspace.preliminary_info = request.must_have_info.strip() or None
    if request.default_geo_target_id is not None:
        workspace.default_geo_target_id = request.default_geo_target_id
    if request.default_language_id is not None:
        workspace.default_language_id = request.default_language_id

    # Rakip URL/karar + excluded_info: paylaşılan çekirdek (provenance'lı
    # exclude_themes yeniden birleşimi + topic senkronu + sürümleme + stale)
    try:
        apply_competitor_review(
            db,
            workspace,
            competitor_urls=request.competitor_urls,
            decisions=request.competitor_decisions,
            excluded_info=request.excluded_info,
        )
    except PolicyValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    if initial_status == "profile_review":
        workspace.profile_approved_at = datetime.utcnow()

    if request.rerun_keywords:
        # Eski öneriler ekranda/response'ta kalmasın; yeni liste üretilecek
        workspace.suggested_keywords = None
        workspace.status = "running"
        workspace.error_message = None
        attempt_id = start_attempt(workspace)
        db.commit()
        db.refresh(workspace)
        background_tasks.add_task(
            _run_keyword_suggestion_from_profile,
            workspace_id=workspace.id,
            attempt_id=attempt_id,
        )
    else:
        # Profil kartlari onaylandi; rakip kesfi/karari keyword uretiminden
        # once tamamlanabilsin. Kalici status refresh sonrasinda da adimi korur.
        if initial_status == "profile_review":
            workspace.status = "competitor_review"
            workspace.error_message = None
        db.commit()
        db.refresh(workspace)

    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/anchors/preview",
    response_model=AnchorPreviewResponse,
)
def preview_workspace_anchors(
    workspace_id: int,
    request: AnchorPreviewRequest,
    db: Session = Depends(get_db),
):
    """
    Marka odakları (anchor) gruplu önizlemesi.
    Opsiyonel profile_data override sanitize edilir; payload'daki anchor_texts
    YOK SAYILIR (anchor'lar her zaman profil alanlarından türetilir).
    """
    from app.core.site_analyzer.anchor_builder import build_anchor_groups

    workspace = verify_workspace(db, workspace_id)
    existing_data = workspace.profile_data if isinstance(workspace.profile_data, dict) else {}

    if isinstance(request.profile_data, dict) and request.profile_data:
        profile = _apply_profile_review_data(request.profile_data, existing_data)
    else:
        profile = existing_data

    groups = build_anchor_groups(profile)
    return AnchorPreviewResponse(
        groups=[AnchorGroupResponse(**group) for group in groups]
    )


@router.put(
    "/workspaces/{workspace_id}/confirm",
    response_model=WorkspaceResponse,
)
def confirm_workspace(
    workspace_id: int,
    request: ProfileConfirmRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Marka çalışması profilini onayla.
    Path A: Workspace'in en son scored run'ı için relevance tetiklenir.
    """
    verify_workspace(db, workspace_id)
    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id)
        .with_for_update()
        .first()
    )

    if workspace.status not in ("draft", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail=f"Profil durumu '{workspace.status}', onaylanamaz (draft veya confirmed olmalı)"
        )

    active_work = find_active_workspace_work(db, workspace_id)
    if active_work:
        raise HTTPException(
            status_code=409,
            detail=f"Şu an {active_work} — bitince tekrar deneyin.",
        )

    existing_data = workspace.profile_data if isinstance(workspace.profile_data, dict) else {}
    incoming_data = request.profile_data if isinstance(request.profile_data, dict) else {}
    sanitized = _sanitize_profile_data(incoming_data, existing_data)
    _ensure_confirmable_profile(sanitized)
    # TEK yazma kapısı: anchor diff → anchor_version + çıktı stale (plan v13)
    apply_profile_data_update(db, workspace, sanitized)

    workspace.status = "confirmed"
    db.commit()
    db.refresh(workspace)

    # Path A: Workspace'in en son scored run'ı için relevance tetikle
    _trigger_latest_run_relevance(db, background_tasks, workspace)

    return WorkspaceResponse.model_validate(workspace)


# ═══════════════════════════════════════════════════════════
# Policy yönetimi (plan_kalite_maliyet.md A+B + plan v13)
# JSON kolonlarında in-place mutation YASAK — her zaman yeni liste/dict atanır.
# Mutasyon sözleşmesi: row lock → aktif iş kontrolü (409) → yazım →
# etkin politika değiştiyse policy_version + çıktı stale (tek transaction).
# ═══════════════════════════════════════════════════════════

from pydantic import BaseModel as _PolicyBase
from pydantic import Field as _PolicyField


class PolicyTermDecision(_PolicyBase):
    term: str
    status: str  # approved | rejected
    kind: str = "competitor"  # competitor | topic_term | topic_alias


class CompetitorPolicyUpdate(_PolicyBase):
    ads: str = "block"
    seo: str = "block"
    social: str = "block"


from app.schemas.brand_profile import CompetitorDecision


class PolicyReviewRequest(_PolicyBase):
    """Kalıcı rakip/konu incelemesi (onay sonrası + legacy yüzey)."""
    competitor_urls: Optional[List[str]] = _PolicyField(None, max_length=3)
    competitor_decisions: Optional[List[CompetitorDecision]] = _PolicyField(
        None, max_length=3
    )
    excluded_info: Optional[str] = None


class CompetitorPreviewRequest(_PolicyBase):
    urls: List[str] = _PolicyField(..., min_length=1, max_length=3)


class CompetitorDiscoveryRequest(_PolicyBase):
    force_refresh: bool = False


class CompetitorDiscoveryDecision(_PolicyBase):
    discovery_id: str
    decision: str
    term: str


class CompetitorDiscoveryDecisionsRequest(_PolicyBase):
    decisions: List[CompetitorDiscoveryDecision] = _PolicyField(
        ..., min_length=1, max_length=10
    )


# /policy/review izinli durumları: background analiz (pending/running)
# validation/profile alanlarını yazarken review 409 almalı (Codex v9-orta).
POLICY_REVIEW_ALLOWED_STATUSES = (
    "profile_review", "competitor_review", "keywords_review", "draft",
    "confirmed", "failed",
)


def _lock_workspace_for_policy_mutation(db: Session, workspace_id: int) -> BrandProfile:
    """Ortak mutasyon girişi: row lock + aktif iş guard'ı (409).

    pending/running statüleri de reddedilir: keyword üretimi TaskResult'suz
    BackgroundTask olduğundan find_active_workspace_work onu GÖREMEZ; üretim
    sürerken onaylanan rakip o koşunun prompt'una ve deterministik son
    filtresine giremezdi ("onaylı rakip keyword'e giremez" garantisi
    deliniyordu — Codex bulgusu, 22.07).
    """
    verify_workspace(db, workspace_id)
    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id)
        .with_for_update()
        .first()
    )
    if workspace.status in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ANALYSIS_RUNNING",
                "message": (
                    "Analiz/keyword üretimi sürüyor — politika değişikliği "
                    "için bitmesini bekleyin."
                ),
            },
        )
    active_work = find_active_workspace_work(db, workspace_id)
    if active_work:
        raise HTTPException(
            status_code=409,
            detail=f"Şu an {active_work} — bitince tekrar deneyin.",
        )
    return workspace


def _bump_policy_version_if_changed(db: Session, workspace, before: tuple) -> bool:
    """Etkin politika değiştiyse sürümü artır + workspace çıktılarını stale et."""
    if policy_effect_snapshot(workspace) == before:
        return False
    workspace.policy_version = int(workspace.policy_version or 1) + 1
    invalidate_workspace_outputs(db, workspace.id)
    return True


def _policy_payload(db: Session, workspace) -> dict:
    """GET/mutasyon yanıtları için provenance'lı politika görünümü."""
    from app.core.policy.competitor_policy import competitor_policy_for, is_legacy_term
    from app.core.policy.competitor_discovery import (
        canonical_profile_fingerprint,
        fresh_channel_run_count,
    )

    terms = []
    discovery_fields = (
        "discovery_id", "candidate_domain", "candidate_url", "rationale",
        "confidence", "verification_status", "evidence_urls", "discovered_at",
        "decided_at", "profile_fingerprint", "discovery_matches",
        "discovery_rejections",
    )
    for entry in workspace.competitor_terms or []:
        payload = {
            "term": entry.get("term", ""),
            "status": entry.get("status", ""),
            "source": entry.get("source", ""),
            "manual_approved": bool(entry.get("manual_approved")),
            "source_urls": list(entry.get("source_urls") or []),
            "legacy": is_legacy_term(entry),
        }
        for field in discovery_fields:
            if field in entry:
                payload[field] = entry.get(field)
        terms.append(payload)
    current_fingerprint = canonical_profile_fingerprint(workspace)
    return {
        "competitor_terms": terms,
        "competitor_policy": competitor_policy_for(workspace),
        "topic_policy": workspace.topic_policy or {
            "excluded_terms": [], "excluded_aliases": []
        },
        "competitor_url_decisions": workspace.competitor_url_decisions or {},
        "policy_version": workspace.policy_version or 1,
        "anchor_version": workspace.anchor_version or 1,
        "discovery_impact": {
            "fresh_channel_run_count": fresh_channel_run_count(db, workspace.id),
            "profile_fingerprint": current_fingerprint,
            "has_stale_suggestions": any(
                entry.get("source") == "ai_discovery"
                and entry.get("status") == "suggested"
                and entry.get("profile_fingerprint") != current_fingerprint
                for entry in (workspace.competitor_terms or [])
            ),
        },
    }


@router.get("/workspaces/{workspace_id}/policy")
def get_workspace_policy(workspace_id: int, db: Session = Depends(get_db)):
    """Rakip + konu politikası — provenance alanlarıyla (plan v13).

    Frontend chip mesajlarını HTTP işlem sonucundan değil BU veriden türetir
    (manual_approved / source_urls / legacy)."""
    workspace = verify_workspace(db, workspace_id)
    return _policy_payload(db, workspace)


@router.post(
    "/workspaces/{workspace_id}/policy/location-preview",
    response_model=LocationFilterPreviewResponse,
)
def preview_location_filter(
    workspace_id: int,
    request: LocationFilterPreviewRequest,
    db: Session = Depends(get_db),
):
    """Lokasyon filtresi icin salt-okunur, kaydetmeyen onizleme (Faz 4).

    plan_v3_lokasyon_filtresi.md §5.7: draft `location_filter_mode` /
    `focus_cities` / `location_exempt_terms` degerlerini Faz 2 validator'i
    (`_apply_location_policy`) ile dogrular, ardindan motorun GERCEKTEN
    dondurecegi AYNI satir kumesini (`build_universe_rows` — A13/A15
    uygunlugu dahil, `freeze_universe_snapshot` ile PAYLASILAN tek yardimci)
    merkezi `location_policy.evaluate_keyword` ile degerlendirir.

    Hicbir profil, keyword, run veya havuz kaydi YAZMAZ; AI cagirmaz.
    """
    from app.core.engine.context import (
        EngineInputError, build_universe_rows, universe_fingerprint as
        compute_universe_fingerprint,
    )
    from app.core.policy import location_policy
    from app.database.models import ScoringRun as _ScoringRunModel

    workspace = verify_workspace(db, workspace_id)
    existing_profile_data = workspace.profile_data or {}

    incoming = request.model_dump(
        include={"location_filter_mode", "focus_cities", "location_exempt_terms"},
        exclude_unset=True,
    )
    sanitized = _sanitize_location_draft(incoming, existing_profile_data)
    mode = sanitized[LOCATION_MODE_FIELD]
    focus_cities = sanitized["focus_cities"]
    exempt_terms = sanitized["location_exempt_terms"]

    draft_profile_data = {
        LOCATION_MODE_FIELD: mode,
        "focus_cities": focus_cities,
        "location_exempt_terms": exempt_terms,
    }
    policy = location_policy.policy_snapshot(draft_profile_data)

    # Persist edilmeyen ScoringRun: yalniz `select_run_keywords`'in okudugu
    # alanlari tasir, session'a HICBIR ZAMAN eklenmez/flush edilmez.
    transient_run = _ScoringRunModel(
        brand_profile_id=workspace_id,
        keyword_selection_mode=request.keyword_selection_mode,
        keyword_limit=request.keyword_limit,
        selected_keyword_ids=request.selected_keyword_ids,
        keyword_source_filter=request.keyword_source_filter,
    )
    try:
        universe = build_universe_rows(db, transient_run)
    except EngineInputError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_KEYWORD_SELECTION",
            "message": str(exc),
        })

    kept = 0
    excluded_by_reason: Dict[str, int] = {}
    excluded_by_city_reason: Dict[Any, int] = {}
    sample_excluded: List[LocationPreviewSample] = []
    sample_exempted: List[LocationPreviewExemptSample] = []

    for row in universe.rows:
        decision = location_policy.evaluate_keyword(
            row.keyword_text, mode=mode, focus_cities=focus_cities,
            exempt_terms=exempt_terms)
        if decision.is_kept:
            kept += 1
            if decision.matched_exempt_term and len(sample_exempted) < LOCATION_PREVIEW_SAMPLE_LIMIT:
                sample_exempted.append(LocationPreviewExemptSample(
                    keyword=row.keyword_text,
                    matched_exempt_term=decision.matched_exempt_term,
                ))
            continue
        reason_code = (decision.exclude_reason or "").split(":", 1)[0]
        excluded_by_reason[reason_code] = excluded_by_reason.get(reason_code, 0) + 1
        city = decision.matched_city or ""
        key = (reason_code, city)
        excluded_by_city_reason[key] = excluded_by_city_reason.get(key, 0) + 1
        if len(sample_excluded) < LOCATION_PREVIEW_SAMPLE_LIMIT:
            sample_excluded.append(LocationPreviewSample(
                keyword=row.keyword_text, matched_city=decision.matched_city,
            ))

    excluded_count = sum(excluded_by_reason.values())
    excluded_by_city = [
        LocationPreviewCityCount(city=city, reason=reason, count=count)
        for (reason, city), count in sorted(excluded_by_city_reason.items())
    ]

    # Faz 6a — TOKEN GÜVEN KURALI (plan §5.7): `incoming` üç draft lokasyon
    # alanından HİÇBİRİNİ taşımıyorsa yanıt tamamen workspace'in KAYITLI
    # `profile_data`'sından üretilmiştir. Aksi halde (profil düzenleme
    # ekranı gibi en az bir alanı açıkça gönderdiyse) `False` döner — bu
    # token değerler tesadüfen kayıtlıyla aynı olsa bile run yetkilendirmede
    # kullanılamaz (bkz. `create_scoring_run`/`execute_scoring`).
    is_saved_policy = not incoming

    return LocationFilterPreviewResponse(
        has_keywords=bool(universe.rows),
        mode=mode,
        evaluated_count=len(universe.rows),
        kept_count=kept,
        excluded_count=excluded_count,
        excluded_by_reason=excluded_by_reason,
        excluded_by_city=excluded_by_city,
        sample_excluded=sample_excluded,
        sample_exempted=sample_exempted,
        city_lexicon_version=policy["city_lexicon_version"],
        city_lexicon_sha256=policy["city_lexicon_sha256"],
        universe_fingerprint=compute_universe_fingerprint(universe),
        location_policy_fingerprint=policy["enforcement_fingerprint"],
        is_saved_policy=is_saved_policy,
    )


@router.get(
    "/runs/{run_id}/location-audit",
    response_model=LocationAuditResponse,
)
def get_location_audit(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    only_excluded: bool = Query(
        False, description="Yalnız lokasyon nedeniyle elenen satırları döndür"),
    db: Session = Depends(get_db),
):
    """Lokasyon filtresi denetimi (Faz 6a) — "bu keyword neden havuza
    girmedi?" sorusunu TAMAMLANMIŞ bir run için salt-okunur yanıtlar.

    plan_v3_lokasyon_filtresi.md §5.6: yalnız run'ın DONDURULMUŞ evreni
    (`load_universe`) ve MÜHÜRLÜ manifest lokasyon politikası
    (`manifest_location_policy`) okunur — canlı profil veya draft önizleme
    değeri HİÇBİR ZAMAN okunmaz; böylece audit sonucu profil sonradan
    değişse bile o run'ın GERÇEKTEN neyle koştuğunu göstermeye devam eder.
    AI çağırmaz. Sayfalanır — sınırsız liste dönmez.
    """
    from app.core.engine.context import EngineInputError, load_universe
    from app.core.engine.persistence import (
        StageContextMismatch, manifest_location_policy,
    )
    from app.core.policy import location_policy

    run = verify_scoring_run(db, run_id, brand_profile_id)
    # Terminal durum sözleşmesi: v3 pipeline'ının gerçek terminal durumu
    # `channel_assigned`dır (orchestrator hiçbir zaman `completed` yazmaz).
    # `completed` yalnız eski/tarihsel alan için geriye uyumlu takma addır —
    # aynı sözleşme `app/api/v1/channels.py`, `app/core/trial_authorization.py`
    # (`SUCCESS_STATUSES`) ve `app/core/dashboard/next_action.py`de kullanılır.
    if run.status not in ("channel_assigned", "completed"):
        raise HTTPException(status_code=409, detail={
            "code": "RUN_NOT_COMPLETED",
            "message": (
                f"Run {run_id} durumu 'channel_assigned' değil "
                f"('{run.status}') — lokasyon denetimi yalnız tamamlanmış "
                "run için kullanılabilir."
            ),
        })

    try:
        universe = load_universe(db, run_id)
    except EngineInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        policy = manifest_location_policy(run)
    except StageContextMismatch as exc:
        raise HTTPException(status_code=409, detail={
            "code": "LOCATION_AUDIT_MANIFEST_INVALID",
            "message": str(exc),
        })

    if policy is None:
        # Sema/lokasyon sözleşmesinden ÖNCE mühürlenmiş run: bu run'da
        # lokasyon filtresi hiç UYGULANMADI — hepsi tutuldu sayılır
        # (`manifest_location_policy` dokümantasyonu: None "filtre yok"
        # ANLAMINA gelmez ama bu run'ı yeni sözleşmeyle YENİDEN
        # değerlendiremeyiz; yalnız gerçekten koşulmuş davranışı raporlarız).
        mode = location_policy.MODE_NONE
        focus_cities: List[str] = []
        exempt_terms: List[str] = []
        lexicon_version = location_policy.CITY_LEXICON_VERSION
    else:
        mode = policy["mode"]
        focus_cities = policy["focus_cities"]
        exempt_terms = policy["exempt_terms"]
        lexicon_version = policy["city_lexicon_version"]

    all_rows: List[LocationAuditRow] = []
    kept_count = 0
    excluded_count = 0
    for row in sorted(universe.rows, key=lambda r: r.keyword_id):
        decision = location_policy.evaluate_keyword(
            row.keyword_text, mode=mode, focus_cities=focus_cities,
            exempt_terms=exempt_terms)
        if decision.is_kept:
            kept_count += 1
        else:
            excluded_count += 1
        if only_excluded and decision.is_kept:
            continue
        reason_code = (decision.exclude_reason or "").split(":", 1)[0] or None
        all_rows.append(LocationAuditRow(
            keyword_id=row.keyword_id,
            keyword=row.keyword_text,
            is_kept=decision.is_kept,
            reason_code=reason_code,
            matched_city=decision.matched_city,
            matched_exempt_term=decision.matched_exempt_term,
        ))

    return LocationAuditResponse(
        scoring_run_id=run_id,
        mode=mode,
        city_lexicon_version=lexicon_version,
        total_rows=len(all_rows),
        kept_count=kept_count,
        excluded_count=excluded_count,
        limit=limit,
        offset=offset,
        rows=all_rows[offset:offset + limit],
    )


@router.put("/workspaces/{workspace_id}/policy/review")
def review_workspace_policy(
    workspace_id: int,
    request: PolicyReviewRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Kalıcı rakip/konu incelemesi (plan v13 — onay sonrası + legacy yüzey).

    Onboarding approve ile AYNI çekirdeği (apply_competitor_review) kullanır;
    onboarding_flow kısıtı yoktur. Kararlar TAM REPLACEMENT'tır.
    """
    verify_workspace(db, workspace_id)
    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id)
        .with_for_update()
        .first()
    )
    if workspace.status not in POLICY_REVIEW_ALLOWED_STATUSES or (
        workspace.status == "failed" and not workspace.profile_data
    ):
        raise HTTPException(
            status_code=409 if workspace.status in ("pending", "running") else 400,
            detail=(
                f"Politika incelemesi için durum '{workspace.status}' uygun değil"
                + (" (analiz sürüyor, bitince deneyin)"
                   if workspace.status in ("pending", "running") else "")
            ),
        )
    active_work = find_active_workspace_work(db, workspace_id)
    if active_work:
        raise HTTPException(
            status_code=409,
            detail=f"Şu an {active_work} — bitince tekrar deneyin.",
        )

    try:
        summary = apply_competitor_review(
            db,
            workspace,
            competitor_urls=request.competitor_urls,
            decisions=request.competitor_decisions,
            excluded_info=request.excluded_info,
        )
    except PolicyValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    db.commit()
    db.refresh(workspace)

    # excluded_info/anchor değiştiyse relevance akışı mevcut tetikleyiciden
    # (yalnız 'scored' run yakalar; stale run'lar yeniden atamada tazelenir)
    if summary.get("anchor_changed"):
        _trigger_latest_run_relevance(db, background_tasks, workspace)

    return {**_policy_payload(db, workspace), "review": summary}


@router.post("/workspaces/{workspace_id}/policy/competitor-suggestions")
def generate_competitor_suggestions(workspace_id: int, db: Session = Depends(get_db)):
    """competitor_urls'ten terim önerileri üretir (status=suggested).

    Reddedilmiş öneri bir daha üretilmez; onaysız öneri filtreye girmez.
    """
    from app.core.policy.competitor_policy import suggest_competitor_terms

    verify_workspace(db, workspace_id)
    # Codex A+B-6: read-modify-write kilitli — eszamanli approve kaybolmaz
    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id)
        .with_for_update()
        .first()
    )
    existing = workspace.competitor_terms or []
    new_suggestions = suggest_competitor_terms(workspace.competitor_urls, existing)
    if new_suggestions:
        workspace.competitor_terms = [*existing, *new_suggestions]  # yeni liste
        db.commit()
        db.refresh(workspace)
    return {
        "suggested": new_suggestions,
        "competitor_terms": workspace.competitor_terms or [],
    }


@router.post("/workspaces/{workspace_id}/policy/terms")
def upsert_policy_term(
    workspace_id: int,
    decision: PolicyTermDecision,
    db: Session = Depends(get_db),
):
    """Terim ekle veya öneriyi karara bağla (approve/reject).

    kind=competitor → set_manual_approval (plan v13 provenance: manuel onay/
    kaldırma source_urls'e DOKUNMAZ; status türetilir — URL destekli terim
    manuel kaldırılınca approved kalabilir, UI bunu provenance'tan açıklar).
    topic_term/topic_alias → basit topic upsert.
    """
    from app.core.policy.competitor_discovery import apply_competitor_term_decisions
    from app.core.policy.topic_policy import upsert_topic_status

    if decision.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="status approved|rejected olmalı")
    if decision.kind not in ("competitor", "topic_term", "topic_alias"):
        raise HTTPException(status_code=400, detail="kind geçersiz")
    term = (decision.term or "").strip()
    if not term:
        raise HTTPException(status_code=400, detail="term boş olamaz")

    workspace = _lock_workspace_for_policy_mutation(db, workspace_id)
    before = policy_effect_snapshot(workspace)

    if decision.kind == "competitor":
        try:
            apply_competitor_term_decisions(db, workspace, [{
                "term": term,
                "decision": decision.status,
            }])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        policy = dict(workspace.topic_policy or {})
        key = "excluded_terms" if decision.kind == "topic_term" else "excluded_aliases"
        policy[key] = upsert_topic_status(policy.get(key), term, decision.status)
        # diğer anahtar korunur
        policy.setdefault(
            "excluded_aliases" if key == "excluded_terms" else "excluded_terms", []
        )
        workspace.topic_policy = policy  # yeni dict

    if decision.kind != "competitor":
        _bump_policy_version_if_changed(db, workspace, before)
    db.commit()
    db.refresh(workspace)
    return _policy_payload(db, workspace)


@router.put("/workspaces/{workspace_id}/policy/competitor-policy")
def set_competitor_policy(
    workspace_id: int,
    update: CompetitorPolicyUpdate,
    db: Session = Depends(get_db),
):
    """Kanal bazlı rakip politikası (block|allow)."""
    for ch_value in (update.ads, update.seo, update.social):
        if ch_value not in ("block", "allow"):
            raise HTTPException(status_code=400, detail="Değerler block|allow olmalı")
    workspace = _lock_workspace_for_policy_mutation(db, workspace_id)
    before = policy_effect_snapshot(workspace)
    workspace.competitor_policy = {
        "ads": update.ads, "seo": update.seo, "social": update.social
    }  # yeni dict
    _bump_policy_version_if_changed(db, workspace, before)
    db.commit()
    return workspace.competitor_policy





PREVIEW_TASK_TYPE = "policy_preview"
PREVIEW_STALE_MINUTES = 10


@router.post(
    "/workspaces/{workspace_id}/policy/competitors/preview",
    status_code=202,
)
def start_competitor_preview(
    workspace_id: int,
    request: CompetitorPreviewRequest,
    db: Session = Depends(get_db),
):
    """Rakip URL'leri için marka adı tespiti başlat (plan v13 — Celery, async).

    202 + task_id döner; sonuç GET .../preview/{task_id} ile poll edilir.
    Fail-open bir kolaylıktır: UI beklerken domain-fallback gösterir. Sonuç
    persist edilmez; kalıcı kayıt yalnız review submit'inde yazılır.
    Workspace başına TEK aktif preview (brand_profile_id kolonu ile sorgulanır).
    """
    from datetime import timedelta, timezone as _tz
    import uuid as _uuid

    from app.core.policy import normalize_competitor_url

    for url in request.urls:
        if not normalize_competitor_url(url):
            raise HTTPException(status_code=400, detail=f"Geçersiz rakip URL: {url}")

    verify_workspace(db, workspace_id)
    # Row lock: eşzamanlı iki istek tek task üretmeli
    db.query(BrandProfile).filter(
        BrandProfile.id == workspace_id
    ).with_for_update().first()

    # Stale preview reconciliation: worker ölmüş/takılmışsa 10dk sonra failed
    stale_cutoff = datetime.now(_tz.utc) - timedelta(minutes=PREVIEW_STALE_MINUTES)
    stale_tasks = (
        db.query(TaskResult)
        .filter(
            TaskResult.brand_profile_id == workspace_id,
            TaskResult.task_type == PREVIEW_TASK_TYPE,
            TaskResult.status.in_(("pending", "running")),
            TaskResult.created_at < stale_cutoff,
        )
        .all()
    )
    for stale in stale_tasks:
        stale.status = "failed"
        stale.error_message = "Preview zaman aşımına uğradı"

    active = (
        db.query(TaskResult)
        .filter(
            TaskResult.brand_profile_id == workspace_id,
            TaskResult.task_type == PREVIEW_TASK_TYPE,
            TaskResult.status.in_(("pending", "running")),
        )
        .first()
    )
    if active:
        db.commit()
        return {"task_id": active.task_id, "status": active.status}

    task_id = str(_uuid.uuid4())
    db.add(TaskResult(
        task_id=task_id,
        task_type=PREVIEW_TASK_TYPE,
        brand_profile_id=workspace_id,
        status="pending",
        progress=0,
        result_data={"urls": list(request.urls)},
    ))
    db.commit()

    try:
        from app.tasks.policy_preview_tasks import run_competitor_preview_task

        run_competitor_preview_task.apply_async(
            args=[workspace_id, list(request.urls)], task_id=task_id
        )
    except Exception as exc:
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        if task:
            task.status = "failed"
            task.error_message = str(exc)
            db.commit()
        raise HTTPException(
            status_code=502, detail="Preview görevi kuyruğa alınamadı"
        )

    return {"task_id": task_id, "status": "pending"}


@router.get("/workspaces/{workspace_id}/policy/competitors/preview/{task_id}")
def get_competitor_preview(
    workspace_id: int,
    task_id: str,
    db: Session = Depends(get_db),
):
    """Preview durumu — workspace sahipliği brand_profile_id ile doğrulanır
    (başka workspace'in task'ı 404)."""
    verify_workspace(db, workspace_id)
    task = (
        db.query(TaskResult)
        .filter(
            TaskResult.task_id == task_id,
            TaskResult.task_type == PREVIEW_TASK_TYPE,
            TaskResult.brand_profile_id == workspace_id,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Preview görevi bulunamadı")
    return {
        "task_id": task.task_id,
        "status": task.status,
        "result": (task.result_data or {}).get("competitors"),
        "error_message": task.error_message,
    }


DISCOVERY_TASK_TYPE = "competitor_discovery"
DISCOVERY_STALE_MINUTES = 10


@router.post(
    "/workspaces/{workspace_id}/policy/competitor-discovery",
    status_code=202,
)
def start_competitor_discovery(
    workspace_id: int,
    request: CompetitorDiscoveryRequest,
    db: Session = Depends(get_db),
):
    """Confirmed profil için tek aktif, maliyet kontrollü rakip keşfi."""
    from app.core.policy.competitor_discovery import canonical_profile_fingerprint

    verify_workspace(db, workspace_id)
    workspace = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == workspace_id)
        .with_for_update()
        .first()
    )
    if workspace.status not in ("competitor_review", "confirmed"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_NOT_CONFIRMED",
                "message": "AI rakip keşfi için önce marka profilini onaylayın.",
            },
        )
    fingerprint = canonical_profile_fingerprint(workspace)
    stale_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=DISCOVERY_STALE_MINUTES
    )
    stale = (
        db.query(TaskResult)
        .filter(
            TaskResult.brand_profile_id == workspace_id,
            TaskResult.task_type == DISCOVERY_TASK_TYPE,
            TaskResult.status.in_(("pending", "running")),
            TaskResult.created_at < stale_cutoff,
        )
        .all()
    )
    for task in stale:
        task.status = "failed"
        task.error_message = "Discovery zaman aşımına uğradı"

    active = (
        db.query(TaskResult)
        .filter(
            TaskResult.brand_profile_id == workspace_id,
            TaskResult.task_type == DISCOVERY_TASK_TYPE,
            TaskResult.status.in_(("pending", "running")),
        )
        .order_by(TaskResult.created_at.desc())
        .first()
    )
    if active:
        db.commit()
        return {"task_id": active.task_id, "status": active.status, "reused": True}

    if not request.force_refresh:
        cached_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = (
            db.query(TaskResult)
            .filter(
                TaskResult.brand_profile_id == workspace_id,
                TaskResult.task_type == DISCOVERY_TASK_TYPE,
                TaskResult.status == "completed",
                TaskResult.created_at >= cached_cutoff,
            )
            .order_by(TaskResult.created_at.desc())
            .all()
        )
        for task in recent:
            if (task.result_data or {}).get("profile_fingerprint") == fingerprint:
                db.commit()
                return {"task_id": task.task_id, "status": "completed", "reused": True}

    task_id = str(uuid.uuid4())
    db.add(TaskResult(
        task_id=task_id,
        task_type=DISCOVERY_TASK_TYPE,
        brand_profile_id=workspace_id,
        status="pending",
        progress=0,
        result_data={"profile_fingerprint": fingerprint},
    ))
    db.commit()
    try:
        from app.tasks.competitor_discovery_tasks import run_competitor_discovery_task

        run_competitor_discovery_task.apply_async(
            args=[workspace_id, fingerprint], task_id=task_id
        )
    except Exception as exc:
        task = db.query(TaskResult).filter(TaskResult.task_id == task_id).first()
        if task:
            task.status = "failed"
            task.error_message = str(exc)[:500]
            db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "BROKER_ERROR", "message": "Rakip keşfi kuyruğa alınamadı."},
        )
    return {"task_id": task_id, "status": "pending", "reused": False}


@router.get(
    "/workspaces/{workspace_id}/policy/competitor-discovery/{task_id}"
)
def get_competitor_discovery(
    workspace_id: int,
    task_id: str,
    db: Session = Depends(get_db),
):
    from app.core.policy.competitor_discovery import fresh_channel_run_count

    verify_workspace(db, workspace_id)
    task = (
        db.query(TaskResult)
        .filter(
            TaskResult.task_id == task_id,
            TaskResult.task_type == DISCOVERY_TASK_TYPE,
            TaskResult.brand_profile_id == workspace_id,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Rakip keşfi bulunamadı")
    data = dict(task.result_data or {})
    return {
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress or 0,
        "candidates": data.get("candidates", []),
        "warnings": data.get("warnings", []),
        "fresh_channel_run_count": fresh_channel_run_count(db, workspace_id),
        "cost": data.get("cost"),
        "fallback_used": bool(data.get("fallback_used")),
        "code": data.get("code"),
        "error_message": task.error_message,
    }


@router.post("/workspaces/{workspace_id}/policy/competitor-discovery/decisions")
def decide_discovered_competitors(
    workspace_id: int,
    request: CompetitorDiscoveryDecisionsRequest,
    db: Session = Depends(get_db),
):
    from app.core.policy.competitor_discovery import (
        apply_competitor_term_decisions,
        canonical_profile_fingerprint,
        fresh_channel_run_count,
    )

    workspace = _lock_workspace_for_policy_mutation(db, workspace_id)
    impacted_before = fresh_channel_run_count(db, workspace_id)
    current_fingerprint = canonical_profile_fingerprint(workspace)
    entries = workspace.competitor_terms or []
    by_id = {
        entry.get("discovery_id"): entry
        for entry in entries
        if entry.get("discovery_id")
    }
    payload = []
    for decision in request.decisions:
        if decision.decision not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="decision approved|rejected olmalı")
        entry = by_id.get(decision.discovery_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Keşif adayı bulunamadı")
        # Fingerprint guard yalniz karar verilmemis oneriler icindir.
        # Onayli rakipler profil degisse de etkin politika olarak kalir ve
        # kullanici bunlari her zaman engelden cikarabilmelidir.
        if (
            entry.get("status") == "suggested"
            and entry.get("profile_fingerprint") != current_fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DISCOVERY_STALE",
                    "message": "Profil değişti, rakip önerilerini yenileyin.",
                },
            )
        payload.append(decision.model_dump())
    try:
        summary = apply_competitor_term_decisions(db, workspace, payload)
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(status_code=400, detail={"code": code, "message": code})
    except LookupError:
        raise HTTPException(status_code=404, detail="Keşif adayı bulunamadı")
    db.commit()
    db.refresh(workspace)
    return {
        **_policy_payload(db, workspace),
        **summary,
        "affected_runs": {
            "fresh_channel_run_count": impacted_before,
        },
    }


@router.post(
    "/workspaces/{workspace_id}/archive",
    response_model=WorkspaceResponse,
)
def archive_workspace(workspace_id: int, db: Session = Depends(get_db)):
    """Marka çalışmasını arşivle (soft delete)."""
    workspace = verify_workspace(db, workspace_id)
    workspace.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/restore",
    response_model=WorkspaceResponse,
)
def restore_workspace(workspace_id: int, db: Session = Depends(get_db)):
    """Arşivden çıkar."""
    workspace = db.query(BrandProfile).filter(
        BrandProfile.id == workspace_id,
        BrandProfile.deleted_at.isnot(None),
    ).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Arşivlenmiş çalışma bulunamadı")
    workspace.deleted_at = None
    db.commit()
    db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/keywords/refresh",
    response_model=WorkspaceKeywordRefreshResponse,
)
def refresh_workspace_keywords(
    workspace_id: int,
    request: WorkspaceKeywordRefreshRequest,
    db: Session = Depends(get_db),
):
    """
    Workspace keyword metriklerini Google Ads ile yeniler.
    Network çağrısı DB delete+insert transaction'ından önce tamamlanır.
    """
    workspace = verify_workspace(db, workspace_id)
    rows = (
        db.query(WorkspaceKeyword, Keyword)
        .join(Keyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == workspace_id)
        .order_by(WorkspaceKeyword.id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="Bu workspace'te yenilenecek keyword yok")

    existing = [
        ExistingWorkspaceKeyword(
            keyword_id=kw.id,
            keyword=kw.keyword,
            monthly_volume=wk.monthly_volume,
            trend_3m=wk.trend_3m,
            trend_12m=wk.trend_12m,
            competition_score=wk.competition_score,
            data_source=wk.data_source,
            sector=wk.sector,
            target_market=wk.target_market,
            geo_target_id=wk.geo_target_id,
            language_id=wk.language_id,
            notes=wk.notes,
        )
        for wk, kw in rows
    ]
    seed_keywords = [item.keyword for item in existing]

    db.commit()

    from app.api.v1.google_ads import _get_service

    customer_id = (
        request.customer_id
        or settings.GOOGLE_ADS_CUSTOMER_ID
        or settings.GOOGLE_ADS_LOGIN_CUSTOMER_ID
    )
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id gerekli")

    svc = _get_service()
    refreshed_ideas = []
    for start in range(0, len(seed_keywords), 20):
        chunk = seed_keywords[start:start + 20]
        ideas, _, _ = svc.enrich_keywords(
            customer_id=customer_id,
            seeds=chunk,
            max_results=request.max_results,
            language_id=workspace.default_language_id,
            geo_target_id=workspace.default_geo_target_id,
        )
        if request.min_volume > 0:
            ideas = [idea for idea in ideas if idea.avg_monthly_searches >= request.min_volume]
        refreshed_ideas.extend(ideas)

    merged = build_refreshed_workspace_keywords(
        existing,
        refreshed_ideas,
        include_new_ideas=request.include_new_ideas,
        data_source="google_ads_api",
    )

    existing_keywords_by_norm = {
        normalize_keyword(item.keyword): item.keyword_id
        for item in existing
    }

    try:
        db.query(WorkspaceKeyword).filter(
            WorkspaceKeyword.brand_profile_id == workspace_id
        ).delete(synchronize_session=False)

        for row in merged["rows"]:
            keyword_id = row["keyword_id"]
            if keyword_id is None:
                normalized = normalize_keyword(row["keyword"])
                keyword_id = existing_keywords_by_norm.get(normalized)
                if keyword_id is None:
                    kw = db.query(Keyword).filter(Keyword.normalized_keyword == normalized).first()
                    if not kw:
                        kw = Keyword(
                            keyword=row["keyword"],
                            normalized_keyword=normalized,
                            monthly_volume=row["monthly_volume"],
                            trend_3m=row["trend_3m"],
                            trend_12m=row["trend_12m"],
                            competition_score=row["competition_score"],
                            data_source="google_ads_api",
                            is_active=True,
                        )
                        db.add(kw)
                        db.flush()
                    keyword_id = kw.id

            db.add(WorkspaceKeyword(
                brand_profile_id=workspace_id,
                keyword_id=keyword_id,
                monthly_volume=row["monthly_volume"],
                trend_3m=row["trend_3m"],
                trend_12m=row["trend_12m"],
                competition_score=row["competition_score"],
                data_source=row["data_source"],
                sector=row["sector"],
                target_market=row["target_market"],
                geo_target_id=row["geo_target_id"] or workspace.default_geo_target_id,
                language_id=row["language_id"] or workspace.default_language_id,
                notes=row["notes"],
            ))

        # Keywords changed → mark all workspace content stale within the same transaction
        from app.core.scoring.state_machine import mark_workspace_content_stale
        mark_workspace_content_stale(db, workspace_id)

        db.commit()
    except Exception:
        db.rollback()
        raise

    diff = merged["diff"]
    return WorkspaceKeywordRefreshResponse(
        workspace_id=workspace_id,
        refreshed=diff["refreshed"],
        unchanged=diff["unchanged"],
        added=diff["added"],
        removed=diff["removed"],
        total_after=len(merged["rows"]),
    )


# ==================== Background task function ====================

def _run_keyword_suggestion(
    workspace_id: int,
    company_url: str,
    competitor_urls: list = None,
    must_have_info: str = None,
    excluded_info: str = None,
    attempt_id: Optional[str] = None,
):
    """Background task: crawl workspace site and generate reviewable seed keywords.

    `attempt_id` (plan_yapilacaklar.md 2.2): dispatch'in yazdığı token. Task'ın
    BÜTÜN yazımları (running geçişi, başarı, her failed) bu token'a koşulludur;
    eşleşmezse hiçbir şey yazılmaz (bkz. core/site_analyzer/analysis_attempt.py).
    """
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    db = SessionLocal()
    ai = None  # finally'de kapatılır (Codex v9-3)
    try:
        workspace = lock_if_current(db, workspace_id, attempt_id, action="running")
        if workspace is None:
            return

        workspace.status = "running"
        workspace.error_message = None
        db.commit()

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        # Telemetri kapsamı (Codex v8-3): BackgroundTasks'ta finalize
        # güvencesi yok → her event anında yazılır (flush_every=1)
        ai.collector = UsageCollector(brand_profile_id=workspace_id, flush_every=1)
        extractor = ProfileExtractor(ai)

        crawl = extractor.crawl_for_profile_content(company_url)
        if crawl["error"] and not crawl["site_content"]:
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                f"Anahtar kelime onerisi basarisiz: {crawl['error']}",
            )
            return

        suggestions = extractor.suggest_keywords(
            crawl["site_content"],
            must_have_info=must_have_info,
            excluded_info=excluded_info,
        )

        # Kısa süreli kilit + token kontrolü: tüm başarı yazımları tek transaction
        workspace = lock_if_current(db, workspace_id, attempt_id, action="success")
        if workspace is None:
            return
        workspace.source_pages = crawl["source_pages"]
        workspace.crawl_content_cache = crawl["site_content"]
        workspace.suggested_keywords = _normalize_seed_keywords(suggestions)[:10] or None
        if not workspace.suggested_keywords:
            workspace.status = "failed"
            workspace.error_message = "Anahtar kelime onerisi basarisiz: AI keyword dondurmedi"
        else:
            workspace.status = "keywords_review"
            workspace.error_message = None
        db.commit()

        logger.info(f"Workspace keyword suggestion completed for workspace_id={workspace_id}")

    except Exception as e:
        logger.error(f"Workspace keyword suggestion failed for workspace_id={workspace_id}: {e}")
        try:
            db.rollback()
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                safe_500_detail(e, "Anahtar kelime onerisi basarisiz oldu"),
            )
        except Exception:
            pass
    finally:
        _close_ai_quietly(ai)
        db.close()


def _run_profile_analysis_first(
    workspace_id: int,
    company_url: str,
    competitor_urls: list = None,
    attempt_id: Optional[str] = None,
):
    """Background task (profil-önce akış, adım 1): crawl + AI profil çıkarma.

    suggested_keywords bu aşamada ÜRETİLMEZ — 10 KW, profil onayından sonra
    `_run_keyword_suggestion_from_profile` ile ayrıca istenir.

    `attempt_id`: bkz. `_run_keyword_suggestion` — tüm yazımlar token'a koşulludur.
    """
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service
    from app.core.site_analyzer.profile_extractor import ProfileExtractor
    from app.core.site_analyzer.anchor_builder import build_anchor_texts

    db = SessionLocal()
    ai = None  # finally'de kapatılır (Codex v9-3)
    try:
        workspace = lock_if_current(db, workspace_id, attempt_id, action="running")
        if workspace is None:
            return

        workspace.status = "running"
        workspace.error_message = None
        db.commit()

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        # Telemetri kapsamı (Codex v8-3): BackgroundTasks'ta finalize
        # güvencesi yok → her event anında yazılır (flush_every=1)
        ai.collector = UsageCollector(brand_profile_id=workspace_id, flush_every=1)
        extractor = ProfileExtractor(ai)

        crawl = extractor.crawl_for_profile_content(company_url)
        if crawl["error"] and not crawl["site_content"]:
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                f"Profil analizi basarisiz: {crawl['error']}",
            )
            return

        profile = extractor.extract_profile_from_site_content(crawl["site_content"])
        profile.pop("suggested_keywords", None)

        # Keep validation outside the final write transaction. Its telemetry
        # is persisted through another session and must not contend with our
        # workspace row lock.
        validation = None
        if competitor_urls and profile:
            validation = extractor.validate_with_competitors(profile, competitor_urls)

        # Kısa süreli kilit + gate (plan v13) + attempt token kontrolü (2.2):
        # token eşleşmezse (yeni koşu başladı / janitor failed yaptı) HİÇBİR
        # şey yazılmaz — profile_data dahil.
        workspace = lock_if_current(db, workspace_id, attempt_id, action="success")
        if workspace is None:
            return
        workspace.source_pages = crawl["source_pages"]
        workspace.crawl_content_cache = crawl["site_content"]
        apply_profile_data_update(db, workspace, profile)
        workspace.suggested_keywords = None

        if validation is not None:
            workspace.validation_data = validation

        workspace.status = "profile_review"
        workspace.error_message = None
        db.commit()

        logger.info(f"Profile-first analysis completed for workspace_id={workspace_id}")

    except Exception as e:
        logger.error(f"Profile-first analysis failed for workspace_id={workspace_id}: {e}")
        try:
            db.rollback()
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                safe_500_detail(e, "Profil analizi başarısız oldu"),
            )
        except Exception:
            pass
    finally:
        _close_ai_quietly(ai)
        db.close()


def _run_keyword_suggestion_from_profile(
    workspace_id: int,
    attempt_id: Optional[str] = None,
):
    """Background task (profil-önce akış, adım 3→4): onaylı profilden 10 KW önerisi.

    `attempt_id` (2.2): bkz. `_run_keyword_suggestion` — tüm yazımlar token'a koşulludur.

    preliminary_info (mutlaka-olsun) doluysa profil AI ile revize edilir
    (fail-open) ve keyword önerisi revize profile dayanır — böylece kullanıcının
    'istenen konular'ı öneriyi gerçekten etkiler.

    VERI SADAKATI INVARIANT'I (plan_marka_profili_sadakati.md P0.1): revizyon
    YALNIZ BELLEKTE kalir, `profile_data`'ya ASLA yazilmaz. Profildeki her liste
    alani kullanici tarafindan duzenlenebilir (`KeywordAnchorReview` →
    EDITABLE_GROUP_FIELDS: products/services/protected_themes/use_cases/
    problems_solved/brand_terms/target_audience), company_name ve sector ise
    kullaniciya kilitli. Yani AI'in guvenle yazabilecegi alan YOKTUR: kullanici
    bir grubu duzenleyip hemen yanindaki "Keyword onerilerini yeniden uret"
    butonuna bastiginda AI duzenlemeyi eziyordu. Ayrica AI semasinda olmayan
    anahtarlar (orn. policy_specificity) dusuyor, bu da `_profile_theme_sets`
    uzerinden policy_version artirip TUM workspace ciktilarini bayatlatiyordu.

    Kullanicinin notu kaybolmaz: excluded_info → exclude_themes + topic_policy
    kaliciligi onay yolunda `apply_competitor_review` tarafindan saglanir,
    must_have_info ise keyword prompt'una ayri parametre olarak gider.
    """
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service
    from app.core.site_analyzer.profile_extractor import (
        ProfileExtractor,
        resolve_site_content,
    )
    from app.core.site_analyzer.anchor_builder import build_anchor_texts
    from app.core.policy.competitor_policy import approved_competitor_terms, match_term

    db = SessionLocal()
    ai = None  # finally'de kapatılır (Codex v9-3)
    try:
        workspace = lock_if_current(db, workspace_id, attempt_id, action="running")
        if workspace is None:
            return

        workspace.status = "running"
        workspace.error_message = None
        db.commit()

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        # Telemetri kapsamı (Codex v8-3): BackgroundTasks'ta finalize
        # güvencesi yok → her event anında yazılır (flush_every=1)
        ai.collector = UsageCollector(brand_profile_id=workspace_id, flush_every=1)
        extractor = ProfileExtractor(ai)

        company_url = workspace.company_url
        preliminary_info = workspace.preliminary_info
        excluded_info = workspace.excluded_info
        profile = workspace.profile_data if isinstance(workspace.profile_data, dict) else {}
        competitor_terms = approved_competitor_terms(workspace)
        # Cache okuma kapisi: dolu-ama-bozuk cache AI'a GIDEMEZ (bkz. resolve_site_content).
        site_content, crawled_source_pages, content_error = resolve_site_content(
            extractor, company_url, workspace.crawl_content_cache
        )
        if content_error:
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                f"Anahtar kelime onerisi basarisiz: {content_error}",
            )
            return

        # SALT-BELLEK revizyon: yalniz asagidaki keyword onerisi prompt'unu
        # sekillendirir; DB'ye yazilmaz (bkz. docstring'teki sadakat invariant'i).
        if (preliminary_info or "").strip():
            revised = extractor.revise_profile_with_requirements(
                profile,
                must_have_info=preliminary_info,
                excluded_info=excluded_info,
            )
            if revised is not profile:
                revised["exclude_themes"] = _merge_exclude_themes(
                    excluded_info,
                    revised.get("exclude_themes", []),
                )
                profile = revised

        suggestions = extractor.suggest_keywords_from_profile(
            site_content,
            profile,
            must_have_info=preliminary_info,
            excluded_info=excluded_info,
            competitor_terms=competitor_terms,
        )
        # Prompt yonlendirmedir; deterministic policy son savunmadir.
        suggestions = [
            keyword for keyword in suggestions
            if match_term(keyword, competitor_terms) is None
        ]

        # Crawl and AI work is complete; keep the row lock only for writes.
        # Attempt token kontrolü aynı kilit altında (2.2): eski koşu yazamaz.
        workspace = lock_if_current(db, workspace_id, attempt_id, action="success")
        if workspace is None:
            return
        if crawled_source_pages is not None:
            workspace.source_pages = crawled_source_pages
            workspace.crawl_content_cache = site_content
        # profile_data'ya YAZIM YOK — sadakat invariant'i (P0.1). Bu yol
        # yalniz crawl cache'ini ve suggested_keywords'u gunceller; anchor/
        # policy surumleri de dolayisiyla artmaz.

        workspace.suggested_keywords = _normalize_seed_keywords(suggestions)[:10] or None
        if not workspace.suggested_keywords:
            workspace.status = "failed"
            workspace.error_message = "Anahtar kelime onerisi basarisiz: AI keyword dondurmedi"
        else:
            workspace.status = "keywords_review"
            workspace.error_message = None
        db.commit()

        logger.info(
            f"Profile-first keyword suggestion completed for workspace_id={workspace_id}"
        )

    except Exception as e:
        logger.error(
            f"Profile-first keyword suggestion failed for workspace_id={workspace_id}: {e}"
        )
        try:
            db.rollback()
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                safe_500_detail(e, "Anahtar kelime onerisi basarisiz oldu"),
            )
        except Exception:
            pass
    finally:
        _close_ai_quietly(ai)
        db.close()


def _run_profile_from_keywords(
    workspace_id: int,
    keywords: List[str],
    attempt_id: Optional[str] = None,
):
    """Background task: generate the full profile from approved seed keywords.

    `attempt_id` (2.2): bkz. `_run_keyword_suggestion` — tüm yazımlar token'a koşulludur.
    """
    from app.database.connection import SessionLocal
    from app.generators.ai_service import get_ai_service
    from app.core.site_analyzer.profile_extractor import (
        ProfileExtractor,
        resolve_site_content,
    )
    from app.core.site_analyzer.anchor_builder import build_anchor_texts

    db = SessionLocal()
    ai = None  # finally'de kapatılır (Codex v9-3)
    try:
        workspace = lock_if_current(db, workspace_id, attempt_id, action="running")
        if workspace is None:
            return

        workspace.status = "running"
        workspace.error_message = None
        db.commit()

        ai = get_ai_service(api_key=settings.GEMINI_API_KEY)
        # Telemetri kapsamı (Codex v8-3): BackgroundTasks'ta finalize
        # güvencesi yok → her event anında yazılır (flush_every=1)
        ai.collector = UsageCollector(brand_profile_id=workspace_id, flush_every=1)
        extractor = ProfileExtractor(ai)

        company_url = workspace.company_url
        preliminary_info = workspace.preliminary_info
        excluded_info = workspace.excluded_info
        competitor_urls = list(workspace.competitor_urls or [])
        # Cache okuma kapisi: dolu-ama-bozuk cache AI'a GIDEMEZ (bkz. resolve_site_content).
        site_content, crawled_source_pages, content_error = resolve_site_content(
            extractor, company_url, workspace.crawl_content_cache
        )
        if content_error:
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                f"Profil uretimi basarisiz: {content_error}",
            )
            return

        approved_keywords = _normalize_seed_keywords(keywords)
        if not approved_keywords:
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                "Profil uretimi basarisiz: En az 1 keyword gerekli",
            )
            return

        profile = extractor.extract_profile_from_keywords(
            site_content,
            approved_keywords,
            must_have_info=preliminary_info,
            excluded_info=excluded_info,
        )
        profile["exclude_themes"] = _merge_exclude_themes(
            excluded_info,
            profile.get("exclude_themes", []),
        )

        validation = None
        if competitor_urls and profile:
            validation = extractor.validate_with_competitors(
                profile,
                competitor_urls,
            )

        # Kısa süreli kilit + gate (plan v13) + attempt token kontrolü (2.2):
        # token eşleşmezse (yeni koşu başladı / janitor failed yaptı) HİÇBİR
        # şey yazılmaz — profile_data dahil.
        workspace = lock_if_current(db, workspace_id, attempt_id, action="success")
        if workspace is None:
            return
        if crawled_source_pages is not None:
            workspace.source_pages = crawled_source_pages
            workspace.crawl_content_cache = site_content
        apply_profile_data_update(db, workspace, profile)
        workspace.suggested_keywords = approved_keywords

        if validation is not None:
            workspace.validation_data = validation

        workspace.status = "draft"
        workspace.error_message = None
        db.commit()

        logger.info(f"Workspace profile generated from keywords for workspace_id={workspace_id}")

    except Exception as e:
        logger.error(f"Workspace profile generation failed for workspace_id={workspace_id}: {e}")
        try:
            db.rollback()
            mark_failed_if_current(
                db, workspace_id, attempt_id,
                safe_500_detail(e, "Profil uretimi basarisiz oldu"),
            )
        except Exception:
            pass
    finally:
        _close_ai_quietly(ai)
        db.close()


def _run_relevance_computation(scoring_run_id: int):
    """
    Background task: compute keyword-brand relevance scores.
    Auto-triggered after profile confirmation.
    Fail-open: errors are logged but don't break the pipeline.
    """
    from app.database.connection import SessionLocal

    db = SessionLocal()
    try:
        # Yeni mimari: ScoringRun.brand_profile_id üzerinden; eski 1:1 fallback
        scoring_run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        profile = None

        # Eski motor run'ı salt-okunur (task sınırı): embedding çağrısı ve
        # otomatik kanal ataması YAPILMAZ. Bu iş için run'ı
        # relevance_computing'e almış bir tetikleyici varsa run orada
        # takılı kalmaz — fail-closed 'failed'a çekilir.
        if scoring_run is not None and is_legacy_run(scoring_run):
            logger.warning(
                f"Relevance compute refused ({LEGACY_RUN_READ_ONLY}): run "
                f"{scoring_run_id} algorithm_version="
                f"{run_algorithm_version(scoring_run)}"
            )
            if scoring_run.status in ("relevance_computing",):
                try:
                    transition(db, scoring_run, target="failed")
                except ValueError:
                    pass
            return

        if scoring_run and scoring_run.brand_profile_id:
            profile = db.query(BrandProfile).filter(
                BrandProfile.id == scoring_run.brand_profile_id,
                BrandProfile.status == "confirmed",
                BrandProfile.deleted_at.is_(None),
            ).first()

        if not profile:
            logger.warning(f"Relevance compute skipped: no confirmed profile for run {scoring_run_id}")
            if scoring_run:
                try:
                    transition(db, scoring_run, target="failed")
                except ValueError:
                    pass
            return

        # Sürümlü ortak core servis (plan v13): snapshot başta, kilitli
        # doğrulama yazımdan hemen önce serviste. Hata → RelevanceRefreshError
        # (eski satırlar korunur) → mevcut fail-open bloğu devralır.
        from app.core.relevance import refresh_keyword_relevance
        from app.core.telemetry import UsageCollector

        requested_anchor_version = int(profile.anchor_version or 1)
        usage_collector = UsageCollector(
            scoring_run_id=scoring_run_id,
            brand_profile_id=scoring_run.brand_profile_id if scoring_run else None,
        )
        try:
            refresh_result = refresh_keyword_relevance(
                db,
                scoring_run,
                profile,
                requested_anchor_version=requested_anchor_version,
                collector=usage_collector,
            )
        finally:
            usage_collector.finalize()

        computed = refresh_result.computed
        keywords_total = refresh_result.total_keywords

        # Status geçişi: relevance_computing → relevance_computed
        if scoring_run:
            try:
                transition(db, scoring_run, target="relevance_computed")
                if scoring_run.auto_assign_channels:
                    from app.core.channel.assignment_dispatcher import enqueue_channel_assignment

                    try:
                        enqueue_channel_assignment(
                            db,
                            scoring_run,
                            relevance_coefficient=float(scoring_run.default_relevance_coefficient),
                            from_status="relevance_computed",
                        )
                    except Exception as assign_error:
                        # Relevance succeeded; channel enqueue failure is recorded on TaskResult
                        # by enqueue_channel_assignment and must not turn the scored run into
                        # a full relevance failure.
                        logger.error(
                            "Auto channel assignment enqueue failed after relevance "
                            f"for run {scoring_run_id}: {assign_error}"
                        )
            except ValueError as ve:
                logger.warning(f"Relevance status transition failed: {ve}")

        logger.info(
            f"Auto relevance computation completed for run {scoring_run_id}: "
            f"{computed}/{keywords_total} keywords scored"
        )

    except Exception as e:
        logger.error(f"Auto relevance computation failed for run {scoring_run_id}: {e}")
        # Fail-open (run-17 dersi): relevance OPSIYONELDIR — hatasi auto
        # akista tum run'u olduremez. auto_assign aciksa kanal atamasi
        # relevance'siz devam eder (relevance_computing → channel_assigning
        # gecisi state machine'de izinli); degilse run failed'a cekilir ki
        # kullanici durumu gorsun ve yeniden deneyebilsin.
        if scoring_run:
            try:
                db.rollback()  # yarim kalmis relevance yazimlari atilir
                if scoring_run.auto_assign_channels:
                    from app.core.channel.assignment_dispatcher import (
                        enqueue_channel_assignment,
                    )
                    enqueue_channel_assignment(
                        db,
                        scoring_run,
                        relevance_coefficient=float(
                            scoring_run.default_relevance_coefficient
                        ),
                        from_status=scoring_run.status,
                    )
                    logger.info(
                        f"Relevance hatasina ragmen run {scoring_run_id} icin "
                        f"kanal atamasi relevance'siz enqueue edildi (fail-open)"
                    )
                else:
                    transition(db, scoring_run, target="failed")
            except Exception as recovery_error:
                logger.error(
                    f"Relevance fail-open kurtarmasi da basarisiz "
                    f"(run {scoring_run_id}): {recovery_error}"
                )
                try:
                    transition(db, scoring_run, target="failed")
                except ValueError:
                    pass
    finally:
        db.close()
