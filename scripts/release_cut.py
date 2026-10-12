#!/usr/bin/env python3
"""Publish a non-prerelease GitHub Release for the version committed on main.

The token is a GitHub App installation token. A release created with the
default GITHUB_TOKEN does not trigger publish.yml or ee-publish.yml.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from github_app_token import Fetch, app_credentials, mint_installation_token
from github_app_token import _fetch as fetch_jwt
from release_version import parse_version, read_version

_UNSET = (
    "RELEASE_GITHUB_APP_ID and RELEASE_GITHUB_APP_PRIVATE_KEY are unset. "
    "Set them on the release environment. A release created with GITHUB_TOKEN "
    "does not trigger publish.yml or ee-publish.yml."
)


def release_tag(version: str) -> str:
    parse_version(version)
    return f"v{version}"


def release_payload(version: str, sha: str) -> dict[str, Any]:
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("release commit must be a 40-character lowercase sha")
    tag = release_tag(version)
    return {
        "tag_name": tag,
        "target_commitish": sha,
        "name": tag,
        "body": f"Release {tag} of plaibook.",
        "draft": False,
        "prerelease": False,
    }


def cut_release(repo: str, version: str, sha: str, token: str, fetch: Fetch) -> str:
    tag = release_tag(version)
    status, payload = fetch("GET", f"https://api.github.com/repos/{repo}/git/ref/tags/{tag}", token, None)
    if status == 200:
        raise RuntimeError(f"tag {tag} already exists")
    if status != 404:
        raise RuntimeError(f"unable to check tag {tag} ({_status_message(status, payload)})")
    status, payload = fetch(
        "POST",
        f"https://api.github.com/repos/{repo}/releases",
        token,
        release_payload(version, sha),
    )
    if status not in (200, 201) or not isinstance(payload, dict):
        raise RuntimeError(f"unable to create release {tag} ({_status_message(status, payload)})")
    url = payload.get("html_url")
    if not isinstance(url, str) or not url:
        raise RuntimeError(f"release {tag} was created without a url")
    return url


def _status_message(status: int, payload: Any) -> str:
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message:
            return f"{status} {message}"
    return str(status)


def _fetch(method: str, url: str, token: str, payload: dict[str, Any] | None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ansible-plaibook-release",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, None


def head_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or len(sha) != 40:
        raise RuntimeError("unable to read the current commit")
    return sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tag and publish the plaibook version on main")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--sha", default="")
    args = parser.parse_args(argv)
    creds = None
    try:
        creds = app_credentials("RELEASE_GITHUB_APP_ID", "RELEASE_GITHUB_APP_PRIVATE_KEY")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if creds is None:
        print(_UNSET, file=sys.stderr)
        return 1
    try:
        version = read_version(args.root)
        sha = args.sha or head_sha(args.root)
        app_id, pem = creds
        token = mint_installation_token(
            app_id,
            pem,
            args.repo,
            fetch_jwt,
            permissions={"contents": "write"},
        )
        url = cut_release(args.repo, version, sha, token, _fetch)
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
