# -*- coding: utf-8 -*-
"""Behavioral tests for attest_cursor_structured.py.

Run with: uv run --python 3.12 pytest action_plugins/test_attest_cursor_structured.py
"""
from __future__ import annotations

import pytest
from attest_cursor_structured import (
    ActionModule,
    CursorAttestationError,
    compute_attestation,
    verify_cursor_attestation,
)

HMAC_KEY = "aa" * 16
NONCE = "AbCdEfGh12345678"
LENS = "Security lens"
PAYLOAD = {
    "findings": [],
    "scores": {"security": 10},
    "prompt_nonce": NONCE,
}


class _FakeShell:
    tmpdir = "/tmp/fake-tmpdir"


class _FakeConnection:
    _shell = _FakeShell()


class _FakeTask:
    def __init__(self, args):
        self.args = dict(args)
        self.action = "attest_cursor_structured"
        self.async_val = False
        self.check_mode = False


def _run_action_module(args):
    action = ActionModule(
        task=_FakeTask(args),
        connection=_FakeConnection(),
        play_context=None,
        loader=None,
        templar=None,
        shared_loader_obj=None,
    )
    return action.run(task_vars={})


def test_matching_nonce_and_hmac_pass():
    structured = dict(PAYLOAD)
    structured["prompt_attestation"] = compute_attestation(HMAC_KEY, structured)
    verify_cursor_attestation(structured, HMAC_KEY, NONCE, LENS)


def test_copied_nonce_with_dropped_findings_fails():
    real = dict(PAYLOAD)
    real["findings"] = [
        {
            "lens": "Security",
            "file": "app.py",
            "line": 1,
            "severity": "Critical",
            "description": "rce",
            "evidence": "eval(x)",
            "confidence": "HIGH",
            "fix": "don't",
        }
    ]
    stolen = compute_attestation(HMAC_KEY, real)
    fabricated = dict(PAYLOAD)
    fabricated["prompt_attestation"] = stolen
    with pytest.raises(CursorAttestationError, match="prompt_attestation"):
        verify_cursor_attestation(fabricated, HMAC_KEY, NONCE, LENS)


def test_copied_nonce_without_attestation_fails():
    with pytest.raises(CursorAttestationError, match="no prompt_attestation"):
        verify_cursor_attestation(dict(PAYLOAD), HMAC_KEY, NONCE, LENS)


def test_wrong_nonce_fails_and_does_not_name_text_or_key():
    structured = dict(PAYLOAD)
    structured["prompt_nonce"] = "definitelyNotTheNonce"
    structured["prompt_attestation"] = compute_attestation(HMAC_KEY, structured)
    with pytest.raises(CursorAttestationError) as excinfo:
        verify_cursor_attestation(structured, HMAC_KEY, NONCE, LENS)
    msg = str(excinfo.value).lower()
    assert "fabricat" in msg
    assert ".text" not in msg
    assert HMAC_KEY not in str(excinfo.value)


def test_wrong_hmac_fails_and_does_not_print_payload():
    structured = dict(PAYLOAD)
    structured["prompt_attestation"] = "0" * 64
    with pytest.raises(CursorAttestationError) as excinfo:
        verify_cursor_attestation(structured, HMAC_KEY, NONCE, LENS)
    msg = str(excinfo.value)
    assert "fabricat" in msg.lower()
    assert ".text" not in msg
    assert "eval(x)" not in msg
    assert HMAC_KEY not in msg


def test_action_module_pass_and_fail_shapes():
    structured = dict(PAYLOAD)
    structured["prompt_attestation"] = compute_attestation(HMAC_KEY, structured)
    ok = _run_action_module(
        {
            "structured": structured,
            "hmac_key": HMAC_KEY,
            "nonce": NONCE,
            "lens_label": LENS,
        }
    )
    assert "failed" not in ok
    assert ok["verified"] is True

    bad = _run_action_module(
        {
            "structured": dict(PAYLOAD),
            "hmac_key": HMAC_KEY,
            "nonce": NONCE,
            "lens_label": LENS,
        }
    )
    assert bad["failed"] is True
    assert "fabricat" in bad["msg"].lower()
    assert ".text" not in bad["msg"]


def test_action_module_requires_args():
    result = _run_action_module({"structured": {}})
    assert result["failed"] is True
