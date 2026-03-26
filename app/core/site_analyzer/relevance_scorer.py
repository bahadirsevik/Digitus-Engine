"""
Keyword Relevance Scorer using Gemini Embedding 2.
Calculates cosine similarity between keywords and brand profile anchors.
"""
import logging
import re
from typing import List, Dict, Tuple, Optional

import numpy as np
import google.generativeai as genai

from app.core.site_analyzer.turkish_normalizer import normalize_turkish

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "models/gemini-embedding-2-preview"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_TASK_TYPE = "SEMANTIC_SIMILARITY"
BATCH_SIZE = 50  # Gemini embedding batch limit

# Blend formula: adjusted = raw_score * (FLOOR + SLOPE * relevance)
# At relevance=0.0 → keeps 35% of score (doesn't zero out)
# At relevance=1.0 → keeps 100% of score
RELEVANCE_FLOOR = 0.35
RELEVANCE_SLOPE = 0.65


class RelevanceScorer:
    """
    Computes keyword-brand relevance using embedding cosine similarity.
    Uses Gemini Embedding 2 with multi-anchor max-similarity approach.
    """

    def __init__(self, api_key: Optional[str] = None):
        if api_key:
            genai.configure(api_key=api_key)

    def compute_relevance(
        self,
        keywords: List[str],
        anchor_texts: List[str],
    ) -> List[Dict]:
        """
        Compute relevance for a batch of keywords against anchor texts.

        Args:
            keywords: List of keyword strings
            anchor_texts: List of brand profile anchor strings

        Returns:
            List of {"keyword": str, "relevance_score": float, "matched_anchor": str}
        """
        if not anchor_texts:
            logger.warning("No anchor texts provided, returning default relevance")
            return [
                {"keyword": kw, "relevance_score": 1.0, "matched_anchor": "none"}
                for kw in keywords
            ]

        # Normalize all texts for Turkish
        normalized_keywords = [normalize_turkish(kw) for kw in keywords]
        normalized_anchors = [normalize_turkish(a) for a in anchor_texts]

        # Embed anchors (small batch, done once)
        anchor_embeddings = self._embed_batch(normalized_anchors)
        if anchor_embeddings is None:
            logger.error("Anchor embedding failed, returning default relevance")
            return [
                {"keyword": kw, "relevance_score": 1.0, "matched_anchor": "none"}
                for kw in keywords
            ]

        # Embed keywords in batches
        results = []
        for batch_start in range(0, len(normalized_keywords), BATCH_SIZE):
            batch_end = batch_start + BATCH_SIZE
            kw_batch = normalized_keywords[batch_start:batch_end]
            original_batch = keywords[batch_start:batch_end]

            kw_embeddings = self._embed_batch(kw_batch)
            if kw_embeddings is None:
                # Fallback for this batch
                for kw in original_batch:
                    results.append({
                        "keyword": kw,
                        "relevance_score": 1.0,
                        "matched_anchor": "embedding_failed",
                        "method": "fuzzy_fallback",
                    })
                continue

            # Compute max cosine similarity for each keyword
            for i, kw in enumerate(original_batch):
                kw_vec = kw_embeddings[i]
                best_score = 0.0
                best_anchor_idx = 0

                for j, anchor_vec in enumerate(anchor_embeddings):
                    sim = self._cosine_similarity(kw_vec, anchor_vec)
                    if sim > best_score:
                        best_score = sim
                        best_anchor_idx = j

                results.append({
                    "keyword": kw,
                    "relevance_score": round(max(0.0, min(1.0, best_score)), 3),
                    "matched_anchor": anchor_texts[best_anchor_idx],
                    "method": "embedding",
                })

        return results

    @staticmethod
    def apply_blend(raw_score: float, relevance_score: float) -> float:
        """
        Apply the blend formula: adjusted = raw_score * (FLOOR + SLOPE * relevance)

        This prevents low-relevance keywords from being completely zeroed out,
        while still significantly down-ranking irrelevant ones.

        Examples:
            raw=100, relevance=0.9 → 100 * (0.35 + 0.65*0.9) = 93.5
            raw=100, relevance=0.5 → 100 * (0.35 + 0.65*0.5) = 67.5
            raw=100, relevance=0.2 → 100 * (0.35 + 0.65*0.2) = 48.0
            raw=100, relevance=0.0 → 100 * (0.35 + 0.65*0.0) = 35.0
        """
        multiplier = RELEVANCE_FLOOR + RELEVANCE_SLOPE * relevance_score
        return raw_score * multiplier

    def _embed_batch(self, texts: List[str]) -> Optional[List[np.ndarray]]:
        """Embed a batch of texts using Gemini Embedding 2."""
        try:
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=texts,
                task_type=EMBEDDING_TASK_TYPE,
                output_dimensionality=EMBEDDING_DIMENSIONS,
            )
            # result['embedding'] is a list of lists for batch input
            embeddings = result["embedding"]
            return [np.array(e, dtype=np.float32) for e in embeddings]
        except Exception as e:
            logger.error(f"Embedding API error: {e}")
            return None

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
