"""
Google Ads content generation module.

Exports:
- AdsGenerator: Main orchestrator for RSA generation
- KeywordGrouper: Groups keywords by intent
- RSAGenerator: Generates RSA components for ad groups
- Validators: HeadlineValidator, DescriptionValidator, DKIValidator
"""

from app.generators.ads.ads_generator import AdsGenerator
from app.generators.ads.keyword_grouper import KeywordGrouper
from app.generators.ads.rsa_generator import RSAGenerator
from app.generators.ads.validators import (
    HeadlineValidator,
    DescriptionValidator,
    DKIValidator,
)
from app.generators.ads.prompt_templates import (
    ADS_GROUPING_PROMPT,
    ADS_RSA_GENERATION_PROMPT,
    HEADLINE_REGENERATION_PROMPT,
)

__all__ = [
    "AdsGenerator",
    "KeywordGrouper",
    "RSAGenerator",
    "HeadlineValidator",
    "DescriptionValidator",
    "DKIValidator",
    "ADS_GROUPING_PROMPT",
    "ADS_RSA_GENERATION_PROMPT",
    "HEADLINE_REGENERATION_PROMPT",
]
