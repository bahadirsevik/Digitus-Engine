"""Claude bake-off içerik karşılaştırma seti (plan K4/K17, §7.3, §7.5) — kör çift üretimi.

İki hibrit koşunun (referans G/G/G, aday) AYNI workspace'teki içeriklerini formata göre
çıkarır, ortak katmanı (K17: ortak / yalnız-ref / yalnız-aday) bulur, provider izlerini
temizleyip mühürlü tohumla kör A/B çiftleri üretir. Patron kartları + açma (unblind) burada;
DeepSeek hakemi `pairwise_judge.py`'de.

Eşleme anahtarları (formata göre, en kararlı olan):
  - rsa:    sıralı normalize keyword demeti (grubun hedef kelimeleri). Grup/tema adı AI
            üretimidir, iki sağlayıcıda farklı çıkar -> anahtar OLAMAZ.
  - blog:   keyword_id (SEO havuzundaki kelime başına bir içerik; son üretim kazanır).
  - social: (idea.keyword normalize, platform, format, sıra no). Kategori adı / fikir başlığı
            AI üretimidir (iki sağlayıcıda eşleşmez -> ortak katman boşalırdı). Sıra no =
            aynı (kelime, hedef) altındaki fikirlerin id sırası; EŞLEME POZİSYONELDİR, anlamsal
            değil ("aynı kelime+hedef için N. fikir"). Bu bilinen sınırdır (rapora yazılır).

Sert kurallar (K4 (1)) gerçek doğrulayıcılarla, ham metin üzerinde hesaplanır (normalize
EDİLMEMİŞ): RSA başlık<=30 / açıklama<=90 / en az 3+2 öğe / `find_ungrounded_claims`==0
(girdi: `grounding_facts` = confirmed product_facts [+ istekte verilen USP]; çağıran parametre
olarak verir, bkz. `ads_generator._grounding_context`); blog zorunlu alanlar; sosyal
`validate_social_content_output` (format_payload dahil, brief hedefinden ContentTargetSpec).
"""
from __future__ import annotations

import random
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.core.keyword_normalize import normalize_keyword

FORMATS = ("rsa", "blog", "social")
RSA_HEADLINE_MAX, RSA_DESCRIPTION_MAX = 30, 90
RSA_MIN_HEADLINES, RSA_MIN_DESCRIPTIONS = 3, 2   # rsa_generator.RSAGenerator.MIN_* ile aynı
BLOG_REQUIRED = ("title", "intro_paragraph", "body_content", "meta_description")
META_MAX = 160

# §7.3 rubriği (ağırlıklar patron tarafından dondurulur; burada yalnız form alanları)
RUBRIC_CORE = (
    ("marka_sadakati", "Marka/ürün sadakati", 3),
    ("turkce_dogallik", "Türkçe doğallık", 2),
    ("kelime_cta_uyum", "Anahtar kelime/CTA hedefine uyum", 2),
    ("ozgunluk", "Özgünlük", 1),
)
RUBRIC_EXTRA = {
    "blog": (("bilgi_yeterliligi", "Bilgi yeterliliği", 2), ("yapi", "Yapı", 2),
             ("arama_niyeti", "Arama niyeti", 2)),
    "rsa": (("baslik_cesitliligi", "Başlık çeşitliliği", 2), ("mesaj_kapsami", "Mesaj kapsamı", 2)),
    "social": (("hook", "Hook", 2), ("uygulanabilirlik", "Uygulanabilirlik", 2),
               ("format_uygunlugu", "Format uygunluğu", 2)),
}


def rubric_dims(fmt: str) -> Tuple[Tuple[str, str, int], ...]:
    if fmt not in RUBRIC_EXTRA:
        raise ValueError(f"bilinmeyen format: {fmt!r}")
    return RUBRIC_CORE + RUBRIC_EXTRA[fmt]


class ContentPairError(ValueError):
    pass


# ── yardımcılar ─────────────────────────────────────────────────────────

def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _key_str(key: Any) -> str:
    return "|".join(map(str, key)) if isinstance(key, tuple) else str(key)


def _item(fmt: str, key: Any, label: str, ws: Any, blocks: List[Tuple[str, str]],
          violations: List[str], meta: Dict[str, Any]) -> Dict[str, Any]:
    return {"format": fmt, "key": key, "key_str": f"{ws}|{_key_str(key)}", "key_label": label,
            "ws": ws, "text_blocks": [(a, b) for a, b in blocks if b and b.strip()],
            "hard_rule_ok": not violations, "hard_rule_violations": violations, "meta": meta}


# ── öğe kurucuları (saf; DB satırından dict'e çevrilmiş girdi) ───────────

def build_rsa_item(group: Mapping[str, Any], *, ws: Any, grounding_facts: str = "") -> Dict[str, Any]:
    """group: {group_id, group_name, target_keywords, headlines: [str], descriptions: [str]}
    (headlines/descriptions sort_order sırasında)."""
    from app.generators.ads.validators import find_ungrounded_claims

    kws = [k for k in (group.get("target_keywords") or []) if _s(k).strip()]
    key: Any = tuple(sorted(normalize_keyword(_s(k)) for k in kws))
    meta: Dict[str, Any] = {"group_id": group.get("group_id")}
    if not key:  # fail-closed: anahtarsız grup yine listelenir, ama işaretli
        key = (normalize_keyword(_s(group.get("group_name"))),)
        meta["key_fallback"] = "group_name"
    heads = [_s(h) for h in group.get("headlines") or []]
    descs = [_s(d) for d in group.get("descriptions") or []]
    v: List[str] = []
    if len(heads) < RSA_MIN_HEADLINES:
        v.append(f"headlines<{RSA_MIN_HEADLINES}")
    if len(descs) < RSA_MIN_DESCRIPTIONS:
        v.append(f"descriptions<{RSA_MIN_DESCRIPTIONS}")
    for i, h in enumerate(heads, 1):
        if len(h) > RSA_HEADLINE_MAX:
            v.append(f"headline{i}>{RSA_HEADLINE_MAX}")
    for i, d in enumerate(descs, 1):
        if len(d) > RSA_DESCRIPTION_MAX:
            v.append(f"description{i}>{RSA_DESCRIPTION_MAX}")
    for i, t in enumerate(heads, 1):
        if find_ungrounded_claims(t, grounding_facts, kws):
            v.append(f"headline{i}:ungrounded_claim")
    for i, t in enumerate(descs, 1):
        if find_ungrounded_claims(t, grounding_facts, kws):
            v.append(f"description{i}:ungrounded_claim")
    blocks = ([(f"Başlık {i}", h) for i, h in enumerate(heads, 1)]
              + [(f"Açıklama {i}", d) for i, d in enumerate(descs, 1)])
    return _item("rsa", key, ", ".join(key), ws, blocks, v, meta)


def build_blog_item(row: Mapping[str, Any], *, ws: Any) -> Dict[str, Any]:
    """row: SEOGeoContent alanları + keyword_text."""
    v = [f"missing:{f}" for f in BLOG_REQUIRED if not _s(row.get(f)).strip()]
    if len(_s(row.get("meta_description"))) > META_MAX:
        v.append(f"meta_description>{META_MAX}")
    subs = row.get("subheadings") or []
    faq = row.get("faq_items") or []
    blocks = [
        ("Başlık", _s(row.get("title"))), ("Meta açıklama", _s(row.get("meta_description"))),
        ("Giriş", _s(row.get("intro_paragraph"))),
        ("Alt başlıklar", "\n".join(f"- {_s(x)}" for x in subs)),
        ("Gövde", _s(row.get("body_content"))),
        ("SSS", "\n".join(f"S: {_s(x.get('question'))}\nC: {_s(x.get('answer'))}"
                          for x in faq if isinstance(x, Mapping))),
    ]
    kid = row["keyword_id"]
    return _item("blog", kid, _s(row.get("keyword_text")) or str(kid), ws, blocks, v,
                 {"seo_geo_content_id": row.get("id")})


def _payload_summary(fp: Any) -> str:
    if not isinstance(fp, Mapping):
        return ""
    kind = fp.get("kind")
    if kind == "video":
        return "\n".join(f"[{s.get('start_sec')}-{s.get('end_sec')} sn] sahne: {s.get('scene')} | ekran yazısı: "
                         f"{s.get('on_screen_text')} | seslendirme: {s.get('voiceover')}"
                         for s in fp.get("segments") or [] if isinstance(s, Mapping))
    if kind == "carousel":
        return "\n".join(f"Slayt {s.get('position')}: {s.get('headline')} — {s.get('body')} "
                         f"(görsel: {s.get('visual_direction')})"
                         for s in fp.get("slides") or [] if isinstance(s, Mapping))
    if kind == "thread":
        return "\n".join(f"{p.get('position')}. {p.get('text')}"
                         for p in fp.get("posts") or [] if isinstance(p, Mapping))
    return ""


def social_hard_violations(row: Mapping[str, Any], target: Mapping[str, Any]) -> List[str]:
    """validate_social_content_output ile (content_persistence ile aynı row_dict) doğrular."""
    from app.core.social.content_contract import (
        ContentTargetSpec, SocialContentOutputValidationError, validate_social_content_output)
    try:
        spec = ContentTargetSpec(
            target_id=target["id"], platform=target["platform"], content_format=target["content_format"],
            duration_preset_id=target.get("duration_preset_id"),
            duration_min_sec=target.get("duration_min_sec"), duration_max_sec=target.get("duration_max_sec"))
        raw = {k: row.get(k) for k in ("hooks", "caption", "cta_text", "hashtags", "format_payload",
                                       "visual_suggestion", "video_concept",
                                       "industry_posting_suggestion", "platform_notes")}
        validate_social_content_output(raw, spec)
        return []
    except SocialContentOutputValidationError as exc:
        return [f"social_contract:{getattr(exc, 'error_code', 'invalid')}:{getattr(exc, 'field', '') or ''}"]
    except Exception as exc:  # noqa: BLE001 — fail-closed: doğrulanamayan içerik ihlalli sayılır
        return [f"social_contract:validator_error:{type(exc).__name__}"]


def build_social_item(row: Mapping[str, Any], *, ws: Any, ordinal: int) -> Dict[str, Any]:
    """row: SocialContent alanları + idea_title/idea_description/keyword_text + target{...}."""
    tgt = row["target"]
    kw = normalize_keyword(_s(row.get("keyword_text")))
    key = (kw, tgt["platform"], tgt["content_format"], ordinal)
    hooks = row.get("hooks") or []
    blocks = [
        ("Fikir", "\n".join(x for x in (_s(row.get("idea_title")), _s(row.get("idea_description"))) if x)),
        ("Hook'lar", "\n".join(f"- {_s(h.get('text')) if isinstance(h, Mapping) else _s(h)}" for h in hooks)),
        ("Açıklama (caption)", _s(row.get("caption"))), ("CTA", _s(row.get("cta_text"))),
        ("Hashtag'ler", " ".join(_s(h) for h in row.get("hashtags") or [])),
        ("Format içeriği", _payload_summary(row.get("format_payload"))),
        ("Görsel öneri", _s(row.get("visual_suggestion"))),
    ]
    label = f"{kw} / {tgt['platform']} {tgt['content_format']} #{ordinal}"
    return _item("social", key, label, ws, blocks, social_hard_violations(row, tgt),
                 {"social_content_id": row.get("id"), "idea_id": row.get("idea_id")})


# ── DB çıkarımı (DB'li; birim testte KAPSANMAZ) ──────────────────────────

def extract_items(db: Any, scoring_run_id: int, *, ws: Any, grounding_facts: str = "",
                  formats: Sequence[str] = FORMATS) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{format: {key_str: Item}}. Tüketici kuralı: ADS yalnız `active AND is_stale=False` set;
    blog: ContentOutput.is_stale=False, kelime başına son içerik; sosyal: brief satırları
    (brief_id NOT NULL), brief/kategori/fikir/içerik non-stale. Aynı key_str çakışırsa ValueError.

    grounding_facts: `load_product_definition(db, run)` [+ istekte verilen trusted USP] — çağıran verir."""
    from sqlalchemy import func
    from app.database.models import (
        AdDescription, AdHeadline, ContentOutput, Keyword, SEOGeoContent, SocialBrief,
        SocialBriefKeyword, SocialBriefTarget, SocialCategory, SocialContent, SocialIdea)

    out: Dict[str, Dict[str, Dict[str, Any]]] = {f: {} for f in formats}

    def put(it: Dict[str, Any]) -> None:
        bucket = out[it["format"]]
        if it["key_str"] in bucket:
            raise ValueError(f"çift anahtar {it['format']}:{it['key_str']} (koşu {scoring_run_id})")
        bucket[it["key_str"]] = it

    if "rsa" in formats:
        from app.generators.ads.generation_sets import active_ad_groups
        for g in active_ad_groups(db, scoring_run_id).order_by("id").all():
            heads = (db.query(AdHeadline).filter(AdHeadline.ad_group_id == g.id)
                     .order_by(AdHeadline.sort_order, AdHeadline.id).all())
            descs = (db.query(AdDescription).filter(AdDescription.ad_group_id == g.id)
                     .order_by(AdDescription.sort_order, AdDescription.id).all())
            put(build_rsa_item({"group_id": g.id, "group_name": g.group_name,
                                "target_keywords": g.target_keywords,
                                "headlines": [h.headline_text for h in heads],
                                "descriptions": [d.description_text for d in descs]},
                               ws=ws, grounding_facts=grounding_facts))
    if "blog" in formats:
        latest = (db.query(SEOGeoContent.keyword_id, func.max(SEOGeoContent.id).label("mid"))
                  .join(ContentOutput, SEOGeoContent.content_output_id == ContentOutput.id)
                  .filter(ContentOutput.scoring_run_id == scoring_run_id,
                          ContentOutput.is_stale == False)  # noqa: E712
                  .group_by(SEOGeoContent.keyword_id).subquery())
        for c, kw in (db.query(SEOGeoContent, Keyword.keyword)
                      .join(latest, SEOGeoContent.id == latest.c.mid)
                      .join(Keyword, Keyword.id == SEOGeoContent.keyword_id)
                      .order_by(SEOGeoContent.keyword_id).all()):
            row = {k: getattr(c, k) for k in ("id", "keyword_id", "title", "meta_description",
                                              "intro_paragraph", "body_content", "subheadings", "faq_items")}
            row["keyword_text"] = kw
            put(build_blog_item(row, ws=ws))
    if "social" in formats:
        q = (db.query(SocialContent, SocialIdea, SocialBriefTarget, Keyword.keyword, SocialBrief.id)
             .join(SocialIdea, SocialContent.idea_id == SocialIdea.id)
             .join(SocialCategory, SocialIdea.category_id == SocialCategory.id)
             .join(SocialBrief, SocialContent.brief_id == SocialBrief.id)
             .join(SocialBriefTarget, SocialIdea.brief_target_id == SocialBriefTarget.id)
             .outerjoin(Keyword, Keyword.id == SocialIdea.keyword_id)
             .filter(SocialBrief.scoring_run_id == scoring_run_id, SocialBrief.is_stale == False,  # noqa: E712
                     SocialCategory.is_stale == False, SocialIdea.is_stale == False,  # noqa: E712
                     SocialContent.is_stale == False)  # noqa: E712
             .order_by(SocialIdea.id))
        counters: Dict[Tuple[str, str, str], int] = {}
        for c, idea, tgt, kw_text, brief_id in q.all():
            if not kw_text:  # fikirde keyword yoksa brief kelimeleri (ayrı sorgu; seyrek yol)
                kw_text = " ".join(r[0] for r in db.query(SocialBriefKeyword.keyword_snapshot)
                                   .filter(SocialBriefKeyword.brief_id == brief_id)
                                   .order_by(SocialBriefKeyword.position).all())
            ck = (normalize_keyword(kw_text), tgt.platform, tgt.content_format)
            counters[ck] = counters.get(ck, 0) + 1
            row = {k: getattr(c, k) for k in ("id", "idea_id", "hooks", "caption", "cta_text", "hashtags",
                                              "format_payload", "visual_suggestion", "video_concept",
                                              "industry_posting_suggestion", "platform_notes")}
            row.update(idea_title=idea.idea_title, idea_description=idea.idea_description,
                       keyword_text=kw_text,
                       target={k: getattr(tgt, k) for k in ("id", "platform", "content_format",
                                                            "duration_preset_id", "duration_min_sec",
                                                            "duration_max_sec")})
            put(build_social_item(row, ws=ws, ordinal=counters[ck]))
    return out


# ── K17 katmanları ──────────────────────────────────────────────────────

def common_layer(ref_items: Mapping[str, Mapping[str, Dict[str, Any]]],
                 cand_items: Mapping[str, Mapping[str, Dict[str, Any]]]):
    """-> (common, only_ref, only_cand); common: {fmt: [(key_str, ref_item, cand_item)]},
    only_*: {fmt: [item]}. key_str sıralı (deterministik). Birincil analiz = common."""
    common: Dict[str, list] = {}
    only_ref: Dict[str, list] = {}
    only_cand: Dict[str, list] = {}
    for fmt in sorted(set(ref_items) | set(cand_items)):
        r, c = ref_items.get(fmt, {}), cand_items.get(fmt, {})
        common[fmt] = [(k, r[k], c[k]) for k in sorted(set(r) & set(c))]
        only_ref[fmt] = [r[k] for k in sorted(set(r) - set(c))]
        only_cand[fmt] = [c[k] for k in sorted(set(c) - set(r))]
    return common, only_ref, only_cand


# ── kör normalizasyon ───────────────────────────────────────────────────

_PROVIDER_RE = re.compile(r"\b(gemini|claude|anthropic)\b", re.IGNORECASE)
_TRANS = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "‘": "'", "’": "'",
                        "‚": "'", "‛": "'", "–": "-", "—": "-", "―": "-", "−": "-",
                        "…": "...", " ": " ", "​": "", "﻿": ""})


def normalize_for_blind(text: str, strip_terms: Optional[re.Pattern] = None) -> str:
    """Her iki taraf AYNEN uygulanır: NFC, akıllı tırnak/tire/üç nokta düzleştirme, sağ boşluk
    ve çoklu boşluk temizliği, sağlayıcı/model adlarını (gemini|claude|anthropic) silme.
    NOT: 'Google' gibi meşru marka geçişleri de silinir (iki tarafta aynı); biçimsel parmak
    izleri (markdown üslubu vb.) kapsam dışıdır."""
    s = unicodedata.normalize("NFC", _s(text)).translate(_TRANS)
    s = (strip_terms or _PROVIDER_RE).sub("", s)
    s = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines())
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _blind_blocks(blocks: Iterable[Tuple[str, str]]) -> List[List[str]]:
    return [[normalize_for_blind(a), normalize_for_blind(b)] for a, b in blocks]


# ── kör çiftler ─────────────────────────────────────────────────────────

def build_blind_pairs(common: Mapping[str, Sequence[Tuple[str, Dict[str, Any], Dict[str, Any]]]], *,
                      seed: int, patron_per_format: int = 15, judge_per_format: int = 30):
    """-> (pairs_public, mapping_secret, sample_table).

    Format başına: ortak çiftler key_str sırasında alınır, Random(f"{seed}:{fmt}") ile karıştırılır;
    ilk `judge_per_format` = hakem örneği (azsa hepsi), ilk `patron_per_format` = patron alt kümesi.
    A/B tarafı çift başına rastgele ama yarı-yarıya dengeli. pairs_public provider bilgisi TAŞIMAZ;
    mapping_secret ayrı dosyaya yazılır (çağıran): {pair_id: {A, B, hard_rule_ref_ok, hard_rule_cand_ok,
    key_str, ws, format}}."""
    public: List[Dict[str, Any]] = []
    secret: Dict[str, Dict[str, Any]] = {}
    table: Dict[str, Dict[str, int]] = {}
    for fmt in sorted(common):
        trip = sorted(common[fmt], key=lambda t: t[0])
        rng = random.Random(f"{seed}:{fmt}")
        rng.shuffle(trip)
        sample = trip[:judge_per_format]
        flips = [True] * ((len(sample) + 1) // 2) + [False] * (len(sample) // 2)
        rng.shuffle(flips)
        for n, ((ks, ref, cand), cand_is_a) in enumerate(zip(sample, flips), 1):
            pid = f"P-{fmt}-{n:03d}"
            a, b = (cand, ref) if cand_is_a else (ref, cand)
            public.append({"pair_id": pid, "format": fmt, "ws": ref["ws"], "key_label": ref["key_label"],
                           "patron": n <= patron_per_format,
                           "A": _blind_blocks(a["text_blocks"]), "B": _blind_blocks(b["text_blocks"])})
            secret[pid] = {"A": "cand" if cand_is_a else "ref", "B": "ref" if cand_is_a else "cand",
                           "hard_rule_ref_ok": bool(ref["hard_rule_ok"]),
                           "hard_rule_cand_ok": bool(cand["hard_rule_ok"]),
                           "key_str": ks, "ws": ref["ws"], "format": fmt}
        table[fmt] = {"n_common": len(trip), "n_judge": len(sample),
                      "n_patron": min(patron_per_format, len(sample))}
    return public, secret, table


def itt_hard_rule_pairs(common: Mapping[str, Sequence[Tuple[str, Dict[str, Any], Dict[str, Any]]]],
                        mapping_secret: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Örneklem DIŞINDA kalan ortak çiftler için yalnız sert-kural satırları (K4 ITT: hiçbir çift
    paydadan düşmez). `evaluate_content` girdisine eklenir (patron/judge_pref=None)."""
    seen = {(m["format"], m["key_str"]) for m in mapping_secret.values()}
    return [{"format": fmt, "ws": ref["ws"], "patron_pref": None, "judge_pref": None,
             "hard_rule_cand_ok": bool(cand["hard_rule_ok"]), "hard_rule_ref_ok": bool(ref["hard_rule_ok"])}
            for fmt, trips in common.items() for ks, ref, cand in trips if (fmt, ks) not in seen]


# ── patron kartları ─────────────────────────────────────────────────────

def _score_line(dim: str, label: str, w: int) -> str:
    return f"- {label} (×{w}) [{dim}]: A=_ B=_"


def render_patron_cards(pairs_public: Sequence[Mapping[str, Any]], out_md: Optional[str] = None) -> str:
    """Yalnız `patron=True` çiftler için Markdown kart formu. out_md verilirse dosyaya (LF, UTF-8)."""
    parts = ["# Kör içerik karşılaştırma kartları\n",
             "Her çift için tek tercih işaretleyin ([x]); puanlar 1–5, boş bırakılabilir. "
             "Uzunluk puanlanmaz.\n"]
    for p in pairs_public:
        if not p.get("patron"):
            continue
        parts.append(f"\n## Çift {p['pair_id']} — {p['format']}\n")
        parts.append(f"Konu: {p['key_label']}\n")
        for side in ("A", "B"):
            parts.append(f"\n### {side}\n")
            parts.extend(f"**{lab}:**\n{txt}\n" for lab, txt in p[side])
        parts.append("\n**Tercih:** [ ] A  [ ] B  [ ] Berabere\n")
        parts.append("\n**Puanlar:**\n" + "\n".join(_score_line(*d) for d in rubric_dims(p["format"])) + "\n")
    md = "\n".join(parts)
    if out_md:
        with open(out_md, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(md)
    return md


_CARD_RE = re.compile(r"^## Çift (\S+) — (\S+)\s*$", re.MULTILINE)
_PREF_RE = re.compile(r"\[([ xX]?)\]\s*(A|B|Berabere)\b")
_SCORE_RE = re.compile(r"\[(\w+)\]:\s*A=(\S*)\s+B=(\S*)")


def _parse_score(tok: str, where: str) -> Optional[int]:
    if tok in ("", "_"):
        return None
    if not re.fullmatch(r"[1-5]", tok):
        raise ContentPairError(f"{where}: puan 1–5 olmalı, bulundu {tok!r}")
    return int(tok)


def parse_patron_cards(md: str, expected_ids: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """-> {pair_id: {"pref": "A"|"B"|"tie", "scores": {dim: {"A": int|None, "B": int|None}}}}.
    Fail-closed: tercih satırı yok / birden fazla ya da hiç işaret yok / geçersiz puan /
    kopya kart / expected_ids'den eksik kart -> ContentPairError."""
    heads = list(_CARD_RE.finditer(md))
    out: Dict[str, Dict[str, Any]] = {}
    for i, m in enumerate(heads):
        pid = m.group(1)
        body = md[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(md)]
        if pid in out:
            raise ContentPairError(f"{pid}: kart iki kez var")
        line = next((ln for ln in body.splitlines() if ln.startswith("**Tercih:**")), None)
        if line is None:
            raise ContentPairError(f"{pid}: 'Tercih' satırı yok")
        boxes = _PREF_RE.findall(line)
        if sorted(n for _, n in boxes) != ["A", "B", "Berabere"]:
            raise ContentPairError(f"{pid}: tercih satırı bozuk")
        checked = [n for c, n in boxes if c.strip()]
        if len(checked) != 1:
            raise ContentPairError(f"{pid}: tam olarak bir tercih işaretlenmeli ({len(checked)} işaret)")
        scores = {d: {"A": _parse_score(a, f"{pid}/{d}/A"), "B": _parse_score(b, f"{pid}/{d}/B")}
                  for d, a, b in _SCORE_RE.findall(body)}
        out[pid] = {"pref": {"A": "A", "B": "B", "Berabere": "tie"}[checked[0]], "scores": scores}
    if expected_ids is not None:
        missing = sorted(set(expected_ids) - set(out))
        if missing:
            raise ContentPairError(f"eksik kart(lar): {missing[:5]}{'…' if len(missing) > 5 else ''}")
    return out


def unblind(prefs_by_pair: Mapping[str, Any], mapping_secret: Mapping[str, Mapping[str, Any]]
            ) -> Dict[str, str]:
    """{pair_id: "A"|"B"|"tie" | {"pref": ...}} -> {pair_id: "cand"|"ref"|"tie"}. Bilinmeyen
    pair_id / geçersiz değer -> ContentPairError."""
    out: Dict[str, str] = {}
    for pid, v in prefs_by_pair.items():
        pref = v.get("pref") if isinstance(v, Mapping) else v
        if pid not in mapping_secret:
            raise ContentPairError(f"{pid}: mapping_secret'ta yok")
        if pref == "tie":
            out[pid] = "tie"
        elif pref in ("A", "B"):
            out[pid] = mapping_secret[pid][pref]
        else:
            raise ContentPairError(f"{pid}: geçersiz tercih {pref!r}")
    return out
