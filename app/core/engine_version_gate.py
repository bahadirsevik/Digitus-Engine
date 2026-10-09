"""Eski motor (v2 / v2_1) run'ları için salt-okunur kapısı.

Ürün kararı: aktif TEK motor v3'tür. Yeni v2/v2_1 run'ı oluşturmak zaten
400 LEGACY_ENGINE_RETIRED ile reddedilir (app/api/v1/scoring.py). Bu modül
MEVCUT eski run'lar için ikinci yarıyı kapatır: eski bir run üzerinden
yeni üretim (skorlama, relevance/embedding, kanal ataması, screening,
SEO/ADS/SOCIAL içerik üretimi, social brief akışı) BAŞLATILAMAZ.
Okuma, geçmiş, listeleme ve export değişmeden çalışır.

Tek kaynak: hangi run'ın eski sayıldığı `is_legacy_run` ile belirlenir.

- API sınırı: `require_non_legacy_run(run)` -> HTTP 409
  {"code": "LEGACY_RUN_READ_ONLY", "message": ..., "algorithm_version": ...}
  Workspace doğrulamasından HEMEN SONRA, herhangi bir AI / embedding /
  screening / Google Ads çağrısından ve Celery dispatch'inden ÖNCE çağrılır.
- Task / dispatcher sınırı: `legacy_run_by_id(db, run_id)` eski run'ı
  döndürür (değilse None); çağıran task kendi mevcut fail-closed kalıbıyla
  (TaskResult / attempt / set failed) tipli sebep yazar ve AI çağırmadan
  döner. Eski run'lar v3'e YÖNLENDİRİLMEZ, sessiz fallback YOKTUR.

NULL/boş semantiği: `algorithm_version` kolonu NOT NULL'dur (v2.1
migration'ı mevcut satırlara 'v2' yazdı; 20260922 migration'ı yalnız
server_default'u 'v3' yaptı, tarihsel değerlere dokunmadı). Kod tabanının
geri kalanı eksik/boş değeri her yerde `or "v2"` ile v2 kabul eder; bu kapı
aynı sözleşmeyi izler ve fail-closed çalışır: yalnız açıkça "v3" olan run
üretime izinlidir. v2, v2_1, None/boş ve tanınmayan her değer salt-okunurdur.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

LEGACY_ALGORITHM_VERSIONS = ("v2", "v2_1")
ACTIVE_ALGORITHM_VERSION = "v3"

LEGACY_RUN_READ_ONLY = "LEGACY_RUN_READ_ONLY"
# Sosyal attempt reason_code sözleşmesi küçük harflidir (ALLOWED_*_REASONS)
LEGACY_RUN_READ_ONLY_REASON = "legacy_run_read_only"


class LegacyRunReadOnlyError(RuntimeError):
    """Core/dispatcher katmanında eski run reddi (HTTP'den bağımsız)."""

    code = LEGACY_RUN_READ_ONLY

    def __init__(self, algorithm_version: Optional[str]):
        self.algorithm_version = algorithm_version or "v2"
        self.message = legacy_run_message(self.algorithm_version)
        super().__init__(f"{LEGACY_RUN_READ_ONLY}: {self.message}")


def run_algorithm_version(run: Any) -> str:
    """Run'ın etkin algoritma sürümü (kod tabanı sözleşmesi: boş -> 'v2')."""
    return (getattr(run, "algorithm_version", None) or "v2")


def is_legacy_run(run: Any) -> bool:
    """Run yeni üretim için KAPALI mı? (yalnız 'v3' açıktır — fail-closed)."""
    return run_algorithm_version(run) != ACTIVE_ALGORITHM_VERSION


RELEVANCE_NOT_USED_BY_V3 = "RELEVANCE_NOT_USED_BY_V3"


def is_v3_run(run: Any) -> bool:
    """Run aktif (v3) motorla mı üretiliyor? (`is_legacy_run`'ın tersi)."""
    return not is_legacy_run(run)


def require_relevance_not_v3(run: Any) -> None:
    """Embedding relevance'ı v3 motorunda KULLANILMAZ (kendi seo_rel/social_rel
    AI aşamaları vardır): v3 run'ında hesaplama tipli 409 ile reddedilir.
    Embedding çağrısından ve herhangi bir DB yazımından ÖNCE çağrılır."""
    if run is not None and is_v3_run(run):
        raise HTTPException(status_code=409, detail={
            "code": RELEVANCE_NOT_USED_BY_V3,
            "message": (
                "V3 motoru embedding ilgi skorunu kullanmaz; bu analiz için "
                "ilgi skoru hesaplanmaz."
            ),
            "algorithm_version": run_algorithm_version(run),
        })


def legacy_run_message(algorithm_version: Optional[str]) -> str:
    return (
        f"Bu analiz eski motorla ({algorithm_version or 'v2'}) üretilmiştir ve "
        "yalnız okunabilir. Yeni üretim için V3 analizi oluşturun."
    )


def legacy_run_detail(run: Any) -> Dict[str, Any]:
    version = run_algorithm_version(run)
    return {
        "code": LEGACY_RUN_READ_ONLY,
        "message": legacy_run_message(version),
        "algorithm_version": version,
    }


def require_non_legacy_run(run: Any) -> None:
    """API sınırı: eski run'da yeni üretim tipli 409 ile reddedilir."""
    if run is not None and is_legacy_run(run):
        raise HTTPException(status_code=409, detail=legacy_run_detail(run))


def legacy_run_by_id(db, scoring_run_id: Optional[int]):
    """Task/dispatcher sınırı: run eskiyse satırı döndürür, değilse None.

    Run bulunamazsa None döner — varlık hataları çağıranın mevcut
    sözleşmesinde kalır (bu kapı yeni bir 404 yolu açmaz).
    """
    if scoring_run_id is None:
        return None
    from app.database.models import ScoringRun

    run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    if run is not None and is_legacy_run(run):
        return run
    return None


def require_non_legacy_run_in_workspace(
    db, scoring_run_id: Optional[int], brand_profile_id: Optional[int]
) -> None:
    """Run kimliği yalnız istekte/brief'te taşınan uçlar için API kapısı.

    Yalnız verilen workspace'e ait bir run için 409 döner; bulunamayan veya
    başka workspace'e ait run'da hiçbir şey yapmaz (mevcut 404 sözleşmesi
    ve varlık-sızıntısı koruması aynen kalır).
    """
    if scoring_run_id is None or brand_profile_id is None:
        return
    from app.database.models import BrandProfile, ScoringRun

    # Workspace doğrulamasıyla AYNI koşul: arşivlenmiş/yabancı workspace'te
    # kapı sessiz kalır, mevcut 404 sözleşmesi çalışır
    run = (
        db.query(ScoringRun)
        .join(BrandProfile, BrandProfile.id == ScoringRun.brand_profile_id)
        .filter(ScoringRun.id == scoring_run_id,
                ScoringRun.brand_profile_id == brand_profile_id,
                BrandProfile.deleted_at.is_(None))
        .first()
    )
    require_non_legacy_run(run)


def require_non_legacy_brief(
    db, brief_id: Optional[int], brand_profile_id: Optional[int]
) -> None:
    """Social brief uçları: brief'in bağlı olduğu run eskiyse 409."""
    if brief_id is None:
        return
    from app.database.models import SocialBrief

    row = (db.query(SocialBrief.scoring_run_id)
           .filter(SocialBrief.id == brief_id)
           .first())
    if row is None:
        return
    require_non_legacy_run_in_workspace(db, row.scoring_run_id, brand_profile_id)
