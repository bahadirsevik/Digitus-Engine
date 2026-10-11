"""Faz 1 deney-geneli sağlayıcı harcama defteri (Codex Faz 1 altyapı #1).

K7 tavanları (Claude 50 / Gemini 20 / DeepSeek 5 $) TEK deneyin toplamıdır,
komut başına değil. Her ücretli sürücü (hybrid_run, profile_compare, replay)
tavanı bu defterden KALAN olarak alır ve bitişte taahhüt edilen maliyeti
(settle edilmemiş tavan yakımları dahil, muhafazakâr) deftere işler.
Dosya mühürlüdür; bozuk mühür fail-closed hatadır.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from app.core.benchmark.reference_characterization import (
    CharacterizationError, seal, verify_seal,
)

LEDGER_PATH = Path("benchmark/faz1_budget_ledger.json")


class Faz1Ledger:
    def __init__(self, path: Path, doc: Dict[str, Any]):
        self.path = Path(path)
        self.doc = doc

    @classmethod
    def open(cls, path: Path = LEDGER_PATH) -> "Faz1Ledger":
        path = Path(path)
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            verify_seal(doc)                       # bozuk defter -> dur
            return cls(path, doc)
        return cls(path, {"kind": "faz1_budget_ledger", "spent_usd": {},
                          "runs": [],
                          "created_at": datetime.now(timezone.utc).isoformat()})

    def spent(self, provider: str) -> float:
        return float(self.doc["spent_usd"].get(provider, 0.0))

    def remaining(self, caps: Mapping[str, float]) -> Dict[str, float]:
        """{provider: cap - harcanan}; biri tükenmişse çağrı YAPILMADAN hata."""
        out = {}
        for prov, cap in caps.items():
            rem = float(cap) - self.spent(prov)      # yuvarlama YOK: 1e-9 tavan da gecerli
            if rem <= 0:
                raise CharacterizationError(
                    f"Faz 1 toplam tavanı tükendi: {prov} harcanan "
                    f"{self.spent(prov):.4f} $ ≥ tavan {cap} $")
            out[prov] = rem
        return out

    def record(self, *, kind: str, ref: str,
               budget_summary: Optional[Mapping[str, Any]]) -> Dict[str, float]:
        """BudgetGuardedAI.summary() ({provider: {...}}) → taahhüt deftere eklenir."""
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
