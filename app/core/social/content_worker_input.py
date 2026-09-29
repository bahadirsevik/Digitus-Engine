# -*- coding: utf-8 -*-
"""Sosyal Brief İçerik Üretimi (contents) Worker Input ve Snapshot Hazırlığı (F1-G.5.2).

Bu modül Celery worker'ın AI çağrısından önce ihtiyaç duyacağı güvenilir,
kanonik ve doğrulanmış DB snapshot'ını hazırlar; contents attempt'ini claim eder,
her seçili fikir için deterministik SocialContentPromptInput ve SocialContentGroundingContext
nesnelerini üretir; önceden kaydedilmiş geçerli içerikleri atlar ve completed replay durumunu yönetir.

Kurallar:
- Celery task içermez.
- AI veya harici ağ çağrısı YAPMAZ.
- API endpoint içermez.
- SocialContent veya SocialIdea persist etmez / oluşturmaz / silmez / değiştirmez.
- db.begin(), commit() veya rollback() KESİNLİKLE ÇAĞIRMAZ.
- Attempt finalize etmez (finish_attempt çağırmaz).
- coverage alanını değiştirmez / mutasyona uğratmaz.
- DB'yi ve attempt coverage request snapshot'ını tek otorite kabul eder.
- Global kilit sırası (ScoringRun -> SocialBrief -> SocialGenerationAttempt) korunur.
- Completed replay yolunda work_items=() döner ve hiçbir DB yazımı yapmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.social.content_contract import (
    ContentTargetSpec,
)
from app.core.social.content_persistence import (
    SocialContentPersistenceError,
    validate_and_build_persisted_social_content,
)
from app.core.social.content_quality import (
    SocialContentGroundingContext,
)
from app.database.models import (
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
    claim_contents_attempt_for_worker,
)
from app.generators.social.brief_content_prompt import (
    SocialContentKeywordInput,
    SocialContentPromptInput,
    _validate_prompt_input,
    validate_grounding_prompt_parity,
)
from app.generators.social.format_matrix import (
    get_duration_preset,
    get_platform_format,
)


# ==================== DOMAIN EXCEPTIONS ====================


class SocialContentWorkerInputError(ValueError):
    """Worker input hazırlık domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CONTENT_WORKER_INPUT_INCONSISTENT",
        field: str | None = None,
        idea_id: int | None = None,
        brief_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field
        self.idea_id = idea_id
        self.brief_id = brief_id

    def __str__(self) -> str:
        parts = [f"[{self.error_code}]", self.message]
        if self.field is not None:
            parts.append(f"(field={self.field})")
        if self.idea_id is not None:
            parts.append(f"(idea_id={self.idea_id})")
        if self.brief_id is not None:
            parts.append(f"(brief_id={self.brief_id})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"SocialContentWorkerInputError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r}, "
            f"idea_id={self.idea_id!r}, brief_id={self.brief_id!r})"
        )


# Sabit Hata Kodları
CONTENT_WORKER_INPUT_INVALID = "CONTENT_WORKER_INPUT_INVALID"
CONTENT_WORKER_INPUT_INCONSISTENT = "CONTENT_WORKER_INPUT_INCONSISTENT"


# ==================== DTO'LAR (IMMUTABLE) ====================


@dataclass(frozen=True)
class SocialContentRequestSnapshot:
    """Attempt.coverage içindeki sürümlü ve dondurulmuş istek snapshot'ı (immutable)."""

    schema_version: str
    idea_ids: tuple[int, ...]
    product_facts: str | None
    trusted_brand_usp: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "idea_ids", tuple(self.idea_ids))


@dataclass(frozen=True)
class SocialContentWorkItem:
    """Tekil bir fikir için üretilen doğrulanmış içerik çalışma girdisi (immutable)."""

    idea_id: int
    prompt_input: SocialContentPromptInput
    grounding_context: SocialContentGroundingContext


@dataclass(frozen=True)
class SocialContentWorkerPreparation:
    """Worker hazırlık aşaması nihai çıktısı (immutable)."""

    attempt_id: int
    task_id: str
    brief_id: int
    scoring_run_id: int
    requested_idea_ids: tuple[int, ...]
    already_present_idea_ids: tuple[int, ...]
    already_completed: bool
    work_items: tuple[SocialContentWorkItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_idea_ids", tuple(self.requested_idea_ids))
        object.__setattr__(
            self,
            "already_present_idea_ids",
            tuple(self.already_present_idea_ids),
        )
        object.__setattr__(self, "work_items", tuple(self.work_items))


# ==================== SNAPSHOT PARSER ====================


def extract_social_content_request_snapshot(
    attempt: SocialGenerationAttempt,
) -> SocialContentRequestSnapshot:
    """Attempt.coverage alanındaki contents_request_v1 snapshot'ını fail-closed doğrular."""
    cov = attempt.coverage
    if not isinstance(cov, dict):
        raise SocialContentWorkerInputError(
            "Attempt coverage verisi bir sözlük (dict) olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="coverage",
        )

    if cov.get("schema_version") != "contents_request_v1":
        raise SocialContentWorkerInputError(
            "Geçersiz coverage schema_version.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="schema_version",
        )

    expected_top_keys = {"schema_version", "request"}
    if set(cov.keys()) != expected_top_keys:
        raise SocialContentWorkerInputError(
            "Attempt coverage yalnızca schema_version ve request alanlarını içermelidir.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="coverage",
        )

    req = cov.get("request")
    if not isinstance(req, dict):
        raise SocialContentWorkerInputError(
            "Coverage request bloğu bir sözlük olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="request",
        )

    expected_req_keys = {"idea_ids", "product_facts", "trusted_brand_usp"}
    if set(req.keys()) != expected_req_keys:
        raise SocialContentWorkerInputError(
            "Coverage request yalnızca idea_ids, product_facts ve trusted_brand_usp alanlarını içermelidir.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="request",
        )

    idea_ids_raw = req.get("idea_ids")
    if not isinstance(idea_ids_raw, list):
        raise SocialContentWorkerInputError(
            "idea_ids bir liste olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="idea_ids",
        )

    if len(idea_ids_raw) < 1 or len(idea_ids_raw) > 30:
        raise SocialContentWorkerInputError(
            "idea_ids sayısı 1 ile 30 arasında olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="idea_ids",
        )

    seen_ids: set[int] = set()
    cleaned_idea_ids: list[int] = []
    for raw_id in idea_ids_raw:
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise SocialContentWorkerInputError(
                "idea_ids elemanları pozitif tamsayı olmalıdır.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="idea_ids",
            )
        if raw_id in seen_ids:
            raise SocialContentWorkerInputError(
                "idea_ids içinde mükerrer ID bulunamaz.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="idea_ids",
            )
        seen_ids.add(raw_id)
        cleaned_idea_ids.append(raw_id)

    # attempt.requested_idea_ids ile birebir ve aynı sırada eşleşmeli
    req_ids_attempt = attempt.requested_idea_ids
    if not isinstance(req_ids_attempt, (list, tuple)):
        raise SocialContentWorkerInputError(
            "Attempt requested_idea_ids liste veya tuple olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="requested_idea_ids",
        )

    for raw_id in req_ids_attempt:
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise SocialContentWorkerInputError(
                "attempt.requested_idea_ids elemanları pozitif tamsayı olmalıdır.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="requested_idea_ids",
            )

    if tuple(cleaned_idea_ids) != tuple(req_ids_attempt):
        raise SocialContentWorkerInputError(
            "Snapshot idea_ids ile attempt.requested_idea_ids birebir ve aynı sırada eşleşmelidir.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            field="idea_ids",
        )

    # product_facts
    pf = req.get("product_facts")
    if pf is not None:
        if not isinstance(pf, str):
            raise SocialContentWorkerInputError(
                "product_facts string veya None olmalıdır.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="product_facts",
            )
        if len(pf) == 0 or pf != pf.strip():
            raise SocialContentWorkerInputError(
                "product_facts boş olamaz ve başında/sonunda boşluk içeremez.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="product_facts",
            )
        if len(pf) > 5000:
            raise SocialContentWorkerInputError(
                "product_facts 5000 karakter sınırını aşamaz.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="product_facts",
            )

    # trusted_brand_usp
    usp = req.get("trusted_brand_usp")
    if usp is not None:
        if not isinstance(usp, str):
            raise SocialContentWorkerInputError(
                "trusted_brand_usp string veya None olmalıdır.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="trusted_brand_usp",
            )
        if len(usp) == 0 or usp != usp.strip():
            raise SocialContentWorkerInputError(
                "trusted_brand_usp boş olamaz ve başında/sonunda boşluk içeremez.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="trusted_brand_usp",
            )
        if len(usp) > 5000:
            raise SocialContentWorkerInputError(
                "trusted_brand_usp 5000 karakter sınırını aşamaz.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                field="trusted_brand_usp",
            )

    return SocialContentRequestSnapshot(
        schema_version="contents_request_v1",
        idea_ids=tuple(cleaned_idea_ids),
        product_facts=pf,
        trusted_brand_usp=usp,
    )


# ==================== INTERNAL TIME VALIDATOR ====================


def _validate_now(now: datetime | None) -> datetime:
    """Timezone-aware UTC zamanını doğrular veya üretir."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError(
                "Enjekte edilen 'now' parametresi timezone-aware olmalıdır (tzinfo is None)."
            )
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


# ==================== ANA WORKER INPUT HAZIRLAMA SERVİSİ ====================


def prepare_social_content_worker_inputs(
    db: Session,
    *,
    attempt_id: int,
    task_id: str,
    now: datetime | None = None,
) -> SocialContentWorkerPreparation:
    """Contents attempt'ini worker için claim eder ve her fikir için doğrulanmış girdileri hazırlar.

    İşlem sırası:
    1. Girdi argümanlarının doğrulanması (db, attempt_id, task_id, now).
    2. Canonical kilit altında attempt'in claim edilmesi:
       ScoringRun -> SocialBrief -> SocialGenerationAttempt.
    3. Attempt.coverage request snapshot'ının (contents_request_v1) doğrulanması.
    4. Completed replay kontrolü: attempt zaten completed ise work_items=() ile erken dönüş.
    5. Otoriter DB zinciri doğrulaması:
       - Brief tazelik, kilit (locked_at) ve kanal atama sürüm denetimi.
       - Her fikir için category, target, format matrix ve brief keyword denetimi.
    6. Önceden kaydedilmiş geçerli SocialContent satırlarının tespiti ve skip edilmesi.
    7. Her eksik fikir için SocialContentPromptInput ve SocialContentGroundingContext üretilmesi.
    8. Deterministik SocialContentWorkerPreparation çıktısının dönülmesi.

    Transaction kuralları:
    - db.begin(), commit() veya rollback() ÇAĞIRMAZ.
    - Sadece claim durumunda flush yapılır.
    """
    current_time = _validate_now(now)

    if not isinstance(db, Session):
        raise SocialContentWorkerInputError(
            "db bir SQLAlchemy Session örneği olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INVALID,
            field="db",
        )

    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
        raise SocialContentWorkerInputError(
            "attempt_id pozitif bir tamsayı olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INVALID,
            field="attempt_id",
        )

    if not isinstance(task_id, str) or not task_id.strip():
        raise SocialContentWorkerInputError(
            "task_id boş olmayan bir string olmalıdır.",
            error_code=CONTENT_WORKER_INPUT_INVALID,
            field="task_id",
        )

    # 1. Canonical kilit ve attempt claim (contents aşaması)
    attempt, brief, scoring_run, already_completed = claim_contents_attempt_for_worker(
        db,
        attempt_id=attempt_id,
        task_id=task_id,
        now=current_time,
    )

    # 2. Coverage request snapshot'ının doğrulanması
    snapshot = extract_social_content_request_snapshot(attempt)

    # 4. Brief tazelik, kilit ve kanal atama sürüm kontrolleri
    if brief.is_stale is not False:
        raise SocialContentWorkerInputError(
            "Brief stale durumda; worker input hazırlanamaz.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            brief_id=brief.id,
        )

    if brief.locked_at is None:
        raise SocialContentWorkerInputError(
            "Brief henüz kilitlenmemiş; worker input hazırlanamaz.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            brief_id=brief.id,
        )

    if brief.channel_assignment_version != scoring_run.channel_assignment_version:
        raise SocialContentWorkerInputError(
            "Brief kanal atama sürümü güncel değil; worker input hazırlanamaz.",
            error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
            brief_id=brief.id,
        )

    # 5. DB Varlıklarının Yüklenmesi
    idea_rows = (
        db.query(SocialIdea)
        .filter(SocialIdea.id.in_(snapshot.idea_ids))
        .all()
    )
    idea_map: dict[int, SocialIdea] = {i.id: i for i in idea_rows}

    for idea_id in snapshot.idea_ids:
        if idea_id not in idea_map:
            raise SocialContentWorkerInputError(
                "Fikir bulunamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea_id,
                brief_id=brief.id,
            )

    cat_ids = {idea_map[id_].category_id for id_ in snapshot.idea_ids}
    category_rows = (
        db.query(SocialCategory)
        .filter(SocialCategory.id.in_(cat_ids))
        .all()
    )
    category_map: dict[int, SocialCategory] = {c.id: c for c in category_rows}

    target_ids = {
        idea_map[id_].brief_target_id
        for id_ in snapshot.idea_ids
        if idea_map[id_].brief_target_id is not None
    }
    target_rows = (
        db.query(SocialBriefTarget)
        .filter(SocialBriefTarget.id.in_(target_ids))
        .all()
    )
    target_map: dict[int, SocialBriefTarget] = {t.id: t for t in target_rows}

    brief_kw_rows = (
        db.query(SocialBriefKeyword)
        .filter(SocialBriefKeyword.brief_id == brief.id)
        .all()
    )
    brief_kw_map: dict[int, SocialBriefKeyword] = {
        bk.keyword_id: bk for bk in brief_kw_rows if bk.keyword_id is not None
    }

    existing_content_rows = (
        db.query(SocialContent)
        .filter(
            SocialContent.brief_id == brief.id,
            SocialContent.idea_id.in_(snapshot.idea_ids),
        )
        .all()
    )
    seen_content_ideas: set[int] = set()
    for ec in existing_content_rows:
        if ec.idea_id in seen_content_ideas:
            raise SocialContentWorkerInputError(
                "SocialContent tablosunda mükerrer idea_id tespit edildi.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=ec.idea_id,
                brief_id=brief.id,
            )
        seen_content_ideas.add(ec.idea_id)

    existing_content_map: dict[int, SocialContent] = {
        ec.idea_id: ec for ec in existing_content_rows
    }

    work_items: list[SocialContentWorkItem] = []
    already_present_idea_ids: list[int] = []

    # Snapshot sırasını kesin olarak koruyarak işle
    for idea_id in snapshot.idea_ids:
        idea = idea_map[idea_id]

        if idea.brief_id != brief.id:
            raise SocialContentWorkerInputError(
                "Fikir belirtilen brief'e ait değil.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        if getattr(idea, "scoring_run_id", None) is not None and idea.scoring_run_id != scoring_run.id:
            raise SocialContentWorkerInputError(
                "Fikir scoring_run uyuşmazlığı içeriyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        if idea.is_stale is not False:
            raise SocialContentWorkerInputError(
                "Fikir stale durumda.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        # Fikir başlığı ve açıklaması kontrolleri
        if not isinstance(idea.idea_title, str) or len(idea.idea_title) == 0 or idea.idea_title != idea.idea_title.strip():
            raise SocialContentWorkerInputError(
                "Fikir başlığı geçersiz veya boşluk içeriyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if len(idea.idea_title) > 200:
            raise SocialContentWorkerInputError(
                "Fikir başlığı 200 karakter sınırını aşıyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        if (
            not isinstance(idea.idea_description, str)
            or len(idea.idea_description) == 0
            or idea.idea_description != idea.idea_description.strip()
        ):
            raise SocialContentWorkerInputError(
                "Fikir açıklaması geçersiz veya boşluk içeriyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if len(idea.idea_description) > 2000:
            raise SocialContentWorkerInputError(
                "Fikir açıklaması 2000 karakter sınırını aşıyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        # Kategori doğrulaması
        cat = category_map.get(idea.category_id)
        if cat is None:
            raise SocialContentWorkerInputError(
                "Fikrin kategorisi bulunamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if cat.brief_id != brief.id or cat.scoring_run_id != scoring_run.id:
            raise SocialContentWorkerInputError(
                "Kategori brief veya scoring_run ile uyuşmuyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if cat.is_stale is not False:
            raise SocialContentWorkerInputError(
                "Kategori stale durumda.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        # Hedef (target) doğrulaması
        if idea.brief_target_id is None:
            raise SocialContentWorkerInputError(
                "Fikrin brief hedefi (brief_target_id) boş olamaz.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        target = target_map.get(idea.brief_target_id)
        if target is None:
            raise SocialContentWorkerInputError(
                "Fikrin brief hedefi bulunamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if target.brief_id != brief.id:
            raise SocialContentWorkerInputError(
                "Brief hedefi farklı brief'e ait.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if idea.target_platform != target.platform:
            raise SocialContentWorkerInputError(
                "Fikir hedef platformu brief hedefiyle uyuşmuyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if idea.content_format != target.content_format:
            raise SocialContentWorkerInputError(
                "Fikir içerik formatı brief hedefiyle uyuşmuyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        fmt_def = get_platform_format(target.platform, target.content_format)
        if fmt_def is None:
            raise SocialContentWorkerInputError(
                "Hedef format kanonik matriste bulunamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        if fmt_def.requires_duration:
            if (
                not target.duration_preset_id
                or target.duration_min_sec is None
                or target.duration_max_sec is None
            ):
                raise SocialContentWorkerInputError(
                    "Video hedefi için süre preset ve sınırları zorunludur.",
                    error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                    idea_id=idea.id,
                    brief_id=brief.id,
                )
            preset_def = get_duration_preset(target.duration_preset_id)
            if preset_def is None or preset_def not in fmt_def.duration_presets:
                raise SocialContentWorkerInputError(
                    "Hedef süre preset'i kanonik matrisle uyuşmuyor.",
                    error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                    idea_id=idea.id,
                    brief_id=brief.id,
                )
            if (
                target.duration_min_sec != preset_def.min_sec
                or target.duration_max_sec != preset_def.max_sec
            ):
                raise SocialContentWorkerInputError(
                    "Hedef süre sınırları kanonik preset değerleriyle uyuşmuyor.",
                    error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                    idea_id=idea.id,
                    brief_id=brief.id,
                )
        else:
            if (
                target.duration_preset_id is not None
                or target.duration_min_sec is not None
                or target.duration_max_sec is not None
            ):
                raise SocialContentWorkerInputError(
                    "Video dışı format için süre alanları NULL olmalıdır.",
                    error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                    idea_id=idea.id,
                    brief_id=brief.id,
                )

        # Anahtar kelime doğrulaması
        if idea.keyword_id is None:
            raise SocialContentWorkerInputError(
                "Fikir anahtar kelimesi (keyword_id) boş olamaz.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        brief_kw = brief_kw_map.get(idea.keyword_id)
        if brief_kw is None:
            raise SocialContentWorkerInputError(
                "Fikir anahtar kelimesi brief kelimeleri arasında bulunamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        kw_text = brief_kw.keyword_snapshot
        if not isinstance(kw_text, str) or len(kw_text) == 0 or kw_text != kw_text.strip():
            raise SocialContentWorkerInputError(
                "Anahtar kelime snapshot metni geçersiz veya boşluk içeriyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )
        if len(kw_text) > 200:
            raise SocialContentWorkerInputError(
                "Anahtar kelime snapshot metni 200 karakter sınırını aşıyor.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        # Önceden kaydedilmiş SocialContent kontrolü (Idempotent Skip & Replay Validation)
        existing_content = existing_content_map.get(idea.id)
        if already_completed and existing_content is None:
            raise SocialContentWorkerInputError(
                "Tamamlanmış attempt replay sırasında içerik satırı bulunamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            )

        if existing_content is not None:
            if existing_content.is_stale is not False:
                raise SocialContentWorkerInputError(
                    "Mevcut sosyal içerik stale durumda.",
                    error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                    idea_id=idea.id,
                    brief_id=brief.id,
                )
            try:
                validate_and_build_persisted_social_content(
                    existing_content,
                    target=target,
                    idea=idea,
                    brief=brief,
                )
            except SocialContentPersistenceError as exc:
                raise SocialContentWorkerInputError(
                    exc.message,
                    error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                    idea_id=idea.id,
                    brief_id=brief.id,
                ) from exc
            already_present_idea_ids.append(idea.id)
            continue

        # Eksik içerik için WorkItem oluşturma
        target_spec = ContentTargetSpec(
            target_id=target.id,
            platform=target.platform,
            content_format=target.content_format,
            duration_preset_id=target.duration_preset_id,
            duration_min_sec=target.duration_min_sec,
            duration_max_sec=target.duration_max_sec,
        )

        keyword_input = SocialContentKeywordInput(
            keyword_id=idea.keyword_id,
            keyword=kw_text,
        )

        prompt_input = SocialContentPromptInput(
            attempt_id=attempt.id,
            idea_id=idea.id,
            target_spec=target_spec,
            idea_title=idea.idea_title,
            idea_description=idea.idea_description,
            primary_keyword=keyword_input,
            brand_name=brief.brand_name_snapshot,
            brand_tone=None,
            brand_context=brief.brand_context_snapshot,
            product_facts=snapshot.product_facts,
            trusted_brand_usp=snapshot.trusted_brand_usp,
        )

        try:
            _validate_prompt_input(prompt_input)
        except Exception as exc:
            raise SocialContentWorkerInputError(
                "Prompt girdisi doğrulanamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            ) from exc

        grounding_context = SocialContentGroundingContext(
            primary_keyword=kw_text,
            product_facts=snapshot.product_facts,
            trusted_brand_usp=snapshot.trusted_brand_usp,
        )

        try:
            validate_grounding_prompt_parity(grounding_context, prompt_input)
        except Exception as exc:
            raise SocialContentWorkerInputError(
                "Grounding context ile prompt input paritesi sağlanamadı.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                idea_id=idea.id,
                brief_id=brief.id,
            ) from exc

        work_items.append(
            SocialContentWorkItem(
                idea_id=idea.id,
                prompt_input=prompt_input,
                grounding_context=grounding_context,
            )
        )

    if already_completed:
        if tuple(already_present_idea_ids) != snapshot.idea_ids:
            raise SocialContentWorkerInputError(
                "Tamamlanmış attempt replay için tüm içerikler eksiksiz mevcut olmalıdır.",
                error_code=CONTENT_WORKER_INPUT_INCONSISTENT,
                brief_id=brief.id,
            )
        return SocialContentWorkerPreparation(
            attempt_id=attempt.id,
            task_id=task_id,
            brief_id=brief.id,
            scoring_run_id=scoring_run.id,
            requested_idea_ids=snapshot.idea_ids,
            already_present_idea_ids=tuple(already_present_idea_ids),
            already_completed=True,
            work_items=(),
        )

    return SocialContentWorkerPreparation(
        attempt_id=attempt.id,
        task_id=task_id,
        brief_id=brief.id,
        scoring_run_id=scoring_run.id,
        requested_idea_ids=snapshot.idea_ids,
        already_present_idea_ids=tuple(already_present_idea_ids),
        already_completed=False,
        work_items=tuple(work_items),
    )
