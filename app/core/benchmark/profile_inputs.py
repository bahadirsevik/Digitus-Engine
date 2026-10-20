# -*- coding: utf-8 -*-
"""Dondurulmuş profil girdileri — plan_claude_gemini_karsilastirma.md Faz 0 madde 6b.

Crawl BİR KEZ yapılır ve diske yazılır; Gemini ve Claude koşuları aynı literal
site_content + preliminary_info ile aynı prompt'u görür (canlı site değişse de).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

PROFILE_INPUTS_DIR = Path("benchmark/profile_inputs")


def _sha(site_content: str, preliminary_info: Optional[str]) -> str:
    return hashlib.sha256(
        (site_content + "\n" + (preliminary_info or "")).encode("utf-8")).hexdigest()


def _path(workspace_id: int) -> Path:
    return PROFILE_INPUTS_DIR / f"{int(workspace_id)}.json"


def _default_crawl(company_url: str) -> Dict[str, Any]:
    from app.core.site_analyzer.profile_extractor import ProfileExtractor

    return ProfileExtractor(None).crawl_for_profile_content(company_url)


def freeze_profile_input(workspace_id: int, *, company_url: str,
                         preliminary_info: Optional[str],
                         crawl: Callable[[str], Dict[str, Any]] = _default_crawl,
                         overwrite: bool = False) -> Path:
    path = _path(workspace_id)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} zaten var (overwrite=True gerekir)")
    result = crawl(company_url)
    if result.get("error") or not (result.get("site_content") or "").strip():
        raise ValueError(f"crawl başarısız, hiçbir şey yazılmadı: {result.get('error')}")
    content = result["site_content"]
    doc = {
        "workspace_id": int(workspace_id),
        "company_url": company_url,
        "preliminary_info": preliminary_info,
        "site_content": content,
        "source_pages": result.get("source_pages"),
        "content_quality": result.get("content_quality"),
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": _sha(content, preliminary_info),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb" if overwrite else "xb") as fh:
        fh.write((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


def load_profile_input(workspace_id: int) -> Dict[str, Any]:
    path = _path(workspace_id)
    if not path.exists():
        raise FileNotFoundError(str(path))
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("input_sha256") != _sha(doc.get("site_content") or "",
                                       doc.get("preliminary_info")):
        raise ValueError(f"{path}: input_sha256 uyuşmuyor (dosya değişmiş)")
    return doc


def extract_profile_frozen(extractor, frozen: Dict[str, Any]) -> Dict[str, Any]:
    return extractor.extract_profile_from_site_content(
        frozen["site_content"], frozen["preliminary_info"])


def build_profile_prompt(frozen: Dict[str, Any]) -> str:
    """extract_profile_from_site_content'in gönderdiği prompt (kırpma YOK; aynısı)."""
    from app.core.site_analyzer.profile_extractor import PROFILE_EXTRACTION_PROMPT

    return PROFILE_EXTRACTION_PROMPT.format(
        site_content=frozen["site_content"],
        preliminary_info=frozen["preliminary_info"]
        or "(kullanıcı tarafından sağlanmadı)",
    )
