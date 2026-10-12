# -*- coding: utf-8 -*-
"""The release bump opens its pull request with the release GitHub App."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUMP = ROOT / ".github" / "workflows" / "release-bump.yml"


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def test_release_bump_opens_the_pull_request_with_the_release_app():
    text = BUMP.read_text(encoding="utf-8")
    document = _load(BUMP)
    job = document["jobs"]["bump"]
    assert job["environment"] == "release"
    assert job["permissions"] == {"contents": "write"}
    step = next(item for item in job["steps"] if item.get("name") == "Open the release pull request")
    assert step["env"]["RELEASE_GITHUB_APP_ID"] == "${{ secrets.RELEASE_GITHUB_APP_ID }}"
    assert step["env"]["RELEASE_GITHUB_APP_PRIVATE_KEY"] == "${{ secrets.RELEASE_GITHUB_APP_PRIVATE_KEY }}"
    assert "GH_TOKEN" not in step["env"]
    assert "secrets.GITHUB_TOKEN" not in text
    assert "--id-env RELEASE_GITHUB_APP_ID" in step["run"]
    assert "--key-env RELEASE_GITHUB_APP_PRIVATE_KEY" in step["run"]
    assert "--permission contents=write" in step["run"]
    assert "--permission pull_requests=write" in step["run"]
    assert 'GH_TOKEN="$token" gh pr create' in step["run"]
