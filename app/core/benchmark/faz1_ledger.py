"""Faz 1 deney-geneli sağlayıcı harcama defteri (Codex Faz 1 altyapı #1).

K7 tavanları (Claude 50 / Gemini 20 / DeepSeek 5 $) TEK deneyin toplamıdır,
komut başına değil. İKİ FAZLI muhasebe (Codex 2. tur): ücretli sürücü çağrıdan
ÖNCE kalan tavanı `reserve()` ile KALICI olarak ayırır (dosyaya yazılır);
kapanışta `settle()` ile taahhüt edilen gerçek maliyet (settle edilmemiş tavan
yakımları dahil, muhafazakâr) işlenir ve rezervasyon kapanır. Süreç ortada
ölürse rezervasyon AÇIK kalır: sonraki komutlar o tutarı harcanmış sayar ve
tavan tükenirse fail-closed durur; insan `reconcile()` ile kapatır.
Dosya mühürlüdür; bozuk mühür fail-closed hatadır.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from app.core.benchmark.reference_characterization import (
    CharacterizationError, seal, verify_seal,
)

LEDGER_PATH = Path("benchmark/faz1_budget_ledger.json")


class _FileLock:
    """O_EXCL kilit dosyasi (Linux/Windows). Cokmede kalan `.lock` elle silinir."""

    def __init__(self, path: Path, timeout_s: float = 60.0):
        self.path = Path(str(path) + ".lock")
        self.timeout_s = timeout_s

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise CharacterizationError(
                        f"defter kilidi alinamadi: {self.path} (cokmus surecin kilidi mi?)")
                time.sleep(0.1)

    def __exit__(self, *exc):
        os.close(self.fd)
        os.unlink(self.path)


class Faz1Ledger:
    def __init__(self, path: Path, doc: Dict[str, Any]):
        self.path = Path(path)
        self.doc = doc
        self.doc.setdefault("spent_usd", {})
        self.doc.setdefault("pending", {})
        self.doc.setdefault("runs", [])

    @classmethod
    def open(cls, path: Path = LEDGER_PATH) -> "Faz1Ledger":
        path = Path(path)
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            verify_seal(doc)                       # bozuk defter -> dur
            return cls(path, doc)
        return cls(path, {"kind": "faz1_budget_ledger",
                          "created_at": datetime.now(timezone.utc).isoformat()})

    def _reload(self) -> None:
        """Paralel surecler: her degisiklikten ONCE diskteki guncel defter okunur
        (aksi halde bir surecin settle'i digerinin rezervasyonunu ezer)."""
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            verify_seal(doc)
            self.doc = doc
            self.doc.setdefault("spent_usd", {})
            self.doc.setdefault("pending", {})
            self.doc.setdefault("runs", [])

    # ── okuma ──
    def spent(self, provider: str) -> float:
        return float(self.doc["spent_usd"].get(provider, 0.0))

    def pending_usd(self, provider: str, exclude_ref: Optional[str] = None) -> float:
        return float(sum(float(p["caps"].get(provider, 0.0))
                         for ref, p in self.doc["pending"].items()
                         if ref != exclude_ref))

    def remaining(self, caps: Mapping[str, float],
                  exclude_ref: Optional[str] = None,
                  total_caps: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
        """{provider: min(koşu tavanı, DENEY tavanı − harcanan − açık rezervasyon)}.

        `caps` = bu koşunun istediği tavan (K7'nin bir parçası olabilir);
        `total_caps` = deneyin K7 toplamı (verilmezse `caps` toplam sayılır).
        Biri tükenmişse çağrı YAPILMADAN hata."""
        total = total_caps or caps
        out = {}
        for prov, cap in caps.items():
            avail = float(total.get(prov, cap)) - self.spent(prov) - self.pending_usd(prov, exclude_ref)
            rem = min(float(cap), avail)
            if rem <= 0:                           # yuvarlama YOK: 1e-9 tavan gecerli
                raise CharacterizationError(
                    f"Faz 1 toplam tavanı tükendi: {prov} harcanan "
                    f"{self.spent(prov):.4f} $ + açık rezervasyon "
                    f"{self.pending_usd(prov, exclude_ref):.4f} $ ≥ deney tavanı "
                    f"{total.get(prov, cap)} $ (koşu isteği {cap} $)")
            out[prov] = rem
        return out

    # ── iki faz ──
    def reserve(self, *, kind: str, ref: str, caps: Mapping[str, float],
                total_caps: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
        """Çağrıdan ÖNCE: kalan tavan bu `ref` adına KALICI ayrılır (dosyaya yazılır)."""
        with _FileLock(self.path):
            self._reload()
            if ref in self.doc["pending"]:
                raise CharacterizationError(
                    f"'{ref}' için AÇIK rezervasyon var — önce settle/reconcile edilmeli")
            rem = self.remaining(caps, total_caps=total_caps)
            self.doc["pending"][ref] = {"kind": kind, "caps": dict(rem),
                                        "at": datetime.now(timezone.utc).isoformat()}
            self.save()
        return rem

    def settle(self, *, ref: str,
               budget_summary: Optional[Mapping[str, Any]]) -> Dict[str, float]:
        """Kapanışta: rezervasyon düşer, taahhüt edilen maliyet deftere girer, dosya yazılır."""
        with _FileLock(self.path):
            self._reload()
            if ref not in self.doc["pending"]:
                raise CharacterizationError(f"'{ref}' için açık rezervasyon yok")
            kind = self.doc["pending"][ref]["kind"]
            added = self.record(kind=kind, ref=ref, budget_summary=budget_summary)
            del self.doc["pending"][ref]
            self.save()
        return added

    def reconcile(self, *, ref: str, actual_usd: Mapping[str, float]) -> Dict[str, float]:
        """Çökmüş (açık kalan) rezervasyonu insan kararıyla gerçek tutara kapatır."""
        summary = {p: {"cost_committed_usd": float(v)} for p, v in actual_usd.items()}
        return self.settle(ref=ref, budget_summary=summary)

    def record(self, *, kind: str, ref: str,
               budget_summary: Optional[Mapping[str, Any]]) -> Dict[str, float]:
        """BudgetGuardedAI.summary() ({provider: {...}}) → taahhüt deftere eklenir (kaydetmez)."""
        added: Dict[str, float] = {}
        for prov, s in (budget_summary or {}).items():
            if not isinstance(s, Mapping):
                continue
            usd = s.get("cost_committed_usd", s.get("cost_settled_usd"))
            if usd is None:
                raise CharacterizationError(f"{prov}: bütçe özeti maliyet taşımıyor")
            added[prov] = float(usd)
            self.doc["spent_usd"][prov] = round(self.spent(prov) + float(usd), 6)
        self.doc["runs"].append({"kind": kind, "ref": ref, "added_usd": added,
                                 "at": datetime.now(timezone.utc).isoformat()})
        return added

    def save(self) -> Path:
        self.doc.pop("payload_sha256", None)
        self.doc = seal(self.doc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        return self.path
