"""
Uçtan uca kanal atama akışı (Skorlama v2 semantiği).

`run_channel_assignment`'ın TAMAMI koşulur (aday havuz -> paralel intent ->
marka filtresi -> prefilter -> sınıf-öncelikli final -> expansion) ve şunlar
doğrulanır:
- Elenen (sınıf -1) kelimeler kapasite boş kalsa bile final havuza GERİ GELMEZ
  (backfill kaldırıldı; expansion yeni aday bulamayınca liste kısa kalır)
- Final havuz sınıf-öncelikli kurulur: düşük skorlu hot_sale (sınıf 2),
  yüksek skorlu lead'lerin (sınıf 1) önünde
- Akış sonunda run status 'channel_assigned'
"""
import json
import re
from decimal import Decimal

from app.core.channel.channel_engine import ChannelEngine
from app.database.models import ChannelPool, Keyword, KeywordScore, PreFilterResult


class ScriptedAdsAI:
    """Prompt tipine ve keyword metnine göre karar veren sahte AI.

    - intent: herkese transactional 0.9 (ADS kapısından geçer)
    - marka filtresi: herkes markayla alakalı
    - ADS prefilter: 'sicak' -> keep hot_sale, 'elenen' -> eliminate,
      diğerleri -> keep lead
    """

    def _ids_from_intent_prompt(self, prompt: str):
        ids = []
        for line in prompt.splitlines():
            line = line.strip()
            if line.startswith("- ") and ":" in line:
                raw = line[2:].split(":", 1)[0].strip()
                if raw.isdigit():
                    ids.append(int(raw))
        return ids

    def _pairs_from_json_prompt(self, prompt: str):
        match = re.search(r"keywords=(\[.*\])", prompt, re.DOTALL)
        if not match:
            return []
        return [(item["id"], item["keyword"]) for item in json.loads(match.group(1))]

    def complete_json(self, prompt=None, **kwargs):
        prompt = prompt or ""
        if "intent_type" in prompt:
            return json.dumps([
                {
                    "keyword_id": kid,
                    "intent_type": "transactional",
                    "confidence": 0.9,
                    "reasoning": "satin alma sinyali",
                }
                for kid in self._ids_from_intent_prompt(prompt)
            ])
        if "is_brand_relevant" in prompt:
            return json.dumps({
                "results": [
                    {
                        "keyword_id": kid,
                        "is_brand_relevant": True,
                        "matched_exclude_theme": None,
                        "reason": "Markayla alakali",
                    }
                    for kid, _ in self._pairs_from_json_prompt(prompt)
                ]
            })
        # ADS prefilter
        results = []
        for kid, keyword in self._pairs_from_json_prompt(prompt):
            if "elenen" in keyword:
                results.append({
                    "keyword_id": kid,
                    "decision": "eliminate",
                    "label": None,
                    "reason_code": "LOW_COMMERCIAL_VALUE",
                    "reason": "Jenerik bilgi sorgusu",
                    "transfer_channel": None,
                })
            else:
                label = "hot_sale" if "sicak" in keyword else "lead"
                results.append({
                    "keyword_id": kid,
                    "decision": "keep",
                    "label": label,
                    "reason_code": "HIGH_BUYING_INTENT",
                    "reason": "Ticari deger var",
                    "transfer_channel": None,
                })
        return json.dumps({"results": results})


def test_full_assignment_no_backfill_and_class_priority(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    ws = make_workspace("E2E Assignment WS", profile_data={"exclude_themes": []})
    run = make_scoring_run(
        brand_profile_id=ws.id,
        status="scored",  # scored -> channel_assigning geçişi state machine'de izinli
        ads_capacity=10,
        enable_seo=False,
        enable_social=False,
        algorithm_version="v2",
    )

    # 10 kelime: 2 hot_sale (DÜŞÜK skorlu), 3 lead (yüksek skorlu), 5 elenen.
    # kept=5 < kapasite=10 -> eski backfill elenenlerden doldururdu.
    texts = (
        ["sicak firsat bir", "sicak firsat iki"]
        + [f"kategori arama {i}" for i in range(3)]
        + [f"elenen konu {i}" for i in range(5)]
    )
    keywords = {}
    for rank, text in enumerate(texts, 1):
        kw = make_keyword(text, brand_profile_id=ws.id, monthly_volume=1000)
        keywords[text] = kw
        score = 5.0 if "sicak" in text else (30.0 - rank)  # hot'lar en düşük skor
        db_session.add(KeywordScore(
            scoring_run_id=run.id,
            keyword_id=kw.id,
            ads_score=Decimal(str(score)),
            ads_rank=rank,
            metrics_snapshot={
                "monthly_volume": 1000,
                "derived": {"h": 0.5, "mb": 0.5, "ln": 0.5, "trk": 0.0, "spec": "v2"},
            },
        ))
    db_session.commit()

    engine = ChannelEngine(db_session, ScriptedAdsAI())
    result = engine.run_channel_assignment(run.id)

    assert result["status"] == "channel_assigned"
    db_session.refresh(run)
    assert run.status == "channel_assigned"

    pool_rows = (
        db_session.query(ChannelPool, Keyword)
        .join(Keyword, ChannelPool.keyword_id == Keyword.id)
        .filter(ChannelPool.scoring_run_id == run.id, ChannelPool.channel == "ADS")
        .order_by(ChannelPool.final_rank)
        .all()
    )
    pool_texts = [keyword.keyword for _, keyword in pool_rows]

    # BACKFILL YOK: elenen 5 kelime, kapasite (10) boş kalmasına rağmen
    # final havuza hiçbir mekanizmayla geri gelmedi
    assert result["steps"]["final_pools"]["ADS"] == 5
    assert all("elenen" not in text for text in pool_texts)

    # Sınıf önceliği: düşük skorlu hot_sale'ler (sınıf 2), yüksek skorlu
    # lead'lerin (sınıf 1) önünde
    assert sorted(pool_texts[:2]) == ["sicak firsat bir", "sicak firsat iki"]
    assert all("kategori" in text for text in pool_texts[2:])

    # ai_class DB'ye doğru yazıldı (2/1/-1)
    pf = {
        row.keyword_id: row
        for row in db_session.query(PreFilterResult).filter_by(
            scoring_run_id=run.id, channel="ADS"
        )
    }
    assert pf[keywords["sicak firsat bir"].id].ai_class == 2
    assert pf[keywords["kategori arama 0"].id].ai_class == 1
    assert pf[keywords["elenen konu 0"].id].ai_class == -1
    assert pf[keywords["elenen konu 0"].id].is_kept is False

    # Expansion çalıştı ama yeni aday bulamadı (tüm evren zaten havuzdaydı) —
    # kapasite altı kalmak v2'de kabul edilen davranış; unfilled AÇIKÇA raporlanır
    expansion = result["steps"]["expansion_rounds"]["ADS"]
    assert expansion["final_count_after_expansion"] == 5
    assert expansion["unfilled_count"] == 5
    # Plan kesin beklenti: aday evreni tükendi (gevşek "in" kabulü yok)
    assert expansion["stop_reason"] == "no_more_candidates"
    assert expansion["expansion_ai_batches_used"] <= expansion["expansion_ai_batch_budget"]

    # Faz G: selection_quality şekli — run-15 karşılaştırmasının ölçüm aleti
    # task result_data'ya da taşınır. Plan Faz E iki üst düzey alan ekledi:
    # algorithm_version + relevance_effect.
    quality = result["selection_quality"]
    assert "error" not in quality
    assert set(quality.keys()) == {
        "channels", "transfers", "expansion",
        "algorithm_version", "relevance_effect",
        # Codex 26. tur #2: final havuz kaynak kırılımı
        "candidate_sources",
    }
    # Baseline koşuda kaynak alanı yok: 'unattributed:unknown'
    # (NULL action 'initial' VARSAYILMAZ — Codex 26b #2)
    assert quality["candidate_sources"]["ADS"]["candidates"]
    assert all(key == "unattributed:unknown"
               for channel in quality["candidate_sources"].values()
               for key in channel["candidates"])
    assert quality["algorithm_version"] == "v2"
    ads_q = quality["channels"]["ADS"]
    assert ads_q["final"] == 5
    assert ads_q["unfilled"] == 5
    for key in (
        "capacity", "intent_fallback", "prefilter_fallback",
        "prefilter_eliminated", "brand_excluded", "hot_sale", "lead",
        "class_counts",
    ):
        assert key in ads_q
