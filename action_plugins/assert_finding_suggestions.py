# -*- coding: utf-8 -*-
"""Assert every posted finding is a GitHub Commit suggestion.

The review agent returns structured fields. This task checks those
fields. ``scripts/github_pr_review.py`` renders the comment. Neither
step asks a model to write the GitHub comment.
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


class ActionModule(ActionBase):
    """Collect suggestion-construction failures. The following assert task fails the run."""

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
        failures = suggestion_failures(findings)
        result["changed"] = False
        result["failures"] = failures
        result["ok"] = not failures
        return result
