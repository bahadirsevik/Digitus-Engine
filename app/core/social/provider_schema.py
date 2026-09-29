"""Sosyal brief şemalarının Gemini `response_schema` uyumlu sağlayıcı kopyası.

Kanonik şemalar (category/idea/content contract) katı JSON Schema'dır:
`additionalProperties: false`, tamsayı `enum` ve `type: "null"` içerir. Gemini'nin
`generation_config.response_schema` alanı bunların üçünü de kabul etmez
(`Unknown name "additional_properties"` 400; SDK tamsayı enum'u istek
gönderilmeden reddeder; `null` tipi tipsiz düğüme dönüşür).

Bu modül kanonik şemaya DOKUNMAZ; derin kopya üzerinde:
  - `additionalProperties: false` düşürülür (fazla alan reddi yerel doğrulayıcıda),
  - string olmayan `enum` düşürülür, izinli değerler açıklamaya yazılır
    (izinli kimlik denetimi yerel doğrulayıcıda),
  - `type: "null"` -> `type: "string", nullable: true` (null zorunluluğu yerel
    doğrulayıcıda).
Beyaz liste dışı her anahtar fail-closed hata verir: şemaya ileride eklenen
desteklenmeyen bir alan sessizce sağlayıcıya gitmez.
"""

from __future__ import annotations

import copy
from typing import Any

# Gemini API Schema nesnesinin kabul ettiği alanlar (JSON Schema adlarıyla).
GEMINI_SCHEMA_KEYS = frozenset({
    "type",
    "format",
    "title",
    "description",
    "nullable",
    "enum",
    "maxItems",
    "minItems",
    "properties",
    "required",
    "minProperties",
    "maxProperties",
    "minLength",
    "maxLength",
    "pattern",
    "example",
    "anyOf",
    "propertyOrdering",
    "default",
    "items",
    "minimum",
    "maximum",
})

_ACCEPTED_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object"})


class ProviderSchemaError(ValueError):
    """Kanonik şema Gemini uyumlu biçime güvenle çevrilemedi."""


def _append_description(node: dict[str, Any], note: str) -> None:
    current = node.get("description")
    node["description"] = f"{current} {note}" if current else note


def _convert(node: Any, path: str) -> dict[str, Any]:
    if type(node) is not dict:
        raise ProviderSchemaError(f"{path}: şema düğümü nesne olmalı")

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "additionalProperties":
            if value is not False:
                raise ProviderSchemaError(
                    f"{path}: yalnız additionalProperties=false yerelde karşılanır")
            continue
        if key not in GEMINI_SCHEMA_KEYS:
            raise ProviderSchemaError(f"{path}: Gemini desteklemeyen şema alanı '{key}'")
        if key == "properties":
            out[key] = {name: _convert(sub, f"{path}.{name}") for name, sub in value.items()}
        elif key == "items":
            out[key] = _convert(value, f"{path}[]")
        elif key == "anyOf":
            out[key] = [_convert(sub, f"{path}|{i}") for i, sub in enumerate(value)]
        else:
            out[key] = copy.deepcopy(value)

    node_type = out.get("type")
    if node_type == "null":
        out["type"] = "string"
        out["nullable"] = True
        out.pop("enum", None)
        _append_description(out, "Bu alan için her zaman JSON null döndür.")
    elif node_type is not None and node_type not in _ACCEPTED_TYPES:
        raise ProviderSchemaError(f"{path}: desteklenmeyen tip {node_type!r}")

    if "enum" in out and not all(type(v) is str for v in out["enum"]):
        allowed = ", ".join(str(v) for v in out.pop("enum"))
        _append_description(out, f"İzin verilen değerler: {allowed}.")

    if "type" not in out and "anyOf" not in out:
        raise ProviderSchemaError(f"{path}: tip belirtilmemiş şema düğümü")
    return out


def to_gemini_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Kanonik katı şemadan Gemini `response_schema` uyumlu yeni bir kopya üretir."""
    return _convert(schema, "$")
