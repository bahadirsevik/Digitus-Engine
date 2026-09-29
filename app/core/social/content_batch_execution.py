# -*- coding: utf-8 -*-
"""DB'siz Çoklu Work Item Batch Yürütücüsü ve Sonuç Agregasyonu (F1-G.5.4).

Bu modül SocialContentWorkerPreparation nesnesinden beslenerek work item kümesini
deterministik ve fail-closed biçimde çalıştırır, sonuçları ve uyarıları agrege eder:
1. Girdi ve hazırlık doğrulaması (0 AI çağrısı).
2. already_completed=True durumunda doğrudan 0 AI çağrısıyla tamamlanmış replay döner.
3. already_completed=False durumunda work_items kümesini deterministik sırayla yürütür.
4. Her item için execute_social_content_work_item çağrılır.
5. Accepted çıktılar accepted_results ve accepted_idea_ids içine eklenir.
6. Rejected ve hata alan çıktılar güvenli warning nesnelerine dönüştürülür.
7. already_present_idea_ids başarılı kabul edilir, ancak sahte result üretilmez.
8. Status ("completed" | "partial" | "failed") saf biçimde hesaplanır.
9. DB, ORM, Session, Heartbeat veya Persistence bağımlılığı KESİNLİKLE İÇERMEZ
   (yalnız tip tespiti için `celery.exceptions.SoftTimeLimitExceeded` import
   edilir; broker/queue bağımlılığı YOKTUR).
10. Deterministik zaman bütçesi (`CONTENT_BATCH_TIME_BUDGET_SECONDS`, enjekte
    edilebilir `clock`): Celery soft_time_limit'e ulaşmadan ÖNCE yeni item
    başlatmayı durdurur; kalan item'lar 'time_budget_exhausted' ile
    unresolved bırakılır, batch normal partial/failed yolundan döner ve
    zaten kabul edilmiş sonuçlar kaybedilmez.
11. `SoftTimeLimitExceeded` hiçbir zaman sıradan bir item hatasına
    dönüştürülmez; savunma hattı olarak yakalanıp 'time_budget_exhausted'
    ile zarif biçimde sonlandırılır (fırlatılmaz), ki batch normal
    partial yolundan dönebilsin ve orkestratörün Transaction C'si zaten
    kabul edilmiş içerikleri persist edebilsin.
"""
from __future__ import annotations

import time as _time_module
from dataclasses import dataclass
from typing import Any, Callable

from celery.exceptions import SoftTimeLimitExceeded

from app.core.social.content_item_execution import (
    SocialContentItemExecutionError,
    SocialContentItemExecutionResult,
    execute_social_content_work_item,
)
from app.core.social.content_worker_input import (
    SocialContentWorkerPreparation,
    SocialContentWorkItem,
)

# ==================== SABİTLER VE ALLOWLIST ====================

BATCH_WARNING_REASON_ALLOWLIST: frozenset[str] = frozenset({
    "content_rejected",
    "content_input_invalid",
    "content_provider_error",
    "content_output_invalid",
    "content_quality_invalid",
    "content_repair_input_invalid",
    "content_repair_provider_error",
    "content_repair_output_invalid",
    "content_execution_inconsistent",
    "time_budget_exhausted",
})

# Celery soft_time_limit=1140s / time_limit=1200s (bkz. app/tasks/generation_tasks.py
# social_brief_contents_task). Bu bütçe soft limit'in HAYLI altında tutulur (~240s
# marj) ki batch, gerçek SoftTimeLimitExceeded sinyaline ULAŞMADAN yeni item
# başlatmayı durdursun ve kabul edilmiş sonuçlar normal 'partial' yoluyla
# Transaction C'de persist edilebilsin (bkz. content_orchestration.py).
# Test edilebilirlik için `clock` parametresiyle enjekte edilebilir.
CONTENT_BATCH_TIME_BUDGET_SECONDS: float = 900.0


# ==================== DOMAIN EXCEPTIONS ====================


class SocialContentBatchExecutionError(ValueError):
    """Sosyal içerik batch yürütme domain hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CONTENT_BATCH_EXECUTION_FAILED",
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


# ==================== DTO'LAR (IMMUTABLE) ====================


@dataclass(frozen=True)
class SocialContentBatchWarning:
    """Batch sırasında çözülemeyen veya reddedilen tekil fikir uyarısı (immutable)."""

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
            raise SocialContentBatchExecutionError(
                "Geçersiz idea_id.",
                field="idea_id",
            )
        if self.reason_code not in BATCH_WARNING_REASON_ALLOWLIST:
            raise SocialContentBatchExecutionError(
                f"Geçersiz reason_code: {self.reason_code}",
                field="reason_code",
            )
        if type(self.claims) is not tuple:
            raise SocialContentBatchExecutionError(
                "claims tuple olmalıdır.",
                field="claims",
            )
        for c in self.claims:
            if type(c) is not str:
                raise SocialContentBatchExecutionError(
                    "claims elemanları string olmalıdır.",
                    field="claims",
                )
        if self.reason_code == "content_rejected":
            # content_rejected durumunda claims boş veya iddia tuple'ı olabilir
            pass
        else:
            # content_rejected dışındaki bütün hata uyarılarında claims kesinlikle boş olmalıdır
            if len(self.claims) != 0:
                raise SocialContentBatchExecutionError(
                    "content_rejected dışındaki uyarılar için claims boş tuple olmalıdır.",
                    field="claims",
                )
        if (
            isinstance(self.ai_calls_used, bool)
            or type(self.ai_calls_used) is not int
            or self.ai_calls_used not in (0, 1, 2)
        ):
            raise SocialContentBatchExecutionError(
                "ai_calls_used 0, 1 veya 2 olmalıdır.",
                field="ai_calls_used",
            )


@dataclass(frozen=True)
class SocialContentBatchExecutionResult:
    """Sosyal içerik batch yürütme nihai agregasyon sonucu (immutable)."""

    attempt_id: int
    brief_id: int
    scoring_run_id: int
    status: str  # Literal["completed", "partial", "failed"]
    requested_idea_ids: tuple[int, ...]
    already_present_idea_ids: tuple[int, ...]
    accepted_results: tuple[SocialContentItemExecutionResult, ...]
    accepted_idea_ids: tuple[int, ...]
    unresolved_idea_ids: tuple[int, ...]
    warnings: tuple[SocialContentBatchWarning, ...]
    ai_calls_used: int
    replayed: bool

    def __post_init__(self) -> None:
        # 1. attempt_id, brief_id, scoring_run_id
        for field_name in ("attempt_id", "brief_id", "scoring_run_id"):
            val = getattr(self, field_name)
            if isinstance(val, bool) or type(val) is not int or val <= 0:
                raise SocialContentBatchExecutionError(
                    f"Geçersiz {field_name}.",
                    field=field_name,
                )

        # 2. status & replayed
        if self.status not in ("completed", "partial", "failed"):
            raise SocialContentBatchExecutionError(
                "status 'completed', 'partial' veya 'failed' olmalıdır.",
                field="status",
            )
        if type(self.replayed) is not bool:
            raise SocialContentBatchExecutionError(
                "replayed exact bool olmalıdır.",
                field="replayed",
            )

        # 3. requested_idea_ids
        if type(self.requested_idea_ids) is not tuple:
            raise SocialContentBatchExecutionError(
                "requested_idea_ids tuple olmalıdır.",
                field="requested_idea_ids",
            )
        if not (1 <= len(self.requested_idea_ids) <= 30):
            raise SocialContentBatchExecutionError(
                "requested_idea_ids 1 ile 30 arasında eleman içermelidir.",
                field="requested_idea_ids",
            )
        if len(set(self.requested_idea_ids)) != len(self.requested_idea_ids):
            raise SocialContentBatchExecutionError(
                "requested_idea_ids benzersiz olmalıdır.",
                field="requested_idea_ids",
            )
        for i in self.requested_idea_ids:
            if isinstance(i, bool) or type(i) is not int or i <= 0:
                raise SocialContentBatchExecutionError(
                    "requested_idea_ids pozitif tamsayılar içermelidir.",
                    field="requested_idea_ids",
                )

        req_set = set(self.requested_idea_ids)

        # 4. already_present_idea_ids
        if type(self.already_present_idea_ids) is not tuple:
            raise SocialContentBatchExecutionError(
                "already_present_idea_ids tuple olmalıdır.",
                field="already_present_idea_ids",
            )
        if len(set(self.already_present_idea_ids)) != len(self.already_present_idea_ids):
            raise SocialContentBatchExecutionError(
                "already_present_idea_ids benzersiz olmalıdır.",
                field="already_present_idea_ids",
            )
        pres_set = set(self.already_present_idea_ids)
        if not pres_set.issubset(req_set):
            raise SocialContentBatchExecutionError(
                "already_present_idea_ids requested kümesinin alt kümesi olmalıdır.",
                field="already_present_idea_ids",
            )
        expected_pres_order = tuple(i for i in self.requested_idea_ids if i in pres_set)
        if self.already_present_idea_ids != expected_pres_order:
            raise SocialContentBatchExecutionError(
                "already_present_idea_ids requested sırasını korumalıdır.",
                field="already_present_idea_ids",
            )

        # 5. accepted_idea_ids
        if type(self.accepted_idea_ids) is not tuple:
            raise SocialContentBatchExecutionError(
                "accepted_idea_ids tuple olmalıdır.",
                field="accepted_idea_ids",
            )
        if len(set(self.accepted_idea_ids)) != len(self.accepted_idea_ids):
            raise SocialContentBatchExecutionError(
                "accepted_idea_ids benzersiz olmalıdır.",
                field="accepted_idea_ids",
            )
        acc_set = set(self.accepted_idea_ids)
        if not acc_set.issubset(req_set):
            raise SocialContentBatchExecutionError(
                "accepted_idea_ids requested kümesinin alt kümesi olmalıdır.",
                field="accepted_idea_ids",
            )
        expected_acc_order = tuple(i for i in self.requested_idea_ids if i in acc_set)
        if self.accepted_idea_ids != expected_acc_order:
            raise SocialContentBatchExecutionError(
                "accepted_idea_ids requested sırasını korumalıdır.",
                field="accepted_idea_ids",
            )

        # already_present ve accepted kesişmemeli
        if pres_set & acc_set:
            raise SocialContentBatchExecutionError(
                "already_present ve accepted ID'leri kesişemez.",
                field="accepted_idea_ids",
            )

        # 6. accepted_results
        if type(self.accepted_results) is not tuple:
            raise SocialContentBatchExecutionError(
                "accepted_results tuple olmalıdır.",
                field="accepted_results",
            )
        if len(self.accepted_results) != len(self.accepted_idea_ids):
            raise SocialContentBatchExecutionError(
                "accepted_results ve accepted_idea_ids eleman sayıları eşit olmalıdır.",
                field="accepted_results",
            )
        for idx, r in enumerate(self.accepted_results):
            if type(r) is not SocialContentItemExecutionResult:
                raise SocialContentBatchExecutionError(
                    "accepted_results elemanları exact SocialContentItemExecutionResult olmalıdır.",
                    field="accepted_results",
                )
            if r.status != "accepted":
                raise SocialContentBatchExecutionError(
                    "accepted_results içindeki tüm elemanların statüsü 'accepted' olmalıdır.",
                    field="accepted_results",
                )
            if r.attempt_id != self.attempt_id:
                raise SocialContentBatchExecutionError(
                    "accepted_results attempt_id batch attempt_id ile eşleşmelidir.",
                    field="accepted_results",
                )
            if r.idea_id != self.accepted_idea_ids[idx]:
                raise SocialContentBatchExecutionError(
                    "accepted_results idea ID'leri accepted_idea_ids ile birebir aynı sırada olmalıdır.",
                    field="accepted_results",
                )

        # 7. successful_idea_ids & unresolved_idea_ids
        successful_set = pres_set | acc_set
        expected_unresolved = tuple(i for i in self.requested_idea_ids if i not in successful_set)
        if type(self.unresolved_idea_ids) is not tuple or self.unresolved_idea_ids != expected_unresolved:
            raise SocialContentBatchExecutionError(
                "unresolved_idea_ids beklenen kanonik eksik ID'lerle uyuşmuyor.",
                field="unresolved_idea_ids",
            )

        # 8. warnings
        if type(self.warnings) is not tuple:
            raise SocialContentBatchExecutionError(
                "warnings tuple olmalıdır.",
                field="warnings",
            )
        for w in self.warnings:
            if type(w) is not SocialContentBatchWarning:
                raise SocialContentBatchExecutionError(
                    "warnings elemanları exact SocialContentBatchWarning olmalıdır.",
                    field="warnings",
                )
        warning_ids = tuple(w.idea_id for w in self.warnings)
        if warning_ids != self.unresolved_idea_ids:
            raise SocialContentBatchExecutionError(
                "warnings idea ID'leri unresolved_idea_ids ile birebir aynı sırada olmalıdır.",
                field="warnings",
            )

        # 9. ai_calls_used
        if isinstance(self.ai_calls_used, bool) or type(self.ai_calls_used) is not int or self.ai_calls_used < 0:
            raise SocialContentBatchExecutionError(
                "ai_calls_used negatif olmayan tamsayı olmalıdır.",
                field="ai_calls_used",
            )
        max_possible_calls = 2 * (len(self.requested_idea_ids) - len(self.already_present_idea_ids))
        if self.ai_calls_used > max_possible_calls:
            raise SocialContentBatchExecutionError(
                "ai_calls_used teorik maksimum çağrı sınırını aşamaz.",
                field="ai_calls_used",
            )
        expected_ai_calls = sum(r.ai_calls_used for r in self.accepted_results) + sum(w.ai_calls_used for w in self.warnings)
        if self.ai_calls_used != expected_ai_calls:
            raise SocialContentBatchExecutionError(
                "ai_calls_used accepted_results ve warnings çağrı toplamı ile eşleşmelidir.",
                field="ai_calls_used",
            )

        # 10. Status ve Replay ilişkileri
        if self.replayed:
            if self.status != "completed":
                raise SocialContentBatchExecutionError(
                    "replayed=True durumunda status 'completed' olmalıdır.",
                    field="status",
                )
            if self.ai_calls_used != 0:
                raise SocialContentBatchExecutionError(
                    "replayed=True durumunda ai_calls_used 0 olmalıdır.",
                    field="ai_calls_used",
                )
            if self.accepted_results != () or self.warnings != ():
                raise SocialContentBatchExecutionError(
                    "replayed=True durumunda accepted_results ve warnings boş olmalıdır.",
                    field="accepted_results",
                )
            if self.unresolved_idea_ids != ():
                raise SocialContentBatchExecutionError(
                    "replayed=True durumunda unresolved_idea_ids boş olmalıdır.",
                    field="unresolved_idea_ids",
                )

        if self.status == "completed":
            if len(self.unresolved_idea_ids) != 0:
                raise SocialContentBatchExecutionError(
                    "completed durumunda unresolved_idea_ids boş olmalıdır.",
                    field="unresolved_idea_ids",
                )
        elif self.status == "partial":
            if len(successful_set) == 0 or len(self.unresolved_idea_ids) == 0:
                raise SocialContentBatchExecutionError(
                    "partial durumunda hem başarılı hem unresolved eleman bulunmalıdır.",
                    field="status",
                )
        elif self.status == "failed":
            if len(successful_set) != 0:
                raise SocialContentBatchExecutionError(
                    "failed durumunda hiçbir başarılı fikir bulunmamalıdır.",
                    field="status",
                )
            if len(self.unresolved_idea_ids) == 0:
                raise SocialContentBatchExecutionError(
                    "failed durumunda unresolved_idea_ids boş olamaz.",
                    field="unresolved_idea_ids",
                )


# ==================== TEK WORK ITEM YARDIMCISI ====================


def _process_single_work_item(
    *,
    ai_service: Any,
    item: SocialContentWorkItem,
    preparation: SocialContentWorkerPreparation,
    accepted_results_list: list[SocialContentItemExecutionResult],
    accepted_ids_set: set[int],
    warnings_map: dict[int, SocialContentBatchWarning],
) -> None:
    """Tek bir work item'ı çalıştırır ve sonucunu accepted_results_list veya warnings_map'e yazar."""
    try:
        result = execute_social_content_work_item(
            ai_service=ai_service,
            work_item=item,
        )

        # 1. Exact-type doğrulaması
        if type(result) is not SocialContentItemExecutionResult:
            calls = 2  # exact DTO değilse bütçe güvenliği için konservatif 2 çağrı
            warnings_map[item.idea_id] = SocialContentBatchWarning(
                idea_id=item.idea_id,
                reason_code="content_execution_inconsistent",
                claims=(),
                ai_calls_used=calls,
            )
            return

        # 2. Child result kimlik ve parite kontrolleri (hem accepted hem rejected için zorunlu)
        is_valid_calls = (
            not isinstance(result.ai_calls_used, bool)
            and type(result.ai_calls_used) is int
            and result.ai_calls_used in (1, 2)
        )
        parity_ok = (
            result.attempt_id == preparation.attempt_id
            and result.attempt_id == item.prompt_input.attempt_id
            and result.idea_id == item.idea_id
            and result.idea_id == item.prompt_input.idea_id
            and is_valid_calls
        )

        if not parity_ok:
            # Parite ihlali: exact DTO ise doğrulanmış çağrı sayısını, değilse konservatif 2 kullan
            calls = result.ai_calls_used if is_valid_calls else 2
            warnings_map[item.idea_id] = SocialContentBatchWarning(
                idea_id=item.idea_id,
                reason_code="content_execution_inconsistent",
                claims=(),
                ai_calls_used=calls,
            )
            return

        # Parite tam sağlandı
        if result.status == "accepted":
            accepted_results_list.append(result)
            accepted_ids_set.add(item.idea_id)
        elif result.status == "rejected":
            warnings_map[item.idea_id] = SocialContentBatchWarning(
                idea_id=item.idea_id,
                reason_code="content_rejected",
                claims=tuple(result.quality_decision.claims) if result.quality_decision else (),
                ai_calls_used=result.ai_calls_used,
            )
        else:
            warnings_map[item.idea_id] = SocialContentBatchWarning(
                idea_id=item.idea_id,
                reason_code="content_execution_inconsistent",
                claims=(),
                ai_calls_used=result.ai_calls_used,
            )
    except SocialContentItemExecutionError as exc:
        calls = (
            exc.ai_calls_used
            if (
                not isinstance(exc.ai_calls_used, bool)
                and type(exc.ai_calls_used) is int
                and exc.ai_calls_used in (0, 1, 2)
            )
            else 2
        )
        reason = (
            exc.reason_code
            if exc.reason_code in BATCH_WARNING_REASON_ALLOWLIST
            else "content_execution_inconsistent"
        )
        warnings_map[item.idea_id] = SocialContentBatchWarning(
            idea_id=item.idea_id,
            reason_code=reason,
            claims=(),
            ai_calls_used=calls,
        )
    except SoftTimeLimitExceeded:
        # Celery yumuşak zaman sınırı sıradan bir item hatası GİBİ
        # yutulamaz; batch executor'a (çağırana) olduğu gibi fırlatılır.
        # Executor bunu 'time_budget_exhausted' olarak zarif biçimde
        # sonlandırır (CLAUDE.md §11 + plan_social_brief_akisi.md).
        raise
    except Exception:
        calls = 2  # Beklenmeyen exception durumunda konservatif 2 çağrı
        warnings_map[item.idea_id] = SocialContentBatchWarning(
            idea_id=item.idea_id,
            reason_code="content_execution_inconsistent",
            claims=(),
            ai_calls_used=calls,
        )


# ==================== BATCH EXECUTOR ====================


def execute_social_content_batch(
    *,
    ai_service: Any,
    preparation: SocialContentWorkerPreparation,
    on_item_finished: Callable[[int], None] | None = None,
    clock: Callable[[], float] | None = None,
    time_budget_seconds: float | None = None,
) -> SocialContentBatchExecutionResult:
    """Doğrulanmış SocialContentWorkerPreparation girdisini deterministik sıra ile yürütür.

    Args:
        ai_service: Yapay zeka servis nesnesi.
        preparation: Worker hazırlık aşaması çıktısı (SocialContentWorkerPreparation).
        on_item_finished: Her work item sonuçlandıktan sonra kanonik idea_id ile çağrılan opsiyonel callback.
        clock: Test edilebilirlik için enjekte edilebilir monotonik zaman kaynağı
            (varsayılan `time.monotonic`). Her çağrı saniye cinsinden artan bir
            float döndürmelidir.
        time_budget_seconds: Bu batch çalıştırması için izin verilen maksimum
            duvar-saati süresi (varsayılan `CONTENT_BATCH_TIME_BUDGET_SECONDS`).
            Bütçe aşıldığında yeni item BAŞLATILMAZ; kalan tüm item'lar
            'time_budget_exhausted' uyarısıyla unresolved bırakılır ve batch
            normal 'partial' (veya tümü etkilenmişse 'failed') yolundan döner.

    Returns:
        SocialContentBatchExecutionResult: Tam, kısmi veya başarısız batch sonucu.

    Raises:
        SocialContentBatchExecutionError: Önkoşul veya sözleşme ihlali durumunda (0 AI çağrısı).
    """
    # ----------------------------------------------------
    # Faz 0: Preparation Preflight Doğrulaması (0 AI Çağrısı)
    # ----------------------------------------------------
    if on_item_finished is not None and not callable(on_item_finished):
        raise SocialContentBatchExecutionError(
            "on_item_finished callable veya None olmalıdır.",
            field="on_item_finished",
        )

    if clock is not None and not callable(clock):
        raise SocialContentBatchExecutionError(
            "clock callable veya None olmalıdır.",
            field="clock",
        )
    clock_fn: Callable[[], float] = clock if clock is not None else _time_module.monotonic

    if time_budget_seconds is None:
        budget_seconds = CONTENT_BATCH_TIME_BUDGET_SECONDS
    elif (
        isinstance(time_budget_seconds, bool)
        or not isinstance(time_budget_seconds, (int, float))
        or time_budget_seconds <= 0
    ):
        raise SocialContentBatchExecutionError(
            "time_budget_seconds pozitif bir sayı olmalıdır.",
            field="time_budget_seconds",
        )
    else:
        budget_seconds = float(time_budget_seconds)

    if ai_service is None:
        raise SocialContentBatchExecutionError(
            "ai_service boş olamaz.",
            field="ai_service",
        )

    if type(preparation) is not SocialContentWorkerPreparation:
        raise SocialContentBatchExecutionError(
            "preparation exact SocialContentWorkerPreparation olmalıdır.",
            field="preparation",
        )

    for field_name in ("attempt_id", "brief_id", "scoring_run_id"):
        val = getattr(preparation, field_name)
        if isinstance(val, bool) or type(val) is not int or val <= 0:
            raise SocialContentBatchExecutionError(
                f"Geçersiz {field_name}.",
                field=field_name,
            )

    if type(preparation.already_completed) is not bool:
        raise SocialContentBatchExecutionError(
            "already_completed exact bool olmalıdır.",
            field="already_completed",
        )

    # requested_idea_ids doğrulaması
    req = preparation.requested_idea_ids
    if type(req) is not tuple:
        raise SocialContentBatchExecutionError(
            "requested_idea_ids tuple olmalıdır.",
            field="requested_idea_ids",
        )
    if not (1 <= len(req) <= 30):
        raise SocialContentBatchExecutionError(
            "requested_idea_ids 1 ile 30 arasında eleman içermelidir.",
            field="requested_idea_ids",
        )
    if len(set(req)) != len(req):
        raise SocialContentBatchExecutionError(
            "requested_idea_ids benzersiz olmalıdır.",
            field="requested_idea_ids",
        )
    for i in req:
        if isinstance(i, bool) or type(i) is not int or i <= 0:
            raise SocialContentBatchExecutionError(
                "requested_idea_ids pozitif tamsayılar içermelidir.",
                field="requested_idea_ids",
            )

    req_set = set(req)

    # already_present_idea_ids doğrulaması
    pres = preparation.already_present_idea_ids
    if type(pres) is not tuple:
        raise SocialContentBatchExecutionError(
            "already_present_idea_ids tuple olmalıdır.",
            field="already_present_idea_ids",
        )
    if len(set(pres)) != len(pres):
        raise SocialContentBatchExecutionError(
            "already_present_idea_ids benzersiz olmalıdır.",
            field="already_present_idea_ids",
        )
    pres_set = set(pres)
    if not pres_set.issubset(req_set):
        raise SocialContentBatchExecutionError(
            "already_present_idea_ids requested kümesinin alt kümesi olmalıdır.",
            field="already_present_idea_ids",
        )
    expected_pres_order = tuple(i for i in req if i in pres_set)
    if pres != expected_pres_order:
        raise SocialContentBatchExecutionError(
            "already_present_idea_ids requested sırasını korumalıdır.",
            field="already_present_idea_ids",
        )

    # work_items doğrulaması
    items = preparation.work_items
    if type(items) is not tuple:
        raise SocialContentBatchExecutionError(
            "work_items tuple olmalıdır.",
            field="work_items",
        )
    for it in items:
        if type(it) is not SocialContentWorkItem:
            raise SocialContentBatchExecutionError(
                "work_items elemanları exact SocialContentWorkItem olmalıdır.",
                field="work_items",
            )

    item_ids = tuple(it.idea_id for it in items)
    if len(set(item_ids)) != len(item_ids):
        raise SocialContentBatchExecutionError(
            "work_items idea_id değerleri benzersiz olmalıdır.",
            field="work_items",
        )
    item_set = set(item_ids)
    if not item_set.issubset(req_set):
        raise SocialContentBatchExecutionError(
            "work_items idea ID'leri requested kümesinde bulunmalıdır.",
            field="work_items",
        )
    expected_item_order = tuple(i for i in req if i in item_set)
    if item_ids != expected_item_order:
        raise SocialContentBatchExecutionError(
            "work_items requested sırasını korumalıdır.",
            field="work_items",
        )

    # already_present ve work_items kesişmemeli
    if pres_set & item_set:
        raise SocialContentBatchExecutionError(
            "already_present_idea_ids ile work_items idea ID'leri kesişemez.",
            field="work_items",
        )

    # Replay veya partition kontrolü
    if preparation.already_completed:
        if len(items) != 0:
            raise SocialContentBatchExecutionError(
                "already_completed=True durumunda work_items boş olmalıdır.",
                field="work_items",
            )
        if pres != req:
            raise SocialContentBatchExecutionError(
                "already_completed=True durumunda already_present_idea_ids requested_idea_ids ile birebir aynı olmalıdır.",
                field="already_present_idea_ids",
            )
        return SocialContentBatchExecutionResult(
            attempt_id=preparation.attempt_id,
            brief_id=preparation.brief_id,
            scoring_run_id=preparation.scoring_run_id,
            status="completed",
            requested_idea_ids=preparation.requested_idea_ids,
            already_present_idea_ids=preparation.already_present_idea_ids,
            accepted_results=(),
            accepted_idea_ids=(),
            unresolved_idea_ids=(),
            warnings=(),
            ai_calls_used=0,
            replayed=True,
        )

    # already_completed is False: Tam partition kontrolü
    if (pres_set | item_set) != req_set:
        raise SocialContentBatchExecutionError(
            "already_present ve work_items requested_idea_ids kümesini eksiksiz kapsamalıdır.",
            field="work_items",
        )

    # ----------------------------------------------------
    # Faz 1: Deterministik Sırayla Work Item Yürütme
    # ----------------------------------------------------
    accepted_results_list: list[SocialContentItemExecutionResult] = []
    accepted_ids_set: set[int] = set()
    warnings_map: dict[int, SocialContentBatchWarning] = {}

    batch_start = clock_fn()

    for item_idx, item in enumerate(items):
        # Deterministik zaman bütçesi: soft_time_limit'e ulaşmadan ÖNCE yeni
        # item başlatmayı durdur. Kalan tüm item'lar (bu dahil) hiç
        # çalıştırılmadan 'time_budget_exhausted' ile unresolved bırakılır;
        # zaten kabul edilmiş sonuçlar bu batch'in normal partial yolundan
        # persist edilebilir.
        if (clock_fn() - batch_start) >= budget_seconds:
            for remaining_item in items[item_idx:]:
                warnings_map[remaining_item.idea_id] = SocialContentBatchWarning(
                    idea_id=remaining_item.idea_id,
                    reason_code="time_budget_exhausted",
                    claims=(),
                    ai_calls_used=0,
                )
            break

        try:
            _process_single_work_item(
                ai_service=ai_service,
                item=item,
                preparation=preparation,
                accepted_results_list=accepted_results_list,
                accepted_ids_set=accepted_ids_set,
                warnings_map=warnings_map,
            )
        except SoftTimeLimitExceeded:
            # Savunma hattı: proaktif bütçe kontrolü zamanında yakalayamadıysa
            # (örn. tek bir item'ın AI çağrıları bütçe kontrolünden sonra
            # başlayıp gerçek soft limit'i aştıysa), bu item ve işlenmemiş
            # tüm kalan item'lar 'time_budget_exhausted' ile unresolved
            # bırakılır. İstisna burada YUTULUR (fırlatılmaz) ki zaten kabul
            # edilmiş sonuçlar normal partial yoluyla persist edilebilsin —
            # bu, celery'nin "temizlik için biraz süre bırakma" niyetiyle
            # (soft_time_limit < time_limit marjı) uyumludur.
            for remaining_item in items[item_idx:]:
                warnings_map[remaining_item.idea_id] = SocialContentBatchWarning(
                    idea_id=remaining_item.idea_id,
                    reason_code="time_budget_exhausted",
                    claims=(),
                    ai_calls_used=2,
                )
            break

        # Work item tamamlandıktan sonra opsiyonel progress / heartbeat callback'i çağrılır.
        # Callback exception'ı item warning'ine çevrilmez, batch dışına taşar.
        if on_item_finished is not None:
            on_item_finished(item.idea_id)

    # ----------------------------------------------------
    # Faz 2: Agregasyon ve Kanonik Sıralama
    # ----------------------------------------------------
    accepted_idea_ids = tuple(i for i in req if i in accepted_ids_set)
    accepted_results_by_id = {r.idea_id: r for r in accepted_results_list}
    accepted_results = tuple(
        accepted_results_by_id[i]
        for i in accepted_idea_ids
        if i in accepted_results_by_id
    )

    successful_ids_set = pres_set | accepted_ids_set
    unresolved_idea_ids = tuple(i for i in req if i not in successful_ids_set)
    warnings = tuple(
        warnings_map.get(
            i,
            SocialContentBatchWarning(
                idea_id=i,
                reason_code="content_execution_inconsistent",
                claims=(),
                ai_calls_used=2,
            ),
        )
        for i in unresolved_idea_ids
    )

    if len(unresolved_idea_ids) == 0:
        status = "completed"
    elif len(successful_ids_set) > 0:
        status = "partial"
    else:
        status = "failed"

    total_ai_calls = sum(r.ai_calls_used for r in accepted_results) + sum(w.ai_calls_used for w in warnings)

    return SocialContentBatchExecutionResult(
        attempt_id=preparation.attempt_id,
        brief_id=preparation.brief_id,
        scoring_run_id=preparation.scoring_run_id,
        status=status,
        requested_idea_ids=preparation.requested_idea_ids,
        already_present_idea_ids=preparation.already_present_idea_ids,
        accepted_results=accepted_results,
        accepted_idea_ids=accepted_idea_ids,
        unresolved_idea_ids=unresolved_idea_ids,
        warnings=warnings,
        ai_calls_used=total_ai_calls,
        replayed=False,
    )
