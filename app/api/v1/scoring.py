"""
Scoring endpoints.

Plan2 §P0/C2 — workspace-aware. Mutating endpoint'lerde brand_profile_id
zorunlu, read endpoint'lerde geçiş döneminde opsiyonel + warning.
"""
from typing import Any, Dict, List, Literal, Optional
import io

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, func, nullslast
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.core.scoring.score_engine import ScoreEngine
from app.core.trial_authorization import (
    authorization_record, lock_workspace, matching_combination,
    record_run_audit, require_create_allowed, require_run_allowed,
    trial_setup,
)
from app.core.trial_snapshot import build_execution_snapshot, snapshot_sha256
from app.core.engine_version_gate import require_non_legacy_run
from app.core.workspace import verify_scoring_run, verify_workspace
from app.database.models import (
    EngineSelection, EngineStageResult, Keyword, KeywordScore, ScoringRun,
)
from app.schemas.scoring import (
    ScoringRunCreate, ScoringRunResponse, ScoringRunStatus,
    ScoringResultsResponse, KeywordScoreResponse,
)


router = APIRouter()


def _v3_numeric(value: Any) -> Optional[float]:
    """JSON skorunu API siralama/gosteriminde guvenle sayiya cevir."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _v3_selection_score(selection: Optional[EngineSelection]) -> Optional[float]:
    if selection is None:
        return None
    scores = selection.scores if isinstance(selection.scores, dict) else {}
    keys = {
        "ADS": ("Selection",),
        "SEO": ("final",),
        "SOCIAL": ("social_score",),
    }.get(selection.channel, ())
    for key in keys:
        value = _v3_numeric(scores.get(key))
        if value is not None:
            return value
    return None


def _v3_family_names(db: Session, run_id: int) -> Dict[str, str]:
    """Family V2 sozlugundeki id -> okunabilir ad eslemesi.

    A1 ana sozlugu ve A2B'de gercekten kabul edilen ek aileler yeterlidir.
    Tekil fallback aileleri sozlukte olmadigi icin acik bir etiket alir.
    """
    rows = (
        db.query(EngineStageResult)
        .filter(
            EngineStageResult.scoring_run_id == run_id,
            EngineStageResult.stage.in_(("family_a1", "family_a2b")),
        )
        .all()
    )
    result: Dict[str, str] = {}
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        families = list(payload.get("families") or [])
        families.extend(payload.get("accepted_families") or [])
        for family in families:
            if not isinstance(family, dict) or not family.get("family_id"):
                continue
            result[str(family["family_id"])] = str(
                family.get("family_name") or family["family_id"]
            )
    return result


def _v3_score_rows(db: Session, run_id: int) -> List[Dict[str, Any]]:
    """V3 snapshot evrenini gercek EngineSelection sonuclariyla birlestir."""
    snapshots = (
        db.query(KeywordScore, Keyword)
        .join(Keyword, KeywordScore.keyword_id == Keyword.id)
        .filter(KeywordScore.scoring_run_id == run_id)
        .all()
    )
    selections = (
        db.query(EngineSelection)
        .filter(EngineSelection.scoring_run_id == run_id)
        .all()
    )
    by_keyword: Dict[int, Dict[str, EngineSelection]] = {}
    for selection in selections:
        by_keyword.setdefault(int(selection.keyword_id), {})[
            str(selection.channel).upper()
        ] = selection

    family_names = _v3_family_names(db, run_id)
    output: List[Dict[str, Any]] = []
    for snapshot, keyword in snapshots:
        channel_map = by_keyword.get(int(keyword.id), {})
        ads = channel_map.get("ADS")
        seo = channel_map.get("SEO")
        social = channel_map.get("SOCIAL")
        family_id = next(
            (
                str(item.family_id)
                for item in (ads, seo, social)
                if item is not None and item.family_id
            ),
            None,
        )
        family_name = family_names.get(family_id) if family_id else None
        if family_id and family_name is None and family_id.startswith("single:"):
            family_name = "Tekil aile"

        output.append({
            "snapshot": snapshot,
            "keyword": keyword,
            "ads_score": _v3_selection_score(ads),
            "seo_score": _v3_selection_score(seo),
            "social_score": _v3_selection_score(social),
            "ads_rank": ads.algorithm_rank if ads else None,
            "seo_rank": seo.algorithm_rank if seo else None,
            "social_rank": social.algorithm_rank if social else None,
            "ads_final_rank": ads.final_rank if ads else None,
            "seo_final_rank": seo.final_rank if seo else None,
            "social_final_rank": social.final_rank if social else None,
            "ads_exclude_reason": ads.exclude_reason if ads else None,
            "seo_exclude_reason": seo.exclude_reason if seo else None,
            "social_exclude_reason": social.exclude_reason if social else None,
            "ads_pool_class": ads.pool_class if ads else None,
            "seo_pool_class": seo.pool_class if seo else None,
            "social_pool_class": social.pool_class if social else None,
            "social_priority": social.priority if social else None,
            "family_id": family_id,
            "family_name": family_name,
        })
    return output


def _sort_v3_rows(
    rows: List[Dict[str, Any]], sort_by: str, sort_dir: str,
) -> List[Dict[str, Any]]:
    def sort_value(row: Dict[str, Any]):
        value = row.get(sort_by)
        if value is None:
            return (1, 0, int(row["keyword"].id))
        number = float(value)
        directed = -number if sort_dir == "desc" else number
        return (0, directed, int(row["keyword"].id))

    return sorted(rows, key=sort_value)


@router.get("/capabilities", response_model=dict)
def get_scoring_capabilities():
    """Codex Faz E #2: UI'nin deney bayrağını ÖNCEDEN bilmesi için —
    kullanıcı 409 duvarına çarpmadan v2.1 seçeneği gizlenir/kapatılır.
    Read-only, workspace scope'u gerektirmez."""
    from app.config import settings as _settings

    return {
        "v21_experiment_enabled": bool(_settings.ENABLE_V21_EXPERIMENT),
        # Motor v3 (plan_algoritma_entegrasyonu.md K2): UI'nin v3 seçeneğini
        # 409 duvarına çarpmadan gizleyebilmesi için — aynı desen.
        "engine_v3_enabled": bool(_settings.ENABLE_ENGINE_V3),
    }


# Faz 6a (plan_v3_lokasyon_filtresi.md §5.7): lokasyon önizleme token
# alanlarıyla genişletilmiş istek tipi. Ayrı modülde tanımlı — bu dosyanın
# `app.schemas.scoring` importu başka bir geliştiricinin commit edilmemiş
# hunk'ını taşıyor, oraya dokunulmaz (bkz. app/schemas/location_gate.py).
from app.schemas.location_gate import ScoringRunCreateWithLocationGate


@router.post("/runs", response_model=ScoringRunResponse, status_code=201)
def create_scoring_run(
    run_data: ScoringRunCreateWithLocationGate,
    db: Session = Depends(get_db),
):
    """Create a new scoring run.

    Mutating: brand_profile_id zorunlu (request body içinden gelir).
    """
    if run_data.brand_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="brand_profile_id is required",
        )

    workspace = verify_workspace(db, run_data.brand_profile_id)

    # Ürün kararı (ADR-004): Yeni analizler yalnız v3 ile oluşturulabilir.
    # v2 ve v2_1 emekliye ayrılmıştır; istekte açıkça gönderilirse tipli 400 döner.
    # Bilinmeyen sürümler (örn. v4) semantik olarak 422 INVALID_ALGORITHM_VERSION döner.
    # Bu kontrol trial authorization kapısından ÖNCE çalışır; böylece yetki durumu
    # legacy motorun emekli olduğu gerçeğini maskelemez.
    if run_data.algorithm_version in ("v2", "v2_1"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "LEGACY_ENGINE_RETIRED",
                "message": (
                    f"'{run_data.algorithm_version}' motoru emekliye ayrılmıştır. "
                    "Yeni analizler yalnızca 'v3' algoritması ile oluşturulabilir."
                ),
            },
        )
    if run_data.algorithm_version != "v3":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_ALGORITHM_VERSION",
                "message": (
                    f"Geçersiz algoritma sürümü: '{run_data.algorithm_version}'. "
                    "Yeni analizler yalnızca 'v3' algoritması ile oluşturulabilir."
                ),
            },
        )

    # ÜCRET KAPISI (deneme workspace'i): satır SELECT ... FOR UPDATE ile
    # kilitlenir; izinli kanal/algoritma birleşimi, başarılı koşu kotası ve
    # delinemez toplam maliyet sınırı doğrulanır. KAPASİTELER SERBESTTİR —
    # kullanıcı UI'dan seçer, burada yalnız DENETİME yazılır. Koşu açmak
    # hak TÜKETMEZ (kota yalnız BAŞARILI koşuları sayar).
    is_trial = trial_setup(workspace) is not None
    if is_trial:
        workspace = lock_workspace(db, workspace.id)
        require_create_allowed(db, workspace, run_data)
    if workspace.status != "confirmed":
        raise HTTPException(
            status_code=400,
            detail="Skorlama için önce marka profili analiz edilip onaylanmalı.",
        )

    # Motor v3 kapısı (plan_algoritma_entegrasyonu.md K2/K10)
    from app.config import settings as _settings

    if not _settings.ENABLE_ENGINE_V3:
        raise HTTPException(status_code=409, detail={
            "code": "ENGINE_V3_DISABLED",
            "message": "Motor v3 deney bayrağı kapalı "
                       "(ENABLE_ENGINE_V3) — v3 koşusu oluşturulamaz.",
        })
    # K14 — v3 tek koşu sözleşmesi: kullanıcı execute'tan sonra ikinci
    # bir işlem yapmaz, motor kendiliğinden başlar. auto_assign_channels
    # false ile oluşturulan bir v3 run'ı motorun asla tetiklenmeyeceği
    # ölü bir run olur — sessizce kabul edilmez, tipli 409.
    if not run_data.auto_assign_channels:
        raise HTTPException(status_code=409, detail={
            "code": "ENGINE_V3_SINGLE_RUN_REQUIRED",
            "message": "v3 tek koşu sözleşmesiyle çalışır; "
                       "auto_assign_channels=true zorunludur — false "
                       "ile oluşturulan bir v3 run'ında motor hiçbir "
                       "zaman başlamaz.",
        })


    # LOKASYON KAPISI (Faz 6a, plan_v3_lokasyon_filtresi.md §5.7): workspace'in
    # KAYITLI lokasyon politikası aktif bir moddaysa (exclude_all/focus_only),
    # run yalnız güncel evren + güncel kayıtlı policy'ye karşı üretilmiş TAZE
    # bir önizleme token'ıyla oluşturulabilir. `mode=none` VE hiçbir token
    # yoksa TAMAMEN geriye uyumludur — hiçbir yeni kontrol çalışmaz.
    from app.core.policy import location_policy as _location_policy

    _saved_mode, _, _ = _location_policy.read_settings(workspace.profile_data)
    _location_gate_payload: Optional[Dict[str, Any]] = None
    # QA bulgusu — preview->create yarışı: kayıtlı mode `none`YKEN bile bir
    # token gönderilmişse SESSİZCE yok sayılamaz. Aksi halde: kullanıcı
    # `exclude_all` iken önizleme alır (geçerli token), policy başka bir
    # sekmede `none`'a düşürülür, create eski token'la çağrılır — dış koşul
    # yalnız CANLI mode'a bakarsa bu adım atlanır ve filtresiz bir run
    # SESSİZCE oluşur (execute'un doğrulayacağı hiçbir gate de mühürlenmez).
    #
    # DOGRULUK DUZELTMESI (QA): TRUTHY kontrol degil VARLIK kontrolu.
    # `location_preview_is_saved_policy=False` acikca gonderilmisse (tam
    # olarak yakalanmak istenen draft-kaynakli durum) ya da bos string bir
    # fingerprint gelmisse eski `any((...))` bunlari YOK sayardi. Sozlesme
    # "alan GONDERILDI mi" sorusudur, degeri dogru mu degil (o kontrol asagida).
    _has_location_token = any(
        value is not None
        for value in (
            run_data.location_universe_fingerprint,
            run_data.location_policy_fingerprint,
            run_data.location_preview_is_saved_policy,
        )
    )
    if _saved_mode == _location_policy.MODE_NONE and _has_location_token:
        raise HTTPException(status_code=409, detail={
            "code": "LOCATION_PREVIEW_STALE",
            "message": (
                "Gönderilen lokasyon önizleme token'ı artık kayıtlı "
                "policy'yle uyuşmuyor (workspace şu an mode='none') — "
                "token başka bir policy'ye karşı alınmış olabilir. "
                "Önizlemeyi yenileyip tekrar deneyin."
            ),
        })
    if _saved_mode != _location_policy.MODE_NONE:
        # Sıra ÖNEMLİ: önce "hiç token yok" (REQUIRED, nötr — istemci hiç
        # önizleme çağırmamış olabilir), sonra "token var ama draft kaynaklı"
        # (DRAFT_REJECTED, daha spesifik) kontrol edilir. Aksi halde alan
        # gönderilmediğinde (varsayılan `None`) yanlışlıkla DRAFT_REJECTED
        # dönerdi — mesaj "draft gönderdiniz" derdi ama istemci hiçbir şey
        # göndermemişti.
        if (not run_data.location_universe_fingerprint
                or not run_data.location_policy_fingerprint):
            raise HTTPException(status_code=400, detail={
                "code": "LOCATION_PREVIEW_REQUIRED",
                "message": (
                    f"Workspace lokasyon filtresi etkin (mode="
                    f"{_saved_mode!r}) — run oluşturmadan önce "
                    "/policy/location-preview ile taze bir önizleme "
                    "alınmalı ve token'ı bu istekte taşınmalıdır."
                ),
            })
        # TOKEN GÜVEN KURALI: draft (kaydedilmemiş) değerlerden üretilen bir
        # önizleme token'ı run yetkilendirmesinde KULLANILAMAZ — değerler
        # tesadüfen kayıtlı policy ile aynı olsa bile (bkz.
        # app/schemas/location_gate.py, app/api/v1/brand_profile.py
        # ::preview_location_filter).
        if run_data.location_preview_is_saved_policy is not True:
            raise HTTPException(status_code=400, detail={
                "code": "LOCATION_PREVIEW_DRAFT_REJECTED",
                "message": (
                    "Gönderilen lokasyon önizleme token'ı draft "
                    "(kaydedilmemiş) değerlerden üretilmiş — run "
                    "yetkilendirmesinde kullanılamaz. Analiz başlatma "
                    "ekranından, kayıtlı policy'ye karşı taze bir önizleme "
                    "alın (POST /brand-profile/workspaces/{id}/policy/"
                    "location-preview, draft alan göndermeden)."
                ),
            })
        from app.core.engine.context import (
            EngineInputError as _EngineInputError,
            build_universe_rows as _build_universe_rows,
            universe_fingerprint as _universe_fingerprint,
        )

        # Önizlemeyle AYNI yardımcı + AYNI seçim alanları: sayılan satırlar
        # motorun GERÇEKTEN kullanacağı satırlarla birebir aynı olsun.
        # Persist edilmeyen ScoringRun — session'a asla eklenmez/flush edilmez.
        _transient_run = ScoringRun(
            brand_profile_id=run_data.brand_profile_id,
            keyword_selection_mode=run_data.keyword_selection_mode,
            keyword_limit=run_data.keyword_limit,
            selected_keyword_ids=run_data.selected_keyword_ids,
            keyword_source_filter=run_data.keyword_source_filter,
        )
        try:
            _universe = _build_universe_rows(db, _transient_run)
        except _EngineInputError as exc:
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_KEYWORD_SELECTION",
                "message": str(exc),
            })
        _expected_policy = _location_policy.policy_snapshot(workspace.profile_data)
        _current_universe_fp = _universe_fingerprint(_universe)
        if (run_data.location_universe_fingerprint != _current_universe_fp
                or run_data.location_policy_fingerprint
                    != _expected_policy["enforcement_fingerprint"]):
            raise HTTPException(status_code=409, detail={
                "code": "LOCATION_PREVIEW_STALE",
                "message": (
                    "Lokasyon önizlemesi güncel değil (keyword seçimi veya "
                    "kayıtlı policy değişmiş) — önizlemeyi yenileyip tekrar "
                    "deneyin."
                ),
            })
        _location_gate_payload = {
            "mode": _saved_mode,
            "universe_fingerprint": run_data.location_universe_fingerprint,
            "policy_fingerprint": run_data.location_policy_fingerprint,
        }

    engine = ScoreEngine(db)
    scoring_run = engine.create_scoring_run(
        ads_capacity=run_data.ads_capacity,
        seo_capacity=run_data.seo_capacity,
        social_capacity=run_data.social_capacity,
        default_relevance_coefficient=run_data.default_relevance_coefficient,
        run_name=run_data.run_name,
        company_url=run_data.company_url,
        competitor_urls=run_data.competitor_urls,
        keyword_source_filter=run_data.keyword_source_filter,
        brand_profile_id=run_data.brand_profile_id,
        enable_ads=run_data.enable_ads,
        enable_seo=run_data.enable_seo,
        enable_social=run_data.enable_social,
        keyword_selection_mode=run_data.keyword_selection_mode,
        keyword_limit=run_data.keyword_limit,
        selected_keyword_ids=run_data.selected_keyword_ids,
        skip_relevance=run_data.skip_relevance,
        auto_assign_channels=run_data.auto_assign_channels,
        algorithm_version=run_data.algorithm_version,
        # Deneme workspace'inde INSERT + denetim kaydı TEK transaction:
        # commit, denetim yazıldıktan sonra yapılır.
        commit=not is_trial,
    )
    if _location_gate_payload is not None:
        # EXECUTE'un create->execute yarışına karşı yeniden doğrulayabilmesi
        # için token BURADA muhurlenir (bkz. execute_scoring). `is_trial`
        # dalı execution_manifest'i spread ile KORUYARAK yeniden atar; bu
        # atama ondan ÖNCE olmalı ki kaybolmasın.
        scoring_run.execution_manifest = {
            **(scoring_run.execution_manifest or {}),
            "location_preview_gate": _location_gate_payload,
        }
        if not is_trial:
            db.commit()
            db.refresh(scoring_run)
    if is_trial:
        # Çalıştırma bağlamı snapshot'ı: YALNIZ tekrarlanabilirlik ve
        # denetim için. Onaylanmış bir kapasiteyle EŞLEŞTİRİLMEZ.
        snapshot = build_execution_snapshot(db, workspace, scoring_run)
        combination = matching_combination(workspace, run_data) or {}
        entry = record_run_audit(workspace, scoring_run,
                                 combination=combination, payload=run_data,
                                 snapshot_sha256=snapshot_sha256(snapshot))
        scoring_run.execution_manifest = {
            **(scoring_run.execution_manifest or {}),
            "trial_authorization": {
                "authorization_id": authorization_record(workspace).get(
                    "authorization_id"),
                "combination_id": entry["combination_id"],
                "combination": entry["combination"],
                "capacities": entry["capacities"],
                "execution_snapshot_sha256": entry[
                    "execution_snapshot_sha256"],
            },
            "trial_execution_snapshot": snapshot,
        }
        db.commit()
        db.refresh(scoring_run)
    return ScoringRunResponse.model_validate(scoring_run)


@router.post("/runs/{run_id}/execute", response_model=dict)
def execute_scoring(
    run_id: int,
    background_tasks: BackgroundTasks,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Execute scoring for all keywords in the run.

    Mutating: brand_profile_id zorunlu.
    """
    from app.core.scoring.state_machine import transition_atomic
    from app.tasks.scoring_tasks import _run_relevance_computation

    run = verify_scoring_run(db, run_id, brand_profile_id)
    # Eski motor (v2/v2_1) run'ı salt-okunurdur: skorlama, relevance ve
    # otomatik kanal ataması zinciri HİÇ başlamaz (tipli 409).
    require_non_legacy_run(run)

    # Motor v3 (plan_algoritma_entegrasyonu.md K14 / Faz 7): v3 execute AI çağırmaz;
    # State machine: pending -> scoring -> snapshot freeze -> scored.
    # pending -> scoring geçişi CAS ile korunur; snapshot düşerse run failed olur.
    if (getattr(run, "algorithm_version", "v2") or "v2") == "v3":
        _workspace_for_run = verify_workspace(db, brand_profile_id)
        require_run_allowed(db, _workspace_for_run, run, stage="execute")
        from app.core.engine.context import (
            EngineInputError, freeze_universe_snapshot,
            universe_fingerprint as _universe_fingerprint,
        )
        from app.core.policy import location_policy as _location_policy
        from app.core.scoring.state_machine import transition, transition_atomic
        from app.core.channel.assignment_dispatcher import (
            enqueue_channel_assignment,
            ChannelAssignmentPreconditionError,
        )

        # 1. pending -> scoring CAS geçişi (mükerrer/yarışan execute tipli 409)
        if not transition_atomic(db, run, target="scoring", from_status="pending"):
            fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
            current_status = fresh.status if fresh else "unknown"
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_NOT_PENDING",
                    "message": f"Run {run.id} durumu 'pending' değil ('{current_status}') — mükerrer veya yarışan execute çağrısı reddedildi.",
                },
            )

        # 2. Snapshot üretimi (hata halinde run -> failed)
        try:
            _frozen_universe = freeze_universe_snapshot(db, run)
        except Exception as exc:
            db.rollback()
            fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
            if fresh and fresh.status == "scoring":
                try:
                    transition(db, fresh, target="failed")
                except Exception:
                    pass
            if isinstance(exc, EngineInputError):
                raise HTTPException(status_code=400, detail=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"Snapshot oluşturulamadı: {exc}",
            )

        # 2b. LOKASYON KAPISI (Faz 6a, plan §5.7 — create->execute yarışına
        # karşı): evren DONDURULDUKTAN HEMEN SONRA, herhangi bir task/AI
        # dispatch'inden ÖNCE, create'te muhurlenen token yeniden doğrulanır.
        # Uyumsuzluk -> run FAILED (pending BIRAKILMAZ, hiçbir Celery task
        # kuyruğa GİRMEZ).
        #
        # DIŞ KOŞUL (QA bulgusu — downgrade deliği): yalnız "canlı mode
        # aktif mi" diye bakmak YETMEZ. `active -> none` senaryosunda
        # (create sırasında exclude_all/focus_only ile token mühürlenmiş,
        # execute'tan önce profil `none`'a düşürülmüş) canlı mode `none`
        # olduğu için kontrol tamamen ATLANIRDI — token'a karşı hiç
        # doğrulama yapılmadan run'a izin verilirdi. Bu yüzden kapı, canlı
        # mode aktifken VEYA run bir token'la mühürlenmişken çalışır; ikisi
        # de yoksa (gerçek `none -> none`) hâlâ TAMAMEN atlanır — geriye
        # uyumluluk bozulmaz.
        _saved_mode, _, _ = _location_policy.read_settings(
            _workspace_for_run.profile_data)
        _gate = (run.execution_manifest or {}).get("location_preview_gate")
        _must_validate_location = (
            _saved_mode != _location_policy.MODE_NONE or _gate is not None
        )
        if _must_validate_location:
            _location_ok = False
            if isinstance(_gate, dict) and _gate.get("mode") == _saved_mode:
                _expected_policy = _location_policy.policy_snapshot(
                    _workspace_for_run.profile_data)
                _fresh_universe_fp = _universe_fingerprint(_frozen_universe)
                _location_ok = (
                    _gate.get("universe_fingerprint") == _fresh_universe_fp
                    and _gate.get("policy_fingerprint")
                        == _expected_policy["enforcement_fingerprint"]
                )
            if not _location_ok:
                db.rollback()
                fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
                if fresh and fresh.status == "scoring":
                    try:
                        transition(db, fresh, target="failed")
                    except Exception:
                        pass
                raise HTTPException(status_code=409, detail={
                    "code": "LOCATION_PREVIEW_STALE",
                    "message": (
                        "Lokasyon önizlemesi run oluşturulduktan sonra "
                        "bayatladı (keyword seçimi/metrikleri veya kayıtlı "
                        "policy değişti) — run başarısız oldu; yeni bir "
                        "önizlemeyle yeniden deneyin."
                    ),
                })

        # 3. scoring -> scored CAS geçişi
        fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
        if not transition_atomic(db, fresh, target="scored", from_status="scoring"):
            fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_TRANSITION_FAILED",
                    "message": f"Run {run.id} scoring -> scored geçişi yapılamadı (mevcut durum: '{fresh.status if fresh else 'unknown'}').",
                },
            )
        fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()

        result = {
            "scoring_run_id": run.id,
            "status": fresh.status,
            "algorithm_version": "v3",
        }

        # 4. v3 tek koşu sözleşmesi (K14): auto_assign_channels zorunludur
        if fresh and fresh.auto_assign_channels:
            try:
                assignment = enqueue_channel_assignment(
                    db,
                    fresh,
                    relevance_coefficient=float(fresh.default_relevance_coefficient or 1.0),
                    from_status="scored",
                )
                fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
                result["channel_assignment_task_id"] = assignment.get("task_id")
                result["status"] = fresh.status if fresh else "channel_assigning"
            except ChannelAssignmentPreconditionError as err:
                raise HTTPException(
                    status_code=409,
                    detail={"code": err.code, "message": err.message},
                )
        return result

    # ÜCRET KAPISI: sağlayıcı çağrısından ÖNCE — izinli birleşim, başarılı
    # koşu kotası ve DELİNEMEZ toplam maliyet sınırı yeniden ölçülür.
    require_run_allowed(db, verify_workspace(db, brand_profile_id), run,
                        stage="execute")

    engine = ScoreEngine(db)
    try:
        result = engine.run_scoring(run.id)

        # Path B: auto-trigger relevance if scoring completed successfully.
        if result.get("should_compute_relevance"):
            fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
            if fresh and transition_atomic(
                db, fresh, target="relevance_computing", from_status="scored"
            ):
                background_tasks.add_task(
                    _run_relevance_computation, scoring_run_id=run.id
                )
        else:
            fresh = db.query(ScoringRun).filter(ScoringRun.id == run.id).first()
            if fresh and fresh.auto_assign_channels:
                from app.core.channel.assignment_dispatcher import enqueue_channel_assignment

                assignment = enqueue_channel_assignment(
                    db,
                    fresh,
                    relevance_coefficient=float(fresh.default_relevance_coefficient),
                    from_status="scored",
                )
                result["channel_assignment_task_id"] = assignment.get("task_id")

        return result
    except HTTPException:
        raise
    except ValueError as e:
        # v2.1 Faz C (Codex #5): tipli dispatch ön-koşulu 404 jeneriğine
        # KARIŞMAZ — 409 {code, message} sözleşmesiyle döner
        from app.core.channel.assignment_dispatcher import (
            ChannelAssignmentPreconditionError,
        )
        if isinstance(e, ChannelAssignmentPreconditionError):
            raise HTTPException(
                status_code=409,
                detail={"code": e.code, "message": e.message},
            )
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs", response_model=List[ScoringRunStatus])
def list_scoring_runs(
    skip: int = 0,
    limit: int = 20,
    brand_profile_id: int = Query(..., description="Workspace scope filter"),
    db: Session = Depends(get_db),
):
    """List scoring runs.

    Read: brand_profile_id zorunlu. Global liste workspace sızıntısı yaratır.
    """
    verify_workspace(db, brand_profile_id)
    query = db.query(ScoringRun).filter(ScoringRun.brand_profile_id == brand_profile_id)
    runs = query.order_by(ScoringRun.created_at.desc()).offset(skip).limit(limit).all()
    return [ScoringRunStatus.model_validate(run) for run in runs]


@router.get("/runs/{run_id}", response_model=ScoringRunStatus)
def get_scoring_run(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get details of a specific scoring run."""
    run = verify_scoring_run(db, run_id, brand_profile_id)
    return ScoringRunStatus.model_validate(run)


@router.delete("/runs/{run_id}", status_code=204)
def delete_scoring_run(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace owning the run"),
    db: Session = Depends(get_db),
):
    """Delete a scoring run. Mutating: brand_profile_id zorunlu."""
    from app.database import crud

    verify_scoring_run(db, run_id, brand_profile_id)
    success = crud.delete_scoring_run(db, run_id)
    if not success:
        # Çok rare race: verify sonrası başkası silmiş.
        raise HTTPException(status_code=404, detail="scoring run not found")
    return None


@router.get("/runs/{run_id}/scores", response_model=ScoringResultsResponse)
def get_scoring_results(
    run_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: Literal[
        "ads_score",
        "seo_score",
        "social_score",
        "ads_rank",
        "seo_rank",
        "social_rank",
    ] = "ads_score",
    sort_dir: Literal["asc", "desc"] = "desc",
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get scored keywords for a run."""
    run = verify_scoring_run(db, run_id, brand_profile_id)
    is_v3 = (run.algorithm_version or "").strip() == "v3"
    v3_rows: List[Dict[str, Any]] = []
    scores = []
    if is_v3:
        all_v3_rows = _sort_v3_rows(_v3_score_rows(db, run_id), sort_by, sort_dir)
        total_scored = len(all_v3_rows)
        v3_rows = all_v3_rows[offset:offset + limit]
    else:
        sort_columns = {
            "ads_score": KeywordScore.ads_score,
            "seo_score": KeywordScore.seo_score,
            "social_score": KeywordScore.social_score,
            "ads_rank": KeywordScore.ads_rank,
            "seo_rank": KeywordScore.seo_rank,
            "social_rank": KeywordScore.social_rank,
        }
        sort_col = sort_columns[sort_by]
        primary_order = desc(sort_col) if sort_dir == "desc" else asc(sort_col)
        base_query = (
            db.query(KeywordScore, Keyword)
            .join(Keyword, KeywordScore.keyword_id == Keyword.id)
            .filter(KeywordScore.scoring_run_id == run_id)
        )
        total_scored = (
            db.query(func.count(KeywordScore.id))
            .filter(KeywordScore.scoring_run_id == run_id)
            .scalar()
            or 0
        )
        scores = (
            base_query
            .order_by(nullslast(primary_order), KeywordScore.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    # Skor ekranı rozeti (plan A): kullanıcı "coin/fintables neden listede?"
    # diye gözle taramasın — kelime skorlanır AMA kanala giremeyecekse
    # etiketlenir. Okuma anında deterministik hesap (sayfa başı ~100 kelime).
    from app.core.policy.competitor_policy import (
        approved_competitor_terms, match_term,
    )
    from app.core.policy.topic_policy import approved_topic_terms
    from app.core.channel.pre_filters.seo_prefilter import SeoPreFilter

    from app.core.policy.competitor_policy import (
        POLICY_BLOCK, competitor_policy_for,
    )

    profile = run.brand_profile_workspace
    comp_terms = approved_competitor_terms(profile) if profile else []
    topic_terms = approved_topic_terms(profile) if profile else []
    comp_policy = competitor_policy_for(profile) if profile else {}
    comp_channels = [
        ch.upper() for ch, mode in comp_policy.items() if mode == POLICY_BLOCK
    ]

    def _exclusion_badges(kw_text: str):
        """Birden fazla neden birlikte döner; her neden gerçek kapsamını
        taşır (rakip: yalnız block kanallar; topic: SOCIAL üretimi;
        fiyat: yalnız SEO)."""
        badges = []
        matched = match_term(kw_text, comp_terms) if comp_terms else None
        if matched and comp_channels:
            badges.append({
                "reason": "competitor",
                "channels": comp_channels,
                "matched_term": matched,
            })
        matched = match_term(kw_text, topic_terms) if topic_terms else None
        if matched:
            badges.append({
                "reason": "topic",
                "channels": ["SOCIAL"],
                "matched_term": matched,
            })
        lowered = kw_text.lower()
        if any(root in lowered for root in SeoPreFilter.PRICE_ROOTS):
            badges.append({
                "reason": "price",
                "channels": ["SEO"],
                "matched_term": None,
            })
        return badges

    if is_v3:
        response_scores = [
            KeywordScoreResponse(
                keyword_id=row["keyword"].id,
                keyword=row["keyword"].keyword,
                ads_score=row["ads_score"],
                seo_score=row["seo_score"],
                social_score=row["social_score"],
                ads_rank=row["ads_rank"],
                seo_rank=row["seo_rank"],
                social_rank=row["social_rank"],
                family_id=row["family_id"],
                family_name=row["family_name"],
                ads_final_rank=row["ads_final_rank"],
                seo_final_rank=row["seo_final_rank"],
                social_final_rank=row["social_final_rank"],
                ads_exclude_reason=row["ads_exclude_reason"],
                seo_exclude_reason=row["seo_exclude_reason"],
                social_exclude_reason=row["social_exclude_reason"],
                ads_pool_class=row["ads_pool_class"],
                seo_pool_class=row["seo_pool_class"],
                social_pool_class=row["social_pool_class"],
                social_priority=row["social_priority"],
                exclusions=_exclusion_badges(row["keyword"].keyword),
            )
            for row in v3_rows
        ]
    else:
        response_scores = [
            KeywordScoreResponse(
                keyword_id=score.keyword_id,
                keyword=keyword.keyword,
                ads_score=score.ads_score,
                seo_score=score.seo_score,
                social_score=score.social_score,
                ads_rank=score.ads_rank,
                seo_rank=score.seo_rank,
                social_rank=score.social_rank,
                exclusions=_exclusion_badges(keyword.keyword),
            )
            for score, keyword in scores
        ]

    return ScoringResultsResponse(
        scoring_run_id=run_id,
        status=run.status,
        algorithm_version=run.algorithm_version,
        total_scored=int(total_scored),
        scores=response_scores,
    )


@router.get("/runs/{run_id}/top/{channel}")
def get_top_by_channel(
    run_id: int,
    channel: str,
    limit: int = 10,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Get top scoring keywords for a specific channel."""
    if channel.upper() not in ["ADS", "SEO", "SOCIAL"]:
        raise HTTPException(status_code=400, detail="Invalid channel. Use ADS, SEO, or SOCIAL")

    verify_scoring_run(db, run_id, brand_profile_id)

    engine = ScoreEngine(db)
    top_keywords = engine.get_top_keywords_by_channel(run_id, channel.upper(), limit)

    return {
        "channel": channel.upper(),
        "top_keywords": top_keywords,
    }


@router.get("/runs/{run_id}/export/xlsx")
def export_scoring_xlsx(
    run_id: int,
    brand_profile_id: int = Query(..., description="Workspace scope"),
    db: Session = Depends(get_db),
):
    """Export scoring results as XLSX file (native Excel format)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    run = verify_scoring_run(db, run_id, brand_profile_id)

    scores = (
        db.query(KeywordScore, Keyword)
        .join(Keyword, KeywordScore.keyword_id == Keyword.id)
        .filter(KeywordScore.scoring_run_id == run_id)
        .all()
    )
    is_v3 = (run.algorithm_version or "").strip() == "v3"
    v3_by_keyword = {
        int(row["keyword"].id): row for row in _v3_score_rows(db, run_id)
    } if is_v3 else {}

    wb = Workbook()
    ws = wb.active
    ws.title = "Skorlama Sonuçları"

    headers = [
        'Keyword ID', 'Keyword', 'Sektör',
        'Aylık Hacim', 'Trend 3M (%)', 'Trend 12M (%)', 'Rekabet Skoru',
        'ADS Skor', 'ADS Sıra', 'SEO Skor', 'SEO Sıra',
        'SOCIAL Skor', 'SOCIAL Sıra', 'Veri Kaynağı',
        'Aile ID', 'Aile Adı',
        'ADS Teslim Sıra', 'ADS Durum',
        'SEO Teslim Sıra', 'SEO Durum',
        'SOCIAL Teslim Sıra', 'SOCIAL Durum', 'SOCIAL Öncelik',
    ]
    ws.append(headers)

    # Sektör: snapshot'taki wk_id -> WorkspaceKeyword (global Keyword.sector
    # KULLANILMAZ — plan v4 §2; workspace'e özgü sektör bilgisi esas)
    from app.database.models import WorkspaceKeyword
    wk_ids = [
        (s.metrics_snapshot or {}).get('wk_id')
        for s, _ in scores
        if (s.metrics_snapshot or {}).get('wk_id') is not None
    ]
    wk_sector_map = {}
    if wk_ids:
        wk_sector_map = {
            wk.id: wk.sector
            for wk in db.query(WorkspaceKeyword).filter(WorkspaceKeyword.id.in_(wk_ids)).all()
        }

    bold_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold_font
    ws.auto_filter.ref = ws.dimensions

    from app.exporters.safe_text import safe_excel_text

    for score, keyword in scores:
        # Metrikler run'in GERCEKTE skorladigi degerlerden: metrics_snapshot
        # (plan v4 §2 — AI/global Keyword degil). Okumalar `.get()` +
        # `is not None`: sifir degerler `or` ile KAYBOLMAZ. Snapshot yoksa
        # (legacy run) Keyword degerlerine kontrollu fallback + etiket.
        snapshot = score.metrics_snapshot or {}
        has_snapshot = bool(snapshot)
        wk_id = snapshot.get('wk_id')

        def _snap(key, keyword_value):
            value = snapshot.get(key)
            if has_snapshot and value is not None:
                return float(value) if not isinstance(value, int) else value
            return keyword_value

        volume = _snap('volume' if is_v3 else 'monthly_volume', keyword.monthly_volume or 0)
        trend_3m = _snap(
            'trend_3m',
            float(keyword.trend_3m) if keyword.trend_3m is not None else 0,
        )
        trend_12m = _snap(
            'trend_12m',
            float(keyword.trend_12m) if keyword.trend_12m is not None else 0,
        )
        competition = _snap(
            'competition' if is_v3 else 'competition_score',
            float(keyword.competition_score) if keyword.competition_score is not None else 0,
        )
        sector = (
            wk_sector_map.get(wk_id) if wk_id is not None else None
        ) or (keyword.sector if not has_snapshot else None) or ''
        data_source = (
            'engine_v3_snapshot' if is_v3 and has_snapshot
            else 'workspace_snapshot' if has_snapshot
            else 'legacy_keyword_fallback'
        )
        v3 = v3_by_keyword.get(int(keyword.id), {})

        def _score_value(channel: str):
            return v3.get(f'{channel}_score') if is_v3 else getattr(score, f'{channel}_score')

        def _rank_value(channel: str):
            return v3.get(f'{channel}_rank') if is_v3 else getattr(score, f'{channel}_rank')

        def _delivery_status(channel: str):
            if not is_v3:
                return ''
            reason = v3.get(f'{channel}_exclude_reason')
            if reason:
                return f'ELENDİ: {reason}'
            return 'TESLİM' if v3.get(f'{channel}_final_rank') is not None else 'ADAY DEĞİL'

        ws.append([
            keyword.id,
            safe_excel_text(keyword.keyword),
            safe_excel_text(sector),
            volume,
            trend_3m,
            trend_12m,
            competition,
            _score_value('ads'),
            _rank_value('ads'),
            _score_value('seo'),
            _rank_value('seo'),
            _score_value('social'),
            _rank_value('social'),
            data_source,
            safe_excel_text(v3.get('family_id') or ''),
            safe_excel_text(v3.get('family_name') or ''),
            v3.get('ads_final_rank'),
            _delivery_status('ads'),
            v3.get('seo_final_rank'),
            _delivery_status('seo'),
            v3.get('social_final_rank'),
            _delivery_status('social'),
            safe_excel_text(v3.get('social_priority') or ''),
        ])

    # Trend/rekabet metrikleri 4 hane; v2 kanal skorları insan ölçeğinde → 2 hane
    metric_cols = [5, 6, 7]
    score_cols = [8, 10, 12]
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for col_idx in metric_cols:
            cell = row[col_idx - 1]
            if isinstance(cell.value, (int, float)):
                cell.number_format = '0.0000'
        for col_idx in score_cols:
            cell = row[col_idx - 1]
            if isinstance(cell.value, (int, float)):
                cell.number_format = '0.00'

    col_widths = [12, 40, 8, 12, 12, 13, 13, 12, 9, 12, 9, 13, 12, 22]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"scoring_run_{run_id}_{run.run_name or 'export'}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
