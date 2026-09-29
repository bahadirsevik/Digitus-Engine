# -*- coding: utf-8 -*-
"""Salt-Okunur Sosyal İçerik Sonuç ve Polling Servisi (F1-G.5.7.2).

Bu modül SocialGenerationAttempt (stage='contents') sonucunu workspace ve brief
güvenliği altında okur, fail-closed kurallarla doğrular ve deterministik,
snapshot sırasını koruyan immutable DTO'lara dönüştürür.

Kurallar:
- Salt-okunurdur: db.commit(), rollback(), flush() veya db.begin() ÇAĞIRMAZ.
- with_for_update() KESİNLİKLE KULLANMAZ.
- ORM modellerini mutate etmez.
- AI veya Celery çağrısı YAPMAZ.
- Workspace izolasyonunu sıkı korur: Cross-workspace veya stage uyuşmazlığında
  bilgi sızdırmadan CONTENT_ATTEMPT_NOT_FOUND semantiği üretir.
- Tarihsel izolasyon garantisi sunar: Sonraki denemelerde üretilen içerikler
  geçmiş partial veya failed attempt sonuçlarına sızamaz.
- Hata mesajlarında dinamik ID, içerik metni, SQL veya kullanıcı verisi sızdırmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.social.content_batch_execution import (
    BATCH_WARNING_REASON_ALLOWLIST,
    SocialContentBatchWarning,
)
from app.core.social.content_persistence import (
    PersistedSocialContent,
    SocialContentPersistenceError,
    validate_and_build_persisted_social_content,
)
from app.core.social.content_worker_input import (
    extract_social_content_request_snapshot,
)
from app.database.models import (
    BrandProfile,
    ScoringRun,
    SocialBrief,
    SocialBriefKeyword,
    SocialBriefTarget,
    SocialCategory,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)

from app.generators.social.attempt_state import (
    ALLOWED_CONTENTS_FAILURE_REASONS,
)

# ==================== SABİT HATA KODLARI ====================

CONTENT_ATTEMPT_NOT_FOUND = "CONTENT_ATTEMPT_NOT_FOUND"
CONTENT_READ_INCONSISTENT = "CONTENT_READ_INCONSISTENT"


# ==================== DOMAIN EXCEPTIONS ====================


class SocialContentReadError(ValueError):
    """Sosyal içerik okuma domain hatası.

    Güvenlik: Hata mesajlarında raw ID, içerik metni, claim, JSON, SQL, task_id
    veya kullanıcı verisi bulunmaz; tüm mesajlar statiktir.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialContentReadError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


class SocialContentReadNotFoundError(SocialContentReadError):
    """Brief veya attempt bulunamadığında (workspace izolasyonu dahil) fırlatılır."""

    def __init__(
        self,
        message: str = "Sosyal içerik attempt'i veya brief bulunamadı.",
        *,
        error_code: str = CONTENT_ATTEMPT_NOT_FOUND,
        field: str | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code, field=field)


# ==================== DTO'LAR (IMMUTABLE) ====================


@dataclass(frozen=True)
class SocialContentReadWarning:
    """Salt-okunur içerik deneme uyarısı (immutable)."""

    idea_id: int
    reason_code: str
    claims: tuple[str, ...]
    ai_calls_used: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.idea_id, bool)
            or type(self.idea_id) is not int
            or self.idea_id <= 0
        ):
            raise SocialContentReadError(
                "Geçersiz uyarı idea_id.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="idea_id",
            )
        if self.reason_code not in BATCH_WARNING_REASON_ALLOWLIST:
            raise SocialContentReadError(
                "Geçersiz uyarı reason_code.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="reason_code",
            )
        if type(self.claims) is not tuple:
            object.__setattr__(self, "claims", tuple(self.claims))
        for c in self.claims:
            if type(c) is not str:
                raise SocialContentReadError(
                    "claims elemanları string olmalıdır.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="claims",
                )
        if self.reason_code != "content_rejected" and len(self.claims) != 0:
            raise SocialContentReadError(
                "content_rejected dışındaki uyarılar için claims boş tuple olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="claims",
            )
        if (
            isinstance(self.ai_calls_used, bool)
            or type(self.ai_calls_used) is not int
            or self.ai_calls_used not in (0, 1, 2)
        ):
            raise SocialContentReadError(
                "ai_calls_used 0, 1 veya 2 olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="ai_calls_used",
            )


@dataclass(frozen=True)
class SocialContentAttemptReadResult:
    """Sosyal içerik attempt salt-okunur polling sonucu (immutable)."""

    brief_id: int
    scoring_run_id: int
    attempt_id: int
    attempt_status: str
    requested_idea_ids: tuple[int, ...]
    successful_idea_ids: tuple[int, ...]
    unresolved_idea_ids: tuple[int, ...]
    contents: tuple[PersistedSocialContent, ...]
    warnings: tuple[SocialContentReadWarning, ...]
    reason_code: str | None
    replayed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_idea_ids", tuple(self.requested_idea_ids))
        object.__setattr__(self, "successful_idea_ids", tuple(self.successful_idea_ids))
        object.__setattr__(self, "unresolved_idea_ids", tuple(self.unresolved_idea_ids))
        object.__setattr__(self, "contents", tuple(self.contents))
        object.__setattr__(self, "warnings", tuple(self.warnings))


# ==================== WARNING PARSER YARDIMCISI ====================


def _parse_and_validate_warnings(
    attempt: SocialGenerationAttempt,
    requested_idea_ids: tuple[int, ...],
) -> tuple[SocialContentReadWarning, ...]:
    """Attempt.warnings alanını fail-closed doğrular ve requested sırasına göre kanonik sıralar.

    Sözleşme Kuralı:
    Attempt warnings listesi veritabanında hangi sırada saklanmış olursa olsun,
    DTO seviyesinde deterministik olarak requested_idea_ids sırasına göre
    kanonik biçimde sıralanır (safe canonical sorting).
    """
    raw_warnings = attempt.warnings
    if raw_warnings is None:
        return ()

    if not isinstance(raw_warnings, (list, tuple)):
        raise SocialContentReadError(
            "Attempt warnings liste formatında olmalıdır.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="warnings",
        )

    expected_keys = {"idea_id", "reason_code", "claims", "ai_calls_used"}
    requested_set = set(requested_idea_ids)
    seen_ids: set[int] = set()
    parsed_warnings: list[SocialContentReadWarning] = []

    for w in raw_warnings:
        if not isinstance(w, dict):
            raise SocialContentReadError(
                "Her uyarı elemanı bir sözlük (dict) olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="warnings",
            )
        if set(w.keys()) != expected_keys:
            raise SocialContentReadError(
                "Uyarı sözlüğü beklenen alanları içermiyor.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="warnings",
            )

        iid = w["idea_id"]
        if isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0:
            raise SocialContentReadError(
                "Geçersiz uyarı idea_id.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="idea_id",
            )
        if iid not in requested_set:
            raise SocialContentReadError(
                "Uyarılardaki fikir ID snapshot listesinde bulunmuyor.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="idea_id",
            )
        if iid in seen_ids:
            raise SocialContentReadError(
                "Uyarılarda mükerrer fikir ID tespit edildi.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="idea_id",
            )
        seen_ids.add(iid)

        rc = w["reason_code"]
        if not isinstance(rc, str) or rc not in BATCH_WARNING_REASON_ALLOWLIST:
            raise SocialContentReadError(
                "Geçersiz uyarı reason_code.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="reason_code",
            )

        claims_raw = w["claims"]
        if not isinstance(claims_raw, (list, tuple)):
            raise SocialContentReadError(
                "claims liste veya tuple olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="claims",
            )
        for c in claims_raw:
            if not isinstance(c, str):
                raise SocialContentReadError(
                    "claims elemanları string olmalıdır.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="claims",
                )
        if rc != "content_rejected" and len(claims_raw) != 0:
            raise SocialContentReadError(
                "content_rejected dışındaki uyarılar için claims boş olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="claims",
            )

        calls = w["ai_calls_used"]
        if isinstance(calls, bool) or not isinstance(calls, int) or calls not in (0, 1, 2):
            raise SocialContentReadError(
                "ai_calls_used 0, 1 veya 2 olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="ai_calls_used",
            )

        # BatchWarning parite doğrulaması
        SocialContentBatchWarning(
            idea_id=iid,
            reason_code=rc,
            claims=tuple(claims_raw),
            ai_calls_used=calls,
        )

        warning_obj = SocialContentReadWarning(
            idea_id=iid,
            reason_code=rc,
            claims=tuple(claims_raw),
            ai_calls_used=calls,
        )
        parsed_warnings.append(warning_obj)

    # Kanonik sıralama: requested_idea_ids sırasına göre
    req_index_map = {iid: idx for idx, iid in enumerate(requested_idea_ids)}
    parsed_warnings.sort(key=lambda item: req_index_map[item.idea_id])
    return tuple(parsed_warnings)


# ==================== ANA SALT-OKUNUR READ SERVİSİ ====================


def load_social_content_result(
    db: Session,
    *,
    brief_id: int,
    attempt_id: int,
    brand_profile_id: int,
    replayed: bool = False,
) -> SocialContentAttemptReadResult:
    """Contents attempt sonucunu salt-okunur olarak doğrular ve sonuç DTO'sunu döndürür (F1-G.5.7.2).

    Kurallar:
    - db.commit(), rollback(), flush() veya with_for_update() ÇAĞIRMAZ.
    - Tüm kontroller fail-closed ve sızıntısız statik hata mesajlarıyla yönetilir.
    - Tarihsel izolasyon korunur: Sonradan üretilen içerikler geçmiş attempt'lere yansıtılmaz.
    """
    if not isinstance(db, Session):
        raise SocialContentReadError(
            "db bir SQLAlchemy Session örneği olmalıdır.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="db",
        )

    if isinstance(brief_id, bool) or not isinstance(brief_id, int) or brief_id <= 0:
        raise SocialContentReadError(
            "brief_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="brief_id",
        )

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialContentReadError(
            "attempt_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="attempt_id",
        )

    if isinstance(brand_profile_id, bool) or not isinstance(brand_profile_id, int) or brand_profile_id <= 0:
        raise SocialContentReadError(
            "brand_profile_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="brand_profile_id",
        )

    if not isinstance(replayed, bool) or type(replayed) is not bool:
        raise SocialContentReadError(
            "replayed boolean olmalıdır.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="replayed",
        )

    # 1. Otoriter Bağlam ve Workspace İzolasyonu Doğrulaması
    # Arşivlenmiş (deleted_at IS NOT NULL) workspace diğer salt-okunur
    # akışlarla (idea_read, idea_retry_read, category read) aynı şekilde
    # fail-closed NOT_FOUND üretir.
    brief = (
        db.query(SocialBrief)
        .join(ScoringRun, SocialBrief.scoring_run_id == ScoringRun.id)
        .join(BrandProfile, ScoringRun.brand_profile_id == BrandProfile.id)
        .filter(
            SocialBrief.id == brief_id,
            ScoringRun.brand_profile_id == brand_profile_id,
            BrandProfile.id == brand_profile_id,
            BrandProfile.deleted_at.is_(None),
        )
        .first()
    )
    if brief is None:
        raise SocialContentReadNotFoundError(
            "Sosyal içerik attempt'i veya brief bulunamadı.",
            error_code=CONTENT_ATTEMPT_NOT_FOUND,
        )

    run = (
        db.query(ScoringRun)
        .filter(
            ScoringRun.id == brief.scoring_run_id,
            ScoringRun.brand_profile_id == brand_profile_id,
        )
        .first()
    )
    if run is None:
        # Cross-workspace erişim: fail-closed not-found
        raise SocialContentReadNotFoundError(
            "Sosyal içerik attempt'i veya brief bulunamadı.",
            error_code=CONTENT_ATTEMPT_NOT_FOUND,
        )

    attempt = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.id == attempt_id)
        .first()
    )
    if attempt is None:
        raise SocialContentReadNotFoundError(
            "Sosyal içerik attempt'i veya brief bulunamadı.",
            error_code=CONTENT_ATTEMPT_NOT_FOUND,
        )

    if attempt.brief_id != brief.id:
        raise SocialContentReadNotFoundError(
            "Sosyal içerik attempt'i veya brief bulunamadı.",
            error_code=CONTENT_ATTEMPT_NOT_FOUND,
        )

    if attempt.stage != "contents":
        raise SocialContentReadNotFoundError(
            "Sosyal içerik attempt'i veya brief bulunamadı.",
            error_code=CONTENT_ATTEMPT_NOT_FOUND,
        )

    allowed_statuses = {"pending", "running", "completed", "partial", "failed"}
    if attempt.status not in allowed_statuses:
        raise SocialContentReadError(
            "Geçersiz veya bilinmeyen attempt durumu.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="status",
        )

    # 2. Snapshot Doğrulaması
    try:
        snapshot = extract_social_content_request_snapshot(attempt)
    except Exception as exc:
        raise SocialContentReadError(
            "Attempt coverage snapshot verisi geçersiz veya bozuk.",
            error_code=CONTENT_READ_INCONSISTENT,
        ) from exc

    requested_idea_ids = tuple(snapshot.idea_ids)

    # attempt.requested_idea_ids ile snapshot idea_ids birebir ve aynı sırada olmalı
    if tuple(attempt.requested_idea_ids or ()) != requested_idea_ids:
        raise SocialContentReadError(
            "Attempt requested_idea_ids ile snapshot idea_ids uyuşmuyor.",
            error_code=CONTENT_READ_INCONSISTENT,
            field="requested_idea_ids",
        )

    # 3. Status Semantiği ve Partition Belirleme
    warnings_tuple = _parse_and_validate_warnings(attempt, requested_idea_ids)

    successful_idea_ids: tuple[int, ...]
    unresolved_idea_ids: tuple[int, ...]

    if attempt.status in ("pending", "running"):
        if len(warnings_tuple) > 0:
            raise SocialContentReadError(
                "Pending veya running durumunda uyarı bulunamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="warnings",
            )
        if attempt.reason_code is not None:
            raise SocialContentReadError(
                "Pending veya running durumunda reason_code bulunamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="reason_code",
            )
        if attempt.error_message is not None:
            raise SocialContentReadError(
                "Pending veya running durumunda error_message bulunamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="error_message",
            )

        successful_idea_ids = ()
        unresolved_idea_ids = ()
        result_reason_code = None

    elif attempt.status == "completed":
        if len(warnings_tuple) > 0:
            raise SocialContentReadError(
                "Completed durumunda uyarı bulunamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="warnings",
            )
        if attempt.reason_code is not None:
            raise SocialContentReadError(
                "Completed durumunda reason_code bulunamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="reason_code",
            )
        if attempt.error_message is not None:
            raise SocialContentReadError(
                "Completed durumunda error_message bulunamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="error_message",
            )

        successful_idea_ids = requested_idea_ids
        unresolved_idea_ids = ()
        result_reason_code = None

    elif attempt.status == "partial":
        if attempt.reason_code != "content_partial":
            raise SocialContentReadError(
                "Partial durumunda reason_code 'content_partial' olmalıdır.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="reason_code",
            )
        if not isinstance(attempt.error_message, str) or not attempt.error_message.strip():
            raise SocialContentReadError(
                "Partial durumunda error_message boş olamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="error_message",
            )
        if len(warnings_tuple) == 0:
            raise SocialContentReadError(
                "Partial durumunda warnings boş olamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="warnings",
            )

        unresolved_idea_ids = tuple(w.idea_id for w in warnings_tuple)
        unresolved_set = set(unresolved_idea_ids)

        # Unresolved, requested kümesinin boş olmayan proper subset'i olmalıdır
        if len(unresolved_set) >= len(requested_idea_ids):
            raise SocialContentReadError(
                "Partial durumunda tüm fikirler unresolved olamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="warnings",
            )

        successful_idea_ids = tuple(
            i for i in requested_idea_ids if i not in unresolved_set
        )
        result_reason_code = "content_partial"

    elif attempt.status == "failed":
        if not isinstance(attempt.error_message, str) or not attempt.error_message.strip():
            raise SocialContentReadError(
                "Failed durumunda error_message boş olamaz.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="error_message",
            )

        if (
            attempt.reason_code is None
            or type(attempt.reason_code) is not str
            or not attempt.reason_code.strip()
            or attempt.reason_code not in ALLOWED_CONTENTS_FAILURE_REASONS
        ):
            raise SocialContentReadError(
                "Geçersiz failure reason_code.",
                error_code=CONTENT_READ_INCONSISTENT,
                field="reason_code",
            )

        if len(warnings_tuple) > 0:
            # Durum A: Item/batch failure ve warnings mevcut
            # Orchestration gereği warnings varsa reason_code tam olarak "content_generation_failed" olmalıdır
            if attempt.reason_code != "content_generation_failed":
                raise SocialContentReadError(
                    "Uyarılı failed durumunda reason_code 'content_generation_failed' olmalıdır.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="reason_code",
                )
            unresolved_idea_ids = tuple(w.idea_id for w in warnings_tuple)
            unresolved_set = set(unresolved_idea_ids)
            successful_idea_ids = tuple(
                i for i in requested_idea_ids if i not in unresolved_set
            )
        else:
            # Durum B: Bootstrap / system failure ve warnings boş
            unresolved_idea_ids = requested_idea_ids
            successful_idea_ids = ()

        result_reason_code = attempt.reason_code

    # 4. Yalnız Tarihsel Başarılı Fikirler İçin İçerik Doğrulaması (Tarihsel İzolasyon)
    contents_list: list[PersistedSocialContent] = []

    if len(successful_idea_ids) > 0:
        # Önceden hedefleri, kategorileri ve brief keyword'lerini sorgula
        targets = (
            db.query(SocialBriefTarget)
            .filter(SocialBriefTarget.brief_id == brief.id)
            .all()
        )
        targets_by_id = {t.id: t for t in targets}

        categories = (
            db.query(SocialCategory)
            .filter(SocialCategory.brief_id == brief.id)
            .all()
        )
        categories_by_id = {c.id: c for c in categories}

        brief_keywords = (
            db.query(SocialBriefKeyword.keyword_id)
            .filter(SocialBriefKeyword.brief_id == brief.id)
            .all()
        )
        brief_keyword_ids = {bk[0] for bk in brief_keywords}

        # İlgili fikirleri yükle
        ideas_rows = (
            db.query(SocialIdea)
            .filter(SocialIdea.id.in_(successful_idea_ids))
            .all()
        )
        ideas_by_id = {i.id: i for i in ideas_rows}

        for idea_id in successful_idea_ids:
            if idea_id not in ideas_by_id:
                raise SocialContentReadError(
                    "Başarılı fikir kaydı bulunamadı.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )
            idea = ideas_by_id[idea_id]

            if idea.brief_id != brief.id:
                raise SocialContentReadError(
                    "Fikir bu brief'e ait değil.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )
            if idea.is_stale is not False:
                raise SocialContentReadError(
                    "Başarılı fikir güncel değil (stale).",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )

            # Hedef doğrulama
            if idea.brief_target_id is None or idea.brief_target_id not in targets_by_id:
                raise SocialContentReadError(
                    "Fikrin hedefi bu brief'e ait değil.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )
            target = targets_by_id[idea.brief_target_id]
            if idea.target_platform != target.platform or idea.content_format != target.content_format:
                raise SocialContentReadError(
                    "Fikrin platform veya formatı bağlı hedef ile uyuşmuyor.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )

            # Kategori doğrulama
            if idea.category_id is None or idea.category_id not in categories_by_id:
                raise SocialContentReadError(
                    "Fikrin kategorisi bu brief'e ait değil.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )
            cat = categories_by_id[idea.category_id]
            if cat.scoring_run_id != brief.scoring_run_id or cat.is_stale is not False:
                raise SocialContentReadError(
                    "Fikrin kategorisi scoring run ile tutarsız veya stale.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )

            # Keyword doğrulama
            if idea.keyword_id is None or idea.keyword_id not in brief_keyword_ids:
                raise SocialContentReadError(
                    "Fikrin anahtar kelimesi brief üyeliğinde bulunmuyor.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="successful_idea_ids",
                )

            # SocialContent satırını sorgula
            content_rows = (
                db.query(SocialContent)
                .filter(
                    SocialContent.idea_id == idea_id,
                    SocialContent.brief_id == brief.id,
                    SocialContent.is_stale.is_(False),
                )
                .all()
            )
            if len(content_rows) == 0:
                raise SocialContentReadError(
                    "Başarılı fikir için geçerli içerik kaydı bulunamadı.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="contents",
                )
            if len(content_rows) > 1:
                raise SocialContentReadError(
                    "Fikir için birden fazla aktif içerik kaydı bulundu.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="contents",
                )
            content_row = content_rows[0]

            # Cross-brief içerik kontrolü
            cross_brief_check = (
                db.query(SocialContent.id)
                .filter(
                    SocialContent.idea_id == idea_id,
                    SocialContent.brief_id != brief.id,
                    SocialContent.is_stale.is_(False),
                )
                .first()
            )
            if cross_brief_check is not None:
                raise SocialContentReadError(
                    "Cross-brief içerik satırı tespit edildi.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="contents",
                )

            # PersistedSocialContent sözleşme doğrulaması
            try:
                persisted_content = validate_and_build_persisted_social_content(
                    content_row,
                    target=target,
                    idea=idea,
                    brief=brief,
                )
            except Exception as exc:
                raise SocialContentReadError(
                    "İçerik kaydı persistence sözleşmesini ihlal ediyor.",
                    error_code=CONTENT_READ_INCONSISTENT,
                    field="contents",
                ) from exc

            contents_list.append(persisted_content)

    return SocialContentAttemptReadResult(
        brief_id=brief.id,
        scoring_run_id=brief.scoring_run_id,
        attempt_id=attempt.id,
        attempt_status=attempt.status,
        requested_idea_ids=requested_idea_ids,
        successful_idea_ids=successful_idea_ids,
        unresolved_idea_ids=unresolved_idea_ids,
        contents=tuple(contents_list),
        warnings=warnings_tuple,
        reason_code=result_reason_code,
        replayed=replayed,
    )
