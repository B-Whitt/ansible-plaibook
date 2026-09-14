# -*- coding: utf-8 -*-
"""Dispatcher-side HMAC attestation for Cursor structured output.

The prompt_nonce canary is copyable from the billed agents.*.prompt.
This plugin binds a per-run HMAC key to the exact structured payload
(excluding prompt_attestation) so a parent that copies the nonce and
submits different findings fails closed. Missing, reused-with-a-different-
payload, or mismatched attestations fail. Do not print .text, the HMAC
key, or the payload in the failure message.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from ansible.plugins.action import ActionBase

_ATTESTATION_FIELD = "prompt_attestation"


class CursorAttestationError(ValueError):
    """Raised when Cursor structured output fails nonce or HMAC checks."""


def canonical_payload(structured: dict) -> str:
    """Compact canonical JSON of structured output without prompt_attestation."""
    if not isinstance(structured, dict):
        raise CursorAttestationError(
            "Cursor structured output is missing or not an object; the named "
            "subagent almost certainly never ran. This is dispatch fabrication, "
            "not a clean review. The model's draft is NOT printed here."
        )
    body = {key: value for key, value in structured.items() if key != _ATTESTATION_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_attestation(hmac_key_hex: str, structured: dict) -> str:
    """Return lowercase hex HMAC-SHA256 of canonical_payload(structured)."""
    try:
        key = bytes.fromhex(hmac_key_hex)
    except (ValueError, TypeError) as exc:
        raise CursorAttestationError(
            "Dispatcher HMAC key is not valid hex. This is a playbook bug, "
            "not a model failure."
        ) from exc
    if not key:
        raise CursorAttestationError(
            "Dispatcher HMAC key is empty. This is a playbook bug, not a model failure."
        )
    message = canonical_payload(structured).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_cursor_attestation(
    structured: dict,
    hmac_key_hex: str,
    nonce: str,
    lens_label: str,
) -> None:
    """Fail closed on nonce mismatch, missing HMAC, or payload-bound HMAC mismatch."""
    if not nonce:
        raise CursorAttestationError(
            f"{lens_label} (agent_family=cursor) has no per-run prompt nonce. "
            "This is a playbook bug, not a model failure."
        )
    if not isinstance(structured, dict):
        raise CursorAttestationError(
            f"{lens_label} (agent_family=cursor) returned structured output "
            "that is missing or not an object. The named subagent almost "
            "certainly never ran; the parent fabricated a completed review. "
            "This is dispatch fabrication, not a clean review. The model's "
            "draft is NOT printed here."
        )
    got_nonce = structured.get("prompt_nonce") or ""
    if got_nonce != nonce:
        raise CursorAttestationError(
            f"{lens_label} (agent_family=cursor) returned structured output "
            "whose prompt_nonce does not match the per-run canary. The named "
            "subagent almost certainly never ran; the parent fabricated a "
            "completed review. This is dispatch fabrication, not a clean "
            "review. The model's draft is NOT printed here."
        )
    got_attestation = structured.get(_ATTESTATION_FIELD) or ""
    if not isinstance(got_attestation, str) or not got_attestation:
        raise CursorAttestationError(
            f"{lens_label} (agent_family=cursor) returned structured output "
            "with no prompt_attestation. A copied nonce without a payload-"
            "bound HMAC is dispatch fabrication, not a clean review. The "
            "model's draft is NOT printed here."
        )
    expected = compute_attestation(hmac_key_hex, structured)
    if not hmac.compare_digest(got_attestation, expected):
        raise CursorAttestationError(
            f"{lens_label} (agent_family=cursor) returned structured output "
            "whose prompt_attestation does not match the dispatcher HMAC of "
            "this payload. The parent copied a nonce or dropped findings. "
            "This is dispatch fabrication, not a clean review. The model's "
            "draft is NOT printed here."
        )


class ActionModule(ActionBase):
    """Verify Cursor structured output nonce + payload-bound HMAC."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("structured", "hmac_key", "nonce", "lens_label"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        structured = self._task.args.get("structured")
        hmac_key = self._task.args.get("hmac_key")
        nonce = self._task.args.get("nonce")
        lens_label = self._task.args.get("lens_label")
        if hmac_key is None or nonce is None or lens_label is None:
            result["failed"] = True
            result["msg"] = (
                "attest_cursor_structured requires hmac_key, nonce, and lens_label"
            )
            return result

        try:
            verify_cursor_attestation(structured, hmac_key, nonce, lens_label)
        except CursorAttestationError as exc:
            result["failed"] = True
            result["msg"] = str(exc)
            return result

        result["changed"] = False
        result["verified"] = True
        return result
