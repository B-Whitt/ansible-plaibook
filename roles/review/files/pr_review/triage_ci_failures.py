#!/usr/bin/env python3
"""Explain failing CI checks from the name and URL already collected.

GitHub: annotations on the check run, plus the failed step's log tail.
GitLab: the failed job trace. No model call. Logs are capped and then
redacted with the same rules as a review failure message.
"""

from __future__ import annotations

import io
import ipaddress
import json
import os
import re
import sys
import time
import zipfile
from typing import Callable
from urllib.parse import quote, urlparse

_FETCH = Callable[[str], tuple[int, str]]

_GITHUB_JOB = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run>\d+)/job/(?P<job>\d+)/?$"
)
_GITHUB_RUN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run>\d+)/?$"
)
_GITHUB_CHECK = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/runs/(?P<check>\d+)/?$"
)
_GITLAB_JOB = re.compile(r"^https://(?P<host>[^/]+)/(?P<project>.+)/-/jobs/(?P<job>\d+)/?$")

_LOG_TAIL_CHARS = 4000
_ERROR_LIMIT = 800
_MAX_DOWNLOAD = 1_000_000
_MAX_ZIP_ENTRIES = 40
_MAX_UNCOMPRESSED = 1_000_000


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


_PAGE_LIMIT = 5
_PAGE_SIZE = 100
_MAX_CHECKS = 50
_TRIAGE_BUDGET_SECONDS = 120.0
_REQUEST_TIMEOUT = 60.0
_log_step = ""
_budget: "_Budget | None" = None


class _Budget:
    """Wall-clock limit for one triage run. Per-request timeouts stay underneath it."""

    def __init__(self, seconds: float = _TRIAGE_BUDGET_SECONDS) -> None:
        self.deadline = time.monotonic() + seconds

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def timeout(self) -> float:
        return min(_REQUEST_TIMEOUT, max(0.0, self.deadline - time.monotonic()))


def _api_repo(owner: str, repo: str, suffix: str) -> str:
    return (
        "https://api.github.com/repos/"
        f"{quote(owner, safe='')}/{quote(repo, safe='')}/{suffix.lstrip('/')}"
    )


def _matches_project(owner: str, repo: str, project: str | None) -> bool:
    return bool(project) and f"{owner}/{repo}" == project


def _paged(fetch: _FETCH, url: str, list_key: str | None) -> list:
    """Follow page query params. Stops after a short page or a fixed page cap."""
    items: list = []
    for page in range(1, _PAGE_LIMIT + 1):
        status, body = fetch(f"{url}?per_page={_PAGE_SIZE}&page={page}")
        if status != 200:
            break
        batch = _json_object(body).get(list_key) if list_key else _json_list(body)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return items


def _github_annotation_url(url: str, owner: str, repo: str) -> bool:
    """True when a check-run URL is this repo's API, not an arbitrary host."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "api.github.com":
        return False
    prefix = f"/repos/{owner}/{repo}/check-runs/"
    return parsed.path.startswith(prefix)


def diagnose_github(check: dict, fetch: _FETCH, project: str | None = None) -> dict[str, str]:
    """Annotations and the failed step log for one GitHub check URL in this repo."""
    global _log_step
    url = str(check.get("url") or "")
    name = str(check.get("name") or "unknown")
    job_match = _GITHUB_JOB.match(url)
    check_match = _GITHUB_CHECK.match(url)
    if job_match is None and check_match is None:
        return _unavailable(check)
    owner = (job_match or check_match).group("owner")
    repo = (job_match or check_match).group("repo")
    if not _matches_project(owner, repo, project):
        return _unavailable(check)

    step_name = ""
    annotation = ""
    log_text = ""
    if job_match:
        job_id = job_match.group("job")
        status, body = fetch(_api_repo(owner, repo, f"actions/jobs/{job_id}"))
        job = _json_object(body) if status == 200 else {}
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        step_name, step_number = _failed_step(steps)
        check_run = str(job.get("check_run_url") or "")
        if _github_annotation_url(check_run, owner, repo):
            annotation = _annotation_text(
                _paged(fetch, check_run.rstrip("/") + "/annotations", None)
            )
        _log_step = step_number
        log_status, log_body = fetch(_api_repo(owner, repo, f"actions/jobs/{job_id}/logs"))
        if log_status == 200 and log_body:
            log_text = log_body
    else:
        assert check_match is not None
        check_id = check_match.group("check")
        annotation = _annotation_text(
            _paged(fetch, _api_repo(owner, repo, f"check-runs/{check_id}/annotations"), None)
        )

    error = _error_text(annotation, log_text)
    return _diagnosis(name, step_name, error, url)


def diagnose_gitlab(
    check: dict,
    fetch: _FETCH,
    allowed_host: str | None = None,
    project: str | None = None,
) -> dict[str, str]:
    """Failed job trace for one GitLab job on this merge request's host and project."""
    url = str(check.get("url") or "")
    name = str(check.get("name") or "unknown")
    match = _GITLAB_JOB.match(url)
    if match is None:
        return _unavailable(check)
    host = match.group("host")
    project_path = match.group("project")
    if not allowed_host or host.lower() != allowed_host.lower():
        return _unavailable(check)
    if not project or project_path != project:
        return _unavailable(check)
    job_id = match.group("job")
    encoded = quote(project_path, safe="")
    status, body = fetch(f"https://{host}/api/v4/projects/{encoded}/jobs/{job_id}/trace")
    if status != 200 or not body:
        return _unavailable(check)
    return _diagnosis(name, name, _error_text("", body), url)


def diagnose_workflow_run(
    check: dict,
    fetch: _FETCH,
    project: str | None = None,
) -> list[dict[str, str]]:
    """Failed jobs for a workflow-run page that has no job id in the URL."""
    url = str(check.get("url") or "")
    name = str(check.get("name") or "unknown")
    match = _GITHUB_RUN.match(url)
    if match is None:
        return [_unavailable(check)]
    owner = match.group("owner")
    repo = match.group("repo")
    if not _matches_project(owner, repo, project):
        return [_unavailable(check)]
    run_id = match.group("run")
    jobs = _paged(fetch, _api_repo(owner, repo, f"actions/runs/{run_id}/jobs"), "jobs")
    failed = [job for job in jobs if isinstance(job, dict) and job.get("conclusion") == "failure"]
    if not failed:
        return [_unavailable(check)]
    found: list[dict[str, str]] = []
    failed = [
        job for job in failed if isinstance(job, dict) and str(job.get("id") or "").isdigit()
    ][:_MAX_CHECKS]
    for job in failed:
        if _budget is not None and _budget.expired():
            break
        job_id = str(job.get("id") or "")
        job_url = f"https://github.com/{owner}/{repo}/actions/runs/{run_id}/job/{job_id}"
        job_name = str(job.get("name") or name)
        found.append(diagnose_github({"name": job_name, "url": job_url}, fetch, project))
    return found or [_unavailable(check)]


def diagnose_checks(
    platform: str,
    checks: list,
    fetch: _FETCH,
    allowed_host: str | None = None,
    project: str | None = None,
    budget: _Budget | None = None,
) -> list[dict[str, str]]:
    """One diagnosis per failing check, capped so a huge matrix cannot stall the runner."""
    global _budget
    _budget = budget or _Budget()
    out: list[dict[str, str]] = []
    try:
        for check in list(checks or [])[:_MAX_CHECKS]:
            if _budget.expired() or len(out) >= _MAX_CHECKS:
                break
            if not isinstance(check, dict):
                continue
            if platform == "github" and _GITHUB_RUN.match(str(check.get("url") or "")):
                out.extend(diagnose_workflow_run(check, fetch, project))
                del out[_MAX_CHECKS:]
            elif platform == "github":
                out.append(diagnose_github(check, fetch, project))
            elif platform == "gitlab":
                out.append(diagnose_gitlab(check, fetch, allowed_host, project))
            else:
                out.append(_unavailable(check))
    finally:
        _budget = None
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


def _zip_entry_key(filename: str) -> tuple[int, str]:
    """Numeric step prefix, so 10_ comes after 2_ rather than before it."""
    base = filename.rsplit("/", 1)[-1]
    head = base.split("_", 1)[0]
    if head.isdigit():
        return (int(head), base)
    return (10**9, base)


def _logs_text(payload: bytes, step_number: str = "") -> str:
    """Plain text, or a bounded read of a GitHub job-log zip, later steps last."""
    if payload[:2] != b"PK":
        return payload.decode("utf-8", errors="replace")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        return ""
    infos = archive.infolist()
    if len(infos) > _MAX_ZIP_ENTRIES:
        return ""
    if step_number:
        matched = [
            info
            for info in infos
            if _zip_entry_key(info.filename)[0] == int(step_number)
        ]
        if not matched:
            return ""
        infos = matched
    parts: list[str] = []
    total = 0
    for info in sorted(infos, key=lambda item: _zip_entry_key(item.filename)):
        if info.file_size > _MAX_UNCOMPRESSED:
            return ""
        remaining = _MAX_UNCOMPRESSED - total
        if remaining <= 0:
            break
        with archive.open(info) as handle:
            chunk = handle.read(remaining)
        total += len(chunk)
        parts.append(chunk.decode("utf-8", errors="replace"))
    return "\n".join(parts)


def _public_https(url: str) -> bool:
    """True for a public https host. Redirect targets must pass this before a download."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _is_github_api(url: str) -> bool:
    """True only when the URL host is api.github.com, not a substring of it."""
    return (urlparse(url).hostname or "").lower() == "api.github.com"


def _headers_for(url: str, gitlab_host: str | None) -> dict[str, str] | None:
    """Tokens stay on the API host that owns them. Anything else gets no request."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "api.github.com":
        return _github_headers()
    if gitlab_host and host == gitlab_host.lower() and parsed.path.startswith("/api/v4/"):
        return _gitlab_headers()
    return None


def _read_capped(response, limit: int = _MAX_DOWNLOAD) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = response.read(min(65536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _request(url: str, headers: dict[str, str]) -> tuple[int, bytes, str]:
    """One GET. Redirects are not followed, so a token cannot ride to another host."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    timeout = _budget.timeout() if _budget else _REQUEST_TIMEOUT
    if timeout <= 0:
        return 0, b"", ""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > _MAX_DOWNLOAD:
                return 0, b"", ""
            return response.status, _read_capped(response), ""
    except urllib.error.HTTPError as exc:
        location = ""
        if exc.headers is not None:
            location = exc.headers.get("Location") or ""
        if exc.code in {301, 302, 303, 307, 308}:
            return exc.code, b"", location
        return exc.code, b"", ""
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, b"", ""


def _real_fetch(url: str, gitlab_host: str | None = None) -> tuple[int, str]:
    headers = _headers_for(url, gitlab_host)
    if headers is None:
        return 0, ""
    status, payload, location = _request(url, headers)
    logs = _is_github_api(url) and urlparse(url).path.rstrip("/").endswith("/logs")
    if logs and status in {301, 302, 303, 307, 308} and location and _public_https(location):
        status, payload, _ = _request(location, {"User-Agent": "ansible-plaibook"})
    if status != 200:
        return status, ""
    if logs:
        return status, _logs_text(payload, _log_step)
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
    hostname = str(document.get("hostname") or "") or None
    project = str(document.get("project") or "") or None
    json.dump(
        diagnose_checks(
            platform,
            checks,
            lambda url: _real_fetch(url, hostname),
            hostname,
            project,
        ),
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
