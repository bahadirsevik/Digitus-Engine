"""Profil analizinde eski sonucun yeni profili ezmesi (plan_yapilacaklar.md 2.2).

`brand_profiles.analysis_attempt_id`: her dispatch yeni token yazar; arka plan
task'ının (başarı VE hata) ve janitörün BÜTÜN yazımları token'a koşulludur.
Token'ı değişmiş bir attempt hiçbir şey yazamaz.

Testler gerçek test DB'sine karşı task fonksiyonlarını doğrudan sürer; AI ve
crawler sahtedir (ücretli çağrı yok). "Yarış" ayrı SessionLocal oturumlarıyla
kurulur: sahte extractor'ın AI adımı sırasında (task'ın kilitsiz çalıştığı
pencere) araya giren olay uygulanır, sonra task kendi bitiş yazımlarına devam
eder.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loguru import logger
from sqlalchemy import text

import app.core.site_analyzer.profile_extractor as extractor_module
import app.core.site_analyzer.stuck_janitor as janitor_module
import app.generators.ai_service as ai_service_module
from app.api.v1 import brand_profile as brand_profile_api
from app.core.site_analyzer.analysis_attempt import lock_workspace_row, start_attempt
from app.core.site_analyzer.stuck_janitor import (
    STUCK_ERROR_MESSAGE,
    fail_if_stuck,
    fail_stuck_profiles,
)
from app.database.connection import SessionLocal
from app.database.models import BrandProfile

# resolve_site_content kalite kapısından geçecek gerçekçi uzunlukta cache
CACHE = (
    "Hisse analiz platformu. Borsa Istanbul hisseleri icin teknik ve temel "
    "analiz araclari sunuyoruz. Portfoy takibi, hisse tarama, finansal "
    "tablo analizi ve gercek zamanli fiyat verisi ozelliklerimiz vardir. "
    "Yatirimcilar icin karar destek raporlari uretiyoruz."
)


def _profile(company_name: str) -> dict:
    return {
        "company_name": company_name,
        "sector": "Finansal teknoloji",
        "brand_summary": "Hisse analiz platformu",
        "target_audience": "Yatirimcilar",
        "products": ["Analiz Platformu"],
        "services": [],
        "use_cases": ["Borsa takibi"],
        "problems_solved": ["Dagitik veri"],
        "brand_terms": [company_name],
        "exclude_themes": ["kripto para"],
        "anchor_texts": ["Analiz Platformu", "Borsa takibi"],
    }


# ── yardımcılar ────────────────────────────────────────────────────


def _dispatch(workspace_id: int) -> str:
    """Gerçek dispatch kalıbı (ayrı oturum): kilit → running → yeni token → commit."""
    session = SessionLocal()
    try:
        workspace = lock_workspace_row(session, workspace_id)
        workspace.status = "running"
        workspace.error_message = None
        token = start_attempt(workspace)
        session.commit()
        return token
    finally:
        session.close()


def _snapshot(workspace_id: int) -> tuple:
    """Task'ların yazabileceği tüm alanların taze (ayrı oturum) görüntüsü."""
    session = SessionLocal()
    try:
        row = session.get(BrandProfile, workspace_id)
        return (
            row.status,
            row.error_message,
            row.analysis_attempt_id,
            row.suggested_keywords,
            row.profile_data,
            row.source_pages,
            row.crawl_content_cache,
            row.validation_data,
        )
    finally:
        session.close()


def _backdate(workspace_id: int, minutes: int) -> None:
    # onupdate tetiklenmesin diye ham SQL (test_profile_janitor.py ile aynı desen)
    session = SessionLocal()
    try:
        session.execute(
            text("UPDATE brand_profiles SET updated_at = :ts WHERE id = :pid"),
            {"ts": datetime.now(timezone.utc) - timedelta(minutes=minutes), "pid": workspace_id},
        )
        session.commit()
    finally:
        session.close()


def _janitor_fails_it(workspace_id: int) -> bool:
    """Okuma anı janitörü (ayrı oturum): 45 dk eski running satırı failed yapar."""
    _backdate(workspace_id, 45)
    session = SessionLocal()
    try:
        row = session.get(BrandProfile, workspace_id)
        return fail_if_stuck(session, row, stale_minutes=15)
    finally:
        session.close()


def _set_b_confirmed(workspace_id: int) -> None:
    """B koşusu bitti ve kullanıcı profili onayladı (confirmed + B verisi)."""
    session = SessionLocal()
    try:
        row = lock_workspace_row(session, workspace_id)
        row.status = "confirmed"
        row.error_message = None
        row.profile_data = _profile("B-Corp")
        row.suggested_keywords = ["b kw"]
        session.commit()
    finally:
        session.close()


class FakeAI:
    collector = None

    def close(self):
        return None


class FakeExtractor:
    """Sahte extractor. Her AI adımı `_ai_step` ile geçer: bir kerelik hook
    (araya giren olay) çalışır, `fail` ise hata fırlatır."""

    hook = None
    fail = False
    crawl_error = False
    ai_calls = 0

    def __init__(self, _ai):
        pass

    @classmethod
    def reset(cls):
        cls.hook = None
        cls.fail = False
        cls.crawl_error = False
        cls.ai_calls = 0

    def _fire_hook(self):
        hook = type(self).hook
        if hook is not None:
            type(self).hook = None  # bir kerelik (iç içe çağrı tekrar tetiklemesin)
            hook()

    def _ai_step(self):
        type(self).ai_calls += 1
        self._fire_hook()
        if type(self).fail:
            raise RuntimeError("sahte AI hatasi")

    def crawl_for_profile_content(self, _url):
        if type(self).crawl_error:
            self._fire_hook()
            return {"error": "site acilamadi", "site_content": "", "source_pages": None}
        return {"error": None, "site_content": CACHE, "source_pages": ["https://fresh.test"]}

    def suggest_keywords(self, _content, **_kwargs):
        self._ai_step()
        return ["kw one", "kw two"]

    def extract_profile_from_site_content(self, _content):
        self._ai_step()
        return _profile("A-Corp")

    def suggest_keywords_from_profile(self, _site, _profile_data, **_kwargs):
        self._ai_step()
        return ["kw one", "kw two"]

    def extract_profile_from_keywords(self, _site, _keywords, **_kwargs):
        self._ai_step()
        return _profile("A-Corp")

    def validate_with_competitors(self, _profile_data, _urls):
        return {"competitors": [], "consistency_score": 1.0}


@pytest.fixture(autouse=True)
def fake_ai_stack(monkeypatch):
    FakeExtractor.reset()
    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda **_kwargs: FakeAI())
    monkeypatch.setattr(extractor_module, "ProfileExtractor", FakeExtractor)
    yield
    FakeExtractor.reset()


# task adı → (çalıştırıcı, başlangıç alanları, normal bitiş durumu)
def _run_keyword_suggestion(ws_id, token):
    brand_profile_api._run_keyword_suggestion(
        workspace_id=ws_id, company_url="https://hissefy.test",
        competitor_urls=[], attempt_id=token,
    )


def _run_profile_analysis_first(ws_id, token):
    brand_profile_api._run_profile_analysis_first(
        workspace_id=ws_id, company_url="https://hissefy.test",
        competitor_urls=[], attempt_id=token,
    )


def _run_keyword_suggestion_from_profile(ws_id, token):
    brand_profile_api._run_keyword_suggestion_from_profile(ws_id, attempt_id=token)


def _run_profile_from_keywords(ws_id, token):
    brand_profile_api._run_profile_from_keywords(
        workspace_id=ws_id, keywords=["kw one", "kw two"], attempt_id=token,
    )


TASKS = {
    "keyword_suggestion": (_run_keyword_suggestion, "keywords_review"),
    "profile_analysis_first": (_run_profile_analysis_first, "profile_review"),
    "keyword_suggestion_from_profile": (_run_keyword_suggestion_from_profile, "keywords_review"),
    "profile_from_keywords": (_run_profile_from_keywords, "draft"),
}


def _make(make_workspace, *, with_cache: bool = True):
    """Task'ların hepsinin koşabileceği, henüz dispatch edilmemiş workspace."""
    return make_workspace(
        status="pending",
        onboarding_flow="profile_first",
        profile_data=_profile("Orijinal"),
        crawl_content_cache=CACHE if with_cache else None,
        suggested_keywords=["eski kw"],
    )


# ── mutlu yol: tek attempt normal sonuna ulaşır ─────────────────────


@pytest.mark.parametrize("task_name", list(TASKS))
def test_happy_path_single_attempt_reaches_normal_end_state(task_name, make_workspace):
    run, end_status = TASKS[task_name]
    ws = _make(make_workspace)
    token = _dispatch(ws.id)

    run(ws.id, token)

    status, error, token_after, *_ = _snapshot(ws.id)
    assert status == end_status
    assert error is None
    assert token_after == token  # başarılı bitişte token'a dokunulmaz


@pytest.mark.parametrize("task_name", list(TASKS))
def test_matching_token_failure_still_marks_failed(task_name, make_workspace):
    """Hata yazımı token eşleşince bugünkü gibi çalışır (failed + mesaj)."""
    run, _ = TASKS[task_name]
    ws = _make(make_workspace)
    token = _dispatch(ws.id)
    FakeExtractor.fail = True

    run(ws.id, token)

    status, error, token_after, *_ = _snapshot(ws.id)
    assert status == "failed"
    assert error
    assert token_after == token


@pytest.mark.parametrize("task_name", list(TASKS))
def test_matching_token_crawl_error_marks_failed(task_name, make_workspace):
    run, _ = TASKS[task_name]
    ws = _make(make_workspace, with_cache=False)
    token = _dispatch(ws.id)
    FakeExtractor.crawl_error = True

    run(ws.id, token)

    status, error, *_ = _snapshot(ws.id)
    assert status == "failed"
    assert "site acilamadi" in error


@pytest.mark.parametrize("task_name", list(TASKS))
def test_direct_call_without_token_still_works_on_null_token_row(task_name, make_workspace):
    """attempt_id=None (doğrudan çağrı) token'ı NULL satırda eski gibi çalışır."""
    run, end_status = TASKS[task_name]
    ws = _make(make_workspace)

    run(ws.id, None)

    assert _snapshot(ws.id)[0] == end_status


# ── eski attempt hiçbir şey yazamaz (başarı + hata × yeni koşu + janitör) ──


@pytest.mark.parametrize("task_name", list(TASKS))
@pytest.mark.parametrize("interferer", ["new_attempt", "janitor"])
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_stale_attempt_writes_nothing(task_name, interferer, outcome, make_workspace):
    run, _ = TASKS[task_name]
    ws = _make(make_workspace)
    token_a = _dispatch(ws.id)

    state_after_interference = {}

    def interfere():
        if interferer == "new_attempt":
            # Kullanıcı tekrar denedi: B başladı, bitti, profil onaylandı.
            state_after_interference["token_b"] = _dispatch(ws.id)
            _set_b_confirmed(ws.id)
        else:
            # 15 dk'yı aşan A okuma anında failed yapıldı (token döndürüldü).
            assert _janitor_fails_it(ws.id) is True
        state_after_interference["snapshot"] = _snapshot(ws.id)

    FakeExtractor.hook = interfere
    FakeExtractor.fail = outcome == "error"

    messages = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        run(ws.id, token_a)  # A, araya girişten SONRA kendi bitiş yazımına gelir
    finally:
        logger.remove(sink_id)

    # Boş geçme koruması: A gerçekten bitiş yazımına geldi ve KORUMA onu durdurdu
    expected_action = "'success'" if outcome == "success" else "'failed'"
    assert any("eskimiş" in m and expected_action in m for m in messages), messages

    after = _snapshot(ws.id)
    assert after == state_after_interference["snapshot"], "eski attempt satıra yazdı"
    assert after[2] != token_a  # token artık A'nın değil
    if interferer == "new_attempt":
        status, error, token, keywords, profile = after[:5]
        assert status == "confirmed"          # B'nin onaylı profili ezilmedi
        assert profile["company_name"] == "B-Corp"
        assert keywords == ["b kw"]
        assert token == state_after_interference["token_b"]
        assert error is None
    else:
        assert after[0] == "failed"
        assert after[1] == STUCK_ERROR_MESSAGE  # A'nın kendi hatası janitör mesajını ezmedi


@pytest.mark.parametrize("task_name", list(TASKS))
def test_stale_attempt_crawl_error_does_not_fail_new_run(task_name, make_workspace):
    """Erken (crawl hatası) failed yazımı da token'a koşulludur."""
    run, _ = TASKS[task_name]
    ws = _make(make_workspace, with_cache=False)
    token_a = _dispatch(ws.id)
    holder = {}

    def interfere():
        holder["token_b"] = _dispatch(ws.id)  # B çalışıyor (running)
        holder["snapshot"] = _snapshot(ws.id)

    FakeExtractor.crawl_error = True
    FakeExtractor.hook = interfere

    run(ws.id, token_a)

    after = _snapshot(ws.id)
    assert after == holder["snapshot"]
    assert after[0] == "running"       # B'nin durumu failed'a çevrilmedi
    assert after[1] is None
    assert after[2] == holder["token_b"]


@pytest.mark.parametrize("task_name", list(TASKS))
def test_attempt_superseded_before_start_does_no_work(task_name, make_workspace):
    """A başlamadan B dispatch edildiyse A 'running' geçişinde durur: AI harcanmaz."""
    run, _ = TASKS[task_name]
    ws = _make(make_workspace)
    token_a = _dispatch(ws.id)
    token_b = _dispatch(ws.id)
    assert token_a != token_b
    before = _snapshot(ws.id)

    run(ws.id, token_a)

    assert FakeExtractor.ai_calls == 0
    assert _snapshot(ws.id) == before


def test_stale_success_spec_scenario_real_b_run_then_confirm(make_workspace):
    """Spec senaryosu, gerçek task'larla: A başlar, B başlar, B biter ve profil
    onaylanır; sonra A'nın başarı yolu çalışır → profil/durum B'ninkiyle kalır."""
    ws = _make(make_workspace)
    token_a = _dispatch(ws.id)
    holder = {}

    def run_b_to_completion_and_confirm():
        class _BExtractor(FakeExtractor):
            def extract_profile_from_site_content(self, _content):
                return _profile("B-Corp")

        # B'nin task'ı kendi sahte extractor'ıyla gerçekten koşar
        extractor_module.ProfileExtractor = _BExtractor
        try:
            token_b = _dispatch(ws.id)
            brand_profile_api._run_profile_analysis_first(
                workspace_id=ws.id, company_url="https://hissefy.test",
                competitor_urls=[], attempt_id=token_b,
            )
        finally:
            extractor_module.ProfileExtractor = FakeExtractor
        holder["token_b"] = token_b
        session = SessionLocal()
        try:  # kullanıcı profili onayladı (confirm_workspace'in sonucu)
            row = lock_workspace_row(session, ws.id)
            assert row.status == "profile_review"
            row.status = "confirmed"
            session.commit()
        finally:
            session.close()
        holder["snapshot"] = _snapshot(ws.id)

    FakeExtractor.hook = run_b_to_completion_and_confirm

    _run_profile_analysis_first(ws.id, token_a)

    after = _snapshot(ws.id)
    assert after == holder["snapshot"]
    assert after[0] == "confirmed"
    assert after[4]["company_name"] == "B-Corp"
    assert after[2] == holder["token_b"]


# ── janitör yarışı ──────────────────────────────────────────────────


def test_fail_if_stuck_ignores_row_restarted_after_it_was_read(db_session, make_workspace):
    """Janitör eski koşuyu (A) okur; yazmadan önce B başlar → B etkilenmez."""
    ws = make_workspace(name="janitor-race-lazy", status="running")
    token_a = _dispatch(ws.id)
    _backdate(ws.id, 45)
    db_session.refresh(ws)           # janitörün gördüğü bayat kopya: A, 45 dk eski
    assert ws.analysis_attempt_id == token_a

    token_b = _dispatch(ws.id)       # okuma ile yazma arasında yeni koşu başladı

    assert fail_if_stuck(db_session, ws, stale_minutes=15) is False

    status, error, token, *_ = _snapshot(ws.id)
    assert status == "running"
    assert error is None
    assert token == token_b


def test_fail_if_stuck_ignores_row_finished_after_it_was_read(db_session, make_workspace):
    """Aynı token ama koşu okuma ile yazma arasında bitti → durum koşulu korur."""
    ws = make_workspace(name="janitor-race-finished", status="running")
    token_a = _dispatch(ws.id)
    _backdate(ws.id, 45)
    db_session.refresh(ws)

    session = SessionLocal()
    try:                              # A tam bu sırada başarıyla bitti
        row = lock_workspace_row(session, ws.id)
        row.status = "draft"
        session.commit()
    finally:
        session.close()

    assert fail_if_stuck(db_session, ws, stale_minutes=15) is False

    status, _error, token, *_ = _snapshot(ws.id)
    assert status == "draft"
    assert token == token_a


def test_fail_stuck_profiles_ignores_row_restarted_after_candidate_read(
    db_session, make_workspace, monkeypatch,
):
    """Startup taraması adayı (A) okur; yazmadan önce B başlar → B etkilenmez."""
    ws = make_workspace(name="janitor-race-startup", status="running")
    token_a = _dispatch(ws.id)
    _backdate(ws.id, 45)
    real_candidates = janitor_module._stuck_candidates
    holder = {}

    def candidates_then_restart(db, threshold):
        found = real_candidates(db, threshold)
        assert found == [(ws.id, token_a)]       # aday gerçekten A
        holder["token_b"] = _dispatch(ws.id)     # aday okundu, yazım henüz yok
        return found

    monkeypatch.setattr(janitor_module, "_stuck_candidates", candidates_then_restart)

    assert fail_stuck_profiles(db_session, stale_minutes=15) == 0

    status, error, token, *_ = _snapshot(ws.id)
    assert status == "running"
    assert error is None
    assert token == holder["token_b"]


def test_janitor_rotates_token_when_it_fails_a_row(db_session, make_workspace):
    ws = make_workspace(name="janitor-rotates", status="running")
    token_a = _dispatch(ws.id)
    _backdate(ws.id, 45)

    assert fail_stuck_profiles(db_session, stale_minutes=15) == 1

    status, error, token, *_ = _snapshot(ws.id)
    assert status == "failed"
    assert error == STUCK_ERROR_MESSAGE
    assert token and token != token_a


def test_janitor_still_fails_legacy_row_with_null_token(db_session, make_workspace):
    """Token'ı NULL eski satır (deploy öncesi running) hâlâ kurtarılır."""
    ws = make_workspace(name="janitor-null-token", status="running")
    assert ws.analysis_attempt_id is None
    _backdate(ws.id, 45)
    db_session.refresh(ws)

    assert fail_if_stuck(db_session, ws, stale_minutes=15) is True

    status, error, token, *_ = _snapshot(ws.id)
    assert (status, error) == ("failed", STUCK_ERROR_MESSAGE)
    assert token is not None


def test_janitor_failed_attempt_finishing_late_is_noop_via_real_endpoint_read(
    client, db_session, make_workspace,
):
    """GET /workspaces/{id} (okuma anı janitörü) A'yı öldürür; A geç bitince no-op."""
    ws = make_workspace(name="janitor-endpoint", status="running", profile_data=None,
                        onboarding_flow="profile_first")
    token_a = _dispatch(ws.id)
    _backdate(ws.id, 45)

    res = client.get(f"/api/v1/brand-profile/workspaces/{ws.id}")
    assert res.status_code == 200
    assert res.json()["status"] == "failed"
    before = _snapshot(ws.id)

    _run_profile_analysis_first(ws.id, token_a)   # A geç "bitti"

    assert FakeExtractor.ai_calls == 0
    assert _snapshot(ws.id) == before
    assert before[4] is None  # profile_data yazılmadı


# ── dispatch noktaları token yazar ve task'a geçirir ────────────────


def test_create_workspace_writes_token_and_passes_it_to_task(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_analysis_first", lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(brand_profile_api.settings, "ENABLE_SITE_PROFILE_ANALYSIS", True)

    res = client.post(
        "/api/v1/brand-profile/workspaces",
        json={"company_url": "https://hissefy.com", "flow_version": "profile_first"},
    )

    assert res.status_code == 201
    assert len(calls) == 1 and calls[0]["attempt_id"]
    assert _snapshot(res.json()["id"])[2] == calls[0]["attempt_id"]


def test_create_workspace_legacy_flow_writes_token_and_passes_it_to_task(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion", lambda **kw: calls.append(kw),
    )
    monkeypatch.setattr(brand_profile_api.settings, "ENABLE_SITE_PROFILE_ANALYSIS", True)

    res = client.post(
        "/api/v1/brand-profile/workspaces",
        json={"name": "Legacy", "company_url": "https://legacy.test"},
    )

    assert res.status_code == 201
    assert len(calls) == 1 and calls[0]["attempt_id"]
    assert _snapshot(res.json()["id"])[2] == calls[0]["attempt_id"]


def test_create_workspace_without_analysis_writes_no_token(client, monkeypatch):
    monkeypatch.setattr(brand_profile_api.settings, "ENABLE_SITE_PROFILE_ANALYSIS", False)

    res = client.post(
        "/api/v1/brand-profile/workspaces",
        json={"company_url": "https://hissefy.com", "flow_version": "profile_first"},
    )

    assert res.status_code == 201
    assert _snapshot(res.json()["id"])[2] is None


def test_keywords_approve_legacy_mints_fresh_token_each_dispatch(
    client, db_session, make_workspace, monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_profile_from_keywords",
        lambda workspace_id, keywords, attempt_id: calls.append(attempt_id),
    )
    ws = make_workspace(
        status="keywords_review", onboarding_flow="legacy",
        suggested_keywords=["hisse analiz"], profile_data=None,
    )

    first = client.put(
        f"/api/v1/brand-profile/workspaces/{ws.id}/keywords/approve",
        json={"keywords": ["teknik analiz"]},
    )
    assert first.status_code == 200 and first.json()["status"] == "running"
    token_1 = _snapshot(ws.id)[2]
    assert token_1 and calls == [token_1]

    # Aynı anda ikinci onay: satır artık running → ikinci dispatch YOK (400)
    second = client.put(
        f"/api/v1/brand-profile/workspaces/{ws.id}/keywords/approve",
        json={"keywords": ["teknik analiz"]},
    )
    assert second.status_code == 400
    assert calls == [token_1]
    assert _snapshot(ws.id)[2] == token_1

    # Janitör A'yı öldürdü → kullanıcı yeniden dener → YENİ token
    _backdate(ws.id, 45)
    db_session.expire_all()
    assert client.get(f"/api/v1/brand-profile/workspaces/{ws.id}").json()["status"] == "failed"
    retry = client.put(
        f"/api/v1/brand-profile/workspaces/{ws.id}/keywords/approve",
        json={"keywords": ["teknik analiz"]},
    )
    assert retry.status_code == 200
    token_2 = _snapshot(ws.id)[2]
    assert token_2 and token_2 != token_1
    assert calls == [token_1, token_2]


def test_profile_approve_rerun_mints_fresh_token(client, make_workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(
        brand_profile_api, "_run_keyword_suggestion_from_profile",
        lambda **kw: calls.append(kw),
    )
    ws = make_workspace(
        status="failed", onboarding_flow="profile_first",
        profile_data=_profile("Orijinal"), analysis_attempt_id="eski-token",
    )

    res = client.put(
        f"/api/v1/brand-profile/workspaces/{ws.id}/profile/approve",
        json={"rerun_keywords": True},
    )

    assert res.status_code == 200
    assert len(calls) == 1
    token = _snapshot(ws.id)[2]
    assert token and token != "eski-token"
    assert calls[0]["attempt_id"] == token
