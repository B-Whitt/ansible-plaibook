# -*- coding: utf-8 -*-
"""The public review workflow runs plai and does not call a controller."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".github" / "workflows" / "plai-review-run.yml"
CALLER = ROOT / ".github" / "workflows" / "plai-review.yml"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 parses the bare key "on" as true.
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def test_reusable_workflow_permissions_and_pins():
    text = RUN.read_text(encoding="utf-8")
    document = _load(RUN)
    assert document["permissions"] == {}
    assert "workflow_call" in document["on"]
    assert "workflow_run" not in document["on"]
    job = document["jobs"]["review"]
    assert job["name"] == "plaibook review"
    assert list(document["jobs"]) == ["review"]
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert job["runs-on"] == "ubuntu-26.04-arm"
    assert "strategy" not in job
    assert "checks" not in job["permissions"]
    assert "actions" not in job["permissions"]
    plai_steps = [step for step in job["steps"] if step.get("id") == "plai"]
    assert len(plai_steps) == 1
    assert "CURSOR_API_KEY" in plai_steps[0]["env"]
    assert "PLAI_GITHUB_APP_PRIVATE_KEY" not in plai_steps[0].get("env", {})
    assert "--no-sandbox" not in plai_steps[0]["run"]
    assert "sandbox_wait_timeout=600" in plai_steps[0]["run"]
    assert "6648bd0c290efbc41ba131ee9831ee45cd431f94/install.sh" in text
    assert "sha256sum -c -" in text
    assert "OPENSHELL_VERSION=v0.1.2" in text
    assert "GITHUB_PATH" in text
    digest = "sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e"
    image = f"ghcr.io/nvidia/openshell-community/sandboxes/base@{digest}"
    assert image in text
    assert "base:latest" not in text
    assert 'sha="${GITHUB_SHA}"' not in text
    assert "workflow_run" not in text
    assert "checksums.txt" in text
    assert "plaibook-image-warm" in text
    assert "policy set --global --yes" in text
    assert "OPENSHELL_PROVISION_TIMEOUT" in text
    inputs = document["on"]["workflow_call"]["inputs"]
    assert inputs["source_sha"]["required"] is False
    assert inputs["source_repository"]["required"] is False
    assert "inputs.source_sha" in text
    publish = [step for step in job["steps"] if step.get("name") == "Post the review"]
    assert len(publish) == 1
    assert "PLAI_GITHUB_APP_PRIVATE_KEY" in publish[0]["env"]
    assert "github.token" not in yaml.dump(publish[0])
    secrets = document["on"]["workflow_call"]["secrets"]
    assert "cursor_api_key" in secrets
    assert secrets["cursor_api_key"]["required"] is True
    assert secrets["github_app_id"]["required"] is False
    assert secrets["github_app_private_key"]["required"] is False
    uses = [step["uses"].split()[0] for step in job["steps"] if "uses" in step]
    for item in uses:
        action, _, sha = item.partition("@")
        assert action
        assert _SHA.fullmatch(sha), item
    assert "plai review" in text
    assert "CURSOR_API_KEY" in text
    assert "ANSIBLE_REVIEW_AGENT_FAMILY: cursor" in text
    assert "PLAIBOOK_PROVIDER_TOKEN" not in text
    assert "checks: write" not in text
    assert "actions: write" not in text
    assert "pull-requests: write" not in text
    assert "--no-sandbox" not in text
    assert "review_require_ci_passing=false" in text
    assert "job.workflow_sha" in text
    assert "job.workflow_repository" in text
    assert "GITHUB_WORKFLOW_SHA" not in text
    assert "GITHUB_WORKFLOW_REF" not in text
    assert "github_app_token.py" in text
    assert "PLAI_GITHUB_APP_ID" in text
    assert "::add-mask::" in text
    assert 'echo "$PLAI_GITHUB_APP_PRIVATE_KEY"' not in text
    assert "echo ${PLAI_GITHUB_APP_PRIVATE_KEY}" not in text
    assert "--post" not in text
    lowered = text.lower()
    assert "aap.aknochow.io" not in lowered
    assert "controller_host" not in lowered
    assert "ansible.controller" not in lowered
    assert "plaibook_dispatch_token" not in lowered


def test_caller_does_not_pin_a_floating_secret_ref():
    text = CALLER.read_text(encoding="utf-8")
    document = _load(CALLER)
    assert document["permissions"] == {}
    job = document["jobs"]["review"]
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    assert "checks" not in job["permissions"]
    assert job["uses"] == "./.github/workflows/plai-review-run.yml"
    assert job["secrets"]["cursor_api_key"] == "${{ secrets.CURSOR_API_KEY }}"
    assert job["secrets"]["github_app_id"] == "${{ secrets.PLAI_GITHUB_APP_ID }}"
    assert job["secrets"]["github_app_private_key"] == "${{ secrets.PLAI_GITHUB_APP_PRIVATE_KEY }}"
    assert "@main" not in text
    assert "CURSOR_API_KEY" in text
    assert "PLAIBOOK_PROVIDER_TOKEN" not in text
    assert "PLAIBOOK_DISPATCH_TOKEN" not in text
    assert "checks: write" not in text
    assert "actions: write" not in text
    assert "pull-requests: write" not in text
    assert document["on"]["pull_request"]["types"] == ["opened", "synchronize", "reopened"]
    assert document["concurrency"]["cancel-in-progress"] is True
    assert document["concurrency"]["group"] == (
        "plaibook-review-${{ github.event.pull_request.number || "
        "github.event.issue.number || github.event.inputs.pr_number || github.run_id }}"
    )
    assert "head.sha" not in document["concurrency"]["group"]
