"""Keyword.normalized_keyword'u guncel normalize_keyword() kuralina getirir (idempotent).

Kiril/Yunan isaretleri artik korunuyor (й!=и, ё!=е); eski anahtarlar kolonda kalirsa
ayni metnin yeniden iceri alinmasi ikinci bir Keyword uretir. Latin/Turkce anahtarlar
degismez, yalnizca farkli olanlar yazilir.

Kullanim (uygulama konteynerinde):
    python scripts/rekey_normalized_keywords.py --dry-run   # varsayilan: sayi + en fazla 20 ornek
    python scripts/rekey_normalized_keywords.py --apply     # 500'luk partilerle commit
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # -I cwd/script dir eklemez

from app.core.keyword_normalize import normalize_keyword  # noqa: E402
from app.database.connection import SessionLocal  # noqa: E402
from app.database.models import Keyword  # noqa: E402

BATCH = 500
SAMPLE = 20


def _stale_rows(db, after_id):
    """id > after_id olan, anahtari eskimis satirlar (id, keyword, old, new), en fazla BATCH id taranir."""
    rows = (
        db.query(Keyword.id, Keyword.keyword, Keyword.normalized_keyword)
        .filter(Keyword.id > after_id)
        .order_by(Keyword.id)
        .limit(BATCH)
        .all()
    )
    last = rows[-1][0] if rows else after_id
    stale = [(i, k, old, normalize_keyword(k)) for i, k, old in rows if old != normalize_keyword(k)]
    return last, bool(rows), stale


def run(db, apply: bool) -> int:
    total, shown, after_id = 0, 0, 0
    while True:
        after_id, had_rows, stale = _stale_rows(db, after_id)
        if not had_rows:
            break
        for kid, kw, old, new in stale:
            total += 1
            if not apply and shown < SAMPLE:
                shown += 1
                print(f"  ({kid}, {kw!r}, {old!r}, {new!r})")
        if apply and stale:
            for kid, _kw, _old, new in stale:
                db.query(Keyword).filter(Keyword.id == kid).update(
                    {Keyword.normalized_keyword: new}, synchronize_session=False
                )
            db.commit()
    print(f"{'updated' if apply else 'would update'}: {total}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="yalniz say/ornek goster (varsayilan)")
    mode.add_argument("--apply", action="store_true", help="degisiklikleri yaz")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        run(db, apply=args.apply)
    finally:
        db.close()


if __name__ == "__main__":
    main()
