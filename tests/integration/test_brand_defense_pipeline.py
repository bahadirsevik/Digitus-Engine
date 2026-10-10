"""Marka savunma bypass'inin intent + prefilter pipeline entegrasyon testleri.

Run #8 senaryosu: "vepa tarak" AI tarafindan navigational (0.95) siniflanip
tum kanallardan eleniyordu; confirmed profildeki brand_terms ile artik
deterministik olarak korunmali. Rakip markalar (sensodyne) etkilenmemeli.
"""
import json
from decimal import Decimal

from app.core.channel.brand_defense import BRAND_DEFENSE_REASON
from app.core.channel.intent_analyzer import IntentAnalyzer
from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
from app.database.models import ChannelCandidate, IntentAnalysis, PreFilterResult


VEPA_PROFILE = {
    "company_name": "Vepa Fırça",
    "products": ["saç fırçası", "diş fırçası", "tarak"],
    "brand_terms": ["Vepa", "Vepa Fırça", "Vepa tarak", "Vepa saç fırçası"],
    "exclude_themes": [],
}


class NavigationalAI:
    """Tum keyword'lere yuksek guvenle 'navigational' donduren sahte AI."""

    def __init__(self):
        self.calls = 0
        self.seen_prompts = []

    def complete_json(self, prompt=None, **kwargs):
        self.calls += 1
        if prompt:
            self.seen_prompts.append(prompt)
        ids = []
        for line in (prompt or "").splitlines():
            line = line.strip()
            if line.startswith("- ") and ":" in line:
                raw = line[2:].split(":", 1)[0].strip()
                if raw.isdigit():
                    ids.append(int(raw))
        return json.dumps([
            {
                "keyword_id": kid,
                "intent_type": "navigational",
                "confidence": 0.95,
                "reasoning": "marka odakli arama",
            }
            for kid in ids
        ])


def _add_candidate(db_session, *, run_id, keyword_id, channel, rank=1, intent_passed=None):
    db_session.add(ChannelCandidate(
        scoring_run_id=run_id,
        keyword_id=keyword_id,
        channel=channel,
        raw_score=Decimal("1.0"),
        rank_in_channel=rank,
    ))
    if intent_passed is not None:
        db_session.add(IntentAnalysis(
            scoring_run_id=run_id,
            keyword_id=keyword_id,
            channel=channel,
            intent_type="commercial",
            confidence_score=Decimal("0.90"),
            ai_reasoning="test",
            is_passed=intent_passed,
        ))
    db_session.commit()


def _intent_row(db_session, run_id, keyword_id, channel):
    return (
        db_session.query(IntentAnalysis)
        .filter_by(scoring_run_id=run_id, keyword_id=keyword_id, channel=channel)
        .first()
    )


def test_own_brand_navigational_passes_intent_gate(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace(profile_data=VEPA_PROFILE)  # status=confirmed (default)
    run = make_scoring_run(brand_profile_id=ws.id)
    own = make_keyword("vepa tarak", brand_profile_id=ws.id)
    rival = make_keyword("sensodyne diş fırçası", brand_profile_id=ws.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=own.id, channel="ADS", rank=1)
    _add_candidate(db_session, run_id=run.id, keyword_id=rival.id, channel="ADS", rank=2)

    ai = NavigationalAI()
    IntentAnalyzer(db_session, ai).analyze_candidates(run.id, "ADS")

    own_row = _intent_row(db_session, run.id, own.id, "ADS")
    rival_row = _intent_row(db_session, run.id, rival.id, "ADS")

    # Kendi marka: navigational olsa da brand_defense ile gecer
    assert own_row.is_passed is True
    assert "[brand_defense]" in (own_row.ai_reasoning or "")
    # AI'in intent etiketi korunur (override yalnizca gecis kararini degistirir)
    assert own_row.intent_type == "navigational"
    # Rakip marka: navigational elemesi aynen calisir
    assert rival_row.is_passed is False
    # Prompt'a marka baglami enjekte edilmis olmali
    assert any("KENDİ MARKASI" in p for p in ai.seen_prompts)


def test_draft_profile_disables_bypass(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace(status="draft", profile_data=VEPA_PROFILE)
    run = make_scoring_run(brand_profile_id=ws.id)
    own = make_keyword("vepa tarak", brand_profile_id=ws.id)
    _add_candidate(db_session, run_id=run.id, keyword_id=own.id, channel="ADS")

    IntentAnalyzer(db_session, NavigationalAI()).analyze_candidates(run.id, "ADS")

    row = _intent_row(db_session, run.id, own.id, "ADS")
    assert row.is_passed is False  # onaysiz profil bypass kaynagi olamaz


def test_prefilter_keeps_own_brand_without_ai_call(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace(profile_data=VEPA_PROFILE)
    run = make_scoring_run(brand_profile_id=ws.id)
    own = make_keyword("vepa saç fırçası", brand_profile_id=ws.id)
    _add_candidate(
        db_session, run_id=run.id, keyword_id=own.id,
        channel="ADS", intent_passed=True,
    )

    class ExplodingAI:
        """Cagrildiginda patlar — own-brand'in AI'a gitmedigini kanitlar."""
        def complete_json(self, *a, **k):
            raise AssertionError("own-brand keyword AI'a gonderilmemeliydi")

    summary = AdsPreFilter(db_session, ExplodingAI()).filter_candidates(run.id)

    assert summary["kept"] == 1
    row = (
        db_session.query(PreFilterResult)
        .filter_by(scoring_run_id=run.id, keyword_id=own.id, channel="ADS")
        .first()
    )
    assert row.is_kept is True
    assert row.label == "brand_defense"
    assert (row.extra_data or {}).get("reason_code") == BRAND_DEFENSE_REASON
    assert (row.extra_data or {}).get("own_brand") is True
    assert row.is_fallback is False
