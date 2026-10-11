"""Faz 1A: motor kosusunun GERCEK istek yakalama katmani (plan K14).

`ai_runner.CAPTURE_SINK` kancasini kurar; her saglayici cagrisi (basarili ve
BASARISIZ) `<out_dir>/<run_id>/calls.jsonl`a bir JSON satiri olarak yazilir,
cikista `index.json` muhurlenir (`seal`). Yakalama motor davranisini degistirmez;
fail-closed: var olan bir yakalamanin ustune yazilmaz / sessiz append yapilmaz.
Ucretli cagri YAPMAZ, ag kullanmaz.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.benchmark.reference_characterization import (
    CharacterizationError, seal, verify_seal,
)
from app.core.engine import ai_runner

DEFAULT_OUT_DIR = Path("benchmark/captures")


def prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StageCapture:
    def __init__(self, *, run_id: str, out_dir: Path = DEFAULT_OUT_DIR):
        self.run_id = str(run_id)
        self.dir = Path(out_dir) / self.run_id
        self._lock = threading.Lock()
        self._calls: List[dict] = []
        self._fh = None
        self._prev = None

    def __enter__(self) -> "StageCapture":
        if (self.dir / "index.json").exists():
            raise CharacterizationError(
                f"yakalama zaten muhurlu: {self.dir} — ustune yazilmaz")
        self.dir.mkdir(parents=True, exist_ok=True)
        # "x": yarim kalmis calls.jsonl'e de SESSIZ append yapilmaz
        self._fh = open(self.dir / "calls.jsonl", "x", encoding="utf-8",
                        newline="\n")
        self._prev = ai_runner.CAPTURE_SINK
        ai_runner.CAPTURE_SINK = self._sink
        return self

    def _sink(self, record: Dict[str, Any]) -> None:
        with self._lock:                 # run_jobs paralel thread'lerinden gelir
            record = {"seq": len(self._calls), **record}
            self._fh.write(json.dumps(record, ensure_ascii=False,
                                      default=str) + "\n")
            self._fh.flush()
            self._calls.append(record)

    def __exit__(self, exc_type, exc, tb) -> None:
        ai_runner.CAPTURE_SINK = self._prev
        self._fh.close()
        stages: Dict[str, Dict[str, Any]] = {}
        for c in self._calls:
            s = stages.setdefault(c["stage"], {
                "n_calls": 0, "n_failed": 0, "providers": set(),
                "models": set(), "prompt_sha_set": set()})
            s["n_calls"] += 1
            s["n_failed"] += c.get("raw_text") is None
            s["providers"].add(c.get("provider"))
            s["models"].add(c.get("model"))
            s["prompt_sha_set"].add(prompt_sha(c["prompt"]))
        index = seal({
            "kind": "stage_capture", "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "clean_exit": exc_type is None,   # False: yarim yakalama
            "n_calls": len(self._calls),
            "stages": {k: {**v, "providers": sorted(map(str, v["providers"])),
                           "models": sorted(map(str, v["models"])),
                           "prompt_sha_set": sorted(v["prompt_sha_set"])}
                       for k, v in sorted(stages.items())},
            "calls_sha256": _file_sha(self.dir / "calls.jsonl"),
        })
        (self.dir / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
            newline="\n")


def load_capture(run_id: str, out_dir: Path = DEFAULT_OUT_DIR
                 ) -> List[Dict[str, Any]]:
    """Muhuru + calls.jsonl SHA'sini dogrular; cagri listesini dondurur."""
    d = Path(out_dir) / str(run_id)
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    verify_seal(index)
    if _file_sha(d / "calls.jsonl") != index.get("calls_sha256"):
        raise CharacterizationError("calls.jsonl SHA'si indeksle uyusmuyor")
    calls = [json.loads(line) for line in
             (d / "calls.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if len(calls) != index.get("n_calls"):
        raise CharacterizationError("calls.jsonl satir sayisi indeksle uyusmuyor")
    return calls


def load_capture_index(run_id: str, out_dir: Path = DEFAULT_OUT_DIR
                       ) -> Dict[str, Any]:
    """Dogrulanmis indeks (replay artifact'i `calls_sha256`i buradan alir)."""
    load_capture(run_id, out_dir)
    return json.loads((Path(out_dir) / str(run_id) / "index.json")
                      .read_text(encoding="utf-8"))
