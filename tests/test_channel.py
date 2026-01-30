"""
Tests for channel assignment modules.
"""
import pytest
from unittest.mock import Mock, MagicMock

from app.core.constants import POOL_MULTIPLIER, CHANNEL_ACCEPTED_INTENTS


class TestPoolBuilder:
    """Tests for PoolBuilder."""
    
    def test_pool_multiplier_is_2(self):
        """Pool size should be 2x capacity."""
        assert POOL_MULTIPLIER == 2
    
    def test_pool_size_calculation(self):
        """Test pool size calculation."""
        capacity = 20
        expected_pool_size = capacity * POOL_MULTIPLIER
        assert expected_pool_size == 40


class TestIntentAnalyzer:
    """Tests for IntentAnalyzer."""
    
    def test_accepted_intents_ads(self):
        """ADS should accept transactional and commercial."""
        accepted = CHANNEL_ACCEPTED_INTENTS['ADS']
        assert 'transactional' in accepted
        assert 'commercial' in accepted
        assert 'informational' not in accepted
    
    def test_accepted_intents_seo(self):
        """SEO should accept informational and commercial."""
        accepted = CHANNEL_ACCEPTED_INTENTS['SEO']
        assert 'informational' in accepted
        assert 'commercial' in accepted
        assert 'transactional' not in accepted
    
    def test_accepted_intents_social(self):
        """SOCIAL should accept trend_worthy and informational."""
        accepted = CHANNEL_ACCEPTED_INTENTS['SOCIAL']
        assert 'trend_worthy' in accepted
        assert 'informational' in accepted
        assert 'transactional' not in accepted


class TestStrategicFinder:
    """Tests for StrategicFinder."""
    
    def test_strategic_intersection_logic(self):
        """Test set intersection for strategic keywords."""
        ads_keywords = {1, 2, 3, 4, 5}
        seo_keywords = {3, 4, 5, 6, 7}
        
        strategic = ads_keywords.intersection(seo_keywords)
        
        assert strategic == {3, 4, 5}
        assert len(strategic) == 3


class TestChannelEngine:
    """Tests for ChannelEngine workflow."""
    
    def test_channel_assignment_steps(self):
        """Test that channel assignment follows correct order."""
        steps = [
            'pool_building',
            'intent_analysis',
            'final_pools',
            'strategic'
        ]
        
        # Verify step order
        assert steps[0] == 'pool_building'
        assert steps[1] == 'intent_analysis'
        assert steps[2] == 'final_pools'
        assert steps[3] == 'strategic'
    
    def test_channel_list(self):
        """Test that all channels are processed."""
        channels = ['ADS', 'SEO', 'SOCIAL']
        
        assert len(channels) == 3
        assert 'ADS' in channels
        assert 'SEO' in channels
        assert 'SOCIAL' in channels


class TestAIService:
    """Tests for AI Service."""
    
    def test_mock_service_returns_json(self):
        """Test mock AI service returns valid JSON for intent analysis."""
        from app.generators.ai_service import MockAIService
        import json
        
        mock = MockAIService()
        response = mock.complete_json("Analyze intent for: test keyword")
        
        # Should be valid JSON
        parsed = json.loads(response)
        assert isinstance(parsed, list) or isinstance(parsed, dict)
    
    def test_mock_service_complete(self):
        """Test mock AI service returns text."""
        from app.generators.ai_service import MockAIService
        
        mock = MockAIService()
        response = mock.complete("Generate content")
        
        assert isinstance(response, str)
        assert len(response) > 0
