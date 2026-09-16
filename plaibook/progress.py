# -*- coding: utf-8 -*-
"""Map ansible-playbook task names to the few review stages the spinner shows."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from plaibook.summary import sanitize_display_line

MAX_CLONE_URL_DISPLAY = 200

# First match wins. Unmapped tasks leave the current stage unchanged.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cache", ("same-commit fast path", "same commit (fast")),
    (
        "sandbox",
        (
            "openshell sandbox",
            "create the sandbox",
            "copy to sandbox",
            "setup_sandbox",
        ),
    ),
    (
        "checkout",
        (
            "clone the target",
            "fetch pr/mr",
            "fetch the pr",
            "parse and validate the review target",
            "resolve the review target",
            "check ci preflight",
            "verify the cloned commit",
            "review each target",
            "review briefing",
            "repo clone url",
        ),
    ),
    ("scan", ("ai-guardian", "guardian scan", "scan the shared diff")),
    (
        "lenses",
        (
            "security and review lens",
            "dispatch lenses",
            "dispatch_lens",
            "security lens",
            "review lens",
        ),
    ),
    ("merge", ("merge findings", "time the merge and score")),
    (
        "explore",
        (
            "exploration stage",
            "look beyond the diff",
            "explore turn",
            "dispatch_explore",
        ),
    ),
    (
        "verify",
        (
            "verification stage",
            "independently verify",
            "verify finding",
            "continuity-audit",
            "continuity audit",
        ),
    ),
    (
        "persist",
        (
            "persistence stage",
            "render and persist",
            "write last_run",
            "write the last_run",
        ),
    ),
    (
        "cleanup",
        (
            "teardown",
            "stop the playbook-owned cursor-sdk-bridge",
            "reap",
        ),
    ),
    (
        "setup",
        (
            "validate the selected provider",
            "provider runtime",
            "provider_preflight",
            "cursor-sdk-bridge sidecar",
            "load operator xdg",
            "align local module execution",
        ),
    ),
)


def stage_for_task(name: str) -> str | None:
    """Return a coarse stage for this ansible task name, or None to keep the last one."""
    haystack = " ".join((name or "").lower().split())
    if not haystack:
        return None
    for stage, needles in _RULES:
        if any(needle in haystack for needle in needles):
            return stage
    return None


def sanitize_clone_url(url: str) -> str:
    """Drop userinfo, query, fragment, and terminal controls before the spinner."""
    text = sanitize_display_line(url or "").split(" ", 1)[0]
    if not text:
        return ""
    if "://" not in text:
        if text.startswith("git@") and ":" in text:
            return text[:MAX_CLONE_URL_DISPLAY]
        return ""
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if not host:
        rendered = parts.scheme + "://" + (parts.path or "")
    else:
        netloc = host if port is None else f"{host}:{port}"
        rendered = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return rendered[:MAX_CLONE_URL_DISPLAY]


def format_stage_line(stage: str, *, clone_url: str | None = None) -> str:
    """Human spinner line. Checkout includes the git URL when the playbook has it."""
    url = sanitize_clone_url(clone_url or "")
    if stage == "checkout" and url:
        return f"{stage} ({url})"
    return stage


def clone_url_from_facts(facts: object) -> str | None:
    if not isinstance(facts, dict):
        return None
    url = facts.get("review_clone_url")
    if isinstance(url, str) and url.strip() and "{{" not in url:
        cleaned = sanitize_clone_url(url.strip())
        return cleaned or None
    return None


def clone_url_from_task_args(args: object) -> str | None:
    if not isinstance(args, dict):
        return None
    repo = args.get("repo")
    if isinstance(repo, str) and ("://" in repo or repo.startswith("git@")) and "{{" not in repo:
        cleaned = sanitize_clone_url(repo.strip())
        return cleaned or None
    return None
