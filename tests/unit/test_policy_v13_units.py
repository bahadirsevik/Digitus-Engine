"""Plan v13 birim testleri — DB'siz saf fonksiyonlar.

Kapsam: kanonik URL anahtarı (registrable domain), terim provenance
state-machine'i (status türetimi), URL reconciliation, legacy backfill,
topic tek-kaynak senkronu, freshness `!=` sözleşmesi, canonical anchor
fingerprint (grup sınırları korunur).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.core.policy.competitor_policy import (
    add_source_url,
    backfill_legacy_source_urls,
    is_legacy_term,
    normalize_competitor_url,
    reconcile_removed_urls,
    reconcile_url_decision,
    set_manual_approval,
)
from app.core.policy.freshness import compute_pool_freshness
from app.core.policy.review import canonical_anchor_fingerprint
from app.core.policy.topic_policy import (
    merge_normalized,
    parse_excluded_info,
    remove_normalized,
    sync_excluded_terms,
)


def _approved_terms(entries):
    return sorted(e["term"] for e in entries if e.get("status") == "approved")


# ── normalize_competitor_url ─────────────────────────────────────────

class TestNormalizeCompetitorUrl:
    def test_variants_same_canonical_key(self):
        # slash, www, port (bilinçli yok sayılır), büyük-küçük harf, path,
        # subdomain — hepsi aynı registrable domain'e düşer
        variants = [
            "https://fintables.com",
            "https://fintables.com/",
            "https://www.fintables.com",
            "HTTPS://FINTABLES.COM/path?q=1",
            "https://fintables.com:8443",
            "https://blog.fintables.com",
            "fintables.com",
        ]
        keys = {normalize_competitor_url(v) for v in variants}
        assert keys == {"fintables.com"}

    def test_second_level_tld(self):
        assert normalize_competitor_url("https://www.firma.com.tr/x") == "firma.com.tr"

    def test_invalid_inputs_empty(self):
        # Boş sonuç GEÇERSİZ demektir — asla eşleşme anahtarı olamaz
        for bad in ["", "   ", "not a url at all !!", "http://", "localhost"]:
            assert normalize_competitor_url(bad) == ""


# ── Provenance state machine ─────────────────────────────────────────

class TestTermProvenance:
    def test_manual_plus_url_lifecycle(self):
        # Kritik senaryo (Codex v4/v5): manuel onay + URL desteği birlikte →
        # manuel kaldırılınca URL varken approved KALIR; URL de kalkınca rejected
        entries = set_manual_approval(None, "fintables", True)
        entries = add_source_url(entries, "fintables", "https://fintables.com")
        assert _approved_terms(entries) == ["fintables"]

        entries = set_manual_approval(entries, "fintables", False)
        assert _approved_terms(entries) == ["fintables"]  # URL hâlâ destekliyor

        entries = reconcile_url_decision(entries, "https://fintables.com", None)
        assert _approved_terms(entries) == []  # iki katman da kalktı

    def test_manual_survives_url_removal(self):
        entries = set_manual_approval(None, "fintables", True)
        entries = add_source_url(entries, "fintables", "https://fintables.com")
        entries = reconcile_removed_urls(entries, [])
        assert _approved_terms(entries) == ["fintables"]  # manuel sahiplik korunur

    def test_term_correction_releases_old(self):
        # Marka adı A→B düzeltilince A serbest kalır, B onaylanır
        entries = reconcile_url_decision(None, "https://fintables.com", "fintables")
        entries = reconcile_url_decision(entries, "https://fintables.com", "fin tables")
        assert _approved_terms(entries) == ["fin tables"]

    def test_not_competitor_no_global_reject(self):
        # "Rakip değil" manuel onaylı AYNI terimi kapatmaz (global reject yok)
        entries = set_manual_approval(None, "fintables", True)
        entries = add_source_url(entries, "fintables", "https://fintables.com")
        entries = reconcile_url_decision(entries, "https://fintables.com", None)
        assert _approved_terms(entries) == ["fintables"]  # manuel katman yaşıyor

    def test_shared_term_two_urls(self):
        # Aynı terim iki URL'den: birini silince approved kalır, ikisi de gidince rejected
        entries = reconcile_url_decision(None, "https://acme.com", "acme")
        entries = reconcile_url_decision(entries, "https://acme.com.tr", "acme")
        entries = reconcile_removed_urls(entries, ["https://acme.com.tr"])
        assert _approved_terms(entries) == ["acme"]
        entries = reconcile_removed_urls(entries, [])
        assert _approved_terms(entries) == []

    def test_legacy_untouched_by_reconciliation(self):
        legacy = [{"term": "eski", "status": "approved", "source": "domain"}]
        result = reconcile_removed_urls(list(legacy), [])
        assert result[0]["status"] == "approved"
        assert is_legacy_term(result[0])

    def test_legacy_backfill_joins_lifecycle(self):
        legacy = [{"term": "fintables", "status": "approved", "source": "domain"}]
        entries = backfill_legacy_source_urls(legacy, ["https://www.fintables.com"])
        assert entries[0]["source_urls"] == ["fintables.com"]
        assert not is_legacy_term(entries[0])
        # Artık normal yaşam döngüsünde: URL kalkınca rejected olur
        entries = reconcile_removed_urls(entries, [])
        assert _approved_terms(entries) == []

    def test_status_always_derived(self):
        entries = set_manual_approval(None, "x", True)
        assert entries[0]["status"] == "approved"
        entries = set_manual_approval(entries, "x", False)
        assert entries[0]["status"] == "rejected"


# ── Topic tek-kaynak senkronu ────────────────────────────────────────

class TestTopicSync:
    def test_sync_adds_and_rejects(self):
        existing = [{"term": "temettü", "status": "approved", "source": "user"}]
        result = sync_excluded_terms(existing, ["kripto para"])
        by_term = {e["term"]: e["status"] for e in result}
        assert by_term["kripto para"] == "approved"
        assert by_term["temettü"] == "rejected"  # artık excluded_info'da yok

    def test_sync_no_competitor_provenance_fields(self):
        result = sync_excluded_terms(None, ["kripto"])
        assert "source_urls" not in result[0]
        assert "manual_approved" not in result[0]

    def test_parse_and_merge_helpers(self):
        assert parse_excluded_info("kripto, temettü\nkripto") == ["kripto", "temettü"]
        assert remove_normalized(["Kripto", "hisse"], ["kripto"]) == ["hisse"]
        assert merge_normalized(["a", "b"], ["B", "c"]) == ["a", "b", "c"]


# ── Freshness (`!=` sözleşmesi) ──────────────────────────────────────

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestFreshness:
    def _run(self, pool_v, rel_v, skip=False):
        return _Obj(
            channel_pool_policy_version=pool_v,
            relevance_anchor_version=rel_v,
            skip_relevance=skip,
        )

    def _ws(self, policy_v, anchor_v):
        return _Obj(policy_version=policy_v, anchor_version=anchor_v)

    def test_fresh_when_equal(self):
        f = compute_pool_freshness(self._run(3, 2), self._ws(3, 2))
        assert not f.channel_pool_stale

    def test_null_is_stale(self):
        f = compute_pool_freshness(self._run(None, None), self._ws(1, 1))
        assert f.policy_stale and f.channel_pool_stale

    def test_run_greater_than_workspace_still_stale(self):
        # v13 invariant #1: `<` restore/hatalı backfill sonrası yanlış fresh
        # üretirdi — eşit olmayan HER şey stale
        f = compute_pool_freshness(self._run(5, 2), self._ws(3, 2))
        assert f.policy_stale

    def test_relevance_axis_independent(self):
        f = compute_pool_freshness(self._run(3, 1), self._ws(3, 2))
        assert not f.policy_stale
        assert f.relevance_stale and f.channel_pool_stale
        d = f.as_dict()
        assert d == {
            "channel_pool_stale": True,
            "policy_stale": False,
            "relevance_stale": True,
            # v2.1 Faz C: üçüncü eksen — v2 run'da her zaman False
            "strategy_stale": False,
            # Corpus screening (plan §7.4): yalnız assistive havuzlarda
            # bayatlayan eksen + bilgi amaçlı atama göstergesi
            "screening_context_stale": False,
            "assignment_in_progress": False,
        }

    def test_skip_relevance_ignores_anchor(self):
        f = compute_pool_freshness(self._run(3, None, skip=True), self._ws(3, 9))
        assert not f.channel_pool_stale

    def test_workspaceless_run_fresh(self):
        f = compute_pool_freshness(self._run(None, None), None)
        assert not f.channel_pool_stale


# ── Canonical anchor fingerprint ─────────────────────────────────────

class TestAnchorFingerprint:
    def test_boundaries_preserved(self):
        # Birleşik-string DEĞİL: ["ab","c"] ile ["a","bc"] farklı olmalı
        assert canonical_anchor_fingerprint(["ab", "c"]) != canonical_anchor_fingerprint(["a", "bc"])

    def test_order_preserved(self):
        assert canonical_anchor_fingerprint(["a", "b"]) != canonical_anchor_fingerprint(["b", "a"])

    def test_whitespace_and_empty_normalized(self):
        assert canonical_anchor_fingerprint([" a ", "", "b"]) == canonical_anchor_fingerprint(["a", "b"])

    def test_none_equals_empty(self):
        assert canonical_anchor_fingerprint(None) == canonical_anchor_fingerprint([])
