# -*- coding: utf-8 -*-
"""Faz 6a — create/execute lokasyon staleness kapıları + run-scoped
salt-okunur audit endpoint'i.

plan_v3_lokasyon_filtresi.md §5.7, §5.6, §6, §8.3. Kapsam:
  * `mode=none` TAMAMEN geriye uyumlu: token istenmez, davranış değişmez.
  * Aktif mod (exclude_all/focus_only): taze, KAYITLI policy'ye karşı
    üretilmiş bir önizleme token'ı olmadan run oluşturulamaz.
  * create->execute yarışı: evren veya kayıtlı policy execute'tan önce
    değişirse run FAILED olur, hiçbir Celery task kuyruğa girmez.
  * DRAFT'tan üretilen bir önizleme token'ı (values kayıtlıyla aynı olsa
    bile) run yetkilendirmesinde KULLANILAMAZ; client `is_saved_policy`
    bayrağını yalan söylese bile bağımsız fingerprint yeniden hesaplaması
    güvenliği sağlar.
  * `/brand-profile/runs/{id}/location-audit`: yalnız run'ın MÜHÜRLÜ
    manifest policy'sini ve DONDURULMUŞ evrenini okur — canlı profil
    sonradan değişse bile mühürlü karara göre raporlar. Sayfalanır. Terminal
    durum kapısı `channel_assigned`dır (GERÇEK pipeline terminali — bkz.
    `app/core/engine/orchestrator.py:418-420`); `completed` yalnız eski/
    tarihsel alan için geriye uyumlu takma addır (aynı sözleşme
    `app/api/v1/channels.py`, `app/core/trial_authorization.py`
    `SUCCESS_STATUSES`, `app/core/dashboard/next_action.py`).
  * AI-çağırmama özelliği YAPISALDIR: bu dosyadaki hiçbir endpoint
    (create_scoring_run/execute_scoring/preview_location_filter/
    get_location_audit) `Depends(get_ai)` tanımlamaz veya `AIService`/
    `GeminiService` import etmez — dinamik bir "boom" guard'ı bu
    endpoint'lerde asla tetiklenmeyeceği için (kod hiç o yola girmiyor)
    burada KASITLI OLARAK kullanılmıyor; property statik koda bakılarak
    doğrulanır, sahte-yeşil bir mock ile değil.

KAPSAM DIŞI: matcher birim testleri (test_location_policy.py), önizleme
endpoint'inin kendi sayım/örnek mantığı (test_location_filter_preview.py),
orchestrator pre-AI filtresi (test_location_filter_v3.py), manifest
mühür/fail-closed sözleşmesi (test_location_manifest_seal.py).
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.core.policy.location_policy import (
    MODE_EXCLUDE_ALL, MODE_FOCUS_ONLY, normalize_location_text,
)
from app.database.models import ScoringRun, TaskResult

PREVIEW_PATH = "/api/v1/brand-profile/workspaces/{id}/policy/location-preview"
CREATE_PATH = "/api/v1/scoring/runs"
AUDIT_PATH = "/api/v1/brand-profile/runs/{id}/location-audit"

RUN_BODY = {
    "run_name": "lokasyon kapisi testi",
    "ads_capacity": 10,
    "seo_capacity": 10,
    "social_capacity": 10,
}


def _setup_workspace(make_workspace, *, location=None, name="loc gate ws"):
    ws = make_workspace(name)
    ws.status = "confirmed"
    ws.profile_data = {
        "company_name": "Loc Gate Co",
        "brand_terms": ["locgate"],
        "sector": "Test",
        **(location or {}),
    }
    ws.channel_strategy = None
    return ws


def _preview_saved(client, ws_id):
    """Draft alanlarından HİÇBİRİNİ göndermeden önizleme al —
    `is_saved_policy=True` döner (workspace'in kayıtlı değerlerine göre)."""
    resp = client.post(PREVIEW_PATH.format(id=ws_id), json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preview_draft(client, ws_id, **location_overrides):
    resp = client.post(PREVIEW_PATH.format(id=ws_id), json=location_overrides)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create(client, ws_id, monkeypatch, **overrides):
    monkeypatch.setattr(settings, "ENABLE_ENGINE_V3", True)
    body = {
        **RUN_BODY, "brand_profile_id": ws_id, "algorithm_version": "v3",
        "auto_assign_channels": True, **overrides,
    }
    return client.post(CREATE_PATH, json=body)


def _token_fields(token: dict) -> dict:
    return {
        "location_universe_fingerprint": token["universe_fingerprint"],
        "location_policy_fingerprint": token["location_policy_fingerprint"],
        "location_preview_is_saved_policy": token["is_saved_policy"],
    }


def _mock_celery(monkeypatch):
    from app.tasks.intent_tasks import run_channel_assignment_task

    calls = {"count": 0}

    def _fake(*args, **kwargs):
        calls["count"] += 1

    monkeypatch.setattr(run_channel_assignment_task, "apply_async", _fake)
    return calls


def _boom_celery(monkeypatch):
    from app.tasks.intent_tasks import run_channel_assignment_task

    def _boom(*args, **kwargs):
        raise AssertionError(
            "Celery run_channel_assignment_task.apply_async ÇAĞRILDI — "
            "lokasyon kapısı task dispatch'inden ÖNCE durmalıydı")

    monkeypatch.setattr(run_channel_assignment_task, "apply_async", _boom)


class TestModeNoneBackwardCompatible:
    def test_create_and_execute_without_token(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """`none -> none`: hiç token/gate yok, execute'taki dış koşul
        (`_saved_mode != MODE_NONE or _gate is not None`) HER İKİ tarafta da
        False'tur -> kapı tamamen atlanır, gerçek geriye uyumluluk."""
        ws = _setup_workspace(make_workspace)  # location alanı yok -> none
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        make_keyword("mobil uygulama", brand_profile_id=ws.id)
        db_session.commit()

        res = _create(client, ws.id, monkeypatch)
        assert res.status_code == 201, res.text
        run_id = res.json()["id"]

        calls = _mock_celery(monkeypatch)
        exec_res = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert exec_res.status_code == 200, exec_res.text
        assert exec_res.json()["status"] == "channel_assigning"
        assert calls["count"] == 1


class TestActiveModeValidToken:
    def test_create_and_execute_succeed_with_fresh_saved_token(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_saved(client, ws.id)
        assert token["is_saved_policy"] is True

        res = _create(client, ws.id, monkeypatch, **_token_fields(token))
        assert res.status_code == 201, res.text
        run_id = res.json()["id"]

        calls = _mock_celery(monkeypatch)
        exec_res = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert exec_res.status_code == 200, exec_res.text
        assert exec_res.json()["status"] == "channel_assigning"
        assert calls["count"] == 1

    def test_create_refused_without_token(
        self, client, db_session, make_workspace, monkeypatch
    ):
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()

        res = _create(client, ws.id, monkeypatch)
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LOCATION_PREVIEW_REQUIRED"
        assert db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count() == 0

    def test_stale_token_from_active_policy_rejected_after_downgrade_to_none(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """QA blocker regresyon testi (preview->create yarışı): kullanıcı
        `exclude_all` iken geçerli bir önizleme token'ı alır; create
        çağrılmadan ÖNCE (başka bir sekmede) kayıtlı policy `none`'a
        düşürülür. Eski dış koşul (`if _saved_mode != MODE_NONE`) canlı
        mode artık `none` olduğu için token'ı SESSİZCE yok sayar ve
        filtresiz bir run oluştururdu — kullanıcı `exclude_all`'a karşı
        yetkilendirdiği run'ın hiç filtrelenmediğini fark etmezdi. Düzeltme:
        mode `none`YKEN bile bir token varsa STALE ile reddedilir; run hiç
        OLUŞMAZ."""
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_saved(client, ws.id)
        assert token["is_saved_policy"] is True

        # Policy DÜŞÜRÜLÜR: exclude_all -> none — token artık ESKİ bir
        # policy'ye karşı üretilmiş durumda.
        ws.profile_data = {**ws.profile_data, "location_filter_mode": "none"}
        db_session.commit()

        runs_before = db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count()

        res = _create(client, ws.id, monkeypatch, **_token_fields(token))
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["code"] == "LOCATION_PREVIEW_STALE"

        # KİLİT İDDİA: run HİÇ OLUŞMADI (sayı öncesi/sonrası birebir aynı).
        runs_after = db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count()
        assert runs_after == runs_before == 0

    def test_no_token_with_none_mode_still_creates_without_gate(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """Regresyon: kayıtlı mode `none` VE hiçbir token gönderilmemişse
        (gerçek geriye uyumlu yol) create hâlâ SORUNSUZ çalışır — yeni
        `_has_location_token` kontrolü yalnız bir token VARSA devreye
        girer, boş alanları STALE'e çevirmez."""
        ws = _setup_workspace(make_workspace)  # location alanı yok -> none
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        res = _create(client, ws.id, monkeypatch)
        assert res.status_code == 201, res.text
        assert db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count() == 1

    def test_explicit_false_saved_policy_flag_with_no_fingerprints_still_rejected(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """QA doğruluk düzeltmesi: `_has_location_token` TRUTHY değil VARLIK
        kontrolü yapmalı. İstek yalnız
        `location_preview_is_saved_policy=False` taşıyor (fingerprint'ler
        YOK) — bu, tam olarak yakalanmak istenen draft-kaynaklı durumdur;
        eski `any((a, b, c))` bunu `False` değeri "boş/yok" sayıp
        SESSİZCE `none -> none` geriye uyumlu yoluna düşürürdü. Doğru
        davranış: alan AÇIKÇA GÖNDERİLDİ, dolayısıyla STALE ile reddedilir
        ve run oluşmaz."""
        ws = _setup_workspace(make_workspace)  # kayıtlı mode -> none
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        runs_before = db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count()

        res = _create(
            client, ws.id, monkeypatch,
            location_preview_is_saved_policy=False,
        )
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["code"] == "LOCATION_PREVIEW_STALE"

        runs_after = db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count()
        assert runs_after == runs_before == 0


class TestExecuteCreateRace:
    def test_universe_changed_before_execute_fails_closed(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_saved(client, ws.id)
        res = _create(client, ws.id, monkeypatch, **_token_fields(token))
        assert res.status_code == 201, res.text
        run_id = res.json()["id"]

        # Evren degisir: create sonrasi yeni bir keyword eklenir.
        make_keyword("mobil uygulama gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        _boom_celery(monkeypatch)
        tasks_before = db_session.query(TaskResult).count()

        exec_res = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert exec_res.status_code == 409, exec_res.text
        assert exec_res.json()["detail"]["code"] == "LOCATION_PREVIEW_STALE"

        db_session.expire_all()
        run = db_session.get(ScoringRun, run_id)
        assert run.status == "failed"
        assert db_session.query(TaskResult).count() == tasks_before

    def test_saved_policy_changed_before_execute_fails_closed(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_saved(client, ws.id)
        res = _create(client, ws.id, monkeypatch, **_token_fields(token))
        assert res.status_code == 201, res.text
        run_id = res.json()["id"]

        # Kayitli policy degisir: exclude_all -> focus_only.
        ws.profile_data = {
            **ws.profile_data,
            "location_filter_mode": MODE_FOCUS_ONLY,
            "focus_cities": ["İstanbul"],
        }
        db_session.commit()

        _boom_celery(monkeypatch)
        tasks_before = db_session.query(TaskResult).count()

        exec_res = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert exec_res.status_code == 409, exec_res.text
        assert exec_res.json()["detail"]["code"] == "LOCATION_PREVIEW_STALE"

        db_session.expire_all()
        run = db_session.get(ScoringRun, run_id)
        assert run.status == "failed"
        assert db_session.query(TaskResult).count() == tasks_before

    def test_policy_downgraded_to_none_before_execute_fails_closed(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """QA blocker regresyon testi (race hole): create sırasında
        kayıtlı policy `exclude_all` ve token geçerliyken run oluşturulur;
        execute'tan ÖNCE profil `none`'a DÜŞÜRÜLÜR. Eski dış koşul
        (`if _saved_mode != MODE_NONE`) canlı mode artık `none` olduğu için
        kapıyı TAMAMEN atlar ve run'ın mühürlü token'a karşı hiç
        doğrulanmadan geçmesine izin verirdi — downgrade ile kapı bypass
        edilebiliyordu. Düzeltme: run bir `location_preview_gate` taşıyorsa
        kapı YİNE çalışır, canlı mode ne olursa olsun."""
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_saved(client, ws.id)
        assert token["is_saved_policy"] is True
        res = _create(client, ws.id, monkeypatch, **_token_fields(token))
        assert res.status_code == 201, res.text
        run_id = res.json()["id"]

        # Policy DÜŞÜRÜLÜR: exclude_all -> none (canlı mode artık aktif
        # DEĞİL — eski dış koşulun deliği tam burada açılıyordu).
        ws.profile_data = {**ws.profile_data, "location_filter_mode": "none"}
        db_session.commit()

        _boom_celery(monkeypatch)
        tasks_before = db_session.query(TaskResult).count()

        exec_res = client.post(
            f"/api/v1/scoring/runs/{run_id}/execute",
            params={"brand_profile_id": ws.id},
        )
        assert exec_res.status_code == 409, exec_res.text
        assert exec_res.json()["detail"]["code"] == "LOCATION_PREVIEW_STALE"

        # Re-query — session'daki nesneye değil, DB'nin GERÇEK durumuna güven.
        db_session.expire_all()
        run = db_session.query(ScoringRun).filter(
            ScoringRun.id == run_id).first()
        assert run.status == "failed"
        assert db_session.query(TaskResult).count() == tasks_before


class TestDraftTokenRejected:
    def test_draft_preview_token_rejected_even_when_values_match_saved(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """Draft istekte deger KAYITLIYLA AYNI olsa bile (SOURCE bazlı kural):
        run yetkilendirmesi reddedilir (plan §5.7)."""
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_draft(
            client, ws.id, location_filter_mode=MODE_EXCLUDE_ALL)
        assert token["is_saved_policy"] is False

        _boom_celery(monkeypatch)
        res = _create(client, ws.id, monkeypatch, **_token_fields(token))
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "LOCATION_PREVIEW_DRAFT_REJECTED"
        assert db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count() == 0

    def test_spoofed_saved_flag_still_rejected_by_independent_fingerprint_check(
        self, client, db_session, make_workspace, make_keyword, monkeypatch
    ):
        """Guvenlik client'in bildirdigi boolean'a DEGIL, sunucunun bagimsiz
        yeniden hesapladigi fingerprint karsilastirmasina dayanir: draft
        deger kayitliyla FARKLIYSA `is_saved_policy=True` diye yalan
        soylense bile STALE ile reddedilir."""
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        db_session.commit()
        make_keyword("yazilim gelistirme", brand_profile_id=ws.id)
        db_session.commit()

        token = _preview_draft(
            client, ws.id, location_filter_mode=MODE_FOCUS_ONLY,
            focus_cities=["İstanbul"])
        assert token["is_saved_policy"] is False

        _boom_celery(monkeypatch)
        res = _create(
            client, ws.id, monkeypatch,
            location_universe_fingerprint=token["universe_fingerprint"],
            location_policy_fingerprint=token["location_policy_fingerprint"],
            location_preview_is_saved_policy=True,  # SPOOF
        )
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "LOCATION_PREVIEW_STALE"
        assert db_session.query(ScoringRun).filter(
            ScoringRun.brand_profile_id == ws.id).count() == 0


def _seal_and_advance_to_channel_assigned(db_session, ws, run):
    """Yardımcı: run'ı dondur + KAYITLI policy'yle mühürle + GERÇEK state
    machine ile pipeline'ın ULAŞTIĞI terminal duruma (`channel_assigned`)
    ilerlet.

    QA bulgusu (Faz 6a incelemesi): orchestrator HİÇBİR ZAMAN 'completed'
    yazmaz — gerçek terminal `channel_assigned`dır
    (`app/core/engine/orchestrator.py:418-420`). Önceki sürüm burada
    `run.status = "completed"` diye ELLE atıyordu; bu, production'ın asla
    üretmediği bir durumu test ediyordu (sahte-yeşil). Artık
    `app.core.scoring.state_machine.transition()` ile GERÇEK ara
    durumlardan (`scoring` -> `scored` -> `channel_assigning` ->
    `channel_assigned`) geçiriliyor — doğrudan string ataması YOK.

    `make_scoring_run(status="channel_assigned")` KULLANILMAZ — fixture o
    statüde OTOMATİK bir "manifest_schema_version"sız engine_v3 ön-mührü
    yazar (plan §5.4'ten ÖNCEKİ eski sözleşme simülasyonu) ve bu, buradaki
    GERÇEK `seal_manifest` çağrısıyla çakışıp `StageContextMismatch`
    fırlatırdı. Bu yüzden run `pending` statüsüyle yaratılıp mühür BURADA
    elle basılır, ardından state machine ile ilerletilir."""
    from app.core.engine.context import (
        build_firm_profile, firm_block, firm_block_sha256,
        freeze_universe_snapshot,
    )
    from app.core.engine.persistence import seal_manifest
    from app.core.policy.location_policy import policy_snapshot
    from app.core.scoring.state_machine import transition

    run.algorithm_version = "v3"
    freeze_universe_snapshot(db_session, run)
    seal_manifest(
        run,
        firm_block_sha256=firm_block_sha256(firm_block(build_firm_profile(ws))),
        algorithm_versions={"ads": "nihai_niche_v1"},
        models={"ads": "gemini-2.5"},
        prompt_shas={"ads": "sha_ads"},
        location_policy=policy_snapshot(ws.profile_data),
    )
    db_session.commit()

    for target in ("scoring", "scored", "channel_assigning", "channel_assigned"):
        transition(db_session, run, target=target)


class TestLocationAuditEndpoint:
    @pytest.mark.parametrize("non_terminal_status", ["pending", "scoring"])
    def test_non_terminal_run_returns_409(
        self, client, db_session, make_workspace, make_scoring_run,
        non_terminal_status,
    ):
        """Genuinely henüz bitmemiş bir run (pending/scoring) — üretim
        pipeline'ının ASLA bu statüde bırakmayacağı bir 'completed' takma
        adı değil, GERÇEK ara durumlar — 409 döner."""
        ws = _setup_workspace(make_workspace)
        db_session.commit()
        run = make_scoring_run(
            brand_profile_id=ws.id, status=non_terminal_status)

        res = client.get(AUDIT_PATH.format(id=run.id),
                         params={"brand_profile_id": ws.id})
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "RUN_NOT_COMPLETED"

    def test_works_for_channel_assigned_run_the_real_pipeline_terminal(
        self, client, db_session, make_workspace, make_scoring_run,
        make_keyword,
    ):
        """Production'ın gerçekte çarptığı durum: orchestrator'ın ürettiği
        `channel_assigned` — `completed` DEĞİL. Bu test, run'ı GERÇEK state
        machine geçişleriyle o duruma taşıyıp endpoint'in 200 döndüğünü
        doğrudan kanıtlar (QA blocker'ının regresyon testi)."""
        ws = _setup_workspace(make_workspace)
        make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
        db_session.commit()

        run = make_scoring_run(brand_profile_id=ws.id)
        _seal_and_advance_to_channel_assigned(db_session, ws, run)

        db_session.expire_all()
        fresh_run = db_session.get(ScoringRun, run.id)
        assert fresh_run.status == "channel_assigned"

        res = client.get(AUDIT_PATH.format(id=run.id),
                         params={"brand_profile_id": ws.id})
        assert res.status_code == 200, res.text

    def test_uses_sealed_policy_even_when_live_profile_changed(
        self, client, db_session, make_workspace, make_scoring_run,
        make_keyword,
    ):
        ws = _setup_workspace(
            make_workspace, location={"location_filter_mode": MODE_EXCLUDE_ALL})
        kw_city = make_keyword("ankara ofis kiralama", brand_profile_id=ws.id)
        kw_plain = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
        db_session.commit()

        run = make_scoring_run(brand_profile_id=ws.id)
        _seal_and_advance_to_channel_assigned(db_session, ws, run)

        # Canli profil SONRADAN degisir: exclude_all -> none. Audit'in bunu
        # GORMEZDEN gelip muhurlu exclude_all'i raporlamasi beklenir.
        ws.profile_data = {**ws.profile_data, "location_filter_mode": "none"}
        db_session.commit()

        res = client.get(AUDIT_PATH.format(id=run.id),
                         params={"brand_profile_id": ws.id, "limit": 100})
        assert res.status_code == 200, res.text
        body = res.json()

        assert body["mode"] == MODE_EXCLUDE_ALL
        by_id = {row["keyword_id"]: row for row in body["rows"]}
        assert by_id[kw_city.id]["is_kept"] is False
        assert by_id[kw_city.id]["reason_code"] == "LOCATION_CITY_FILTER"
        assert by_id[kw_city.id]["matched_city"] == "Ankara"
        assert by_id[kw_city.id]["matched_exempt_term"] is None
        assert by_id[kw_plain.id]["is_kept"] is True
        assert by_id[kw_plain.id]["reason_code"] is None
        assert by_id[kw_plain.id]["matched_city"] is None

    def test_reports_exemption_protected_keyword_and_bounds_pagination(
        self, client, db_session, make_workspace, make_scoring_run,
        make_keyword,
    ):
        ws = _setup_workspace(
            make_workspace,
            location={
                "location_filter_mode": MODE_EXCLUDE_ALL,
                "location_exempt_terms": ["gaziantep fıstığı"],
            },
        )
        city_kws = [
            make_keyword(f"ankara ofis kiralama {i}", brand_profile_id=ws.id)
            for i in range(5)
        ]
        exempt_kw = make_keyword(
            "gaziantep fıstığı fiyat", brand_profile_id=ws.id)
        plain_kw = make_keyword("ofis kiralama hizmeti", brand_profile_id=ws.id)
        db_session.commit()

        run = make_scoring_run(brand_profile_id=ws.id)
        _seal_and_advance_to_channel_assigned(db_session, ws, run)

        # Tam liste (tek sayfada) — nedenler/eslesmeler dogru mu.
        full = client.get(AUDIT_PATH.format(id=run.id),
                          params={"brand_profile_id": ws.id, "limit": 100})
        assert full.status_code == 200
        full_body = full.json()
        assert full_body["total_rows"] == 7
        assert full_body["excluded_count"] == 5
        assert full_body["kept_count"] == 2
        by_id = {row["keyword_id"]: row for row in full_body["rows"]}
        assert by_id[exempt_kw.id]["is_kept"] is True
        # Muhurlu manifest'teki `exempt_terms` NORMALIZE edilmis haliyle
        # saklanir (policy_snapshot sozlesmesi) — audit bunu OLDUGU GIBI
        # dondurur, onizleme endpoint'inin orijinal-casing ornekleriyle
        # KARISTIRILMAMALI.
        assert (by_id[exempt_kw.id]["matched_exempt_term"]
                == normalize_location_text("gaziantep fıstığı"))
        assert by_id[plain_kw.id]["is_kept"] is True
        for kw in city_kws:
            assert by_id[kw.id]["is_kept"] is False
            assert by_id[kw.id]["reason_code"] == "LOCATION_CITY_FILTER"
            assert by_id[kw.id]["matched_city"] == "Ankara"

        # Sayfalama: sinirsiz liste asla donmez.
        page1 = client.get(AUDIT_PATH.format(id=run.id), params={
            "brand_profile_id": ws.id, "limit": 3, "offset": 0})
        page2 = client.get(AUDIT_PATH.format(id=run.id), params={
            "brand_profile_id": ws.id, "limit": 3, "offset": 3})
        assert len(page1.json()["rows"]) == 3
        assert len(page2.json()["rows"]) == 3
        ids1 = {r["keyword_id"] for r in page1.json()["rows"]}
        ids2 = {r["keyword_id"] for r in page2.json()["rows"]}
        assert ids1.isdisjoint(ids2)
        # Toplam sayaclar sayfa boyutundan bagimsiz her zaman TUM evreni yansitir.
        assert page1.json()["total_rows"] == 7
        assert page1.json()["excluded_count"] == 5

        # only_excluded=True yalniz elenenleri dondurur.
        excluded_only = client.get(AUDIT_PATH.format(id=run.id), params={
            "brand_profile_id": ws.id, "limit": 100, "only_excluded": True})
        excluded_rows = excluded_only.json()["rows"]
        assert len(excluded_rows) == 5
        assert all(not r["is_kept"] for r in excluded_rows)

    def test_unknown_run_returns_404(self, client, make_workspace):
        ws = make_workspace()
        res = client.get(AUDIT_PATH.format(id=999_999),
                         params={"brand_profile_id": ws.id})
        assert res.status_code == 404
