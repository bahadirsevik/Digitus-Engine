import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.database.models import WorkspaceKeyword


def test_workspace_keyword_data_source_constraint_exists():
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in WorkspaceKeyword.__table__.constraints
        if hasattr(constraint, "sqltext")
    }

    assert "ck_workspace_keywords_data_source" in constraints
    sqltext = constraints["ck_workspace_keywords_data_source"]
    assert "csv" in sqltext
    assert "google_ads_api" in sqltext
    assert "url_seed" in sqltext
    assert "manual" in sqltext
