"""Sürümlü relevance yenileme servisi (plan v13, Codex v11 #4/#5/#6).

API router'ından bağımsız TEK core uygulama — TÜM relevance yolları
(manuel /relevance/compute, profil-onayı background yolu, kanal atama task'ı)
bunu kullanır. Sözleşme:

- State transition YAPMAZ, task enqueue ETMEZ, SessionLocal AÇMAZ
  (çağıranın session'ı ile çalışır; sarmalayıcılar transition/auto-assign
  ve hata-politikalarını kendileri uygular).
- Önce TÜM skorlar hesaplanır ve uzunluk sözleşmesi doğrulanır; yazmadan
  HEMEN ÖNCE workspace kilitli okunup anchor_version == requested_anchor_version
  doğrulanır — eşit değilse HİÇBİR ŞEY yazılmaz (uzun iş sırasında profil
  değiştiyse eski sonuç 'yeni' diye kaydedilmez).
- Eski satır silme + yeni yazım + run.relevance_anchor_version güncelleme
  AYNI transaction'da (atomik). Hata → rollback, eski satırlar korunur.
- FALLBACK YASAĞI (plan_marka_profili_sadakati.md P1.2): embedding'i alınamayan
  TEK bir kelime bile varsa HİÇBİR satır yazılmaz ve RelevanceRefreshError
  fırlatılır. Yedek yöntem skoru (0.5) kalıcılaştırılmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import (
    BrandProfile,
    Keyword,
    KeywordRelevance,
    KeywordScore,
    ScoringRun,
)


class RelevanceRefreshError(Exception):
    """Relevance yenileme başarısız — eski satırlar korunmuş durumda.

    version_changed=True: iş sürerken workspace anchor_version değişti
    (sonuçlar bilinçli olarak YAZILMADI).
    fallback=True: en az bir kelimenin embedding'i alınamadı; yedek yöntem
    skorları kalıcılaştırılmadı (P1.2)."""

    def __init__(
        self, message: str, *, version_changed: bool = False, fallback: bool = False
    ):
        super().__init__(message)
        self.version_changed = version_changed
        self.fallback = fallback


@dataclass
class RelevanceRefreshResult:
    total_keywords: int
    computed: int
    failed: int
    average_relevance: float


def refresh_keyword_relevance(
    db: Session,
    scoring_run: ScoringRun,
    profile: BrandProfile,
    *,
    requested_anchor_version: int,
    collector=None,
    scorer=None,
) -> RelevanceRefreshResult:
    anchor_texts = (profile.profile_data or {}).get("anchor_texts", [])
    if not anchor_texts:
        raise RelevanceRefreshError("Profilde anchor text bulunamadı.")

    keyword_scores = (
        db.query(KeywordScore, Keyword)
        .join(Keyword, KeywordScore.keyword_id == Keyword.id)
        .filter(KeywordScore.scoring_run_id == scoring_run.id)
        .all()
    )
    if not keyword_scores:
        raise RelevanceRefreshError("Bu run için keyword skoru bulunamadı.")

    owns_scorer = scorer is None
    if scorer is None:
        from app.core.cache import get_redis_bytes_client
        from app.core.site_analyzer.relevance_scorer import RelevanceScorer

        scorer = RelevanceScorer(
            api_key=settings.GEMINI_API_KEY,
            redis_client=get_redis_bytes_client(),
            collector=collector,
        )

    keywords = [kw.keyword for _, kw in keyword_scores]
    try:
        relevance_results = scorer.compute_relevance(keywords, anchor_texts)
    finally:
        if owns_scorer:
            closer = getattr(scorer, "close", None)  # fake'ler close'suz olabilir
            if callable(closer):
                closer()

    # Uzunluk sözleşmesi (Codex v11): uyumsuzlukta zip sessizce kelime düşürür —
    # eski kayıtlar SİLİNMEDEN durulur.
    if len(relevance_results) != len(keyword_scores):
        raise RelevanceRefreshError(
            f"Relevance sonuç sayısı uyuşmuyor "
            f"({len(relevance_results)}/{len(keyword_scores)}) — "
            f"mevcut relevance kayıtları korundu, yeniden deneyin."
        )

    # FALLBACK YASAĞI (plan_marka_profili_sadakati.md P1.2).
    # RelevanceScorer, embedding batch'i patladığında 0.5 + method
    # 'fuzzy_fallback' üretir. Bu satırlar yazılırsa uydurma nötr skorlar
    # ChannelPool sıralamasına girer, run "taze" damgalanır ve kullanıcıya
    # hiçbir sinyal gitmez.
    #
    # Yalnız sürüm damgasını atlamak YETMEZ: kanal ataması bitiminde
    # `_finalize_pool_versions` (channel_engine.py) run.relevance_anchor_version'ı
    # yeniden yazar — "bir sonraki atama tazelesin" mekanizması bir sonraki
    # atamanın kendisi tarafından ezilir. Tek doğru davranış hiç yazmamaktır.
    #
    # Bu, channel_engine'in ZATEN belgelediği sözleşmedir: "Başarısızlık
    # assignment'ı BAŞARISIZ yapar (eski/neutral relevance ile devam YOK —
    # anchor değiştiyse eski sıralama yanlıştır)". Servis fallback yazıp
    # "başardım" döndüğü için sözleşme çiğneniyordu.
    failed = sum(1 for r in relevance_results if r.get("method") != "embedding")
    if failed:
        # Kilit henüz ALINMADI ve hiçbir yazım yapılmadı → rollback ÇAĞIRMA:
        # çağıranın aynı session'daki bekleyen işi boşuna atılmasın.
        raise RelevanceRefreshError(
            f"{failed}/{len(relevance_results)} kelimenin embedding'i alınamadı "
            f"— sonuçlar YAZILMADI, mevcut relevance kayıtları ve sürüm "
            f"korundu. Lütfen yeniden deneyin.",
            fallback=True,
        )

    # Yazmadan HEMEN ÖNCE kilitli sürüm doğrulaması (uzun embedding işi
    # sırasında profil değişmiş olabilir).
    locked = (
        db.query(BrandProfile)
        .filter(BrandProfile.id == profile.id)
        .with_for_update()
        .first()
    )
    if locked is None or locked.anchor_version != requested_anchor_version:
        db.rollback()
        raise RelevanceRefreshError(
            "Profil anchor'ları relevance hesaplanırken değişti — sonuçlar "
            "yazılmadı, güncel profil ile yeniden deneyin.",
            version_changed=True,
        )

    # POZİSYONEL eşleme (Codex v10-1): metin-tabanlı map aynı metinli farklı
    # Keyword ID'lerini eziyordu (snapshot mimarisi aynı metne birden çok ID
    # üretebilir).
    deduped: dict = {}
    for (_, kw), result in zip(keyword_scores, relevance_results):
        kw_id = kw.id
        if kw_id not in deduped or result["relevance_score"] > deduped[kw_id]["relevance_score"]:
            deduped[kw_id] = result

    try:
        db.query(KeywordRelevance).filter(
            KeywordRelevance.scoring_run_id == scoring_run.id
        ).delete(synchronize_session=False)

        computed = 0
        total_relevance = 0.0
        for kw_id, result in deduped.items():
            db.add(KeywordRelevance(
                scoring_run_id=scoring_run.id,
                keyword_id=kw_id,
                relevance_score=result["relevance_score"],
                matched_anchor=result.get("matched_anchor", ""),
                method=result.get("method", "embedding"),
            ))
            total_relevance += result["relevance_score"]
            computed += 1

        scoring_run.relevance_anchor_version = requested_anchor_version
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            f"Relevance yazımı başarısız (run {scoring_run.id}): {exc} — "
            f"eski satırlar korundu"
        )
        raise RelevanceRefreshError(f"Relevance yazımı başarısız: {exc}")

    avg = total_relevance / computed if computed > 0 else 0.0
    return RelevanceRefreshResult(
        total_keywords=len(keyword_scores),
        computed=computed,
        # Buraya yalnız failed == 0 iken ulaşılır (fallback yasağı yukarıda
        # erken döner). Alan API sözleşmesi için korunuyor.
        failed=failed,
        average_relevance=round(avg, 3),
    )
