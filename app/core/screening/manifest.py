"""Deney manifesti üreticisi + sonuç artifact bütünlüğü (plan_ai §4a).

Sözleşme: "Manifest sonuç artifact'ının içinde taşınır; MANİFEST'SİZ SONUÇ
GEÇERSİZDİR." Bu modül manifest'i üretir, artifact'a gömer ve doğrular.

Manifest alanları (plan_ai §4a): dataset artifact SHA'ları + kanonik harita
SHA'sı, prompt SHA, şema SHA, reason-code sürümü, provider/model, seed,
temperature, batch_size, max_output_tokens, git SHA, zaman damgası.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from app.core.screening.contract import (
    BATCH_SIZE,
    DEEPSEEK_THINKING,
    GEMINI_THINKING_LEVEL,
    MAX_OUTPUT_TOKENS,
    PERMUTATION_SEEDS,
    PROMPT_VERSION,
    REASON_CODE_VERSION,
    RESPONSE_SCHEMA,
    TEMPERATURE,
    PROMPT_TEMPLATE,
    RC_V1,
)

PAYLOAD_HASH_EXCLUDED = ("artifact_payload_sha256", "generated_at")


class ManifestError(RuntimeError):
    """Manifest eksik/tutarsız — sonuç geçersizdir."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_hash(obj: Any) -> str:
    return _sha256_text(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def source_run_params(source_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Türetilmiş (offline) artifact'ın künyesi KAYNAK koşunun sözleşmesini
    MİRAS ALIR (Codex 3. tur #1). Aksi halde `build_manifest` varsayılanları
    yazar ve sticky v3a/0.0/10 koşusundan türeyen rapor v2/0.3/30 görünür —
    fail-closed provenance iddiası delinir. Kaynakta alanlar eksikse hata:
    tahminle doldurulmaz.
    """
    m = source_doc.get("manifest")
    if not isinstance(m, dict):
        raise ManifestError("kaynak artifact manifest taşımıyor")
    missing = [k for k in ("prompt_version", "temperature", "batch_size")
               if m.get(k) is None]
    if missing:
        raise ManifestError(f"kaynak manifest'te eksik alanlar: {missing}")
    return {
        "prompt_version": m["prompt_version"],
        "temperature": m["temperature"],
        "batch_size": m["batch_size"],
    }


def canonical_hash(obj: Any) -> str:
    """Kanonik JSON hash'i (denetim kayitlari icin public yardimci)."""
    return _canonical_hash(obj)


def contract_hashes(prompt_version: Optional[str] = None) -> Dict[str, str]:
    """Dondurulmuş sözleşmenin hash'leri.

    NOT: prompt hash'i ŞABLONUN kendisidir (bağlam/keyword'lerden bağımsız);
    freeze testindeki hash sabit bir ÖRNEK çıktının hash'idir — ikisi farklı
    amaçlara hizmet eder, birbirinin yerine geçmez. RC_V1 ayrıca kendi
    hash'iyle taşınır (şablon kodları içerse de açık kayıt tercih edilir).
    """
    from app.core.screening.contract import PROMPT_TEMPLATES

    template = PROMPT_TEMPLATES[prompt_version or PROMPT_VERSION]
    return {
        "prompt_template_sha256": _sha256_text(template),
        "response_schema_sha256": _canonical_hash(RESPONSE_SCHEMA),
        "reason_codes_sha256": _canonical_hash(
            {k: list(v) for k, v in RC_V1.items()}
        ),
    }


def _git_sha() -> Optional[str]:
    try:
        from app.core.gitinfo import resolve_git_sha

        return resolve_git_sha()
    except Exception:
        return None


def build_manifest(
    *,
    provider: str,
    model: str,
    seed: int,
    dataset_slug: str,
    dataset_files: Optional[List[str]] = None,
    generated_at: str,
    universe_size: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    prompt_version: Optional[str] = None,
    temperature: Optional[float] = None,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    """GERÇEKTE KULLANILAN parametreler manifest'e yazılır (Codex #1).

    Önceki sürüm daima sözleşme varsayılanlarını (v2/0.3/30) yazıyordu;
    deney v3a/0/10 ile koşulsa bile artifact 'v2' diyordu ve doğrulama
    bunu kabul ediyordu. Artık üst düzey alanlar parametrelerden gelir ve
    hash'ler KULLANILAN prompt sürümünden hesaplanır.
    """
    """Koşu başına deney manifesti.

    `generated_at` DIŞARIDAN verilir (deterministik testler + artifact
    payload hash'inin zamandan bağımsız kalması için).
    """
    dataset_shas = {}
    for path in dataset_files or []:
        if os.path.exists(path):
            dataset_shas[os.path.basename(path)] = _sha256_file(path)
        else:
            dataset_shas[os.path.basename(path)] = None
    used_prompt = prompt_version or PROMPT_VERSION
    manifest = {
        "prompt_version": used_prompt,
        "reason_code_version": REASON_CODE_VERSION,
        **contract_hashes(used_prompt),
        "provider": provider,
        "model": model,
        "seed": seed,
        "permutation_seeds": list(PERMUTATION_SEEDS),
        "temperature": TEMPERATURE if temperature is None else temperature,
        "batch_size": BATCH_SIZE if batch_size is None else batch_size,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "thinking": {
            "gemini_level": GEMINI_THINKING_LEVEL,
            "deepseek": DEEPSEEK_THINKING,
        },
        "dataset_slug": dataset_slug,
        "dataset_sha256": dataset_shas,
        "universe_size": universe_size,
        "git_sha": _git_sha(),
        "generated_at": generated_at,
    }
    if extra:
        manifest["extra"] = extra
    return manifest


REQUIRED_MANIFEST_FIELDS = (
    "prompt_version", "reason_code_version", "prompt_template_sha256",
    "response_schema_sha256", "reason_codes_sha256", "provider", "model",
    "seed", "temperature", "batch_size", "max_output_tokens", "dataset_slug",
)


def compute_payload_sha(doc: Dict[str, Any]) -> str:
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items()
                    if k not in PAYLOAD_HASH_EXCLUDED}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o

    return _canonical_hash(strip(doc))


def seal_artifact(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Artifact'a payload SHA'sını gömer (öz-bütünlük)."""
    if "manifest" not in doc:
        raise ManifestError("artifact manifest taşımıyor — sonuç geçersiz")
    doc = dict(doc)
    doc["artifact_payload_sha256"] = compute_payload_sha(doc)
    return doc


def validate_artifact(doc: Dict[str, Any], *, check_contract: bool = True) -> None:
    """Manifest + öz-bütünlük + (opsiyonel) canlı sözleşme uyumu.

    check_contract=True iken artifact'ın hash'leri ŞU ANKİ dondurulmuş
    sözleşmeyle karşılaştırılır — sözleşme değiştikten sonra eski sonucu
    "güncel" diye kullanmayı engeller (plan_ai §13: immutable snapshot).
    """
    manifest = doc.get("manifest")
    if not isinstance(manifest, dict):
        raise ManifestError("manifest yok — sonuç geçersiz")
    missing = [f for f in REQUIRED_MANIFEST_FIELDS if manifest.get(f) in (None, "")]
    if missing:
        raise ManifestError(f"manifest alanları eksik: {missing}")

    expected = doc.get("artifact_payload_sha256")
    if not expected:
        raise ManifestError("artifact_payload_sha256 yok")
    actual = compute_payload_sha(doc)
    if actual != expected:
        raise ManifestError(f"payload SHA uyuşmuyor: {actual} != {expected}")

    # Üst düzey ile `extra` ÇELİŞİRSE artifact geçersizdir (Codex #1)
    extra = manifest.get("extra") or {}
    for top_key, extra_key in (("prompt_version", "prompt_version_used"),
                               ("batch_size", "batch_size"),
                               ("temperature", "temperature_override")):
        if extra_key in extra and extra[extra_key] is not None:
            if manifest.get(top_key) != extra[extra_key]:
                raise ManifestError(
                    f"manifest çelişkisi: {top_key}={manifest.get(top_key)!r} "
                    f"ama extra.{extra_key}={extra[extra_key]!r}"
                )

    if check_contract:
        live = contract_hashes(manifest.get("prompt_version"))
        for key, value in live.items():
            if manifest.get(key) != value:
                raise ManifestError(
                    f"sözleşme değişmiş ({key}): artifact "
                    f"{manifest.get(key)} != canlı {value} — bu sonuç güncel "
                    f"sözleşmeyle karşılaştırılamaz"
                )
