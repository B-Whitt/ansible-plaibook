#!/usr/bin/env python3
"""Explain failing CI checks from the name and URL already collected.

GitHub: annotations on the check run, plus the failed step's log tail.
GitLab: the failed job trace. No model call. Logs are capped and then
redacted with the same rules as a review failure message.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from typing import Callable

_FETCH = Callable[[str], tuple[int, str]]

_GITHUB_JOB = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/\d+/job/(?P<job>\d+)/?$"
)
_GITHUB_CHECK = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/runs/(?P<check>\d+)/?$"
)
_GITLAB_JOB = re.compile(r"^https://(?P<host>[^/]+)/(?P<project>.+)/-/jobs/(?P<job>\d+)/?$")

_LOG_TAIL_CHARS = 4000
_ERROR_LIMIT = 800


def _redact(text: str) -> str:
    from plaibook.task_failures import redact_failure_text

    cleaned = redact_failure_text(text or "")
    if len(cleaned) > _ERROR_LIMIT:
        cleaned = cleaned[-_ERROR_LIMIT:]
    return cleaned.strip()


def _error_text(annotation: str, log_text: str) -> str:
    """Keep the annotation and the log tail inside the same cap as a failure message."""
    ann = _redact(annotation) if annotation else ""
    raw_log = log_text or ""
    if len(raw_log) > _LOG_TAIL_CHARS:
        raw_log = raw_log[-_LOG_TAIL_CHARS:]
    log = _redact(raw_log) if raw_log else ""
    if ann and log:
        budget = _ERROR_LIMIT - len(ann) - 1
        if budget < 1:
            return ann[:_ERROR_LIMIT]
        if len(log) > budget:
            log = log[-budget:]
        return f"{ann}\n{log}"
    return ann or log


def _diagnosis(job: str, step: str, error: str, url: str) -> dict[str, str]:
    return {
        "job": job or "unknown",
        "step": step or "",
        "error": error or "CI log was not available.",
        "url": url or "",
    }


def _unavailable(check: dict) -> dict[str, str]:
    return _diagnosis(
        str(check.get("name") or "unknown"),
        "",
        "CI log was not available.",
        str(check.get("url") or ""),
    )


def _failed_step(steps: list) -> tuple[str, str]:
    failed = [step for step in steps if isinstance(step, dict) and step.get("conclusion") == "failure"]
    if not failed:
        return "", ""
    step = failed[-1]
    return str(step.get("name") or ""), str(step.get("number") or "")


def _annotation_text(annotations: list) -> str:
    lines: list[str] = []
    for item in annotations:
        if not isinstance(item, dict):
            continue
        level = str(item.get("annotation_level") or "")
        if level and level not in {"failure", "warning"}:
            continue
        message = str(item.get("message") or "").strip()
        if message:
            lines.append(message)
    return "\n".join(lines)


def _github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ansible-plaibook"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gitlab_headers() -> dict[str, str]:
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GLAB_TOKEN")
    headers = {"User-Agent": "ansible-plaibook"}
    if token:
        headers["PRIVATE-TOKEN"] = token
    return headers


def diagnose_github(check: dict, fetch: _FETCH) -> dict[str, str]:
    """Annotations and the failed step log for one GitHub check URL."""
    url = str(check.get("url") or "")
    name = str(check.get("name") or "unknown")
    job_match = _GITHUB_JOB.match(url)
    check_match = _GITHUB_CHECK.match(url)
    if job_match is None and check_match is None:
        return _unavailable(check)

    step_name = ""
    annotation = ""
    log_text = ""
    if job_match:
        owner = job_match.group("owner")
        repo = job_match.group("repo")
        job_id = job_match.group("job")
        status, body = fetch(f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}")
        job = _json_object(body) if status == 200 else {}
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        step_name, _ = _failed_step(steps)
        check_run = str(job.get("check_run_url") or "")
        if check_run:
            ann_status, ann_body = fetch(check_run.rstrip("/") + "/annotations")
            if ann_status == 200:
                annotation = _annotation_text(_json_list(ann_body))
        log_status, log_body = fetch(f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
        if log_status == 200 and log_body:
            log_text = log_body
    else:
        assert check_match is not None
        owner = check_match.group("owner")
        repo = check_match.group("repo")
        check_id = check_match.group("check")
        ann_status, ann_body = fetch(
            f"https://api.github.com/repos/{owner}/{repo}/check-runs/{check_id}/annotations"
        )
        if ann_status == 200:
            annotation = _annotation_text(_json_list(ann_body))

    error = _error_text(annotation, log_text)
    return _diagnosis(name, step_name, error, url)


def diagnose_gitlab(check: dict, fetch: _FETCH) -> dict[str, str]:
    """Failed job trace for one GitLab job URL."""
    url = str(check.get("url") or "")
    name = str(check.get("name") or "unknown")
    match = _GITLAB_JOB.match(url)
    if match is None:
        return _unavailable(check)
    project = match.group("project")
    job_id = match.group("job")
    host = match.group("host")
    encoded = re.sub(r"/", "%2F", project)
    status, body = fetch(f"https://{host}/api/v4/projects/{encoded}/jobs/{job_id}/trace")
    if status != 200 or not body:
        return _unavailable(check)
    return _diagnosis(name, name, _error_text("", body), url)


def diagnose_checks(platform: str, checks: list, fetch: _FETCH) -> list[dict[str, str]]:
    """One diagnosis per failing check. Unknown URLs stay a name without a log."""
    out: list[dict[str, str]] = []
    for check in checks or []:
        if not isinstance(check, dict):
            continue
        if platform == "github":
            out.append(diagnose_github(check, fetch))
        elif platform == "gitlab":
            out.append(diagnose_gitlab(check, fetch))
        else:
            out.append(_unavailable(check))
    return out


def _json_object(body: str) -> dict:
    try:
        parsed = json.loads(body or "")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(body: str) -> list:
    try:
        parsed = json.loads(body or "")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _logs_text(payload: bytes) -> str:
    """Plain text, or every file in a GitHub job-log zip, later steps last."""
    if payload[:2] != b"PK":
        return payload.decode("utf-8", errors="replace")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        return ""
    parts = [
        archive.read(name).decode("utf-8", errors="replace")
        for name in sorted(archive.namelist())
    ]
    return "\n".join(parts)


def _real_fetch(url: str) -> tuple[int, str]:
    import urllib.request

    github = "api.github.com" in url
    headers = _github_headers() if github else _gitlab_headers()
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            status = response.status
    except Exception:
        return 0, ""
    if github and url.endswith("/logs"):
        return status, _logs_text(payload)
    return status, payload.decode("utf-8", errors="replace")


def main() -> int:
    raw = sys.stdin.read()
    try:
        document = json.loads(raw or "{}")
    except json.JSONDecodeError:
        print("triage input was not JSON", file=sys.stderr)
        return 2
    if not isinstance(document, dict):
        print("triage input was not an object", file=sys.stderr)
        return 2
    platform = str(document.get("platform") or "")
    checks = document.get("failing_checks")
    if not isinstance(checks, list):
        checks = []
    json.dump(diagnose_checks(platform, checks, _real_fetch), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
