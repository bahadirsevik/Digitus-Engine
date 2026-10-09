"""Uzun profil anchor'lari relevance yazimini dusurmemeli."""

from app.database.models import KeywordRelevance


def test_keyword_relevance_preserves_long_matched_anchor(
    db_session,
    make_workspace,
    make_keyword,
    make_scoring_run,
):
    workspace = make_workspace("Long Anchor", status="confirmed")
    keyword = make_keyword("uzun anchor testi", brand_profile_id=workspace.id)
    run = make_scoring_run(
        brand_profile_id=workspace.id,
        status="scored",
        ads_capacity=10,
        seo_capacity=10,
        social_capacity=10,
    )
    anchor = "Kurumsal hedef kitle ve hizmet kapsamı " * 30
    assert len(anchor) > 500

    db_session.add(KeywordRelevance(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
        relevance_score=0.875,
        matched_anchor=anchor,
        method="embedding",
    ))
    db_session.commit()

    stored = db_session.query(KeywordRelevance).filter_by(
        scoring_run_id=run.id,
        keyword_id=keyword.id,
    ).one()
    assert stored.matched_anchor == anchor
