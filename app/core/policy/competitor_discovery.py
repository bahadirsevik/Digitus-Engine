"""AI rakip keşfi: fingerprint, doğrulama, persistence ve karar çekirdeği."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.policy.competitor_policy import (
    TERM_APPROVED,
    TERM_REJECTED,
    TERM_SUGGESTED,
    normalize_competitor_url,
    set_manual_approval,
)
from app.core.site_analyzer.turkish_normalizer import normalize_turkish

DISCOVERY_SOURCE = "ai_discovery"
DISCOVERY_MAX_RAW = 15
DISCOVERY_MAX_VERIFIED = 10

DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["name", "url", "rationale"],
            },
        }
    },
    "required": ["candidates"],
}

VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "detected_name": {"type": "string"},
                    "relationship": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["url", "detected_name", "relationship", "rationale"],
            },
        }
    },
    "required": ["candidates"],
}


def _normalized_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return sorted({normalize_turkish(str(item).strip()) for item in value if str(item).strip()})


def canonical_profile_payload(workspace) -> Dict[str, Any]:
    """Discovery girdisinin canonical ve denetlenebilir payload'ı."""
    profile = dict(getattr(workspace, "profile_data", None) or {})
    return {
        "company_domain": normalize_competitor_url(getattr(workspace, "company_url", "")),
        "company_name": normalize_turkish(str(profile.get("company_name") or "")),
        "sector": normalize_turkish(str(profile.get("sector") or "")),
        "brand_summary": normalize_turkish(str(profile.get("brand_summary") or "")),
        "products": _normalized_list(profile.get("products")),
        "services": _normalized_list(profile.get("services")),
        "target_audience": normalize_turkish(str(profile.get("target_audience") or "")),
        "geo_target_id": str(getattr(workspace, "default_geo_target_id", None) or ""),
        "language_id": str(getattr(workspace, "default_language_id", None) or ""),
        "competitor_domains": sorted({
            domain
            for domain in (
                normalize_competitor_url(url)
                for url in (getattr(workspace, "competitor_urls", None) or [])[:3]
            )
            if domain
        }),
    }


def canonical_profile_fingerprint(workspace) -> str:
    encoded = json.dumps(
        canonical_profile_payload(workspace),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fresh_channel_run_count(db: Session, workspace_id: int) -> int:
    """Merkezi freshness helper'ıyla güncel, materyalize run sayısı."""
    from app.core.workspace import pool_freshness_for_run
    from app.database.models import ChannelPool, ScoringRun

    run_ids = (
        db.query(ScoringRun.id)
        .join(ChannelPool, ChannelPool.scoring_run_id == ScoringRun.id)
        .filter(ScoringRun.brand_profile_id == workspace_id)
        .distinct()
        .all()
    )
    ids = [row[0] for row in run_ids]
    runs = db.query(ScoringRun).filter(ScoringRun.id.in_(ids)).all() if ids else []
    return sum(
        1 for run in runs
        if not pool_freshness_for_run(db, run).channel_pool_stale
    )


def _profile_prompt(workspace) -> str:
    profile = canonical_profile_payload(workspace)
    return (
        "Google araması kullanarak aşağıdaki markanın Türkiye pazarındaki doğrudan "
        "ve yakın rakiplerini bul. Haber sitesi, dizin, pazar yeri, entegrasyon partneri "
        "ve markanın kendisini rakip sayma. En fazla 15 gerçek şirket döndür. Her aday "
        "için resmi ana sayfa URL'sini ver. rationale alanını TÜRKÇE yaz (1-2 cümle; "
        "kullanıcıya gösterilecek).\n\nMARKA PROFİLİ:\n"
        + json.dumps(profile, ensure_ascii=False, indent=2)
    )


def _parse_object(text: str) -> Dict[str, Any]:
    from app.core.channel.ai_json import parse_ai_json_object

    return parse_ai_json_object(text) or {}


def discover_raw_candidates(ai_service, workspace) -> Dict[str, Any]:
    """Tek-çağrı optimizasyonu, başarısızsa garantili iki-adımlı yol."""
    from app.config import settings
    from app.generators.ai_service import scoped

    ai = scoped(
        ai_service,
        "competitor_discovery",
        model=settings.COMPETITOR_DISCOVERY_MODEL,
        thinking_level="low",
    )
    prompt = _profile_prompt(workspace)
    fallback_used = False
    try:
        grounded = ai.complete_grounded(
            prompt,
            max_tokens=3500,
            response_schema=DISCOVERY_SCHEMA,
        )
        if not grounded.search_queries or not grounded.evidence_urls:
            raise RuntimeError("Grounding metadata unavailable")
        parsed = _parse_object(grounded.text)
    except Exception:
        fallback_used = True
        grounded = ai.complete_grounded(prompt, max_tokens=3500)
        extraction_prompt = (
            "Aşağıdaki grounded araştırmayı belirtilen JSON şemasına dönüştür. "
            "Metinde kanıtlanmayan şirket ekleme.\n\n" + grounded.text
        )
        parsed = _parse_object(
            ai.complete_json(
                extraction_prompt,
                max_tokens=2500,
                response_schema=DISCOVERY_SCHEMA,
            )
        )
    candidates = [
        item for item in (parsed.get("candidates") or [])
        if isinstance(item, dict)
    ][:DISCOVERY_MAX_RAW]
    return {
        "candidates": candidates,
        "search_queries": grounded.search_queries,
        "evidence_urls": grounded.evidence_urls,
        "grounding_supports": grounded.supports,
        "fallback_used": fallback_used,
    }


def _known_keys(workspace) -> tuple[set[str], set[str]]:
    domains = {
        normalize_competitor_url(getattr(workspace, "company_url", "")),
        *{
            normalize_competitor_url(url)
            for url in (getattr(workspace, "competitor_urls", None) or [])[:3]
        },
    }
    names: set[str] = set()
    for entry in getattr(workspace, "competitor_terms", None) or []:
        name = normalize_turkish(str(entry.get("term") or ""))
        domain = str(entry.get("candidate_domain") or "")
        if name:
            names.add(name)
        if domain:
            domains.add(domain)
    domains.discard("")
    return domains, names


def _domain_label(domain: str) -> str:
    return (domain.split(".", 1)[0] if domain else "").replace("-", " ")


def _name_domain_match(name: str, domain: str) -> bool:
    name_tokens = set(normalize_turkish(name).split())
    domain_tokens = set(normalize_turkish(_domain_label(domain)).split())
    return bool(name_tokens and domain_tokens and (name_tokens & domain_tokens))


def _candidate_evidence_urls(
    name: str,
    domain: str,
    supports: Sequence[Dict[str, Any]],
) -> List[str]:
    """Return only sources attached to a segment naming this candidate."""
    normalized_name = normalize_turkish(name)
    normalized_domain = normalize_turkish(domain)
    domain_label = normalize_turkish(_domain_label(domain))
    matched: List[str] = []
    for support in supports:
        text = normalize_turkish(str(support.get("text") or ""))
        if not text:
            continue
        candidate_named = (
            (normalized_domain and normalized_domain in text)
            or (len(normalized_name) >= 4 and normalized_name in text)
            or (len(domain_label) >= 4 and domain_label in text)
        )
        if not candidate_named:
            continue
        for url in support.get("evidence_urls") or []:
            value = str(url).strip()
            if value and value not in matched:
                matched.append(value)
    return matched


def verify_discovered_candidates(
    ai_service,
    workspace,
    raw: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """SSRF güvenli fetch + tek structured ilişki doğrulaması."""
    from app.core.url_guard import safe_fetch_html
    from app.generators.ai_service import scoped

    known_domains, known_names = _known_keys(workspace)
    fetched: List[Dict[str, str]] = []
    seen_domains: set[str] = set()
    for item in raw.get("candidates") or []:
        url = str(item.get("url") or "").strip()
        domain = normalize_competitor_url(url)
        proposed_name = str(item.get("name") or "").strip()
        name_key = normalize_turkish(proposed_name)
        if (
            not domain
            or domain in known_domains
            or domain in seen_domains
            or (name_key and name_key in known_names)
        ):
            continue
        try:
            html = safe_fetch_html(url)
        except Exception:
            continue
        soup = BeautifulSoup(html, "lxml")
        title = (soup.title.string or "").strip() if soup.title else ""
        body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:2500]
        if not body:
            continue
        seen_domains.add(domain)
        fetched.append({
            "url": url,
            "domain": domain,
            "proposed_name": proposed_name,
            "discovery_rationale": str(item.get("rationale") or "").strip(),
            "title": title,
            "content": body,
        })

    if not fetched:
        return []
    profile = canonical_profile_payload(workspace)
    prompt = (
        "Ana marka ile aday siteleri karşılaştır. Her aday için resmi marka adını "
        "ve ilişkiyi yalnız direct, nearby veya not_competitor olarak ver. Satıcı, "
        "haber sitesi, dizin ve partner not_competitor olmalıdır. rationale alanını "
        "TÜRKÇE yaz (1-2 cümle; kullanıcıya gösterilecek).\n\nANA MARKA:\n"
        + json.dumps(profile, ensure_ascii=False)
        + "\n\nADAY SİTELER:\n"
        + json.dumps(fetched, ensure_ascii=False)
    )
    ai = scoped(ai_service, "competitor_preview")
    parsed = _parse_object(
        ai.complete_json(prompt, max_tokens=3000, response_schema=VERIFICATION_SCHEMA)
    )
    by_domain = {
        normalize_competitor_url(item.get("url", "")): item
        for item in (parsed.get("candidates") or [])
        if isinstance(item, dict)
    }
    grounding_supports = [
        item for item in (raw.get("grounding_supports") or [])
        if isinstance(item, dict)
    ]
    verified: List[Dict[str, Any]] = []
    for source in fetched:
        decision = by_domain.get(source["domain"], {})
        relationship = str(decision.get("relationship") or "").lower()
        if relationship not in ("direct", "nearby"):
            continue
        name = str(decision.get("detected_name") or source["proposed_name"]).strip()
        if not name:
            continue
        # Chunk URL'si redirect olsa bile support segmenti hangi aday cümlesini
        # desteklediğini taşır. Başka bir adayın kanıtı bu adayı high yapamaz.
        evidence_urls = _candidate_evidence_urls(
            source["proposed_name"], source["domain"], grounding_supports
        )
        grounded = bool(raw.get("search_queries") and evidence_urls)
        confidence = (
            "high"
            if relationship == "direct" and grounded and _name_domain_match(name, source["domain"])
            else "medium"
        )
        verified.append({
            "discovery_id": str(uuid.uuid4()),
            "term": name,
            "candidate_domain": source["domain"],
            "candidate_url": source["url"],
            "rationale": str(decision.get("rationale") or source["discovery_rationale"]),
            "confidence": confidence,
            "verification_status": relationship,
            "evidence_urls": evidence_urls[:10],
        })
    verified.sort(key=lambda item: (0 if item["confidence"] == "high" else 1, item["term"]))
    return verified[:DISCOVERY_MAX_VERIFIED]


def persist_suggestions(workspace, candidates: Sequence[Dict[str, Any]], fingerprint: str) -> List[dict]:
    """Yeni suggested setini yazar; onay/red hafızasını korur."""
    now = datetime.now(timezone.utc).isoformat()
    retained = [
        dict(entry)
        for entry in (workspace.competitor_terms or [])
        if not (
            entry.get("source") == DISCOVERY_SOURCE
            and entry.get("status") == TERM_SUGGESTED
        )
    ]
    workspace.competitor_terms = retained
    known_domains, known_names = _known_keys(workspace)
    added: List[dict] = []
    for candidate in candidates:
        domain = str(candidate.get("candidate_domain") or "")
        name_key = normalize_turkish(str(candidate.get("term") or ""))
        if not domain or domain in known_domains or not name_key or name_key in known_names:
            continue
        entry = {
            **candidate,
            "source": DISCOVERY_SOURCE,
            "status": TERM_SUGGESTED,
            "source_urls": [],
            "manual_approved": False,
            "discovered_at": now,
            "profile_fingerprint": fingerprint,
        }
        added.append(entry)
        known_domains.add(domain)
        known_names.add(name_key)
    workspace.competitor_terms = [*retained, *added]
    return added


def _merge_discovery_metadata(target: dict, candidate: dict, *, approved: bool) -> dict:
    field = "discovery_matches" if approved else "discovery_rejections"
    history = list(target.get(field) or [])
    history.append({
        key: candidate.get(key)
        for key in (
            "discovery_id", "candidate_domain", "candidate_url", "confidence",
            "verification_status", "evidence_urls", "profile_fingerprint",
        )
    })
    return {**target, field: history}


def apply_competitor_term_decisions(
    db: Session,
    workspace,
    decisions: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Competitor kararlarının tek yazma kapısı; commit çağırana aittir."""
    from app.core.policy.review import policy_effect_snapshot
    from app.core.scoring.state_machine import invalidate_workspace_outputs

    normalized_requested: List[str] = []
    for decision in decisions:
        term = str(decision.get("term") or "").strip()
        if not term:
            raise ValueError("COMPETITOR_TERM_EMPTY")
        normalized_requested.append(normalize_turkish(term))
    if len(normalized_requested) != len(set(normalized_requested)):
        raise ValueError("DUPLICATE_COMPETITOR_TERM")

    before = policy_effect_snapshot(workspace)
    entries = [dict(entry) for entry in (workspace.competitor_terms or [])]
    for decision in decisions:
        discovery_id = decision.get("discovery_id")
        approved = decision.get("decision") == "approved"
        term = str(decision.get("term") or "").strip()
        if not discovery_id:
            entries = set_manual_approval(entries, term, approved)
            continue
        index = next(
            (i for i, entry in enumerate(entries) if entry.get("discovery_id") == discovery_id),
            None,
        )
        if index is None:
            raise LookupError("DISCOVERY_NOT_FOUND")
        candidate = dict(entries[index])
        collision = next(
            (
                i for i, entry in enumerate(entries)
                if i != index and normalize_turkish(str(entry.get("term") or ""))
                == normalize_turkish(term)
            ),
            None,
        )
        if collision is not None:
            existing = _merge_discovery_metadata(
                entries[collision], candidate, approved=approved
            )
            entries[collision] = (
                set_manual_approval([existing], term, True)[0]
                if approved else existing
            )
            entries.pop(index)
            continue
        candidate["term"] = term
        candidate["manual_approved"] = approved
        candidate["status"] = TERM_APPROVED if approved else TERM_REJECTED
        candidate["decided_at"] = datetime.now(timezone.utc).isoformat()
        entries[index] = candidate

    workspace.competitor_terms = entries
    policy_changed = policy_effect_snapshot(workspace) != before
    stale_outputs = 0
    if policy_changed:
        workspace.policy_version = int(workspace.policy_version or 1) + 1
        stale_outputs = invalidate_workspace_outputs(db, workspace.id)
    return {
        "policy_changed": policy_changed,
        "policy_version": workspace.policy_version,
        "stale_outputs": stale_outputs,
    }
