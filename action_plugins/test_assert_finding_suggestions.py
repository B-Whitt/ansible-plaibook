# -*- coding: utf-8 -*-
"""The suggestion assert rejects a finding that cannot be committed."""

from assert_finding_suggestions import suggestion_errors, suggestion_failures


def test_suggestion_checker_comes_from_the_installed_package():
    assert suggestion_errors.__module__ == "plaibook.finding_suggestions"


def test_suggestion_failures_ignore_refuted_and_reject_prose():
    valid = {
        "file": "app.py",
        "line": 4,
        "replacement": "return name.endswith(suffix)\n",
        "evidence_status": "verified",
    }
    prose = dict(valid)
    prose["replacement"] = "Add a suffix check."
    prose["description"] = "missing suffix"
    refuted = dict(prose)
    refuted["evidence_status"] = "refuted"
    omitted = dict(valid)
    omitted.pop("replacement")
    assert suggestion_failures([valid, refuted, omitted]) == []
    failures = suggestion_failures([prose])
    assert len(failures) == 1
    assert "drop-in source" in failures[0]
    assert "missing suffix" in failures[0]
