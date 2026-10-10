"""
Motor v3 tablo migrasyonu testleri
(`migrations/versions/20260918_001_add_engine_tables.py`).

Kalıp `tests/integration/test_migration_chain.py`'den BİREBİR alınmıştır
(fresh_schema fixture'ı): boş şemadan `alembic upgrade head` ile iki yeni
tablonun (`engine_stage_results`, `engine_selections`) oluştuğunu ve
kısıtlarının yerinde olduğunu kanıtlar. Ayrıca migration'ın
`20260730_001_add_corpus_screening.py` testindeki idempotent kalıpla
tekrar koşulunca patlamadığını doğrular. Var olan test dosyasına
DOKUNULMAZ; bu ayrı bir dosyadır.
"""
from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database.models import EngineSelection

_NEW_TABLES = ("engine_stage_results", "engine_selections")


@pytest.fixture(scope="module")
def fresh_schema(db_engine):
    """Drop all tables and re-run `alembic upgrade head` from scratch.

    Bkz. `tests/integration/test_migration_chain.py::fresh_schema` — aynı
    kalıp, ayrı modül (fixture'lar modül seviyesinde izole çalışır).
    """
    with db_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()

    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd="/app",
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    yield


# ---------------------------------------------------------------------------
# (a) Boş DB'den upgrade head -> iki tablo + kısıtlar
# ---------------------------------------------------------------------------


def test_engine_tables_created_from_empty_db(fresh_schema, db_engine):
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    missing = set(_NEW_TABLES) - tables
    assert not missing, f"eksik tablo(lar): {sorted(missing)}. mevcut: {sorted(tables)}"


def test_engine_stage_results_unique_constraint(fresh_schema, db_engine):
    inspector = inspect(db_engine)
    unique_constraints = inspector.get_unique_constraints("engine_stage_results")
    target = {"scoring_run_id", "stage", "scope_type", "scope_key"}
    found = any(
        target.issubset(set(uc.get("column_names", []))) for uc in unique_constraints
    )
    assert found, f"uq_engine_stage_results_scope bulunamadı: {unique_constraints}"


def test_engine_stage_results_scope_type_check_constraint(fresh_schema, db_engine):
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'engine_stage_results'::regclass AND contype = 'c'"
            )
        ).fetchall()
    names = {r[0] for r in rows}
    assert "ck_engine_stage_results_scope_type" in names, f"bulunan: {names}"


def test_engine_stage_results_index(fresh_schema, db_engine):
    inspector = inspect(db_engine)
    index_names = {idx["name"] for idx in inspector.get_indexes("engine_stage_results")}
    assert "idx_engine_stage_results_run_stage" in index_names, index_names


def test_engine_selections_unique_constraint(fresh_schema, db_engine):
    inspector = inspect(db_engine)
    unique_constraints = inspector.get_unique_constraints("engine_selections")
    target = {"scoring_run_id", "keyword_id", "channel"}
    found = any(
        target.issubset(set(uc.get("column_names", []))) for uc in unique_constraints
    )
    assert found, f"uq_engine_selections_run_keyword_channel bulunamadı: {unique_constraints}"


def test_engine_selections_check_constraints(fresh_schema, db_engine):
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'engine_selections'::regclass AND contype = 'c'"
            )
        ).fetchall()
    names = {r[0] for r in rows}
    assert "ck_engine_selections_channel" in names, f"bulunan: {names}"
    assert "ck_engine_selections_selected_xor_excluded" in names, f"bulunan: {names}"


def test_engine_selections_index(fresh_schema, db_engine):
    inspector = inspect(db_engine)
    index_names = {idx["name"] for idx in inspector.get_indexes("engine_selections")}
    assert "idx_engine_selections_run_channel_rank" in index_names, index_names


# ---------------------------------------------------------------------------
# (b) Tablolar zaten varken migration tekrar koşunca PATLAMAMALI (idempotent)
# ---------------------------------------------------------------------------


def test_engine_migration_is_idempotent_when_tables_already_exist(fresh_schema, db_engine):
    """Baseline squash create_all senaryosu: tablolar zaten var, migration'ın
    ikinci değerlendirmesi ne yeni nesne üretmeli ne de patlamalı."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "migrations" / "versions"
        / "20260918_001_add_engine_tables.py"
    )
    spec = importlib.util.spec_from_file_location("mig_20260918_001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    inspector = inspect(db_engine)
    before_tables = set(inspector.get_table_names())
    before_indexes = {
        t: {i["name"] for i in inspector.get_indexes(t)} for t in _NEW_TABLES
    }
    before_unique = {
        t: [set(uc.get("column_names", [])) for uc in inspector.get_unique_constraints(t)]
        for t in _NEW_TABLES
    }

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with db_engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()  # IKINCI kez — hiçbir şey değişmemeli, patlamamalı

    inspector = inspect(db_engine)
    assert set(inspector.get_table_names()) == before_tables
    for table, names in before_indexes.items():
        assert {i["name"] for i in inspector.get_indexes(table)} == names
    for table, uniques in before_unique.items():
        after = [set(uc.get("column_names", [])) for uc in inspector.get_unique_constraints(table)]
        assert after == uniques


# ---------------------------------------------------------------------------
# (c) ck_engine_selections_selected_xor_excluded gerçekten çalışıyor mu?
#
# Kısıt XOR'dur: `(final_rank IS NULL) <> (exclude_reason IS NULL)`. Bir
# satır ya SEÇİLMİŞ (final_rank dolu, exclude_reason boş) ya da ELENMİŞ
# (exclude_reason dolu, final_rank boş) olabilir — ikisi birden dolu YA DA
# ikisi birden boş olamaz.
# ---------------------------------------------------------------------------


def test_selected_xor_excluded_check_constraint_blocks_double_null(db_session, make_workspace, make_scoring_run, make_keyword):
    """final_rank NULL + exclude_reason NULL -> IntegrityError (DB'ye yazarak kanıtlanır)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("check kisitli kelime")

    bad = EngineSelection(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        channel="ADS",
        algorithm_rank=1,
        final_rank=None,
        exclude_reason=None,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_selected_xor_excluded_check_constraint_blocks_double_not_null(db_session, make_workspace, make_scoring_run, make_keyword):
    """YENİ vaka: final_rank VE exclude_reason AYNI ANDA dolu -> IntegrityError.

    XOR kısıtı öncekinden daha sıkıdır: eski `excluded_has_reason` kısıtı
    yalnız "exclude_reason doluysa final_rank boş olmalı" derdi ve teslim
    edilmiş (final_rank dolu) bir satırın AYNI ZAMANDA bir exclude_reason
    taşımasına izin verirdi. XOR bunu da engeller — bir satır ayni anda
    hem "seçildi" hem "elendi" görünemez.
    """
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("hem secilen hem elenen kelime")

    bad = EngineSelection(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        channel="ADS",
        algorithm_rank=1,
        final_rank=1,
        exclude_reason="policy_excluded",
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_selected_xor_excluded_check_constraint_allows_final_rank_only(db_session, make_workspace, make_scoring_run, make_keyword):
    """Karşı-kanıt: yalnız final_rank doluysa (seçilmiş satır) kısıt engellemez."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("secildi kelime")

    ok = EngineSelection(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        channel="ADS",
        algorithm_rank=1,
        final_rank=1,
        exclude_reason=None,
    )
    db_session.add(ok)
    db_session.commit()  # patlamamalı


def test_selected_xor_excluded_check_constraint_allows_exclude_reason_only(db_session, make_workspace, make_scoring_run, make_keyword):
    """Karşı-kanıt: yalnız exclude_reason doluysa (elenmiş satır) kısıt engellemez."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("elenen kelime")

    ok = EngineSelection(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        channel="SEO",
        algorithm_rank=5,
        final_rank=None,
        exclude_reason="policy_excluded",
    )
    db_session.add(ok)
    db_session.commit()  # patlamamalı


def test_engine_selections_channel_check_constraint(db_session, make_workspace, make_scoring_run, make_keyword):
    """Kanal beyaz listesi dışı değer de CHECK ile engellenmeli (bonus kısıt kanıtı)."""
    workspace = make_workspace()
    run = make_scoring_run(brand_profile_id=workspace.id)
    keyword = make_keyword("gecersiz kanal kelime")

    bad = EngineSelection(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        channel="EMAIL",  # ADS|SEO|SOCIAL dışı
        algorithm_rank=1,
        final_rank=1,
        exclude_reason=None,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
