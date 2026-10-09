"""Codex v10-4: drift simülatörleri GERÇEK motora karşı kilitlenir.

Simülatör (app/core/site_analyzer/drift_metrics.py) yanlışsa E kabul
raporu geçersizdir — bu testler sentetik run üzerinde gerçek
_build_final_pools_v2 ve PoolBuilder.build_candidate_pools çıktısıyla
birebir karşılaştırır. Ek: v10-1 production pozisyonel eşleme testi
(aynı metinli farklı Keyword ID'leri relevance kaybetmez).
"""
import pytest

from app.core.site_analyzer.drift_metrics import (
    rank_avg,
    simulate_candidate_pools,
    simulate_final_pools,
    spearman_tie_aware,
)
from app.database.models import (
    ChannelCandidate,
    ChannelPool,
    IntentAnalysis,
    KeywordRelevance,
    PreFilterResult,
)


class TestRankAvg:
    def test_ties_get_average_rank(self):
        ranks = rank_avg([1.0, 2.0, 2.0, 3.0])
        assert list(ranks) == [0.0, 1.5, 1.5, 3.0]

    def test_all_equal(self):
        ranks = rank_avg([5.0, 5.0, 5.0])
        assert list(ranks) == [1.0, 1.0, 1.0]

    def test_spearman_perfect_and_reversed(self):
        a = {1: 0.1, 2: 0.2, 3: 0.3, 4: 0.4}
        b_same = dict(a)
        b_rev = {1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1}
        assert spearman_tie_aware(a, b_same) == pytest.approx(1.0)
        assert spearman_tie_aware(a, b_rev) == pytest.approx(-1.0)


def _seed_full_run(db_session, make_workspace, make_keyword,
                   make_scoring_run, make_keyword_score):
    """Sınıf/adjusted/hacim eksenleri gerçekten ayrıştıran sentetik run."""
    ws = make_workspace("Drift WS", status="confirmed")
    run = make_scoring_run(
        brand_profile_id=ws.id, status="channel_assigned",
        ads_capacity=3, seo_capacity=3, social_capacity=3,
    )
    specs = [
        # (text, ads, seo, social, rel, ads_class, volume, gt, ga)
        ("kelime bir", 40, 30, 50, 0.9, 2, 1000, True, False),
        ("kelime iki", 38, 28, 48, 0.8, 1, 5000, False, True),
        ("kelime uc", 36, 26, 46, 0.7, 2, 200, False, False),
        ("kelime dort", 34, 24, 44, 0.6, None, 300, True, True),
        ("kelime bes", 32, 22, 42, 0.5, 1, 300, False, False),
        ("kelime alti", 30, 20, 40, 0.4, -1, 800, True, False),
    ]
    kids = []
    for i, (text, ads, seo, social, rel, cls, vol, gt, ga) in enumerate(specs, 1):
        kw = make_keyword(text, brand_profile_id=ws.id)
        kids.append(kw.id)
        make_keyword_score(
            scoring_run_id=run.id, keyword_id=kw.id,
            ads_score=ads, seo_score=seo, social_score=social,
            ads_rank=i, seo_rank=i, social_rank=i,
            metrics_snapshot={"monthly_volume": vol,
                              "derived": {"h": 0.5}},
        )
        db_session.add(KeywordRelevance(
            scoring_run_id=run.id, keyword_id=kw.id,
            relevance_score=rel, matched_anchor="a", method="embedding",
        ))
        for channel in ("ADS", "SEO", "SOCIAL"):
            db_session.add(ChannelCandidate(
                scoring_run_id=run.id, keyword_id=kw.id, channel=channel,
                raw_score=10, rank_in_channel=i,
            ))
            db_session.add(IntentAnalysis(
                scoring_run_id=run.id, keyword_id=kw.id, channel=channel,
                intent_type="commercial", is_passed=True,
                gt=gt if channel == "SEO" else None,
                ga=ga if channel == "SEO" else None,
            ))
            db_session.add(PreFilterResult(
                scoring_run_id=run.id, keyword_id=kw.id, channel=channel,
                # sınıf ekseni yalnız ADS'te ayrışsın; -1 sınıfı elenmiş demek
                is_kept=(cls != -1) if channel == "ADS" else True,
                ai_class=cls if channel == "ADS" else (3 if channel == "SOCIAL" else None),
            ))
    db_session.commit()
    return run, kids


class TestFinalPoolSimulatorLockedToEngine:
    def test_simulator_reproduces_real_engine_selection(
        self, db_session, make_workspace, make_keyword,
        make_scoring_run, make_keyword_score,
    ):
        from app.core.channel.channel_engine import ChannelEngine
        from app.generators.ai_service import MockAIService

        run, _ = _seed_full_run(
            db_session, make_workspace, make_keyword,
            make_scoring_run, make_keyword_score,
        )

        engine = ChannelEngine(db_session, MockAIService())
        engine._build_final_pools_v2(run.id, run, relevance_coefficient=1.0)

        relevance_map = {
            r.keyword_id: float(r.relevance_score)
            for r in db_session.query(KeywordRelevance).filter(
                KeywordRelevance.scoring_run_id == run.id
            ).all()
        }
        sim = simulate_final_pools(db_session, run, relevance_map)

        for channel in ("ADS", "SEO", "SOCIAL"):
            actual = [
                cp.keyword_id
                for cp in db_session.query(ChannelPool)
                .filter(ChannelPool.scoring_run_id == run.id,
                        ChannelPool.channel == channel)
                .order_by(ChannelPool.final_rank)
                .all()
            ]
            simulated = [r["kid"] for r in sim[channel]]
            # SIRALI liste karşılaştırması (yalnız küme değil) — tie-break
            # zinciri dahil birebir aynı olmalı
            assert simulated == actual, channel


class TestCandidateSimulatorLockedToPoolBuilder:
    def test_simulator_reproduces_real_initial_candidates(
        self, db_session, make_workspace, make_keyword,
        make_scoring_run, make_keyword_score,
    ):
        from app.core.channel.pool_builder import PoolBuilder

        # PoolBuilder relevance yolu CONFIRMED profil ister
        ws = make_workspace(
            "Cand WS", status="confirmed",
            profile_data={"anchor_texts": ["tema"]},
        )
        run = make_scoring_run(
            brand_profile_id=ws.id, status="scored",
            ads_capacity=2, seo_capacity=2, social_capacity=2,
        )
        # 8 kelime, kapasite×3=6 kesmesi gerçekten uygulanır
        for i in range(1, 9):
            kw = make_keyword(f"aday {i}", brand_profile_id=ws.id)
            make_keyword_score(
                scoring_run_id=run.id, keyword_id=kw.id,
                ads_score=50 - i, seo_score=50 - i, social_score=50 - i,
                ads_rank=i, seo_rank=i, social_rank=i,
            )
            db_session.add(KeywordRelevance(
                scoring_run_id=run.id, keyword_id=kw.id,
                # tersine relevance: sıralama saf skordan farklılaşsın
                relevance_score=0.3 + 0.08 * i,
                matched_anchor="a", method="embedding",
            ))
        db_session.commit()

        PoolBuilder(db_session).build_candidate_pools(run.id, relevance_coefficient=1.0)

        relevance_map = {
            r.keyword_id: float(r.relevance_score)
            for r in db_session.query(KeywordRelevance).filter(
                KeywordRelevance.scoring_run_id == run.id
            ).all()
        }
        sim = simulate_candidate_pools(db_session, run, relevance_map)

        for channel in ("ADS", "SEO", "SOCIAL"):
            actual = [
                cc.keyword_id
                for cc in db_session.query(ChannelCandidate)
                .filter(ChannelCandidate.scoring_run_id == run.id,
                        ChannelCandidate.channel == channel)
                .order_by(ChannelCandidate.rank_in_channel)
                .all()
            ]
            assert sim[channel] == actual, channel
            assert len(actual) == 6  # kapasite×3 kesmesi uygulandı


class TestDuplicateTextRelevance:
    def test_duplicate_text_keywords_each_get_own_relevance(
        self, db_session, make_workspace, make_keyword,
        make_scoring_run, make_keyword_score, monkeypatch,
    ):
        """Codex v10-1: metin-tabanlı map aynı metinli farklı Keyword
        ID'lerini eziyordu (dev run-11: 'hisse takip' ID 4 relevance'sız).
        Pozisyonel eşlemede İKİ ID de kendi skorunu alır."""
        from app.api.v1.brand_profile import _run_relevance_computation

        ws = make_workspace(
            "Dup WS", status="confirmed",
            profile_data={"company_name": "T", "sector": "s",
                          "anchor_texts": ["tema"], "exclude_themes": []},
        )
        kw1 = make_keyword("hisse takip", brand_profile_id=ws.id)
        kw2 = make_keyword("hisse takip", brand_profile_id=ws.id)  # AYNI metin
        assert kw1.id != kw2.id
        run = make_scoring_run(brand_profile_id=ws.id, status="relevance_computing")
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw1.id,
                           ads_score=10, seo_score=10, social_score=10,
                           ads_rank=1, seo_rank=1, social_rank=1)
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw2.id,
                           ads_score=9, seo_score=9, social_score=9,
                           ads_rank=2, seo_rank=2, social_rank=2)

        class _PositionalFake:
            def __init__(self, *args, **kwargs):
                pass

            def compute_relevance(self, keywords, anchor_texts):
                # pozisyona göre FARKLI skor — metin-map'i ayırt edemez
                return [{
                    "keyword": kw,
                    "relevance_score": round(0.5 + 0.2 * i, 3),
                    "matched_anchor": "tema",
                    # P1.2 fallback yasagi: 'embedding' disi her deger
                    # BASARISIZ sayilir; bu fake BASARIYI taklit ediyor.
                    "method": "embedding",
                } for i, kw in enumerate(keywords)]

        monkeypatch.setattr(
            "app.core.site_analyzer.relevance_scorer.RelevanceScorer",
            _PositionalFake,
        )

        _run_relevance_computation(scoring_run_id=run.id)

        db_session.expire_all()
        rows = db_session.query(KeywordRelevance).filter(
            KeywordRelevance.scoring_run_id == run.id
        ).all()
        by_kid = {r.keyword_id: float(r.relevance_score) for r in rows}
        # HER İKİ ID de relevance aldı (eski bug: yalnız biri alırdı)
        assert set(by_kid) == {kw1.id, kw2.id}
        # ve skorlar pozisyonel (farklı) — metin-map olsaydı ikisi aynı olurdu
        assert by_kid[kw1.id] != by_kid[kw2.id]

    def test_sync_endpoint_duplicate_text_positional(
        self, client, db_session, make_workspace, make_keyword,
        make_scoring_run, make_keyword_score, monkeypatch,
    ):
        """Codex v11-düşük: sync yol (/relevance/compute) için de doğrudan
        regresyon — pozisyonel eşleme her iki ID'ye kendi skorunu verir."""
        ws = make_workspace(
            "DupSync WS", status="confirmed",
            profile_data={"company_name": "T", "sector": "s",
                          "anchor_texts": ["tema"], "exclude_themes": []},
        )
        kw1 = make_keyword("hisse takip", brand_profile_id=ws.id)
        kw2 = make_keyword("hisse takip", brand_profile_id=ws.id)
        run = make_scoring_run(brand_profile_id=ws.id, status="scored")
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw1.id,
                           ads_score=10, seo_score=10, social_score=10,
                           ads_rank=1, seo_rank=1, social_rank=1)
        make_keyword_score(scoring_run_id=run.id, keyword_id=kw2.id,
                           ads_score=9, seo_score=9, social_score=9,
                           ads_rank=2, seo_rank=2, social_rank=2)

        class _PositionalFake:
            def __init__(self, *args, **kwargs):
                pass

            def compute_relevance(self, keywords, anchor_texts):
                return [{
                    "keyword": kw,
                    "relevance_score": round(0.4 + 0.3 * i, 3),
                    "matched_anchor": "tema",
                    # P1.2 fallback yasagi: 'embedding' disi her deger
                    # BASARISIZ sayilir; bu fake BASARIYI taklit ediyor.
                    "method": "embedding",
                } for i, kw in enumerate(keywords)]

        monkeypatch.setattr(
            "app.core.site_analyzer.relevance_scorer.RelevanceScorer",
            _PositionalFake,
        )

        resp = client.post(
            f"/api/v1/brand-profile/runs/{run.id}/relevance/compute",
            params={"brand_profile_id": ws.id},
        )
        # V3 embedding relevance'ı kullanmaz (plan 1.4): sync uç artık
        # hesaplamaz. Pozisyonel eşleme servis katmanında kalır ve
        # yukarıdaki background ikizi (test_duplicate_text_keywords_each_get_
        # own_relevance) tarafından doğrulanır.
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "RELEVANCE_NOT_USED_BY_V3"

        db_session.expire_all()
        assert db_session.query(KeywordRelevance).filter(
            KeywordRelevance.scoring_run_id == run.id
        ).count() == 0


class TestShortResultContract:
    """Codex v11-orta: scorer kısa liste dönerse zip sessizce kelime
    düşürürdü — artık uyumsuzlukta ESKİ kayıtlar silinmeden durulur."""

    def _seed(self, make_workspace, make_keyword, make_scoring_run,
              make_keyword_score, db_session, *, status):
        ws = make_workspace(
            "Short WS", status="confirmed",
            profile_data={"company_name": "T", "sector": "s",
                          "anchor_texts": ["tema"], "exclude_themes": []},
        )
        kw1 = make_keyword("kelime a", brand_profile_id=ws.id)
        kw2 = make_keyword("kelime b", brand_profile_id=ws.id)
        run = make_scoring_run(brand_profile_id=ws.id, status=status)
        for i, kw in enumerate((kw1, kw2), 1):
            make_keyword_score(scoring_run_id=run.id, keyword_id=kw.id,
                               ads_score=10, seo_score=10, social_score=10,
                               ads_rank=i, seo_rank=i, social_rank=i)
        # ESKİ relevance kaydı — kısa sonuçta KORUNMALI
        db_session.add(KeywordRelevance(
            scoring_run_id=run.id, keyword_id=kw1.id,
            relevance_score=0.77, matched_anchor="eski", method="embedding",
        ))
        db_session.commit()
        return ws, run, kw1

    @staticmethod
    def _short_fake():
        class _ShortFake:
            def __init__(self, *args, **kwargs):
                pass

            def compute_relevance(self, keywords, anchor_texts):
                return [{  # 2 kelimeye 1 sonuç — sözleşme ihlali
                    "keyword": keywords[0],
                    "relevance_score": 0.9,
                    "matched_anchor": "tema",
                    # P1.2 fallback yasagi: 'embedding' disi her deger
                    # BASARISIZ sayilir; bu fake BASARIYI taklit ediyor.
                    "method": "embedding",
                }]
        return _ShortFake

    def test_sync_endpoint_aborts_and_preserves_old_rows(
        self, client, db_session, make_workspace, make_keyword,
        make_scoring_run, make_keyword_score, monkeypatch,
    ):
        ws, run, kw1 = self._seed(make_workspace, make_keyword,
                                  make_scoring_run, make_keyword_score,
                                  db_session, status="scored")
        monkeypatch.setattr(
            "app.core.site_analyzer.relevance_scorer.RelevanceScorer",
            self._short_fake(),
        )
        resp = client.post(
            f"/api/v1/brand-profile/runs/{run.id}/relevance/compute",
            params={"brand_profile_id": ws.id},
        )
        # V3 embedding relevance'ı kullanmaz (plan 1.4): sync uç artık
        # hesaplamaz (eskiden 502 abort); eski kayıt yine dokunulmaz.
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "RELEVANCE_NOT_USED_BY_V3"

        db_session.expire_all()
        old = db_session.query(KeywordRelevance).filter(
            KeywordRelevance.scoring_run_id == run.id
        ).one()
        assert old.keyword_id == kw1.id  # eski kayıt SİLİNMEDİ
        assert float(old.relevance_score) == pytest.approx(0.77)

    def test_background_marks_failed_and_preserves_old_rows(
        self, db_session, make_workspace, make_keyword,
        make_scoring_run, make_keyword_score, monkeypatch,
    ):
        from app.api.v1.brand_profile import _run_relevance_computation
        from app.database.models import ScoringRun

        ws, run, kw1 = self._seed(make_workspace, make_keyword,
                                  make_scoring_run, make_keyword_score,
                                  db_session, status="relevance_computing")
        monkeypatch.setattr(
            "app.core.site_analyzer.relevance_scorer.RelevanceScorer",
            self._short_fake(),
        )
        _run_relevance_computation(scoring_run_id=run.id)

        db_session.expire_all()
        assert db_session.get(ScoringRun, run.id).status == "failed"
        old = db_session.query(KeywordRelevance).filter(
            KeywordRelevance.scoring_run_id == run.id
        ).one()
        assert old.keyword_id == kw1.id
        assert float(old.relevance_score) == pytest.approx(0.77)