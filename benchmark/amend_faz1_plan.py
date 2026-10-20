"""Faz 1 kilidini acan patron kararlarini muhurlu plana isler (11.10.2026).

Kaynak: patron cevaplari (sohbet, 11.10): K12 sozlu izin + sistemde hassas veri
yok; konsol 8 cent / 100 $ kredi; test firmalari Hissefy + Digitus; dogruluk/
karsilastirma patronda. Kullanim (lean kokunden):
  python benchmark/amend_faz1_plan.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.benchmark.claude_bakeoff_plan import (
    assert_workspace_allowed, faz1_blockers)
from app.core.benchmark.reference_characterization import seal, verify_seal

PLAN = Path("benchmark/claude_bakeoff_plan.json")
WS = {"Hissefy": 21, "Digitus": 29}          # ws 21: onayli profil + 540 kw; ws 29: onayli + 671 kw


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    verify_seal(plan)
    plan["decisions"]["K12_document_reference"] = (
        "Patron beyani 11.10.2026: musteri izni SOZLU; sistemde yapay zekaya "
        "gonderilmesi sakincali veri yok (yazili belge referansi yok, patron karari)")
    plan["spike"]["console_reconciled"] = True        # patron: 8 cent harcanmis
    plan["credit"].update({"net_available_usd": 99.92, "expiry": "bilinmiyor",
                           "rollover": "bilinmiyor", "scope": "bilinmiyor",
                           "source": "patron beyani 11.10: 100 $ kredi, 8 cent harcanmis"})
    plan["workspaces"] = {"1A": list(WS), "1B": list(WS), "profile": list(WS), "ids": WS,
                          "note": "Patron 11.10: testler Hissefy + Digitus; GR-7/Optimice iptal"}
    plan["capacities"] = {"ADS": 60, "SEO": 30, "SOCIAL": 30}   # tum konfiglerde ayni (adalet)
    plan["scope_change"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "by": "patron",
        "text": ("Karsilastirma ve dogruluk degerlendirmesi PATRONDA: Claude yalniz sistemleri "
                 "kosar ve ciktilari teslim eder. DeepSeek hakem, patron kartlari, patron etiket "
                 "recall ve G1-G7 hukumleri bu turda KOSULMAZ (altyapi hazir, istege bagli). "
                 "1A asama replay'i de atlanir; teslim edilen: 1B konfig ciktilari + profil kiyasi."),
        "deliverables": ["G/G/G", "Aday", "G/G/C", "profile_compare"],
    }
    plan.setdefault("amendments", []).append(
        {"at": plan["scope_change"]["at"], "what": "Faz 1 kilidi acildi (K12 sozlu, kredi, ws, kapsam)"})
    plan.pop("payload_sha256", None)
    plan = seal(plan)
    blockers = faz1_blockers(plan)
    assert blockers == [], blockers
    for ws in WS.values():
        assert_workspace_allowed(plan, ws, "1B")
        assert_workspace_allowed(plan, ws, "profile")
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("plan re-sealed; blockers =", blockers, "; ws =", WS)


if __name__ == "__main__":
    main()
