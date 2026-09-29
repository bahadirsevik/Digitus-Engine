"""
Social Media Content Generator Module.

3-Phase Pipeline:
1. CategoryGenerator - Generate content categories
2. IdeaGenerator - Generate ideas per category
3. ContentGenerator - Generate full content packages

Main Orchestrator: SocialGenerator
"""

from app.generators.social.category_generator import CategoryGenerator
from app.generators.social.idea_generator import IdeaGenerator
from app.generators.social.content_generator import ContentGenerator
from app.generators.social.social_generator import SocialGenerator
from app.generators.social.prompt_templates import (
    SOCIAL_CATEGORY_PROMPT,
    SOCIAL_IDEA_PROMPT,
    SOCIAL_CONTENT_PROMPT,
    IDEA_REGENERATE_PROMPT,
    CONTENT_REGENERATE_PROMPT,
)

__all__ = [
    "CategoryGenerator",
    "IdeaGenerator",
    "ContentGenerator",
    "SocialGenerator",
    "SOCIAL_CATEGORY_PROMPT",
    "SOCIAL_IDEA_PROMPT",
    "SOCIAL_CONTENT_PROMPT",
    "IDEA_REGENERATE_PROMPT",
    "CONTENT_REGENERATE_PROMPT",
    "SocialCategoryPromptError",
    "build_category_retry_correction",
    "build_social_category_prompt",
    "serialize_social_category_prompt_input",
    "SocialCategoryGenerationError",
    "SocialCategoryAIResult",
    "SocialBriefCategoryGenerator",
    "IdeaKeywordSnapshot",
    "SocialIdeaPromptInput",
    "SocialIdeaPromptError",
    "build_social_idea_prompt",
    "build_idea_retry_correction",
    "serialize_social_idea_prompt_input",
    "SocialBriefIdeaGenerator",
    "SocialIdeaGenerationError",
    "SocialIdeaAIResult",
    "claim_ideas_attempt_for_worker",
    "claim_ideas_retry_attempt_for_worker",
    "claim_contents_attempt_for_worker",
    "finalize_ideas_attempt_failure",
    "finalize_ideas_retry_attempt_failure",
    "finalize_contents_attempt_failure",
    "reconcile_expired_ideas_attempt_for_read",
    "reconcile_expired_ideas_retry_attempt_for_read",
    "reconcile_expired_contents_attempt_for_read",
    "lock_ideas_attempt_for_finalize",
    "lock_ideas_retry_attempt_for_finalize",
    "lock_contents_attempt_for_content_write",
    "SocialContentKeywordInput",
    "SocialContentPromptInput",
    "SocialContentPromptError",
    "serialize_social_content_prompt_input",
    "build_social_content_prompt",
    "build_social_content_repair_prompt",
    "validate_grounding_prompt_parity",
    "validate_social_content_prompt_input",
    "SocialBriefContentGenerator",
    "SocialContentAIResult",
    "SocialContentGenerationError",
    "SocialContentRepairAIResult",
    "SocialContentRepairError",
]

from app.generators.social.attempt_state import (
    claim_contents_attempt_for_worker,
    claim_ideas_attempt_for_worker,
    claim_ideas_retry_attempt_for_worker,
    finalize_contents_attempt_failure,
    finalize_ideas_attempt_failure,
    finalize_ideas_retry_attempt_failure,
    lock_contents_attempt_for_content_write,
    lock_ideas_attempt_for_finalize,
    lock_ideas_retry_attempt_for_finalize,
    reconcile_expired_contents_attempt_for_read,
    reconcile_expired_ideas_attempt_for_read,
    reconcile_expired_ideas_retry_attempt_for_read,
)


from app.generators.social.brief_category_prompt import (
    SocialCategoryPromptError,
    build_category_retry_correction,
    build_social_category_prompt,
    serialize_social_category_prompt_input,
)
from app.generators.social.brief_category_generator import (
    SocialBriefCategoryGenerator,
    SocialCategoryAIResult,
    SocialCategoryGenerationError,
)
from app.generators.social.brief_idea_prompt import (
    IdeaKeywordSnapshot,
    SocialIdeaPromptError,
    SocialIdeaPromptInput,
    build_idea_retry_correction,
    build_social_idea_prompt,
    serialize_social_idea_prompt_input,
)
from app.generators.social.brief_idea_generator import (
    SocialBriefIdeaGenerator,
    SocialIdeaAIResult,
    SocialIdeaGenerationError,
)
from app.generators.social.brief_content_prompt import (
    SocialContentKeywordInput,
    SocialContentPromptError,
    SocialContentPromptInput,
    build_social_content_prompt,
    build_social_content_repair_prompt,
    serialize_social_content_prompt_input,
    validate_grounding_prompt_parity,
    validate_social_content_prompt_input,
)
from app.generators.social.brief_content_generator import (
    SocialBriefContentGenerator,
    SocialContentAIResult,
    SocialContentGenerationError,
    SocialContentRepairAIResult,
    SocialContentRepairError,
)

