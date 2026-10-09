"""
ADS üretim versiyonlaması testleri (plan Faz E).

Kapsam: ortak dispatcher yarış güvenliği (eşzamanlı iki transaction),
yaşam döngüsü (ilk başarı active / sonraki draft), atomik aktivasyon
(partial unique index ihlalsiz), regenerate klonu, başarısızlıkta mevcut
setlerin korunması, E2d stale reconciliation, içerik-staleness ve
migration backfill invariant'ları.
"""
import json
import threading
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from app.database.models import (
    AdDescription,
    AdGenerationSet,
    AdGroup,
    AdHeadline,
    TaskResult,
)
from app.generators.ads.generation_sets import (
    SET_ACTIVE,
    SET_ARCHIVED,
    SET_DRAFT,
    SET_FAILED,
    SET_GENERATING,
    AdsGenerationConflict,
    activate_set,
    active_ad_groups,
    dispatch_ads_generation,
    finalize_ads_failure,
    finalize_ads_success,
    get_active_set,
    reconcile_stale_ads,
)


def _make_set(db, run_id, version, status, *, is_stale=False, task_id=None):
    gen_set = AdGenerationSet(
        scoring_run_id=run_id, version_number=version, status=status,
        is_stale=is_stale, task_id=task_id,
    )
    db.add(gen_set)
    db.flush()
    return gen_set


def _make_group(db, gen_set, name="Grup"):
    group = AdGroup(
        scoring_run_id=gen_set.scoring_run_id,
        generation_set_id=gen_set.id,
        group_name=name,
        target_keyword_ids=[1],
        target_keywords=["kelime"],
    )
    db.add(group)
    db.flush()
    return group


# ---------------------------------------------------------------------------
# Dispatcher: yarış güvenliği
# ---------------------------------------------------------------------------


def test_dispatch_creates_generating_set_and_pending_task(
    db_session, make_workspace, make_scoring_run
):
    ws = make_workspace("Dispatch WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    gen_set = dispatch_ads_generation(
        db_session, run.id, task_id="task-disp-1",
        request_snapshot={"operation": "full"},
    )

    assert gen_set.status == SET_GENERATING
    assert gen_set.version_number == 1
    task = db_session.query(TaskResult).filter_by(task_id="task-disp-1").one()
    assert task.status == "pending" and task.task_type == "ads"

    # Aynı run'a ikinci dispatch → conflict (generating set guard'ı)
    with pytest.raises(AdsGenerationConflict):
        dispatch_ads_generation(
            db_session, run.id, task_id="task-disp-2",
            request_snapshot={"operation": "full"},
        )


def test_concurrent_dispatch_two_transactions_one_wins(
    db_session, make_workspace, make_scoring_run
):
    """Eşzamanlı iki AYRI transaction: biri kabul, biri 409-conflict.
    (UUID+unique kısıt yarışı kapatmaz — satır kilidi serileştirir.)"""
    from app.database.connection import SessionLocal

    ws = make_workspace("Concurrent WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    # run_id'yi thread'lerden ÖNCE yakala — commit sonrası expire olan ORM
    # nesnesine thread'lerden erişmek fixture session'ını paylaştırır
    # ('provisioning a new connection' hatasının kaynağı)
    run_id = run.id
    db_session.commit()

    results = {}
    barrier = threading.Barrier(2)

    def _attempt(name, task_id):
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            barrier.wait(timeout=10)
            gen_set = dispatch_ads_generation(
                db, run_id, task_id=task_id,
                request_snapshot={"operation": "full"},
            )
            results[name] = ("ok", gen_set.id)
        except AdsGenerationConflict as c:
            results[name] = ("conflict", c.task_id)
        except Exception as e:  # pragma: no cover - teşhis için
            results[name] = ("error", str(e))
        finally:
            db.close()

    threads = [
        threading.Thread(target=_attempt, args=(f"t{i}", f"task-conc-{i}"))
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    outcomes = sorted(v[0] for v in results.values())
    assert outcomes == ["conflict", "ok"], results
    # Tek generating set oluştu
    count = db_session.query(AdGenerationSet).filter_by(
        scoring_run_id=run_id, status=SET_GENERATING
    ).count()
    assert count == 1


# ---------------------------------------------------------------------------
# Yaşam döngüsü: ilk başarı active, sonraki draft; aktivasyon atomik
# ---------------------------------------------------------------------------


def test_first_success_active_second_draft_and_activation_flow(
    db_session, make_workspace, make_scoring_run
):
    ws = make_workspace("Lifecycle WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    set1 = _make_set(db_session, run.id, 1, SET_GENERATING, task_id="lc-t1")
    db_session.add(TaskResult(task_id="lc-t1", task_type="ads",
                              scoring_run_id=run.id, status="running"))
    _make_group(db_session, set1, "Set1 Grup")
    db_session.commit()

    status1 = finalize_ads_success(db_session, set1, groups_count=1, failed_groups=0)
    assert status1 == SET_ACTIVE  # run'da başka başarılı set yok → otomatik aktif
    task1 = db_session.query(TaskResult).filter_by(task_id="lc-t1").one()
    assert task1.status == "completed" and task1.progress == 100
    assert task1.result_data["generation_set_id"] == set1.id

    # İkinci başarılı üretim → draft
    set2 = _make_set(db_session, run.id, 2, SET_GENERATING, task_id="lc-t2")
    db_session.add(TaskResult(task_id="lc-t2", task_type="ads",
                              scoring_run_id=run.id, status="running"))
    _make_group(db_session, set2, "Set2 Grup")
    db_session.commit()
    status2 = finalize_ads_success(db_session, set2, groups_count=1, failed_groups=0)
    assert status2 == SET_DRAFT

    # Aktivasyon: eski aktif archived, hedef active — atomik, index ihlalsiz
    activate_set(db_session, set2)
    db_session.expire_all()
    assert db_session.get(AdGenerationSet, set1.id).status == SET_ARCHIVED
    assert db_session.get(AdGenerationSet, set2.id).status == SET_ACTIVE

    # Archived set yeniden aktive edilebilir
    activate_set(db_session, db_session.get(AdGenerationSet, set1.id))
    db_session.expire_all()
    assert db_session.get(AdGenerationSet, set1.id).status == SET_ACTIVE
    assert db_session.get(AdGenerationSet, set2.id).status == SET_ARCHIVED


def test_activation_guards_generating_failed_stale_empty(
    db_session, make_workspace, make_scoring_run
):
    ws = make_workspace("Activation Guard WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    generating = _make_set(db_session, run.id, 1, SET_GENERATING)
    failed = _make_set(db_session, run.id, 2, SET_FAILED)
    stale_draft = _make_set(db_session, run.id, 3, SET_DRAFT, is_stale=True)
    _make_group(db_session, stale_draft)
    empty_draft = _make_set(db_session, run.id, 4, SET_DRAFT)
    db_session.commit()

    for bad in (generating, failed):
        with pytest.raises(ValueError):
            activate_set(db_session, bad)
    with pytest.raises(ValueError, match="Stale"):
        activate_set(db_session, stale_draft)
    with pytest.raises(ValueError, match="Boş"):
        activate_set(db_session, empty_draft)


def test_failure_finalize_does_not_touch_existing_sets(
    db_session, make_workspace, make_scoring_run
):
    ws = make_workspace("Failure WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    active = _make_set(db_session, run.id, 1, SET_ACTIVE)
    _make_group(db_session, active)
    failing = _make_set(db_session, run.id, 2, SET_GENERATING, task_id="fail-t")
    db_session.add(TaskResult(task_id="fail-t", task_type="ads",
                              scoring_run_id=run.id, status="running"))
    db_session.commit()

    finalize_ads_failure(db_session, failing, error_message="0 grup üretildi")

    db_session.expire_all()
    assert db_session.get(AdGenerationSet, failing.id).status == SET_FAILED
    assert db_session.get(AdGenerationSet, active.id).status == SET_ACTIVE  # DOKUNULMADI
    task = db_session.query(TaskResult).filter_by(task_id="fail-t").one()
    assert task.status == "failed"

    # Geç hata: completed set failed'a DÜŞÜRÜLMEZ
    finalize_ads_failure(db_session, active, error_message="geç hata")
    db_session.expire_all()
    assert db_session.get(AdGenerationSet, active.id).status == SET_ACTIVE


# ---------------------------------------------------------------------------
# E2d stale reconciliation
# ---------------------------------------------------------------------------


def _age(db, obj, seconds):
    """created_at/started_at'i geçmişe çeker (stale eşiğini aşırır)."""
    past = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    if hasattr(obj, "created_at"):
        obj.created_at = past
    if hasattr(obj, "started_at") and obj.started_at is not None:
        obj.started_at = past
    db.flush()


def test_stale_reconciliation_by_set_status(
    db_session, make_workspace, make_scoring_run
):
    ws = make_workspace("Reconcile WS")
    run = make_scoring_run(brand_profile_id=ws.id)

    # Senaryo A: set BAŞARIYLA commit'lenmiş (active) ama task running kalmış
    # → set KORUNUR, task set özetinden completed ONARILIR
    done_set = _make_set(db_session, run.id, 1, SET_ACTIVE, task_id="rec-done")
    done_set.groups_count = 5
    _make_group(db_session, done_set)
    done_task = TaskResult(task_id="rec-done", task_type="ads",
                           scoring_run_id=run.id, status="running")
    db_session.add(done_task)
    db_session.flush()
    _age(db_session, done_task, 2000)

    # Senaryo B: generating set + stale task → İKİSİ failed
    half_set = _make_set(db_session, run.id, 2, SET_GENERATING, task_id="rec-half")
    half_task = TaskResult(task_id="rec-half", task_type="ads",
                           scoring_run_id=run.id, status="running")
    db_session.add(half_task)
    db_session.flush()
    _age(db_session, half_task, 2000)

    # Senaryo C: orphan generating set (TaskResult'ı yok) → failed
    orphan_set = _make_set(db_session, run.id, 3, SET_GENERATING, task_id="rec-orphan")
    _age(db_session, orphan_set, 2000)
    db_session.commit()

    reconcile_stale_ads(db_session, run.id)
    db_session.commit()
    db_session.expire_all()

    # A: set intact, task onarıldı; aktif gruplar kullanılabilir kalıyor
    assert db_session.get(AdGenerationSet, done_set.id).status == SET_ACTIVE
    repaired = db_session.query(TaskResult).filter_by(task_id="rec-done").one()
    assert repaired.status == "completed"
    assert repaired.result_data.get("reconciled") is True
    assert active_ad_groups(db_session, run.id).count() == 1

    # B: ikisi de failed
    assert db_session.get(AdGenerationSet, half_set.id).status == SET_FAILED
    assert db_session.query(TaskResult).filter_by(
        task_id="rec-half").one().status == "failed"

    # C: orphan failed → run artık 409'a kilitli DEĞİL
    assert db_session.get(AdGenerationSet, orphan_set.id).status == SET_FAILED
    fresh = dispatch_ads_generation(
        db_session, run.id, task_id="rec-new",
        request_snapshot={"operation": "full"},
    )
    assert fresh.status == SET_GENERATING


def test_worker_cannot_write_to_stale_failed_set(
    db_session, make_workspace, make_scoring_run
):
    """Geç dönen worker: set stale-failed edilmişse finalize sonuç YAZMAZ."""
    ws = make_workspace("Late Worker WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    gen_set = _make_set(db_session, run.id, 1, SET_FAILED, task_id="late-t")
    db_session.commit()

    outcome = finalize_ads_success(db_session, gen_set, groups_count=3, failed_groups=0)
    assert outcome == "aborted"
    db_session.expire_all()
    assert db_session.get(AdGenerationSet, gen_set.id).status == SET_FAILED
    assert db_session.get(AdGenerationSet, gen_set.id).groups_count is None


# ---------------------------------------------------------------------------
# İçerik staleness (kanal reassignment) + stale-sonrası semantik
# ---------------------------------------------------------------------------


def test_reassignment_marks_sets_stale_and_next_success_is_draft(
    db_session, make_workspace, make_scoring_run
):
    from app.core.scoring.state_machine import transition

    ws = make_workspace("Stale WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    active = _make_set(db_session, run.id, 1, SET_ACTIVE)
    _make_group(db_session, active)
    db_session.commit()
    assert active_ad_groups(db_session, run.id).count() == 1

    # Kanal reassignment → içerik staleness (status KORUNUR, is_stale=True)
    transition(db_session, run, "channel_assigning")
    db_session.expire_all()
    stale_active = db_session.get(AdGenerationSet, active.id)
    assert stale_active.status == SET_ACTIVE
    assert stale_active.is_stale is True
    # Export/GET artık okumaz
    assert active_ad_groups(db_session, run.id).count() == 0
    assert get_active_set(db_session, run.id) is None
    # Stale set aktive edilemez (yeniden)
    stale_active.status = SET_ARCHIVED
    db_session.commit()
    with pytest.raises(ValueError, match="Stale"):
        activate_set(db_session, stale_active)

    # Stale-sonrası yeni üretim: run'da set VAR (stale da olsa) → DRAFT
    new_set = _make_set(db_session, run.id, 2, SET_GENERATING, task_id="stale-new")
    db_session.add(TaskResult(task_id="stale-new", task_type="ads",
                              scoring_run_id=run.id, status="running"))
    _make_group(db_session, new_set)
    db_session.commit()
    status = finalize_ads_success(db_session, new_set, groups_count=1, failed_groups=0)
    assert status == SET_DRAFT  # kullanıcı onayına kadar aktifleşmez


def test_mid_generation_reassignment_keeps_set_stale_draft(
    db_session, make_workspace, make_scoring_run
):
    """Staleness yarışı (codex): üretim SÜRERKEN kanal ataması yenilenirse
    finalize is_stale'i SIFIRLAYAMAZ ve set otomatik active OLAMAZ.

    GERÇEK yarış: worker AYRI session'da seti önceden yüklemiş (identity
    map is_stale=False cache'li) — reassignment BAŞKA session'da olur;
    finalize populate_existing ile kilit altında satırı tazelemek ZORUNDA
    (expire_all ile aynı session'da test etmek yarışı maskeler)."""
    from app.core.scoring.state_machine import transition
    from app.database.connection import SessionLocal

    ws = make_workspace("Midgen Stale WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="scored")
    run_id = run.id
    gen_set = _make_set(db_session, run_id, 1, SET_GENERATING, task_id="midgen-1")
    db_session.add(TaskResult(task_id="midgen-1", task_type="ads",
                              scoring_run_id=run_id, status="running"))
    _make_group(db_session, gen_set)
    set_id = gen_set.id
    db_session.commit()

    worker_db = SessionLocal()
    reassign_db = SessionLocal()
    try:
        # Worker seti üretim başında yükler → is_stale=False cache'lenir
        worker_set = worker_db.get(AdGenerationSet, set_id)
        assert worker_set.is_stale is False

        # Üretim sürerken BAŞKA session'da reassignment → set stale
        reassign_run = reassign_db.get(type(run), run_id)
        transition(reassign_db, reassign_run, "channel_assigning")
        reassign_db.commit()

        # Worker geç bitirir: cache'lenmiş False'a rağmen finalize DB'den
        # tazeler → active DEĞİL draft, is_stale korunur
        status = finalize_ads_success(
            worker_db, worker_set, groups_count=1, failed_groups=0
        )
        assert status == SET_DRAFT
    finally:
        worker_db.close()
        reassign_db.close()

    db_session.expire_all()
    final = db_session.get(AdGenerationSet, set_id)
    assert final.status == SET_DRAFT
    assert final.is_stale is True  # finalize is_stale'i sıfırlamadı
    task = db_session.query(TaskResult).filter_by(task_id="midgen-1").one()
    assert (task.result_data or {}).get("status") == SET_DRAFT
    assert active_ad_groups(db_session, run_id).count() == 0
    assert get_active_set(db_session, run_id) is None
    with pytest.raises(ValueError, match="Stale"):
        activate_set(db_session, final)


def test_worker_aborts_before_ai_when_set_stale(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """Dispatch ile worker başlangıcı arasında reassignment: worker AI'a
    BAŞLAMADAN set+task failed yapar (run 409 kilidinde kalmaz)."""
    from app.tasks import generation_tasks

    ws = make_workspace("Prestale WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    gen_set = _make_set(
        db_session, run.id, 1, SET_GENERATING,
        is_stale=True, task_id="prestale-1",
    )
    db_session.add(TaskResult(task_id="prestale-1", task_type="ads",
                              scoring_run_id=run.id, status="pending"))
    db_session.commit()

    def _boom(*args, **kwargs):
        raise AssertionError("AI üretimi ÇAĞRILMAMALIYDI (stale set)")

    monkeypatch.setattr(
        "app.generators.ads.ads_generator.AdsGenerator.generate_ads", _boom
    )
    result = generation_tasks.generate_ads_task.apply(
        kwargs={
            "scoring_run_id": run.id,
            "brand_name": "Marka",
            "generation_set_id": gen_set.id,
        },
        task_id="prestale-1",
    )
    assert result.result.get("reason") == "stale_before_generation"

    db_session.expire_all()
    assert db_session.get(AdGenerationSet, gen_set.id).status == SET_FAILED
    task = db_session.query(TaskResult).filter_by(task_id="prestale-1").one()
    assert task.status == "failed"


def test_worker_passes_dropped_negatives_to_set_warnings(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """Plan 2.1: hedefle çakışıp atılan negatifler finalize_ads_success'e
    warnings= olarak geçer ve AdGenerationSet.warnings'e yazılır."""
    from types import SimpleNamespace

    from app.schemas.ads import AdGroupFullSchema
    from app.tasks import generation_tasks

    ws = make_workspace("NegWarn WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")
    gen_set = _make_set(
        db_session, run.id, 1, SET_GENERATING, task_id="negwarn-1",
    )
    db_session.add(TaskResult(task_id="negwarn-1", task_type="ads",
                              scoring_run_id=run.id, status="pending"))
    db_session.commit()

    drop = {
        "type": "negative_dropped", "group": "Laptop", "negative": "ucuz",
        "match_type": "broad", "target": "ucuz laptop", "rule": "broad",
    }
    group = AdGroupFullSchema(
        name="Laptop", theme="t", keyword_ids=[1], keywords=["ucuz laptop"],
        headlines=[], descriptions=[], negative_keywords=[],
        dropped_negatives=[drop],
    )
    fake_result = SimpleNamespace(
        total_groups=1, failed_groups=0, ad_groups=[group],
        total_headlines=0, total_keywords=1,
    )
    monkeypatch.setattr(
        "app.generators.ads.ads_generator.AdsGenerator.generate_ads",
        lambda self, *a, **k: fake_result,
    )
    monkeypatch.setattr(
        "app.generators.ai_service.get_ai_service",
        lambda *a, **k: SimpleNamespace(),
    )

    result = generation_tasks.generate_ads_task.apply(
        kwargs={
            "scoring_run_id": run.id,
            "brand_name": "Marka",
            "generation_set_id": gen_set.id,
        },
        task_id="negwarn-1",
    )
    assert result.result.get("status") == "completed", result.result

    db_session.expire_all()
    saved = db_session.get(AdGenerationSet, gen_set.id)
    assert saved.status == SET_ACTIVE
    assert saved.warnings == [drop]


def test_ads_dispatch_endpoints_return_202(
    client, db_session, make_workspace, make_scoring_run, monkeypatch
):
    """Async sözleşme (codex): full üretim VE grup regenerate 202 döner."""
    from app.tasks import generation_tasks

    monkeypatch.setattr(
        generation_tasks.generate_ads_task, "apply_async",
        lambda *a, **k: None,
    )
    ws = make_workspace("E202 WS")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigned")

    res = client.post(
        f"/api/v1/generation/ads/rsa?brand_profile_id={ws.id}",
        json={"scoring_run_id": run.id, "brand_name": "Marka"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "generating"
    assert body["generation_set_id"]

    # İlk seti başarıyla bitir → regenerate için non-stale aktif kaynak
    gen_set = db_session.get(AdGenerationSet, body["generation_set_id"])
    group = _make_group(db_session, gen_set)
    db_session.commit()
    finalize_ads_success(db_session, gen_set, groups_count=1, failed_groups=0)

    res2 = client.post(
        f"/api/v1/generation/ads/rsa/group/{group.id}/regenerate"
        f"?brand_profile_id={ws.id}",
        json={},
    )
    assert res2.status_code == 202, res2.text
    assert res2.json()["status"] == "generating"


# ---------------------------------------------------------------------------
# Regenerate: klon + kaynak set değişmez + run kimliği setten
# ---------------------------------------------------------------------------


def test_clone_and_regenerate_leaves_source_untouched(
    db_session, make_workspace, make_scoring_run
):
    from app.generators.ads.ads_generator import AdsGenerator

    ws = make_workspace("Clone WS")
    run = make_scoring_run(brand_profile_id=ws.id)
    source = _make_set(db_session, run.id, 1, SET_ACTIVE)
    g1 = _make_group(db_session, source, "Korunacak Grup")
    db_session.add(AdHeadline(ad_group_id=g1.id, headline_text="Basit Baslik",
                              headline_type="benefit", sort_order=0))
    g2 = _make_group(db_session, source, "Yenilenecek Grup")
    db_session.add(AdDescription(ad_group_id=g2.id,
                                 description_text="Eski aciklama",
                                 description_type="benefit", sort_order=0))
    target = _make_set(db_session, run.id, 2, SET_GENERATING)
    db_session.commit()

    generator = AdsGenerator(db_session, None)  # AI klonlamada kullanılmaz
    cloned = generator.clone_groups_to_set(
        source.id, target.id, exclude_group_id=g2.id
    )
    db_session.commit()

    assert cloned == 1
    clone_groups = db_session.query(AdGroup).filter_by(
        generation_set_id=target.id
    ).all()
    assert [g.group_name for g in clone_groups] == ["Korunacak Grup"]
    # Run kimliği SETTEN türetildi
    assert all(g.scoring_run_id == run.id for g in clone_groups)
    # Klonun çocukları kopyalandı
    clone_headlines = db_session.query(AdHeadline).filter_by(
        ad_group_id=clone_groups[0].id
    ).count()
    assert clone_headlines == 1
    # KAYNAK set değişmedi
    source_groups = db_session.query(AdGroup).filter_by(
        generation_set_id=source.id
    ).count()
    assert source_groups == 2


# ---------------------------------------------------------------------------
# Migration backfill invariant'ları (boş-DB upgrade YETMEZ)
# ---------------------------------------------------------------------------



def test_migration_backfill_links_legacy_groups(db_engine):
    """Legacy yol: head'de veri → downgrade (kolon+tablo düşer, GRUPLAR
    kalır) → upgrade head → backfill legacy seti yaratıp bağlar; NULL
    kalmaz; run başına 1 active; kolon NOT NULL.

    NOT: 'önceki revision'a temiz upgrade' baseline squash yüzünden eski
    şemayı VERMEZ (canlı create_all) — legacy durum ancak downgrade ile
    üretilebilir; bu test o gerçeğe göre kurgulandı."""
    # Şemayı sıfırla ve head'e getir
    with db_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True, text=True, cwd="/app",
    )
    assert result.returncode == 0, result.stderr

    # Head'de geçerli veri (set'li grup)
    with db_engine.connect() as conn:
        run_id = conn.execute(text(
            "INSERT INTO scoring_runs (run_name, status, total_keywords, "
            " ads_capacity, seo_capacity, social_capacity, algorithm_version) "
            "VALUES ('legacy run', 'completed', 0, 10, 10, 10, 'v2') RETURNING id"
        )).scalar()
        set_id = conn.execute(text(
            "INSERT INTO ad_generation_sets "
            "(scoring_run_id, version_number, status, is_stale) "
            "VALUES (:r, 1, 'active', false) RETURNING id"
        ), {"r": run_id}).scalar()
        conn.execute(text(
            "INSERT INTO ad_groups (scoring_run_id, generation_set_id, "
            " group_name, target_keyword_ids, target_keywords) "
            "VALUES (:r, :s, 'Legacy Grup', '[]'::json, '[]'::json)"
        ), {"r": run_id, "s": set_id})
        conn.commit()

    # Downgrade: set tablosu + kolon düşer, AdGroup SATIRI kalır → legacy durum
    result = subprocess.run(
        ["alembic", "downgrade", "20260713_001"],
        capture_output=True, text=True, cwd="/app",
    )
    assert result.returncode == 0, result.stderr
    with db_engine.connect() as conn:
        surviving = conn.execute(text("SELECT count(*) FROM ad_groups")).scalar()
        assert surviving == 1  # grup migration'sız hayatta

    # Upgrade head: backfill legacy seti yaratıp bağlamalı
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True, text=True, cwd="/app",
    )
    assert result.returncode == 0, result.stderr

    with db_engine.connect() as conn:
        # NULL kalmadı
        nulls = conn.execute(text(
            "SELECT count(*) FROM ad_groups WHERE generation_set_id IS NULL"
        )).scalar()
        assert nulls == 0
        # Legacy set: version 1, active, gruba bağlı
        sets = conn.execute(text(
            "SELECT id, status, version_number FROM ad_generation_sets "
            "WHERE scoring_run_id = :r"
        ), {"r": run_id}).fetchall()
        assert len(sets) == 1
        assert sets[0][1] == "active" and sets[0][2] == 1
        # Run başına ≤1 active (partial unique index mevcut)
        actives = conn.execute(text(
            "SELECT count(*) FROM ad_generation_sets "
            "WHERE scoring_run_id = :r AND status = 'active'"
        ), {"r": run_id}).scalar()
        assert actives == 1

    # Kolon NOT NULL oldu
    inspector = inspect(db_engine)
    col = next(
        c for c in inspector.get_columns("ad_groups")
        if c["name"] == "generation_set_id"
    )
    assert col["nullable"] is False
