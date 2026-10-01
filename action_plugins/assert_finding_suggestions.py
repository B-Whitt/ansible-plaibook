# -*- coding: utf-8 -*-
"""Drop replacements GitHub cannot commit, and keep the findings.

The review agent returns structured fields. A bad ``replacement`` is
cleared so the finding stays in the summary and the play continues.
``scripts/github_pr_review.py`` renders the comment. Neither step asks
a model to write the GitHub comment.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ansible.plugins.action import ActionBase  # noqa: E402


def _suggestion_errors():
    """Import the publisher's checker from the installed package.

    ``pip install plaibook`` copies this file into ``plaibook/share``.
    The checker itself is ``plaibook.finding_suggestions``. A source
    checkout that has not installed the package can still load
    ``scripts/github_pr_review.py``.
    """
    try:
        from plaibook.finding_suggestions import suggestion_errors

        return suggestion_errors
    except ImportError:
        pass
    candidates = [
        Path(__file__).resolve().parents[1] / "scripts",
        Path.cwd() / "scripts",
    ]
    for path in candidates:
        if (path / "github_pr_review.py").is_file():
            entry = str(path)
            if entry not in sys.path:
                sys.path.insert(0, entry)
            break
    else:
        raise ImportError(
            "plaibook.finding_suggestions is not installed and "
            "github_pr_review.py was not found next to the playbook or in ./scripts"
        )
    from github_pr_review import suggestion_errors

    return suggestion_errors


suggestion_errors = _suggestion_errors()


def suggestion_failures(findings) -> list[str]:
    """Error strings for findings that cannot be posted as suggestion blocks."""
    if not isinstance(findings, list):
        return ["findings must be a list"]
    failures: list[str] = []
    for finding in findings:
        if isinstance(finding, dict) and finding.get("evidence_status") == "refuted":
            continue
        failures.extend(suggestion_errors(finding))
    return failures


def repair_suggestions(findings) -> tuple[list, list[str]]:
    """Clear replacements GitHub cannot commit. Keep every finding.

    A refuted finding is left unchanged. It is not posted. Every other
    finding with a bad replacement stays in the review with ``replacement``
    set to an empty string, so the summary still lists it and the play
    does not fail after the agents have already returned.
    """
    if not isinstance(findings, list):
        raise TypeError("findings must be a list")
    repaired: list = []
    warnings: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            repaired.append(finding)
            warnings.append("finding is not an object")
            continue
        if finding.get("evidence_status") == "refuted":
            repaired.append(finding)
            continue
        errors = suggestion_errors(finding)
        if not errors:
            repaired.append(finding)
            continue
        warnings.extend(errors)
        cleaned = dict(finding)
        cleaned["replacement"] = ""
        repaired.append(cleaned)
    return repaired, warnings


class ActionModule(ActionBase):
    """Clear uncommittable replacements and return the findings. The play continues."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("findings",))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = {}
        result = super().run(tmp, task_vars)
        del tmp
        findings = self._task.args.get("findings")
        if findings is None:
            result["failed"] = True
            result["msg"] = "assert_finding_suggestions requires a 'findings' argument"
            return result
        try:
            repaired, warnings = repair_suggestions(findings)
        except TypeError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result
        result["changed"] = False
        result["findings"] = repaired
        result["warnings"] = warnings
        result["failures"] = warnings
        result["ok"] = True
        return result
