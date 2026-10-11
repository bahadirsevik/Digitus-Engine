"""Faz 1 patron karşılaştırma çıktıları: mühürlü hybrid-run artifact'larından
havuz / içerik / özet dosyaları (xlsx + md). Etiketli (kör değil) çıktı; ham metin korunur.

python -m app.core.benchmark.faz1_export --artifacts benchmark/faz1/*.json --out benchmark/faz1/export/
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.benchmark.reference_characterization import verify_seal

CONFIG_ORDER = ["G/G/G", "Aday", "G/G/C"]
CHANNELS = ["ADS", "SEO", "SOCIAL"]
FORMAT_TITLES = {"rsa": "RSA grupları", "blog": "Blog", "social": "Sosyal"}
MISSING = "— yok —"
COST_COL = "liste fiyatı (konsol faturası ayrı)"


def _rank(cfg: str) -> int:
    return CONFIG_ORDER.index(cfg) if cfg in CONFIG_ORDER else len(CONFIG_ORDER)


def _order(cfgs: Sequence[str]) -> List[str]:
    return sorted(cfgs, key=_rank)


def load_artifacts(paths: Sequence[str]) -> Dict[int, List[dict]]:
    """{workspace_id: [artifact...]} config sırasında; her artifact mührü doğrulanır."""
    groups: Dict[int, List[dict]] = defaultdict(list)
    for p in paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        verify_seal(doc)
        groups[doc["workspace_id"]].append(doc)
    return {ws: sorted(v, key=lambda d: _rank(d["config_name"])) for ws, v in sorted(groups.items())}


# ── saf render fonksiyonları ─────────────────────────────────────────────

def render_pools_rows(pools_by_config: Mapping[str, Sequence[Mapping[str, Any]]],
                      texts: Mapping[int, str]) -> Tuple[List[list], List[list]]:
    """pools_by_config: {config: [{keyword_id, final_rank}...]} (tek kanal).
    Dönüş: (yan yana sıralı satırlar, Ortak satırları). İlk satır başlık."""
    cfgs = _order(list(pools_by_config))
    ranked = {c: [texts.get(r["keyword_id"], str(r["keyword_id"]))
                  for r in sorted(pools_by_config[c], key=lambda r: r["final_rank"])] for c in cfgs}
    n = max((len(v) for v in ranked.values()), default=0)
    side = [["rank", *cfgs]] + [[i + 1, *(ranked[c][i] if i < len(ranked[c]) else "" for c in cfgs)]
                                for i in range(n)]
    sets = {c: set(v) for c, v in ranked.items()}
    allkw = sorted(set().union(*sets.values())) if sets else []
    ortak = [["keyword", *[f"in {c}" for c in cfgs]]]
    ortak += [[k, *("✓" if k in sets[c] else "—" for c in cfgs)] for k in allkw]
    common = [k for k in allkw if all(k in sets[c] for c in cfgs)]
    ortak += [[], ["toplam", *(len(sets[c]) for c in cfgs)], ["ortak (hepsinde)", len(common)]]
    return side, ortak


def _item_block(it: Optional[Mapping[str, Any]]) -> str:
    if not it:
        return MISSING
    lines = [f"{lbl}: {txt}" for lbl, txt in it["text_blocks"]]
    if it.get("hard_rule_violations"):
        lines.append("Hard-rule ihlalleri: " + ", ".join(it["hard_rule_violations"]))
    return "\n\n".join(lines)


def render_content_md(items_by_config: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> str:
    """Tek format: {config: {key_str: Item}} → key_label'a göre gruplu markdown."""
    cfgs = _order(list(items_by_config))
    keys: Dict[str, str] = {}  # key_str -> label
    for c in cfgs:
        for ks, it in items_by_config[c].items():
            keys.setdefault(ks, it["key_label"])
    out: List[str] = []
    for ks, label in sorted(keys.items(), key=lambda kv: (kv[1], kv[0])):
        out.append(f"### {label}\n")
        for c in cfgs:
            out.append(f"**{c}**\n\n{_item_block(items_by_config[c].get(ks))}\n")
    return "\n".join(out)


def content_rows(items_by_config: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> List[list]:
    rows = [["key_label", "config", "label", "text", "hard_rule_ok", "violations"]]
    for c in _order(list(items_by_config)):
        for it in sorted(items_by_config[c].values(), key=lambda i: (i["key_label"], i["key_str"])):
            viol = ", ".join(it.get("hard_rule_violations") or [])
            for lbl, txt in it["text_blocks"]:
                rows.append([it["key_label"], c, lbl, txt, bool(it["hard_rule_ok"]), viol])
    return rows


def _write_xlsx(path: Path, sheets: Mapping[str, Sequence[Sequence[Any]]]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        for r in rows:
            ws.append(list(r))
    wb.save(path)


# ── DB'li dışa aktarım (birim testte KAPSANMAZ) ───────────────────────────

def export_pools(db: Any, group: Sequence[dict], out_dir: Path) -> Path:
    from app.core.engine.persistence import load_channel_pools
    from app.database.models import Keyword
    sheets: Dict[str, list] = {}
    for ch in CHANNELS:
        by_cfg = {a["config_name"]: [{"keyword_id": p.keyword_id, "final_rank": p.final_rank}
                                     for p in load_channel_pools(db, scoring_run_id=a["scoring_run_id"], channel=ch)]
                  for a in group}
        ids = {r["keyword_id"] for v in by_cfg.values() for r in v}
        texts = dict(db.query(Keyword.id, Keyword.keyword).filter(Keyword.id.in_(ids)).all()) if ids else {}
        side, ortak = render_pools_rows(by_cfg, texts)
        sheets[ch], sheets[f"{ch} Ortak"] = side, ortak
    path = out_dir / f"ws{group[0]['workspace_id']}_havuzlar.xlsx"
    _write_xlsx(path, sheets)
    return path


def export_content(db: Any, group: Sequence[dict], out_dir: Path) -> List[Path]:
    from app.core.benchmark.content_pairs import extract_items
    from app.core.channel.brand_defense import load_product_definition
    from app.database.models import ScoringRun
    wsid = group[0]["workspace_id"]
    per_cfg: Dict[str, dict] = {}
    for a in group:
        run = db.query(ScoringRun).filter(ScoringRun.id == a["scoring_run_id"]).one()
        facts = load_product_definition(db, run) or ""
        per_cfg[a["config_name"]] = extract_items(db, a["scoring_run_id"], ws=wsid, grounding_facts=facts)
    md = [f"# ws{wsid} içerik karşılaştırması\n"]
    sheets = {}
    for fmt, title in FORMAT_TITLES.items():
        by_cfg = {c: v.get(fmt, {}) for c, v in per_cfg.items()}
        md.append(f"## {title}\n\n{render_content_md(by_cfg)}")
        sheets[title] = content_rows(by_cfg)
    p_md, p_xl = out_dir / f"ws{wsid}_icerikler.md", out_dir / f"ws{wsid}_icerikler.xlsx"
    p_md.write_text("\n".join(md), encoding="utf-8", newline="\n")
    _write_xlsx(p_xl, sheets)
    return [p_md, p_xl]


# ── özet (yalnız artifact) ───────────────────────────────────────────────

def _is_claude(provider: str) -> bool:
    return any(s in provider.lower() for s in ("claude", "anthropic"))


def _social_count(social):
    """Kaydedilen sosyal fikir sayisi: artifact `persisted_idea_ids` tasir (hybrid_run)."""
    if social.get("n_items") is not None:
        return social["n_items"]
    ids = social.get("persisted_idea_ids")
    return len(ids) if isinstance(ids, list) else None


def summary_rows(groups: Mapping[int, Sequence[dict]]) -> List[list]:
    head = ["ws", "config", "scoring_run_id", "istek (provider)", f"maliyet Gemini USD [{COST_COL}]",
            f"maliyet Claude USD [{COST_COL}]", f"toplam USD [{COST_COL}]",
            "süre setup s", "süre orkestrasyon s", "süre ADS s", "süre SEO s", "süre sosyal s",
            "ADS grup", "blog", "sosyal", "content_errors"]
    rows = [head]
    for ws, group in groups.items():
        for a in group:
            prov = a.get("budget_summary") or {}
            g = sum(float(v.get("cost_settled_usd") or 0) for k, v in prov.items() if not _is_claude(k))
            c = sum(float(v.get("cost_settled_usd") or 0) for k, v in prov.items() if _is_claude(k))
            reqs = ", ".join(f"{k}={v.get('requests_used', 0)}" for k, v in sorted(prov.items()))
            w, ct = a.get("wall_clock_s") or {}, a.get("content") or {}
            errs = a.get("content_errors")
            rows.append([ws, a["config_name"], a.get("scoring_run_id"), reqs, round(g, 4), round(c, 4),
                         round(g + c, 4), w.get("setup"), w.get("orchestration"), w.get("content_ads"),
                         w.get("content_seo"), w.get("content_social"),
                         (ct.get("ads") or {}).get("n_groups"),
                         len((ct.get("seo") or {}).get("content_ids") or []),
                         _social_count(ct.get("social") or {}),
                         json.dumps(errs, ensure_ascii=False) if errs else ""])
    return rows


def export_summary(groups: Mapping[int, Sequence[dict]], out_dir: Path) -> List[Path]:
    rows = summary_rows(groups)
    md = ["# Faz 1 özet\n", f"Maliyetler artifact'lardaki liste fiyatıdır ({COST_COL}).\n",
          "| " + " | ".join(str(h) for h in rows[0]) + " |", "|" + "---|" * len(rows[0])]
    md += ["| " + " | ".join("" if x is None else str(x).replace("|", "/") for x in r) + " |" for r in rows[1:]]
    p_md, p_xl = out_dir / "ozet.md", out_dir / "ozet.xlsx"
    p_md.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    _write_xlsx(p_xl, {"Özet": rows})
    return [p_md, p_xl]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-db", action="store_true", help="yalnız özet (DB'siz)")
    a = ap.parse_args(argv)
    paths = [p for pat in a.artifacts for p in sorted(glob.glob(pat))]  # Windows kabuğu glob açmaz
    if not paths:
        raise SystemExit("artifact bulunamadı")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    groups = load_artifacts(paths)
    written = export_summary(groups, out)
    if not a.no_db:
        from app.database.connection import SessionLocal
        db = SessionLocal()
        try:
            for group in groups.values():
                written.append(export_pools(db, group, out))
                written += export_content(db, group, out)
        finally:
            db.close()
    print("\n".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
