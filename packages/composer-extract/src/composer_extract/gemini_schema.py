"""Translates Pydantic's JSON Schema into Gemini's ``responseSchema`` dialect.

Split out of :mod:`.gemini_client` because it is a self-contained concern (pure
schema-to-schema translation, no I/O) rather than part of the extractor itself.
"""

from __future__ import annotations

from typing import Any

#: JSON Schema types pydantic emits, mapped to Gemini's OpenAPI-subset dialect.
_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _resolve_ref(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    if "description" in node:
        resolved = {**resolved, "description": node["description"]}
    return resolved


def _resolve_any_of(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """A Pydantic ``X | None`` union, folded into Gemini's ``nullable`` flag on
    the non-null branch — Gemini's schema dialect has no null type of its own."""
    options = [option for option in node["anyOf"] if option.get("type") != "null"]
    nullable = len(options) != len(node["anyOf"])
    resolved = _resolve(options[0], defs) if options else {"type": "STRING"}
    if nullable:
        resolved = {**resolved, "nullable": True}
    if "description" in node:
        resolved = {**resolved, "description": node["description"]}
    return resolved


def _resolve_typed(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "description" in node:
        out["description"] = node["description"]
    json_type = node.get("type")
    if json_type in _TYPE_MAP:
        out["type"] = _TYPE_MAP[json_type]
    if json_type == "array" and "items" in node:
        out["items"] = _resolve(node["items"], defs)
    if json_type == "object" and "properties" in node:
        out["properties"] = {name: _resolve(prop, defs) for name, prop in node["properties"].items()}
        if "required" in node:
            out["required"] = node["required"]
    return out


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        return _resolve_ref(node, defs)
    if "anyOf" in node:
        return _resolve_any_of(node, defs)
    return _resolve_typed(node, defs)


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic's JSON Schema, translated into Gemini's ``responseSchema`` dialect.

    Gemini's structured-output schema rejects ``$ref``/``$defs`` outright and has
    no ``type: null`` union — both of which ``BaseModel.model_json_schema()``
    uses for every nested model and every ``X | None`` field here. Resolving
    ``$ref`` inline and folding a null branch into ``nullable: true`` is what
    lets the same Pydantic models that already validate Ollama's answers also
    constrain and validate Gemini's.
    """
    return _resolve(schema, schema.get("$defs", {}))
