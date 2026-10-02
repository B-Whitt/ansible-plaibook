# -*- coding: utf-8 -*-
"""OpenAI strict schemas must require every property.

Strict mode rejects a schema that lists a property and leaves it out of
``required``. That mismatch used to pass unit tests, then fail a review
after the agents had already returned.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VARS = ROOT / "roles" / "review" / "vars" / "main.yml"
_TEMPLATE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
_STRICT_SCHEMAS = {
    "security_findings_schema",
    "review_findings_schema",
    "continuity_audit_schema",
}


def _load_yaml(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), path
    return document


def resolve(value, variables: dict):
    """Substitute ``{{ name }}`` vars the way the review playbook does."""
    if isinstance(value, str):
        match = _TEMPLATE.fullmatch(value.strip())
        if match:
            name = match.group(1)
            if name not in variables:
                raise KeyError(name)
            return resolve(variables[name], variables)
        return value
    if isinstance(value, dict):
        return {key: resolve(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, variables) for item in value]
    return value


def strict_schema_names() -> list[str]:
    """Schema var names passed to OpenAI with ``strict: true``."""
    names: list[str] = []
    for path in sorted((ROOT / "roles").rglob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        names.extend(_strict_names(document))
    return names


def _strict_names(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("strict") is True and "schema" in node:
            schema = node["schema"]
            match = _TEMPLATE.fullmatch(str(schema).strip())
            if match is None:
                raise AssertionError(f"strict schema is not a var reference: {schema!r}")
            found.append(match.group(1))
        for value in node.values():
            found.extend(_strict_names(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_strict_names(item))
    return found


def strict_violations(schema, path: str = "$") -> list[str]:
    """OpenAI strict-mode violations. Optional properties are violations."""
    if not isinstance(schema, dict):
        return [f"{path} is not an object schema"]
    violations: list[str] = []
    if isinstance(schema.get("type"), str) and "{{" in schema["type"]:
        violations.append(f"{path} still contains a template")
    if "$ref" in schema:
        violations.append(f"{path} uses $ref")
    object_schema = schema.get("type") == "object" or "properties" in schema
    if object_schema:
        if schema.get("additionalProperties") is not False:
            violations.append(f"{path} additionalProperties must be false")
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            violations.append(f"{path} properties must be an object")
            properties = {}
        required = schema.get("required") or []
        if not isinstance(required, list):
            violations.append(f"{path} required must be a list")
            required = []
        missing = [name for name in properties if name not in required]
        if missing:
            violations.append(f"{path} properties not required: {', '.join(missing)}")
        extra = [name for name in required if name not in properties]
        if extra:
            violations.append(f"{path} required names missing from properties: {', '.join(extra)}")
        for name, child in properties.items():
            violations.extend(strict_violations(child, f"{path}.{name}"))
    if schema.get("type") == "array" or "items" in schema:
        items = schema.get("items")
        if isinstance(items, str):
            violations.append(f"{path}.items is an unresolved template")
        elif isinstance(items, dict):
            violations.extend(strict_violations(items, f"{path}.items"))
        else:
            violations.append(f"{path}.items must be one schema")
    for key in ("anyOf", "oneOf", "allOf"):
        if key not in schema:
            continue
        branches = schema[key]
        if not isinstance(branches, list):
            violations.append(f"{path}.{key} must be a list")
            continue
        for index, child in enumerate(branches):
            violations.extend(strict_violations(child, f"{path}.{key}[{index}]"))
    return violations


def test_optional_property_is_a_strict_violation():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "replacement": {"type": "string"},
            "start_line": {"type": "integer"},
        },
        "required": ["replacement"],
    }
    violations = strict_violations(schema)
    assert any("start_line" in item for item in violations)


def test_openai_strict_schemas_require_every_property():
    names = strict_schema_names()
    assert _STRICT_SCHEMAS <= set(names)
    assert "explore_tools" not in names
    variables = _load_yaml(VARS)
    item = resolve(variables["_finding_item"], variables)
    assert item["required"] == [
        "lens",
        "file",
        "line",
        "severity",
        "description",
        "evidence",
        "confidence",
        "fix",
        "replacement",
        "start_line",
    ]
    for name in names:
        schema = resolve(variables[name], variables)
        assert strict_violations(schema) == [], name
