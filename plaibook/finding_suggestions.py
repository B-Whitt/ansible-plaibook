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
# A call such as Update(record) is source even when the name is an imperative word.
_CALL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(")
# An assignment such as ``A = 1`` is source even when the name is an imperative word.
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
# Case-sensitive so "Return the value." stays prose and "return value" stays source.
_STATEMENT = re.compile(
    r"^(?:return|raise|pass|break|continue|yield|assert|del|global|nonlocal|"
    r"import|from|with|for|while|if|elif|else|try|except|finally|class|def|"
    r"async|await|lambda|match|case)\b"
)


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
    """True when the replacement is a sentence rather than source.

    A programming statement such as ``return value`` or ``import os`` is
    source, including when it is indented. Imperative prose is rejected
    before punctuation is treated as proof of source, so ``Use timeout=30.``
    is not a suggestion.
    """
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if any(_STATEMENT.match(line) or _CALL.match(line) or _ASSIGN.match(line) for line in lines):
        return False
    first = lines[0] if lines else ""
    if _PROSE_START.match(first):
        return True
    if "\n" not in body and body.rstrip().endswith(".") and " " in body:
        return True
    if re.search(r"[=(){}\[\]<>]", body):
        return False
    return False


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
    # A multi-line body may replace one cited line. comment_anchor omits
    # start_line when it equals line, which is the single-line GitHub anchor.
    if "\n" in body:
        raw_start = finding.get("start_line")
        try:
            start_line = int(raw_start)
            line = int(finding["line"])
        except (KeyError, TypeError, ValueError):
            return [f"{where} multi-line replacement needs start_line{suffix}"]
        if not 1 <= start_line <= line:
            return [f"{where} multi-line replacement needs start_line{suffix}"]
    return []


def suggestion_replacement(finding: dict[str, Any]) -> str | None:
    """Return ``replacement`` when GitHub can commit it, otherwise None."""
    if suggestion_errors(finding):
        return None
    return _replacement_body(finding)
