"""Relevance fallback yasagi (plan_marka_profili_sadakati.md P1.2).

Embedding batch'i patladiginda `RelevanceScorer` 0.5 + method='fuzzy_fallback'
uretiyordu ve servis bu satirlari YAZIP "basardim" donuyordu. Sonuc: uydurma
notr skorlar ChannelPool siralamasina giriyor, run "taze" damgalaniyor ve
kullaniciya hicbir sinyal gitmiyordu.

Yalniz surum damgasini atlamak YETMEZ: `_finalize_pool_versions`
(channel_engine.py) kanal atamasi bitiminde run.relevance_anchor_version'i
yeniden yazar. Tek dogru davranis HIC YAZMAMAKTIR.

Bu dosya `refresh_keyword_relevance` icin ilk testleri getiriyor; sahte scorer
kullanilir, UCRETLI CAGRI YOK.
"""
import pytest

from app.core.relevance import RelevanceRefreshError, refresh_keyword_relevance
from app.database.models import KeywordRelevance


ANCHORS = ["dijital pazarlama ajansi"]
STALE_SCORE = 0.111
STALE_ANCHOR = "ESKI ANCHOR"


class _Scorer:
    """compute_relevance donusunu method listesinden kurar (pozisyonel)."""

    def __init__(self, methods):
        self._methods = methods
        self.calls = 0

    def compute_relevance(self, keywords, anchor_texts):
        self.calls += 1
        assert len(keywords) == len(self._methods), "test kurulumu tutarsiz"
        return [
            {
                "relevance_score": 0.9 if method == "embedding" else 0.5,
                "matched_anchor": anchor_texts[0],
                "method": method,
            }
            for method in self._methods
        ]


@pytest.fixture
def relevance_fixture(
    db_session, make_workspace, make_keyword, make_scoring_run, make_keyword_score,
):
    """Iki kelimeli, ONCEDEN relevance satiri olan bir run kurar.

    workspace.anchor_version = 5, run.relevance_anchor_version = 1 →
    "surum damgalandi mi" sorusu keskin olcuebilir.
    """
    workspace = make_workspace(
        name="Relevance WS",
        status="confirmed",
        profile_data={
            "company_name": "Digitus",
            "products": ["SEO"],
            "anchor_texts": ANCHORS,
        },
        anchor_version=5,
    )
    run = make_scoring_run(
        brand_profile_id=workspace.id, relevance_anchor_version=1,
    )
    keywords = [
        make_keyword("seo ajansi", brand_profile_id=workspace.id),
        make_keyword("google ads ajansi", brand_profile_id=workspace.id),
    ]
    for kw in keywords:
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw.id, ads_score=10)

    # Onceki basarili kosunun satiri — abort'ta AYNEN kalmali.
    db_session.add(KeywordRelevance(
        scoring_run_id=run.id,
        keyword_id=keywords[0].id,
        relevance_score=STALE_SCORE,
        matched_anchor=STALE_ANCHOR,
        method="embedding",
    ))
    db_session.commit()

    return workspace, run, keywords


def _relevance_rows(db_session, run_id):
    return (
        db_session.query(KeywordRelevance)
        .filter(KeywordRelevance.scoring_run_id == run_id)
        .all()
    )


def test_single_fallback_aborts_without_writing_any_row(
    db_session, relevance_fixture,
):
    """TEK kelime bile yedek yontemle skorlandiysa hicbir satir yazilmaz."""
    workspace, run, _ = relevance_fixture
    scorer = _Scorer(["embedding", "fuzzy_fallback"])

    with pytest.raises(RelevanceRefreshError) as exc_info:
        refresh_keyword_relevance(
            db_session, run, workspace,
            requested_anchor_version=5,
            scorer=scorer,
        )

    assert exc_info.value.fallback is True
    assert exc_info.value.version_changed is False
    # Mesaj eyleme donuk: kac kelime + yeniden deneme cagrisi
    assert "1/2" in str(exc_info.value)

    db_session.expire_all()
    rows = _relevance_rows(db_session, run.id)
    # 1) Eski satir SILINMEDI ve degismedi
    assert len(rows) == 1
    assert float(rows[0].relevance_score) == pytest.approx(STALE_SCORE)
    assert rows[0].matched_anchor == STALE_ANCHOR
    # 2) Yeni (0.5 / fuzzy_fallback) satir YAZILMADI
    assert all(r.method == "embedding" for r in rows)
    # 3) Surum damgalanmadi -> bir sonraki atama tazelemek zorunda
    db_session.refresh(run)
    assert int(run.relevance_anchor_version) == 1


def test_all_fallback_aborts(db_session, relevance_fixture):
    """Tamami yedek yontemse de ayni davranis (kismi degil, tam abort)."""
    workspace, run, _ = relevance_fixture
    scorer = _Scorer(["fuzzy_fallback", "fuzzy_fallback"])

    with pytest.raises(RelevanceRefreshError) as exc_info:
        refresh_keyword_relevance(
            db_session, run, workspace,
            requested_anchor_version=5,
            scorer=scorer,
        )

    assert exc_info.value.fallback is True
    assert "2/2" in str(exc_info.value)
    db_session.expire_all()
    assert len(_relevance_rows(db_session, run.id)) == 1
    db_session.refresh(run)
    assert int(run.relevance_anchor_version) == 1


def test_clean_embedding_run_still_writes_and_stamps(
    db_session, relevance_fixture,
):
    """Kontrol testi: yasak mutlu yolu BLOKE ETMIYOR."""
    workspace, run, keywords = relevance_fixture
    scorer = _Scorer(["embedding", "embedding"])

    result = refresh_keyword_relevance(
        db_session, run, workspace,
        requested_anchor_version=5,
        scorer=scorer,
    )

    assert result.failed == 0
    assert result.computed == 2
    assert result.average_relevance == pytest.approx(0.9)

    db_session.expire_all()
    rows = _relevance_rows(db_session, run.id)
    assert len(rows) == 2
    assert {float(r.relevance_score) for r in rows} == {0.9}
    assert all(r.matched_anchor == ANCHORS[0] for r in rows)
    db_session.refresh(run)
    assert int(run.relevance_anchor_version) == 5


def test_fallback_abort_does_not_rollback_caller_state(
    db_session, relevance_fixture,
):
    """Abort rollback CAGIRMAZ: kilit alinmadan once donuyoruz.

    Servis fallback dalinda `db.rollback()` cagirsaydi, ayni session'da
    calisan cagiranin (orn. kanal atama pipeline'i) bekleyen isi bosuna
    atilirdi. Surum-degisti dali rollback cagirir cunku FOR UPDATE kilidini
    birakmasi gerekir; fallback dalinda boyle bir kilit YOK.
    """
    workspace, run, _ = relevance_fixture
    run.run_name = "cagiranin bekleyen degisikligi"

    with pytest.raises(RelevanceRefreshError):
        refresh_keyword_relevance(
            db_session, run, workspace,
            requested_anchor_version=5,
            scorer=_Scorer(["embedding", "fuzzy_fallback"]),
        )

    assert run.run_name == "cagiranin bekleyen degisikligi"


def test_length_contract_still_checked_before_fallback_guard(
    db_session, relevance_fixture,
):
    """Uzunluk sozlesmesi fallback yasagindan ONCE calisir (mesaj ayrisir)."""
    workspace, run, _ = relevance_fixture

    class _ShortScorer:
        def compute_relevance(self, keywords, anchor_texts):
            return [{
                "relevance_score": 0.5,
                "matched_anchor": anchor_texts[0],
                "method": "fuzzy_fallback",
            }]

    with pytest.raises(RelevanceRefreshError) as exc_info:
        refresh_keyword_relevance(
            db_session, run, workspace,
            requested_anchor_version=5,
            scorer=_ShortScorer(),
        )

    assert exc_info.value.fallback is False
    assert "sonuç sayısı uyuşmuyor" in str(exc_info.value)
    db_session.expire_all()
    assert len(_relevance_rows(db_session, run.id)) == 1
