# -*- coding: utf-8 -*-
"""İddia incelemesini GÜNCEL profille yeniden değerlendirir (plan_iddia_denetimi rev.4, kural 5).

Ücretsiz ve deterministiktir: AI çağrısı YOK, içerik metni DEĞİŞMEZ, set AKTİFLEŞTİRİLMEZ.
Yalnız bulgu kayıtları yenilenir: her kanalın `current`'ı değişir, önceki `current`
`history`'ye itilir (en çok 5). Eşleştirme profildeki ifadeyi tanımıyorsa (ör. tanınmayan
eş anlam) uyarı KORUNUR — "temizlendi" yalnız mevcut eşleştirmenin tanıdığı durumdadır (N2).
Commit çağırana aittir.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.compliance.publish_review import CLAIM_CRITERION, legacy_seo_checks
from app.core.channel.brand_defense import load_grounding_facts, load_product_definition
from app.database.models import (
    AdGenerationSet,
    AdGroup,
    ContentOutput,
    Keyword,
    ScoringRun,
    SEOGeoContent,
    SocialBrief,
    SocialContent,
    SocialGenerationAttempt,
    SocialIdea,
)
from app.generators.ads.generation_sets import SET_ACTIVE, SET_DRAFT
from app.generators.ads.validators import classify_claims
from app.generators.claim_review import (
    failure_text,
    finding_dict,
    make_current,
    open_findings,
    refresh_review,
    set_review,
    social_record,
    social_replace_review,
    with_set_review,
)

# SEO'da taranan alanlar (SEOGeoContentGenerator._CLAIM_* ile aynı)
_SEO_STR_FIELDS = ("title", "meta_description", "intro_paragraph", "internal_link_anchor", "external_link_anchor")
_SEO_LIST_FIELDS = ("subheadings", "body_sections", "bullet_points", "image_alt_texts", "faq_items")


def _flatten(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _flatten(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in _flatten(v)]
    return [str(value)]


def _key(f: dict) -> tuple:
    return (f.get("group_id"), f.get("asset_type"), f.get("asset_text"), f.get("kind"), f.get("span"))


def _count_cleared(before: List[dict], after: List[dict]) -> int:
    still = {_key(f) for f in after}
    return sum(1 for f in before if _key(f) not in still)


def _applied(record: Optional[dict]) -> list:
    return list((((record or {}).get("current")) or {}).get("applied") or [])


# ── ADS ──

def _reevaluate_rsa(db: Session, run: ScoringRun) -> Dict[str, int]:
    sets = (
        db.query(AdGenerationSet)
        .filter(
            AdGenerationSet.scoring_run_id == run.id,
            AdGenerationSet.status.in_((SET_ACTIVE, SET_DRAFT)),
            AdGenerationSet.is_stale.is_(False),
        )
        .all()
    )
    cleared = still_flagged = 0
    for gen_set in sets:
        # Üretimle AYNI kurucu: profil + o setin istekte verdiği güvenilir USP (request_snapshot)
        usp = ((gen_set.request_snapshot or {}).get("trusted_brand_usp") or "").strip()
        facts = load_grounding_facts(db, run, usp)
        prev = set_review(gen_set.warnings)
        before = open_findings(prev)
        findings: List[dict] = []
        for group in db.query(AdGroup).filter(AdGroup.generation_set_id == gen_set.id).all():
            exempt = group.target_keywords or []
            for asset_type, assets, attr in (
                ("headline", group.headlines, "headline_text"),
                ("description", group.descriptions, "description_text"),
            ):
                for asset in assets:
                    text = getattr(asset, attr)
                    found = classify_claims(text, facts, exempt)
                    findings += [
                        finding_dict(
                            f, group_id=group.id, group_name=group.group_name,
                            asset_type=asset_type, asset_text=text,
                        )
                        for f in found
                    ]
                    if found:
                        asset.validation_action = "claim_review"
                    elif asset.validation_action == "claim_review":
                        asset.validation_action = None
        if prev or findings:
            record = refresh_review(prev, make_current(findings, _applied(prev)))
            gen_set.warnings = with_set_review(gen_set.warnings, record)
        cleared += _count_cleared(before, findings)
        still_flagged += len(findings)
    return {"sets": len(sets), "cleared": cleared, "still_flagged": still_flagged}


# ── SEO ──

def _reevaluate_seo(db: Session, run: ScoringRun) -> Dict[str, int]:
    from datetime import datetime

    facts = load_grounding_facts(db, run)  # SEO üretimiyle aynı (USP yok)

    rows = (
        db.query(SEOGeoContent)
        .join(ContentOutput, SEOGeoContent.content_output_id == ContentOutput.id)
        .filter(ContentOutput.scoring_run_id == run.id)
        .all()
    )
    cleared = still_flagged = evaluated = 0
    for row in rows:
        seo_row = row.seo_compliance
        if seo_row is None:
            continue
        keyword = db.query(Keyword).filter(Keyword.id == row.keyword_id).first()
        exempt = [keyword.keyword if keyword else "", str(datetime.now().year)]
        texts: List[str] = []
        for field in (*_SEO_STR_FIELDS, *_SEO_LIST_FIELDS):
            texts.extend(_flatten(getattr(row, field, None)))
        seen, found = set(), []
        for text in texts:
            for f in classify_claims(text, facts, exempt):
                if f.raw not in seen:
                    seen.add(f.raw)
                    found.append(f)
        findings = [finding_dict(f) for f in found]

        checks = [dict(c) for c in (seo_row.checks_json or legacy_seo_checks(seo_row))]
        old = next((c for c in checks if c.get("criterion") == CLAIM_CRITERION), None)
        prev = (old or {}).get("claim_review")
        before = open_findings(prev)
        item = {
            "criterion": CLAIM_CRITERION,
            "status": "fail" if findings else "pass",
            "importance": "critical",
            "details": failure_text(findings),
        }
        if findings or prev:
            # `original` yalnız onarımın uygulandığı kayıtta kalır (history'de); yenisine kopyalanmaz
            item["claim_review"] = refresh_review(prev, make_current(findings, _applied(prev)))
        checks = [c for c in checks if c.get("criterion") != CLAIM_CRITERION] + [item]
        seo_row.checks_json = checks  # yeni liste: JSON değişimi izlenir
        evaluated += 1
        cleared += _count_cleared(before, findings)
        still_flagged += len(findings)
    return {"contents": evaluated, "cleared": cleared, "still_flagged": still_flagged}


# ── SOSYAL (brief içerikleri) ──

def _social_texts(row: SocialContent) -> List[str]:
    texts = [h.get("text") for h in (row.hooks or []) if isinstance(h, dict)]
    texts += [row.caption, row.cta_text, row.visual_suggestion]
    texts += list(row.hashtags or [])
    payload = row.format_payload if isinstance(row.format_payload, dict) else {}
    for seg in payload.get("segments") or []:
        texts += [seg.get("on_screen_text"), seg.get("voiceover")]
    for slide in payload.get("slides") or []:
        texts += [slide.get("headline"), slide.get("body")]
    for post in payload.get("posts") or []:
        texts.append(post.get("text"))
    return [t for t in texts if isinstance(t, str)]


def _brief_facts(db: Session, run: ScoringRun, brief_id: int) -> str:
    """Brief içeriklerinin üretimdeki dayanağının ÜST KÜMESİ: güncel profil listeleri +
    brief'in 'contents' attempt'lerinin snapshot'larındaki ürün tanımı ve güvenilir USP.
    Üretimde bağlam product_facts + trusted_brand_usp idi (content_flow); burada hiçbiri
    eksik kalmaz, bu yüzden re-evaluate üretimde temiz geçen içeriğe YENİ bulgu ekleyemez."""
    parts = [load_grounding_facts(db, run), load_product_definition(db, run) or ""]
    attempts = (
        db.query(SocialGenerationAttempt)
        .filter(SocialGenerationAttempt.brief_id == brief_id, SocialGenerationAttempt.stage == "contents")
        .all()
    )
    for attempt in attempts:
        request = (attempt.coverage or {}).get("request") or {}
        parts += [request.get("product_facts") or "", request.get("trusted_brand_usp") or ""]
    return "\n".join(p for p in parts if p)


def _reevaluate_social(db: Session, run: ScoringRun) -> Dict[str, int]:
    rows = (
        db.query(SocialContent)
        .join(SocialBrief, SocialContent.brief_id == SocialBrief.id)
        .filter(SocialBrief.scoring_run_id == run.id, SocialContent.is_stale.is_(False))
        .all()
    )
    cleared = still_flagged = 0
    facts_by_brief: Dict[int, str] = {}
    for row in rows:
        if row.brief_id not in facts_by_brief:
            facts_by_brief[row.brief_id] = _brief_facts(db, run, row.brief_id)
        facts = facts_by_brief[row.brief_id]
        idea = db.query(SocialIdea).filter(SocialIdea.id == row.idea_id).first()
        keyword = (
            db.query(Keyword).filter(Keyword.id == idea.keyword_id).first()
            if idea is not None and idea.keyword_id else None
        )
        exempt = [keyword.keyword] if keyword else []
        seen, found = set(), []
        for text in _social_texts(row):
            for f in classify_claims(text, facts, exempt):
                if f.raw not in seen:
                    seen.add(f.raw)
                    found.append(f)
        old_warnings = list(row.validation_warnings or [])
        prev = social_record(old_warnings)
        before = open_findings(prev)
        findings = [finding_dict(f) for f in found]
        if prev or findings:
            record = refresh_review(prev, make_current(findings, _applied(prev)))
            row.validation_warnings = social_replace_review(old_warnings, found, record)
        cleared += _count_cleared(before, findings)
        still_flagged += len(findings)
    return {"contents": len(rows), "cleared": cleared, "still_flagged": still_flagged}


def reevaluate_claims(db: Session, run: ScoringRun) -> Dict[str, Dict[str, int]]:
    """Run'ın RSA (active+draft, non-stale), SEO ve brief sosyal içeriklerini yeniden değerlendirir.

    Dayanak her kanalda ÜRETİMLE aynı kurucudan gelir (RSA: set snapshot USP'si; SEO: profil;
    sosyal: brief snapshot'ları) — re-evaluate üretimde temiz olana yeni bulgu eklemez."""
    return {
        "rsa": _reevaluate_rsa(db, run),
        "seo": _reevaluate_seo(db, run),
        "social": _reevaluate_social(db, run),
    }
