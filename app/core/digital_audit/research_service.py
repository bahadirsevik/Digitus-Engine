"""Arastirma yasam dongusu: baslatma (tek aktif), adim adim kayit, bayat uzlastirma, brief istegi.

Denetimle (service.py) ayni kalip: kosullu UPDATE + sureli kilit. Her adimin sonucu
bitince KAYDEDILIR; worker olurse o ana kadarki bolumler 'partial' olarak korunur.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.digital_audit import research, strategy
from app.database.models import BrandProfile, DigitalAudit, DigitalResearch

ACTIVE = ("pending", "running")
FINAL = ("completed", "partial")
# Tek adim (aramali cagri + kaynak indirme) birkac dakika surebilir; kira her adimda yenilenir.
LEASE_SECONDS = 900
STALE_MESSAGE = "Araştırma zamanında tamamlanmadı (sunucu yanıt vermeyi bıraktı). O ana kadar kaydedilen bölümler korundu."
CORE_SECTIONS = ("sector", "audience", "synthesis")


class ResearchInputError(ValueError):
    """Gecersiz istek (eksik sektor, bilinmeyen segment/kanal)."""


class ResearchStateError(RuntimeError):
    """Istek raporun mevcut durumunda yapilamaz (409)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lease() -> datetime:
    return _now() + timedelta(seconds=LEASE_SECONDS)


def reconcile_expired(db: Session, brand_profile_id: int) -> int:
    """Kirasi dolmus aktif arastirmalari kapatir (okuma/baslatma ani). Commit EDER."""
    expired = (
        DigitalResearch.brand_profile_id == brand_profile_id,
        DigitalResearch.status.in_(ACTIVE),
        DigitalResearch.lease_expires_at < _now(),
    )
    values = dict(error_message=STALE_MESSAGE, completed_at=_now(), lease_expires_at=None)
    changed = 0
    for condition, status in ((DigitalResearch.sections_done > 0, "partial"),
                              (DigitalResearch.sections_done == 0, "failed")):
        changed += db.execute(
            update(DigitalResearch).where(*expired, condition)
            .values(status=status, **values).execution_options(synchronize_session=False)
        ).rowcount or 0
    db.commit()
    if changed:
        db.expire_all()
        logger.warning(f"Bayat araştırma kapatıldı: {changed} kayıt (workspace={brand_profile_id})")
    return changed


def _active(db: Session, brand_profile_id: int) -> Optional[DigitalResearch]:
    return (
        db.query(DigitalResearch)
        .filter(DigitalResearch.brand_profile_id == brand_profile_id, DigitalResearch.status.in_(ACTIVE))
        .first()
    )


def start_research(db: Session, workspace: BrandProfile, sector: str, geography: str,
                   focus: Optional[str] = None) -> Tuple[DigitalResearch, bool]:
    """Yeni arastirma acar ya da sureni dondurur: (report, reused). Tek aktif DB indeksiyle garanti."""
    reconcile_expired(db, workspace.id)
    active = _active(db, workspace.id)
    if active:
        return active, True
    sector, geography = (sector or "").strip(), (geography or "").strip()
    if len(sector) < 3 or len(geography) < 2:
        raise ResearchInputError("Sektör ve coğrafya yazılmalı.")
    # Sentez, workspace'in sonuc uretmis en yeni site denetimine dayanir (yoksa onsuz calisir).
    audit = (
        db.query(DigitalAudit)
        .filter(DigitalAudit.brand_profile_id == workspace.id, DigitalAudit.status.in_(FINAL))
        .order_by(DigitalAudit.created_at.desc(), DigitalAudit.id.desc()).first()
    )
    report = DigitalResearch(
        brand_profile_id=workspace.id, audit_id=audit.id if audit else None,
        status="pending", stage="research", progress=0, task_id=str(uuid.uuid4()),
        lease_expires_at=_lease(),
        inputs={"sector": sector[:200], "geography": geography[:120], "focus": (focus or "").strip()[:300] or None},
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = _active(db, workspace.id)
        if active:
            return active, True
        raise
    db.refresh(report)
    return report, False


def request_briefs(db: Session, report: DigitalResearch, segments: List[int], channels: List[str]) -> None:
    """Secilen segment + kanallar icin brief asamasini kuyruga hazirlar (durum -> pending)."""
    available = ((report.sections or {}).get("synthesis") or {}).get("segments") or []
    segments = sorted(set(segments))
    if not segments or any(i < 0 or i >= len(available) for i in segments):
        raise ResearchInputError("Geçerli en az bir segment seçin.")
    channels = list(dict.fromkeys(channels))
    if not channels or any(c not in research.CHANNELS for c in channels):
        raise ResearchInputError("Geçerli en az bir kanal seçin.")
    if research.synthesis_stale(report):
        raise ResearchStateError("Rakam kararları ya da brif/beyan bilgileri değişti; brief üretmeden önce segmentleri yenileyin.")
    _requeue(db, report, "briefs",
             {"status": "pending", "selection": {"segments": segments, "channels": channels}})


def request_synthesis(db: Session, report: DigitalResearch) -> None:
    """Sentezi analistin guncel rakam kararlariyla yeniden uretir. Eski briefler silinir
    (segmentler degisecegi icin gecersizdir)."""
    sections = report.sections or {}
    if not any((sections.get(name) or {}).get("status") == "ok" for name in ("sector", "audience")):
        raise ResearchInputError("Segment üretmek için araştırma bölümü yok.")
    _requeue(db, report, "synthesis", None)


def request_strategy(db: Session, report: DigitalResearch) -> None:
    """Karar odakli rapor taslagini kuyruga hazirlar (ucretli tek cagri; acik kullanici eylemi).
    Suren is varken ikinci istek 409 alir: cift tiklama iki cagri uretmez."""
    synthesis = (report.sections or {}).get("synthesis") or {}
    if synthesis.get("status") != "ok":
        raise ResearchInputError("Strateji için önce segmentler üretilmiş olmalı.")
    if research.synthesis_stale(report):
        raise ResearchStateError("Segmentler güncel değil; strateji üretmeden önce segmentleri yenileyin.")
    _requeue(db, report, "strategy", report.briefs)


def _locked_strategy(db: Session, report: DigitalResearch, version: int, revision: int) -> Dict:
    """Satiri KILITLER ve istegin gordugu surum + icerik revizyonunu veritabaninda dogrular.
    Iki analist ayni taslagi actiysa, biri duzeltince digerinin eski ekrandan kaydi/onayi 409 alir:
    kimse gormedigi metni onaylayamaz. Cagiran ayni transaction'da yazar ve commit eder."""
    db.rollback()  # okuma transaction'ini kapat: kilit en guncel satiri gorsun
    row = (db.query(DigitalResearch).filter(DigitalResearch.id == report.id)
           .with_for_update().populate_existing().one())
    if row.status not in FINAL:
        raise ResearchStateError("Bu marka çalışmasında süren bir araştırma var.")
    current = (row.sections or {}).get("strategy") or {}
    if current.get("status") != "ok":
        raise ResearchInputError("Düzeltilecek strateji taslağı yok.")
    if strategy.state(row) == "stale":
        raise ResearchStateError("Strateji güncel değil; önce yeniden üretin.")
    if current.get("version") != version or int(current.get("revision") or 0) != revision:
        raise ResearchStateError("Strateji bu arada değişti; sayfayı yenileyip güncel metni inceleyin.")
    return current


VERIFICATION_FIELDS = ("primary_source_url", "regulation_date", "verified_by", "verified_at")


def save_restriction_verification(db: Session, report: DigitalResearch, index: int, data: Dict) -> None:
    """Bir reklam kisitinin BIRINCIL KAYNAK teyidi (analist girer). Dort alan birlikte zorunludur: yarim kayit
    kisiti 'teyit edildi' gostermesin. Tum alanlar bossa teyit kaldirilir. Sistem kaynagi kendisi kontrol etmez;
    teyidi yapan analisttir ve adi kayda gecer."""
    values = {f: str(data.get(f) or "").strip()[:500] for f in VERIFICATION_FIELDS}
    if any(values.values()):
        if not all(values.values()):
            raise ResearchInputError("Teyit için birincil kaynak adresi, mevzuat tarihi, teyit eden ve teyit tarihi birlikte girilmeli.")
        if not values["primary_source_url"].lower().startswith(("http://", "https://")):
            raise ResearchInputError("Birincil kaynak adresi http:// ya da https:// ile başlamalı.")
        try:
            date.fromisoformat(values["regulation_date"])
            verified = date.fromisoformat(values["verified_at"])
        except ValueError:
            raise ResearchInputError("Tarihler YYYY-AA-GG biçiminde olmalı.")
        if verified > _now().date():
            raise ResearchInputError("Teyit tarihi gelecekte olamaz.")
        if len(values["verified_by"]) < 2:
            raise ResearchInputError("Teyit edenin adı yazılmalı.")
    db.rollback()
    row = (db.query(DigitalResearch).filter(DigitalResearch.id == report.id)
           .with_for_update().populate_existing().one())
    if row.status not in FINAL:
        db.rollback()
        raise ResearchStateError("Bu marka çalışmasında süren bir araştırma var.")
    sections = dict(row.sections or {})
    sector = dict(sections.get("sector") or {})
    restrictions = [dict(r) for r in sector.get("restrictions") or []]
    if not 0 <= index < len(restrictions):
        db.rollback()
        raise ResearchInputError("Kısıt bulunamadı.")
    if any(values.values()):
        restrictions[index]["verification"] = values
    else:
        restrictions[index].pop("verification", None)
    sector["restrictions"] = restrictions
    sections["sector"] = sector
    report.sections = sections
    db.commit()
    db.refresh(report)


def _write_strategy(db: Session, report: DigitalResearch, new_strategy: Dict) -> None:
    report.sections = {**(report.sections or {}), "strategy": new_strategy}
    db.commit()
    db.refresh(report)


def save_strategy_edit(db: Session, report: DigitalResearch, version: int, revision: int, content: Dict) -> None:
    """Analist duzeltmesi. Dayanaksiz madde reddedilir; duzeltme revizyonu artirir ve onayi dusurur."""
    try:
        current = _locked_strategy(db, report, version, revision)
        edited = strategy.normalize(content, current["catalogue"], current["ad_base_status"], strict=True)
    except strategy.StrategyInputError as exc:
        db.rollback()
        raise ResearchInputError(str(exc))
    except Exception:
        db.rollback()
        raise
    _write_strategy(db, report, {**current, "edited": edited, "approval": None, "revision": revision + 1})


def approve_strategy(db: Session, report: DigitalResearch, version: int, revision: int, approved_by: str) -> None:
    """Analist EKRANDA GORDUGU revizyonu isaretler. Onay icerik ozetine baglidir: sonraki duzeltme,
    yenileme ya da girdi degisikligi onayi gecersiz kilar."""
    try:
        current = _locked_strategy(db, report, version, revision)
        waiting = strategy.open_warnings(strategy.content(current))
        if waiting:
            raise ResearchInputError(f"Uyarısı duran {waiting} madde var; düzeltin ya da çıkarın, sonra işaretleyin.")
    except Exception:
        db.rollback()
        raise
    _write_strategy(db, report, {**current, "approval": {
        "approved_by": approved_by.strip()[:120], "approved_at": _now().isoformat(),
        "version": version, "revision": revision, "review_sha": strategy.review_sha(report, current)}})


def _requeue(db: Session, report: DigitalResearch, stage: str, briefs: Optional[Dict]) -> None:
    try:
        claimed = db.execute(
            update(DigitalResearch)
            .where(DigitalResearch.id == report.id, DigitalResearch.status.in_(FINAL))
            .values(status="pending", stage=stage, progress=0, task_id=str(uuid.uuid4()),
                    lease_expires_at=_lease(), error_message=None, completed_at=None, briefs=briefs)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
    except IntegrityError:
        db.rollback()
        claimed = 0
    if not claimed:
        raise ResearchStateError("Bu marka çalışmasında süren bir araştırma var.")
    db.refresh(report)


def mark_dispatch_failed(db: Session, report: DigitalResearch) -> None:
    if report.stage != "research":  # arastirma sonucu yerinde durur; yalniz ek istek dusmustur
        report.status = _final_status(report.sections or {})
        if report.stage == "briefs":
            report.briefs = {**(report.briefs or {}), "status": "failed", "error": "Kuyruğa alınamadı."}
    else:
        report.status = "failed"
    report.error_message = "Araştırma kuyruğa alınamadı. Lütfen tekrar deneyin."
    report.completed_at = _now()
    report.lease_expires_at = None
    db.commit()


def _save(db: Session, report_id: int, task_id: str, **values) -> bool:
    """Arastirma hala bu worker'daysa degerleri yazar + kirayi yeniler. Commit EDER."""
    written = db.execute(
        update(DigitalResearch)
        .where(DigitalResearch.id == report_id, DigitalResearch.status == "running",
               DigitalResearch.task_id == task_id)
        .values(lease_expires_at=_lease(), **values).execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return bool(written)


def _final_status(sections: Dict) -> str:
    done = [(sections.get(name) or {}).get("status") == "ok" for name in CORE_SECTIONS]
    return "completed" if all(done) else "partial" if any(done) else "failed"


def _failed(exc: Exception) -> Dict:
    message = str(exc) if isinstance(exc, research.ResearchError) else "Bu bölüm üretilirken beklenmeyen bir hata oluştu."
    return {"status": "failed", "error": message[:300]}


def run_research(db: Session, report_id: int, task_id: str, ai, ads_service=None) -> str:
    """Donus: completed | partial | failed | skipped | superseded."""
    from celery.exceptions import SoftTimeLimitExceeded

    claimed = db.execute(
        update(DigitalResearch)
        .where(DigitalResearch.id == report_id, DigitalResearch.status == "pending",
               DigitalResearch.task_id == task_id)
        .values(status="running", started_at=_now(), lease_expires_at=_lease())
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if not claimed:
        return "skipped"

    report = db.get(DigitalResearch, report_id)
    workspace = db.get(BrandProfile, report.brand_profile_id)
    audit = db.get(DigitalAudit, report.audit_id) if report.audit_id else None
    sections: Dict = dict(report.sections or {})
    usage: Dict = dict(report.usage or {})

    def checkpoint(progress: int, **extra) -> bool:
        done = sum(1 for s in sections.values() if (s or {}).get("status") == "ok")
        return _save(db, report_id, task_id, sections=dict(sections), sections_done=done,
                     usage=dict(usage), progress=progress, **extra)

    try:
        if report.stage == "strategy":
            try:
                sections["strategy"] = strategy.generate(ai, workspace, audit, report, usage)
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                logger.exception(f"strateji üretimi başarısız (research={report_id})")
                previous = sections.get("strategy") or {}
                # Onceki gecerli cikti BOZULMAZ; hata yaninda gosterilir.
                sections["strategy"] = ({**previous, "last_error": _failed(exc)["error"]}
                                        if previous.get("status") == "ok" else _failed(exc))
            if not checkpoint(95):
                return "superseded"
        elif report.stage in ("synthesis", "briefs"):
            # Kullanilan kararlar ciktiyla birlikte saklanir: analist uretim surerken karar
            # degistirirse sonuc okuma aninda bayat gorunur (research.synthesis_stale).
            overrides = list(report.claim_overrides or [])
            used = research.claim_ids(sections.get("sector"), overrides)
            answers_sha = research.answers_sha(audit.answers if audit else None)
            facts = research.build_facts(workspace, audit, sections, overrides)
            try:
                if report.stage == "synthesis":
                    sections["synthesis"] = {**research.synthesize(ai, facts, usage), "claim_ids": used,
                                             "answers_sha": answers_sha}
                    extra = {}
                else:
                    selection = (report.briefs or {}).get("selection") or {}
                    chosen = [sections["synthesis"]["segments"][i] for i in selection.get("segments", [])]
                    briefs = research.generate_briefs(ai, facts, chosen, selection.get("channels", []), usage)
                    extra = {"briefs": {**briefs, "selection": selection, "claim_ids": used,
                                        "answers_sha": answers_sha}}
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                logger.exception(f"{report.stage} üretimi başarısız (research={report_id})")
                if report.stage == "synthesis":
                    sections["synthesis"] = _failed(exc)
                    extra = {}
                else:
                    extra = {"briefs": {**_failed(exc), "selection": (report.briefs or {}).get("selection") or {}}}
            if not checkpoint(95, **extra):
                return "superseded"
        else:
            if not _run_research_steps(db, workspace, audit, report.inputs or {}, sections, usage,
                                       ai, ads_service, checkpoint):
                return "superseded"
    except SoftTimeLimitExceeded:
        logger.warning(f"Araştırma süre sınırına takıldı (research={report_id})")

    final = _final_status(sections)
    error = None if final == "completed" else (
        "Bazı bölümler üretilemedi; diğerleri aşağıda." if final == "partial" else "Araştırma sonuç üretemedi.")
    written = db.execute(
        update(DigitalResearch)
        .where(DigitalResearch.id == report_id, DigitalResearch.status == "running",
               DigitalResearch.task_id == task_id)
        .values(status=final, progress=100, completed_at=_now(), lease_expires_at=None, error_message=error)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return final if written else "superseded"


def _run_research_steps(db, workspace, audit, inputs, sections, usage, ai, ads_service, checkpoint) -> bool:
    """Adimlar: arama talebi -> sektor -> hedef kitle -> kaynak dogrulama -> sentez. False = kira kaybedildi."""
    from celery.exceptions import SoftTimeLimitExceeded

    def attempt(name: str, fn):
        try:
            return fn()
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.exception(f"Araştırma adımı başarısız: {name}")
            sections[name] = _failed(exc)
            return None

    sections["demand"] = attempt("demand", lambda: research.collect_demand(db, workspace, ads_service)) \
        or sections.get("demand")
    if not checkpoint(10):
        return False
    questions = research.question_keywords(db, workspace.id)

    # Ham arastirma hemen kaydedilir (verified=False: hicbir iddia dogrulanmis sayilmaz);
    # worker dogrulamadan once olurse ucretli cagri sonucu kaybolmaz.
    sector_raw = attempt("sector", lambda: research.research_sector(ai, inputs, usage))
    if sector_raw:
        sections["sector"] = {**research.build_sector(sector_raw, []), "verified": False}
    if not checkpoint(35):
        return False
    audience_raw = attempt("audience", lambda: research.research_audience(ai, inputs, questions, usage))
    if audience_raw:
        sections["audience"] = {**research.build_audience(audience_raw, [], questions), "verified": False}
    if not checkpoint(60):
        return False

    raws = [raw for raw in (sector_raw, audience_raw) if raw]
    if raws:
        sources = attempt("sources", lambda: research.verify(raws))
        sections.pop("sources", None)  # ayri bolum degil; hata olursa iddialar "bulunamadi" kalir
        sources = sources or []
        usage["sources_fetched"] = len(sources)
        if sector_raw:
            wanted = set(sector_raw["urls"])
            sections["sector"] = {
                **research.build_sector(sector_raw, [s for s in sources if s["url"] in wanted]), "verified": True}
        if audience_raw:
            wanted = set(audience_raw["urls"])
            sections["audience"] = {**research.build_audience(
                audience_raw, [s for s in sources if s["url"] in wanted], questions), "verified": True}
        if not checkpoint(80):
            return False

    if raws:  # arastirma yoksa sentezin dayanagi yok
        answers_sha = research.answers_sha(audit.answers if audit else None)
        facts = research.build_facts(workspace, audit, sections)
        synthesis = attempt("synthesis", lambda: research.synthesize(ai, facts, usage))
        if synthesis:
            sections["synthesis"] = {**synthesis, "claim_ids": research.claim_ids(sections.get("sector")),
                                     "answers_sha": answers_sha}
    else:
        sections["synthesis"] = {"status": "failed", "error": "Araştırma sonuç vermediği için sentez yapılmadı."}
    return checkpoint(95)


def save_claim_overrides(db: Session, report: DigitalResearch, overrides: List[str]) -> None:
    """Analistin baglami okuyup kararini degistirdigi iddialar. Bulgulari DEGISTIRMEZ.

    Onceki kararlarla uretilmis sentez ve briefler bu andan sonra bayat sayilir
    (research.synthesis_stale): rapora girmez, yenilenmeleri gerekir.

    Yalniz kaynakta gecen iddialar (found / short) icin; bulunamayan iddia rapora alinamaz.
    """
    known = {c["id"] for c in ((report.sections or {}).get("sector") or {}).get("claims") or []
             if c["check"]["status"] in ("found", "short")}
    unknown = set(overrides) - known
    if unknown:
        raise ResearchInputError(f"Bu iddia için karar değiştirilemez: {sorted(unknown)[0]}")
    report.claim_overrides = sorted(set(overrides))
    db.commit()


def serialize(report: DigitalResearch, detail: bool = False) -> Dict:
    data = {
        "id": report.id, "status": report.status, "stage": report.stage, "progress": report.progress,
        "error_message": report.error_message, "inputs": report.inputs or {},
        "created_at": report.created_at, "completed_at": report.completed_at,
    }
    if detail:
        sections = dict(report.sections or {})
        if (sections.get("strategy") or {}).get("status") == "ok":  # ekran da guncel eksik listesini gorur
            sections["strategy"] = {**sections["strategy"], "gaps": strategy.live_gaps(report, sections["strategy"])}
        data.update(
            audit_id=report.audit_id, sections=sections, briefs=report.briefs,
            claim_overrides=report.claim_overrides or [], usage=report.usage or {},
            synthesis_stale=research.synthesis_stale(report), briefs_stale=research.briefs_stale(report),
            strategy_state=strategy.state(report),
            channels=research.CHANNELS,
        )
    return data
