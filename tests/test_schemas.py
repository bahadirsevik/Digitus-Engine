"""
Tests for Pydantic schemas.
"""
import pytest
from decimal import Decimal
from pydantic import ValidationError

from app.schemas.keyword import KeywordCreate, KeywordUpdate, KeywordResponse
from app.schemas.scoring import ScoringRunCreate
from app.schemas.channel import ChannelPoolResponse
from app.schemas.content import AdGroupRequest, SEOGeoRequest
from app.schemas.export import ExportRequest, ExportFormat


class TestKeywordSchemas:
    """Tests for Keyword schemas."""
    
    def test_keyword_create_valid(self):
        """Test valid keyword creation."""
        data = {
            'keyword': 'test keyword',
            'monthly_volume': 1000,
            'trend_12m': Decimal('15.5'),
            'trend_3m': Decimal('22.3'),
            'competition_score': Decimal('0.75'),
            'sector': 'teknoloji'
        }
        keyword = KeywordCreate(**data)
        assert keyword.keyword == 'test keyword'
        assert keyword.monthly_volume == 1000
    
    def test_keyword_create_minimal(self):
        """Test keyword creation with minimal data."""
        keyword = KeywordCreate(keyword='minimal test')
        assert keyword.keyword == 'minimal test'
        assert keyword.monthly_volume == 0
    
    def test_keyword_create_invalid_competition(self):
        """Test validation for competition score out of range."""
        with pytest.raises(ValidationError):
            KeywordCreate(keyword='test', competition_score=Decimal('1.5'))
    
    def test_keyword_update_partial(self):
        """Test partial update schema."""
        update = KeywordUpdate(monthly_volume=2000)
        assert update.monthly_volume == 2000
        assert update.keyword is None


class TestScoringSchemas:
    """Tests for Scoring schemas."""
    
    def test_scoring_run_create_valid(self):
        """Test valid scoring run creation."""
        data = {
            'run_name': 'Test Run',
            'ads_capacity': 20,
            'seo_capacity': 30,
            'social_capacity': 25
        }
        run = ScoringRunCreate(**data)
        assert run.ads_capacity == 20
        assert run.seo_capacity == 30
    
    def test_scoring_run_create_invalid_capacity(self):
        """Test validation for zero capacity."""
        with pytest.raises(ValidationError):
            ScoringRunCreate(ads_capacity=0, seo_capacity=10, social_capacity=10)


class TestChannelSchemas:
    """Tests for Channel schemas."""
    
    def test_channel_pool_response(self):
        """Test channel pool response schema."""
        data = {
            'keyword_id': 1,
            'keyword': 'test',
            'channel': 'ADS',
            'final_rank': 1,
            'is_strategic': True
        }
        pool = ChannelPoolResponse(**data)
        assert pool.channel == 'ADS'
        assert pool.is_strategic == True


class TestContentSchemas:
    """Tests for Content schemas."""
    
    def test_ad_group_request(self):
        """Test ad group request schema."""
        request = AdGroupRequest(keyword_ids=[1, 2, 3])
        assert len(request.keyword_ids) == 3
    
    def test_seo_geo_request_defaults(self):
        """Test SEO+GEO request with defaults."""
        request = SEOGeoRequest(keyword_ids=[1])
        assert request.content_type == 'blog_post'
        assert request.target_word_count == 1500


class TestExportSchemas:
    """Tests for Export schemas."""
    
    def test_export_request(self):
        """Test export request schema."""
        request = ExportRequest(scoring_run_id=1, format=ExportFormat.DOCX)
        assert request.format == ExportFormat.DOCX
        assert request.include_scores == True
    
    def test_export_format_enum(self):
        """Test export format enum values."""
        assert ExportFormat.DOCX.value == 'docx'
        assert ExportFormat.PDF.value == 'pdf'
        assert ExportFormat.EXCEL.value == 'excel'
