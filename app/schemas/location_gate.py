# -*- coding: utf-8 -*-
"""Lokasyon onizleme token alanlari + run denetim (audit) semalari.

Faz 6a (plan_v3_lokasyon_filtresi.md SS5.7, SS5.6): create/execute
staleness kapilari ve salt-okunur per-keyword audit endpoint'i icin
request/response modelleri.

Neden AYRI modul (repo koku CLAUDE.md + gorev talimati): `app/api/v1/scoring.py`
ve `app/schemas/scoring.py` baska bir gelistiricinin commit edilmemis "V3 skor
ekrani" hunk'larini tasiyor (import blogu, get_scoring_results,
export_scoring_xlsx / KeywordScoreResponse-ScoringResultsResponse). Yeni
semalari o dosyalara eklemek `git add -p` ile hunk bazinda ayri stage etmeyi
imkansizlastirirdi. `app/schemas/brand_profile.py` temiz olsa da lokasyon
token semasi hem `scoring.py` (create_scoring_run) hem `brand_profile.py`
(location-preview) router'inda kullanilir; coklu-tuketicili, cakismasiz bir
modul en dusuk surtunmeli secenektir.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from app.schemas.scoring import ScoringRunCreate


class LocationPreviewTokenFields(BaseModel):
    """`exclude_all`/`focus_only` modunda run olusturma istegine tasinan
    onizleme token'i (plan SS5.7). `mode=none` bu alanlari GEREKTIRMEZ —
    hicbiri gonderilmezse `None` kalir ve create/execute hicbir yeni kontrol
    calistirmaz (mevcut `none` davranisi TAMAMEN korunur).
    """
    location_universe_fingerprint: Optional[str] = None
    location_policy_fingerprint: Optional[str] = None
    # Faz 4 onizleme yanitindaki `is_saved_policy` aynen geri gonderilir.
    # DRAFT'tan (profil duzenleme ekrani, henuz kaydedilmemis degerler)
    # uretilen bir onizlemenin token'i `False`/`None` tasir ve run
    # yetkilendirmesinde ACIKCA reddedilir — degerler tesaduefen kayitli
    # policy ile ayni olsa BILE (plan SS5.7: "yalniz analiz baslatma
    # modalindaki ... token'i run yetkilendirmesinde kullanilir"). Ayrica
    # create/execute BAGIMSIZ OLARAK kayitli profile karsi fingerprint'leri
    # yeniden hesaplar; bu alan client tarafindan yanlis bildirilse bile
    # o karsilastirma tek basina guvenligi saglar (bkz. scoring.py).
    location_preview_is_saved_policy: Optional[bool] = None


class ScoringRunCreateWithLocationGate(ScoringRunCreate, LocationPreviewTokenFields):
    """`POST /scoring/runs` istek govdesi — lokasyon token alanlariyla
    genisletilmis `ScoringRunCreate`.

    `app/schemas/scoring.py::ScoringRunCreate` dirty oldugu icin token
    alanlari BURADA mixin olarak eklenir; JSON govde sekli DEGISMEZ (tek
    model, FastAPI embed etmez) — eski istemciler token alanlarini hic
    gondermeden calismaya devam eder (`mode=none` icin zaten gerekmiyor).
    """


class LocationAuditRow(BaseModel):
    """Tek keyword icin MUHURLU lokasyon karari (plan SS5.6)."""
    keyword_id: int
    keyword: str
    is_kept: bool
    reason_code: Optional[str] = None
    matched_city: Optional[str] = None
    matched_exempt_term: Optional[str] = None


class LocationAuditResponse(BaseModel):
    """Run'in MUHURLU baglamiyla salt-okunur lokasyon denetimi.

    Yalniz `load_universe` (dondurulmus evren) + `manifest_location_policy`
    (muhurlu politika) okunur; canli profil veya draft onizleme degeri
    HICBIR ZAMAN okunmaz (plan SS5.6/SS5.7 — audit tekrarlanabilir olmali).
    """
    scoring_run_id: int
    mode: str
    city_lexicon_version: str
    total_rows: int
    kept_count: int
    excluded_count: int
    limit: int
    offset: int
    rows: List[LocationAuditRow]
