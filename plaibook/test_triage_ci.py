# -*- coding: utf-8 -*-
"""CI triage reads failing-check URLs. It does not call a model."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _triage():
    path = (
        Path(__file__).resolve().parents[1]
        / "roles/review/files/pr_review/triage_ci_failures.py"
    )
    spec = importlib.util.spec_from_file_location("triage_ci_failures", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_github_annotations_and_log_tail_name_the_failed_step():
    mod = _triage()
    secret = "ghp_" + ("a" * 20)
    log = ("noise\n" * 400) + f"failed with {secret}\n"
    job = {
        "steps": [
            {"name": "Setup", "conclusion": "success", "number": 1},
            {"name": "Run pytest", "conclusion": "failure", "number": 2},
        ],
        "check_run_url": "https://api.github.com/repos/org/repo/check-runs/9",
    }
    annotations = [{"annotation_level": "failure", "message": "AssertionError: expected true"}]

    def fetch(url: str) -> tuple[int, str]:
        if url.endswith("/actions/jobs/2"):
            return 200, json.dumps(job)
        if url.endswith("/annotations"):
            return 200, json.dumps(annotations)
        if url.endswith("/logs"):
            return 200, log
        raise AssertionError(url)

    found = mod.diagnose_github(
        {
            "name": "ci/unit-tests",
            "url": "https://github.com/org/repo/actions/runs/1/job/2",
        },
        fetch,
    )
    assert found["job"] == "ci/unit-tests"
    assert found["step"] == "Run pytest"
    assert "AssertionError" in found["error"]
    assert secret not in found["error"]
    assert len(found["error"]) <= 800


def test_gitlab_job_trace_is_the_diagnosis():
    mod = _triage()

    def fetch(url: str) -> tuple[int, str]:
        assert url == "https://gitlab.example/api/v4/projects/org%2Frepo/jobs/9/trace"
        return 200, "ERROR: compiler failed\n"

    found = mod.diagnose_gitlab(
        {"name": "build", "url": "https://gitlab.example/org/repo/-/jobs/9"},
        fetch,
    )
    assert found["job"] == "build"
    assert found["step"] == "build"
    assert "compiler failed" in found["error"]


def test_unknown_check_url_does_not_fetch():
    mod = _triage()

    def fetch(_url: str) -> tuple[int, str]:
        raise AssertionError("network")

    found = mod.diagnose_checks(
        "github",
        [{"name": "ci/lint", "url": "https://example.test/not-a-job"}],
        fetch,
    )
    assert found[0]["job"] == "ci/lint"
    assert found[0]["error"] == "CI log was not available."
