# -*- coding: utf-8 -*-
"""GitHub App installation tokens for the plaibook review author."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
from github_app_token import (
    app_credentials,
    build_jwt,
    main,
    mint_installation_token,
    normalize_pem,
)


@pytest.fixture(scope="module")
def app_pem(tmp_path_factory: pytest.TempPathFactory) -> str:
    path: Path = tmp_path_factory.mktemp("app") / "key.pem"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(path), "2048"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path.read_text(encoding="utf-8")


def test_missing_app_credentials(monkeypatch, capsys):
    monkeypatch.delenv("PLAI_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("PLAI_GITHUB_APP_PRIVATE_KEY", raising=False)
    assert main(["--repo", "acme/repo"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unset" in captured.err
    assert "BEGIN" not in captured.err


def test_normalize_pem_accepts_escaped_newlines():
    pem = normalize_pem("-----BEGIN PRIVATE KEY-----\\nYWJj\\n-----END PRIVATE KEY-----")
    assert pem.startswith("-----BEGIN PRIVATE KEY-----\n")
    assert pem.endswith("-----END PRIVATE KEY-----\n")


def test_app_id_must_be_numeric(monkeypatch):
    monkeypatch.setenv("PLAI_GITHUB_APP_ID", "not-a-number")
    monkeypatch.setenv("PLAI_GITHUB_APP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    with pytest.raises(RuntimeError, match="numeric"):
        app_credentials()


def test_jwt_signature_verifies(app_pem, tmp_path):
    token = build_jwt("12345", app_pem, now=1_700_000_000)
    header, payload, signature = token.split(".")
    header_json = json.loads(base64.urlsafe_b64decode(header + "=="))
    payload_json = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert header_json == {"alg": "RS256", "typ": "JWT"}
    assert payload_json["iss"] == "12345"
    assert payload_json["iat"] == 1_700_000_000 - 60
    assert payload_json["exp"] == 1_700_000_000 + 540
    key_path = tmp_path / "key.pem"
    pub_path = tmp_path / "key.pub"
    data_path = tmp_path / "data"
    sig_path = tmp_path / "sig"
    key_path.write_text(app_pem, encoding="utf-8")
    data_path.write_bytes(f"{header}.{payload}".encode("ascii"))
    sig_path.write_bytes(base64.urlsafe_b64decode(signature + "=="))
    subprocess.run(
        ["openssl", "pkey", "-in", str(key_path), "-pubout", "-out", str(pub_path)],
        check=True,
        timeout=60,
    )
    verified = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(pub_path), "-signature", str(sig_path), str(data_path)],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert verified.returncode == 0


def test_sign_timeout_raises(monkeypatch, app_pem):
    def expired(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="openssl", timeout=30)

    monkeypatch.setattr("github_app_token.subprocess.run", expired)
    with pytest.raises(RuntimeError, match="sign"):
        build_jwt("12345", app_pem, now=1_700_000_000)


def test_mint_requests_pull_request_write_and_returns_only_the_token(app_pem):
    calls = []

    def fetch(method, url, jwt, payload):
        calls.append((method, url, jwt, payload))
        if method == "GET":
            return 200, {"id": 42}
        return 201, {"token": "ghs_example"}

    token = mint_installation_token("12345", app_pem, "acme/repo", fetch, now=1_700_000_000)
    assert token == "ghs_example"
    assert calls[0][0] == "GET"
    assert calls[0][1] == "https://api.github.com/repos/acme/repo/installation"
    assert calls[0][2].count(".") == 2
    assert "PRIVATE" not in calls[0][2]
    assert calls[1][1] == "https://api.github.com/app/installations/42/access_tokens"
    assert calls[1][3] == {"repositories": ["repo"], "permissions": {"pull_requests": "write"}}


def test_main_prints_only_the_token(monkeypatch, capsys, app_pem):
    monkeypatch.setenv("PLAI_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("PLAI_GITHUB_APP_PRIVATE_KEY", app_pem.replace("\n", "\\n"))

    def fetch(method, url, jwt, payload):
        if method == "GET":
            return 200, {"id": 42}
        return 201, {"token": "ghs_example", "expires_at": "2026-09-28T00:00:00Z"}

    monkeypatch.setattr("github_app_token._fetch", fetch)
    assert main(["--repo", "acme/repo"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "ghs_example"
    assert "ghs_example" not in captured.err
    assert "PRIVATE" not in captured.out
    assert app_pem.splitlines()[1] not in captured.out
