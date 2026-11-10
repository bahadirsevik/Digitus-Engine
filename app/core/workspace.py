"""
Workspace doğrulama helper'ları (plan2 §P0 — ortak guard isimlendirme).

Standart kontrat (hepsi için aynı):
- brand_profile_id None ise HTTPException 400 ("required") — mutating=True'da.
- brand_profile_id var ama workspace yoksa/arşivse 404 ("not found").
- İlişkili obje (run/task/keyword/export) workspace ile eşleşmiyorsa 404
  (mevcudiyet sızıntısını önlemek için 403 değil).

Endpoint kodu sadece tek satır `obj = verify_X(...)` çağırarak işini bitirir.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core import markets
from app.database.models import (
    BrandProfile,
    ExportJob,
    ScoringRun,
    TaskResult,
    WorkspaceKeyword,
)


def verify_workspace(
    db: Session, brand_profile_id: int, lock: bool = False
) -> BrandProfile:
    """Workspace var mı + arşivlenmiş değil mi?

    lock=True: satır FOR SHARE tutulur (caller transaction'ı bitene dek);
    pazar değişimi (FOR UPDATE) ile ilk yazma yolları serileşir. GET yolları lock=False.

    Raises HTTPException 404 if not found or soft-deleted.
    """
    query = db.query(BrandProfile).filter(
        BrandProfile.id == brand_profile_id,
        BrandProfile.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update(read=True)
    workspace = query.first()

    if not workspace:
        raise HTTPException(
            status_code=404,
            detail="Marka çalışması bulunamadı veya arşivlenmiş",
        )

    return workspace


def market_change_blocker(db: Session, ws: BrandProfile) -> Optional[str]:
    """Pazar (hedef konum/dil) değişimini engelleyen neden; None → değişebilir."""
    from app.core.policy.guards import find_active_workspace_work

    if db.query(WorkspaceKeyword.id).filter(WorkspaceKeyword.brand_profile_id == ws.id).first():
        return "çalışmada kelime var"
    if db.query(ScoringRun.id).filter(ScoringRun.brand_profile_id == ws.id).first():
        return "çalışmada analiz var"
    if db.query(ExportJob.id).filter(ExportJob.brand_profile_id == ws.id).first():
        return "çalışmada dışa aktarım var"
    return find_active_workspace_work(db, ws.id)


def set_workspace_market(
    db: Session, ws: BrandProfile, geo_target_id: Optional[str], language_id: Optional[str]
) -> bool:
    """Pazarı değiştir; değiştiyse True. Commit ETMEZ.

    Girdiler kanonik (markets.validate_market_input çıktısı). Çağıran satırı
    FOR UPDATE tutmalı (kontrol ile yazma aynı transaction'da kalsın).
    """
    geo = markets._clean_geo(geo_target_id)
    lang = markets._clean_language(language_id)
    if (geo, lang) == (
        markets._clean_geo(ws.default_geo_target_id),
        markets._clean_language(ws.default_language_id),
    ):
        return False
    reason = market_change_blocker(db, ws)
    if reason:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MARKET_LOCKED",
                "message": "Bu çalışmada veri var; hedef konum/dil değiştirilemez. Yeni pazar çalışması açın.",
                "reason": reason,
            },
        )
    ws.default_geo_target_id = geo or None
    ws.default_language_id = lang or None
    return True


def stamp_rows_market(rows: list, ws: BrandProfile) -> list:
    """Kelime satırlarına çalışma pazarını damgala (yerinde); DB yazımından ÖNCE çağrılır.

    Açık geo/dil farklıysa isteğin TAMAMI 409 (hiçbir satır yazılmaz); boşsa çalışma değeri.
    """
    geo, lang = markets.workspace_market(ws)
    bad = 0
    first_bad = None
    for row in rows:
        row_geo = markets._clean_geo(row.get("geo_target_id")) or geo
        row_lang = markets._clean_language(row.get("language_id")) or lang
        if (row_geo, row_lang) != (geo, lang):
            bad += 1
            first_bad = first_bad or (row_geo, row_lang)
    if bad:
        a_geo, a_lang = first_bad
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MARKET_MISMATCH",
                "message": (
                    f"Bu liste {markets.location_label(a_geo)} · {markets.language_name(a_lang) or a_lang} "
                    f"ile ölçüldü; çalışma pazarı {markets.location_label(geo)} · "
                    f"{markets.language_name(lang) or lang}. Listeyi yeniden çekin."
                ),
                "rows": bad,
            },
        )
    for row in rows:
        row["geo_target_id"] = geo
        row["language_id"] = lang
    return rows


def verify_scoring_run(
    db: Session,
    run_id: int,
    brand_profile_id: int,
) -> ScoringRun:
    """Scoring run var mı + verilen workspace'e ait mi?

    brand_profile_id her zaman zorunlu (read veya mutating fark etmez).
    mutating parametresi artık yalnızca dökümantasyon amaçlı; davranışı değiştirmez.

    Raises:
        HTTPException 400: brand_profile_id None.
        HTTPException 404: run bulunamadı veya workspace ile eşleşmiyor.
    """
    if brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="Önce bir marka çalışması seçin.",
        )

    verify_workspace(db, brand_profile_id)

    run = db.query(ScoringRun).filter(ScoringRun.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail="Analiz bulunamadı.",
        )

    if run.brand_profile_id != brand_profile_id:
        raise HTTPException(
            status_code=404,
            detail="Analiz bu marka çalışmasında bulunamadı.",
        )

    return run


def pool_freshness_for_run(db: Session, run: ScoringRun):
    """Run'ın havuz bayatlık durumu — merkezi hesap (plan v13)."""
    from app.core.policy.freshness import compute_pool_freshness

    workspace = None
    if run.brand_profile_id:
        workspace = db.query(BrandProfile).filter(
            BrandProfile.id == run.brand_profile_id
        ).first()
    return compute_pool_freshness(run, workspace)


def require_fresh_channel_pool(db: Session, run: ScoringRun):
    """Merkezi POLICY_STALE guard'ı (plan v13): generation dispatch / grup
    regenerate / export, işin BAŞINDA bunu çağırır — satır bazlı is_stale
    filtrelerine tek başına güvenilmez (legacy content_output_id=NULL
    kayıtları kapsam dışı kalabiliyordu).

    Raises HTTPException 409 (code=POLICY_STALE) when the pool is stale.
    Returns PoolFreshness.
    """
    freshness = pool_freshness_for_run(db, run)
    if freshness.channel_pool_stale:
        reasons = []
        if freshness.policy_stale:
            reasons.append("politika değişti")
        if freshness.relevance_stale:
            reasons.append("marka profili değişti")
        if freshness.strategy_stale:
            reasons.append("kanal stratejisi değişti")
        if freshness.assignment_in_progress:
            reasons.append("kanal ataması sürüyor")
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_STALE",
                "message": (
                    "Kanal havuzu güncel değil: " + ", ".join(reasons)
                    + ". Kanal atamasını yenileyin."
                ),
                **freshness.as_dict(),
            },
        )
    return freshness


def verify_task_in_workspace(
    db: Session,
    task_id: str,
    brand_profile_id: int,
) -> TaskResult:
    """Task var mı + ait olduğu run verilen workspace'e mi ait?

    Plan2 §P0/C4: tasks endpoint'leri read olsa da workspace zorunlu —
    cross-workspace task leak kritik.

    Raises:
        HTTPException 400: brand_profile_id None.
        HTTPException 404: task yoksa veya run workspace ile eşleşmiyorsa.
    """
    if brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="Önce bir marka çalışması seçin.",
        )

    verify_workspace(db, brand_profile_id)

    row = (
        db.query(TaskResult, ScoringRun)
        .join(ScoringRun, TaskResult.scoring_run_id == ScoringRun.id)
        .filter(TaskResult.task_id == task_id)
        .filter(ScoringRun.brand_profile_id == brand_profile_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="İşlem bu marka çalışmasında bulunamadı.")
    return row[0]


def verify_export_in_workspace(
    db: Session,
    export_job_id: str,
    brand_profile_id: int,
) -> ExportJob:
    """ExportJob var mı + verilen workspace'e ait mi?

    Plan2 §P2: in-memory dict yerini DB-backed ExportJob aldı.

    Raises:
        HTTPException 400: brand_profile_id None.
        HTTPException 404: job yoksa veya workspace ile eşleşmiyorsa.
    """
    if brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="Önce bir marka çalışması seçin.",
        )

    verify_workspace(db, brand_profile_id)

    job = (
        db.query(ExportJob)
        .filter(
            ExportJob.id == export_job_id,
            ExportJob.brand_profile_id == brand_profile_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Dışa aktarım bu marka çalışmasında bulunamadı.")
    return job
