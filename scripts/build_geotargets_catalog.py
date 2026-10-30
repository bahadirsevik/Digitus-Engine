"""Google geotargets CSV'sinden uygulama konum katalogunu uretir.

Kullanim:
    python -I scripts/build_geotargets_catalog.py <resmi_geotargets.csv> <cikti.csv.gz>

Suzgec: Status == Active, Country Code in app.core.markets.COUNTRIES,
Target Type in app.core.markets.CATALOG_TARGET_TYPES. Kolonlar: Criteria ID, Name,
Canonical Name, Parent ID, Country Code, Target Type. Girdi guvenilmez veri sayilir:
yalniz csv ile okunur, hicbir sey calistirilmaz. Cikti deterministiktir (gzip mtime=0).
"""
import csv
import gzip
import io
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # -I cwd/script dir eklemez

from app.core.markets import CATALOG_COLUMNS, CATALOG_TARGET_TYPES, COUNTRIES  # noqa: E402


def build(src: Path, dst: Path) -> Counter:
    codes = {c["code"] for c in COUNTRIES}
    kept: Counter = Counter()
    with open(src, encoding="utf-8", newline="") as fh, open(dst, "wb") as raw, \
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as text:
        out = csv.writer(text, lineterminator="\n")
        out.writerow(CATALOG_COLUMNS)
        for r in csv.DictReader(fh):
            if (r["Status"] != "Active" or r["Country Code"] not in codes
                    or r["Target Type"] not in CATALOG_TARGET_TYPES):
                continue
            out.writerow([r[c] for c in CATALOG_COLUMNS])
            kept[r["Target Type"]] += 1
    return kept


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.parent.mkdir(parents=True, exist_ok=True)
    kept = build(src, dst)
    print(f"rows={sum(kept.values())} gz_kb={dst.stat().st_size / 1024:.0f}")
    for t, n in sorted(kept.items(), key=lambda kv: -kv[1]):
        print(f"  {t}: {n}")
