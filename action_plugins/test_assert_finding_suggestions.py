# -*- coding: utf-8 -*-
"""A bad replacement is cleared. The finding stays in the review."""

from assert_finding_suggestions import repair_suggestions, suggestion_errors, suggestion_failures


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


def test_repair_keeps_the_finding_when_the_replacement_cannot_be_committed():
    valid = {
        "file": "app.py",
        "line": 4,
        "replacement": "return name.endswith(suffix)\n",
        "description": "missing suffix",
        "evidence_status": "verified",
    }
    prose = dict(valid)
    prose["replacement"] = "Add a suffix check."
    multiline = dict(valid)
    multiline["line"] = 5
    multiline["description"] = "missing suffix on the next line"
    multiline["replacement"] = "return name.endswith(suffix)\nreturn False\n"
    refuted = dict(prose)
    refuted["evidence_status"] = "refuted"
    findings, warnings = repair_suggestions([valid, prose, multiline, refuted])
    assert findings[0]["replacement"] == valid["replacement"]
    assert findings[1]["replacement"] == ""
    assert findings[1]["description"] == "missing suffix"
    assert findings[2]["replacement"] == ""
    assert findings[3]["replacement"] == "Add a suffix check."
    assert len(warnings) == 2
    assert suggestion_errors(findings[1]) == []
    assert suggestion_errors(findings[2]) == []
    assert any("start_line" in item for item in warnings)
