# -*- coding: utf-8 -*-
"""Kalıcı AI maliyet ledger'ı — hard-cap OTORİTESİ (plan §5.7 / §10.2).

Neden DB: bellek sayacı tek process içinde yeterlidir; worker hard-kill,
Celery retry veya çok-worker dağıtımında harcanmış bütçeyi UNUTUR. Bu
modül `ai_cost_reservations` tablosunu tek yazma noktası yapar.

Sözleşme (Codex 6-7. tur):
- ONAYLI CAP OTORİTESİ DB'DİR: her rezervasyonda kilitlenen attempt
  satırından `approved_screening_cap_usd` / `approved_downstream_cap_usd`
  OKUNUR. NULL ise fail-closed. Constructor'a verilen değerler yalnız
  BEKLENEN değer olarak birebir doğrulanır (plan-binding kalıbı).
- Birleşik cap sözleşme gereği iki onaylı cap'in TOPLAMIDIR.
- Her SELECT/UPDATE `budget_owner_attempt_id` ile sınırlıdır: bir ledger
  BAŞKA attempt'in rezervasyonuna DOKUNAMAZ.
- Yuvarlama hard-cap YÖNÜNDE konservatif: harcama/tavan ROUND_CEILING,
  onaylı cap ROUND_FLOOR.
- `actual > ceiling` invariant ihlalidir: muhasebe tavanı yakar ama
  GERÇEK sağlayıcı maliyeti `observed_actual_usd`'de KORUNUR.
- settle/ceiling-charge GERÇEK CAS'tır (`WHERE state='reserved'`).
- Aynı (kind, request_id, attempt) için tipli davranış:
  `ReservationInFlight` / `ReservationAlreadyClosed`.
- Stale reconciler CANLI çağrıyla yarışmaz: varsayılan olarak yalnız
  TERMINAL attempt'lerin açık rezervasyonlarını kapatır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select, update

from app.database.models import AiCostReservation, ChannelAssignmentAttempt

BUDGET_SCREENING = "screening"
BUDGET_DOWNSTREAM = "downstream"
BUDGET_KINDS = (BUDGET_SCREENING, BUDGET_DOWNSTREAM)
STATE_RESERVED = "reserved"
STATE_SETTLED = "settled"
STATE_CEILING = "ceiling_charged"
TERMINAL_OWNER_STATES = ("completed", "failed")
# Codex 8. tur #1: provider harcamasi YALNIZ calisan attempt icin.
# Orkestrasyon (Chunk 4) harcamadan ONCE attempt'i `running` yapar;
# gecikmis Celery retry terminal attempt'te YENI rezervasyon ACAMAZ.
SPENDABLE_OWNER_STATES = ("running",)
# Açık rezervasyonun "sahipsiz" sayılması için gereken yaş (worker öldü)
STALE_RESERVATION_SECONDS = 1800
_CENT = Decimal("0.000001")
CAP_COLUMNS = {
    BUDGET_SCREENING: "approved_screening_cap_usd",
    BUDGET_DOWNSTREAM: "approved_downstream_cap_usd",
}


class BudgetError(RuntimeError):
    """Ledger sözleşmesi ihlali (fail-closed)."""


class BudgetExceeded(BudgetError):
    """Onaylı cap aşılacaktı — provider çağrısı YAPILMADI."""


class LedgerInvariantError(BudgetError):
    """actual > ceiling veya CAS kaybı — koşu durdurulur."""


class ReservationInFlight(BudgetError):
    """Aynı (kind, request_id, attempt) için AÇIK rezervasyon var."""


class ReservationAlreadyClosed(BudgetError):
    """Aynı kimlik zaten kapanmış — çağıran replay/checkpoint yolunu seçer."""


class OwnerNotSpendable(BudgetError):
    """Attempt harcanabilir durumda değil (terminal/beklemede)."""


class TrialWorkspaceCapExceeded(BudgetExceeded):
    """Deneme workspace'inin assignment maliyet sınırı — çağrı YAPILMADI.

    Attempt bazlı cap'ler tek bir koşuyu sınırlar; bu sınır WORKSPACE
    genelidir ve eşzamanlı iki koşunun birlikte sınırı aşmasını engeller.

    KAPSAM: bu ledger yalnız assignment attempt'ine bağlı çağrıları
    (downstream + screening) kapsar. Relevance embedding gibi ledger
    DIŞI çağrılar burada rezerve EDİLMEZ; onlar usage event'lerinden
    ölçülür ve kapılarda maruziyete dahil olur.
    """


def to_decimal(value: Any, name: str,
               rounding: str = ROUND_CEILING,
               allow_negative: bool = False) -> Decimal:
    """Güvenli Decimal: float ASLA doğrudan kullanılmaz (ikili yuvarlama).

    Varsayılan yuvarlama ROUND_CEILING'dir: harcama/tavan tarafında
    aşağı yuvarlama hard-cap'i sessizce gevşetirdi (Codex 7. tur #4).
    Negatiflik QUANTIZE'DAN ÖNCE kontrol edilir (Codex 8. tur #3):
    -0.0000001 önce 0.000000'a yuvarlanıp sonraki kontrolden geçiyordu.
    """
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, bool) or value is None:
        raise BudgetError(f"{name} sayı olmalı: {value!r}")
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        dec = Decimal(str(value))       # repr üzerinden — 0.1 tuzağı yok
    elif isinstance(value, str):
        try:
            dec = Decimal(value)
        except InvalidOperation as exc:
            raise BudgetError(f"{name} geçersiz ondalık: {value!r}") from exc
    else:
        raise BudgetError(f"{name} desteklenmeyen tip: {type(value).__name__}")
    if not dec.is_finite():
        raise BudgetError(f"{name} sonlu olmalı: {value!r}")
    if not allow_negative and dec < 0:
        raise BudgetError(f"{name} negatif olamaz: {dec}")
    return dec.quantize(_CENT, rounding=rounding)


def to_cap_decimal(value: Any, name: str) -> Decimal:
    """Onaylı cap: KONSERVATİF aşağı yuvarlama (daha az izin)."""
    return to_decimal(value, name, rounding=ROUND_FLOOR)


class AiCostLedger:
    """Bir assignment attempt'inin (screening + downstream) bütçe sahibi.

    Cap'ler DB'den okunur (OTORİTE); `expected_*` ZORUNLUDUR ve
    preflight'ta onaylanan değerle birebir doğrulanır — sapmada
    fail-closed. Harcama yalnız `running` attempt için yapılabilir.
    """

    def __init__(self, session_factory, *, attempt_id: int,
                 expected_screening_cap_usd: Any,
                 expected_downstream_cap_usd: Any):
        if not callable(session_factory):
            raise BudgetError("session_factory çağrılabilir olmalı")
        if not isinstance(attempt_id, int) or isinstance(attempt_id, bool) \
                or attempt_id <= 0:
            raise BudgetError(f"attempt_id pozitif olmalı: {attempt_id!r}")
        self._sf = session_factory
        self.attempt_id = attempt_id
        # Codex 8. tur #2: preflight cap baglamasi ZORUNLU — "onaydan
        # sonra cap degisirse reddedilir" garantisi cagirana birakilmaz
        self._expected = {
            BUDGET_SCREENING: to_cap_decimal(expected_screening_cap_usd,
                                             "expected_screening_cap_usd"),
            BUDGET_DOWNSTREAM: to_cap_decimal(expected_downstream_cap_usd,
                                              "expected_downstream_cap_usd"),
        }
        for kind, cap in self._expected.items():
            if cap <= 0:
                raise BudgetError(
                    f"expected {kind} cap pozitif olmalı: {cap}")

    # ── iç yardımcılar ───────────────────────────────────────────────
    def _lock_owner_caps(self, session, *,
                         require_spendable: bool = False
                         ) -> Tuple[Decimal, Decimal]:
        """Owner satırını KİLİTLE; durumu ve ONAYLI cap'leri DB'den oku.

        Codex 7. tur #1: cap otoritesi constructor DEĞİL DB'dir.
        Codex 8. tur #1: harcama için durum da AYNI kilitli sorguda
        okunur — terminal attempt'e gelen gecikmiş retry yeni rezervasyon
        AÇAMAZ (kilit, terminal geçişiyle yarışı da serileştirir).
        """
        row = session.execute(
            select(ChannelAssignmentAttempt.id,
                   ChannelAssignmentAttempt.approved_screening_cap_usd,
                   ChannelAssignmentAttempt.approved_downstream_cap_usd,
                   ChannelAssignmentAttempt.status)
            .where(ChannelAssignmentAttempt.id == self.attempt_id)
            .with_for_update()
        ).first()
        if row is None:
            raise BudgetError(
                f"bütçe sahibi attempt {self.attempt_id} yok — fail-closed")
        if require_spendable and row[3] not in SPENDABLE_OWNER_STATES:
            raise OwnerNotSpendable(
                f"attempt {self.attempt_id} durumu {row[3]!r} — provider "
                f"harcaması yalnız {SPENDABLE_OWNER_STATES} durumunda "
                f"yapılabilir (gecikmiş retry reddedildi)")
        caps = {}
        for kind in BUDGET_KINDS:
            raw = row[1] if kind == BUDGET_SCREENING else row[2]
            if raw is None:
                raise BudgetError(
                    f"attempt {self.attempt_id} için onaylı "
                    f"{CAP_COLUMNS[kind]} YOK — harcama yapılamaz "
                    f"(fail-closed)")
            cap = to_cap_decimal(raw, CAP_COLUMNS[kind])
            if cap <= 0:
                raise BudgetError(
                    f"{CAP_COLUMNS[kind]} pozitif olmalı: {cap}")
            expected = self._expected[kind]
            if expected is not None and expected != cap:
                raise BudgetError(
                    f"CAP BAĞLAMA REDDİ: {CAP_COLUMNS[kind]} DB'de {cap}, "
                    f"beklenen {expected} — onaydan sonra değişmiş")
            caps[kind] = cap
        return caps[BUDGET_SCREENING], caps[BUDGET_DOWNSTREAM]

    def _lock_trial_workspace(self, session) -> Optional[Tuple[int, Decimal]]:
        """Deneme workspace'i ise satırı KİLİTLE ve delinemez sınırı oku.

        Kilit sırası HER YERDE workspace → attempt/run'dır (create
        endpoint'i ve dispatcher da böyle kilitler); tersine kilitleme
        deadlock üretirdi. Normal müşteri workspace'inde hiçbir kilit
        alınmaz ve `None` döner (sıfır ek maliyet).
        """
        import sqlalchemy as sa

        from app.core.trial_authorization import (
            assignment_cost_cap_usd, trial_setup,
        )
        from app.database.models import BrandProfile, ScoringRun

        workspace_id = session.execute(
            select(ScoringRun.brand_profile_id)
            .join(ChannelAssignmentAttempt,
                  ChannelAssignmentAttempt.scoring_run_id == ScoringRun.id)
            .where(ChannelAssignmentAttempt.id == self.attempt_id)
        ).scalar()
        if workspace_id is None:
            return None
        # Kilit ÖNCE: cap okuması ve taahhüt toplamı aynı kilidin altında
        workspace = session.execute(
            sa.select(BrandProfile)
            .where(BrandProfile.id == workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if workspace is None or trial_setup(workspace) is None:
            return None
        cap = assignment_cost_cap_usd(workspace)
        if cap is None:
            raise BudgetError(
                f"deneme workspace {workspace_id} için assignment maliyet "
                f"sınırı YOK — harcama yapılamaz (fail-closed)")
        return int(workspace_id), to_cap_decimal(cap,
                                                 "assignment_cost_cap_usd")

    def _workspace_committed(self, session, workspace_id: int) -> Decimal:
        """Workspace GENELİNDE taahhüt (AYRIŞTIRILMIŞ hesap).

            max(kapanmış ledger, usage) + AÇIK rezervasyon tavanı

        Kapanmış rezervasyon usage event'inde de görünür (çifte sayım),
        bu yüzden yalnız o iki kalem `max` ile birleşir; açık rezervasyon
        henüz usage üretmediği için TAM eklenir.
        """
        from app.core.trial_authorization import workspace_cost_usd

        cost = workspace_cost_usd(session, workspace_id)
        return to_decimal(cost["exposure_usd"], "workspace_exposure")

    def _committed(self, session, kind: Optional[str] = None) -> Decimal:
        """settled/ceiling_charged gerçekleşen + AÇIK rezervasyon tavanı.

        Muhasebe `actual_usd` (varsa) yoksa `ceiling_usd` üzerinden yapılır;
        `observed_actual_usd` DENETİM alanıdır, cap hesabına girmez.
        """
        query = select(
            func.coalesce(func.sum(
                func.coalesce(AiCostReservation.actual_usd,
                              AiCostReservation.ceiling_usd)), 0)
        ).where(AiCostReservation.budget_owner_attempt_id == self.attempt_id)
        if kind is not None:
            query = query.where(AiCostReservation.budget_kind == kind)
        return to_decimal(session.execute(query).scalar_one(), "committed")

    def _owned_row(self, session, reservation_id: int):
        """Rezervasyonu YALNIZ bu attempt'in sahipliğinde getir (#2)."""
        row = session.execute(
            select(AiCostReservation.id, AiCostReservation.ceiling_usd,
                   AiCostReservation.state)
            .where(AiCostReservation.id == reservation_id,
                   AiCostReservation.budget_owner_attempt_id
                   == self.attempt_id)
        ).first()
        if row is None:
            raise BudgetError(
                f"rezervasyon {reservation_id} bu attempt "
                f"({self.attempt_id}) için yok — çapraz erişim reddedildi")
        return row

    # ── genel API ────────────────────────────────────────────────────
    def caps(self) -> Dict[str, Decimal]:
        """DB'deki onaylı cap'ler + sözleşme gereği birleşik toplam."""
        session = self._sf()
        try:
            screening, downstream = self._lock_owner_caps(session)
            session.rollback()          # yalnız okuma; kilidi bırak
            return {BUDGET_SCREENING: screening,
                    BUDGET_DOWNSTREAM: downstream,
                    "combined": screening + downstream}
        finally:
            session.close()

    def reserve(self, *, kind: str, request_id: str, ceiling_usd: Any,
                request_attempt: int = 1, auto_attempt: bool = False,
                stage: Optional[str] = None,
                provider: Optional[str] = None,
                model: Optional[str] = None) -> int:
        """Çağrı ÖNCESİ atomik rezervasyon; cap aşılacaksa BudgetExceeded.

        `auto_attempt=True`: aynı `request_id` için sıradaki deneme numarası
        SAHİP KİLİDİ ALTINDA hesaplanır (çökme sonrası tavandan yakılmış
        denemenin ardından yeniden deneme). Kimlik gizlice EZİLMEZ: her
        deneme kendi satırını alır, denetim izi korunur.
        """
        if kind not in BUDGET_KINDS:
            raise BudgetError(f"bilinmeyen bütçe türü: {kind!r}")
        if not request_id or not isinstance(request_id, str):
            raise BudgetError("request_id boş olmayan metin olmalı")
        if isinstance(request_attempt, bool) \
                or not isinstance(request_attempt, int) \
                or request_attempt < 1:
            raise BudgetError(
                f"request_attempt >= 1 tam sayı olmalı: {request_attempt!r}")
        ceiling = to_decimal(ceiling_usd, "ceiling_usd")
        session = self._sf()
        try:
            # DENEME WORKSPACE SINIRI (assignment kapsamı): attempt
            # cap'lerinden ÖNCE ve AYNI transaction'da. Workspace satırı
            # kilitli olduğu için iki eşzamanlı koşu birlikte de aşamaz.
            trial = self._lock_trial_workspace(session)
            if trial is not None:
                workspace_id, workspace_cap = trial
                committed = self._workspace_committed(session, workspace_id)
                if committed + ceiling > workspace_cap:
                    raise TrialWorkspaceCapExceeded(
                        f"HARD-CAP [deneme workspace {workspace_id}]: "
                        f"maruziyet ${committed} + sıradaki tavan ${ceiling} "
                        f"> assignment sınırı ${workspace_cap} — çağrı "
                        f"YAPILMADI")
            screening_cap, downstream_cap = self._lock_owner_caps(
                session, require_spendable=True)
            caps = {BUDGET_SCREENING: screening_cap,
                    BUDGET_DOWNSTREAM: downstream_cap}
            combined_cap = screening_cap + downstream_cap

            if auto_attempt:
                # Kilit altında: eşzamanlı iki reserve aynı numarayı almaz
                last = session.execute(
                    select(func.max(AiCostReservation.attempt))
                    .where(AiCostReservation.budget_owner_attempt_id
                           == self.attempt_id,
                           AiCostReservation.budget_kind == kind,
                           AiCostReservation.request_id == request_id)
                ).scalar()
                request_attempt = int(last or 0) + 1

            # Codex 7. tur #6: aynı kimliğin tekrarında TİPLİ davranış
            existing = session.execute(
                select(AiCostReservation.id, AiCostReservation.state)
                .where(AiCostReservation.budget_owner_attempt_id
                       == self.attempt_id,
                       AiCostReservation.budget_kind == kind,
                       AiCostReservation.request_id == request_id,
                       AiCostReservation.attempt == request_attempt)
            ).first()
            if existing is not None:
                if existing[1] == STATE_RESERVED:
                    raise ReservationInFlight(
                        f"aynı kimlikte AÇIK rezervasyon var "
                        f"(id {existing[0]}, {kind}/{request_id}"
                        f"#{request_attempt})")
                raise ReservationAlreadyClosed(
                    f"aynı kimlik zaten kapanmış (id {existing[0]}, "
                    f"durum {existing[1]}) — replay/checkpoint yolunu seçin")

            kind_committed = self._committed(session, kind)
            total_committed = self._committed(session)
            if kind_committed + ceiling > caps[kind]:
                raise BudgetExceeded(
                    f"HARD-CAP [{kind}]: taahhüt ${kind_committed} + sıradaki "
                    f"tavan ${ceiling} > onaylı ${caps[kind]} — çağrı "
                    f"YAPILMADI")
            if total_committed + ceiling > combined_cap:
                raise BudgetExceeded(
                    f"HARD-CAP [birleşik]: toplam taahhüt ${total_committed} "
                    f"+ ${ceiling} > onaylı birleşik ${combined_cap} — "
                    f"çağrı YAPILMADI")
            row = AiCostReservation(
                budget_owner_attempt_id=self.attempt_id, budget_kind=kind,
                request_id=request_id, attempt=request_attempt,
                stage=stage, provider=provider, model=model,
                ceiling_usd=ceiling, state=STATE_RESERVED)
            session.add(row)
            session.commit()
            return int(row.id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def settle(self, reservation_id: int, actual_usd: Any) -> Decimal:
        """Gerçek usage ile kapat (CAS: yalnız `reserved` satır güncellenir)."""
        actual = to_decimal(actual_usd, "actual_usd")
        session = self._sf()
        try:
            row = self._owned_row(session, reservation_id)
            ceiling = to_decimal(row[1], "ceiling_usd")
            if actual > ceiling:
                # Sağlayıcı sınırı delinmiş: MUHASEBE tavanı yakar,
                # GERÇEK maliyet observed_actual_usd'de korunur (#3)
                self._cas_close(session, reservation_id, STATE_CEILING,
                                ceiling, observed=actual)
                session.commit()
                raise LedgerInvariantError(
                    f"İNVARYANT: gerçek maliyet ${actual} > rezervasyon "
                    f"${ceiling} (rez {reservation_id}) — tavan yakıldı, "
                    f"gerçek harcama observed_actual_usd'de KAYITLI, "
                    f"koşu durduruldu")
            self._cas_close(session, reservation_id, STATE_SETTLED, actual,
                            observed=actual)
            session.commit()
            return actual
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def charge_ceiling(self, reservation_id: int,
                       observed_actual_usd: Optional[Any] = None) -> Decimal:
        """Usage yoksa/kısmi ise tavanın TAMAMI yanar (plan §10.1)."""
        session = self._sf()
        try:
            row = self._owned_row(session, reservation_id)
            ceiling = to_decimal(row[1], "ceiling_usd")
            observed = (None if observed_actual_usd is None
                        else to_decimal(observed_actual_usd,
                                        "observed_actual_usd"))
            self._cas_close(session, reservation_id, STATE_CEILING, ceiling,
                            observed=observed)
            session.commit()
            return ceiling
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _cas_close(self, session, reservation_id: int, state: str,
                   actual: Decimal, observed: Optional[Decimal]) -> None:
        """CAS: yalnız BU attempt'in `reserved` satırı ilerler."""
        values = {"state": state, "actual_usd": actual,
                  "settled_at": func.now()}
        if observed is not None:
            values["observed_actual_usd"] = observed
        result = session.execute(
            update(AiCostReservation)
            .where(AiCostReservation.id == reservation_id,
                   AiCostReservation.budget_owner_attempt_id
                   == self.attempt_id,
                   AiCostReservation.state == STATE_RESERVED)
            .values(**values)
        )
        if result.rowcount != 1:
            raise LedgerInvariantError(
                f"CAS kaybı: rezervasyon {reservation_id} zaten kapatılmış "
                f"veya bu attempt'e ait değil (rowcount={result.rowcount})")

    def reservation_status(self, *, kind: str, request_id: str,
                           request_attempt: int = 1) -> Optional[Dict[str, Any]]:
        """Salt-okunur gözlem: verilen (kind, request_id, attempt) için
        rezervasyon var mı, hangi `state`'te — KİLİT ALMAZ, `reserve()`'ün
        CAS/kilit mantığına müdahale ETMEZ (Social V4 runner B1/B2 fix'i:
        `ReservationAlreadyClosed` sonrası kapanan rezervasyonun `settled`
        mi `ceiling_charged` mi olduğunu AYIRT ETMEK için — kapalı bir
        ledger kaydı asla körlemesine "tamam" sayılamaz)."""
        session = self._sf()
        try:
            row = session.execute(
                select(AiCostReservation.id, AiCostReservation.state,
                       AiCostReservation.actual_usd,
                       AiCostReservation.ceiling_usd)
                .where(AiCostReservation.budget_owner_attempt_id
                       == self.attempt_id,
                       AiCostReservation.budget_kind == kind,
                       AiCostReservation.request_id == request_id,
                       AiCostReservation.attempt == request_attempt)
            ).first()
            if row is None:
                return None
            return {"id": int(row[0]), "state": row[1],
                    "actual_usd": row[2], "ceiling_usd": row[3]}
        finally:
            session.close()

    def latest_attempt_number(self, *, kind: str, request_id: str) -> int:
        """Salt-okunur: bu (kind, request_id) için şimdiye kadar açılmış EN
        YÜKSEK `attempt` numarası (hiç yoksa 0). Kilit ALMAZ — çağıran taraf
        yeni bir `reserve()` çağrısında CAS/kilit zaten devreye girer;
        bu yalnız "kaç deneme hakkı kaldı" hesaplamak için gözlemdir."""
        session = self._sf()
        try:
            val = session.execute(
                select(func.max(AiCostReservation.attempt))
                .where(AiCostReservation.budget_owner_attempt_id
                       == self.attempt_id,
                       AiCostReservation.budget_kind == kind,
                       AiCostReservation.request_id == request_id)
            ).scalar()
            return int(val or 0)
        finally:
            session.close()

    def snapshot(self) -> Dict[str, Any]:
        """Bellek özeti YALNIZ hız/gözlem içindir; otorite ledger'dır."""
        session = self._sf()
        try:
            caps_row = session.execute(
                select(ChannelAssignmentAttempt.approved_screening_cap_usd,
                       ChannelAssignmentAttempt.approved_downstream_cap_usd)
                .where(ChannelAssignmentAttempt.id == self.attempt_id)
            ).first()
            rows = session.execute(
                select(AiCostReservation.budget_kind,
                       AiCostReservation.state,
                       func.count(AiCostReservation.id),
                       func.coalesce(func.sum(
                           func.coalesce(AiCostReservation.actual_usd,
                                         AiCostReservation.ceiling_usd)), 0),
                       func.coalesce(func.sum(
                           AiCostReservation.observed_actual_usd), 0))
                .where(AiCostReservation.budget_owner_attempt_id
                       == self.attempt_id)
                .group_by(AiCostReservation.budget_kind,
                          AiCostReservation.state)
            ).all()
            screening_cap = (to_cap_decimal(caps_row[0], "cap")
                             if caps_row and caps_row[0] is not None else None)
            downstream_cap = (to_cap_decimal(caps_row[1], "cap")
                              if caps_row and caps_row[1] is not None
                              else None)
            out: Dict[str, Any] = {
                "attempt_id": self.attempt_id,
                "caps": {
                    BUDGET_SCREENING: (None if screening_cap is None
                                       else str(screening_cap)),
                    BUDGET_DOWNSTREAM: (None if downstream_cap is None
                                        else str(downstream_cap)),
                },
                "combined_cap_usd": (
                    None if screening_cap is None or downstream_cap is None
                    else str(screening_cap + downstream_cap)),
                "by_kind": {},
                "ceiling_charges": 0,
            }
            totals = Decimal("0")
            observed_total = Decimal("0")
            for kind, state, count, amount, observed in rows:
                blk = out["by_kind"].setdefault(
                    kind, {"settled_usd": Decimal("0"),
                           "open_reserved_usd": Decimal("0"),
                           "requests": 0, "ceiling_charges": 0})
                amount = to_decimal(amount, "amount")
                blk["requests"] += int(count)
                totals += amount
                observed_total += to_decimal(observed, "observed")
                if state == STATE_RESERVED:
                    blk["open_reserved_usd"] += amount
                else:
                    blk["settled_usd"] += amount
                if state == STATE_CEILING:
                    blk["ceiling_charges"] += int(count)
                    out["ceiling_charges"] += int(count)
            for blk in out["by_kind"].values():
                blk["settled_usd"] = str(blk["settled_usd"])
                blk["open_reserved_usd"] = str(blk["open_reserved_usd"])
            out["committed_total_usd"] = str(totals)
            out["observed_actual_total_usd"] = str(observed_total)
            if out["combined_cap_usd"] is not None:
                out["remaining_combined_usd"] = str(
                    screening_cap + downstream_cap - totals)
            return out
        finally:
            session.close()


def reconcile_stale_reservations(session, *, attempt_id: Optional[int] = None,
                                 older_than_seconds: int =
                                 STALE_RESERVATION_SECONDS,
                                 require_terminal_owner: bool = True) -> int:
    """Sahipsiz açık rezervasyonları TAVAN YAKARAK kapatır.

    Codex 7. tur #5: yalnız yaşa bakmak CANLI uzun çağrıyla yarışır.
    Varsayılan olarak YALNIZ terminal (completed/failed) attempt'lerin
    açık rezervasyonları kapatılır — canlılık tahmini yapılmaz. Koşan
    ama ölmüş worker'ın attempt'i önce task-seviyesi stale tespitiyle
    `failed` yapılır, sonra bu fonksiyon çağrılır.

    `require_terminal_owner=False` yalnız operatör müdahalesi içindir ve
    canlı çağrıyı kesebilir; bilinçli kullanılmalıdır.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=int(older_than_seconds))
    stmt = (update(AiCostReservation)
            .where(AiCostReservation.state == STATE_RESERVED,
                   AiCostReservation.created_at < cutoff)
            .values(state=STATE_CEILING,
                    actual_usd=AiCostReservation.ceiling_usd,
                    settled_at=func.now()))
    if attempt_id is not None:
        stmt = stmt.where(
            AiCostReservation.budget_owner_attempt_id == attempt_id)
    if require_terminal_owner:
        terminal_owners = select(ChannelAssignmentAttempt.id).where(
            ChannelAssignmentAttempt.status.in_(TERMINAL_OWNER_STATES))
        stmt = stmt.where(
            AiCostReservation.budget_owner_attempt_id.in_(terminal_owners))
    result = session.execute(stmt)
    session.commit()
    return int(result.rowcount or 0)
