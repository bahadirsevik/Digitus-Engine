"""SEO uretiminde confirmed profil baglami (plan_marka_profili_sadakati.md P1.1).

SORUN. SEO prompt'undaki "Sektor" ve "Hedef Kitle" alanlari confirmed
profilden DEGIL, request veya `Keyword` tablosunun LEGACY kolonlarindan
geliyordu. O kolonlar hicbir CSV'den doldurulmuyor (`models.py` "LEGACY",
`google_ads_parser` hic atamiyor) ve bulk yolu `target_market`'i hic
gondermiyordu → prompt pratikte "Genel / Genel" ile kosuyordu. `services` de
marka baglami blogunda hic yoktu (ADS ve SOCIAL bu alani product_facts
uzerinden goruyordu).

FALLBACK ZINCIRI: acik request > CONFIRMED profil > Keyword LEGACY > "Genel".

Testler `_generate_raw_content`'i yakalayip prompt girdilerini olcer —
UCRETLI CAGRI YOK.
"""
from __future__ import annotations

import pytest

from app.generators.seo_geo.seo_geo_generator import SEOGeoGenerator
from app.schemas.seo_geo import SEOGEOGenerateRequest

PROFILE = {
    "company_name": "Vepa",
    "sector": "Kişisel bakım",
    "target_audience": "Doğal ürün kullanan kadınlar",
    "products": ["Doğal saç fırçası", "Diş fırçası"],
    "services": ["Saç bakım danışmanlığı", "Diş beyazlatma randevusu"],
    "use_cases": ["Saç açma"],
    "problems_solved": ["Saç dökülmesi"],
    "anchor_texts": ["Doğal saç fırçası"],
}


class _Captured(Exception):
    """Prompt girdileri yakalandi — akisi burada durdur (AI'a hic gitme)."""

    def __init__(self, kwargs):
        super().__init__("captured")
        self.kwargs = kwargs


@pytest.fixture
def capture_prompt(monkeypatch):
    def _fake(self, **kwargs):
        raise _Captured(kwargs)

    monkeypatch.setattr(SEOGeoGenerator, "_generate_raw_content", _fake)


def _run(db_session, keyword_id, scoring_run_id, **request_kwargs):
    request = SEOGEOGenerateRequest(keyword_id=keyword_id, **request_kwargs)
    generator = SEOGeoGenerator(db_session, None)
    with pytest.raises(_Captured) as exc_info:
        generator.generate_content(request, scoring_run_id=scoring_run_id)
    return exc_info.value.kwargs


@pytest.fixture
def seo_setup(db_session, make_workspace, make_keyword, make_scoring_run):
    """Confirmed profil + LEGACY kolonlari DOLU bir keyword."""

    def _make(*, status="confirmed", profile_data=PROFILE, deleted=False,
              legacy_sector="LEGACY SEKTOR", legacy_market="LEGACY PAZAR",
              keyword_text="saç fırçası önerileri"):
        ws_kwargs = {}
        if deleted:
            from datetime import datetime, timezone

            ws_kwargs["deleted_at"] = datetime.now(timezone.utc)
        workspace = make_workspace(
            name="SEO ctx",
            status=status,
            profile_data=dict(profile_data) if profile_data else None,
            **ws_kwargs,
        )
        run = make_scoring_run(brand_profile_id=workspace.id)
        keyword = make_keyword(
            keyword_text,
            brand_profile_id=workspace.id,
            sector=legacy_sector,
            target_market=legacy_market,
        )
        return workspace, run, keyword

    return _make


# ── Fallback zinciri ────────────────────────────────────────────────────────


def test_confirmed_profile_supplies_sector_and_audience(
    db_session, seo_setup, capture_prompt
):
    """Request yoksa CONFIRMED profil kullanilir (legacy kolon DEGIL)."""
    _, run, kw = seo_setup()

    captured = _run(db_session, kw.id, run.id)

    assert captured["sector"] == "Kişisel bakım"
    assert captured["target_market"] == "Doğal ürün kullanan kadınlar"


def test_explicit_request_overrides_confirmed_profile(
    db_session, seo_setup, capture_prompt
):
    """Acik request bir OVERRIDE'dir; profili ezmeye devam eder."""
    _, run, kw = seo_setup()

    captured = _run(
        db_session, kw.id, run.id,
        sector="Kullanıcının seçtiği sektör",
        target_market="Kullanıcının seçtiği pazar",
    )

    assert captured["sector"] == "Kullanıcının seçtiği sektör"
    assert captured["target_market"] == "Kullanıcının seçtiği pazar"


def test_legacy_columns_used_when_no_confirmed_profile(
    db_session, seo_setup, capture_prompt
):
    """Profil DRAFT ise baglam saglamaz → zincir legacy kolona duser."""
    _, run, kw = seo_setup(status="draft")

    captured = _run(db_session, kw.id, run.id)

    assert captured["sector"] == "LEGACY SEKTOR"
    assert captured["target_market"] == "LEGACY PAZAR"
    assert captured["brand_context"] == ""


def test_archived_workspace_supplies_no_context(
    db_session, seo_setup, capture_prompt
):
    _, run, kw = seo_setup(deleted=True)

    captured = _run(db_session, kw.id, run.id)

    assert captured["sector"] == "LEGACY SEKTOR"
    assert captured["brand_context"] == ""


def test_falls_back_to_genel_when_no_source(db_session, seo_setup, capture_prompt):
    """Hicbir kaynak yoksa 'Genel' — regresyonun kilidi."""
    _, run, kw = seo_setup(
        status="draft", legacy_sector=None, legacy_market=None,
    )

    captured = _run(db_session, kw.id, run.id)

    assert captured["sector"] == "Genel"
    assert captured["target_market"] == "Genel"


def test_profile_without_sector_falls_through_to_legacy(
    db_session, seo_setup, capture_prompt
):
    """Confirmed profil VAR ama sektor bos → legacy kolona duser."""
    _, run, kw = seo_setup(
        profile_data={**PROFILE, "sector": "", "target_audience": ""},
    )

    captured = _run(db_session, kw.id, run.id)

    assert captured["sector"] == "LEGACY SEKTOR"
    assert captured["target_market"] == "LEGACY PAZAR"


# ── services marka bağlamı ──────────────────────────────────────────────────


def test_related_service_enters_brand_context(db_session, seo_setup, capture_prompt):
    """Keyword'le ortusen hizmet marka baglamina girer."""
    _, run, kw = seo_setup(keyword_text="saç fırçası önerileri")

    ctx = _run(db_session, kw.id, run.id)["brand_context"]

    assert "Hizmetler:" in ctx
    assert "Saç bakım danışmanlığı" in ctx


def test_unrelated_service_is_filtered_out(db_session, seo_setup, capture_prompt):
    """Konu disi hizmet keyword filtresiyle DISARIDA kalir (sizinti yok).

    NOT: `filter_brand_items_by_keyword` stem ORTUSMESI arar, tam esitlik
    degil — "saç fırçası önerileri" icin "Diş fırçası" ORTAK 'firca' stem'i
    yuzunden GECER. Bu urun tarafinin MEVCUT davranisidir ve P1.1 kapsaminda
    degistirilmedi; hizmetler ayni kurala baglandi. Burada olculen: ortak
    stem'i OLMAYAN hizmetin girmedigidir.
    """
    _, run, kw = seo_setup(keyword_text="saç fırçası önerileri")

    ctx = _run(db_session, kw.id, run.id)["brand_context"]

    assert "Saç bakım danışmanlığı" in ctx
    assert "Diş beyazlatma randevusu" not in ctx


# ── Bulk yolu ───────────────────────────────────────────────────────────────


def test_bulk_request_does_not_promote_legacy_sector_as_override():
    """Bulk yolu legacy `kw.sector`'u request gibi ONE GECIRMEZ.

    Gecirseydi generator onu ACIK KULLANICI OVERRIDE'i sanar ve confirmed
    profilin sektorunun onune koyardı — P1.1'in tam tersi.
    """
    import inspect

    from app.tasks import generation_tasks

    source = inspect.getsource(generation_tasks.generate_seo_chunk_task)
    assert "SEOGEOGenerateRequest(" in source
    assert "sector=getattr(kw" not in source
    assert "sector=kw.sector" not in source
