"""Motor v3 ortak baglami: onayli profil, firma blogu ve evren.

Kaynak sadakati (plan_algoritma_entegrasyonu.md K1):
  * `firm_block` metni `scripts/ads_v3_ai.firm_block` ile BIREBIR ayni
    uretilir; buradaki kopya uretim kodudur, kilit dosyasi degildir.
    Esitlik `tests/unit/test_engine_context.py` parity testiyle kanitlanir.
  * Profil alan eslemesi `scripts/ads_v3_run.load_profile` ile ayni KAPALI
    beyaz listedir; `sold_brands` BILINCLI OLARAK yoktur.
  * Evren `workspace_keywords x keywords`tir; A13 (hacim <= 0) ve A15
    (rekabeti cozulemeyen) satirlar disarida birakilir ve AYRI raporlanir.

Bu modul AI CAGIRMAZ. Tek yazma noktasi `freeze_universe_snapshot`tir
(run evrenini `KeywordScore.metrics_snapshot` olarak dondurur);
diger her sey salt-okumadir.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.models import (
    BrandProfile, Keyword, KeywordScore, ScoringRun, WorkspaceKeyword,
)


class EngineInputError(ValueError):
    """Motor girdisi kilidin varsaydigi sozlesmeye uymuyor — fail-closed."""


class SnapshotMismatchError(EngineInputError):
    """Dondurulmus evren ile yeni secim uyusmuyor.

    Snapshot BIR KEZ yazilir: run'in evreni dondurulduktan sonra secim veya
    metrikler degistiyse eski snapshot SESSIZCE yeniden yazilmaz — kosu durur.
    """


# Profil beyaz listesi — kapali kume (load_profile ile ayni sira ve isimler)
FIRM_PROFILE_FIELDS = (
    "brand_name",
    "sector",
    "brand_summary",
    "target_audience",
    "products",
    "services",
    "protected_themes",
    "exclude_themes",
    "brand_terms",
)


def build_firm_profile(profile: BrandProfile) -> Dict[str, Any]:
    """Onayli workspace profilinden KAPALI beyaz listeli firma sozlugu.

    `scripts/ads_v3_run.load_profile` ile ayni eslemedir: marka adi
    `profile_data.company_name`, yoksa workspace adidir.
    """
    if profile is None:
        raise EngineInputError("workspace profili yok — motor kosamaz")
    data = profile.profile_data if isinstance(profile.profile_data, dict) else {}
    return {
        "brand_name": data.get("company_name") or profile.name,
        "sector": data.get("sector"),
        "brand_summary": data.get("brand_summary"),
        "target_audience": data.get("target_audience"),
        "products": data.get("products"),
        "services": data.get("services"),
        "protected_themes": data.get("protected_themes"),
        "exclude_themes": data.get("exclude_themes"),
        "brand_terms": data.get("brand_terms"),
    }


def firm_block(profile: Dict[str, Any]) -> str:
    """Firma baglam metni — kilitli davranisin uretim kopyasi.

    DIKKAT: bu metin prompt'a birebir girer ve `firm_block_sha256` uzerinden
    freshness otoritesidir (K15). Bicimi degistirmek algoritmayi degistirir.
    """
    parts = [f"Sektor: {profile.get('sector') or '-'}",
             f"Marka: {profile.get('brand_name') or '-'}",
             f"Ozet: {profile.get('brand_summary') or '-'}"]
    if profile.get("target_audience"):
        parts.append(f"Hedef kitle: {profile['target_audience']}")
    for field_name, label in (("products", "Urunler"), ("services", "Hizmetler")):
        values = profile.get(field_name) or []
        if values:
            parts.append(f"{label}: " + ", ".join(map(str, values)))
    for field_name, label in (("protected_themes", "Korunan temalar"),
                              ("exclude_themes", "Disarida birakilan temalar")):
        values = profile.get(field_name) or []
        if values:
            parts.append(f"{label}: " + ", ".join(map(str, values)))
    terms = profile.get("brand_terms") or []
    if terms:
        parts.append("Marka terimleri: " + ", ".join(map(str, terms)))
    return "\n".join(parts)


def firm_block_sha256(block: str) -> str:
    """Freshness otoritesi (K15): relevance'in GERCEK girdisinin hash'i."""
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def load_confirmed_profile(db: Session, workspace_id: int) -> BrandProfile:
    """Yalniz `status == 'confirmed'` profil kabul edilir; degilse fail-closed."""
    profile = (db.query(BrandProfile)
               .filter(BrandProfile.id == workspace_id,
                       BrandProfile.deleted_at.is_(None))
               .first())
    if profile is None:
        raise EngineInputError(f"workspace {workspace_id} bulunamadi")
    if profile.status != "confirmed":
        raise EngineInputError(
            f"workspace {workspace_id} profili onayli degil "
            f"(status={profile.status!r}) — motor kosamaz")
    return profile


@dataclass
class UniverseRow:
    keyword_id: int
    keyword_text: str
    volume: int
    trend_3m: float
    trend_12m: float
    competition: Optional[float]          # None = cozulemedi (A15 adayi)


@dataclass
class Universe:
    """Bir KOSUNUN dondurulmus evreni + disarida birakilanlarin AYRI raporu.

    Evren workspace'e degil RUN'a aittir: run'in keyword secim modu, kaynak
    filtresi ve aktiflik kurallari uygulandiktan sonra dondurulur. Motor bu
    nesneyi YALNIZ snapshot'tan okur; canli `workspace_keywords` tablosu
    dondurmadan SONRA degisse bile kosu etkilenmez.
    """
    scoring_run_id: Optional[int] = None
    workspace_id: Optional[int] = None
    rows: List[UniverseRow] = field(default_factory=list)
    volume_zero: List[int] = field(default_factory=list)          # A13
    competition_unresolved: List[int] = field(default_factory=list)  # A15 adayi
    raw_row_count: int = 0

    def for_channel(self, channel: str) -> List[UniverseRow]:
        """Kanal sozlesmesine gore evren.

        SEO rekabeti cozulemeyen satiri ETIKETTEN BAGIMSIZ disarida birakir
        (A15). ADS ise `competition_to_r` ile null'i 0,5 sayar, bu yuzden
        satiri tutar. SOCIAL rekabet kullanmaz.
        """
        if channel == "SEO":
            return [row for row in self.rows if row.competition is not None]
        return list(self.rows)


SNAPSHOT_MARKER = "engine_v3"


def select_run_keywords(db: Session, run: ScoringRun) -> List[Any]:
    """Run'in keyword secimini uygular — v2 `_select_keywords_with_metrics`
    ile AYNI semantik: yalniz aktif keyword'ler, `keyword_source_filter`
    WorkspaceKeyword.data_source uzerinden, `top_n` hacme gore, `specific`
    ise workspace'e ait olmayan/pasif id'de HATA.
    """
    query = (db.query(Keyword, WorkspaceKeyword)
             .join(WorkspaceKeyword, WorkspaceKeyword.keyword_id == Keyword.id)
             .filter(WorkspaceKeyword.brand_profile_id == run.brand_profile_id)
             .filter(Keyword.is_active.is_(True)))

    if run.keyword_source_filter:
        query = query.filter(
            WorkspaceKeyword.data_source == run.keyword_source_filter)

    if run.keyword_selection_mode == "top_n":
        if not run.keyword_limit:
            raise EngineInputError("top_n modu keyword_limit ister")
        # DIKKAT: `.limit()` uygulanmis bir Query uzerinde tekrar
        # `.order_by()` cagirmak SQLAlchemy'de InvalidRequestError uretir.
        # Secim hacme gore yapilir, teslim sirasi keyword_id'dir.
        rows = (query.order_by(WorkspaceKeyword.monthly_volume.desc(),
                               Keyword.id.asc())
                .limit(run.keyword_limit).all())
        return sorted(rows, key=lambda pair: pair[0].id)

    if run.keyword_selection_mode == "specific":
        if not run.selected_keyword_ids:
            raise EngineInputError("specific modu selected_keyword_ids ister")
        valid = {row[0] for row in
                 query.filter(Keyword.id.in_(run.selected_keyword_ids))
                      .with_entities(Keyword.id).all()}
        invalid = set(run.selected_keyword_ids) - valid
        if invalid:
            raise EngineInputError(
                f"{len(invalid)} keyword bu workspace'e ait degil veya pasif: "
                f"{sorted(invalid)[:5]}")
        query = query.filter(Keyword.id.in_(valid))

    return query.order_by(Keyword.id.asc()).all()


def build_universe_rows(db: Session, run: Any) -> Universe:
    """Run'in satir secimi + uygunlugunu KAYDETMEDEN kurar (A13/A15 dahil).

    `freeze_universe_snapshot`'in eskiden kendi icinde tuttugu "FAZ 1: bellekte
    dogrula" adiminin AYNEN cikarilmis halidir: `select_run_keywords` ile ayni
    secim semantigini uygular, hacim<=0 satirlari A13 olarak `volume_zero`'ya
    dusurur, rekabeti cozulemeyen satirlari `competition_unresolved`'a
    isaretler (ama evrende TUTAR — yalniz SEO kanal sozlesmesi bunlari
    `for_channel("SEO")` ile eler).

    SALT-OKUNUR: DB'ye HICBIR SEY eklemez/flush etmez/degistirmez —
    `run.algorithm_version` de burada KONTROL EDILMEZ (o denetim yalniz
    `freeze_universe_snapshot`'in yazma sozlesmesine aittir; boylece bu
    yardimci surume bagli olmayan salt-okunur cagiranlarca da kullanilabilir).

    Bu fonksiyon TEK satir-secim kaynagidir: hem `freeze_universe_snapshot`
    (asagida, KeywordScore yazar) hem de lokasyon filtresi onizleme endpoint'i
    (`app/api/v1/brand_profile.py::preview_location_filter`, hicbir sey
    yazmaz) AYNI bunu cagirir. Boylece onizlemede sayilan satirlar motorun
    GERCEKTEN kosacagi satirlarla birebir ayni olur — hacim<=0 gibi motora
    hic ulasmayan satirlar "lokasyon nedeniyle elendi" diye YANLIS
    raporlanmaz (plan_v3_lokasyon_filtresi.md §5.7).
    """
    selected = select_run_keywords(db, run)

    universe = Universe(scoring_run_id=getattr(run, "id", None),
                        workspace_id=run.brand_profile_id,
                        raw_row_count=len(selected))
    rows: List[UniverseRow] = []
    for keyword, wk in selected:
        competition = _competition_value(wk.competition_score, keyword.id)
        volume = int(wk.monthly_volume) if wk.monthly_volume is not None else 0
        if volume <= 0:
            universe.volume_zero.append(int(keyword.id))       # A13
            continue
        if competition is None:
            universe.competition_unresolved.append(int(keyword.id))
        rows.append(UniverseRow(
            keyword_id=int(keyword.id),
            keyword_text=keyword.keyword,
            volume=volume,
            trend_3m=_number(wk.trend_3m),
            trend_12m=_number(wk.trend_12m),
            competition=competition,
        ))
    universe.rows = rows
    return universe


def universe_fingerprint(universe: Universe) -> str:
    """Evrenin deterministik kimligi (satir kumesi + metrikler).

    Onizleme (Faz 4) ile gercek kosu (ileride Faz 5) ayni evreni gordugunu
    bu kimlikle kanitlar: id kumesi VEYA herhangi bir satirin hacim/trend/
    rekabet degeri degisirse fingerprint degisir.
    """
    payload = json.dumps(
        [[row.keyword_id, row.keyword_text, row.volume, row.trend_3m,
          row.trend_12m, row.competition]
         for row in sorted(universe.rows, key=lambda r: r.keyword_id)],
        sort_keys=True, ensure_ascii=False, default=float,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_dict(row: UniverseRow) -> Dict[str, Any]:
    """`UniverseRow` -> `KeywordScore.metrics_snapshot` yazim bicimi."""
    return {
        "engine": SNAPSHOT_MARKER,
        "keyword_text": row.keyword_text,
        "volume": row.volume,
        "trend_3m": row.trend_3m,
        "trend_12m": row.trend_12m,
        "competition": row.competition,
    }


def freeze_universe_snapshot(db: Session, run: ScoringRun) -> Universe:
    """Run evrenini DONDURUR: `KeywordScore.metrics_snapshot` yazar.

    Sozlesme:
      * YALNIZ `algorithm_version == "v3"` run'larda calisir (tipli hata).
      * AI cagirmaz. Kanal skorlari NULL birakilir (K14).
      * IKI FAZLI: once butun satirlar BELLEKTE dogrulanir (`build_universe_rows`
        ile — bkz. orada), sonra topluca eklenir. Gec bir satirda dogrulama
        duserse session'da PENDING `KeywordScore` KALMAZ — alakasiz bir commit
        bile sifir snapshot yazar.
      * TEK YAZIM: ilk freeze'den sonra mevcut snapshot'lar ASLA yeniden
        yazilmaz. Ikinci cagri ayni evren + ayni icerikse idempotent doner,
        farkliysa `SnapshotMismatchError` ile fail-closed durur.
    """
    version = (run.algorithm_version or "").strip()
    if version != "v3":
        raise EngineInputError(
            f"freeze_universe_snapshot yalniz motor v3 run'larinda calisir "
            f"(run {run.id} algorithm_version={version!r})")

    # ── FAZ 1: bellekte dogrula (DB'ye HICBIR SEY eklenmez) ──────────
    universe = build_universe_rows(db, run)
    prepared: List[Any] = [(row.keyword_id, _snapshot_dict(row))
                           for row in universe.rows]

    # ── FAZ 2: mevcut snapshot varsa KARSILASTIR, yoksa topluca yaz ──
    existing = {row.keyword_id: row for row in
                db.query(KeywordScore)
                  .filter(KeywordScore.scoring_run_id == run.id).all()}
    if existing:
        _assert_snapshot_unchanged(run, existing, prepared)
        return universe                     # idempotent — yeniden YAZILMAZ

    db.add_all([KeywordScore(scoring_run_id=run.id, keyword_id=kid,
                             metrics_snapshot=snapshot)
                for kid, snapshot in prepared])
    db.flush()
    return universe


def _assert_snapshot_unchanged(run: ScoringRun, existing: Dict[int, Any],
                               prepared: List[Any]) -> None:
    """Ikinci freeze: evren ve icerik BIREBIR ayni degilse fail-closed."""
    prepared_ids = {kid for kid, _ in prepared}
    existing_ids = {int(kid) for kid in existing}
    if prepared_ids != existing_ids:
        added = sorted(prepared_ids - existing_ids)[:5]
        removed = sorted(existing_ids - prepared_ids)[:5]
        raise SnapshotMismatchError(
            f"run {run.id}: dondurulmus evren degismis "
            f"(yeni: {added}, dusen: {removed}) — snapshot yeniden yazilmaz")
    for kid, snapshot in prepared:
        stored = existing[kid].metrics_snapshot
        if _canonical_snapshot(stored) != _canonical_snapshot(snapshot):
            raise SnapshotMismatchError(
                f"run {run.id}: keyword {kid} metrikleri dondurmadan sonra "
                "degismis — snapshot yeniden yazilmaz")


def _canonical_snapshot(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        return json.dumps(None)
    return json.dumps({key: snapshot.get(key) for key in
                       ("engine", "keyword_text", "volume", "trend_3m",
                        "trend_12m", "competition")},
                      sort_keys=True, ensure_ascii=False, default=float)


def load_universe(db: Session, scoring_run_id: int) -> Universe:
    """Motorun TEK evren kaynagi: run'in dondurulmus snapshot'i.

    Canli `workspace_keywords` OKUNMAZ. Snapshot yoksa fail-closed.

    NOT: `volume_zero` (A13) yalniz `freeze_universe_snapshot`in dondurdugu
    nesnede doludur — A13 satirlari snapshot'a hic girmedigi icin buradan
    yeniden uretilemez. O listeye sonradan ihtiyaci olan taraf onu dondurma
    aninda kaydetmelidir.
    """
    rows = (db.query(KeywordScore)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .order_by(KeywordScore.keyword_id.asc())
            .all())
    if not rows:
        raise EngineInputError(
            f"run {scoring_run_id} icin dondurulmus evren yok — once snapshot")

    run = db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
    universe = Universe(scoring_run_id=scoring_run_id,
                        workspace_id=run.brand_profile_id if run else None,
                        raw_row_count=len(rows))
    for row in rows:
        snapshot = row.metrics_snapshot
        if not isinstance(snapshot, dict) or snapshot.get("engine") != SNAPSHOT_MARKER:
            raise EngineInputError(
                f"run {scoring_run_id} keyword {row.keyword_id}: snapshot motor "
                "v3 bicimi degil — bu run motor v3 ile kosulamaz")
        universe.rows.append(_row_from_snapshot(row.keyword_id, snapshot))
        if snapshot.get("competition") is None:
            universe.competition_unresolved.append(int(row.keyword_id))
    return universe


def _row_from_snapshot(keyword_id: Any, snapshot: Dict[str, Any]) -> UniverseRow:
    competition = snapshot.get("competition")
    return UniverseRow(
        keyword_id=int(keyword_id),
        keyword_text=snapshot.get("keyword_text"),
        volume=int(snapshot.get("volume") or 0),
        trend_3m=_number(snapshot.get("trend_3m")),
        trend_12m=_number(snapshot.get("trend_12m")),
        competition=None if competition is None else float(competition),
    )


def _competition_value(raw: Any, keyword_id: Any) -> Optional[float]:
    """Rekabet degeri; olcek disi deger KILIDIN sozlesmesini bozar.

    `competition_to_r` 1'in ustundeki degerde HATA verir (ADS kod haritasi
    6. adim). Sonlu olmayan ve negatif degerler de reddedilir: motor yarim
    sinyal uretmeden, evren yuklemede durur.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        raise EngineInputError(
            f"rekabet sonlu degil: keyword_id={keyword_id} competition={raw!r}")
    if value < 0 or value > 1:
        raise EngineInputError(
            f"rekabet olcegi bozuk: keyword_id={keyword_id} competition={value} "
            "(beklenen aralik 0-1). Bu workspace yeniden import edilmeden "
            "motor kosulamaz.")
    return value


def _number(raw: Any, default: float = 0.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default
