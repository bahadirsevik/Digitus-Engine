"""
Pydantic schemas for Brand Profile & Keyword Relevance.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict


# ==================== REQUEST SCHEMAS ====================

class ProfileAnalyzeRequest(BaseModel):
    """Request to trigger site profile analysis."""
    company_url: str = Field(..., description="Firma web sitesi URL'si")
    competitor_urls: Optional[List[str]] = Field(
        None,
        max_length=3,
        description="Rakip site URL'leri (max 3)"
    )


class ProfileConfirmRequest(BaseModel):
    """Request to confirm/edit a draft profile."""
    profile_data: Optional[Dict[str, Any]] = Field(
        None,
        description="Düzeltilmiş profil verisi (None ise mevcut draft onaylanır)"
    )


# ==================== RESPONSE SCHEMAS ====================

class ProfilePageInfo(BaseModel):
    """Crawled page summary."""
    url: str
    title: str
    status: int


class ProfileDataSchema(BaseModel):
    """Brand profile data structure."""
    company_name: Optional[str] = None
    sector: Optional[str] = None
    brand_summary: Optional[str] = None
    products: List[str] = []
    services: List[str] = []
    target_audience: Optional[str] = None
    use_cases: List[str] = []
    problems_solved: List[str] = []
    brand_terms: List[str] = []
    # Korunacak temalar (Faz B): dışlama temasıyla çakışan sorguyu koruyan
    # kanonik alan. Eski profillerde yok → boş liste (geriye uyumlu).
    protected_themes: List[str] = []
    exclude_themes: List[str] = []
    anchor_texts: List[str] = []
    # Lokasyon politikası (plan_v3_lokasyon_filtresi.md §4). Alanı olmayan
    # eski profiller `none` + boş listeler olarak okunur → davranış değişmez.
    # `focus_cities` anchor üretimine GİRMEZ (anchor_builder kapalı beyaz
    # liste kullanır); konu ilgisi ile coğrafi tercih karıştırılmaz.
    location_filter_mode: str = "none"
    focus_cities: List[str] = []
    location_exempt_terms: List[str] = []


class ValidationDataSchema(BaseModel):
    """Competitor validation result."""
    consistency_score: Optional[float] = None
    competitors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    profile_adjustments: List[str] = []


class BrandProfileResponse(BaseModel):
    """Full brand profile response."""
    id: int
    scoring_run_id: Optional[int] = None
    company_url: str
    competitor_urls: Optional[List[str]] = None
    status: str
    profile_data: Optional[Dict[str, Any]] = None
    validation_data: Optional[Dict[str, Any]] = None
    source_pages: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KeywordRelevanceResponse(BaseModel):
    """Single keyword relevance result."""
    keyword_id: int
    keyword: str
    relevance_score: float
    matched_anchor: Optional[str] = None
    method: str = "embedding"


class WorkspaceCreateRequest(BaseModel):
    """Yeni marka çalışması oluşturma isteği."""
    name: Optional[str] = Field(None, max_length=200, description="Çalışma adı (boşsa domain'den türetilir)")
    company_url: str = Field(..., description="Şirket URL")
    competitor_urls: Optional[List[str]] = Field(None, max_length=3, description="Rakip URL'leri")
    preliminary_info: Optional[str] = Field(None, description="Marka vizyonu, hedef kitlesi, benzersiz satış noktaları")
    must_have_info: Optional[str] = Field(None, description="Profili mutlaka sekillendirmesi gereken bilgiler")
    excluded_info: Optional[str] = Field(None, description="Markada mutlaka olmamasi gereken konular")
    default_geo_target_id: Optional[str] = Field("2792", description="Geo target ID")
    default_language_id: Optional[str] = Field("1037", description="Language ID")
    flow_version: Literal["legacy", "profile_first"] = Field(
        "legacy",
        description="Onboarding akışı: legacy (keyword-önce) | profile_first (profil-önce sihirbaz)",
    )


class CompetitorDecision(BaseModel):
    """Rakipler kartındaki URL-bazlı kullanıcı kararı (plan v13).

    decision=block → term bu URL'nin desteğiyle engellenir;
    decision=not_competitor → yalnız bu URL'nin desteği serbest bırakılır
    (global reject yazılmaz — manuel onaylı aynı terim etkilenmez).
    """
    url: str = Field(..., max_length=500)
    term: str = Field("", max_length=200)
    decision: Literal["block", "not_competitor"]


class WorkspaceProfileApproveRequest(BaseModel):
    """Profil-önce akışta 5-kart profil onayı / adım-4 inline düzeltme isteği."""
    profile_data: Optional[Dict[str, Any]] = Field(
        None, description="Kartlarda düzenlenen profil verisi (None ise mevcut hali onaylanır)"
    )
    competitor_urls: Optional[List[str]] = Field(None, max_length=3, description="Rakip URL'leri (max 3)")
    competitor_decisions: Optional[List[CompetitorDecision]] = Field(
        None,
        max_length=3,
        description=(
            "Rakipler kartı kararları (TAM REPLACEMENT: verilirse geçerli her "
            "URL için tam bir karar içermeli)"
        ),
    )
    must_have_info: Optional[str] = Field(
        None, description="Mutlaka olması gereken konular (preliminary_info kolonuna yazılır)"
    )
    excluded_info: Optional[str] = Field(None, description="Mutlaka olmaması gereken konular")
    default_geo_target_id: Optional[str] = Field(None, description="Geo target ID")
    default_language_id: Optional[str] = Field(None, description="Language ID")
    rerun_keywords: bool = Field(
        True,
        description=(
            "true: keyword önerisi (yeniden) üretilir; false: profil+anchor "
            "kaydedilir ve ilk profil onayında rakip inceleme aşamasına geçilir"
        ),
    )


class AnchorGroupResponse(BaseModel):
    """Marka odakları (anchor) grubu — kaynak profil alanına göre."""
    source_field: str
    label: str
    kept_items: List[str] = []
    excluded_items: List[str] = []
    anchor: str


class AnchorPreviewRequest(BaseModel):
    """Anchor önizleme isteği (opsiyonel profil override; anchor_texts yok sayılır)."""
    profile_data: Optional[Dict[str, Any]] = None


class AnchorPreviewResponse(BaseModel):
    """Gruplu anchor önizlemesi."""
    groups: List[AnchorGroupResponse] = []


class ChannelSeedInput(BaseModel):
    """Tek bir seed keyword icin kullanici kanal tercihi (plan9 §5).

    `not_suitable` TEKIL secenektir: kanallarla birlikte gonderilemez.
    Dogrulama `app/core/channel_seed.py` icinde tek noktadadir.
    """
    keyword: str = Field(..., min_length=1, max_length=500)
    channels: List[str] = Field(default_factory=list, max_length=3)
    not_suitable: bool = False
    replaced_original_keyword: Optional[str] = Field(None, max_length=500)


class ChannelSeedResponse(BaseModel):
    """Kayitli kanal seed'i."""
    model_config = ConfigDict(from_attributes=True)

    keyword: str
    canonical_keyword: str
    keyword_id: Optional[int] = None
    channels: List[str] = Field(default_factory=list)
    not_suitable: bool = False
    replaced_original_keyword: Optional[str] = None
    labelled_by: Optional[str] = None
    labelled_at: Optional[datetime] = None
    source: Optional[str] = None


class WorkspaceKeywordApproveRequest(BaseModel):
    """Kullanici tarafindan onaylanan veya duzenlenen seed keyword listesi.

    `channel_seeds` OPSIYONELDIR: gonderilmezse bugunku davranis BIREBIR
    korunur (plan9 §10 geriye uyumluluk). Gonderilirse kanal tercihleri
    ayri ve yapisal tabloya yazilir; `keywords` alani duz metin listesi
    olarak calismaya devam eder.
    """
    keywords: List[str] = Field(..., min_length=1, max_length=20)
    channel_seeds: Optional[List[ChannelSeedInput]] = Field(
        None, max_length=20,
        description=("Kanal tercihleri. Verilmezse mevcut davranis degismez."))


class WorkspaceKeywordRefreshRequest(BaseModel):
    """Workspace keyword metriklerini Google Ads ile yenileme isteği."""
    customer_id: Optional[str] = Field(None, description="Google Ads customer ID")
    max_results: int = Field(300, ge=1, le=5000)
    min_volume: int = Field(0, ge=0)
    include_new_ideas: bool = Field(False, description="Google Ads'in döndürdüğü yeni fikirleri de ekle")


class WorkspaceKeywordRefreshResponse(BaseModel):
    """Workspace keyword refresh diff raporu."""
    workspace_id: int
    refreshed: int
    unchanged: int
    added: int
    removed: int
    total_after: int


class WorkspaceResponse(BaseModel):
    """Marka çalışması detay yanıtı."""
    id: int
    name: Optional[str] = None
    company_url: str
    competitor_urls: Optional[List[str]] = None
    # Plan v13: kalıcı URL kararları — Rakipler kartı yeniden açılışta
    # varsayılanı BUNDAN türetir ("Rakip değil" block'a geri dönmez)
    competitor_url_decisions: Optional[Dict[str, Any]] = None
    status: str
    onboarding_flow: str = "legacy"
    profile_approved_at: Optional[datetime] = None
    profile_data: Optional[Dict[str, Any]] = None
    validation_data: Optional[Dict[str, Any]] = None
    suggested_keywords: Optional[List[str]] = None
    preliminary_info: Optional[str] = None
    excluded_info: Optional[str] = None
    deleted_at: Optional[datetime] = None
    default_geo_target_id: Optional[str] = None
    default_language_id: Optional[str] = None
    is_system_default: bool = False
    scoring_run_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)





class WorkspaceListResponse(BaseModel):
    """Çalışma listesi öğesi (kart görünümü için)."""
    id: int
    name: Optional[str] = None
    company_url: str
    status: str
    onboarding_flow: str = "legacy"
    profile_approved_at: Optional[datetime] = None
    validation_data: Optional[Dict[str, Any]] = None
    # Plan v13 sözleşmesi: Rakipler kartı liste satırından açılır — URL'ler ve
    # kalıcı kararlar TAŞINMAZSA kart boş başlar ve tam-replacement kaydı
    # mevcut kararları SİLER (Codex blocker'ı). Bu iki alan zorunlu yüktür.
    competitor_urls: Optional[List[str]] = None
    competitor_url_decisions: Optional[Dict[str, Any]] = None
    profile_data: Optional[Dict[str, Any]] = None
    suggested_keywords: Optional[List[str]] = None
    excluded_info: Optional[str] = None
    default_geo_target_id: Optional[str] = None
    default_language_id: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime
    run_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class LocationFilterPreviewRequest(BaseModel):
    """Draft lokasyon filtresi ayarlarını değerlendiren salt-okunur önizleme
    isteği (plan_v3_lokasyon_filtresi.md §5.7). Hiçbir profil/keyword/run/
    havuz kaydı YAZILMAZ; AI çağrılmaz.

    Üç lokasyon alanı KASITLI gevşek tipli (`Any`): tek doğrulama kaynağı
    `app.api.v1.brand_profile._apply_location_policy`dir (Faz 2) — pydantic
    erken 422 üretirse o fonksiyonun tipli 400 hataları (örn.
    `INVALID_LOCATION_LIST`) hiç tetiklenmez. Aynı desen `ProfileConfirmRequest`
    / `WorkspaceProfileApproveRequest`'in `profile_data: Dict[str, Any]`
    alanında zaten kullanılıyor.

    Alan hiç GÖNDERİLMEZSE workspace'in kayıtlı değeri kullanılır (kısmi
    önizleme mümkün olsun diye); `null` gönderilmesi Faz 2 ile AYNI şekilde
    reddedilir (`location_filter_mode` için sessizce `none`'a düşmez).
    """
    location_filter_mode: Optional[Any] = None
    focus_cities: Optional[Any] = None
    location_exempt_terms: Optional[Any] = None

    # Onizlenecek keyword evreni — henüz bir ScoringRun yokken bile motorun
    # GERÇEKTEN kullanacağı seçim semantiğini uygulamak için (ScoringRunCreate
    # ile AYNI alan adları/anlamı). Verilmezse "all" (workspace'in tüm aktif
    # keyword'leri) kullanılır.
    keyword_selection_mode: Literal["all", "top_n", "specific"] = Field(
        "all", description="Motorun kullanacağı keyword seçim modu")
    keyword_limit: Optional[int] = Field(
        None, gt=0, le=1000, description="top_n modu için limit")
    selected_keyword_ids: Optional[List[int]] = Field(
        None, max_length=500, description="specific modu için keyword ID'leri")
    keyword_source_filter: Optional[Literal["csv", "google_ads_api"]] = None


class LocationPreviewCityCount(BaseModel):
    """Şehir + neden bazında eleme sayısı.

    `LOCATION_CITY_FILTER` (exclude_all) ve `LOCATION_NON_FOCUS_CITY`
    (focus_only) AYRI satırlar olarak sayılır — aynı şehir iki nedenle
    görünmez (mod her zaman tekildir), ama alan adı bu ayrımı açık tutar.
    """
    city: str
    reason: str
    count: int


class LocationPreviewSample(BaseModel):
    """Lokasyon nedeniyle elenecek örnek keyword."""
    keyword: str
    matched_city: Optional[str] = None


class LocationPreviewExemptSample(BaseModel):
    """Muafiyet sayesinde korunan örnek keyword."""
    keyword: str
    matched_exempt_term: str


class LocationFilterPreviewResponse(BaseModel):
    """Lokasyon filtresi önizleme yanıtı.

    `has_keywords=False` iken diğer sayısal alanlar 0'dır ama bu GERÇEK bir
    "hiçbir şey elenmiyor" sonucu DEĞİLDİR — önizlenecek keyword yok demektir
    (boş workspace veya seçim/A13 sonrası uygun satır kalmaması). Arayüz bu
    durumu sıfır sonuçtan AYRI göstermelidir.
    """
    has_keywords: bool
    mode: str
    evaluated_count: int
    kept_count: int
    excluded_count: int
    excluded_by_reason: Dict[str, int] = {}
    excluded_by_city: List[LocationPreviewCityCount] = []
    sample_excluded: List[LocationPreviewSample] = []
    sample_exempted: List[LocationPreviewExemptSample] = []
    city_lexicon_version: str
    city_lexicon_sha256: str
    # Faz 6a: create/execute'a taşınıp AI çağrısından önce yeniden
    # hesaplanarak karşılaştırılan çift — LOCATION_PREVIEW_STALE kapısının
    # girdisi (bkz. `app/schemas/location_gate.py`,
    # `app/api/v1/scoring.py::create_scoring_run`/`execute_scoring`).
    universe_fingerprint: str
    location_policy_fingerprint: str
    # Faz 6a — TOKEN GÜVEN KURALI: bu alan `True` iken istek üç draft
    # lokasyon alanından (`location_filter_mode`/`focus_cities`/
    # `location_exempt_terms`) HİÇBİRİNİ göndermedi; yanıt tamamen
    # workspace'in KAYITLI `profile_data`'sına göre üretildi. Profil
    # düzenleme ekranı bu alanları draft değerlerle GÖNDERİR → `False` döner
    # ve o token'ın run yetkilendirmesinde kullanılması ACIKÇA reddedilir
    # (değerler tesadüfen kayıtlı policy ile aynı olsa bile — plan §5.7).
    is_saved_policy: bool


class RelevanceComputeResponse(BaseModel):
    """Response after computing relevance scores."""
    scoring_run_id: int
    total_keywords: int
    computed: int
    failed: int
    average_relevance: float
