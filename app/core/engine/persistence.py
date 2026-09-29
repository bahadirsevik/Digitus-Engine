"""Motor v3 asama sonucu yazimi ve resume — FAIL-CLOSED.

Iki kural (plan_algoritma_entegrasyonu.md §4):

  YAZMA  : `engine_stage_results` satiri YALNIZ eksiksiz sema ve ID
           dogrulamasi gectikten sonra yazilir. Beklenen ID'lerden biri
           eksikse veya bir payload zorunlu alani tasimiyorsa HICBIR satir
           yazilmaz — yarim sonuc checkpoint SAYILMAZ.

  RESUME : Mevcut satir tamamlanmis sayilmak icin run'in muhurlu baglamiyla
           (model + prompt SHA + firm_block SHA) uyusmak ZORUNDADIR.
           Uyusmazlikta SESSIZ YENIDEN KULLANIM YAPILMAZ: hata firlatilir.

Bu modul AI cagirmaz; yalniz dogrulama ve kalicilik.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from app.database.models import (
    ChannelPool, EngineSelection, EngineStageResult, ScoringRun,
)

MANIFEST_KEY = "engine_v3"
# Manifest sozlesme surumu. 1 = lokasyon politikasi ONCESI muhurler
# (yalnizca `location_filter_mode="none"` ile geriye donuk tamamlanabilir),
# 2 = `location_policy` ZORUNLU.
LEGACY_MANIFEST_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 2
# V2 snapshot'inin TASIMASI GEREKEN alanlar: yalniz fingerprint yeterli
# degildir — denetim ve sozluk kimligi de muhrun parcasidir.
LOCATION_POLICY_REQUIRED_FIELDS = (
    "mode",
    "focus_cities",
    "exempt_terms",
    "city_lexicon_version",
    "city_lexicon_sha256",
    "enforcement_fingerprint",
)

SCOPE_TYPES = ("keyword", "family", "url_group", "run")


class StageValidationError(ValueError):
    """Asama cevabi eksik/bozuk — satir YAZILMAZ."""


class StageContextMismatch(RuntimeError):
    """Kayitli satir muhurlu baglamla uyusmuyor — sessiz yeniden kullanim YOK."""


@dataclass(frozen=True)
class StageContext:
    """Bir asamanin muhurlu kimligi."""
    model: str
    prompt_sha: str
    firm_block_sha256: str

    def as_dict(self) -> Dict[str, str]:
        return {"model": self.model, "prompt_sha": self.prompt_sha,
                "firm_block_sha256": self.firm_block_sha256}


# ── execution_manifest muhru ─────────────────────────────────────────

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def seal_manifest(run: ScoringRun, *, firm_block_sha256: str,
                  algorithm_versions: Mapping[str, str],
                  models: Mapping[str, str],
                  prompt_shas: Mapping[str, str],
                  location_policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Run'in v3 baglamini `execution_manifest` icine muhurler.

    Muhur TAMAMEN DEGISMEZDIR: firma hash'i, algoritma kilit kimlikleri,
    kanal/asama modelleri, prompt SHA'lari ve lokasyon politikasi birlikte
    muhurlenir. Ikinci muhurleme YALNIZ butun nesne BIREBIR ayniysa kabul
    edilir; tek bir model, kilit kimligi veya prompt SHA'si degisse bile
    fail-closed reddedilir (ayni firma hash'i buna izin VERMEZ).

    `location_policy` ZORUNLUDUR (sema surumu 2): yeni run'lar lokasyon
    snapshot'i olmadan muhurlenemez — eksik snapshot sessizce "filtre yok"
    varsayilanina DUSMEZ (plan_v3_lokasyon_filtresi.md §5.4).

    Mevcut manifest anahtarlari KORUNUR; yalniz `engine_v3` bolumu yazilir.
    """
    if not isinstance(location_policy, Mapping):
        raise StageContextMismatch(
            f"run {run.id}: lokasyon politikasi snapshot'i eksik veya bozuk — "
            "muhurleme reddedildi (sessizce 'filtre yok' varsayilmaz)")
    missing = [field for field in LOCATION_POLICY_REQUIRED_FIELDS
               if field not in location_policy]
    if missing or not location_policy.get("enforcement_fingerprint"):
        raise StageContextMismatch(
            f"run {run.id}: lokasyon politikasi snapshot'inda zorunlu alan(lar) "
            f"eksik: {missing or ['enforcement_fingerprint']} — "
            "muhurleme reddedildi")
    section = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "firm_block_sha256": firm_block_sha256,
        "algorithm_versions": dict(algorithm_versions),
        "models": dict(models),
        "prompt_shas": dict(prompt_shas),
        "location_policy": dict(location_policy),
    }
    manifest = dict(run.execution_manifest or {})
    existing = manifest.get(MANIFEST_KEY)
    if existing is not None and _canonical(existing) != _canonical(section):
        raise StageContextMismatch(
            f"run {run.id} zaten muhurlu ve yeni muhur BIREBIR ayni degil — "
            "motor baglami degismez (sessizce yeniden muhurlenmez)")
    manifest[MANIFEST_KEY] = section
    run.execution_manifest = manifest
    return manifest


def verify_stage_context(run: ScoringRun, stage: str,
                         context: StageContext) -> None:
    """Asama baglamini GERCEK run manifestiyle dogrular — fail-closed.

    Muhurde o asama icin kayitli model ve prompt SHA'si YOKSA da reddedilir:
    muhurlenmemis bir asama kosulamaz.
    """
    section = manifest_section(run)
    if context.firm_block_sha256 != section.get("firm_block_sha256"):
        raise StageContextMismatch(
            f"{stage}: firm_block_sha256 muhurle uyusmuyor — profil degismis")
    expected_model = (section.get("models") or {}).get(stage)
    if expected_model is None or context.model != expected_model:
        raise StageContextMismatch(
            f"{stage}: model muhurle uyusmuyor "
            f"(muhur={expected_model!r}, gelen={context.model!r})")
    expected_prompt = (section.get("prompt_shas") or {}).get(stage)
    if expected_prompt is None or context.prompt_sha != expected_prompt:
        raise StageContextMismatch(
            f"{stage}: prompt SHA muhurle uyusmuyor")


def manifest_section(run: ScoringRun) -> Dict[str, Any]:
    """Muhurlu v3 bolumu; yoksa fail-closed."""
    manifest = run.execution_manifest or {}
    section = manifest.get(MANIFEST_KEY) if isinstance(manifest, dict) else None
    if not isinstance(section, dict) or not section.get("firm_block_sha256"):
        raise StageContextMismatch(
            f"run {run.id} icin muhurlu motor baglami yok — resume reddedildi")
    return section


def manifest_firm_block_sha(run: ScoringRun) -> str:
    return str(manifest_section(run)["firm_block_sha256"])


def manifest_location_policy(run: ScoringRun) -> Optional[Dict[str, Any]]:
    """Muhurlu lokasyon politikasi; YALNIZ gercek legacy muhurde None.

    Karar SEMAYA gore verilir — `location_policy`nin yoklugu TEK BASINA
    "eski muhur" sayilmaz. Gercek legacy muhurde sema surumu de politika da
    YOKTUR; ikisinin karisimi tutarsiz semadir ve kabul edilmez:

      | sema anahtari | politika | sonuc                        |
      |---------------|----------|------------------------------|
      | yok           | yok      | None (gercek legacy)         |
      | yok           | var      | fail-closed (tutarsiz)       |
      | acik None     | -        | fail-closed                  |
      | 1             | yok      | None (acik legacy)           |
      | 1             | var      | fail-closed (tutarsiz)       |
      | 2             | eksiksiz | politika                     |
      | 2             | eksik    | fail-closed                  |
      | diger         | -        | fail-closed                  |

    None donmesi "filtre yok" ANLAMINA GELMEZ — yalnizca bu run'in lokasyon
    sozlesmesinden once muhurlendigini soyler. Cagiran (finalize) eski
    manifesti ancak CANLI profil filtresi kapaliysa kabul eder; filtre
    acilmissa kosu reddedilir ve yeni run istenir (plan §5.4).
    """
    section = manifest_section(run)
    has_version_key = "manifest_schema_version" in section
    raw_version = section.get("manifest_schema_version")
    policy = section.get("location_policy")

    if not has_version_key:
        # Sema anahtari YOK: yalniz politika da yoksa gercek legacy'dir.
        if policy is None:
            return None
        raise StageContextMismatch(
            f"run {run.id}: manifest sema surumu yok ancak lokasyon politikasi "
            "tasiyor — tutarsiz muhur, teslimat reddedildi")

    version = raw_version
    if not isinstance(version, int) or isinstance(version, bool):
        raise StageContextMismatch(
            f"run {run.id}: manifest sema surumu gecersiz ({raw_version!r}) — "
            "teslimat reddedildi")
    if version not in (LEGACY_MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION):
        raise StageContextMismatch(
            f"run {run.id}: desteklenmeyen manifest sema surumu {version} "
            f"(bilinen: {LEGACY_MANIFEST_SCHEMA_VERSION}, "
            f"{MANIFEST_SCHEMA_VERSION}) — teslimat reddedildi")

    if version == LEGACY_MANIFEST_SCHEMA_VERSION:
        if policy is None:
            return None
        raise StageContextMismatch(
            f"run {run.id}: sema surumu {LEGACY_MANIFEST_SCHEMA_VERSION} "
            "lokasyon politikasi TASIYAMAZ — tutarsiz muhur, teslimat "
            "reddedildi")
    if policy is None:
        raise StageContextMismatch(
            f"run {run.id}: sema surumu {version} lokasyon politikasi ZORUNLU "
            "kilar ancak muhurde yok — eski muhur SAYILMAZ, teslimat reddedildi")
    if not isinstance(policy, dict):
        raise StageContextMismatch(
            f"run {run.id}: muhurlu lokasyon politikasi bozuk — "
            "varsayilana dusulmez, teslimat reddedildi")
    missing = [field for field in LOCATION_POLICY_REQUIRED_FIELDS
               if field not in policy]
    if missing or not policy.get("enforcement_fingerprint"):
        raise StageContextMismatch(
            f"run {run.id}: muhurlu lokasyon politikasi eksik alan tasiyor "
            f"({missing or ['enforcement_fingerprint']}) — teslimat reddedildi")
    return policy


# ── dogrulama ────────────────────────────────────────────────────────

def validate_stage_entries(entries: Mapping[Any, Mapping[str, Any]], *,
                           expected_keys: Iterable[Any],
                           required_fields: Sequence[str],
                           stage: str) -> Dict[str, Dict[str, Any]]:
    """Tam sema + ID dogrulamasi. Gecemezse HICBIR sey yazilmaz.

    Donen sozluk `scope_key -> payload`; anahtarlar string'e cevrilir
    (scope_key kolonu String'dir, int keyword_id'ler de burada normalize
    edilir ki resume karsilastirmasi tip farkindan kaymasin).
    """
    normalized = {str(key): value for key, value in entries.items()}
    expected = [str(key) for key in expected_keys]

    missing = [key for key in expected if key not in normalized]
    if missing:
        raise StageValidationError(
            f"{stage}: cevapta {len(missing)} ID eksik "
            f"(ornek: {missing[:5]}) — yarim sonuc checkpoint sayilmaz")

    unexpected = [key for key in normalized if key not in set(expected)]
    if unexpected:
        raise StageValidationError(
            f"{stage}: istenmeyen ID dondu (ornek: {unexpected[:5]})")

    for key in expected:
        payload = normalized[key]
        if not isinstance(payload, Mapping):
            raise StageValidationError(
                f"{stage}: {key} icin payload sozluk degil: {type(payload).__name__}")
        absent = [name for name in required_fields if payload.get(name) is None]
        if absent:
            raise StageValidationError(
                f"{stage}: {key} icin zorunlu alan(lar) eksik: {absent}")
    return {key: dict(normalized[key]) for key in expected}


# ── yazma / okuma ────────────────────────────────────────────────────

def write_stage_results(db: Session, *, run: ScoringRun, stage: str,
                        scope_type: str,
                        entries: Mapping[Any, Mapping[str, Any]],
                        expected_keys: Iterable[Any],
                        required_fields: Sequence[str],
                        context: StageContext) -> int:
    """Dogrulanmis asama sonucunu kalici checkpoint olarak yazar.

    Dogrulama duserse HICBIR satir yazilmaz. Basarili yazim commit edilir;
    boylece worker/container cokse bile tamamlanmis ve ucreti odenmis batch
    sonraki kosuda yeniden provider'a gonderilmez. Nihai EngineSelection ve
    ChannelPool teslimati bu fonksiyonun disinda, kendi atomik transaction'inda
    kalir.

    Baglam ONCE run'in GERCEK manifestiyle dogrulanir (muhurlu model +
    prompt SHA + firma hash). Ayni scope icin kayitli satir varsa:
      * baglam AYNIYSA  → atlanir (idempotent),
      * baglam FARKLIYSA → `StageContextMismatch` (sessiz ezme YOK).
    """
    if scope_type not in SCOPE_TYPES:
        raise StageValidationError(f"gecersiz scope_type: {scope_type!r}")
    verify_stage_context(run, stage, context)
    scoring_run_id = run.id

    validated = validate_stage_entries(
        entries, expected_keys=expected_keys,
        required_fields=required_fields, stage=stage)

    existing = {
        row.scope_key: row
        for row in db.query(EngineStageResult).filter(
            EngineStageResult.scoring_run_id == scoring_run_id,
            EngineStageResult.stage == stage,
            EngineStageResult.scope_type == scope_type,
        ).all()
    }
    for scope_key, row in existing.items():
        if scope_key in validated and not _matches(row, context):
            raise StageContextMismatch(
                f"{stage}/{scope_key}: kayitli satir muhurlu baglamla "
                "uyusmuyor — sessizce uzerine yazilmaz")

    written = 0
    for scope_key, payload in validated.items():
        if scope_key in existing:
            continue
        db.add(EngineStageResult(
            scoring_run_id=scoring_run_id,
            stage=stage,
            scope_type=scope_type,
            scope_key=scope_key,
            payload=payload,
            model=context.model,
            prompt_sha=context.prompt_sha,
            firm_block_sha256=context.firm_block_sha256,
        ))
        written += 1
    if written > 0:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
    return written


def load_stage_results(db: Session, *, run: ScoringRun, stage: str,
                       scope_type: str, context: StageContext
                       ) -> Dict[str, Dict[str, Any]]:
    """Resume: kayitli sonuclari YALNIZ baglam uyusuyorsa tamamlanmis sayar.

    `scope_type` ZORUNLUDUR (Faz 2): ayni run'da artik birden fazla scope
    turu (keyword / family / url_group / run) bulunuyor; filtresiz yukleme
    farkli turleri tek sozlukte karistirir ve scope_key'ler carpisabilirdi.

    Baglam once run'in GERCEK manifestiyle dogrulanir; muhurden sapan bir
    baglamla resume DENENEMEZ.
    """
    if scope_type not in SCOPE_TYPES:
        raise StageValidationError(f"gecersiz scope_type: {scope_type!r}")
    verify_stage_context(run, stage, context)
    scoring_run_id = run.id
    query = db.query(EngineStageResult).filter(
        EngineStageResult.scoring_run_id == scoring_run_id,
        EngineStageResult.stage == stage,
        EngineStageResult.scope_type == scope_type,
    )

    out: Dict[str, Dict[str, Any]] = {}
    for row in query.all():
        if not _matches(row, context):
            raise StageContextMismatch(
                f"{stage}/{row.scope_key}: kayitli baglam "
                f"(model={row.model}, prompt={row.prompt_sha[:12]}…, "
                f"firm={row.firm_block_sha256[:12]}…) muhurlu baglamla "
                "uyusmuyor — yeniden kullanim reddedildi")
        out[row.scope_key] = dict(row.payload or {})
    return out


def _matches(row: EngineStageResult, context: StageContext) -> bool:
    return (row.model == context.model
            and row.prompt_sha == context.prompt_sha
            and row.firm_block_sha256 == context.firm_block_sha256)


# ── engine_selections ve channel_pools kaliciligi ──────────────────

def write_engine_selections(
    db: Session,
    *,
    scoring_run_id: int,
    selections: Sequence[Mapping[str, Any] | EngineSelection],
) -> int:
    """EngineSelection satirlarini yazar.

    XOR kisiti kontrol edilir:
      (final_rank is None) != (exclude_reason is None)
      Satir ya secilmis ya elenmis olmalidir.
    """
    written = 0
    for item in selections:
        if isinstance(item, EngineSelection):
            obj = item
        else:
            final_rank = item.get("final_rank")
            exclude_reason = item.get("exclude_reason")
            # XOR kontrolu
            if (final_rank is None) == (exclude_reason is None):
                raise StageValidationError(
                    f"EngineSelection XOR kisiti ihlali (keyword_id={item.get('keyword_id')}): "
                    f"final_rank={final_rank!r}, exclude_reason={exclude_reason!r} — "
                    "biri dolu digeri None olmalidir"
                )
            obj = EngineSelection(
                scoring_run_id=scoring_run_id,
                keyword_id=int(item["keyword_id"]),
                channel=str(item["channel"]).upper(),
                algorithm_rank=int(item["algorithm_rank"]),
                scores=item.get("scores"),
                pool_class=item.get("pool_class"),
                family_id=str(item["family_id"]) if item.get("family_id") is not None else None,
                priority=str(item["priority"]) if item.get("priority") is not None else None,
                final_rank=int(final_rank) if final_rank is not None else None,
                exclude_reason=str(exclude_reason) if exclude_reason is not None else None,
                policy_version=int(item["policy_version"]) if item.get("policy_version") is not None else None,
            )
        db.add(obj)
        written += 1
    db.flush()
    return written


def load_engine_selections(
    db: Session,
    *,
    scoring_run_id: int,
    channel: Optional[str] = None,
) -> List[EngineSelection]:
    """Kayitli EngineSelection satirlarini yukler."""
    q = db.query(EngineSelection).filter(
        EngineSelection.scoring_run_id == scoring_run_id
    )
    if channel:
        q = q.filter(EngineSelection.channel == channel.upper())
    return q.order_by(EngineSelection.algorithm_rank.asc()).all()


def load_channel_pools(
    db: Session,
    *,
    scoring_run_id: int,
    channel: Optional[str] = None,
) -> List[ChannelPool]:
    """Kayitli ChannelPool satirlarini yukler."""
    q = db.query(ChannelPool).filter(
        ChannelPool.scoring_run_id == scoring_run_id
    )
    if channel:
        q = q.filter(ChannelPool.channel == channel.upper())
    return q.order_by(ChannelPool.final_rank.asc()).all()
