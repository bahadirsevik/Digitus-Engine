"""Bake-off / teşhis koşuları için ORTAK dondurulmuş girdi katmanı.

Evren run 24/25'in immutable `KeywordScore` kümesinden, bağlam run'ın
`execution_manifest.strategy_snapshot`'ından gelir; import haritasıyla iki
yönlü kilitlenir (fail-closed). Hem `run_screening_bakeoff.py` hem
`run_screening_stability.py` buradan okur — ikinci bir kopya tutulmaz.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from app.core.benchmark.candidate_replay import (
    load_run_inputs,
    resolve_positive_ids,
    verify_universe_lock,
)
from app.core.screening.contract import BATCH_SIZE, build_prompt
from app.core.screening.runner import ScreeningContext

DATASETS: Dict[str, Dict[str, Any]] = {
    "dijital": {"workspace_id": 29, "run_id": 24,
                "capacities": {"ADS": 17, "SEO": 33, "SOCIAL": 20},
                "artifact": "benchmark/v21_dataset_dijital.json"},
    "gr7": {"workspace_id": 30, "run_id": 25,
            "capacities": {"ADS": 63, "SEO": 86, "SOCIAL": 110},
            "artifact": "benchmark/v21_dataset_gr7.json"},
}
MAP_PATH = "benchmark/v21_prod_import_map.json"
PROVIDER_OF = {
    "gemini-3.5-flash-lite": "gemini",
    "gemini-3.5-flash": "gemini",
    "deepseek-v4-flash": "deepseek",
    "deepseek-v4-pro": "deepseek",
}


def canonical_hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_frozen_inputs(db, slug: str, map_doc) -> Dict[str, Any]:
    cfg = DATASETS[slug]
    loaded = load_run_inputs(db, cfg["run_id"], cfg["workspace_id"],
                             expected_capacities=cfg["capacities"])
    verify_universe_lock(loaded["rows"], map_doc, slug)
    positives = resolve_positive_ids(loaded["rows"], map_doc, slug)

    from app.core.policy.channel_strategy import require_dispatch_strategy_context
    from app.database.models import ScoringRun

    run = db.query(ScoringRun).filter(ScoringRun.id == cfg["run_id"]).first()
    snapshot = require_dispatch_strategy_context(run)
    context = ScreeningContext(
        product_definition=snapshot["product_definition"],
        content_strategy=snapshot["content_strategy"],
        social_mode=snapshot["social_mode"],
        target_audience=snapshot.get("target_audience"),
    )
    universe = [{"id": r["keyword_id"], "keyword": r["text"]}
                for r in loaded["rows"]]
    return {
        "rows": loaded["rows"],
        "universe": universe,
        "positives": positives,
        "context": context,
        "universe_sha256": canonical_hash(
            [[r["keyword_id"], r["text"]] for r in loaded["rows"]]
        ),
        "context_sha256": canonical_hash(context.as_dict()),
        "input_hash_sha256": loaded["input_hash"],
        "capacities": loaded["capacities"],
        # Üretim baseline sırası (PoolBuilder relevance yolu) bu katsayıya
        # bağlıdır — union replay ve assistive materyalizasyon okur
        "coefficient": loaded["coefficient"],
        "run_id": cfg["run_id"],
        "workspace_id": cfg["workspace_id"],
    }


def max_prompt_bytes(context, universe) -> int:
    """Bu evrende bir batch'in üretebileceği EN BÜYÜK prompt (UTF-8 bayt)."""
    biggest = 0
    for i in range(0, len(universe), BATCH_SIZE):
        batch = universe[i:i + BATCH_SIZE]
        size = len(build_prompt(
            product_definition=context.product_definition,
            content_strategy=context.content_strategy,
            target_audience=context.target_audience or "-",
            social_mode=context.social_mode,
            keywords=batch,
        ).encode("utf-8"))
        biggest = max(biggest, size)
    return biggest


def make_provider(model: str, collector=None,
                  temperature: Optional[float] = None,
                  prompt_version: Optional[str] = None):
    """Sağlayıcı fabrikası. `temperature` YALNIZ teşhis koşularında
    verilir; verilmezse dondurulmuş sözleşme değeri kullanılır."""
    from app.config import settings
    from app.core.screening.providers import (
        DeepSeekCorpusScreeningProvider,
        GeminiCorpusScreeningProvider,
    )

    provider_name = PROVIDER_OF.get(model)
    if provider_name == "gemini":
        return GeminiCorpusScreeningProvider(model=model, collector=collector,
                                             temperature=temperature,
                                             prompt_version=prompt_version)
    if provider_name == "deepseek":
        return DeepSeekCorpusScreeningProvider(
            model=model, api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL, collector=collector,
            temperature=temperature, prompt_version=prompt_version,
        )
    raise SystemExit(f"bilinmeyen model: {model}")
