# -*- coding: utf-8 -*-
"""ideas_retry Coverage Snapshot Parser ve Doğrulayıcısı (F1-F.7.4).

Bu modül SocialGenerationAttempt (stage='ideas_retry') üzerindeki coverage
snapshot'ını preflight replay, worker input, persistence ve read katmanları
için tek ve ortak bir fail-closed saf parser olarak ayrıştırır ve doğrular.

Kurallar:
- DB sorgusu yapmaz, Session almaz.
- AI, zaman veya global state kullanmaz.
- Attempt nesnesini veya coverage dict'ini mutate etmez.
- Hata mesajlarında raw JSON, SQL veya dinamik kullanıcı metni sızdırmaz.
- Saf planner (build_social_idea_retry_plan) ile kanonik pariteyi yeniden teyit eder.

Kategori kapsaması (K4) ek alanları — geriye uyumlu, İKİ mod:
- Yeni mod: baseline.persisted_category_ids_at_start + plan.empty_category_ids +
  generated.assignments birlikte bulunur. Plan boş kategorileri de onarır; bir
  hedef birden çok atamada (farklı kategori) yer alabilir.
- Legacy mod: bu üç alanın HİÇBİRİ yoktur (alan eklenmeden önce yazılmış snapshot).
  Planner kategori bilgisi olmadan (persisted_category_ids=None) çağrılır; eski
  sözleşme (atama başına benzersiz hedef, atamalar = eksik hedefler) aynen geçerlidir.
- Karışık (yalnız bir kısmı var) snapshot fail-closed reddedilir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.social.idea_planner import SocialIdeaGenerationPlan
from app.core.social.idea_retry_planner import (
    IdeaRetryPlanError,
    SocialIdeaRetryPlan,
    build_social_idea_retry_plan,
)
from app.database.models import SocialGenerationAttempt


class SocialIdeaRetrySnapshotError(ValueError):
    """Fikir tekrar deneme (retry) plan snapshot doğrulama hatası."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.field = field

    def __repr__(self) -> str:
        return (
            f"SocialIdeaRetrySnapshotError(error_code={self.error_code!r}, "
            f"message={self.message!r}, field={self.field!r})"
        )


@dataclass(frozen=True)
class SocialIdeaRetryPlanSnapshot:
    """Doğrulanmış ve saf planner ile teyit edilmiş immutable retry snapshot DTO'su."""

    source_attempt_id: int
    canonical_target_ids: tuple[int, ...]
    persisted_target_ids_at_start: tuple[int, ...]
    missing_target_ids: tuple[int, ...]
    plan: SocialIdeaRetryPlan
    generated_total_accepted: int
    generated_target_ids: tuple[int, ...]
    # None = legacy snapshot (kategori kapsaması kayıtlı değil)
    persisted_category_ids_at_start: tuple[int, ...] | None = None
    # Kabul edilen (category_id, target_id) çiftleri, plan atama sırasıyla.
    # Legacy'de generated_target_ids + atama haritasından türetilir.
    generated_pairs: tuple[tuple[int, int], ...] = ()

    @property
    def is_legacy(self) -> bool:
        return self.persisted_category_ids_at_start is None

    @property
    def empty_category_ids(self) -> tuple[int, ...]:
        return self.plan.empty_category_ids

    @property
    def requested_target_ids(self) -> tuple[int, ...]:
        return self.plan.requested_target_ids

    @property
    def unfilled_target_ids(self) -> tuple[int, ...]:
        """Eksik hedeflerden bu denemede fikir almayanlar (kanonik sıra)."""
        got = {t for _, t in self.generated_pairs}
        return tuple(t for t in self.missing_target_ids if t not in got)

    @property
    def unfilled_category_ids(self) -> tuple[int, ...]:
        """Boş kategorilerden bu denemede fikir almayanlar (plan sırası)."""
        got = {c for c, _ in self.generated_pairs}
        return tuple(c for c in self.empty_category_ids if c not in got)


def extract_social_idea_retry_plan_snapshot(
    attempt: SocialGenerationAttempt,
    *,
    source_plan: SocialIdeaGenerationPlan,
    expected_canonical_target_ids: tuple[int, ...],
    expected_source_attempt_id: int | None = None,
) -> SocialIdeaRetryPlanSnapshot:
    """ideas_retry coverage snapshot'ını fail-closed biçimde doğrular ve DTO üretir.

    Args:
        attempt: İncelenecek SocialGenerationAttempt örneği.
        source_plan: Kaynak attempt'e ait SocialIdeaGenerationPlan.
        expected_canonical_target_ids: Güncel brief kanonik hedef ID demeti.
        expected_source_attempt_id: İsteğe bağlı kaynak attempt ID beklentisi (varsa tam eşleşmeli).

    Returns:
        SocialIdeaRetryPlanSnapshot: Doğrulanmış immutable sonuç nesnesi.

    Raises:
        SocialIdeaRetrySnapshotError: Snapshot yapısı, tipleri, planner paritesi veya
            stage uyuşmazlığında IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID veya
            kaynak attempt uyuşmazlığında IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH.
    """
    # 1. Attempt nesnesi ve stage doğrulaması
    if not isinstance(attempt, SocialGenerationAttempt):
        raise SocialIdeaRetrySnapshotError(
            "Attempt nesnesi geçerli bir SocialGenerationAttempt örneği olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="attempt",
        )

    if attempt.stage != "ideas_retry":
        raise SocialIdeaRetrySnapshotError(
            "Attempt aşaması 'ideas_retry' olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="stage",
        )

    # 2. expected_canonical_target_ids doğrulaması
    if not isinstance(expected_canonical_target_ids, tuple):
        raise SocialIdeaRetrySnapshotError(
            "Beklenen kanonik hedef listesi bir demet (tuple) olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="expected_canonical_target_ids",
        )

    if not (1 <= len(expected_canonical_target_ids) <= 6):
        raise SocialIdeaRetrySnapshotError(
            "Beklenen kanonik hedef adedi 1 ile 6 arasında olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="expected_canonical_target_ids",
        )

    seen_exp_canon: set[int] = set()
    for tid in expected_canonical_target_ids:
        if isinstance(tid, bool) or type(tid) is not int or tid <= 0:
            raise SocialIdeaRetrySnapshotError(
                "Beklenen kanonik hedef listesi pozitif tamsayılardan oluşmalıdır.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="expected_canonical_target_ids",
            )
        if tid in seen_exp_canon:
            raise SocialIdeaRetrySnapshotError(
                "Beklenen kanonik hedef listesinde mükerrer ID bulunamaz.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="expected_canonical_target_ids",
            )
        seen_exp_canon.add(tid)

    # 3. expected_source_attempt_id doğrulaması (varsa)
    if expected_source_attempt_id is not None:
        if (
            isinstance(expected_source_attempt_id, bool)
            or type(expected_source_attempt_id) is not int
            or expected_source_attempt_id <= 0
        ):
            raise SocialIdeaRetrySnapshotError(
                "Beklenen kaynak attempt ID pozitif bir tamsayı olmalıdır.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="expected_source_attempt_id",
            )

    # 4. source_plan doğrulaması
    if not isinstance(source_plan, SocialIdeaGenerationPlan):
        raise SocialIdeaRetrySnapshotError(
            "Kaynak plan geçerli bir SocialIdeaGenerationPlan örneği olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="source_plan",
        )

    # 5. Coverage üst yapı doğrulaması
    cov = attempt.coverage
    if not isinstance(cov, dict):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage snapshot bir sözlük (dict) olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="coverage",
        )

    if cov.get("schema_version") != "ideas_retry_plan_v1":
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage snapshot geçersiz schema_version taşıyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="schema_version",
        )

    expected_top_keys = {"schema_version", "request", "baseline", "plan", "generated"}
    # "metrics" (plan §3 Ölçüm) opsiyoneldir; yalnız terminal yazımda eklenir.
    if set(cov.keys()) not in (expected_top_keys, expected_top_keys | {"metrics"}):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage snapshot beklenmeyen veya eksik alanlar içeriyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="coverage",
        )
    if "metrics" in cov:
        raw_metrics = cov["metrics"]
        if not isinstance(raw_metrics, dict) or any(
            not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int) or v < 0
            for k, v in raw_metrics.items()
        ):
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage metrics bloğu geçersiz.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="metrics",
            )

    # 6. Request bloğu doğrulaması
    req = cov.get("request")
    if not isinstance(req, dict) or set(req.keys()) != {"source_attempt_id"}:
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage request bloğu geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="request",
        )

    source_id = req.get("source_attempt_id")
    if isinstance(source_id, bool) or type(source_id) is not int or source_id <= 0:
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage source_attempt_id geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="request.source_attempt_id",
        )

    if expected_source_attempt_id is not None and source_id != expected_source_attempt_id:
        raise SocialIdeaRetrySnapshotError(
            "Replay request source_attempt_id parametresi mevcut attempt ile eşleşmiyor.",
            error_code="IDEA_RETRY_ATTEMPT_REQUEST_MISMATCH",
            field="request.source_attempt_id",
        )

    # 7. Baseline bloğu doğrulaması
    baseline = cov.get("baseline")
    legacy_baseline_keys = {"canonical_target_ids", "persisted_target_ids_at_start"}
    if not isinstance(baseline, dict) or set(baseline.keys()) not in (
        legacy_baseline_keys,
        legacy_baseline_keys | {"persisted_category_ids_at_start"},
    ):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage baseline bloğu geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="baseline",
        )

    canonical_raw = baseline.get("canonical_target_ids")
    if (
        not isinstance(canonical_raw, list)
        or len(canonical_raw) < 1
        or len(canonical_raw) > 6
    ):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage canonical_target_ids geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="baseline.canonical_target_ids",
        )

    seen_canon: set[int] = set()
    for tid in canonical_raw:
        if isinstance(tid, bool) or type(tid) is not int or tid <= 0 or tid in seen_canon:
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage canonical_target_ids içinde geçersiz veya mükerrer ID tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="baseline.canonical_target_ids",
            )
        seen_canon.add(tid)

    if tuple(canonical_raw) != expected_canonical_target_ids:
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage canonical_target_ids güncel brief hedefleriyle uyuşmuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="baseline.canonical_target_ids",
        )

    persisted_raw = baseline.get("persisted_target_ids_at_start")
    if not isinstance(persisted_raw, list):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage persisted_target_ids_at_start geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="baseline.persisted_target_ids_at_start",
        )

    seen_persisted: set[int] = set()
    for tid in persisted_raw:
        if isinstance(tid, bool) or type(tid) is not int or tid <= 0 or tid in seen_persisted:
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage persisted_target_ids_at_start içinde geçersiz veya mükerrer ID tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="baseline.persisted_target_ids_at_start",
            )
        seen_persisted.add(tid)

    if not seen_persisted.issubset(seen_canon):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage persisted_target_ids canonical kümenin alt kümesi olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="baseline.persisted_target_ids_at_start",
        )

    expected_persisted_order = tuple(
        tid for tid in expected_canonical_target_ids if tid in seen_persisted
    )
    if tuple(persisted_raw) != expected_persisted_order:
        raise SocialIdeaRetrySnapshotError(
            "Snapshot persisted_target_ids_at_start canonical sırayı korumuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="baseline.persisted_target_ids_at_start",
        )

    persisted_tuple = tuple(persisted_raw)

    # 7b. Kategori baseline'ı (yeni mod) — yoksa legacy
    is_legacy = "persisted_category_ids_at_start" not in baseline
    persisted_cat_tuple: tuple[int, ...] | None = None
    if not is_legacy:
        cat_raw = baseline.get("persisted_category_ids_at_start")
        if not isinstance(cat_raw, list):
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage persisted_category_ids_at_start geçersiz.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="baseline.persisted_category_ids_at_start",
            )
        plan_cat_order = tuple(source_plan.covered_category_ids)
        seen_cat: set[int] = set()
        for cid in cat_raw:
            if (
                isinstance(cid, bool)
                or type(cid) is not int
                or cid <= 0
                or cid in seen_cat
                or cid not in plan_cat_order
            ):
                raise SocialIdeaRetrySnapshotError(
                    "Attempt coverage persisted_category_ids_at_start içinde geçersiz ID var.",
                    error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                    field="baseline.persisted_category_ids_at_start",
                )
            seen_cat.add(cid)
        if tuple(cat_raw) != tuple(c for c in plan_cat_order if c in seen_cat):
            raise SocialIdeaRetrySnapshotError(
                "persisted_category_ids_at_start plan kategori sırasını korumuyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="baseline.persisted_category_ids_at_start",
            )
        persisted_cat_tuple = tuple(cat_raw)

    # 8. Saf Retry Planner ile beklenen planı yeniden üret
    try:
        expected_plan = build_social_idea_retry_plan(
            source_plan=source_plan,
            canonical_target_ids=expected_canonical_target_ids,
            persisted_target_ids=persisted_tuple,
            persisted_category_ids=persisted_cat_tuple,
        )
    except Exception:
        raise SocialIdeaRetrySnapshotError(
            "Snapshot baseline verisinden geçerli bir retry planı üretilemedi.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan",
        )

    # 9. Plan bloğu şeması ve planner paritesi
    plan_raw = cov.get("plan")
    expected_plan_keys = {"total_requested", "missing_target_ids", "assignments"}
    if not is_legacy:
        expected_plan_keys = expected_plan_keys | {"empty_category_ids"}
    if not isinstance(plan_raw, dict) or set(plan_raw.keys()) != expected_plan_keys:
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage plan bloğu geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan",
        )

    if not is_legacy:
        empty_raw = plan_raw.get("empty_category_ids")
        if not isinstance(empty_raw, list) or tuple(empty_raw) != expected_plan.empty_category_ids:
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage empty_category_ids planner sonucuyla uyuşmuyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="plan.empty_category_ids",
            )

    total_req = plan_raw.get("total_requested")
    if (
        isinstance(total_req, bool)
        or type(total_req) is not int
        or total_req != expected_plan.total_requested
    ):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage total_requested geçersiz veya planner sonucuyla uyuşmuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.total_requested",
        )

    missing_raw = plan_raw.get("missing_target_ids")
    if (
        not isinstance(missing_raw, list)
        or tuple(missing_raw) != expected_plan.missing_target_ids
    ):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage missing_target_ids planner sonucuyla uyuşmuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.missing_target_ids",
        )

    assignments_raw = plan_raw.get("assignments")
    if (
        not isinstance(assignments_raw, list)
        or len(assignments_raw) != len(expected_plan.assignments)
    ):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage assignments adedi planner sonucuyla uyuşmuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.assignments",
        )

    seen_assign_targets: set[int] = set()
    seen_assign_pairs: set[tuple[int, int]] = set()
    for idx, a in enumerate(assignments_raw):
        if not isinstance(a, dict) or set(a.keys()) != {
            "category_id",
            "target_id",
            "requested_count",
        }:
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage assignment öğesi şeması geçersiz.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="plan.assignments",
            )
        cid = a.get("category_id")
        tid = a.get("target_id")
        rc = a.get("requested_count")
        exp_a = expected_plan.assignments[idx]
        if (
            isinstance(cid, bool)
            or type(cid) is not int
            or cid != exp_a.category_id
            or isinstance(tid, bool)
            or type(tid) is not int
            or tid != exp_a.target_id
            or isinstance(rc, bool)
            or type(rc) is not int
            or rc != 1
        ):
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage assignment alanı planner sonucuyla uyuşmuyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="plan.assignments",
            )
        # Legacy: atama başına benzersiz hedef. Yeni mod: benzersiz (kategori, hedef)
        # çifti (boş kategori ataması kapsanmış bir hedefi de isteyebilir).
        if (is_legacy and tid in seen_assign_targets) or (cid, tid) in seen_assign_pairs:
            raise SocialIdeaRetrySnapshotError(
                "Attempt coverage assignments içinde mükerrer atama tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="plan.assignments",
            )
        seen_assign_targets.add(tid)
        seen_assign_pairs.add((cid, tid))

    # 10. Set cebiri & hedef sırası invariantları
    seen_missing = set(missing_raw)
    if seen_persisted & seen_missing:
        raise SocialIdeaRetrySnapshotError(
            "Persisted ve missing target kümeleri kesişemez.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.missing_target_ids",
        )

    if (seen_persisted | seen_missing) != seen_canon:
        raise SocialIdeaRetrySnapshotError(
            "Persisted ve missing kümelerinin birleşimi canonical target kümesine eşit olmalıdır.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.missing_target_ids",
        )

    expected_missing_order = tuple(
        tid for tid in expected_canonical_target_ids if tid in seen_missing
    )
    if tuple(missing_raw) != expected_missing_order:
        raise SocialIdeaRetrySnapshotError(
            "missing_target_ids canonical hedef sırasını korumuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.missing_target_ids",
        )

    if is_legacy and [a["target_id"] for a in assignments_raw] != missing_raw:
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage assignments sırası missing_target_ids ile eşleşmiyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.assignments",
        )
    # Her eksik hedef en az bir atama almalıdır (her iki mod)
    if not seen_missing.issubset(seen_assign_targets):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage assignments eksik hedeflerin tamamını kapsamıyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="plan.assignments",
        )

    # 11. attempt.requested_target_ids doğrulaması (legacy'de = missing_target_ids)
    requested_list = list(expected_plan.requested_target_ids)
    req_targets = attempt.requested_target_ids
    if not isinstance(req_targets, (list, tuple)) or list(req_targets) != requested_list:
        raise SocialIdeaRetrySnapshotError(
            "attempt.requested_target_ids snapshot atama hedefleriyle eşleşmiyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="requested_target_ids",
        )

    seen_req_tids: set[int] = set()
    for r_tid in req_targets:
        if isinstance(r_tid, bool) or type(r_tid) is not int or r_tid <= 0:
            raise SocialIdeaRetrySnapshotError(
                "attempt.requested_target_ids içinde geçersiz ID tipi tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="requested_target_ids",
            )
        if r_tid in seen_req_tids:
            raise SocialIdeaRetrySnapshotError(
                "attempt.requested_target_ids içinde mükerrer ID tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="requested_target_ids",
            )
        seen_req_tids.add(r_tid)

    # 12. Generated bloğu doğrulaması
    gen_dict = cov.get("generated")
    expected_gen_keys = {"total_accepted", "target_ids"}
    if not is_legacy:
        expected_gen_keys = expected_gen_keys | {"assignments"}
    if not isinstance(gen_dict, dict) or set(gen_dict.keys()) != expected_gen_keys:
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage generated bloğu geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="generated",
        )

    tot_acc = gen_dict.get("total_accepted")
    gen_tids = gen_dict.get("target_ids")
    if (
        isinstance(tot_acc, bool)
        or type(tot_acc) is not int
        or tot_acc < 0
        or tot_acc > total_req
        or not isinstance(gen_tids, list)
    ):
        raise SocialIdeaRetrySnapshotError(
            "Attempt coverage generated alan tipleri veya total_accepted değeri geçersiz.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="generated.total_accepted",
        )

    if is_legacy and tot_acc != len(gen_tids):
        raise SocialIdeaRetrySnapshotError(
            "Generated total_accepted ile target_ids uzunluğu uyuşmuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="generated.total_accepted",
        )

    seen_gen_tids: set[int] = set()
    for g_id in gen_tids:
        if isinstance(g_id, bool) or type(g_id) is not int or g_id <= 0:
            raise SocialIdeaRetrySnapshotError(
                "Generated target_ids içinde geçersiz ID tipi tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="generated.target_ids",
            )
        if g_id in seen_gen_tids:
            raise SocialIdeaRetrySnapshotError(
                "Generated target_ids içinde mükerrer ID tespit edildi.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="generated.target_ids",
            )
        if g_id not in requested_list:
            raise SocialIdeaRetrySnapshotError(
                "Generated target_ids atama hedefleri dışından ID içeriyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="generated.target_ids",
            )
        seen_gen_tids.add(g_id)

    expected_gen_order = [
        tid for tid in expected_canonical_target_ids if tid in seen_gen_tids
    ]
    if gen_tids != expected_gen_order:
        raise SocialIdeaRetrySnapshotError(
            "Generated target_ids sırası canonical hedef sırasına uymuyor.",
            error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
            field="generated.target_ids",
        )

    plan_pairs = expected_plan.assignment_pairs
    if is_legacy:
        target_to_cat = {t: c for c, t in plan_pairs}
        generated_pairs = tuple((target_to_cat[t], t) for t in gen_tids)
    else:
        gen_assign_raw = gen_dict.get("assignments")
        if not isinstance(gen_assign_raw, list) or len(gen_assign_raw) != tot_acc:
            raise SocialIdeaRetrySnapshotError(
                "Generated assignments total_accepted ile uyuşmuyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="generated.assignments",
            )
        parsed_pairs: list[tuple[int, int]] = []
        for item in gen_assign_raw:
            if not isinstance(item, dict) or set(item.keys()) != {"category_id", "target_id"}:
                raise SocialIdeaRetrySnapshotError(
                    "Generated assignment öğesi şeması geçersiz.",
                    error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                    field="generated.assignments",
                )
            g_cid = item.get("category_id")
            g_tid = item.get("target_id")
            if (
                isinstance(g_cid, bool)
                or type(g_cid) is not int
                or isinstance(g_tid, bool)
                or type(g_tid) is not int
                or (g_cid, g_tid) not in plan_pairs
                or (g_cid, g_tid) in parsed_pairs
            ):
                raise SocialIdeaRetrySnapshotError(
                    "Generated assignment plan dışı veya mükerrer.",
                    error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                    field="generated.assignments",
                )
            parsed_pairs.append((g_cid, g_tid))
        if parsed_pairs != [p for p in plan_pairs if p in parsed_pairs]:
            raise SocialIdeaRetrySnapshotError(
                "Generated assignments plan atama sırasına uymuyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="generated.assignments",
            )
        if {t for _, t in parsed_pairs} != seen_gen_tids:
            raise SocialIdeaRetrySnapshotError(
                "Generated target_ids generated assignments ile uyuşmuyor.",
                error_code="IDEA_RETRY_ATTEMPT_SNAPSHOT_INVALID",
                field="generated.target_ids",
            )
        generated_pairs = tuple(parsed_pairs)

    return SocialIdeaRetryPlanSnapshot(
        source_attempt_id=source_id,
        canonical_target_ids=expected_canonical_target_ids,
        persisted_target_ids_at_start=persisted_tuple,
        missing_target_ids=tuple(missing_raw),
        plan=expected_plan,
        generated_total_accepted=tot_acc,
        generated_target_ids=tuple(gen_tids),
        persisted_category_ids_at_start=persisted_cat_tuple,
        generated_pairs=generated_pairs,
    )
