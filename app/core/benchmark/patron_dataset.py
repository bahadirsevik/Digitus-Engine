"""Optimice importer'ının doğrulama yardımcıları (Codex v18-5, testlenebilir).

- strict_number: bozuk sayı SESSİZCE default'a düşmez — satır/alan bağlamıyla
  hata (resmî araç sessiz geçemez).
- parse_mark: YALNIZ 'x' (büyük/küçük) pozitiftir; başka dolu değer hatadır
  (yanlışlıkla kaydırılmış kolon/yorum hücresi pozitif sayılamaz).
- trend_scale_evidence: ×100 tam-sayı oranı + kesir-aralık sinyali. Bu bir
  HEURİSTİKTİR, matematiksel kesin kanıt değildir (Codex v18-5 uyarısı
  dokümante): iki bağımsız sinyal birden sağlanmadan geçmez.
- stable_imported_at: içerik (SHA) değişmediyse önceki imported_at KORUNUR —
  idempotent yeniden yazım, daha önce geçerli olan run'ı sonradan
  geçersizleştiremez.
"""
from __future__ import annotations

from typing import List, Optional


class DatasetError(ValueError):
    """İçe aktarma doğrulama hatası (satır/alan bağlamlı)."""


# Google Keyword Planner, bir yıl önce ~0 hacimli kelimelerde büyümeyi
# '∞' olarak basar. Deterministik eşleme: skorlama zaten trendi [-1,+3]
# oranına kırptığı için ∞ ≡ clamp tavanı (+%300 → kesir 3.0). Eski kod
# bunu SESSİZCE 0 yapıyordu (Codex v18-5 haklıydı — strict parse yakaladı).
INFINITY_TREND_FRACTION = 3.0


def strict_number(value, *, row: int, field: str,
                  allow_none: bool = False,
                  infinity_as: Optional[float] = None) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_none:
            return None
        raise DatasetError(f"satır {row}: {field} boş (sayı şart)")
    if isinstance(value, str) and value.strip() == "∞":
        if infinity_as is not None:
            return infinity_as
        raise DatasetError(
            f"satır {row}: {field}='∞' bu alan için tanımsız"
        )
    try:
        return float(value)
    except (TypeError, ValueError):
        raise DatasetError(
            f"satır {row}: {field}={value!r} sayıya çevrilemedi "
            f"(sessiz default YOK)"
        )


def parse_mark(value, *, row: int, channel: str) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if text.lower() == "x":
        return True
    raise DatasetError(
        f"satır {row}: {channel} kolonunda beklenmeyen işaret {text!r} "
        f"(yalnız 'x' geçerli — kaymış kolon/yorum hücresi olabilir)"
    )


def trend_scale_evidence(values: List[float]) -> dict:
    """İki sinyalli heuristik (kesin kanıt DEĞİL, dokümante):
    S1: değerlerin ≥%95'i ×100'de tam sayı (Google tam yüzde raporlar).
    S2: sıfır-dışı değerlerin ≥%30'u |v|<1 bandında (yüzde okumasında
        '%0.xx'lik değişim' anlamına gelirdi — Google böyle raporlamaz).
    İkisi birden sağlanırsa kesir kabul edilir (×100 uygulanır)."""
    if not values:
        return {"ok": False, "reason": "değer yok"}
    integral = sum(1 for v in values if abs(v * 100 - round(v * 100)) < 1e-6)
    s1 = integral / len(values)
    nonzero = [v for v in values if v != 0]
    sub_one = (sum(1 for v in nonzero if abs(v) < 1) / len(nonzero)
               if nonzero else 0.0)
    ok = s1 >= 0.95 and sub_one >= 0.30
    return {"ok": ok, "integral_ratio": round(s1, 4),
            "sub_one_ratio": round(sub_one, 4),
            "n": len(values)}


def stable_imported_at(existing_doc: Optional[dict], final_sha: str,
                       raw_sha: str, now_iso: str) -> str:
    """İçerik aynıysa önceki damga korunur (Codex v18-5d)."""
    if (existing_doc
            and existing_doc.get("source_final_xlsx_sha256") == final_sha
            and existing_doc.get("source_raw_xlsx_sha256") == raw_sha
            and existing_doc.get("imported_at")):
        return existing_doc["imported_at"]
    return now_iso
