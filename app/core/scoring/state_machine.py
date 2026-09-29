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


def _apply_channel_assigning_side_effects(db: Session, run_id: int) -> None:
    """`channel_assigning` hedefli geçişin TÜM yan etkileri — commit ETMEZ.

    Tek source-of-truth: hem transition() hem begin_channel_assignment()
    buradan geçer (Codex v8-1: dispatcher transition_atomic kullandığı için
    version artışı ve ADS/SOCIAL stale işaretleri production yolunda hiç
    koşmuyordu).

    Yan etkiler:
      - ContentOutput.is_stale=True (kanal reassign önceki içeriği geçersiz kılar)
      - ADS AdGenerationSet.is_stale=True (status korunur; AdGroup'ların çoğunda
        content_output_id=NULL olduğu için ContentOutput işareti kapsamıyordu)
      - channel_assignment_version += 1 (üretim-ortası yarışın deterministik
        tespiti — SOCIAL kayıt-öncesi kilitli doğrulama bunu okur)
      - SOCIAL kategori/fikir/içerik ÜÇ tablo stale (parent-child invariant)
    """
    from sqlalchemy import func

    _mark_run_outputs_stale(db, run_id)

    db.execute(
        update(ScoringRun)
        .where(ScoringRun.id == run_id)
        .values(
            channel_assignment_version=func.coalesce(
                ScoringRun.channel_assignment_version, 1
            ) + 1
        )
    )


def _mark_run_outputs_stale(db: Session, run_id: int) -> int:
    """Tek run'ın TÜM üretilmiş çıktılarını stale işaretle — commit ETMEZ.

    Ortak parça (plan v13, F1-B): hem kanal reassignment yan etkisi hem politika/anchor
    mutasyonu (invalidate_workspace_outputs) hem de keyword/pool stale wrapper'ları bunu kullanır.
    Reassignment'a özgü channel_assignment_version artışı BURADA DEĞİL — çağıranda kalır.
    Kapsanan çıktılar:
      - ContentOutput (is_stale=True)
      - AdGenerationSet (mark_run_ad_sets_stale)
      - SocialBrief (is_stale=True)
      - SocialCategory, SocialIdea, SocialContent (is_stale=True; brief'li ve legacy zincir)

    SOCIAL child ID'leri parent güncellemesinden ÖNCE toplanır (sıra korunmalı).
    """
    stale_count = db.query(ContentOutput).filter(
        ContentOutput.scoring_run_id == run_id
    ).update({"is_stale": True}, synchronize_session=False)

    from app.generators.ads.generation_sets import mark_run_ad_sets_stale
    mark_run_ad_sets_stale(db, run_id)

    from app.database.models import SocialBrief, SocialCategory, SocialContent, SocialIdea
    from sqlalchemy import or_

    stale_brief_ids = [
        row.id for row in db.query(SocialBrief.id).filter(
            SocialBrief.scoring_run_id == run_id
        ).all()
    ]

    cat_filter = SocialCategory.scoring_run_id == run_id
    if stale_brief_ids:
        cat_filter = or_(cat_filter, SocialCategory.brief_id.in_(stale_brief_ids))
    stale_cat_ids = [
        row.id for row in db.query(SocialCategory.id).filter(cat_filter).all()
    ]

    idea_conditions = []
    if stale_cat_ids:
        idea_conditions.append(SocialIdea.category_id.in_(stale_cat_ids))
    if stale_brief_ids:
        idea_conditions.append(SocialIdea.brief_id.in_(stale_brief_ids))

    if idea_conditions:
        stale_idea_ids = [
            row.id for row in db.query(SocialIdea.id).filter(or_(*idea_conditions)).all()
        ]
    else:
        stale_idea_ids = []

    content_conditions = []
    if stale_idea_ids:
        content_conditions.append(SocialContent.idea_id.in_(stale_idea_ids))
    if stale_brief_ids:
        content_conditions.append(SocialContent.brief_id.in_(stale_brief_ids))

    if content_conditions:
        stale_content_ids = [
            row.id for row in db.query(SocialContent.id).filter(or_(*content_conditions)).all()
        ]
    else:
        stale_content_ids = []

    if stale_brief_ids:
        stale_count += db.query(SocialBrief).filter(
            SocialBrief.id.in_(stale_brief_ids)
        ).update({"is_stale": True}, synchronize_session=False)

    if stale_cat_ids:
        stale_count += db.query(SocialCategory).filter(
            SocialCategory.id.in_(stale_cat_ids)
        ).update({"is_stale": True}, synchronize_session=False)

    if stale_idea_ids:
        stale_count += db.query(SocialIdea).filter(
            SocialIdea.id.in_(stale_idea_ids)
        ).update({"is_stale": True}, synchronize_session=False)

    if stale_content_ids:
        stale_count += db.query(SocialContent).filter(
            SocialContent.id.in_(stale_content_ids)
        ).update({"is_stale": True}, synchronize_session=False)

    return stale_count


def invalidate_workspace_outputs(
    db: Session,
    brand_profile_id: int,
    only_algorithm_version: str | None = None,
) -> int:
    """Workspace run'larının üretilmiş çıktılarını stale işaretle.

    Politika/anchor mutasyonunda çağrılır (plan v13): mutasyonla AYNI
    transaction'da koşar, commit ETMEZ, etkilenen satır sayısını döndürür.
    Etkin politika değişmediyse ÇAĞRILMAMALIDIR (çağıran karar verir).
    channel_assignment_version'a dokunmaz (o yalnız reassignment yan etkisi).

    only_algorithm_version (v2.1 Faz C, Codex #2): strateji onayı YALNIZ
    v2_1 run'larını bayatlatır — v2 run'lar stratejiden bağımsızdır ve
    içerikleri de bayatlatılmaz ("mevcut müşterilerin v2 davranışı
    değişmez" garantisi). Politika/anchor çağrıları filtresiz kalır.
    """
    from app.database.models import ScoringRun as _ScoringRun

    run_query = db.query(_ScoringRun.id).filter(
        _ScoringRun.brand_profile_id == brand_profile_id
    )
    if only_algorithm_version is not None:
        run_query = run_query.filter(
            _ScoringRun.algorithm_version == only_algorithm_version
        )
    run_ids = [r.id for r in run_query.all()]
    total = 0
    for run_id in run_ids:
        total += _mark_run_outputs_stale(db, run_id)
    return total


def begin_channel_assignment(db: Session, run: ScoringRun, from_status: str) -> bool:
    """Atomic CAS + channel_assigning yan etkileri TEK transaction'da.

    Dispatcher (enqueue_channel_assignment) bunun yerine transition_atomic +
    mark_run_content_stale kullanıyordu → version/ADS/SOCIAL yan etkileri
    atlanıyordu (Codex v8-1). Returns False if status already changed
    (yan etki UYGULANMAZ, transaction geri alınır).
    """
    if not _is_valid_transition(from_status, "channel_assigning"):
        raise ValueError(
            f"Invalid transition: {from_status} -> channel_assigning"
        )

    result = db.execute(
        update(ScoringRun)
        .where(ScoringRun.id == run.id)
        .where(ScoringRun.status == from_status)
        .values(status="channel_assigning")
        .returning(ScoringRun.id)
    )
    if result.first() is None:
        db.rollback()
        return False

    _apply_channel_assigning_side_effects(db, run.id)
    db.commit()
    # CAS/UPDATE'ler ORM kopyasını bypass etti — bayat attr kalmasın
    db.expire(run, ["status", "channel_assignment_version"])
    return True


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
        _apply_channel_assigning_side_effects(db, run.id)
        # Version DB'de UPDATE ile arttı — ORM kopyası bayat kalmasın
        db.expire(run, ["channel_assignment_version"])

    # Status değişikliği + ilgili timestamp
    run.status = target
    timestamp_col = _get_timestamp_col_name(target)
    if timestamp_col:
        setattr(run, timestamp_col, datetime.utcnow())

    db.commit()


def mark_workspace_content_stale(db: Session, brand_profile_id: int) -> int:
    """Workspace'e ait TÜM scoring run'larının üretilmiş çıktılarını stale işaretle ve commit et.

    Çağrılma noktası: workspace keyword refresh tamamlandığında (brand_profile.py).
    Keyword değişiklikleri skor ve kanal atamasını geçersiz kılar; dolayısıyla
    önceki içerikler, ADS setleri, SocialBrief ve sosyal alt zincirinin tamamı
    stale işaretlenir.

    İşlem `_mark_run_outputs_stale` ortak yardımcısı üzerinden her run için
    uygulanır ve tüm run'lar işlendikten sonra tek commit yapılır.
    `channel_assignment_version` artırılmaz.

    Returns:
        total: Stale işaretlenen toplam kayıt sayısı.
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

    total = 0
    for run_id in run_ids:
        total += _mark_run_outputs_stale(db, run_id)
    db.commit()
    return total


def mark_run_content_stale(db: Session, scoring_run_id: int) -> int:
    """Tek scoring run'a ait TÜM üretilmiş çıktıları stale işaretle ve commit et.

    Kanal havuzu değiştiğinde veya otomatik/manuel kanal ataması yeniden
    başladığında kullanılır (channels.py). ContentOutput, ADS setleri,
    SocialBrief ve sosyal alt zincirinin tamamını `_mark_run_outputs_stale`
    üzerinden stale yapar.

    Run status'unu veya channel_assignment_version'ı DEĞİŞTİRMEZ. Tek commit yapar.

    Returns:
        stale_count: Stale işaretlenen toplam kayıt sayısı.
    """
    stale_count = _mark_run_outputs_stale(db, scoring_run_id)
    db.commit()
    return stale_count


def restore_status_after_enqueue_failure(
    db: Session,
    run_id: int,
    *,
    expected_status: str,
    restore_status: str,
) -> bool:
    """
    Celery enqueue hatasında status'u kontrollü geri al.

    Bu normal state transition değil; sadece task kuyruğa verilemeden
    `channel_assigning` statüsüne geçilmişse kullanılır.
    """
    result = db.execute(
        update(ScoringRun)
        .where(ScoringRun.id == run_id)
        .where(ScoringRun.status == expected_status)
        .values(status=restore_status)
        .returning(ScoringRun.id)
    )
    db.commit()
    return result.first() is not None


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
