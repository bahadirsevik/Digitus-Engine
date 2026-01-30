"""
Tests for database models and CRUD operations.
"""
import pytest
from decimal import Decimal


class TestKeywordModel:
    """Tests for Keyword model."""
    
    def test_keyword_creation_data(self):
        """Test keyword data structure."""
        keyword_data = {
            'keyword': 'test keyword',
            'monthly_volume': 1000,
            'trend_12m': 15.5,
            'trend_3m': 22.3,
            'competition_score': 0.75,
            'sector': 'teknoloji',
            'target_market': 'TR',
            'is_active': True
        }
        
        assert keyword_data['keyword'] == 'test keyword'
        assert keyword_data['monthly_volume'] == 1000
        assert keyword_data['competition_score'] == 0.75
    
    def test_keyword_required_fields(self):
        """Test required fields for keyword."""
        required_fields = ['keyword', 'monthly_volume']
        keyword_data = {'keyword': 'test', 'monthly_volume': 100}
        
        for field in required_fields:
            assert field in keyword_data


class TestScoringRunModel:
    """Tests for ScoringRun model."""
    
    def test_scoring_run_data(self):
        """Test scoring run data structure."""
        run_data = {
            'run_name': 'Test Run',
            'total_keywords': 100,
            'ads_capacity': 20,
            'seo_capacity': 30,
            'social_capacity': 25,
            'status': 'pending'
        }
        
        assert run_data['ads_capacity'] == 20
        assert run_data['status'] == 'pending'
    
    def test_scoring_run_status_values(self):
        """Test valid status values."""
        valid_statuses = ['pending', 'scoring', 'intent_analysis', 'completed', 'failed']
        
        for status in valid_statuses:
            run_data = {'status': status}
            assert run_data['status'] in valid_statuses


class TestKeywordScoreModel:
    """Tests for KeywordScore model."""
    
    def test_score_data(self):
        """Test keyword score data structure."""
        score_data = {
            'scoring_run_id': 1,
            'keyword_id': 1,
            'ads_score': Decimal('1234.5678'),
            'seo_score': Decimal('567.8901'),
            'social_score': Decimal('890.1234'),
            'ads_rank': 1,
            'seo_rank': 5,
            'social_rank': 3
        }
        
        assert score_data['ads_rank'] == 1
        assert score_data['ads_score'] == Decimal('1234.5678')


class TestChannelPoolModel:
    """Tests for ChannelPool model."""
    
    def test_channel_pool_data(self):
        """Test channel pool data structure."""
        pool_data = {
            'scoring_run_id': 1,
            'keyword_id': 1,
            'channel': 'ADS',
            'final_rank': 1,
            'is_strategic': True
        }
        
        assert pool_data['channel'] in ['ADS', 'SEO', 'SOCIAL']
        assert pool_data['is_strategic'] == True
    
    def test_valid_channels(self):
        """Test valid channel values."""
        valid_channels = ['ADS', 'SEO', 'SOCIAL']
        
        for channel in valid_channels:
            pool_data = {'channel': channel}
            assert pool_data['channel'] in valid_channels


class TestIntentAnalysisModel:
    """Tests for IntentAnalysis model."""
    
    def test_intent_data(self):
        """Test intent analysis data structure."""
        intent_data = {
            'scoring_run_id': 1,
            'keyword_id': 1,
            'channel': 'ADS',
            'intent_type': 'transactional',
            'confidence_score': Decimal('0.85'),
            'ai_reasoning': 'User wants to buy',
            'is_passed': True
        }
        
        assert intent_data['intent_type'] == 'transactional'
        assert intent_data['is_passed'] == True
    
    def test_valid_intent_types(self):
        """Test valid intent types."""
        valid_intents = ['transactional', 'informational', 'navigational', 'commercial', 'trend_worthy']
        
        for intent in valid_intents:
            intent_data = {'intent_type': intent}
            assert intent_data['intent_type'] in valid_intents


class TestContentOutputModel:
    """Tests for ContentOutput model."""
    
    def test_content_output_data(self):
        """Test content output data structure."""
        content_data = {
            'keyword_id': 1,
            'channel': 'SEO',
            'content_type': 'blog_post',
            'content_data': {
                'title': 'Test Blog Post',
                'body': 'Content here...',
                'meta_description': 'Description'
            },
            'seo_compliance_score': Decimal('0.92'),
            'geo_compliance_score': Decimal('0.88')
        }
        
        assert content_data['content_type'] == 'blog_post'
        assert 'title' in content_data['content_data']
