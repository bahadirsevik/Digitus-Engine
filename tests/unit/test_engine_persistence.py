"""
Motor v3 asama sonucu yazımı + resume testleri (`app/core/engine/persistence.py`).

İki fail-closed kural test edilir (plan_algoritma_entegrasyonu.md §4):
  * YAZMA : doğrulama düşerse HİÇBİR satır yazılmaz.
  * RESUME: mevcut satır mühürlü bağlamla (model/prompt_sha/firm_block_sha256)
    uyuşmuyorsa sessiz yeniden kullanım YAPILMAZ — hata fırlatılır.

Codex sözleşme açığı (C): manifest TAMAMEN değişmezdir — `seal_manifest`
artık `prompt_shas` da mühürler ve dördü (firma hash'i, algoritma sürümleri,
modeller, prompt SHA'ları) BİRLİKTE mühürlenir; ikinci mühürleme yalnız
birebir aynı nesneyle kabul edilir. `write_stage_results`/`load_stage_results`
artık `scoring_run_id` değil `run` (ScoringRun) alıyor ve ÖNCE
`verify_stage_context` ile run'ın GERÇEK mühürlü bağlamını doğruluyor —
bu yüzden her yazma/okuma testinden önce ilgili stage mühürlenmelidir.
"""
from __future__ import annotations

import pytest

from app.core.policy.location_policy import policy_snapshot as _loc_snap
from app.core.engine.persistence import (
    MANIFEST_KEY,
    MANIFEST_SCHEMA_VERSION,
    SCOPE_TYPES,
    StageContext,
    StageContextMismatch,
    StageValidationError,
    load_stage_results,
    manifest_firm_block_sha,
    manifest_section,
    seal_manifest,
    validate_stage_entries,
    verify_stage_context,
    write_stage_results,
)
from app.database.models import EngineStageResult

CTX = StageContext(model="gemini-2.0-flash", prompt_sha="prompt-sha-abc", firm_block_sha256="firm-sha-xyz")


def _seal(run, *stages, model=CTX.model, prompt_sha=CTX.prompt_sha,
          firm_block_sha256=CTX.firm_block_sha256, algorithm_versions=None):
    """Test yardımcısı: verilen aşamalar için CTX ile eşleşen bir manifest mühürler.

    write_stage_results/load_stage_results artık run'ın mühürlü bağlamını
    ÖNCE doğruladığı için, mevcut testlerin çoğu bu mühürleme adımına
    ihtiyaç duyar (aksi halde StageContextMismatch alırlar).
    """
    return seal_manifest(
        run,
        firm_block_sha256=firm_block_sha256,
        algorithm_versions=algorithm_versions or {},
        models={stage: model for stage in stages},
        prompt_shas={stage: prompt_sha for stage in stages},
        location_policy=_loc_snap({}),
    )


# ---------------------------------------------------------------------------
# validate_stage_entries
# ---------------------------------------------------------------------------


def test_validate_stage_entries_missing_id_raises():
    with pytest.raises(StageValidationError):
        validate_stage_entries(
            {1: {"funnel": "transactional"}},
            expected_keys=[1, 2],
            required_fields=["funnel"],
            stage="ads_funnel",
        )


def test_validate_stage_entries_extra_id_raises():
    with pytest.raises(StageValidationError):
        validate_stage_entries(
            {1: {"funnel": "x"}, 2: {"funnel": "y"}, 3: {"funnel": "z"}},
            expected_keys=[1, 2],
            required_fields=["funnel"],
            stage="ads_funnel",
        )


def test_validate_stage_entries_non_dict_payload_raises():
    with pytest.raises(StageValidationError):
        validate_stage_entries(
            {1: "not-a-mapping"},
            expected_keys=[1],
            required_fields=["funnel"],
            stage="ads_funnel",
        )


def test_validate_stage_entries_required_field_none_raises():
    with pytest.raises(StageValidationError):
        validate_stage_entries(
            {1: {"funnel": None}},
            expected_keys=[1],
            required_fields=["funnel"],
            stage="ads_funnel",
        )


def test_validate_stage_entries_success_normalizes_int_keys_to_string():
    result = validate_stage_entries(
        {1: {"funnel": "transactional"}, 2: {"funnel": "informational"}},
        expected_keys=[1, 2],
        required_fields=["funnel"],
        stage="ads_funnel",
    )
    assert result == {"1": {"funnel": "transactional"}, "2": {"funnel": "informational"}}
    assert all(isinstance(key, str) for key in result)


# ---------------------------------------------------------------------------
# write_stage_results — fail-closed yazma
# ---------------------------------------------------------------------------


def test_write_stage_results_validation_failure_writes_nothing(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    with pytest.raises(StageValidationError):
        write_stage_results(
            db_session,
            run=run,
            stage="ads_funnel",
            scope_type="keyword",
            entries={1: {"funnel": "transactional"}},  # ID 2 eksik
            expected_keys=[1, 2],
            required_fields=["funnel"],
            context=CTX,
        )

    count = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .count()
    )
    assert count == 0


def test_write_stage_results_invalid_scope_type_raises(db_session, make_workspace, make_scoring_run):
    """scope_type kontrolü manifest doğrulamasından ÖNCE çalışır — mühür şart değil."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)

    with pytest.raises(StageValidationError):
        write_stage_results(
            db_session,
            run=run,
            stage="ads_funnel",
            scope_type="not_a_real_scope",
            entries={},
            expected_keys=[],
            required_fields=[],
            context=CTX,
        )


def test_write_stage_results_without_sealed_manifest_raises(db_session, make_workspace, make_scoring_run):
    """Run hiç mühürlenmemişse yazma reddedilir — resume/write muhurden önce olamaz."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)

    with pytest.raises(StageContextMismatch):
        write_stage_results(
            db_session,
            run=run,
            stage="ads_funnel",
            scope_type="keyword",
            entries={1: {"funnel": "transactional"}},
            expected_keys=[1],
            required_fields=["funnel"],
            context=CTX,
        )

    count = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .count()
    )
    assert count == 0


def test_write_stage_results_context_diverging_from_manifest_writes_nothing(
    db_session, make_workspace, make_scoring_run
):
    """Muhurden SAPAN baglamla yazma denemesi hicbir satir yazmaz (DB sorgusuyla kanit)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    diverging = StageContext(model="baska-model", prompt_sha=CTX.prompt_sha, firm_block_sha256=CTX.firm_block_sha256)
    with pytest.raises(StageContextMismatch):
        write_stage_results(
            db_session,
            run=run,
            stage="ads_funnel",
            scope_type="keyword",
            entries={1: {"funnel": "transactional"}},
            expected_keys=[1],
            required_fields=["funnel"],
            context=diverging,
        )

    count = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id)
        .count()
    )
    assert count == 0


def test_write_stage_results_success_writes_rows(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    entries = {
        1: {"funnel": "transactional", "relevance": 0.9},
        2: {"funnel": "informational", "relevance": 0.1},
    }
    written = write_stage_results(
        db_session,
        run=run,
        stage="ads_funnel",
        scope_type="keyword",
        entries=entries,
        expected_keys=[1, 2],
        required_fields=["funnel", "relevance"],
        context=CTX,
    )
    assert written == 2

    rows = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id, EngineStageResult.stage == "ads_funnel")
        .order_by(EngineStageResult.scope_key)
        .all()
    )
    assert [r.scope_key for r in rows] == ["1", "2"]
    assert rows[0].payload == {"funnel": "transactional", "relevance": 0.9}
    assert rows[0].model == CTX.model
    assert rows[0].prompt_sha == CTX.prompt_sha
    assert rows[0].firm_block_sha256 == CTX.firm_block_sha256


def test_write_stage_results_is_durable_across_sessions(
    db_session, make_workspace, make_scoring_run
):
    """Validated checkpoint survives caller rollback and a fresh DB session."""
    from app.database.connection import SessionLocal

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries={1: {"funnel": "transactional"}}, expected_keys=[1],
        required_fields=["funnel"], context=CTX,
    )
    # Bir worker exception/crash sonrasindaki yeni session gorunurlugunu
    # taklit eder. Cagiran rollback'i kalici checkpoint'i silememeli.
    db_session.rollback()
    other = SessionLocal()
    try:
        row = other.query(EngineStageResult).filter(
            EngineStageResult.scoring_run_id == run.id,
            EngineStageResult.stage == "ads_funnel",
            EngineStageResult.scope_key == "1",
        ).one()
        assert row.payload == {"funnel": "transactional"}
    finally:
        other.close()


def test_write_stage_results_second_call_same_context_is_idempotent(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()
    entries = {1: {"funnel": "transactional"}}

    write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1], required_fields=["funnel"], context=CTX,
    )
    written_again = write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1], required_fields=["funnel"], context=CTX,
    )
    assert written_again == 0

    count = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id, EngineStageResult.stage == "ads_funnel")
        .count()
    )
    assert count == 1


def test_write_stage_results_zero_written_does_not_commit_pending_changes(
    db_session, make_workspace, make_scoring_run
):
    """written == 0 (idempotent call) does not commit unrelated pending session changes."""
    from app.database.connection import SessionLocal
    from app.database.models import BrandProfile

    workspace = make_workspace(name="Original Name")
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    entries = {1: {"funnel": "transactional"}}
    written = write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1], required_fields=["funnel"], context=CTX,
    )
    assert written == 1

    # Session'da ilgisiz bir pending değişiklik yap (commit edilmemiş)
    workspace.name = "Pending Uncommitted Name"

    # Idempotent çağrı: written == 0 olmalı ve session commit ETMEMELİ
    written_zero = write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1], required_fields=["funnel"], context=CTX,
    )
    assert written_zero == 0

    # Ayrı bir DB session'ı ile bakıldığında değişiklik commit OLMAMIŞ olmalı
    other = SessionLocal()
    try:
        fresh_ws = other.get(BrandProfile, workspace.id)
        assert fresh_ws.name == "Original Name"
    finally:
        other.close()

    db_session.rollback()
    assert workspace.name == "Original Name"


def test_write_stage_results_commit_failure_rolls_back_and_leaves_session_usable(
    db_session, make_workspace, make_scoring_run, monkeypatch
):
    """Commit failure triggers rollback and does not leave session in PendingRollbackError."""
    from app.database.connection import SessionLocal

    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    entries = {2: {"funnel": "navigational"}}

    def _failing_commit():
        raise RuntimeError("Simulated DB commit error")

    monkeypatch.setattr(db_session, "commit", _failing_commit)

    with pytest.raises(RuntimeError, match="Simulated DB commit error"):
        write_stage_results(
            db_session, run=run, stage="ads_funnel", scope_type="keyword",
            entries=entries, expected_keys=[2], required_fields=["funnel"], context=CTX,
        )

    # Session rollback yapıldığı için session hala kullanılabilir (PendingRollbackError YOK)
    monkeypatch.undo()
    res = db_session.query(EngineStageResult).filter(
        EngineStageResult.scoring_run_id == run.id,
        EngineStageResult.stage == "ads_funnel",
        EngineStageResult.scope_key == "2",
    ).first()
    assert res is None  # Rollback yapıldı, kısmi kayıt kalmadı

    # Başka bir session'dan da kısmi kayıt görünmemeli
    other = SessionLocal()
    try:
        count = other.query(EngineStageResult).filter(
            EngineStageResult.scoring_run_id == run.id,
            EngineStageResult.scope_key == "2",
        ).count()
        assert count == 0
    finally:
        other.close()


def test_write_stage_results_different_context_raises_and_does_not_overwrite(
    db_session, make_workspace, make_scoring_run
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()
    entries = {1: {"funnel": "transactional"}}

    write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1], required_fields=["funnel"], context=CTX,
    )

    # Üç bağlam sapması da — model, prompt_sha, firm_block_sha256 — run'ın
    # GERÇEK mühürlü bağlamıyla uyuşmadığı için verify_stage_context'te
    # reddedilir (satıra hiç ulaşılmaz); gözlemlenebilir davranış aynı:
    # StageContextMismatch + satır bozulmadan kalır.
    different_model_ctx = StageContext(
        model="different-model", prompt_sha=CTX.prompt_sha, firm_block_sha256=CTX.firm_block_sha256
    )
    with pytest.raises(StageContextMismatch):
        write_stage_results(
            db_session, run=run, stage="ads_funnel", scope_type="keyword",
            entries={1: {"funnel": "commercial"}}, expected_keys=[1],
            required_fields=["funnel"], context=different_model_ctx,
        )

    different_prompt_ctx = StageContext(
        model=CTX.model, prompt_sha="different-prompt-sha", firm_block_sha256=CTX.firm_block_sha256
    )
    with pytest.raises(StageContextMismatch):
        write_stage_results(
            db_session, run=run, stage="ads_funnel", scope_type="keyword",
            entries={1: {"funnel": "commercial"}}, expected_keys=[1],
            required_fields=["funnel"], context=different_prompt_ctx,
        )

    different_firm_ctx = StageContext(
        model=CTX.model, prompt_sha=CTX.prompt_sha, firm_block_sha256="different-firm-sha"
    )
    with pytest.raises(StageContextMismatch):
        write_stage_results(
            db_session, run=run, stage="ads_funnel", scope_type="keyword",
            entries={1: {"funnel": "commercial"}}, expected_keys=[1],
            required_fields=["funnel"], context=different_firm_ctx,
        )

    # Orijinal satır bozulmadan kalmış olmalı (sessizce ezilmedi).
    row = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id, EngineStageResult.stage == "ads_funnel")
        .one()
    )
    assert row.payload == {"funnel": "transactional"}
    assert row.model == CTX.model
    assert row.prompt_sha == CTX.prompt_sha
    assert row.firm_block_sha256 == CTX.firm_block_sha256


def test_write_stage_results_scope_type_family_and_url_group(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "family_a1", "seo_urlgroup")
    db_session.commit()

    written_family = write_stage_results(
        db_session, run=run, stage="family_a1", scope_type="family",
        entries={"fam_1": {"label": "iskele"}}, expected_keys=["fam_1"],
        required_fields=["label"], context=CTX,
    )
    written_urlgroup = write_stage_results(
        db_session, run=run, stage="seo_urlgroup", scope_type="url_group",
        entries={"grp_a": {"members": [1, 2]}}, expected_keys=["grp_a"],
        required_fields=["members"], context=CTX,
    )
    assert written_family == 1
    assert written_urlgroup == 1

    family_row = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id, EngineStageResult.scope_type == "family")
        .one()
    )
    urlgroup_row = (
        db_session.query(EngineStageResult)
        .filter(EngineStageResult.scoring_run_id == run.id, EngineStageResult.scope_type == "url_group")
        .one()
    )
    assert family_row.scope_key == "fam_1"
    assert urlgroup_row.scope_key == "grp_a"
    assert set(SCOPE_TYPES) == {"keyword", "family", "url_group", "run"}


# ---------------------------------------------------------------------------
# load_stage_results — resume
# ---------------------------------------------------------------------------


def test_load_stage_results_returns_payloads_when_context_matches(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()
    entries = {1: {"funnel": "transactional"}, 2: {"funnel": "informational"}}
    write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1, 2], required_fields=["funnel"], context=CTX,
    )

    loaded = load_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword", context=CTX
    )
    assert loaded == {"1": {"funnel": "transactional"}, "2": {"funnel": "informational"}}


def test_load_stage_results_context_mismatch_raises_no_silent_reuse(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()
    entries = {1: {"funnel": "transactional"}}
    write_stage_results(
        db_session, run=run, stage="ads_funnel", scope_type="keyword",
        entries=entries, expected_keys=[1], required_fields=["funnel"], context=CTX,
    )

    stale_ctx = StageContext(model=CTX.model, prompt_sha=CTX.prompt_sha, firm_block_sha256="profil-degisti")
    with pytest.raises(StageContextMismatch):
        load_stage_results(
            db_session, run=run, stage="ads_funnel", scope_type="keyword", context=stale_ctx
        )


def test_load_stage_results_context_diverging_from_manifest_raises(db_session, make_workspace, make_scoring_run):
    """Muhurden SAPAN baglamla resume reddedilir — satir hic okunmadan biter."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    diverging = StageContext(model=CTX.model, prompt_sha="baska-prompt", firm_block_sha256=CTX.firm_block_sha256)
    with pytest.raises(StageContextMismatch):
        load_stage_results(
            db_session, run=run, stage="ads_funnel", scope_type="keyword", context=diverging
        )


def test_load_stage_results_filters_by_scope_type(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "mixed_stage")
    db_session.commit()
    write_stage_results(
        db_session, run=run, stage="mixed_stage", scope_type="keyword",
        entries={1: {"v": "kw"}}, expected_keys=[1], required_fields=["v"], context=CTX,
    )
    write_stage_results(
        db_session, run=run, stage="mixed_stage", scope_type="family",
        entries={"fam_1": {"v": "fam"}}, expected_keys=["fam_1"], required_fields=["v"], context=CTX,
    )

    keyword_only = load_stage_results(
        db_session, run=run, stage="mixed_stage", context=CTX, scope_type="keyword"
    )
    assert keyword_only == {"1": {"v": "kw"}}


def test_load_stage_results_requires_scope_type_keyword_argument(
    db_session, make_workspace, make_scoring_run
):
    """Faz 2: `scope_type` artik ZORUNLU pozisyonel-olmayan parametre —
    verilmezse Python'un kendisi TypeError firlatir (fonksiyon imzasi
    `*` sonrasi zorunlu keyword-only)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    with pytest.raises(TypeError):
        load_stage_results(db_session, run=run, stage="ads_funnel", context=CTX)


def test_load_stage_results_invalid_scope_type_raises_stage_validation_error(
    db_session, make_workspace, make_scoring_run
):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    with pytest.raises(StageValidationError, match="gecersiz scope_type"):
        load_stage_results(
            db_session, run=run, stage="ads_funnel",
            scope_type="not_a_real_scope", context=CTX,
        )


# ---------------------------------------------------------------------------
# seal_manifest / manifest_section / manifest_firm_block_sha
# ---------------------------------------------------------------------------


def test_seal_manifest_writes_engine_v3_and_preserves_existing_keys(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    run.execution_manifest = {"git_sha": "abc123", "some_other_key": {"nested": True}}
    db_session.commit()

    manifest = seal_manifest(
        run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
        models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-x"},
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    assert manifest["git_sha"] == "abc123"
    assert manifest["some_other_key"] == {"nested": True}
    assert manifest[MANIFEST_KEY]["firm_block_sha256"] == "sha-1"
    assert manifest[MANIFEST_KEY]["algorithm_versions"] == {"ads": "v1"}
    assert manifest[MANIFEST_KEY]["models"] == {"ads_funnel": "gemini-x"}
    assert manifest[MANIFEST_KEY]["prompt_shas"] == {"ads_funnel": "prompt-x"}

    db_session.refresh(run)
    assert run.execution_manifest["git_sha"] == "abc123"
    assert run.execution_manifest[MANIFEST_KEY]["firm_block_sha256"] == "sha-1"


def test_seal_manifest_reseal_with_identical_section_is_idempotent(db_session, make_workspace, make_scoring_run):
    """Birebir AYNI nesneyle ikinci mühürleme sorunsuz geçer (Codex açığı C)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    kwargs = dict(
        firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
        models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-x"},
        location_policy=_loc_snap({}),
    )
    seal_manifest(run, **kwargs)
    db_session.commit()

    manifest = seal_manifest(run, **kwargs)  # birebir aynı nesne — patlamamalı
    # Sema surumu 2: lokasyon politikasi muhrun AYRILMAZ parcasidir.
    assert manifest[MANIFEST_KEY] == {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "firm_block_sha256": "sha-1",
        "algorithm_versions": {"ads": "v1"},
        "models": {"ads_funnel": "gemini-x"},
        "prompt_shas": {"ads_funnel": "prompt-x"},
        "location_policy": _loc_snap({}),
    }


def test_seal_manifest_reseal_with_different_models_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    seal_manifest(
        run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
        models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-x"},
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    with pytest.raises(StageContextMismatch):
        seal_manifest(
            run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
            models={"ads_funnel": "gemini-y"}, prompt_shas={"ads_funnel": "prompt-x"},
            location_policy=_loc_snap({}),
        )


def test_seal_manifest_reseal_with_different_algorithm_versions_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    seal_manifest(
        run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
        models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-x"},
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    with pytest.raises(StageContextMismatch):
        seal_manifest(
            run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v2"},
            models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-x"},
            location_policy=_loc_snap({}),
        )


def test_seal_manifest_reseal_with_different_prompt_shas_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    seal_manifest(
        run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
        models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-x"},
        location_policy=_loc_snap({}),
    )
    db_session.commit()

    with pytest.raises(StageContextMismatch):
        seal_manifest(
            run, firm_block_sha256="sha-1", algorithm_versions={"ads": "v1"},
            models={"ads_funnel": "gemini-x"}, prompt_shas={"ads_funnel": "prompt-DIFFERENT"},
            location_policy=_loc_snap({}),
        )


def test_seal_manifest_reseal_with_different_sha_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    seal_manifest(run, firm_block_sha256="sha-1", algorithm_versions={}, models={}, prompt_shas={}, location_policy=_loc_snap({}))
    db_session.commit()

    with pytest.raises(StageContextMismatch):
        seal_manifest(run, firm_block_sha256="sha-2", algorithm_versions={}, models={}, prompt_shas={}, location_policy=_loc_snap({}))


def test_manifest_section_raises_when_never_sealed(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    with pytest.raises(StageContextMismatch):
        manifest_section(run)
    with pytest.raises(StageContextMismatch):
        manifest_firm_block_sha(run)


def test_manifest_firm_block_sha_returns_sealed_value(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    seal_manifest(run, firm_block_sha256="sha-abc", algorithm_versions={}, models={}, prompt_shas={}, location_policy=_loc_snap({}))
    assert manifest_firm_block_sha(run) == "sha-abc"


# ---------------------------------------------------------------------------
# verify_stage_context — asama baglami dogrudan dogrulama
# ---------------------------------------------------------------------------


def test_verify_stage_context_passes_when_matching(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    verify_stage_context(run, "ads_funnel", CTX)  # patlamamalı


def test_verify_stage_context_model_mismatch_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    wrong = StageContext(model="wrong-model", prompt_sha=CTX.prompt_sha, firm_block_sha256=CTX.firm_block_sha256)
    with pytest.raises(StageContextMismatch):
        verify_stage_context(run, "ads_funnel", wrong)


def test_verify_stage_context_prompt_sha_mismatch_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    wrong = StageContext(model=CTX.model, prompt_sha="wrong-prompt-sha", firm_block_sha256=CTX.firm_block_sha256)
    with pytest.raises(StageContextMismatch):
        verify_stage_context(run, "ads_funnel", wrong)


def test_verify_stage_context_firm_block_mismatch_raises(db_session, make_workspace, make_scoring_run):
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")
    db_session.commit()

    wrong = StageContext(model=CTX.model, prompt_sha=CTX.prompt_sha, firm_block_sha256="wrong-firm-sha")
    with pytest.raises(StageContextMismatch):
        verify_stage_context(run, "ads_funnel", wrong)


def test_verify_stage_context_unsealed_stage_raises(db_session, make_workspace, make_scoring_run):
    """Muhurde o asama icin kayitli model/prompt YOKSA da reddedilir."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    _seal(run, "ads_funnel")  # yalniz ads_funnel muhurlu
    db_session.commit()

    with pytest.raises(StageContextMismatch):
        verify_stage_context(run, "never_sealed_stage", CTX)
