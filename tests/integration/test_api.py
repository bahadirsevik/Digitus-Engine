import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from app.main import app
from app.dependencies import get_db

# Create a mock database session
mock_db_session = MagicMock()

def override_get_db():
    yield mock_db_session

@pytest.fixture
def client():
    # Patch init_db to prevent real DB connection attempt during startup
    with patch("app.database.connection.init_db"):
        app.dependency_overrides[get_db] = override_get_db
        # Set raise_server_exceptions=False to allow exception handler to return 500 response
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

@patch("app.api.v1.keywords.crud")
def test_list_keywords(mock_crud, client):
    # Setup mock data matching KeywordResponse schema
    mock_keyword = MagicMock()
    mock_keyword.id = 1
    mock_keyword.keyword = "test keyword"
    mock_keyword.sector = "Technology"
    mock_keyword.monthly_volume = 1000
    mock_keyword.trend_12m = 0.1
    mock_keyword.trend_3m = 0.05
    mock_keyword.competition_score = 0.5
    mock_keyword.target_market = "US"
    mock_keyword.is_active = True
    mock_keyword.created_at = "2024-01-01T00:00:00"
    mock_keyword.updated_at = "2024-01-01T00:00:00"

    # Configure mocks
    mock_crud.get_keywords.return_value = [mock_keyword]

    # Configure DB mock for count query
    # db.query(Keyword).filter(...).count()
    mock_db_session.query.return_value.filter.return_value.count.return_value = 1

    response = client.get("/api/v1/keywords/")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["keyword"] == "test keyword"
    assert data["items"][0]["monthly_volume"] == 1000

@patch("app.api.v1.keywords.crud")
def test_get_keyword_not_found(mock_crud, client):
    mock_crud.get_keyword.return_value = None

    response = client.get("/api/v1/keywords/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Keyword not found"

def test_unhandled_exception_logging(client):
    """
    Test that unhandled exceptions are caught by the global handler
    and return 500 status code.
    We'll patch a route to raise an exception.
    """
    with patch("app.api.v1.keywords.crud.get_keywords", side_effect=Exception("Unexpected Database Error")):
        response = client.get("/api/v1/keywords/")
        assert response.status_code == 500
        assert response.json()["message"] == "Internal Server Error"
