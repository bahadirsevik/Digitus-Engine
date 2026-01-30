"""
Tests for scoring algorithms.
"""
import pytest
from decimal import Decimal

from app.core.scoring.normalizer import (
    normalize_competition,
    normalize_trend,
    safe_log,
    calculate_combined_trend
)
from app.core.scoring.ads_scorer import calculate_ads_score, calculate_bulk_ads_scores
from app.core.scoring.seo_scorer import calculate_seo_score, calculate_bulk_seo_scores
from app.core.scoring.social_scorer import calculate_social_score, calculate_bulk_social_scores


class TestNormalizer:
    """Tests for normalizer functions."""
    
    def test_normalize_competition_already_normalized(self):
        """Test competition already in 0-1 range."""
        assert normalize_competition(0.5) == 0.5
        assert normalize_competition(0.0) == 0.0
        assert normalize_competition(1.0) == 1.0
    
    def test_normalize_competition_from_percentage(self):
        """Test normalizing from 0-100 range."""
        assert normalize_competition(50) == 0.5
        assert normalize_competition(0) == 0.0
        assert normalize_competition(100) == 1.0
    
    def test_normalize_trend_zero(self):
        """Test zero trend."""
        assert normalize_trend(0) == 1.0
    
    def test_normalize_trend_positive(self):
        """Test positive trends."""
        assert normalize_trend(50) == 1.5
        assert normalize_trend(100) == 2.0
    
    def test_normalize_trend_negative(self):
        """Test negative trends."""
        result = normalize_trend(-50)
        assert result == 0.5
    
    def test_normalize_trend_extreme_negative(self):
        """Test extreme negative trend doesn't go below 0.1."""
        result = normalize_trend(-100)
        assert result >= 0.1
    
    def test_safe_log_positive(self):
        """Test log of positive values."""
        result = safe_log(100)
        assert result == pytest.approx(2.0, rel=0.01)
    
    def test_safe_log_zero(self):
        """Test log of zero returns 0."""
        assert safe_log(0) == 0
    
    def test_safe_log_negative(self):
        """Test log of negative returns 0."""
        assert safe_log(-10) == 0
    
    def test_calculate_combined_trend(self):
        """Test combined trend calculation."""
        result = calculate_combined_trend(
            trend_3m=50,
            trend_12m=25,
            weight_3m=0.6,
            weight_12m=0.4
        )
        # 3m: 1.5, 12m: 1.25
        # (1.5 * 0.6 + 1.25 * 0.4) / 1.0 = 0.9 + 0.5 = 1.4
        assert result == pytest.approx(1.4, rel=0.01)


class TestADSScorer:
    """Tests for ADS scoring algorithm."""
    
    def test_ads_score_basic(self):
        """Test basic ADS score calculation."""
        score = calculate_ads_score(
            monthly_volume=1000,
            trend_3m=0,
            trend_12m=0,
            competition_score=0.5
        )
        assert score > 0
    
    def test_ads_score_zero_volume(self):
        """Test zero volume returns zero score."""
        score = calculate_ads_score(
            monthly_volume=0,
            trend_3m=50,
            trend_12m=50,
            competition_score=0.5
        )
        assert score == 0.0
    
    def test_ads_score_high_volume_wins(self):
        """Test higher volume results in higher score."""
        low_vol = calculate_ads_score(1000, 0, 0, 0.5)
        high_vol = calculate_ads_score(10000, 0, 0, 0.5)
        assert high_vol > low_vol
    
    def test_ads_score_positive_trend_helps(self):
        """Test positive trend increases score."""
        no_trend = calculate_ads_score(1000, 0, 0, 0.5)
        with_trend = calculate_ads_score(1000, 50, 50, 0.5)
        assert with_trend > no_trend
    
    def test_ads_score_high_competition_hurts(self):
        """Test high competition decreases score."""
        low_comp = calculate_ads_score(1000, 0, 0, 0.1)
        high_comp = calculate_ads_score(1000, 0, 0, 0.9)
        assert low_comp > high_comp
    
    def test_bulk_ads_scores(self):
        """Test bulk scoring."""
        keywords = [
            {'id': 1, 'monthly_volume': 1000, 'trend_3m': 10, 'trend_12m': 5, 'competition_score': 0.5},
            {'id': 2, 'monthly_volume': 5000, 'trend_3m': 20, 'trend_12m': 10, 'competition_score': 0.3},
        ]
        results = calculate_bulk_ads_scores(keywords)
        
        assert len(results) == 2
        assert results[0]['ads_rank'] == 1  # Higher score first
        assert results[1]['ads_rank'] == 2


class TestSEOScorer:
    """Tests for SEO scoring algorithm."""
    
    def test_seo_score_basic(self):
        """Test basic SEO score calculation."""
        score = calculate_seo_score(
            monthly_volume=1000,
            trend_3m=0,
            trend_12m=0,
            competition_score=0.5
        )
        assert score > 0
    
    def test_seo_score_zero_volume(self):
        """Test zero volume returns zero score."""
        score = calculate_seo_score(0, 50, 50, 0.5)
        assert score == 0.0
    
    def test_seo_score_low_competition_helps(self):
        """Test low competition increases score (opportunity)."""
        low_comp = calculate_seo_score(1000, 0, 0, 0.1)
        high_comp = calculate_seo_score(1000, 0, 0, 0.9)
        assert low_comp > high_comp
    
    def test_bulk_seo_scores(self):
        """Test bulk SEO scoring."""
        keywords = [
            {'id': 1, 'monthly_volume': 1000, 'trend_3m': 10, 'trend_12m': 5, 'competition_score': 0.8},
            {'id': 2, 'monthly_volume': 5000, 'trend_3m': 20, 'trend_12m': 10, 'competition_score': 0.2},
        ]
        results = calculate_bulk_seo_scores(keywords)
        
        assert len(results) == 2
        assert 'seo_score' in results[0]
        assert 'seo_rank' in results[0]


class TestSocialScorer:
    """Tests for Social scoring algorithm."""
    
    def test_social_score_basic(self):
        """Test basic Social score calculation."""
        score = calculate_social_score(
            monthly_volume=1000,
            trend_3m=50,
            trend_12m=10
        )
        assert score > 0
    
    def test_social_score_zero_volume(self):
        """Test zero volume returns zero score."""
        score = calculate_social_score(0, 50, 50)
        assert score == 0.0
    
    def test_social_score_trend_emphasis(self):
        """Test recent trend has higher weight."""
        recent_trend = calculate_social_score(1000, 100, 0)
        old_trend = calculate_social_score(1000, 0, 100)
        # 3m trend has 3x weight, so recent should be higher
        assert recent_trend > old_trend
    
    def test_bulk_social_scores(self):
        """Test bulk Social scoring."""
        keywords = [
            {'id': 1, 'monthly_volume': 1000, 'trend_3m': 10, 'trend_12m': 5},
            {'id': 2, 'monthly_volume': 5000, 'trend_3m': 100, 'trend_12m': 50},
        ]
        results = calculate_bulk_social_scores(keywords)
        
        assert len(results) == 2
        assert 'social_score' in results[0]
        assert 'social_rank' in results[0]
