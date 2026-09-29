"""
Keyword management endpoints.

Plan2 §P0/C3 — legacy global yollar kapatıldı. brand_profile_id artık
list/get/create/update/delete/import/cleanup-duplicates için zorunlu;
verilmezse 400 ("brand_profile_id is required"). Global Keyword
silme yolları (crud.delete_all_keywords / crud.delete_keyword) artık
endpoint'lerden çağrılmıyor.
"""
import base64
import gzip
import json
import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.v1.google_ads import _get_redis_client
from app.core.csv_import.google_ads_parser import GoogleAdsCSVError, parse_google_ads_csv
from app.core.csv_import.import_plan import (
    apply_import_plan,
    build_import_plan,
    compact_plan_payload,
    import_plan_to_payload,
)
from app.core.constants import WORKSPACE_KEYWORD_LIMIT
from app.core.policy.import_gate import resolve_import_theme_policy
from app.core.workspace import verify_workspace
from app.database import crud
from app.database.models import Keyword, WorkspaceKeyword
from app.dependencies import get_db
from app.schemas.keyword import (
    KeywordCreate,
    KeywordImportRequest,
    KeywordImportResponse,
    KeywordUploadCsvCommitRequest,
    KeywordUploadCsvResponse,
    KeywordListResponse,
    KeywordResponse,
    KeywordUpdate,
    WorkspaceKeywordResponse,
)


router = APIRouter()

CSV_IMPORT_MAX_BYTES = 10 * 1024 * 1024
CSV_IMPORT_MAX_ROWS = 50_000
CSV_IMPORT_TOKEN_TTL_SECONDS = 10 * 60
CSV_IMPORT_TOKEN_PREFIX = "keyword_csv_import"


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _encode_token_payload(payload: dict) -> str:
    raw = json.dumps(payload, default=_json_default, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def _decode_token_payload(raw: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(raw)).decode("utf-8"))


def _token_key(token: str) -> str:
    return f"{CSV_IMPORT_TOKEN_PREFIX}:{token}"


def _store_import_plan(payload: dict) -> str:
    redis_client = _get_redis_client()
    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Analiz sonucu geçici olarak saklanamadı, lütfen tekrar deneyin",
        )
    token = str(uuid.uuid4())
    try:
        redis_client.setex(_token_key(token), CSV_IMPORT_TOKEN_TTL_SECONDS, _encode_token_payload(payload))
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Analiz sonucu geçici olarak saklanamadı, lütfen tekrar deneyin",
        )
    return token


def _load_import_plan(token: str) -> dict:
    redis_client = _get_redis_client()
    if not redis_client:
        raise HTTPException(
            status_code=503,
            detail="Analiz sonucu geçici olarak okunamadı, lütfen tekrar deneyin",
        )
    try:
        raw = redis_client.get(_token_key(token))
    except Exception:
        raw = None
    if not raw:
        raise HTTPException(status_code=410, detail="Analiz süresi doldu, lütfen dosyayı tekrar analiz edin")
    return _decode_token_payload(raw)



def _verify_keyword_in_workspace(
    db: Session, keyword_id: int, brand_profile_id: int
) -> tuple[Keyword, WorkspaceKeyword]:
    """Return (Keyword, WorkspaceKeyword) when the keyword is linked to the
    workspace; otherwise raise 404 (existence leak prevention)."""
    row = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(Keyword.id == keyword_id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="keyword not found in workspace")
    keyword, wk = row
    return keyword, wk


@router.get("/", response_model=KeywordListResponse)
def list_keywords(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2000),
    active_only: bool = Query(True),
    sector: Optional[str] = Query(None),
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """List keywords in a workspace.

    `brand_profile_id` zorunludur. Yalnızca bu workspace'in WorkspaceKeyword
    bağlantılı keyword'leri döner.
    """
    verify_workspace(db, brand_profile_id)

    base_query = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
    )
    if active_only:
        base_query = base_query.filter(Keyword.is_active == True)  # noqa: E712
    if sector:
        base_query = base_query.filter(WorkspaceKeyword.sector == sector)

    total = base_query.count()
    rows = base_query.order_by(Keyword.id).offset(skip).limit(limit).all()

    items = []
    for kw, wk in rows:
        resp = WorkspaceKeywordResponse.model_validate(kw)
        resp.monthly_volume = wk.monthly_volume or 0
        resp.trend_3m = wk.trend_3m or 0
        resp.trend_12m = wk.trend_12m or 0
        resp.competition_score = wk.competition_score or 0.5
        resp.sector = wk.sector
        resp.target_market = wk.target_market
        resp.data_source = wk.data_source or "csv"
        resp.wk_id = wk.id
        resp.wk_monthly_volume = resp.monthly_volume
        resp.wk_trend_3m = float(resp.trend_3m)
        resp.wk_trend_12m = float(resp.trend_12m)
        resp.wk_competition_score = float(resp.competition_score)
        resp.wk_data_source = resp.data_source
        items.append(resp)

    return KeywordListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/pool/export.xlsx")
def export_keyword_pool_xlsx(
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Aktif kelime havuzunu XLSX olarak indirir (plan v4 §3).

    Sozlesme: ekranla ayni — YALNIZ `is_active=True` bagli keyword'ler,
    metrikler WorkspaceKeyword snapshot'indan. Statik yol `/pool/export.xlsx`
    dinamik `/{keyword_id}` route'larindan ONCE tanimlidir (cakisma olmaz).
    """
    import io

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from app.exporters.safe_text import safe_excel_text

    verify_workspace(db, brand_profile_id)

    rows = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .filter(Keyword.is_active == True)  # noqa: E712
        .order_by(Keyword.id)
        .all()
    )

    if not rows:
        raise HTTPException(status_code=400, detail="Kelime havuzu boş — önce keyword ekleyin")

    wb = Workbook()
    ws = wb.active
    ws.title = "Aktif Kelime Havuzu"

    headers = ['Keyword', 'Kaynak', 'Sektör', 'Aylık Hacim', 'Trend 3M (%)', 'Trend 12M (%)', 'Rekabet']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.auto_filter.ref = ws.dimensions

    for kw, wk in rows:
        ws.append([
            safe_excel_text(kw.keyword),
            safe_excel_text(wk.data_source or 'csv'),
            safe_excel_text(wk.sector or ''),
            wk.monthly_volume or 0,
            float(wk.trend_3m) if wk.trend_3m is not None else 0,
            float(wk.trend_12m) if wk.trend_12m is not None else 0,
            float(wk.competition_score) if wk.competition_score is not None else 0,
        ])

    for i, width in enumerate([40, 14, 16, 12, 13, 13, 10], 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"digitus_kelime_havuzu_{brand_profile_id}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/upload-csv", response_model=KeywordUploadCsvResponse)
async def upload_csv_keywords(
    brand_profile_id: int = Form(...),
    dry_run: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Analyze a Google Ads CSV file and return a dry-run import plan."""
    if not dry_run:
        raise HTTPException(status_code=400, detail="CSV import önce dry-run olarak analiz edilmelidir")
    workspace = verify_workspace(db, brand_profile_id)

    content = await file.read()
    if len(content) > CSV_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="CSV dosyası 10 MB sınırını aşıyor")

    try:
        parsed = parse_google_ads_csv(content, filename=file.filename or "upload.csv")
    except GoogleAdsCSVError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if len(parsed.keywords) > CSV_IMPORT_MAX_ROWS:
        raise HTTPException(status_code=422, detail="CSV dosyası 50.000 satır sınırını aşıyor")

    parser_meta = parsed.meta.__dict__
    plan = build_import_plan(
        db,
        brand_profile_id=brand_profile_id,
        keyword_rows=[item.to_keyword_data() for item in parsed.keywords],
        parser_meta=parser_meta,
        source_file_name=file.filename or "upload.csv",
        junk_rows=parsed.junk_rows,
        # P1.6: duz liste DEGIL politika — provenance (kullanici onayli /
        # AI kaynakli / korunan) listeden turetilemez.
        theme_policy=resolve_import_theme_policy(workspace),
        pool_limit=WORKSPACE_KEYWORD_LIMIT,
    )
    response_payload = import_plan_to_payload(plan)
    response_payload["dry_run_token"] = _store_import_plan(compact_plan_payload(plan))
    return KeywordUploadCsvResponse.model_validate(response_payload)


@router.post("/upload-csv/commit", response_model=KeywordUploadCsvResponse)
def commit_csv_keywords(
    payload: KeywordUploadCsvCommitRequest,
    db: Session = Depends(get_db),
):
    """Commit a previously created CSV dry-run token."""
    verify_workspace(db, payload.brand_profile_id)
    plan_payload = _load_import_plan(payload.dry_run_token)
    if int(plan_payload.get("brand_profile_id")) != payload.brand_profile_id:
        raise HTTPException(status_code=403, detail="Bu import token'ı bu workspace için geçerli değil")

    result = apply_import_plan(db, plan_payload, pool_limit=WORKSPACE_KEYWORD_LIMIT)
    result.update(
        {
            "dry_run_token": None,
            "parser_meta": plan_payload.get("parser_meta") or {},
            "accepted_keywords": [],
            "excluded_keywords": [],
            "exact_duplicates": [],
            "exact_duplicates_different_metrics": [],
            "fuzzy_skipped": [],
            "fuzzy_kept": [],
            "junk_rows": [],
            "skipped_exact": 0,
            "skipped_fuzzy": 0,
            "kept_fuzzy_different_metrics": 0,
            "kept_fuzzy_default_metrics": 0,
            "exact_duplicate_different_metrics": 0,
        }
    )
    return KeywordUploadCsvResponse.model_validate(result)


@router.get("/{keyword_id}", response_model=KeywordResponse)
def get_keyword(
    keyword_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get a specific keyword by ID. The keyword must be linked to the workspace."""
    verify_workspace(db, brand_profile_id)
    keyword, _ = _verify_keyword_in_workspace(db, keyword_id, brand_profile_id)
    return KeywordResponse.model_validate(keyword)


@router.post("/", response_model=KeywordResponse, status_code=201)
def create_keyword(
    keyword_data: KeywordCreate,
    db: Session = Depends(get_db),
):
    """Create a keyword and link it to a workspace.

    `brand_profile_id` zorunludur (request body içinden gelir).
    """
    if keyword_data.brand_profile_id is None:
        raise HTTPException(status_code=400, detail="brand_profile_id is required")
    workspace = verify_workspace(db, keyword_data.brand_profile_id)

    result = crud.create_keywords_bulk(
        db,
        [keyword_data.model_dump(exclude={"brand_profile_id"})],
        brand_profile_id=keyword_data.brand_profile_id,
        return_details=True,
        theme_policy=resolve_import_theme_policy(workspace),
        pool_limit=WORKSPACE_KEYWORD_LIMIT,
    )
    if isinstance(result, dict) and (result["created"] + result["linked"]) > 0:
        keyword = crud.get_keyword_by_text(db, keyword_data.keyword)
        if keyword:
            return KeywordResponse.model_validate(keyword)
    if isinstance(result, dict) and result.get("skipped_theme"):
        matched = next(
            (d.get("matched") for d in result.get("skipped_details", [])
             if d.get("reason") == "skipped_theme"),
            None,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Keyword yasaklı temayla eşleşti: {matched}. force_include ile ekleyebilirsiniz.",
        )
    if isinstance(result, dict) and result.get("skipped_limit"):
        raise HTTPException(
            status_code=400,
            detail=f"Havuz limiti dolu ({WORKSPACE_KEYWORD_LIMIT}). Önce keyword silin.",
        )
    raise HTTPException(status_code=400, detail="Keyword could not be created")


@router.put("/{keyword_id}", response_model=KeywordResponse)
def update_keyword(
    keyword_id: int,
    keyword_data: KeywordUpdate,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Update a keyword linked to a workspace.

    Keyword rows are global and can be linked to multiple workspaces. Updating a
    shared row would leak the change into other workspaces, so shared keywords
    are rejected until a clone/relink flow is implemented.
    """
    verify_workspace(db, brand_profile_id)
    _verify_keyword_in_workspace(db, keyword_id, brand_profile_id)

    link_count = (
        db.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.keyword_id == keyword_id)
        .count()
    )
    if link_count > 1:
        raise HTTPException(
            status_code=409,
            detail="keyword is linked to multiple workspaces; update would affect other workspaces",
        )

    keyword = crud.update_keyword(
        db,
        keyword_id,
        keyword_data.model_dump(exclude_unset=True),
    )
    if not keyword:
        raise HTTPException(status_code=404, detail="keyword not found")
    return KeywordResponse.model_validate(keyword)


@router.delete("/all", status_code=204)
def delete_all_keywords(
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Unlink all keywords from a workspace.

    `brand_profile_id` zorunlu. Global Keyword tablosu KORUNUR — yalnızca
    WorkspaceKeyword bağlantıları silinir. Global delete legacy yolu
    plan2 §P0/C3 ile kaldırıldı (veri kaybı riski).
    """
    verify_workspace(db, brand_profile_id)
    (
        db.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return None


@router.delete("/{wk_id}", status_code=204)
def delete_keyword(
    wk_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Unlink a single WorkspaceKeyword snapshot from a workspace.

    Path param is WorkspaceKeyword.id (wk_id), NOT Keyword.id.
    This allows deleting one metric snapshot without touching other snapshots
    of the same keyword text. Global Keyword row is preserved. (plan2 §P0/C3)
    """
    verify_workspace(db, brand_profile_id)

    success = crud.remove_workspace_keyword_by_id(db, wk_id, brand_profile_id)
    if not success:
        raise HTTPException(status_code=404, detail="keyword not found in workspace")
    return None


@router.post("/import", response_model=KeywordImportResponse)
def import_keywords(
    import_data: KeywordImportRequest,
    db: Session = Depends(get_db),
):
    """Bulk import keywords into a workspace. `brand_profile_id` zorunlu."""
    if import_data.brand_profile_id is None:
        raise HTTPException(status_code=400, detail="brand_profile_id is required")
    workspace = verify_workspace(db, import_data.brand_profile_id)

    total_before = (
        db.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.brand_profile_id == import_data.brand_profile_id)
        .count()
    )
    result = crud.create_keywords_bulk(
        db,
        [kw.model_dump(exclude={"brand_profile_id"}) for kw in import_data.keywords],
        brand_profile_id=import_data.brand_profile_id,
        return_details=True,
        theme_policy=resolve_import_theme_policy(workspace),
        pool_limit=WORKSPACE_KEYWORD_LIMIT,
    )

    if isinstance(result, dict):
        created_new = result["created"]
        linked_existing = result["linked"]
        created = created_new + linked_existing
        skipped = (
            result["skipped_exact"]
            + result["skipped_fuzzy"]
            + result.get("fuzzy_merged_in_batch", 0)
            + result.get("skipped_theme", 0)
            + result.get("skipped_limit", 0)
        )
        fuzzy_merged_in_batch = result.get("fuzzy_merged_in_batch", 0)
        skipped_theme = result.get("skipped_theme", 0)
        skipped_limit = result.get("skipped_limit", 0)
        skipped_details = result.get("skipped_details", [])
        theme_warnings = result.get("theme_warnings", [])
    else:
        created_new = result
        linked_existing = 0
        created = result
        skipped = len(import_data.keywords) - created
        fuzzy_merged_in_batch = 0
        skipped_theme = 0
        skipped_limit = 0
        skipped_details = []
        theme_warnings = []

    total_after = (
        db.query(WorkspaceKeyword)
        .filter(WorkspaceKeyword.brand_profile_id == import_data.brand_profile_id)
        .count()
    )

    return KeywordImportResponse(
        created=created,
        skipped=skipped,
        requested=len(import_data.keywords),
        total_before=total_before,
        total_after=total_after,
        created_new=created_new,
        linked_existing=linked_existing,
        fuzzy_merged_in_batch=fuzzy_merged_in_batch,
        skipped_theme=skipped_theme,
        skipped_limit=skipped_limit,
        pool_limit=WORKSPACE_KEYWORD_LIMIT,
        pool_total=total_after,
        skipped_details=skipped_details,
        warned_theme=len(theme_warnings),
        theme_warnings=theme_warnings,
        message=(
            f"{created} keywords created/linked, {skipped} skipped "
            f"(total: {total_before} -> {total_after})"
            + (
                f" | {len(theme_warnings)} satır AI dışlama temasına takıldı "
                f"(eklendi, gözden geçirin)"
                if theme_warnings else ""
            )
        ),
    )


@router.post("/cleanup-duplicates")
def cleanup_duplicate_keywords(
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Workspace içindeki fuzzy duplicate keyword'leri tespit edip workspace bağlantısını kaldırır.

    `brand_profile_id` zorunlu. Sadece bu workspace'e bağlı WorkspaceKeyword'lerde
    dedup yapar; duplicate bulunanların WorkspaceKeyword link'i silinir. Global Keyword
    satırı ve diğer workspace'lerin bağlantıları KORUNUR (plan2 §P0/C3; global cleanup
    ve global is_active mutasyonu kaldırıldı).
    """
    from loguru import logger

    from app.core.keyword_dedup import deduplicate_keywords

    verify_workspace(db, brand_profile_id)

    # Workspace'e bağlı, aktif keyword'leri çek.
    rows = (
        db.query(Keyword, WorkspaceKeyword)
        .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
        .filter(WorkspaceKeyword.brand_profile_id == brand_profile_id)
        .filter(Keyword.is_active == True)  # noqa: E712
        .all()
    )

    if len(rows) <= 1:
        return {
            "total_links_before": len(rows),
            "total_links_after": len(rows),
            "links_removed": 0,
            "removed_keywords": [],
            "message": "No duplicates found",
            # Geriye uyumluluk (deprecated) — link-removal anlamiyla korunur
            "duplicates_deactivated": 0,
            "deactivated_keywords": [],
        }

    # Deterministik survivor (Codex, Hissefy denetimi): "ilk kayıt kalır"
    # kuralı DB satır sırasına bağlı kalmasın — sabit sıra kilitlenir
    rows = sorted(rows, key=lambda pair: (pair[1].id, pair[0].id))
    keyword_dicts = [
        {
            "keyword": kw.keyword,
            "monthly_volume": wk.monthly_volume or 0,
            "competition_score": float(wk.competition_score or 0),
            "_db_id": kw.id,
        }
        for kw, wk in rows
    ]

    deduped, merge_details = deduplicate_keywords(
        keyword_dicts, return_merge_details=True)
    survived_ids = {d["_db_id"] for d in deduped}

    # Workspace-scoped dedup: global Keyword.is_active'e DOKUNMA. Keyword satirlari
    # global ve birden fazla workspace'e baglanabilir; global flag'i kapatmak duplicate'i
    # diger workspace'lerde de pasiflestirir (sizinti). Bunun yerine yalnizca bu
    # workspace'in WorkspaceKeyword baglantisini kaldir (plan2 §P0/C3 delete semantigi).
    removed_keywords = []
    for kw, wk in rows:
        if kw.id not in survived_ids:
            db.delete(wk)
            removed_keywords.append({"id": kw.id, "keyword": kw.keyword})

    removed_count = len(removed_keywords)
    if removed_keywords:
        db.commit()
        logger.info(
            "Workspace {} cleanup: {} duplicate keyword link(s) removed",
            brand_profile_id,
            removed_count,
        )

    return {
        "total_links_before": len(rows),
        "total_links_after": len(rows) - removed_count,
        "links_removed": removed_count,
        "removed_keywords": removed_keywords[:50],
        # GERÇEK dropped→kept eşleşmesi (Codex, Hissefy denetimi):
        # tüketiciler hedefi fuzzy ile YENİDEN TAHMİN ETMEZ
        "merge_details": merge_details,
        "message": f"{removed_count} duplicate keyword link(s) removed from workspace",
        # Geriye uyumluluk (deprecated) — eski isimler artik link-removal sayisini tasir
        "duplicates_deactivated": removed_count,
        "deactivated_keywords": removed_keywords[:50],
    }
