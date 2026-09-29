# -*- coding: utf-8 -*-
"""Deneme workspace'lerinde ÜCRETLİ koşu yetkisi (sade, fail-closed).

Yetki bir PAYLOAD SÖZLEŞMESİ DEĞİLDİR: kapasiteler kullanıcının UI'dan
serbestçe seçtiği değerlerdir. Yetki yalnız şu sınırları kurar:

1. İzinli kombinasyonlar — `v3 + ADS/SEO` ve `v3 + SOCIAL`.
   (Kanal/algoritma birleşimi; kapasiteler SERBEST.)
2. En fazla iki BAŞARILI koşu. Koşu oluşturmak hak TÜKETMEZ; yalnız
   başarıyla tamamlanan koşu sayılır — başarısız koşu hak yakmaz.
3. Workspace başına maliyet sınırı (`assignment_cost_cap_usd`). ATOMİK
   uygulama kapsamı: `AiCostLedger` (kanal atama downstream + corpus
   screening) — çağrı ÖNCESİ rezervasyonla. Bu ledger'ın DIŞINDA kalan
   çağrılar (ör. relevance embedding) usage event'lerinden ÖLÇÜLÜR ve
   create/execute/dispatch kapılarında toplam maruziyete DAHİL EDİLİR;
   çağrı başına atomik DEĞİLDİR (bkz. `cap_scope`).
4. Kaynak workspace'lere hiçbir şey yazılmaz (yetki yalnız deneme
   workspace'lerinin `validation_data.trial_setup` alanında yaşar).

Kullanıcının seçtiği kapasiteler ve çalıştırma bağlamı snapshot'ı koşu
açılışında DENETİM için kaydedilir (`run_audit` + run manifest); bunlar
önceden onaylanmış bir kapasiteyle EŞLEŞTİRME amacıyla KULLANILMAZ.

Eşzamanlılık: aynı workspace'te en fazla iki AKTİF koşu ve aynı
kombinasyonda en fazla BİR aktif koşu olabilir; yarış kontrolü workspace
satır kilidiyle yapılır (`lock_workspace`).

Kota kaynağı KIRPILABİLİR denetim listesi DEĞİLDİR: `run_audit` son 50
kayda kırpılabilir, kota `scoring_runs.execution_manifest` içindeki
`authorization_id` eşleşmesi ve DB durumundan hesaplanır (`trial_runs`).

Tipli kodlar:
    TRIAL_RUN_NOT_AUTHORIZED        yetki yok / geri alınmış
    TRIAL_RUN_COMBINATION_NOT_ALLOWED  kanal+algoritma birleşimi izinsiz
    TRIAL_RUN_QUOTA_EXHAUSTED       iki başarılı koşu tamamlanmış
    TRIAL_RUN_ACTIVE_LIMIT          iki aktif koşu zaten var
    TRIAL_RUN_COMBINATION_ACTIVE    aynı kombinasyonda aktif koşu var
    TRIAL_RUN_AUTHORIZATION_MISMATCH koşu bu yetki dönemine ait değil
    TRIAL_RUN_COST_CAP_EXCEEDED     maliyet sınırı aşılmış
    INCOMPATIBLE_TRIAL_AUTHORIZATION yetkilendirme sözleşmesi eski/uyumsuz/eksik/bozuk
    LEGACY_TRIAL_AUTHORIZATION      yalnız eski motor kombinasyonları tanımlı

Ledger kapsamındaki her çağrı için sınır, kalıcı ledger'ın `reserve()`
işleminde ATOMİK uygulanır (`app/core/telemetry/ai_cost_budget.py`):
workspace satırı kilitliyken tüm koşuların maruziyeti toplanır ve sınır
aşılacaksa çağrı YAPILMAZ. Maruziyet ayrıştırılmış hesaptır:
`max(kapanmış ledger, usage) + açık rezervasyon tavanı`.

Bu modül YALNIZ `validation_data.trial_setup` taşıyan workspace'leri
etkiler; normal müşteri workspace'lerinde hiçbir kapı eklemez.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

TRIAL_SETUP_KEY = "trial_setup"
AUTHORIZATION_KEY = "run_authorization"
AUDIT_KEY = "run_audit"

AUTHORIZATION_CONTRACT_VERSION = "V3-TRIAL-AUTHORIZATION-2026-09-22-v1"

NOT_AUTHORIZED = "TRIAL_RUN_NOT_AUTHORIZED"
COMBINATION_NOT_ALLOWED = "TRIAL_RUN_COMBINATION_NOT_ALLOWED"
QUOTA_EXHAUSTED = "TRIAL_RUN_QUOTA_EXHAUSTED"
COST_CAP_EXCEEDED = "TRIAL_RUN_COST_CAP_EXCEEDED"
ACTIVE_LIMIT = "TRIAL_RUN_ACTIVE_LIMIT"
COMBINATION_ACTIVE = "TRIAL_RUN_COMBINATION_ACTIVE"
AUTHORIZATION_MISMATCH = "TRIAL_RUN_AUTHORIZATION_MISMATCH"
INCOMPATIBLE_TRIAL_AUTHORIZATION = "INCOMPATIBLE_TRIAL_AUTHORIZATION"
LEGACY_TRIAL_AUTHORIZATION = "LEGACY_TRIAL_AUTHORIZATION"

DEFAULT_MAX_SUCCESSFUL_RUNS = 2
DEFAULT_MAX_ACTIVE_RUNS = 2
AUDIT_KEEP_LAST = 50
# Koşunun BAŞARIYLA tamamlandığı sayılan durumlar. `scored` yeterli
# değildir: kanal ataması yapılmamış bir koşu ürün çıktısı vermez.
SUCCESS_STATUSES = ("channel_assigned", "completed")
# Hâlâ iş üreten (ve ücret yakabilecek) durumlar.
ACTIVE_STATUSES = ("pending", "scoring", "scored", "relevance_computing",
                   "relevance_computed", "channel_assigning")

ALLOWED_COMBINATIONS: tuple = (
    {"id": "v3_ads_seo", "label": "v3 + ADS/SEO (SOCIAL kapalı)",
     "algorithm_version": "v3",
     "enable_ads": True, "enable_seo": True, "enable_social": False},
    {"id": "v3_social", "label": "v3 + yalnız SOCIAL",
     "algorithm_version": "v3",
     "enable_ads": False, "enable_seo": False, "enable_social": True},
)


def _get(source: Any, field: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        value = source.get(field)
    else:
        value = getattr(source, field, None)
    return default if value is None else value


def combination_of(source: Any) -> Dict[str, Any]:
    """Payload/run'dan kanal+algoritma birleşimi (kapasiteler HARİÇ)."""
    return {
        "algorithm_version": str(_get(source, "algorithm_version", "v3")),
        "enable_ads": bool(_get(source, "enable_ads", True)),
        "enable_seo": bool(_get(source, "enable_seo", True)),
        "enable_social": bool(_get(source, "enable_social", True)),
    }


def capacities_of(source: Any) -> Dict[str, Any]:
    """Kullanıcının UI'dan seçtiği kapasiteler — DENETİM için kaydedilir."""
    def _int(field):
        value = _get(source, field)
        return int(value) if value is not None else None

    return {
        "ads_capacity": _int("ads_capacity"),
        "seo_capacity": _int("seo_capacity"),
        "social_capacity": _int("social_capacity"),
        "keyword_selection_mode": str(_get(source, "keyword_selection_mode",
                                           "all")),
        "keyword_limit": _int("keyword_limit"),
        "auto_assign_channels": bool(_get(source, "auto_assign_channels",
                                          False)),
        "skip_relevance": bool(_get(source, "skip_relevance", False)),
        "default_relevance_coefficient": float(
            _get(source, "default_relevance_coefficient", 1.0)),
    }


# ── Okuma yardımcıları ───────────────────────────────────────────────
def trial_setup(workspace) -> Optional[Dict[str, Any]]:
    data = getattr(workspace, "validation_data", None) or {}
    setup = data.get(TRIAL_SETUP_KEY)
    return setup if isinstance(setup, dict) else None


def authorization_record(workspace) -> Dict[str, Any]:
    setup = trial_setup(workspace) or {}
    record = setup.get(AUTHORIZATION_KEY)
    return record if isinstance(record, dict) else {}


def is_authorized(workspace) -> bool:
    setup = trial_setup(workspace)
    if not setup or setup.get("paid_run_authorized") is not True:
        return False
    record = authorization_record(workspace)
    return bool(record.get("authorization_id")) and not record.get("revoked_at")


def allowed_combinations(workspace) -> List[Dict[str, Any]]:
    record = authorization_record(workspace)
    # Fail-closed: Kayıtlı sözleşme sürümü uyumsuzsa veya kombinasyon listesi
    # eksik/boş/hatalıysa asla varsayılan V3 kataloğunu verme.
    if record.get("contract_version") != AUTHORIZATION_CONTRACT_VERSION:
        return []
    rows = record.get("allowed_combinations")
    if not isinstance(rows, list) or not rows:
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("algorithm_version") == "v3"]


def max_successful_runs(workspace) -> int:
    value = authorization_record(workspace).get("max_successful_runs")
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SUCCESSFUL_RUNS


def assignment_cost_cap_usd(workspace) -> Optional[float]:
    """Ledger'ın ATOMİK uyguladığı cap (kanal atama + corpus screening).

    ADLANDIRMA DÜRÜSTLÜĞÜ: bu bir "tüm sağlayıcı çağrıları" toplamı
    DEĞİLDİR. `AiCostLedger` yalnız assignment attempt'ine bağlı
    çağrıları (downstream + screening) çağrı ÖNCESİ rezerve eder.
    Relevance embedding çağrıları bu ledger'ın KAPSAMI DIŞINDADIR;
    onlar `ai_usage_events`'e düşer ve create/execute/dispatch
    kapılarında ÖLÇÜLÜR (çağrı başına atomik DEĞİL).
    """
    record = authorization_record(workspace)
    value = record.get("assignment_cost_cap_usd")
    if value is None:
        # Geriye uyum: eski kayıtlarda alan adı farklıydı
        value = record.get("max_total_cost_usd")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cap_scope(workspace) -> Dict[str, Any]:
    """Cap'in NEYİ kapsadığını açıkça raporlar (sessiz iddia YOK)."""
    return {
        "assignment_cost_cap_usd": assignment_cost_cap_usd(workspace),
        "atomic_pre_call": ["channel_assignment_downstream",
                            "corpus_screening"],
        "measured_at_gates_only": ["relevance_embedding",
                                   "content_generation"],
        "note": ("Ledger cap'i YALNIZ assignment attempt'ine bağlı "
                 "çağrıları çağrı öncesi rezerve eder. Diğer sağlayıcı "
                 "çağrıları (ör. relevance embedding) usage event'lerinden "
                 "ÖLÇÜLÜR ve kapılarda toplam maruziyete dahil edilir; "
                 "çağrı başına atomik değildir."),
    }


def run_audit(workspace) -> List[Dict[str, Any]]:
    rows = authorization_record(workspace).get(AUDIT_KEY)
    return [row for row in rows if isinstance(row, dict)] if rows else []


def matching_combination(workspace, source) -> Optional[Dict[str, Any]]:
    wanted = combination_of(source)
    for row in allowed_combinations(workspace):
        if all(row.get(key) == value for key, value in wanted.items()):
            return row
    return None


def trial_runs(db, workspace) -> Dict[str, Any]:
    """Yetki döneminin koşuları — DB DURUMUNDAN ve RUN MANIFEST'inden.

    Kota, KIRPILABİLİR `run_audit` listesinden HESAPLANMAZ: kaynak,
    `scoring_runs.execution_manifest -> trial_authorization ->
    authorization_id` eşleşmesi ve koşunun DB durumudur. Böylece denetim
    listesi 50'ye kırpılsa da kota etkilenmez.
    """
    import sqlalchemy as sa

    authorization_id = authorization_record(workspace).get("authorization_id")
    if not authorization_id:
        return {"successful": [], "active": [], "failed": [],
                "active_by_combination": {}, "authorization_id": None}
    rows = db.execute(sa.text("""
        SELECT id, status,
               execution_manifest->'trial_authorization'->>'combination_id'
        FROM scoring_runs
        WHERE brand_profile_id = :w
          AND execution_manifest->'trial_authorization'->>'authorization_id'
              = :aid
        ORDER BY id
    """), {"w": workspace.id, "aid": authorization_id}).all()
    successful, active, failed = [], [], []
    active_by_combination: Dict[str, List[int]] = {}
    for run_id, status, combination_id in rows:
        if status in SUCCESS_STATUSES:
            successful.append(int(run_id))
        elif status in ACTIVE_STATUSES:
            active.append(int(run_id))
            active_by_combination.setdefault(
                combination_id or "?", []).append(int(run_id))
        else:
            failed.append(int(run_id))
    return {"successful": successful, "active": active, "failed": failed,
            "active_by_combination": active_by_combination,
            "authorization_id": authorization_id}


def successful_runs(db, workspace) -> Dict[str, Any]:
    """Başarılı koşu kotası (başarısız koşu hak TÜKETMEZ)."""
    runs = trial_runs(db, workspace)
    return {"count": len(runs["successful"]), "run_ids": runs["successful"],
            "failed_run_ids": runs["failed"]}


def max_active_runs(workspace) -> int:
    value = authorization_record(workspace).get("max_active_runs")
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_ACTIVE_RUNS


_USAGE_SPEND_SQL = """
    SELECT COALESCE(SUM(
        (COALESCE(prompt_tokens, 0)::numeric / 1000000)
            * COALESCE((price_snapshot->>'input_per_m')::numeric, 0)
      + ((COALESCE(candidates_tokens, 0)
          + COALESCE(thoughts_tokens, 0))::numeric / 1000000)
            * COALESCE((price_snapshot->>'output_per_m')::numeric, 0)
    ), 0)
    FROM ai_usage_events
    WHERE brand_profile_id = :w AND price_snapshot IS NOT NULL
"""
# Kapanmış rezervasyonlar (settle/tavan yakıldı) GERÇEKLEŞEN maliyettir;
# açık rezervasyonlar ise HENÜZ harcanmamış TAAHHÜTtür. İkisi ayrı toplanır.
_SETTLED_LEDGER_SQL = """
    SELECT COALESCE(SUM(COALESCE(r.actual_usd, r.ceiling_usd)), 0)
    FROM ai_cost_reservations r
    JOIN channel_assignment_attempts a ON a.id = r.budget_owner_attempt_id
    JOIN scoring_runs s ON s.id = a.scoring_run_id
    WHERE s.brand_profile_id = :w AND r.state <> 'reserved'
"""
_OPEN_RESERVED_SQL = """
    SELECT COALESCE(SUM(r.ceiling_usd), 0)
    FROM ai_cost_reservations r
    JOIN channel_assignment_attempts a ON a.id = r.budget_owner_attempt_id
    JOIN scoring_runs s ON s.id = a.scoring_run_id
    WHERE s.brand_profile_id = :w AND r.state = 'reserved'
"""


def workspace_cost_usd(db, workspace_id: int) -> Dict[str, float]:
    """Workspace maruziyeti — AYRIŞTIRILMIŞ hesap.

        exposure = max(settled_ledger, usage_spent) + open_reserved

    Neden `max(...)` YALNIZ kapanmış kalemlerde: settle edilen bir
    rezervasyon `ai_usage_events`'te de görünür (çifte sayım riski), bu
    yüzden ikisinden BÜYÜĞÜ alınır. AÇIK rezervasyon ise henüz hiçbir
    usage event üretmemiştir; ayrı ve TAM olarak eklenir — aksi halde
    (usage 3, settled 1, açık 5) örneğinde maruziyet 6 çıkar ve 8'lik
    gerçek taahhüt görünmez.
    """
    import sqlalchemy as sa

    usage = float(db.execute(sa.text(_USAGE_SPEND_SQL),
                             {"w": workspace_id}).scalar() or 0)
    settled = float(db.execute(sa.text(_SETTLED_LEDGER_SQL),
                               {"w": workspace_id}).scalar() or 0)
    reserved = float(db.execute(sa.text(_OPEN_RESERVED_SQL),
                                {"w": workspace_id}).scalar() or 0)
    realized = max(settled, usage)
    return {
        "usage_spent_usd": round(usage, 6),
        "settled_ledger_usd": round(settled, 6),
        "spent_usd": round(realized, 6),
        "reserved_usd": round(reserved, 6),
        "exposure_usd": round(realized + reserved, 6),
    }


# ── Kapılar ──────────────────────────────────────────────────────────
def _not_authorized(workspace) -> Dict[str, Any]:
    record = authorization_record(workspace)
    setup = trial_setup(workspace) or {}
    return {
        "code": NOT_AUTHORIZED,
        "message": (
            "Bu bir DENEME workspace'i ve ücretli koşu yetkisi yok "
            "(verilmedi veya geri alındı). Profil/anchor kontrolü ve "
            "maliyet onayından sonra scripts/authorize_trial_runs.py ile "
            "yetki verilmelidir."),
        "workspace_id": workspace.id,
        "review_required": bool(setup.get("review_required", True)),
        "revoked_at": record.get("revoked_at"),
    }


def has_only_legacy_combinations(workspace) -> bool:
    """Kaydedilmiş yetki yalnız v2/v2_1 gibi legacy kombinasyonlar mı taşıyor?"""
    record = authorization_record(workspace)
    combos = record.get("allowed_combinations")
    if not combos or not isinstance(combos, list):
        return False
    # Kaydedilmiş kombinasyonlar varsa ve HİÇBİRİ v3 değilse
    has_v3 = any(
        isinstance(c, dict) and c.get("algorithm_version") == "v3"
        for c in combos
    )
    return not has_v3


def _legacy_authorization_refused(workspace) -> Dict[str, Any]:
    return {
        "code": LEGACY_TRIAL_AUTHORIZATION,
        "message": (
            "Bu deneme workspace'inin yetkilendirmesi eski (v2/v2.1) sözleşmesine aittir. "
            "V3 motoru için otomatik yetki genişletmesi yapılmaz (fail-closed); "
            "scripts/authorize_trial_runs.py ile V3 kombinasyonları verilerek "
            "yeniden yetkilendirilmelidir."
        ),
        "workspace_id": workspace.id,
    }


def check_incompatible_authorization(workspace) -> Optional[Dict[str, Any]]:
    """Eski, uyumsuz, eksik veya bozuk yetkilendirme kaydı kontrolü (fail-closed).

    Aşağıdaki durumlarda 409 hata nesnesi döner:
    1. contract_version eksik veya AUTHORIZATION_CONTRACT_VERSION ile uyuşmuyor -> INCOMPATIBLE_TRIAL_AUTHORIZATION
    2. allowed_combinations alanı eksik, None, boş veya liste değil -> INCOMPATIBLE_TRIAL_AUTHORIZATION
    3. allowed_combinations içinde hatalı (dict olmayan) kayıt var -> INCOMPATIBLE_TRIAL_AUTHORIZATION
    4. allowed_combinations içinde hiç v3 kombinasyonu yok (yalnız legacy) -> LEGACY_TRIAL_AUTHORIZATION
    """
    record = authorization_record(workspace)
    contract_ver = record.get("contract_version")
    combos = record.get("allowed_combinations")

    if contract_ver != AUTHORIZATION_CONTRACT_VERSION:
        return {
            "code": INCOMPATIBLE_TRIAL_AUTHORIZATION,
            "message": (
                f"Bu deneme workspace'inin yetkilendirme sözleşmesi uyumsuzdur "
                f"({contract_ver!r} != {AUTHORIZATION_CONTRACT_VERSION!r}). "
                "V3 motoru için otomatik yetki genişletmesi yapılmaz (fail-closed); "
                "scripts/authorize_trial_runs.py ile V3 kombinasyonları verilerek "
                "yeniden yetkilendirilmelidir."
            ),
            "workspace_id": workspace.id,
            "contract_version": contract_ver,
        }

    if combos is None or not isinstance(combos, list) or len(combos) == 0:
        return {
            "code": INCOMPATIBLE_TRIAL_AUTHORIZATION,
            "message": (
                "Yetki kaydında izinli kombinasyonlar eksik, boş veya geçersizdir. "
                "Otomatik varsayılan V3 yetkisi verilmez (fail-closed); "
                "scripts/authorize_trial_runs.py ile yeniden yetkilendirilmelidir."
            ),
            "workspace_id": workspace.id,
        }

    if any(not isinstance(c, dict) for c in combos):
        return {
            "code": INCOMPATIBLE_TRIAL_AUTHORIZATION,
            "message": (
                "Yetki kaydında hatalı biçimlendirilmiş kombinasyon tanımı bulundu. "
                "scripts/authorize_trial_runs.py ile yeniden yetkilendirilmelidir."
            ),
            "workspace_id": workspace.id,
        }

    has_v3 = any(c.get("algorithm_version") == "v3" for c in combos if isinstance(c, dict))
    if not has_v3:
        return _legacy_authorization_refused(workspace)

    return None


def _combination_refused(workspace, source) -> Dict[str, Any]:
    wanted = combination_of(source)
    return {
        "code": COMBINATION_NOT_ALLOWED,
        "message": (
            "Bu kanal/algoritma birleşimi deneme kapsamında değil. İzinli "
            "birleşimler: v3 + ADS/SEO (SOCIAL kapalı) ve v3 + yalnız "
            "SOCIAL. Kapasiteleri istediğiniz gibi seçebilirsiniz."),
        "workspace_id": workspace.id,
        "requested": wanted,
        "allowed_combinations": [
            {key: row.get(key) for key in
             ("id", "label", "algorithm_version", "enable_ads", "enable_seo",
              "enable_social")}
            for row in allowed_combinations(workspace)],
    }


def _quota_refused(workspace, usage: Dict[str, Any]) -> Dict[str, Any]:
    limit = max_successful_runs(workspace)
    return {
        "code": QUOTA_EXHAUSTED,
        "message": (
            f"Deneme kapsamındaki {limit} başarılı koşu tamamlandı "
            f"({usage['count']}/{limit}). Yeni koşu için yetki yeniden "
            f"verilmelidir. (Başarısız koşular hak tüketmez.)"),
        "workspace_id": workspace.id,
        "max_successful_runs": limit,
        "successful_run_ids": usage["run_ids"],
    }


def check_cost_cap(db, workspace, *, stage: str) -> Optional[Dict[str, Any]]:
    """Maliyet kapısı — kapıda ÖLÇÜLEN toplam maruziyete karşı.

    Ölçüm assignment ledger'ını VE usage event'lerini (embedding dahil)
    kapsar; atomik uygulama yalnız ledger kapsamında yapılır
    (bkz. `cap_scope`).
    """
    cap = assignment_cost_cap_usd(workspace)
    if cap is None:
        return None
    cost = workspace_cost_usd(db, workspace.id)
    if cost["exposure_usd"] < cap:
        return None
    return {
        "code": COST_CAP_EXCEEDED,
        "message": (
            f"Workspace maliyet sınırı aşıldı: ${cost['exposure_usd']} >= "
            f"${cap}. Sağlayıcı çağrısı yapılmadan durduruldu."),
        "workspace_id": workspace.id,
        "stage": stage,
        "assignment_cost_cap_usd": cap,
        "cap_scope": cap_scope(workspace),
        **cost,
    }


def check_create_allowed(db, workspace, payload) -> Optional[Dict[str, Any]]:
    """Create kapısı. BAŞARILI koşu hakkı TÜKETMEZ; sınırları doğrular.

    Yarış kontrolü çağıranın aldığı workspace satır kilidi altındadır
    (bkz. `lock_workspace`): eşzamanlı iki create ne aktif koşu sınırını
    ne de aynı kombinasyonda tek aktif koşu kuralını aşabilir.
    """
    if trial_setup(workspace) is None:
        return None
    if not is_authorized(workspace):
        return _not_authorized(workspace)
    incompat = check_incompatible_authorization(workspace)
    if incompat is not None:
        return incompat
    combination = matching_combination(workspace, payload)
    if combination is None:
        return _combination_refused(workspace, payload)
    runs = trial_runs(db, workspace)
    if len(runs["successful"]) >= max_successful_runs(workspace):
        return _quota_refused(workspace, {"count": len(runs["successful"]),
                                          "run_ids": runs["successful"]})
    limit = max_active_runs(workspace)
    if len(runs["active"]) >= limit:
        return {
            "code": ACTIVE_LIMIT,
            "message": (
                f"Bu deneme workspace'inde aynı anda en fazla {limit} aktif "
                f"koşu olabilir (şu an {len(runs['active'])}). Açık koşular "
                f"bitmeden yenisi başlatılamaz."),
            "workspace_id": workspace.id,
            "max_active_runs": limit,
            "active_run_ids": runs["active"],
        }
    active_same = runs["active_by_combination"].get(combination.get("id"), [])
    if active_same:
        return {
            "code": COMBINATION_ACTIVE,
            "message": (
                f"'{combination.get('label')}' kombinasyonunda zaten aktif "
                f"bir koşu var (run #{active_same[0]}). Aynı kombinasyonda "
                f"ikinci koşu aynı anda çalıştırılamaz."),
            "workspace_id": workspace.id,
            "combination_id": combination.get("id"),
            "active_run_ids": active_same,
        }
    return check_cost_cap(db, workspace, stage="create")


def run_authorization_id(run) -> Optional[str]:
    """Koşunun manifest'ine yazılmış yetki kimliği."""
    manifest = getattr(run, "execution_manifest", None) or {}
    block = manifest.get("trial_authorization")
    return block.get("authorization_id") if isinstance(block, dict) else None


def check_run_allowed(db, workspace, run, *, stage: str
                      ) -> Optional[Dict[str, Any]]:
    """Execute / kanal atama dispatch kapısı (sağlayıcı çağrısından ÖNCE)."""
    if trial_setup(workspace) is None:
        return None
    if not is_authorized(workspace):
        return _not_authorized(workspace)
    incompat = check_incompatible_authorization(workspace)
    if incompat is not None:
        return incompat
    # Koşu, GEÇERLİ yetki dönemine ait olmalı. Eski yetki döneminde açılmış
    # (veya yetki dışında oluşmuş) bir pending run, yetki yenilenince
    # sessizce çalışamaz: sınırlar o koşu için hiç uygulanmamıştır.
    current_id = authorization_record(workspace).get("authorization_id")
    run_id_value = run_authorization_id(run)
    if run_id_value != current_id:
        return {
            "code": AUTHORIZATION_MISMATCH,
            "message": (
                f"Koşu #{run.id} bu yetki dönemine ait değil "
                f"(koşu: {str(run_id_value)[:16] or 'YOK'}, geçerli: "
                f"{str(current_id)[:16]}). Sağlayıcı çağrısı yapılmadan "
                f"durduruldu; koşu yeniden açılmalıdır."),
            "workspace_id": workspace.id,
            "run_id": run.id,
            "stage": stage,
            "run_authorization_id": run_id_value,
            "current_authorization_id": current_id,
        }
    if matching_combination(workspace, run) is None:
        return _combination_refused(workspace, run)
    runs = trial_runs(db, workspace)
    if (len(runs["successful"]) >= max_successful_runs(workspace)
            and run.id not in runs["successful"]):
        return _quota_refused(workspace, {"count": len(runs["successful"]),
                                          "run_ids": runs["successful"]})
    return check_cost_cap(db, workspace, stage=stage)


def require_create_allowed(db, workspace, payload) -> None:
    from fastapi import HTTPException

    problem = check_create_allowed(db, workspace, payload)
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)


def require_run_allowed(db, workspace, run, *, stage: str = "execute") -> None:
    from fastapi import HTTPException

    problem = check_run_allowed(db, workspace, run, stage=stage)
    if problem is not None:
        raise HTTPException(status_code=409, detail=problem)


# ── Denetim kaydı ────────────────────────────────────────────────────
def record_run_audit(workspace, run, *, combination: Dict[str, Any],
                     payload: Any,
                     snapshot_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Koşu açılışını denetime yazar. COMMIT ETMEZ.

    Kapasiteler burada KAYDEDİLİR (önceden onaylanmış bir değerle
    karşılaştırılmaz); amaç koşunun neyle başlatıldığının denetlenebilir
    olmasıdır.
    """
    data = dict(getattr(workspace, "validation_data", None) or {})
    setup = dict(data.get(TRIAL_SETUP_KEY) or {})
    record = dict(setup.get(AUTHORIZATION_KEY) or {})
    entry = {
        "run_id": int(run.id),
        "created_at": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat(),
        "authorization_id": record.get("authorization_id"),
        "combination_id": combination.get("id"),
        "combination": combination_of(payload),
        "capacities": capacities_of(payload),
        "execution_snapshot_sha256": snapshot_sha256,
    }
    # Denetim listesi KIRPILABİLİR (son AUDIT_KEEP_LAST kayıt). Kota bu
    # listeden HESAPLANMAZ (bkz. trial_runs) — kırpma hakları etkilemez.
    entries = list(record.get(AUDIT_KEY) or []) + [entry]
    if len(entries) > AUDIT_KEEP_LAST:
        record["audit_truncated"] = True
        record["audit_dropped_count"] = (
            int(record.get("audit_dropped_count") or 0)
            + len(entries) - AUDIT_KEEP_LAST)
        entries = entries[-AUDIT_KEEP_LAST:]
    record[AUDIT_KEY] = entries
    setup[AUTHORIZATION_KEY] = record
    data[TRIAL_SETUP_KEY] = setup
    workspace.validation_data = data
    return entry


def lock_workspace(db, workspace_id: int):
    """Workspace satırını `SELECT ... FOR UPDATE` ile kilitler."""
    from app.database.models import BrandProfile

    return (db.query(BrandProfile)
            .filter(BrandProfile.id == workspace_id)
            .populate_existing()
            .with_for_update()
            .one())


__all__ = ["ACTIVE_LIMIT", "ACTIVE_STATUSES", "ALLOWED_COMBINATIONS",
           "AUDIT_KEEP_LAST", "AUDIT_KEY", "AUTHORIZATION_KEY",
           "AUTHORIZATION_CONTRACT_VERSION",
           "COMBINATION_ACTIVE", "COMBINATION_NOT_ALLOWED",
           "COST_CAP_EXCEEDED", "DEFAULT_MAX_ACTIVE_RUNS",
           "DEFAULT_MAX_SUCCESSFUL_RUNS", "NOT_AUTHORIZED", "QUOTA_EXHAUSTED",
           "SUCCESS_STATUSES", "TRIAL_SETUP_KEY", "LEGACY_TRIAL_AUTHORIZATION",
           "INCOMPATIBLE_TRIAL_AUTHORIZATION",
           "allowed_combinations", "has_only_legacy_combinations",
           "check_incompatible_authorization",
           "authorization_record", "capacities_of", "check_cost_cap",
           "check_create_allowed", "check_run_allowed", "combination_of",
           "is_authorized", "lock_workspace", "matching_combination",
           "max_active_runs", "max_successful_runs", "assignment_cost_cap_usd", "cap_scope",
           "run_authorization_id", "AUTHORIZATION_MISMATCH",
           "record_run_audit", "require_create_allowed", "require_run_allowed",
           "run_audit", "successful_runs", "trial_runs", "trial_setup",
           "workspace_cost_usd"]
