# -*- coding: utf-8 -*-
"""Review outputs share the same plaibook version line as plai --version."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

from jinja2 import Environment

from plaibook import __version__, version_text
from plaibook.summary import format_pretty

ROOT = Path(__file__).resolve().parents[1]


def _filter_module():
    path = ROOT / "filter_plugins" / "plaibook_version.py"
    spec = importlib.util.spec_from_file_location("plaibook_version_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_git_repo(path: Path) -> None:
    (path / "pyproject.toml").write_text('name = "plaibook"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "init"],
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
    assert version_text() == f"plaibook {__version__} (dev, main@{head[:12]})"


def test_index_install_stays_on_the_package_version(monkeypatch, tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setattr(
        "plaibook._direct_url",
        lambda: {"url": "https://files.pythonhosted.org/packages/plaibook.whl"},
    )
    assert version_text() == f"plaibook {__version__}"


def test_format_pretty_footer_is_version_text():
    pretty = format_pretty({"status": "ok", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW", "score": 100}]})
    assert version_text() in pretty.splitlines()


def test_final_review_summary_template_is_version_text():
    filters = _filter_module().FilterModule().filters()
    template = (ROOT / "roles" / "review" / "templates" / "final_review_summary.j2").read_text(encoding="utf-8")
    assert template.splitlines()[1] == "{{ '' | plaibook_version_line }}"
    env = Environment()
    env.filters.update(filters)
    assert env.from_string(template.splitlines()[1]).render() == version_text()


def test_github_summary_body_is_version_text():
    from scripts.github_pr_review import summary_body

    body = summary_body({"status": "ok", "targets": [{"verdict": "READY_FOR_HUMAN_REVIEW", "score": 90}]}, [])
    lines = body.splitlines()
    assert lines[0] == "plaibook review: **READY_FOR_HUMAN_REVIEW**"
    assert lines[2] == version_text()
