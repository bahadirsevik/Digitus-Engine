"""
Keyword Relevance Scorer using Gemini Embedding 2.
Calculates cosine similarity between keywords and brand profile anchors.
"""
import hashlib
import json
import logging
import re
import time
from typing import List, Dict, Tuple, Optional

import numpy as np
from google import genai
from google.genai import types as genai_types

from app.core.site_analyzer.turkish_normalizer import (
    NORMALIZER_VERSION,
    normalize_turkish,
)

logger = logging.getLogger(__name__)

# Plan E: STABIL model (preview 2026-07-14'te embedding-001 gibi kapanma
# riski taşır); canlı doğrulandı (16.07): 3 per-text Content → 3 embedding.
EMBEDDING_MODEL = "models/gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768
# task_type KALDIRILDI (plan E) — yerine tutarlı instruction öneki.
# Önek yalnız API'ye giden metne eklenir; cache md5'i HAM normalize metin
# üzerindendir, önek değişimi INSTRUCTION_VERSION ile anahtara yansır.
#
# v2 (Codex v9-1): Google'ın simetrik anlamsal benzerlik için belgelediği
# resmi format. A/B ölçümü (16.07, run-18/723 kelime): official, eski
# skorlarla daha yüksek süreklilik verdi (tie-aware Spearman 0.58 vs
# custom 0.54) ve custom ile zaten uyumluydu (|Δ| ort 0.02, havuz
# Jaccard 0.82-1.00). v1 (deneysel Türkçe önek) drift script'inde
# karşılaştırma varyantı olarak yaşıyor.
EMBEDDING_INSTRUCTION_VERSION = 2
EMBEDDING_INSTRUCTION = "task: sentence similarity | query: "
BATCH_SIZE = 50  # Gemini embedding batch limit
EMBEDDING_RETRY_BASE_DELAY = 1.0
EMBEDDING_RATE_LIMIT_DELAY = 65.0  # wait > 60s for per-minute quota reset on 429
INTER_BATCH_DELAY = 0.3  # seconds between batches to avoid quota bursting
EMBEDDING_CACHE_TTL = 7 * 24 * 3600  # 7 days in seconds
EMBEDDING_FALLBACK_SCORE = 0.5  # neutral "don't know" when embedding fails
# Plan E fallback-storm sınırı: kurtarma çağrıları (batch retry + ikiye
# bölme + tekiller) compute_relevance başına TOPLAM bu bütçeden düşer;
# bitince neutral fallback + telemetride 'embedding_fallback_exhausted'.
# Üst sınır: mutlu yol 16 çağrı (723 kelime/50 + anchor) + bütçe 10 = 26
# (plan tavanı 16+1+2+10=29'un altında).
EMBEDDING_FAILURE_CALL_BUDGET = 10
# Codex v9-2: embed yanıtı token sayısı DÖNDÜRMÜYOR (canlı probe 16.07:
# metadata=None, statistics=None) — maliyet muhasebesi için belgeli yerel
# tahmin kullanılır. KALİBRASYON KANITI (Codex v10-5, canlı count_tokens
# probe 16.07, 60 örnek + instruction): embedding modeli gerçekte 4.65
# chars/token → /4 tahmini maliyeti ~%16 FAZLA gösterir (muhafazakâr =
# üst tahmin). Event'ler cache_status ':est' son ekiyle işaretlidir;
# D raporu bu değerleri "estimated" olarak sunmalıdır.
EMBEDDING_CHARS_PER_TOKEN = 4


def estimate_embedding_tokens(texts: List[str]) -> int:
    """Instruction öneki DAHİL yaklaşık giriş token sayısı."""
    total_chars = sum(len(EMBEDDING_INSTRUCTION) + len(t) for t in texts)
    return max(1, total_chars // EMBEDDING_CHARS_PER_TOKEN)

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

    def __init__(self, api_key: Optional[str] = None, redis_client=None,
                 collector=None):
        self.client = genai.Client(api_key=api_key) if api_key else None
        self._redis = redis_client
        # Usage telemetrisi (Codex v8-3): embedding çağrıları da ai_usage_events
        # kapsamına girer — "tam run maliyeti tek SQL" hedefi relevance'sız yalan
        # olur. Collector opsiyonel + fail-open; embed response token sayısı
        # döndürmediği için token alanları NULL kalır (istek sayısı + latency
        # kaynak gerçek; maliyet bağlanması E fazında).
        self._collector = collector
        # Plan E: kurtarma çağrısı bütçesi (instance = compute_relevance ömrü)
        self._failure_calls_left = EMBEDDING_FAILURE_CALL_BUDGET
        self._fallback_exhausted_reported = False

    def close(self) -> None:
        """Embedding client'ını kapatır (Codex v9-3 — plan G sözleşmesinin
        embedding tarafı). İdempotent ve fail-open; tüm çağıranlar
        (sync endpoint, background relevance, drift script) finally'de çağırır
        — uzun ömürlü web process'inde httpx havuzları birikmesin."""
        client, self.client = self.client, None
        if client is None:
            return
        try:
            closer = getattr(client, "close", None)
            if callable(closer):
                closer()
        except Exception as close_error:  # pragma: no cover - fail-open
            logger.warning("Embedding client kapatma hatası: %s", close_error)

    @staticmethod
    def _cache_key(text: str) -> str:
        """Plan E cache anahtarı: model + dim + instruction_version +
        normalizer_version + md5(normalize metin). Önek/normalizer/model
        değişimi eski cache'i YAPISAL olarak geçersiz kılar — sessiz
        karışım imkânsız."""
        digest = hashlib.md5(text.encode()).hexdigest()
        return (
            f"embed:{EMBEDDING_MODEL}:{EMBEDDING_DIMENSIONS}:"
            f"i{EMBEDDING_INSTRUCTION_VERSION}:n{NORMALIZER_VERSION}:{digest}"
        )

    def _record_embedding(self, *, latency_ms: int, batch_size: int,
                          retry_reason: Optional[str] = None,
                          prompt_tokens: Optional[int] = None) -> None:
        if self._collector is None:
            return
        try:
            self._collector.record(
                stage="embedding",
                model=EMBEDDING_MODEL,
                # Codex v9-2: tahmini giriş token'ı (API sayım döndürmüyor);
                # PRICE_TABLE'daki embedding satırıyla maliyet üretir
                prompt_tokens=prompt_tokens,
                total_tokens=prompt_tokens,
                latency_ms=latency_ms,
                retry_reason=retry_reason,
                # ':est' — token alanları TAHMİNİDİR (chars/4, üst tahmin;
                # kalibrasyon: gerçek 4.65 chars/token, probe 16.07)
                cache_status=f"miss:{batch_size}:est",
            )
        except Exception as telemetry_error:  # fail-open
            logger.warning("Embedding telemetri kaydı başarısız: %s", telemetry_error)

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
        anchor_embeddings = self._embed_batch_cached(normalized_anchors)
        if anchor_embeddings is None:
            logger.error("Anchor embedding failed, returning default relevance")
            return [
                {"keyword": kw, "relevance_score": EMBEDDING_FALLBACK_SCORE, "matched_anchor": "none"}
                for kw in keywords
            ]

        # Embed keywords in batches
        results = []
        for batch_start in range(0, len(normalized_keywords), BATCH_SIZE):
            batch_end = batch_start + BATCH_SIZE
            kw_batch = normalized_keywords[batch_start:batch_end]
            original_batch = keywords[batch_start:batch_end]

            if batch_start > 0:
                time.sleep(INTER_BATCH_DELAY)

            kw_embeddings = self._embed_batch_cached(kw_batch)
            if kw_embeddings is None:
                # Fallback for this batch
                for kw in original_batch:
                    results.append({
                        "keyword": kw,
                        "relevance_score": EMBEDDING_FALLBACK_SCORE,
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

    def _embed_batch_cached(self, texts: List[str]) -> Optional[List[np.ndarray]]:
        """
        Embed texts with per-text Redis cache (key: embed:{model}:{md5(text)}, TTL 7 days).
        Returns embeddings in the same order as input texts.
        Falls back to direct _embed_batch for all texts if Redis is unavailable.

        Cache format: raw float32 bytes via numpy tobytes()/frombuffer().
        A stale/corrupt entry whose byte length doesn't match EMBEDDING_DIMENSIONS*4
        is treated as a cache miss so a fresh embedding is fetched.
        """
        if self._redis is None:
            embeddings = self._embed_batch(texts)
            # Savunma: sayı uyuşmazlığı ASLA kısa liste olarak yukarı sızmaz
            # (compute_relevance kw_embeddings[i] ile erişiyor — run-17 IndexError)
            if embeddings is not None and len(embeddings) != len(texts):
                return None
            return embeddings

        results: List[Optional[np.ndarray]] = [None] * len(texts)
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        expected_bytes = EMBEDDING_DIMENSIONS * 4  # float32 = 4 bytes per element

        # Check cache for each text
        for i, text in enumerate(texts):
            cache_key = self._cache_key(text)
            try:
                cached = self._redis.get(cache_key)
                if cached is not None and len(cached) == expected_bytes:
                    results[i] = np.frombuffer(cached, dtype=np.float32)
                else:
                    # Treat wrong-size bytes as a cache miss (dimension mismatch / corruption)
                    uncached_indices.append(i)
                    uncached_texts.append(text)
            except Exception:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Embed uncached texts
        if uncached_texts:
            embeddings = self._embed_batch(uncached_texts)
            if embeddings is None:
                # If any embedding fails and we had cache hits, still return None (consistency)
                # to trigger the fallback path in compute_relevance
                return None
            for idx, embedding in zip(uncached_indices, embeddings):
                results[idx] = embedding
                cache_key = self._cache_key(texts[idx])
                try:
                    self._redis.set(cache_key, embedding.tobytes(), ex=EMBEDDING_CACHE_TTL)
                except Exception:
                    pass  # Cache write failure is non-fatal

        # If any result is still None (shouldn't happen), return None to trigger fallback
        if any(r is None for r in results):
            return None
        return results  # type: ignore[return-value]

    # ═══════════════════════════════════════════════════════════
    # Plan E: sınırlı kurtarma zinciri (fallback storm sınırı)
    #   mutlu yol: batch başına 1 çağrı (bütçe DIŞI)
    #   geçici hata: 1 batch retry → ikiye bölme → tekiller (hepsi bütçeli)
    #   yapısal uyuşmazlık (n metin ≠ n embedding): doğrudan tekiller
    #   bütçe (TOPLAM ≤ EMBEDDING_FAILURE_CALL_BUDGET) bitince: None →
    #   neutral fallback + telemetride 'embedding_fallback_exhausted'
    # ═══════════════════════════════════════════════════════════

    def _embed_api_call(self, texts: List[str]) -> Optional[List[np.ndarray]]:
        """TEK provider çağrısı — retry YOK (kurtarma çağıranda, bütçeli).

        Her metin AYRI types.Content olarak sarılır (plan E — düz string
        listesi bu modelde TEK embedding döndürüyordu, run-17). Instruction
        öneki yalnız API metnine eklenir; cache md5'i ham normalize metin
        üzerindendir (önek INSTRUCTION_VERSION ile anahtarda).

        Dönüş: embeddings (tam liste) | None (yapısal uyuşmazlık).
        Geçici hatalar exception olarak yükselir (çağıran sınıflandırır).
        """
        contents = [
            genai_types.Content(
                parts=[genai_types.Part(text=EMBEDDING_INSTRUCTION + t)]
            )
            for t in texts
        ]
        est_tokens = estimate_embedding_tokens(texts)
        started = time.perf_counter()
        try:
            result = self.client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=contents,
                config=genai_types.EmbedContentConfig(
                    output_dimensionality=EMBEDDING_DIMENSIONS,
                ),
            )
        except Exception as e:
            self._record_embedding(
                latency_ms=int((time.perf_counter() - started) * 1000),
                batch_size=len(texts),
                retry_reason=str(e)[:200],
                prompt_tokens=est_tokens,
            )
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        embeddings = result.embeddings
        if len(embeddings) != len(texts):
            # Yapısal anomali — retry ANLAMSIZ (deterministik davranış);
            # hiçbir koşulda kısa liste dönmez (run-17 IndexError dersi).
            # Telemetride başarı DEĞİL, ayrı sınıf olarak görünür.
            self._record_embedding(
                latency_ms=latency_ms, batch_size=len(texts),
                retry_reason=(
                    f"embedding_count_mismatch:{len(embeddings)}/{len(texts)}"
                ),
                prompt_tokens=est_tokens,
            )
            logger.warning(
                "Embedding API %s metne %s embedding dondurdu (yapısal)",
                len(texts), len(embeddings),
            )
            return None
        self._record_embedding(latency_ms=latency_ms, batch_size=len(texts),
                               prompt_tokens=est_tokens)
        return [np.array(e.values, dtype=np.float32) for e in embeddings]

    def _consume_failure_call(self) -> bool:
        """Kurtarma çağrısı bütçesinden 1 düşer; bütçe bittiyse False +
        (bir kez) telemetride embedding_fallback_exhausted."""
        if self._failure_calls_left <= 0:
            if not self._fallback_exhausted_reported:
                self._fallback_exhausted_reported = True
                logger.error(
                    "Embedding kurtarma bütçesi tükendi (%s çağrı) — "
                    "kalan metinler neutral fallback alacak",
                    EMBEDDING_FAILURE_CALL_BUDGET,
                )
                self._record_embedding(
                    latency_ms=0, batch_size=0,
                    retry_reason="embedding_fallback_exhausted",
                )
            return False
        self._failure_calls_left -= 1
        return True

    def _recovery_call(self, texts: List[str], *,
                       rate_limited: bool = False,
                       backoff: bool = True) -> Optional[List[np.ndarray]]:
        """Bütçeli tek kurtarma çağrısı. None: başarısız/bütçe yok.

        backoff=False: yapısal-uyuşmazlık tekilleri (sunucu sağlıklı,
        sorun yanıt şekli — bekleme anlamsız).
        """
        if not self._consume_failure_call():
            return None
        if backoff:
            time.sleep(
                EMBEDDING_RATE_LIMIT_DELAY if rate_limited
                else EMBEDDING_RETRY_BASE_DELAY
            )
        try:
            return self._embed_api_call(texts)
        except Exception as e:
            logger.warning("Embedding kurtarma çağrısı hata (%s metin): %s",
                           len(texts), str(e)[:120])
            return None

    def _recover_singles(self, texts: List[str], *,
                         backoff: bool = True) -> Optional[List[np.ndarray]]:
        """Metinleri tek tek dener (her biri bütçeli); biri bile
        kurtarılamazsa None (batch fallback'e düşer)."""
        singles: List[np.ndarray] = []
        for text in texts:
            one = self._recovery_call([text], backoff=backoff)
            if one is None:
                return None
            singles.extend(one)
        return singles

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        message = str(exc)
        return "429" in message or "quota" in message.lower()

    def _embed_batch(self, texts: List[str]) -> Optional[List[np.ndarray]]:
        """Batch embedding — mutlu yol 1 çağrı, kurtarma sınırlı (plan E)."""
        if self.client is None:
            logger.error("Embedding istemi olusturulamadi: api_key verilmedi")
            return None

        # Mutlu yol (bütçe DIŞI)
        try:
            result = self._embed_api_call(texts)
        except Exception as e:
            logger.warning(
                "Embedding batch hata (%s metin): %s — kurtarma zinciri",
                len(texts), str(e)[:120],
            )
            return self._recover_transient(texts, rate_limited=self._is_rate_limit(e))

        if result is not None:
            return result
        # Yapısal uyuşmazlık: batch retry atlanır (deterministik davranış,
        # tekrar denemek çağrı israfı), doğrudan tekiller — backoff'suz
        if len(texts) == 1:
            logger.error("Embedding API tek metne uyumsuz sonuç döndürdü")
            return None
        return self._recover_singles(texts, backoff=False)

    def _recover_transient(self, texts: List[str],
                           rate_limited: bool) -> Optional[List[np.ndarray]]:
        """Geçici hata kurtarması: 1 batch retry → ikiye bölme → tekiller."""
        retried = self._recovery_call(texts, rate_limited=rate_limited)
        if retried is not None:
            return retried
        if len(texts) < 2:
            return None

        mid = len(texts) // 2
        halves: List[np.ndarray] = []
        for part in (texts[:mid], texts[mid:]):
            got = self._recovery_call(part)
            if got is None and len(part) >= 2:
                got = self._recover_singles(part)
            if got is None:
                return None
            halves.extend(got)
        return halves

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
