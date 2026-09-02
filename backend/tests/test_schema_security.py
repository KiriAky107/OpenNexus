import pytest

from app.schema_security import (
    ExternalSchemaReferenceError,
    UnresolvableLocalSchemaReferenceError,
    reject_external_schema_references,
)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "file:///host/private-schema.json"},
        {"properties": {"value": {"$ref": "https://schema.invalid/value.json"}}},
        {"allOf": [{"$dynamicRef": "https://schema.invalid/dynamic"}]},
    ],
)
def test_external_json_schema_references_are_rejected(schema) -> None:
    with pytest.raises(ExternalSchemaReferenceError):
        reject_external_schema_references(schema)


def test_local_json_schema_fragment_reference_is_allowed() -> None:
    reject_external_schema_references(
        {
            "$defs": {"value": {"type": "string"}},
            "properties": {"value": {"$ref": "#/$defs/value"}},
        }
    )


@pytest.mark.parametrize("reference", ["#/$defs/missing", "#missing-anchor"])
def test_unresolvable_local_schema_reference_is_rejected(reference: str) -> None:
    with pytest.raises(UnresolvableLocalSchemaReferenceError):
        reject_external_schema_references({"type": "object", "$ref": reference})


def test_root_reference_cannot_use_anchor_from_nested_schema_resource() -> None:
    schema = {
        "$defs": {
            "nested": {
                "$id": "nested",
                "$anchor": "inside",
                "type": "string",
            }
        },
        "properties": {"value": {"$ref": "#inside"}},
    }

    with pytest.raises(UnresolvableLocalSchemaReferenceError):
        reject_external_schema_references(schema)


def test_nested_schema_resource_can_resolve_its_own_anchor() -> None:
    schema = {
        "$defs": {
            "nested": {
                "$id": "nested",
                "$anchor": "inside",
                "allOf": [{"$ref": "#inside"}],
            }
        }
    }

    reject_external_schema_references(schema)
