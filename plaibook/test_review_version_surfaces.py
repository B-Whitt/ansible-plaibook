# -*- coding: utf-8 -*-
"""Review outputs share the same plaibook version line as plai --version."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

from jinja2 import Environment

from plaibook import version_text
from plaibook.summary import format_pretty

SHA = "664b8e1d39acf7c6df6ed432fcd294dd8d318afa"


def _filter_module():
    path = Path(__file__).resolve().parents[1] / "filter_plugins" / "plaibook_version.py"
    spec = importlib.util.spec_from_file_location("plaibook_version_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_git_repo(path: Path, *, branch: str | None = "main") -> None:
    subprocess.run(["git", "init", "-b", branch or "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "--allow-empty", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00",
        },
    )


def test_version_text_uses_github_workspace_when_direct_url_is_missing(monkeypatch, tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _init_git_repo(repo)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.delenv("PLAIBOOK_ROOT", raising=False)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setattr("plaibook._direct_url", lambda: None)
    line = version_text()
    assert line.endswith(f"(dev, main@{head[:12]})")


def test_format_pretty_footer_includes_version_line(monkeypatch):
    monkeypatch.setattr("plaibook.summary.version_text", lambda: "plaibook 0.1.28 (dev, main@abc)")
    pretty = format_pretty({"status": "ok", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW", "score": 100}]})
    assert "plaibook 0.1.28 (dev, main@abc)" in pretty


def test_final_review_summary_template_includes_version_line(monkeypatch):
    monkeypatch.setattr("plaibook.version_text", lambda direct_url=...: "plaibook 0.1.28 (dev, feat/x@664b8e1d39ac)")
    filters = _filter_module().FilterModule().filters()
    env = Environment()
    env.filters.update(filters)
    template_path = (
        Path(__file__).resolve().parents[1]
        / "roles"
        / "review"
        / "templates"
        / "final_review_summary.j2"
    )
    assert "plaibook_version_line" in template_path.read_text(encoding="utf-8")
    rendered = env.from_string("Plaibook: {{ '' | plaibook_version_line }}").render()
    assert rendered == "Plaibook: plaibook 0.1.28 (dev, feat/x@664b8e1d39ac)"


def test_github_summary_body_includes_version_line(monkeypatch):
    from scripts.github_pr_review import summary_body

    monkeypatch.setattr("scripts.github_pr_review.version_text", lambda: f"plaibook 0.1.28 (dev, {SHA[:12]})")
    body = summary_body({"status": "ok", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW", "score": 90}]}, [])
    assert f"plaibook 0.1.28 (dev, {SHA[:12]})" in body
    assert "plaibook review: **READY_FOR_HUMAN_REVIEW**" in body
