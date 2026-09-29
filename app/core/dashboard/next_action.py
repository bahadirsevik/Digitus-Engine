"""Pure next-action decision function for the dashboard cockpit (P5.1).

`build_next_action()` takes a plain `NextActionContext` dataclass and returns a
`NextAction`. It performs no DB access and no I/O so it is fully unit-testable.

Priority order matches the karar ağacı in plan2.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from app.schemas.dashboard import NextAction


# Plan2: blocking task kuralı.
BLOCKING_TASK_TYPES = frozenset({
    "scoring",
    "relevance",
    "channel_assignment",
    "SEMANTIC_SIMILARITY",  # embedding tasks feed relevance computation
})


@dataclass
class RunContext:
    id: int
    status: str
    skip_relevance: bool = False
    enable_ads: bool = True
    enable_seo: bool = True
    enable_social: bool = True


@dataclass
class ChannelContext:
    pool_count: int = 0
    expected_count: int = 0
    generated_count: int = 0

    @property
    def status(self) -> str:
        if self.pool_count == 0:
            return "empty"
        if self.generated_count == 0:
            return "ready"
        if self.expected_count > 0 and self.generated_count < self.expected_count:
            return "partial"
        return "complete"


@dataclass
class TaskContext:
    task_id: str
    task_type: Optional[str]
    status: str

    @property
    def is_active(self) -> bool:
        return self.status in {"pending", "running"}

    @property
    def is_blocking(self) -> bool:
        return self.is_active and (self.task_type or "") in BLOCKING_TASK_TYPES


@dataclass
class ExportContext:
    status: str
    # Plan v4 tur-6 #1: next_action'in "tam rapor" akisi YALNIZ sections'inda
    # 'all' olan job'lara bakar — ADS-only export tam rapor sanilmaz.
    # NextActionContext.latest_export'a TAM RAPOR job'u konur; kanal-bolumu
    # exportlari bu akisa girmez.
    sections: tuple = ()
    export_id: str = ""


@dataclass
class NextActionContext:
    has_workspace: bool = False
    workspace_status: Optional[str] = None
    profile_ready: bool = False
    keyword_count: int = 0
    active_run: Optional[RunContext] = None
    blocking_task: Optional[TaskContext] = None
    relevance_exists: bool = False
    channels: Dict[str, ChannelContext] = field(default_factory=dict)
    latest_export: Optional[ExportContext] = None

    def channel(self, name: str) -> ChannelContext:
        return self.channels.get(name, ChannelContext())

    @property
    def total_pool(self) -> int:
        return sum(c.pool_count for c in self.channels.values())

    @property
    def total_generated(self) -> int:
        return sum(c.generated_count for c in self.channels.values())

    @property
    def any_pool(self) -> bool:
        return self.total_pool > 0

    @property
    def ready_channels(self) -> list:
        return [name for name, c in self.channels.items() if c.status == "ready"]

    @property
    def partial_channels(self) -> list:
        return [name for name, c in self.channels.items() if c.status == "partial"]

    @property
    def all_complete(self) -> bool:
        active = [c for c in self.channels.values() if c.pool_count > 0]
        if not active:
            return False
        return all(c.status == "complete" for c in active)


def _na(
    key: str,
    label: str,
    path: str,
    severity: str,
    reason: str,
    export_id: str = "",
) -> NextAction:
    return NextAction(
        key=key, label=label, path=path, severity=severity, reason=reason,
        export_id=export_id or None,
    )


def _scores_path(run_id: int) -> str:
    return f"/keywords?view=scores&run_id={run_id}"


def _channel_path(channel: str, run_id: int) -> str:
    mapping = {
        "ads": "/ads",
        "seo": "/seo-geo",
        "social": "/social",
    }
    return f"{mapping.get(channel.lower(), '/seo-geo')}?run_id={run_id}"


def build_next_action(ctx: NextActionContext) -> NextAction:
    """Decide the single most-relevant CTA for the cockpit."""
    if not ctx.has_workspace:
        return _na(
            "create_workspace",
            "Marka Çalışması Oluştur",
            "/brand-profile",
            "primary",
            "Önce bir marka çalışması oluşturmalısın.",
        )

    if ctx.workspace_status == "competitor_review":
        # Profil ONAYLI; bekleyen adım rakip inceleme/keyword üretimi —
        # "profil onaylanmadı" metni bu aşamada yanıltıcı olur.
        return _na(
            "resume_competitor_review",
            "Rakip İncelemesini Tamamla",
            "/brand-profile",
            "warning",
            "Profil onaylandı; rakip inceleme adımı tamamlanmayı bekliyor.",
        )

    if not ctx.profile_ready:
        return _na(
            "confirm_profile",
            "Marka Profilini Onayla",
            "/brand-profile",
            "warning",
            "Marka profili henüz onaylanmadı.",
        )

    if ctx.keyword_count == 0:
        return _na(
            "create_keywords",
            "Keyword Havuzu Oluştur",
            "/keywords",
            "primary",
            "Workspace için henüz keyword yok.",
        )

    if ctx.active_run is None:
        return _na(
            "start_scoring",
            "Skorlama Başlat",
            "/keywords",
            "primary",
            "Henüz bir skorlama çalışması yok.",
        )

    active = ctx.active_run
    qs = f"?run_id={active.id}"

    if active.status == "failed":
        return _na(
            "check_failed_run",
            "Skorlamayı Kontrol Et",
            _scores_path(active.id),
            "danger",
            "Aktif run hata ile sonuçlandı.",
        )

    if ctx.blocking_task is not None:
        return _na(
            "track_task",
            "İşlemi Takip Et",
            f"/tasks{qs}",
            "neutral",
            "Aktif bir kritik işlem sürüyor.",
        )

    if active.status == "scored":
        if not active.skip_relevance and not ctx.relevance_exists:
            return _na(
                "compute_relevance",
                "İlgi Skoru Hesapla",
                _scores_path(active.id),
                "primary",
                "Skorlama bitti, ilgi skoru hesaplanmalı.",
            )
        if not ctx.any_pool:
            return _na(
                "start_channels",
                "Kanal Atamasını Başlat",
                _scores_path(active.id),
                "primary",
                "Kanal ataması yapılmalı.",
            )

    if active.status == "relevance_computed" and not ctx.any_pool:
        return _na(
            "start_channels",
            "Kanal Atamasını Başlat",
            _scores_path(active.id),
            "primary",
            "İlgi skoru hazır; kanal atamasını başlatabilirsin.",
        )

    if ctx.any_pool is False and active.status in {"channel_assigned"}:
        return _na(
            "check_channels",
            "Kanal Sonuçlarını Kontrol Et",
            _scores_path(active.id),
            "warning",
            "Kanal ataması yapıldı ama tüm havuzlar boş.",
        )

    if ctx.any_pool:
        if ctx.total_generated == 0:
            ready = ctx.ready_channels
            if len(ready) == 1:
                tab = ready[0].lower()
                labels = {
                    "ads": "Google Ads İçeriği Üret",
                    "seo": "SEO İçeriği Üret",
                    "social": "Sosyal İçerik Üret",
                }
                return _na(
                    f"generate_{tab}",
                    labels.get(tab, "İçerik Üret"),
                    _channel_path(tab, active.id),
                    "primary",
                    "Kanal havuzu hazır; içerik üretimine geç.",
                )
            return _na(
                "generate_content",
                "İçerik Üretimine Geç",
                _channel_path("seo", active.id),
                "primary",
                "Kanal havuzları hazır; içerik üretimine geç.",
            )

        if ctx.partial_channels:
            return _na(
                "complete_content",
                "Eksik İçerikleri Tamamla",
                _channel_path(ctx.partial_channels[0], active.id),
                "warning",
                "Bazı kanallar için içerik eksik.",
            )

        if ctx.all_complete:
            # Export sayfası kalktı (plan v4): hedef, İndir menüsü bulunan
            # varsayılan kanal sayfası — sabit tek hedef (Codex tur-5 #3).
            # ctx.latest_export TAM RAPOR job'udur (sections 'all' içeren);
            # ADS-only export burada görünmez → "Tam Rapor Oluştur" kalır.
            export = ctx.latest_export
            if export is None:
                return _na(
                    "create_export",
                    "Tam Rapor Oluştur",
                    f"/seo-geo{qs}",
                    "primary",
                    "Tüm içerikler hazır; İndir menüsünden tam rapor oluşturabilirsin.",
                )
            if export.status == "failed":
                return _na(
                    "check_export_failed",
                    "Export Hatasını Kontrol Et",
                    f"/seo-geo{qs}",
                    "danger",
                    "Son tam rapor denemesi başarısız oldu.",
                )
            if export.status in {"pending", "processing"}:
                return _na(
                    "track_export",
                    "Export Durumunu Takip Et",
                    f"/seo-geo{qs}",
                    "neutral",
                    "Tam rapor hazırlanıyor.",
                )
            if export.status == "completed":
                # Doğrudan indirme: kart export_id ile exportApi.download çağırır
                return _na(
                    "download_export",
                    "Raporu İndir",
                    f"/seo-geo{qs}",
                    "primary",
                    "Hazır tam raporu indirebilirsin.",
                    export_id=export.export_id,
                )

    return _na(
        "review_run",
        "Run Detayını İncele",
        _scores_path(active.id),
        "neutral",
        "Aktif çalışmayı incele.",
    )
