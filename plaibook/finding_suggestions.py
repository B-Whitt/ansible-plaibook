# -*- coding: utf-8 -*-
"""Decide whether a finding can be a GitHub Commit suggestion.

This lives in the installed package so ``plai review`` can check findings
without a source checkout. ``scripts/github_pr_review.py`` renders the
comment. The agent does not write it.
"""

from __future__ import annotations

import re
from typing import Any

_PROSE_START = re.compile(
    r"^(Add|Remove|Replace|Use|Change|Move|Update|Consider|Ensure|Set|Make|"
    r"Do not|Don't|Avoid|Prefer|Switch|Convert|Introduce|Extract|Refactor|"
    r"Handle|Check|Validate|Return|Raise|Wrap|Call|Pass|Include|Import|"
    r"Drop|Delete|Rename|Document|Note|This|The|A|An)\b",
    re.IGNORECASE,
)
_UNSAFE_LINE = re.compile(r"\b(TODO|FIXME|rest of|unchanged)\b", re.IGNORECASE)
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./@+-]+$")


def _safe_patch(body: str, limit: int = 12) -> bool:
    if not body.strip() or "..." in body or "…" in body or "```" in body:
        return False
    lines = body.splitlines()
    if not lines or len(lines) > limit:
        return False
    if any(line.startswith(("diff ", "+++", "---", "@@")) for line in lines):
        return False
    if any(_UNSAFE_LINE.search(line) for line in lines):
        return False
    return True


def _looks_like_prose(body: str) -> bool:
    if re.search(r"[=(){}\[\]<>]|^\s+\S", body, re.MULTILINE):
        return False
    first = body.strip().splitlines()[0] if body.strip() else ""
    if _PROSE_START.match(first):
        return True
    return "\n" not in body and body.rstrip().endswith(".") and " " in body


def _valid_location(finding: dict[str, Any]) -> bool:
    path = str(finding.get("file") or "")
    if not path or ".." in path.split("/") or not _SAFE_PATH.fullmatch(path):
        return False
    try:
        line = int(finding["line"])
    except (KeyError, TypeError, ValueError):
        return False
    return line >= 1


def comment_anchor(finding: dict[str, Any]) -> dict[str, Any]:
    line = int(finding["line"])
    anchor: dict[str, Any] = {"path": str(finding["file"]), "line": line}
    raw_start = finding.get("start_line")
    if raw_start in (None, ""):
        return anchor
    try:
        start_line = int(raw_start)
    except (TypeError, ValueError):
        return anchor
    if 1 <= start_line < line:
        anchor["start_line"] = start_line
    return anchor


def _has_replacement(finding: dict[str, Any]) -> bool:
    raw = finding.get("replacement")
    return isinstance(raw, str) and bool(raw.strip())


def _replacement_body(finding: dict[str, Any]) -> str | None:
    """Return drop-in source from ``replacement``. The ``fix`` text is never used."""
    raw = finding.get("replacement")
    if not isinstance(raw, str) or not raw.strip():
        return None
    body = raw.strip("\n")
    if not _safe_patch(body, limit=40) or _looks_like_prose(body):
        return None
    return body


def suggestion_errors(finding: dict[str, Any]) -> list[str]:
    """Reasons a provided replacement cannot be posted as a Commit suggestion.

    A finding with no replacement is still reported. It is listed in the
    review summary instead of being dropped or turned into a suggestion.
    """
    if not isinstance(finding, dict):
        return ["finding is not an object"]
    if not _has_replacement(finding):
        return []
    where = f"{finding.get('file')}:{finding.get('line')}"
    detail = str(finding.get("description") or "").strip().splitlines()
    suffix = f" ({detail[0][:160]})" if detail and detail[0] else ""
    if not _valid_location(finding):
        return [f"{where} needs a repository-relative file and a line >= 1{suffix}"]
    body = _replacement_body(finding)
    if body is None:
        return [f"{where} replacement is not drop-in source for those lines{suffix}"]
    if "\n" in body and "start_line" not in comment_anchor(finding):
        return [f"{where} multi-line replacement needs start_line{suffix}"]
    return []


def suggestion_replacement(finding: dict[str, Any]) -> str | None:
    """Return ``replacement`` when GitHub can commit it, otherwise None."""
    if suggestion_errors(finding):
        return None
    return _replacement_body(finding)
