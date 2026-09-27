# -*- coding: utf-8 -*-
"""Resolve the OpenShell gateway URL from the CLI's on-disk config.

The selected gateway name defaults to ``openshell``. Its endpoint lives
in ``~/.config/openshell/gateways/<name>/metadata.json``. When that file
is absent, the playbook keeps its fallback URL.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_GATEWAY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DEFAULT_GATEWAY_NAME = "openshell"


def openshell_gateway_endpoint(name: str, fallback: str, config_home: str | None = None) -> str:
    """Return ``gateway_endpoint`` for *name*, or *fallback* when unset."""
    gateway = str(name or "").strip() or _DEFAULT_GATEWAY_NAME
    if _GATEWAY_NAME.fullmatch(gateway) is None:
        return fallback
    home = config_home if config_home is not None else os.environ.get("HOME", "")
    meta = Path(home) / ".config" / "openshell" / "gateways" / gateway / "metadata.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    endpoint = data.get("gateway_endpoint")
    if not isinstance(endpoint, str):
        return fallback
    endpoint = endpoint.strip()
    if not endpoint.startswith("https://") or any(ch.isspace() for ch in endpoint):
        return fallback
    if "@" in endpoint.split("://", 1)[-1].split("/", 1)[0]:
        return fallback
    return endpoint


class FilterModule:
    def filters(self):
        return {
            "openshell_gateway_endpoint": openshell_gateway_endpoint,
        }
