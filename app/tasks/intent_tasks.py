"""
Celery tasks for intent analysis.
"""
from loguru import logger

from app.tasks.celery_app import celery_app
from app.database.connection import SessionLocal
from app.core.channel.channel_engine import ChannelEngine
from app.core.engine.orchestrator import run_v3_orchestration
from app.core.scoring.state_machine import transition
from app.generators.ai_service import get_ai_service
from app.tasks.task_status import update_task_status


# time_limit 1800→3600 (soft 3300): pencere-çarpanlı deney koşuları (4×
# aday ≈ 4× AI çağrısı ≈ ~30dk) hard limitin dibine yatmasın; normal
# koşular ~7dk sürdüğünden üretime etkisi yok.
@celery_app.task(bind=True, name="intent.run_channel_assignment",
                 soft_time_limit=3300, time_limit=3600)
def run_channel_assignment_task(
    self,
    scoring_run_id: int,
    relevance_coefficient: float = 1.0,
    requested_policy_version: int = None,
    requested_anchor_version: int = None,
    recompute_relevance: bool = False,
    requested_strategy_version: int = None,
):
    """
    Arkaplan görevi olarak kanal ataması yapar.
    Progress tracking ile frontend'den takip edilebilir.

    Plan v13: requested_* sürümleri dispatch-anı snapshot'ıdır (değişmez task
    argümanı). Finalize'da workspace sürümüyle karşılaştırılır — eşleşmezse
    havuz aktive edilmez (POLICY_VERSION_CHANGED). recompute_relevance=True
    ise havuz kurulmadan önce relevance core servisiyle tazelenir.
    """
    from app.core.telemetry import UsageCollector
    from app.database.models import ScoringRun

    db = SessionLocal()
    ai = get_ai_service()
    task_id = (
        getattr(self.request, "id", None)
        if getattr(self, "request", None) and getattr(self.request, "id", None)
        else f"sync-task-{scoring_run_id}"
    )

    # Usage telemetrisi (plan C): task-scoped, thread-safe; paralel intent
    # katmanları aynı root servisi/collector'ı güvenle paylaşır
    run_row = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    collector = UsageCollector(
        scoring_run_id=scoring_run_id,
        brand_profile_id=run_row.brand_profile_id if run_row else None,
        task_id=task_id,
    )
    ai.collector = collector

    # SIRA (Codex 12. tur #2): ÖNCE claim, SONRA durum yazımı. Tersi
    # olursa gecikmiş mükerrer teslim TAMAMLANMIŞ task'ı `running` yapıp
    # sonsuza dek öyle bırakabiliyordu.
    from app.core.screening.attempt_state import (
        AssignmentNotClaimable,
        claim_assignment,
        try_finish_attempt,
    )
    from app.tasks.task_status import mark_task_running_if_active

    try:
        claim_assignment(db, parent_task_id=task_id)
    except AssignmentNotClaimable as exc:
        logger.warning(f"channel_assignment mükerrer teslim reddedildi: {exc}")
        db.close()
        return {'status': 'skipped', 'reason': 'ASSIGNMENT_NOT_CLAIMABLE',
                'scoring_run_id': scoring_run_id}
    # Eski motor run'ı salt-okunur (task sınırı, API + dispatcher kapısının
    # arkasındaki son savunma — ör. deploy öncesi kuyruğa girmiş iş):
    # ledger, ChannelEngine ve HİÇBİR AI çağrısı başlamaz; task + attempt
    # tipli sebeple kapanır, run channel_assigning'de takılı kalmaz.
    from app.core.engine_version_gate import (
        LEGACY_RUN_READ_ONLY,
        is_legacy_run,
        legacy_run_message,
        run_algorithm_version,
    )

    if run_row is not None and is_legacy_run(run_row):
        _legacy_version = run_algorithm_version(run_row)
        _legacy_message = legacy_run_message(_legacy_version)
        logger.warning(
            f"channel_assignment reddedildi ({LEGACY_RUN_READ_ONLY}): run "
            f"{scoring_run_id} algorithm_version={_legacy_version}")
        try:
            update_task_status(
                task_id, status='failed', error_message=_legacy_message,
                result_data={'scoring_run_id': scoring_run_id,
                              'reason': LEGACY_RUN_READ_ONLY,
                              'algorithm_version': _legacy_version})
            try_finish_attempt(db, parent_task_id=task_id,
                               error_code=LEGACY_RUN_READ_ONLY,
                               error_message=_legacy_message)
            if run_row.status == "channel_assigning":
                transition(db, run_row, target="failed")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"legacy red kaydı yazılamadı (run "
                         f"{scoring_run_id}): {exc}")
        finally:
            _closer = getattr(ai, "close", None)
            if callable(_closer):
                try:
                    _closer()
                except Exception:
                    pass
            db.close()
        return {'status': 'failed', 'reason': LEGACY_RUN_READ_ONLY,
                'scoring_run_id': scoring_run_id,
                'algorithm_version': _legacy_version}

    # Terminal kayıt geriye TAŞINMAZ (yalnız pending|running -> running)
    mark_task_running_if_active(task_id)
    update_task_status(task_id, progress=0)

    # Downstream ledger (plan §10.2 / Codex 21. tur #1): onaylı cap'i OLAN
    # attempt'lerde Gemini çağrıları da AYNI bütçeye bağlanır — birleşik
    # hard-cap ancak böyle GERÇEK garantidir. Cap'i olmayan (off) koşularda
    # bağlanmaz ve davranış BUGÜNKÜYLE AYNI kalır.
    downstream_binding = None
    try:
        from app.core.screening.attempt_state import ACTIVE_STATES  # noqa: F401
        from app.core.telemetry.ai_cost_budget import AiCostLedger
        from app.core.telemetry.downstream_ledger import (
            attach_downstream_ledger,
        )
        from app.database.models import ChannelAssignmentAttempt

        attempt_row = (db.query(ChannelAssignmentAttempt)
                       .filter(ChannelAssignmentAttempt.parent_task_id
                               == task_id)
                       .first())
        algorithm_version = getattr(run_row, "algorithm_version", "v2") or "v2"
        if algorithm_version == "v3":
            if attempt_row is None:
                raise RuntimeError(
                    f"v3 koşusu (run={scoring_run_id}) için ChannelAssignmentAttempt bulunamadı — "
                    "bütçe kaydı olmadan orkestratör çalıştırılamaz"
                )
            if (
                attempt_row.approved_screening_cap_usd is None
                or attempt_row.approved_screening_cap_usd <= 0
                or attempt_row.approved_downstream_cap_usd is None
                or attempt_row.approved_downstream_cap_usd <= 0
            ):
                raise RuntimeError(
                    f"v3 koşusu (attempt={attempt_row.id}) geçerli onaylı bütçe limitlerine sahip değil "
                    f"(screening={attempt_row.approved_screening_cap_usd}, "
                    f"downstream={attempt_row.approved_downstream_cap_usd})"
                )

        if (attempt_row is not None
                and attempt_row.screening_mode == "assistive"
                and (attempt_row.approved_downstream_cap_usd is None
                     or attempt_row.approved_screening_cap_usd is None)):
            # Assistive attempt cap'siz olamaz: muhasebesiz Gemini hatti
            # baslatmak yerine fail-closed dur (Codex 27. tur #2)
            raise RuntimeError(
                "assistive attempt onayli cap tasimiyor — muhasebesiz "
                "downstream calistirilmaz")
        if (attempt_row is not None
                and attempt_row.approved_downstream_cap_usd is not None
                and attempt_row.approved_screening_cap_usd is not None):
            # Muhasebeye baglanamayan route varsa HIC harcama yapilmaz
            from app.core.telemetry.downstream_ledger import (
                require_ledgered_routes,
            )

            require_ledgered_routes()
            downstream_binding = attach_downstream_ledger(
                ai,
                AiCostLedger(
                    SessionLocal, attempt_id=attempt_row.id,
                    expected_screening_cap_usd=(
                        attempt_row.approved_screening_cap_usd),
                    expected_downstream_cap_usd=(
                        attempt_row.approved_downstream_cap_usd)),
                scoring_run_id=scoring_run_id, attempt_id=attempt_row.id)
            logger.info(
                f"downstream ledger bagli: attempt {attempt_row.id}, cap "
                f"${attempt_row.approved_downstream_cap_usd}")

        if algorithm_version == "v3" and downstream_binding is None:
            raise RuntimeError(
                f"v3 koşusu (run={scoring_run_id}) için ledger bağlanamadı — "
                "muhasebesiz provider/orkestratör çağrısına izin verilmez"
            )
    except Exception as exc:  # noqa: BLE001
        # Muhasebe kurulamadiysa KOSU DURUR: bagli olmayan harcama
        # "birlesik cap" vaadini bozar (fail-closed)
        logger.error(f"downstream ledger kurulamadi: {exc}")
        update_task_status(task_id, status='failed',
                           error_message=f'Butce muhasebesi kurulamadi: {exc}')
        try_finish_attempt(db, parent_task_id=task_id,
                           error_code='LEDGER_BIND_FAILED',
                           error_message=str(exc))
        try:
            if run_row and getattr(run_row, "algorithm_version", "v2") == "v3":
                if run_row.status in ("scored", "channel_assigning"):
                    transition(db, run_row, target="failed")
        except Exception:
            pass
        db.close()
        return {'status': 'failed', 'reason': 'LEDGER_BIND_FAILED', 'error': str(exc)}

    try:
        self.update_state(state='PROGRESS', meta={'step': 'channel_assignment'})

        algorithm_version = getattr(run_row, "algorithm_version", "v2") or "v2"
        if algorithm_version == "v3":
            def _progress_cb(progress: int, message: str) -> None:
                update_task_status(
                    task_id,
                    status="running",
                    progress=progress,
                    result_data={"current_message": message, "step": message},
                )

            result = run_v3_orchestration(
                db,
                run=run_row,
                ai=ai,
                task_id=task_id,
                progress_callback=_progress_cb,
            )

            update_task_status(
                task_id,
                status='completed',
                progress=100,
                result_data={
                    'scoring_run_id': scoring_run_id,
                    'algorithm_version': 'v3',
                    'delivery_summary': result,
                    'channels': result.get('channels', {}),
                    'usage': collector.finalize(),
                    'downstream_ledger': (downstream_binding.summary()
                                          if downstream_binding else None),
                }
            )
        else:
            engine = ChannelEngine(db, ai)
            result = engine.run_channel_assignment(
                scoring_run_id,
                relevance_coefficient=relevance_coefficient,
                task_id=task_id,
                requested_policy_version=requested_policy_version,
                requested_anchor_version=requested_anchor_version,
                recompute_relevance=recompute_relevance,
                requested_strategy_version=requested_strategy_version,
            )

            update_task_status(
                task_id,
                status='completed',
                progress=100,
                result_data={
                    'scoring_run_id': scoring_run_id,
                    'effective_relevance_coefficient': relevance_coefficient,
                    'steps': result.get('steps', {}),
                    # Faz G: run-15 karşılaştırmasının ölçüm aleti
                    'selection_quality': result.get('selection_quality', {}),
                    # Plan C: ham token toplamları + ikincil maliyet tahmini
                    'usage': collector.finalize(),
                    # Ledger muhasebesi (TAHMIN degil, rezervasyon/settle)
                    'downstream_ledger': (downstream_binding.summary()
                                          if downstream_binding else None),
                }
            )

        # Codex 10. tur #2: attempt KAPATILIR (aktif attempt partial
        # unique'i bir sonraki atamayi bloke etmesin). Shadow'da tarama
        # hala kosuyorsa kapanmaz — `try_finish_attempt` iki tarafi da
        # kontrol eder ve son biten taraf kapatir.
        try_finish_attempt(db, parent_task_id=task_id)

        return {
            'status': 'completed',
            'scoring_run_id': scoring_run_id,
            'result': result
        }

    except Exception as e:
        usage = collector.finalize()  # failure yolunda da persist (plan C)
        update_task_status(
            task_id, status='failed', error_message=str(e),
            result_data={
                'scoring_run_id': scoring_run_id, 'usage': usage,
                'algorithm_version': getattr(run_row, "algorithm_version", "v2"),
                # Hata yolunda da muhasebe ozeti KAYBOLMAZ (denetim)
                'downstream_ledger': (downstream_binding.summary()
                                      if downstream_binding else None),
            },
        )
        try_finish_attempt(db, parent_task_id=task_id,
                           error_code='ASSIGNMENT_FAILED',
                           error_message=str(e))
        # v3 hata durumunda run status'ünü 'failed' yap (state machine)
        try:
            if run_row and run_row.status in ("scored", "channel_assigning"):
                transition(db, run_row, target="failed")
        except Exception:
            pass
        return {'status': 'failed', 'error': str(e)}

    finally:
        # Plan G: task-scope shutdown — client'ı root servis kapatır
        closer = getattr(ai, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
        db.close()
