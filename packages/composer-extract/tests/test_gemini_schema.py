"""The schema translation that makes Pydantic's JSON Schema usable as Gemini's
``responseSchema``: $ref/$defs resolved inline, nullable unions folded down."""

from __future__ import annotations

from composer_extract.gemini_schema import to_gemini_schema
from composer_extract.schema import PageExtraction


def test_to_gemini_schema_resolves_refs_and_nullable_unions() -> None:
    schema = to_gemini_schema(PageExtraction.model_json_schema())

    assert schema["type"] == "OBJECT"
    concert = schema["properties"]["concerts"]["items"]
    assert concert["type"] == "OBJECT"
    assert concert["properties"]["date"] == {
        "type": "STRING",
        "nullable": True,
        "description": "Concert date as ISO-8601 (YYYY-MM-DD); null if unknown.",
    }
    soloist = concert["properties"]["soloists"]["items"]
    assert soloist["type"] == "OBJECT"
    assert soloist["required"] == ["name"]
    assert "$ref" not in str(schema)
    assert "$defs" not in schema
