#!/usr/bin/env python3
"""Mint a GitHub App installation token for posting a plaibook review.

The workflow uses this only when PLAI_GITHUB_APP_ID and
PLAI_GITHUB_APP_PRIVATE_KEY are set. The token is printed on stdout and
nowhere else. Name the GitHub App ``plai-review`` so the review author is
``plai-review[bot]``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
Fetch = Callable[[str, str, str, dict[str, Any] | None], tuple[int, Any]]


def app_credentials() -> tuple[str, str] | None:
    app_id = os.environ.get("PLAI_GITHUB_APP_ID", "").strip()
    raw_key = os.environ.get("PLAI_GITHUB_APP_PRIVATE_KEY", "")
    if not app_id or not raw_key.strip():
        return None
    if not app_id.isdigit():
        raise RuntimeError("PLAI_GITHUB_APP_ID must be numeric")
    return app_id, normalize_pem(raw_key)


def normalize_pem(raw: str) -> str:
    text = raw.strip()
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    if "-----BEGIN " not in text or "-----END " not in text:
        raise RuntimeError("PLAI_GITHUB_APP_PRIVATE_KEY is not a PEM private key")
    if not text.endswith("\n"):
        text += "\n"
    return text


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_jwt(app_id: str, pem: str, now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(
        json.dumps(
            {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _sign_rs256(f"{header}.{payload}".encode("ascii"), pem)
    return f"{header}.{payload}.{signature}"


def _sign_rs256(signing_input: bytes, pem: str) -> str:
    with tempfile.TemporaryDirectory(prefix="plai-app-") as directory:
        key_path = os.path.join(directory, "key.pem")
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(pem)
        try:
            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_path],
                input=signing_input,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("unable to sign the GitHub App JWT") from None
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("unable to sign the GitHub App JWT")
    return _b64url(result.stdout)


def mint_installation_token(
    app_id: str,
    pem: str,
    repo: str,
    fetch: Fetch,
    now: int | None = None,
) -> str:
    if not _REPO.fullmatch(repo):
        raise RuntimeError("repository must be owner/name")
    owner_repo = repo.split("/", 1)
    jwt = build_jwt(app_id, pem, now=now)
    status, payload = fetch("GET", f"https://api.github.com/repos/{repo}/installation", jwt, None)
    installation_id = _installation_id(payload) if status == 200 else None
    if installation_id is None:
        raise RuntimeError(f"unable to find the GitHub App installation ({status})")
    status, payload = fetch(
        "POST",
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        jwt,
        {"repositories": [owner_repo[1]], "permissions": {"pull_requests": "write"}},
    )
    token = payload.get("token") if status in (200, 201) and isinstance(payload, dict) else ""
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"unable to mint a GitHub App installation token ({status})")
    return token


def _installation_id(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    try:
        value = int(payload["id"])
    except (KeyError, TypeError, ValueError):
        return None
    if value < 1:
        return None
    return value


def _fetch(method: str, url: str, jwt: str, payload: dict[str, Any] | None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ansible-plaibook-plaibook-review",
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
    else:
        if not raw:
            return status, None
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a plai-review GitHub App installation token")
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        creds = app_credentials()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if creds is None:
        print("PLAI_GITHUB_APP_ID and PLAI_GITHUB_APP_PRIVATE_KEY are unset", file=sys.stderr)
        return 1
    app_id, pem = creds
    try:
        token = mint_installation_token(app_id, pem, args.repo, _fetch)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
