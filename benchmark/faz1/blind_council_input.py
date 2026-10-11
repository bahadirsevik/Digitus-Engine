"""Faz 1 ciktilarini KOR (A/B/C) olarak konsey girdisine donusturur.

Her workspace icin uc konfigurasyon rastgele A/B/C harfi alir (tohumlu); gizli esleme
AYRI dosyaya yazilir ve konseye GITMEZ. Metinler `normalize_for_blind` ile saglayici
izlerinden temizlenir. Iki dosya/ws: ana (havuzlar + blog + sosyal) ve RSA (her sistemin
TUM gruplari; gruplar sistemler arasi eslesmez). Kullanim (lean kokunden, DB erisimli):
  PYTHONPATH=/app python benchmark/faz1/blind_council_input.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from app.core.benchmark.content_pairs import extract_items, normalize_for_blind
from app.core.channel.brand_defense import load_product_definition
from app.core.engine.persistence import load_channel_pools
from app.database.connection import SessionLocal
from app.database.models import Keyword, ScoringRun

ART = Path("benchmark/faz1")
OUT = Path("benchmark/council_in")
SECRET = ART / "kor_esleme_gizli.json"
WS_NAME = {21: "Firma-1", 29: "Firma-2"}          # firma adi da gizlenir
CONFIGS = ["G/G/G", "Aday", "G/G/C"]
SEED = "faz1-kor-2026-10-12"
POOL_TOP = 40
MAX_RSA_PER_SYSTEM, MAX_BLOG, BLOG_CHARS = 12, 5, 2600

RUBRIC = """## Degerlendirme kurallari (plan §7.3)
Her formatta A/B/C'yi siralayin (beraberlik serbest), 1-5 puan ve KISA gerekce verin.
Ortak boyutlar: marka/urun sadakati (x3), Turkce dogallik (x2), anahtar kelime/CTA hedefine uyum (x2), ozgunluk (x1).
Format ekleri (x2): blog -> bilgi yeterliligi, yapi, arama niyeti; RSA -> baslik cesitliligi, mesaj kapsami;
sosyal -> hook, uygulanabilirlik, format uygunlugu. UZUNLUK PUANLANMAZ. Dayanaksiz iddia (sayi, "en iyi",
kazanc garantisi) agir kusurdur. Havuzlar icin: markayla ilgisiz / konu-disi kelime sayisini ve listenin
ticari isabetini degerlendirin. Hangi A/B/C'nin hangi yapay zeka modeli oldugunu TAHMIN ETMEYIN ve yazmayin;
yalniz icerige bakin. Sonunda format basina ve genel olarak "en iyi: X" seklinde net hukum verin."""


def _write(path: Path, lines) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(path, len("\n".join(lines)), "chars")


def main() -> None:
    db = SessionLocal()
    secret = {}
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        for ws in (21, 29):
            arts = {c: json.loads((ART / f"ws{ws}_{c.replace('/', '')}.json").read_text(encoding="utf-8"))
                    for c in CONFIGS}
            labels = ["A", "B", "C"]
            random.Random(f"{SEED}:{ws}").shuffle(labels)
            mapping = dict(zip(CONFIGS, labels))          # config -> harf
            secret[str(ws)] = {"workspace": ws, "mapping": mapping,
                               "runs": {c: arts[c]["scoring_run_id"] for c in CONFIGS}}
            by_label = {mapping[c]: arts[c] for c in CONFIGS}
            texts = {}

            def kw_text(kid):
                if kid not in texts:
                    row = db.get(Keyword, kid)
                    texts[kid] = getattr(row, "keyword", None) or getattr(row, "keyword_text", str(kid))
                return texts[kid]

            lines = [f"# {WS_NAME[ws]} — kor cikti kiyasi (A/B/C)", "",
                     "Ayni firma, ayni anahtar kelime evreni, ayni kapasite (ADS 60 / SEO 30 / SOCIAL 30). "
                     "Uc sistem ayni isi yapti; A/B/C etiketleri rastgeledir.", "", RUBRIC, ""]
            # ── havuzlar ──
            lines.append("## 1. Secilen anahtar kelime havuzlari (ilk %d)" % POOL_TOP)
            for ch in ("ADS", "SEO", "SOCIAL"):
                lines.append(f"\n### {ch}")
                cols = {}
                for lab in sorted(by_label):
                    rows = load_channel_pools(db, scoring_run_id=by_label[lab]["scoring_run_id"], channel=ch)
                    rows = sorted(rows, key=lambda r: (r.final_rank if r.final_rank is not None else 10**9))
                    cols[lab] = [kw_text(r.keyword_id) for r in rows[:POOL_TOP]]
                n = max(len(v) for v in cols.values()) if cols else 0
                lines.append("| # | " + " | ".join(sorted(cols)) + " |")
                lines.append("|---|" + "---|" * len(cols))
                for i in range(n):
                    lines.append(f"| {i+1} | " + " | ".join(
                        normalize_for_blind(cols[l][i]) if i < len(cols[l]) else "" for l in sorted(cols)) + " |")
            # ── icerikler ──
            items = {}
            for lab, art in by_label.items():
                run = db.get(ScoringRun, art["scoring_run_id"])
                facts = load_product_definition(db, run)
                items[lab] = extract_items(db, art["scoring_run_id"], ws=ws, grounding_facts=facts)

            def common_keys(fmt, limit):
                keysets = [set(items[l].get(fmt, {})) for l in items]
                allkeys = set().union(*keysets)
                scored = sorted(allkeys, key=lambda k: (-sum(k in s for s in keysets), k))
                return [k for k in scored if sum(k in s for s in keysets) >= 2][:limit]

            def render(fmt, title, limit, clip=None):
                lines.append(f"\n## {title}")
                for key in common_keys(fmt, limit):
                    any_item = next(items[l][fmt][key] for l in items if key in items[l].get(fmt, {}))
                    lines.append(f"\n### {normalize_for_blind(any_item.get('key_label') or key)}")
                    for lab in sorted(items):
                        it = items[lab].get(fmt, {}).get(key)
                        lines.append(f"\n**{lab}**")
                        if not it:
                            lines.append("— yok —")
                            continue
                        for label, text in it["text_blocks"]:
                            t = normalize_for_blind(text or "")
                            if clip and len(t) > clip:
                                t = t[:clip] + " […kesildi…]"
                            lines.append(f"- {label}: {t}")

            # RSA: gruplar sistemler arasi ayni kelime kumesini vermez -> anahtar eslesmez;
            # her sistemin TUM gruplari kendi basligi altinda AYRI dosyaya yazilir.
            rsa = [f"# {WS_NAME[ws]} — kor RSA (Google Ads) kiyasi (A/B/C)", "",
                   "Ayni firma, ayni anahtar kelime evreni. Her sistem kendi reklam gruplarini kurdu; gruplar "
                   "sistemler arasi birebir eslesmez, bu yuzden her sistemin gruplari TOPLU degerlendirilir.",
                   "", RUBRIC, "", "## Google Ads RSA gruplari (her sistemin tum gruplari)"]
            for lab in sorted(items):
                groups = items[lab].get("rsa", {})
                rsa.append(f"\n### Sistem {lab} — {len(groups)} grup")
                for key in sorted(groups)[:MAX_RSA_PER_SYSTEM]:
                    it = groups[key]
                    rsa.append(f"\n#### {normalize_for_blind(it.get('key_label') or key)}")
                    for label, text in it["text_blocks"]:
                        rsa.append(f"- {label}: {normalize_for_blind(text or '')}")
                    if it.get("hard_rule_violations"):
                        rsa.append(f"- kural ihlali: {it['hard_rule_violations']}")
            rsa += ["", "## Istenen cikti",
                    "RSA icin A/B/C siralamasi + 1-5 puan + gerekce (baslik cesitliligi, mesaj kapsami, marka "
                    "sadakati, dayanaksiz iddia, karakter sinirlari). Model tahmini YAPMAYIN."]
            _write(OUT / f"faz1_kor_rsa_ws{ws}.md", rsa)

            lines.append("\n## 2. Google Ads RSA gruplari\nAyri dosyada (faz1_kor_rsa_ws%d.md) degerlendirilir." % ws)
            render("blog", "3. Blog icerikleri (ilk %d karakter)" % BLOG_CHARS, MAX_BLOG, BLOG_CHARS)
            render("social", "4. Sosyal medya icerikleri", 12)
            lines += ["", "## Istenen cikti",
                      "Her bolum (havuz ADS/SEO/SOCIAL, blog, sosyal) icin A/B/C siralamasi + 1-5 puan + 2-3 cumle "
                      "gerekce; dayanaksiz iddia ve konu-disi kelime ornekleri; genel hukum: 'en iyi: X, ikinci: Y, "
                      "ucuncu: Z'. Model tahmini YAPMAYIN."]
            _write(OUT / f"faz1_kor_ws{ws}.md", lines)
    finally:
        db.close()
    SECRET.write_text(json.dumps(secret, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("secret ->", SECRET)


if __name__ == "__main__":
    main()
