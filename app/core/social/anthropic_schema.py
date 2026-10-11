"""Kanonik katı şemadan Anthropic `output_config.format` json_schema kopyası.

Anthropic yapılandırılmış çıktı, her `object` düğümünde
`additionalProperties: false` ister (Gemini'nin tersi: orada düşer).
Kanonik şemaya DOKUNMAZ; derin kopya üzerinde:
  - her object düğümüne `additionalProperties: false` konur,
  - `propertyOrdering` (yalnız Gemini) düşer,
  - `nullable: true` -> `type: [T, "null"]`,
  - `required` listeleri OLDUĞU GİBİ kalır (optional alan optional kalır;
    spike reddederse [T, "null"] + yerel normalizasyon yedeği ayrı iştir).
Tanınmayan düğüm şekli fail-closed `ProviderSchemaError` verir.
"""
from __future__ import annotations

import copy
from typing import Any

from app.core.social.provider_schema import ProviderSchemaError

SCHEMA_TRANSFORMER_VERSION = "1"

_MAP_KEYS = ("properties", "$defs")      # ad -> şema
_LIST_KEYS = ("anyOf", "allOf")          # şema listesi
_SHAPE_KEYS = ("type", "anyOf", "allOf", "$ref", "enum", "const")


def _convert(node: Any, path: str) -> dict[str, Any]:
    if type(node) is not dict:
        raise ProviderSchemaError(f"{path}: şema düğümü nesne olmalı")
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "propertyOrdering":
            continue
        if key in _MAP_KEYS:
            if type(value) is not dict:
                raise ProviderSchemaError(f"{path}.{key}: nesne olmalı")
            out[key] = {n: _convert(s, f"{path}.{n}") for n, s in value.items()}
        elif key == "items":
            out[key] = _convert(value, f"{path}[]")
        elif key in _LIST_KEYS:
            if type(value) is not list or not value:
                raise ProviderSchemaError(f"{path}.{key}: boş olmayan liste olmalı")
            out[key] = [_convert(s, f"{path}|{i}") for i, s in enumerate(value)]
        else:
            out[key] = copy.deepcopy(value)

    node_type = out.get("type")
    if node_type is not None and not (
        isinstance(node_type, str)
        or (isinstance(node_type, list) and node_type
            and all(isinstance(t, str) for t in node_type))
    ):
        raise ProviderSchemaError(f"{path}: geçersiz type {node_type!r}")
    if "nullable" in out:
        nullable = out.pop("nullable")
        if type(nullable) is not bool:
            raise ProviderSchemaError(f"{path}: nullable bool olmalı")
        if nullable:
            if node_type is None:
                raise ProviderSchemaError(f"{path}: tipsiz düğümde nullable")
            types = [node_type] if isinstance(node_type, str) else list(node_type)
            if "null" not in types:
                types.append("null")
            out["type"] = types if len(types) > 1 else types[0]
            node_type = out["type"]
    if not any(k in out for k in _SHAPE_KEYS):
        raise ProviderSchemaError(f"{path}: tip belirtilmemiş şema düğümü")

    types = [node_type] if isinstance(node_type, str) else (node_type or [])
    if "object" in types:
        if out.get("additionalProperties", False) is not False:
            raise ProviderSchemaError(
                f"{path}: yalnız additionalProperties=false desteklenir")
        out["additionalProperties"] = False
    return out


def to_anthropic_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Kanonik şemadan Anthropic json_schema uyumlu yeni bir kopya üretir."""
    return _convert(schema, "$")
