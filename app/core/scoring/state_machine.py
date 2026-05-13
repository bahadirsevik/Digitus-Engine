"""
ScoringRun state machine.

Tüm status geçişleri bu modül üzerinden yapılır.
Direkt .status = atamaları yasaktır (lint guard ile kontrol edilir).
"""
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import update

from app.database.models import ScoringRun, ChannelPool, ContentOutput

# Geçerli geçişler (from -> {to, ...})
_VALID_TRANSITIONS: Dict[str, set] = {
    "pending": {"scoring"},
    "scoring": {"scored", "failed"},
    "scored": {"relevance_computing", "channel_assigning", "failed"},
    "relevance_computing": {"relevance_computed", "failed", "channel_assigning"},
    "relevance_computed": {"channel_assigning", "failed"},
    "channel_assigning": {"channel_assigned", "failed"},
    "channel_assigned": {"completed", "relevance_computing", "channel_assigning", "failed"},
    "completed": {"channel_assigning", "failed"},
    "failed": {"scoring", "relevance_computing", "channel_assigning"},
    # backward-compat: eski status değerlerini de kabul et
    "intent_analysis": {"completed", "failed"},
}


def _is_valid_transition(from_status: str, to_status: str) -> bool:
    """Geçiş izinli mi?"""
    allowed = _VALID_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def _get_timestamp_col_name(target_status: str) -> Optional[str]:
    """Status'a karşılık gelen timestamp kolon adı."""
    return {
        "scoring": "started_at",
        "scored": "completed_at",
        "relevance_computing": "relevance_started_at",
        "relevance_computed": "relevance_completed_at",
    }.get(target_status)


def transition(db: Session, run: ScoringRun, target: str):
    """
    State machine geçişi + yan etkiler.
    Geçersiz geçiş ValueError fırlatır.

    ContentOutput.is_stale matrisi (tek source-of-truth):
      SET True  → hedef durum `channel_assigning` olan HER geçiş
                  (channel reassign önceki içeriği geçersiz kılar)
      SET True  → workspace keyword refresh (brand_profile.py → mark_workspace_content_stale)
      RESET yok → content tekrar üretildiğinde yeni ContentOutput satırı oluşturulur;
                  eski stale satır silinmez ama export'ta is_stale=False filtresiyle dışlanır.

    Diğer yan etkiler:
      channel_assigned → relevance_computing: ChannelPool temizlenir
      (relevance yeniden sıralamaya gireceği için eski pool stale)
    """
    from_status = run.status

    if not _is_valid_transition(from_status, target):
        raise ValueError(f"Invalid transition: {from_status} -> {target}")

    # Yan etkiler
    if from_status == "channel_assigned" and target == "relevance_computing":
        # PoolBuilder relevance ile sıralama yapıyor — pool stale olur
        db.query(ChannelPool).filter(
            ChannelPool.scoring_run_id == run.id
        ).delete(synchronize_session=False)

    if target == "channel_assigning":
        # Kanal yeniden atanacak → mevcut içerik stale
        db.query(ContentOutput).filter(
            ContentOutput.scoring_run_id == run.id
        ).update({"is_stale": True}, synchronize_session=False)

    # Status değişikliği + ilgili timestamp
    run.status = target
    timestamp_col = _get_timestamp_col_name(target)
    if timestamp_col:
        setattr(run, timestamp_col, datetime.utcnow())

    db.commit()


def mark_workspace_content_stale(db: Session, brand_profile_id: int) -> int:
    """
    Workspace'e ait TÜM scoring run'larının ContentOutput'larını stale işaretle.

    Çağrılma noktası: workspace keyword refresh tamamlandığında.
    Keyword değişiklikleri skor ve kanal atamasını geçersiz kılar; dolayısıyla
    önceki içerikler de stale sayılır.

    Returns: stale işaretlenen satır sayısı.
    """
    from app.database.models import ScoringRun as _ScoringRun

    run_ids = [
        r.id
        for r in db.query(_ScoringRun.id).filter(
            _ScoringRun.brand_profile_id == brand_profile_id
        ).all()
    ]
    if not run_ids:
        return 0

    result = db.execute(
        update(ContentOutput)
        .where(ContentOutput.scoring_run_id.in_(run_ids))
        .where(ContentOutput.is_stale == False)  # noqa: E712
        .values(is_stale=True)
        .returning(ContentOutput.id)
    )
    db.commit()
    return len(result.fetchall())


def transition_atomic(
    db: Session,
    run: ScoringRun,
    target: str,
    from_status: str,
) -> bool:
    """
    State machine kuralları içinde atomic compare-and-set.
    Returns True if transition succeeded, False if status already changed.

    NOT: Yan etkiler atomic değil; transition_atomic sadece status için.
    Yan etki gerektiren geçişler transition() üzerinden yapılır.
    """
    if not _is_valid_transition(from_status, target):
        raise ValueError(f"Invalid transition: {from_status} -> {target}")

    values_dict: Dict[str, Any] = {"status": target}
    timestamp_col_name = _get_timestamp_col_name(target)
    if timestamp_col_name:
        values_dict[timestamp_col_name] = datetime.utcnow()

    result = db.execute(
        update(ScoringRun)
        .where(ScoringRun.id == run.id)
        .where(ScoringRun.status == from_status)
        .values(**values_dict)
        .returning(ScoringRun.id)
    )
    db.commit()

    return result.first() is not None
