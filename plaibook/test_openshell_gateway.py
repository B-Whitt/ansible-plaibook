# -*- coding: utf-8 -*-
"""The review uses the openshell CLI's recorded gateway endpoint."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _filter():
    path = Path(__file__).resolve().parents[1] / "filter_plugins" / "openshell_gateway.py"
    spec = importlib.util.spec_from_file_location("openshell_gateway", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_configured_gateway_endpoint_replaces_the_fallback(tmp_path):
    mod = _filter()
    gateway = tmp_path / ".config" / "openshell" / "gateways" / "openshell"
    gateway.mkdir(parents=True)
    (gateway / "metadata.json").write_text(
        json.dumps({"name": "openshell", "gateway_endpoint": "https://localhost:17670"}),
        encoding="utf-8",
    )
    assert mod.openshell_gateway_endpoint(
        "openshell",
        "https://host.openshell.internal:17670",
        config_home=str(tmp_path),
    ) == "https://localhost:17670"


def test_missing_config_keeps_the_fallback(tmp_path):
    mod = _filter()
    fallback = "https://host.openshell.internal:17670"
    assert mod.openshell_gateway_endpoint("openshell", fallback, config_home=str(tmp_path)) == fallback


def test_blank_name_defaults_to_openshell(tmp_path):
    mod = _filter()
    gateway = tmp_path / ".config" / "openshell" / "gateways" / "openshell"
    gateway.mkdir(parents=True)
    (gateway / "metadata.json").write_text(
        json.dumps({"gateway_endpoint": "https://localhost:17670"}),
        encoding="utf-8",
    )
    assert mod.openshell_gateway_endpoint("", "https://fallback.example", config_home=str(tmp_path)) == (
        "https://localhost:17670"
    )


def test_unsafe_endpoint_keeps_the_fallback(tmp_path):
    mod = _filter()
    gateway = tmp_path / ".config" / "openshell" / "gateways" / "openshell"
    gateway.mkdir(parents=True)
    (gateway / "metadata.json").write_text(
        json.dumps({"gateway_endpoint": "http://localhost:17670"}),
        encoding="utf-8",
    )
    fallback = "https://host.openshell.internal:17670"
    assert mod.openshell_gateway_endpoint("openshell", fallback, config_home=str(tmp_path)) == fallback


def test_gateway_name_cannot_escape_the_config_dir(tmp_path):
    mod = _filter()
    fallback = "https://host.openshell.internal:17670"
    assert mod.openshell_gateway_endpoint("../openshell", fallback, config_home=str(tmp_path)) == fallback
