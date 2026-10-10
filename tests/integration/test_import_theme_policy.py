"""Import tema kapisi: provenance ayrimi + koruma onceligi (P1.6 test matrisi).

Sozlesme (plan_marka_profili_sadakati.md P1.6):

  hard      = topic_policy onayli terimler UNION excluded_info (kullanici yazdi)
              -> ELER
  advisory  = exclude_themes MINUS hard (AI uretimi)
              -> UYARI, satir KABUL edilir
  protected = profile_data.protected_themes
              -> IKISINI DE EZER
  force_include -> yalniz hard elemeyi atlar

Regresyonun onemi: AI'in urettigi bir tema, kullanici hic onaylamadan keyword'u
daha SKORLAMAYA ULASMADAN eliyordu.
"""
from __future__ import annotations

from app.core.csv_import.import_plan import (
    apply_import_plan,
    build_import_plan,
    compact_plan_payload,
    import_plan_to_payload,
)
from app.core.policy.import_gate import (
    DECISION_EXCLUDE,
    DECISION_INCLUDE,
    DECISION_PROTECTED,
    decide_keyword_theme,
    resolve_import_theme_policy,
)

AI_THEME = "kripto para"
USER_THEME = "temettü takibi"


def _profile(**overrides):
    data = {
        "company_name": "Hissefy",
        "sector": "Finansal teknoloji",
        "products": ["Analiz Platformu"],
        # Iki tema da AYNI listede durur — provenance BURADAN turetilemez.
        "exclude_themes": [USER_THEME, AI_THEME],
        "anchor_texts": ["Analiz Platformu"],
    }
    data.update(overrides)
    return data


def _approved_topic_policy(*terms):
    return {
        "excluded_terms": [
            {"term": t, "status": "approved"} for t in terms
        ],
        "excluded_aliases": [],
    }


def _import(client, ws_id, keywords):
    return client.post(
        "/api/v1/keywords/import",
        json={"brand_profile_id": ws_id, "keywords": keywords},
    )


def _csv_rows(*rows):
    return [
        {
            "keyword": kw,
            "monthly_volume": vol,
            "competition_score": 0.5,
            "trend_3m": 0,
            "trend_12m": 0,
            "data_source": "csv",
        }
        for kw, vol in rows
    ]


# ── Politika cozumleme ──────────────────────────────────────────────────────


def test_policy_splits_user_terms_from_ai_terms(make_workspace):
    """Ayni exclude_themes listesi hard ve advisory olarak IKIYE ayrilir."""
    ws = make_workspace(
        name="Provenance",
        profile_data=_profile(),
        excluded_info=USER_THEME,
    )

    policy = resolve_import_theme_policy(ws)

    assert policy.hard_themes == [USER_THEME]
    assert policy.advisory_themes == [AI_THEME]


def test_policy_reads_approved_topic_terms(make_workspace):
    """excluded_info bos olsa da onayli topic policy terimi HARD sayilir."""
    ws = make_workspace(
        name="Topic policy",
        profile_data=_profile(),
        topic_policy=_approved_topic_policy(AI_THEME),
    )

    policy = resolve_import_theme_policy(ws)

    assert policy.hard_themes == [AI_THEME]
    assert policy.advisory_themes == [USER_THEME]


def test_unapproved_topic_terms_are_not_hard(make_workspace):
    """status != approved olan terim politikaya GIRMEZ."""
    ws = make_workspace(
        name="Pending term",
        profile_data=_profile(),
        topic_policy={
            "excluded_terms": [{"term": AI_THEME, "status": "pending"}],
            "excluded_aliases": [],
        },
    )

    assert resolve_import_theme_policy(ws).hard_themes == []


# ── Karar matrisi (saf fonksiyon) ───────────────────────────────────────────


def test_decision_matrix(make_workspace):
    ws = make_workspace(
        name="Matrix",
        profile_data=_profile(protected_themes=["kripto para vergilendirmesi"]),
        excluded_info=USER_THEME,
    )
    policy = resolve_import_theme_policy(ws)

    # 1) onayli tema -> exclude
    hard = decide_keyword_theme("temettü takip programı", policy)
    assert hard.decision == DECISION_EXCLUDE
    assert hard.matched_exclude_theme == USER_THEME

    # 2) yalniz AI temasi -> include + uyari
    advisory = decide_keyword_theme("kripto para sinyalleri", policy)
    assert advisory.decision == DECISION_INCLUDE
    assert advisory.has_advisory_warning is True
    assert advisory.matched_advisory_theme == AI_THEME

    # 3) exclude + protected cakismasi -> protected (include)
    both = decide_keyword_theme("kripto para vergilendirmesi rehberi", policy)
    assert both.decision == DECISION_PROTECTED
    assert both.matched_protected_theme == "kripto para vergilendirmesi"

    # 4) force_include -> include
    forced = decide_keyword_theme(
        "temettü takip programı", policy, force_include=True
    )
    assert forced.decision == DECISION_INCLUDE
    assert forced.is_excluded is False

    # 5) alakasiz kelime -> include, uyari yok
    clean = decide_keyword_theme("portföy takip programı", policy)
    assert clean.decision == DECISION_INCLUDE
    assert clean.has_advisory_warning is False


def test_protected_decision_produces_no_advisory_warning(make_workspace):
    """Protected karari AI temasina da uysa UYARI URETMEZ (Codex, 07.08).

    "Protected ikisini de ezer" sozlesmesi geregi: kullanicinin acikca
    kapsam-ici ilan ettigi konu icin "gozden gecirin" demek celiskidir.
    matched_advisory_theme yine de DOLU kalir (denetim icin).
    """
    ws = make_workspace(
        name="Protected no warn",
        # AI temasi "kripto para" + korunan konu ayni kelimeye oturur
        profile_data=_profile(protected_themes=["kripto para vergilendirmesi"]),
    )
    policy = resolve_import_theme_policy(ws)

    decision = decide_keyword_theme("kripto para vergilendirmesi rehberi", policy)

    assert decision.decision == DECISION_PROTECTED
    assert decision.matched_advisory_theme == AI_THEME  # denetim izi korunur
    assert decision.has_advisory_warning is False       # ama uyari YOK


def test_protected_beats_hard_exclusion_even_when_broader(make_workspace):
    """Genis dislama, acikca korunan konuyu eleyemez."""
    ws = make_workspace(
        name="Protected wins",
        profile_data=_profile(
            exclude_themes=["saç boyası"],
            protected_themes=["saç boyası zararları"],
        ),
        excluded_info="saç boyası",
    )
    policy = resolve_import_theme_policy(ws)

    decision = decide_keyword_theme("saç boyası zararları nelerdir", policy)

    assert decision.decision == DECISION_PROTECTED
    assert decision.matched_exclude_theme == "saç boyası"  # eslesti AMA elemedi


# ── JSON import yolu ────────────────────────────────────────────────────────


def test_ai_only_theme_is_imported_with_warning(client, make_workspace):
    """EN ONEMLI REGRESYON: AI temasi kullanici onayi olmadan hard-drop YAPMAZ."""
    ws = make_workspace(name="AI advisory", profile_data=_profile())

    res = _import(client, ws.id, [
        {"keyword": "kripto para sinyalleri", "monthly_volume": 50},
        {"keyword": "temettü takip programı", "monthly_volume": 100},
    ])

    assert res.status_code == 200
    data = res.json()
    assert data["created"] == 2          # ikisi de eklendi
    assert data["skipped_theme"] == 0    # hicbiri elenmedi
    assert data["warned_theme"] == 2     # ikisi de uyari uretti
    assert {w["matched"] for w in data["theme_warnings"]} == {AI_THEME, USER_THEME}


def test_user_approved_theme_still_hard_drops(client, make_workspace):
    ws = make_workspace(
        name="User hard",
        profile_data=_profile(),
        excluded_info=USER_THEME,
    )

    res = _import(client, ws.id, [
        {"keyword": "temettü takip programı", "monthly_volume": 100},
        {"keyword": "portföy takip programı", "monthly_volume": 100},
        {"keyword": "kripto para sinyalleri", "monthly_volume": 50},
    ])

    data = res.json()
    # temettü elenir; portföy tam-stem kuralindan gecer; kripto AI temasi -> girer
    assert data["skipped_theme"] == 1
    assert data["created"] == 2
    themed = [d for d in data["skipped_details"] if d["reason"] == "skipped_theme"]
    assert themed[0]["keyword"] == "temettü takip programı"
    assert themed[0]["matched"] == USER_THEME
    assert data["warned_theme"] == 1  # kripto uyarisi


def test_protected_theme_survives_import(client, make_workspace):
    ws = make_workspace(
        name="Protected import",
        profile_data=_profile(
            exclude_themes=["saç boyası"],
            protected_themes=["saç boyası zararları"],
        ),
        excluded_info="saç boyası",
    )

    res = _import(client, ws.id, [
        {"keyword": "saç boyası zararları nelerdir", "monthly_volume": 90},
        {"keyword": "saç boyası fiyatları", "monthly_volume": 90},
    ])

    data = res.json()
    assert data["created"] == 1
    assert data["skipped_theme"] == 1
    themed = [d for d in data["skipped_details"] if d["reason"] == "skipped_theme"]
    assert themed[0]["keyword"] == "saç boyası fiyatları"


def test_protected_keyword_is_not_reported_as_warning(client, make_workspace):
    """JSON import: protected keyword uyari sayacina GIRMEZ."""
    ws = make_workspace(
        name="Protected no warn import",
        profile_data=_profile(protected_themes=["kripto para vergilendirmesi"]),
    )

    res = _import(client, ws.id, [
        {"keyword": "kripto para vergilendirmesi rehberi", "monthly_volume": 40},
    ])

    data = res.json()
    assert data["created"] == 1
    assert data["skipped_theme"] == 0
    assert data["warned_theme"] == 0
    assert data["theme_warnings"] == []


def test_draft_workspace_ai_theme_does_not_drop(client, make_workspace):
    """Onaysiz profilde AI temasiyla eleme YOK (Codex matrisi)."""
    ws = make_workspace(
        name="Draft ws", status="draft", profile_data=_profile(),
    )

    res = _import(client, ws.id, [
        {"keyword": "kripto para sinyalleri", "monthly_volume": 50},
    ])

    assert res.json()["created"] == 1
    assert res.json()["skipped_theme"] == 0


# ── CSV yolu: preview == commit ─────────────────────────────────────────────


def test_csv_preview_and_commit_agree(db_session, make_workspace):
    ws = make_workspace(
        name="CSV parity",
        profile_data=_profile(),
        excluded_info=USER_THEME,
    )

    plan = build_import_plan(
        db_session,
        brand_profile_id=ws.id,
        keyword_rows=_csv_rows(
            ("temettü takip programı", 100),   # hard -> elenir
            ("kripto para sinyalleri", 50),    # advisory -> girer + uyari
            ("portföy takip programı", 100),   # temiz
        ),
        parser_meta={},
        source_file_name="test.csv",
        theme_policy=resolve_import_theme_policy(ws),
    )
    payload = import_plan_to_payload(plan)

    assert payload["skipped_theme"] == 1
    assert payload["warned_theme"] == 1
    assert payload["accepted"] == 2
    assert payload["theme_excluded"][0]["matched_keyword"] == USER_THEME
    assert payload["theme_warned"][0]["matched_keyword"] == AI_THEME
    assert "gözden geçirin" in payload["message"]

    # Commit plani REPLAY eder → preview ile ayni sonuc (yapisal garanti)
    result = apply_import_plan(db_session, compact_plan_payload(plan), pool_limit=100)
    assert result["created"] == payload["accepted"] == 2


def test_csv_preview_does_not_warn_for_protected_keyword(db_session, make_workspace):
    """CSV dry-run: protected keyword `theme_warned` listesine GIRMEZ."""
    ws = make_workspace(
        name="CSV protected",
        profile_data=_profile(protected_themes=["kripto para vergilendirmesi"]),
    )

    plan = build_import_plan(
        db_session,
        brand_profile_id=ws.id,
        keyword_rows=_csv_rows(
            ("kripto para vergilendirmesi rehberi", 40),  # protected
            ("kripto para sinyalleri", 50),               # advisory
        ),
        parser_meta={},
        source_file_name="test.csv",
        theme_policy=resolve_import_theme_policy(ws),
    )
    payload = import_plan_to_payload(plan)

    assert payload["accepted"] == 2
    assert payload["skipped_theme"] == 0
    assert payload["warned_theme"] == 1  # yalniz advisory olan
    warned = {row["keyword"] for row in payload["theme_warned"]}
    assert warned == {"kripto para sinyalleri"}
