"""Dayanıklı AI JSON liste parser'ı.

Gemini bazen truncated / kaçışsız tırnaklı / markdown fence'li / dengesiz
parantezli JSON döndürür. Bu modül `intent_analyzer`'daki kanıtlanmış
kurtarma mantığını tek yerde toplar.

NOT: Şimdilik yalnızca `brand_filter` bu helper'ı kullanır (plan5 Faz A1).
`intent_analyzer` migrasyonu ayrı bir iştir; çalışan intent parser'ı bu
fazda ellenmez.

Temel kurtarma stratejisi:
  1. Fence temizliği + akıllı tırnak + trailing comma normalizasyonu.
  2. Birden fazla kesim adayı (tam metin, ilk `[`/`{`'ten sonu, sona kadar).
  3. Her aday için ham + dengesiz-parantez-kapatılmış deneme.
  4. Hepsi başarısızsa: obje obje `raw_decode` ile KISMİ liste çıkarımı —
     truncated array'de kırılma noktasına kadarki tüm tam objeleri kurtarır.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence


class AIJsonParseError(ValueError):
    """AI yanıtından hiçbir JSON objesi/listesi kurtarılamadı."""


def _normalize(text: str) -> str:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    text = text.replace("﻿", "")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _close_unbalanced_json(text: str) -> str:
    s = text.strip()
    if not s:
        return s
    if s.count("[") > s.count("]"):
        s += "]" * (s.count("[") - s.count("]"))
    if s.count("{") > s.count("}"):
        s += "}" * (s.count("{") - s.count("}"))
    return s


def _extract_object_list(text: str) -> List[Dict[str, Any]]:
    """Metindeki tüm tam JSON objelerini sırayla çıkar (kısmi kurtarma).

    Truncated bir array'de son (bozuk) objeye kadarki tüm geçerli objeleri
    kurtarır — 'Unterminated string' senaryosunun asıl çözümü budur.
    """
    decoder = json.JSONDecoder()
    out: List[Dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
            if isinstance(obj, dict):
                out.append(obj)
            i += end
        except Exception:
            i += 1
    return out


def parse_ai_json_list(
    raw: str,
    *,
    result_keys: tuple = ("results", "keywords"),
    allow_partial: bool = True,
) -> List[Dict[str, Any]]:
    """AI yanıtından dict listesi çıkarır.

    Args:
        raw: Ham AI yanıtı.
        result_keys: Yanıt bir dict ise liste bu anahtarların altında aranır.
        allow_partial: True ise, tam parse başarısız olduğunda obje-obje
            kısmi kurtarma denenir.

    Returns:
        Dict listesi (kurtarılabilen objeler). Boş liste dönebilir.

    Raises:
        AIJsonParseError: yanıt boş ise veya hiçbir şey kurtarılamazsa
            (allow_partial=False iken).
    """
    if not raw or not raw.strip():
        raise AIJsonParseError("AI boş yanıt döndürdü")

    text = _normalize(raw)

    candidates = [text]
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    ends = [i for i in (text.rfind("]"), text.rfind("}")) if i >= 0]
    if starts:
        candidates.append(text[min(starts):])
    if ends:
        candidates.append(text[: max(ends) + 1])
    if starts and ends and max(ends) >= min(starts):
        candidates.append(text[min(starts): max(ends) + 1])

    seen = set()
    uniq = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)

    for cand in uniq:
        for attempt in (cand, _close_unbalanced_json(cand)):
            try:
                data = json.loads(attempt)
            except Exception:
                continue
            if isinstance(data, dict):
                for key in result_keys:
                    if isinstance(data.get(key), list):
                        return [x for x in data[key] if isinstance(x, dict)]
                # Tek obje → tek elemanlı liste
                return [data]
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]

    if allow_partial:
        extracted = _extract_object_list(text)
        # results:[...] sarmalı truncated ise iç objeler doğrudan çıkar
        if extracted:
            return extracted

    raise AIJsonParseError("AI yanıtından JSON listesi kurtarılamadı")


def parse_ai_json_object(
    raw: str,
    required_fields: Sequence[str] = (),
) -> Dict[str, Any]:
    """AI yanıtından TEK içerik objesi çıkarır (SEO generator deseninin geneli).

    Sıra: düz json.loads (+ zorunlu alan kontrolü) → kurtarma zinciri
    (`parse_ai_json_list`) adayları içinden zorunlu alanların TAMAMINI
    taşıyan ilk obje.

    Sözleşme: parse başarılı ama zorunlu alan eksikse "kurtarıldı" SAYILMAZ —
    AIJsonParseError atılır. Sessiz eksik-veri üretimi yasak.
    """
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and all(f in parsed for f in required_fields):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        candidates = parse_ai_json_list(raw, result_keys=())
    except AIJsonParseError:
        candidates = []

    for cand in candidates:
        if all(field in cand for field in required_fields):
            return cand

    raise AIJsonParseError(
        "AI yanıtından zorunlu alanları içeren JSON objesi kurtarılamadı"
    )
