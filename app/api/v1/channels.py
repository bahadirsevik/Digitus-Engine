"""
Channel assignment endpoints.

Plan2 §P0/C2 — workspace-aware. Mutating /assign brand_profile_id zorunlu,
read /pools opsiyonel + warning (P4'te zorunlu).
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_ai
from app.core.http_headers import content_disposition
from app.generators.ai_service import AIService
from app.core.channel.channel_engine import ChannelEngine
from app.core.channel.assignment_dispatcher import (
    ChannelAssignmentPreconditionError,
    enqueue_channel_assignment,
    get_active_channel_assignment_task,
    get_active_generation_task,
)
from app.core.engine_version_gate import require_non_legacy_run
from app.core.scoring.state_machine import mark_run_content_stale
from app.core.workspace import pool_freshness_for_run, verify_scoring_run
from app.database.models import ChannelPool
from app.schemas.channel import ChannelAssignRequest


router = APIRouter()


@router.post("/runs/{run_id}/assign")
def run_channel_assignment(
    run_id: int,
    request: ChannelAssignRequest | None = Body(default=None),
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Run full channel assignment process as a background Celery task.

    Mutating: brand_profile_id zorunlu.
    Returns task_id for polling progress via /api/v1/tasks/{task_id}.
    """
    scoring_run = verify_scoring_run(db, run_id, brand_profile_id)
    # Eski motor run'ı salt-okunur: yeniden atama (AI + screening) açılmaz
    require_non_legacy_run(scoring_run)

    # Guard: prevent re-assignment while content generation tasks are active.
    active_channel_task = get_active_channel_assignment_task(db, run_id)
    if active_channel_task:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Kanal ataması zaten çalışıyor.",
                "task_id": active_channel_task.task_id,
            },
        )

    active_generation_task = get_active_generation_task(db, run_id)
    if active_generation_task:
        raise HTTPException(
            status_code=409,
            detail=(
                "Active content generation task exists for this run. "
                "Wait until it completes before re-running channel assignment."
            ),
        )

    override = request.relevance_override if request else None
    effective_relevance_coefficient = float(
        override if override is not None else scoring_run.default_relevance_coefficient
    )

    try:
        assignment = enqueue_channel_assignment(
            db,
            scoring_run,
            relevance_coefficient=effective_relevance_coefficient,
            from_status=scoring_run.status,
            # Codex 10. tur #4: onay dispatch'e BAGLANIR
            approved_screening_mode=(request.screening_mode
                                     if request else None),
            approved_preflight_sha256=(request.preflight_sha256
                                       if request else None),
            approved_screening_hard_cap_usd=(
                request.approved_screening_hard_cap_usd if request else None),
        )
    except ChannelAssignmentPreconditionError as exc:
        # v2.1 Faz C (Codex #5): tipli ön-koşul redleri 409 sözleşmesiyle
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message},
        )

    return {
        "task_id": assignment["task_id"],
        "status": assignment["status"],
        "scoring_run_id": run_id,
        "effective_relevance_coefficient": assignment["effective_relevance_coefficient"],
        "screening_mode": assignment.get("screening_mode"),
        "screening_job_id": assignment.get("screening_job_id"),
        "screening_skipped_reason": assignment.get("screening_skipped_reason"),
    }


@router.get("/runs/{run_id}/assignment-preflight")
def get_assignment_preflight(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    mode: str | None = Query(
        default=None,
        description="off|shadow|assistive — verilmezse run tercihi kullanılır"),
    relevance_override: float | None = Query(
        default=None, ge=0.1, le=3.0,
        description=("Dispatch'te kullanılacak ilgi katsayısı; preflight "
                     "SHA'sı buna BAĞLIDIR — UI ekrandaki değeri "
                     "göndermelidir")),
    db: Session = Depends(get_db),
):
    """Tarama modu için ONAY paketi (plan §6.3) — hiçbir şey başlatmaz.

    Fail-closed: bağlam/evren/fiyat/telemetri eksikse tahmin uydurulmaz,
    `{code, message}` ile 409 döner. UI, ONAYLANAN tutar olarak YALNIZ
    `enforced_hard_cap_usd` (ledger'ın uyguladığı screening cap'i)
    gösterir ve onayı `preflight_sha256` ile bağlar. Shadow ve assistive'de
    downstream cap aynı attempt ledger'ında ayrıca uygulanır ve preflight
    SHA'sına dahildir.

    `relevance_override` dispatch ile AYNI değer olmalıdır: katsayı SHA
    bileşenidir, farklı gönderilirse dispatch PREFLIGHT_MISMATCH verir.
    """
    from app.config import settings as _settings
    from app.core.screening.preflight import (
        MODE_OFF,
        PreflightError,
        build_assignment_preflight,
        resolve_screening_mode,
    )
    from app.database.models import BrandProfile

    scoring_run = verify_scoring_run(db, run_id, brand_profile_id)
    effective_mode = mode or resolve_screening_mode(scoring_run, _settings)
    if effective_mode == MODE_OFF:
        # Off yolu ücretsizdir: onay paketi YOKTUR, cap oluşmaz
        return {
            "scoring_run_id": run_id,
            "screening_mode": MODE_OFF,
            # Codex 22. tur #5: `screening_enabled` UI GORUNURLUGUDUR ve
            # artik AYRI bayraga baglidir. Yeni sozlesmede tarama sunucu
            # kontrollu ve kullaniciya gorunmezdir; eski teknik UI yalniz
            # ic hata ayiklama icin acilir.
            "screening_enabled": bool(
                _settings.ENABLE_CORPUS_SCREENING
                and _settings.CORPUS_SCREENING_TECHNICAL_UI),
            "requires_approval": False,
        }
    workspace = db.get(BrandProfile, brand_profile_id)
    try:
        preflight = build_assignment_preflight(
            db, scoring_run, workspace, mode=effective_mode,
            relevance_coefficient=float(
                relevance_override
                if relevance_override is not None
                else (scoring_run.default_relevance_coefficient or 1.0)),
            settings=_settings)
    except PreflightError as exc:
        raise HTTPException(status_code=409,
                            detail={"code": exc.code,
                                    "message": exc.message})
    # Evren satırları ve bağlam metni yanıtta TAŞINMAZ (gereksiz yük +
    # prompt bağlamının API'den sızmaması)
    drop = ("universe_rows", "context", "identity_components")
    # `screening_enabled` HER İKİ dalda da döner: UI mod seçicisini bu
    # alana bakarak render eder ve yalnız off dalında dönerse tercih
    # zaten `shadow` olan koşularda seçici HİÇ görünmezdi (canlı UI
    # doğrulamasında yakalandı).
    return {"requires_approval": True,
            "screening_enabled": bool(
                _settings.ENABLE_CORPUS_SCREENING
                and _settings.CORPUS_SCREENING_TECHNICAL_UI),
            **{k: v for k, v in preflight.items() if k not in drop}}


@router.get("/runs/{run_id}/screening")
def get_run_screening_status(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Bu run'ın SON tarama işinin durumu/sonucu (UI progress + özet).

    Salt okunur; hiçbir şey başlatmaz. Tarama hiç çalışmadıysa
    `{"exists": false}` döner — UI o durumda hiçbir şey göstermez
    (bayrak kapalıyken davranış DEĞİŞMEZ).
    """
    from app.core.screening.counterfactual import counterfactual_summary
    from app.database.models import (
        ChannelAssignmentAttempt,
        CorpusScreeningJob,
    )

    verify_scoring_run(db, run_id, brand_profile_id)
    # Otorite: SON attempt'in BAĞLI OLDUĞU job. "Run'ın en yeni job'ı"
    # yanlış kaynaktır — reuse yolunda koşu eski (completed) job'ı
    # kullanır ve UI, kullanılmayan başarısız job'ı gösterirdi (canlı
    # koşuda görüldü: reuse başarılıyken ekranda "failed" yazıyordu).
    attempt = (db.query(ChannelAssignmentAttempt)
               .filter(ChannelAssignmentAttempt.scoring_run_id == run_id)
               .order_by(ChannelAssignmentAttempt.id.desc())
               .first())
    job = None
    if attempt is not None and attempt.screening_job_id:
        job = db.get(CorpusScreeningJob, attempt.screening_job_id)
    if job is None:
        job = (db.query(CorpusScreeningJob)
               .filter(CorpusScreeningJob.scoring_run_id == run_id)
               .order_by(CorpusScreeningJob.id.desc())
               .first())
        attempt = (db.query(ChannelAssignmentAttempt)
                   .filter(ChannelAssignmentAttempt.screening_job_id
                           == (job.id if job else None))
                   .first()) if job is not None else None
    if job is None:
        return {"exists": False, "scoring_run_id": run_id}
    from app.database.models import CorpusCandidateSelection

    applied_rows = (db.query(CorpusCandidateSelection)
                    .filter(CorpusCandidateSelection.screening_job_id
                            == job.id,
                            CorpusCandidateSelection.is_applied.is_(True))
                    .count())
    return {
        "exists": True,
        "scoring_run_id": run_id,
        "screening_job_id": job.id,
        "status": job.status,
        "error_code": job.error_code,
        "screening_mode": getattr(attempt, "screening_mode", None),
        # Gerçekleşen maliyet ledger'dan gelir (tahmin DEĞİL)
        "cost_usd": float(job.cost_usd) if job.cost_usd is not None else None,
        "provider_calls": job.actual_requests,
        "planned_requests": job.planned_requests,
        "ceiling_charges": job.ceiling_charges,
        "coverage_resolved": job.coverage_resolved,
        "universe_size": len((job.input_snapshot or {}).get("rows") or []),
        "unresolved": job.unresolved_count,
        "contract_violations": job.contract_violations,
        "applied_screening_channels": job.applied_screening_channels,
        # Shadow'da canlı havuz DEĞİŞMEZ: seçimler yalnız ölçümdür
        # Codex 20. tur #4: VARSAYIM degil OLCUM — gercekten uygulanmis
        # (is_applied=true) secim satiri var mi? Assistive fallback'te
        # carpan 3 olsa da satir yazilmamis olabilir.
        "applied_to_live_pool": applied_rows > 0,
        "applied_selection_rows": applied_rows,
        "applied_candidate_multiplier": int(
            getattr(attempt, "applied_candidate_multiplier", 1) or 1),
        # Ölçüm taramayı ÜRETEN attempt'e yazılıdır; reuse yolunda da
        # görünsün diye JOB üzerinden özetlenir
        "counterfactual": counterfactual_summary(db, job_id=job.id),
    }


@router.get("/runs/{run_id}/pools")
def get_channel_pools(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """Get all channel pools for a scoring run. Read: opsiyonel workspace."""
    scoring_run = verify_scoring_run(db, run_id, brand_profile_id)
    engine = ChannelEngine(db, ai)
    pools = engine.get_channel_pools(run_id)
    # Plan v13: bayatlık sinyali — UI bandı iki nedeni ayrı gösterir
    freshness = pool_freshness_for_run(db, scoring_run)
    return {**pools, **freshness.as_dict()}


@router.get("/runs/{run_id}/pools/{channel}/export.xlsx")
def export_channel_pool_xlsx(
    run_id: int,
    channel: str,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Tek kanalin final havuzunu zengin kolonlarla XLSX indirir (plan v4 §4).

    Veri kaynagi public read-model (`collect_channel_pool`) — pools API'siyle
    ayni satirlar. Bayat havuzdan "guncel" gorunumlu dosya verilmez:
    `require_fresh_channel_pool` 409 POLICY_STALE atar (plan v13 sozlesmesi).
    """
    import io

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from app.core.workspace import require_fresh_channel_pool
    from app.exporters.data_collector import collect_channel_pool
    from app.exporters.safe_text import safe_excel_text

    channel = channel.upper()
    if channel not in ["ADS", "SEO", "SOCIAL"]:
        raise HTTPException(status_code=400, detail="Invalid channel")

    scoring_run = verify_scoring_run(db, run_id, brand_profile_id)
    require_fresh_channel_pool(db, scoring_run)

    rows = collect_channel_pool(db, run_id, channel)
    if not rows:
        raise HTTPException(status_code=400, detail=f"{channel} havuzu boş — önce kanal ataması yapın")

    wb = Workbook()
    ws = wb.active
    ws.title = f"{channel} Havuzu"

    headers = [
        'Sıra', 'Keyword', 'Aylık Hacim', 'Kanal Skoru',
        'Relevance', 'Adjusted Skor', 'Niyet', 'Stratejik', 'Etiket',
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.auto_filter.ref = ws.dimensions

    for row in rows:
        channel_score = row.ads_score or row.seo_score or row.social_score
        ws.append([
            row.final_rank or 0,
            safe_excel_text(row.keyword),
            row.volume or 0,
            float(channel_score) if channel_score is not None else '',
            float(row.vector_similarity) if row.vector_similarity is not None else '',
            float(row.vector_adjusted_score) if row.vector_adjusted_score is not None else '',
            safe_excel_text(row.intent or ''),
            'Evet' if row.is_strategic else '',
            safe_excel_text(row.pool_label or ''),
        ])

    for i, width in enumerate([7, 40, 12, 12, 11, 13, 18, 10, 20], 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"digitus_{channel}_havuzu_run{run_id}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/runs/{run_id}/pools/{channel}")
def get_single_channel_pool(
    run_id: int,
    channel: str,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """Get pool for a specific channel."""
    if channel.upper() not in ["ADS", "SEO", "SOCIAL"]:
        raise HTTPException(status_code=400, detail="Invalid channel")

    scoring_run = verify_scoring_run(db, run_id, brand_profile_id)
    engine = ChannelEngine(db, ai)
    pools = engine.get_channel_pools(run_id)
    freshness = pool_freshness_for_run(db, scoring_run)

    return {
        "channel": channel.upper(),
        "keywords": pools.get("channels", {}).get(channel.upper(), []),
        **freshness.as_dict(),
    }


@router.delete("/runs/{run_id}/pools/{pool_id}")
def remove_channel_pool_item(
    run_id: int,
    pool_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Remove a keyword from one run/channel pool and compact final ranks."""
    scoring_run = verify_scoring_run(db, run_id, brand_profile_id)
    if scoring_run.status not in {"channel_assigned", "completed"}:
        raise HTTPException(
            status_code=409,
            detail="Havuz yalnızca kanal ataması tamamlanmış çalışmalarda düzenlenebilir.",
        )

    active_channel_task = get_active_channel_assignment_task(db, run_id)
    if active_channel_task:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Kanal ataması sürerken havuz düzenlenemez.",
                "task_id": active_channel_task.task_id,
            },
        )

    active_generation_task = get_active_generation_task(db, run_id)
    if active_generation_task:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "İçerik üretimi sürerken havuz düzenlenemez.",
                "task_id": active_generation_task.task_id,
            },
        )

    pool_item = (
        db.query(ChannelPool)
        .filter(ChannelPool.id == pool_id)
        .filter(ChannelPool.scoring_run_id == run_id)
        .first()
    )
    if not pool_item:
        raise HTTPException(status_code=404, detail="Havuz satırı bulunamadı.")

    channel = pool_item.channel
    db.delete(pool_item)
    db.flush()

    remaining = (
        db.query(ChannelPool)
        .filter(ChannelPool.scoring_run_id == run_id)
        .filter(ChannelPool.channel == channel)
        .order_by(ChannelPool.final_rank.asc(), ChannelPool.id.asc())
        .all()
    )
    for rank, item in enumerate(remaining, 1):
        item.final_rank = rank

    content_marked_stale = mark_run_content_stale(db, run_id)
    counts = {
        row.channel: row.count
        for row in (
            db.query(ChannelPool.channel, func.count(ChannelPool.id).label("count"))
            .filter(ChannelPool.scoring_run_id == run_id)
            .group_by(ChannelPool.channel)
            .all()
        )
    }

    return {
        "scoring_run_id": run_id,
        "channel": channel,
        "pool_counts": counts,
        "content_marked_stale": content_marked_stale,
    }
