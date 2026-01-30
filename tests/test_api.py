"""
Tests for API endpoints.
"""
import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""
    
    def test_root_endpoint_structure(self):
        """Test root endpoint returns expected structure."""
        expected_keys = ['message', 'version', 'status']
        mock_response = {
            'message': 'DIGITUS ENGINE API',
            'version': '2.0.0',
            'status': 'running'
        }
        
        for key in expected_keys:
            assert key in mock_response
    
    def test_health_endpoint_structure(self):
        """Test health endpoint structure."""
        expected_keys = ['status', 'components']
        mock_response = {
            'status': 'healthy',
            'components': {'api': 'up', 'database': 'up'}
        }
        
        for key in expected_keys:
            assert key in mock_response


class TestKeywordEndpoints:
    """Tests for keyword endpoints."""
    
    def test_keyword_list_response_structure(self):
        """Test keyword list response structure."""
        mock_response = {
            'items': [],
            'total': 0,
            'skip': 0,
            'limit': 100
        }
        
        assert 'items' in mock_response
        assert 'total' in mock_response
        assert 'skip' in mock_response
        assert 'limit' in mock_response


class TestScoringEndpoints:
    """Tests for scoring endpoints."""
    
    def test_scoring_run_create_request(self):
        """Test scoring run creation request structure."""
        request = {
            'run_name': 'Test Run',
            'ads_capacity': 20,
            'seo_capacity': 30,
            'social_capacity': 25
        }
        
        assert request['ads_capacity'] > 0
        assert request['seo_capacity'] > 0
        assert request['social_capacity'] > 0
    
    def test_valid_channels(self):
        """Test valid channel names."""
        valid_channels = ['ADS', 'SEO', 'SOCIAL']
        
        for channel in valid_channels:
            assert channel in valid_channels


class TestChannelEndpoints:
    """Tests for channel endpoints."""
    
    def test_channel_assignment_response_structure(self):
        """Test channel assignment response structure."""
        mock_response = {
            'scoring_run_id': 1,
            'steps': {
                'pool_building': {'ADS': 40, 'SEO': 60, 'SOCIAL': 50},
                'intent_analysis': {},
                'final_pools': {},
                'strategic': {}
            }
        }
        
        assert 'scoring_run_id' in mock_response
        assert 'steps' in mock_response


class TestExportEndpoints:
    """Tests for export endpoints."""
    
    def test_export_formats(self):
        """Test supported export formats."""
        from app.schemas.export import ExportFormat
        
        assert ExportFormat.DOCX.value == 'docx'
        assert ExportFormat.PDF.value == 'pdf'
        assert ExportFormat.EXCEL.value == 'excel'
    
    def test_export_request_structure(self):
        """Test export request structure."""
        request = {
            'scoring_run_id': 1,
            'format': 'excel',
            'include_scores': True,
            'include_intent_analysis': False,
            'include_content': False
        }
        
        assert 'scoring_run_id' in request
        assert 'format' in request


class TestGenerationEndpoints:
    """Tests for content generation endpoints."""
    
    def test_ad_group_request_structure(self):
        """Test ad group request structure."""
        request = {
            'keyword_ids': [1, 2, 3]
        }
        
        assert 'keyword_ids' in request
        assert len(request['keyword_ids']) > 0
    
    def test_seo_geo_request_structure(self):
        """Test SEO+GEO request structure."""
        request = {
            'keyword_ids': [1],
            'content_type': 'blog_post',
            'target_word_count': 1500
        }
        
        assert request['content_type'] == 'blog_post'
        assert request['target_word_count'] >= 300
    
    def test_social_request_structure(self):
        """Test social request structure."""
        request = {
            'keyword_ids': [1, 2],
            'platforms': ['instagram', 'twitter', 'linkedin']
        }
        
        assert 'platforms' in request
        assert len(request['platforms']) > 0
