# -*- coding: utf-8 -*-
"""Record failed Ansible tasks and write them at the end of the play.

The callback displays this report. Quiet ``plai review`` swallows callback
output, so the CLI reprints the file when it is non-empty. The file is
written only for a path this module allows, and only when something failed.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from typing import Mapping

LOG_PREFIX = "plaibook-task-failures-"
LOG_SUFFIX = ".log"
ENV_LOG = "PLAIBOOK_TASK_FAILURES_LOG"
_MESSAGE_LIMIT = 800
_USERINFO = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_TOKEN_QUERY = re.compile(
    r"([?&](?:token|access_token|private_token|api_key|key)=)[^&\s]*",
    re.IGNORECASE,
)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean_failure_message(text: object, *, limit: int = _MESSAGE_LIMIT) -> str:
    """One failure message, with URL secrets removed and length capped."""
    raw = text if isinstance(text, str) else ""
    raw = raw.replace("\x00", "")
    raw = _ANSI.sub("", raw)
    raw = _USERINFO.sub("://***@", raw)
    raw = _TOKEN_QUERY.sub(r"\1***", raw)
    raw = raw.strip()
    if len(raw) > limit:
        raw = raw[: limit - 1] + "…"
    return raw or "task failed"


def note_failure(
    failures: list[dict[str, str]],
    *,
    task: str,
    message: object,
    path: str = "",
    host: str = "",
    ignored: bool = False,
    kind: str = "failed",
) -> list[dict[str, str]]:
    """Append one failure. ``kind`` is ``failed``, ``ignored``, or ``unreachable``."""
    failures.append(
        {
            "task": (task or "unnamed task").strip() or "unnamed task",
            "message": clean_failure_message(message),
            "path": (path or "").strip(),
            "host": (host or "").strip(),
            "ignored": "true" if ignored else "false",
            "kind": kind if kind in {"failed", "ignored", "unreachable"} else "failed",
        }
    )
    return failures


def render_task_failures(failures: list[Mapping[str, str]], *, log_path: str = "") -> str:
    """Human report. Empty when there is nothing to say."""
    if not failures:
        return ""
    lines = [f"Task failures ({len(failures)}):"]
    if log_path:
        lines.append(f"Log: {log_path}")
    for item in failures:
        kind = item.get("kind") or "failed"
        if item.get("ignored") == "true" and kind == "failed":
            kind = "ignored"
        where = item.get("path") or ""
        task = item.get("task") or "unnamed task"
        label = f"{task} ({where})" if where else task
        host = item.get("host") or ""
        host_prefix = f"{host}: " if host else ""
        lines.append(f"- [{kind}] {host_prefix}{label}")
        message = item.get("message") or "task failed"
        for message_line in message.splitlines() or ["task failed"]:
            lines.append(f"  {message_line}")
    return "\n".join(lines) + "\n"


def is_loop_aggregate(payload: object) -> bool:
    """True when this registered result is a loop wrapper, not one item."""
    if not isinstance(payload, dict):
        return False
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return False
    first = results[0]
    return isinstance(first, dict) and bool(first.get("_ansible_item_result"))


def failure_message_from_result(payload: object) -> str:
    """Prefer ``msg``. Do not copy stdout, stderr, or exception text."""
    if not isinstance(payload, dict):
        return "task failed"
    msg = payload.get("msg")
    if isinstance(msg, str) and msg.strip():
        return msg
    if isinstance(msg, list):
        parts = [part for part in msg if isinstance(part, str) and part.strip()]
        if parts:
            return "\n".join(parts)
    return "task failed"


def _allowed_roots() -> list[str]:
    roots: list[str] = []
    cache_tmp = os.path.join(os.path.expanduser("~"), ".cache", "ansible-plaibook", "tmp")
    for raw in (tempfile.gettempdir(), cache_tmp):
        try:
            real = os.path.realpath(raw)
        except OSError:
            continue
        if real not in roots:
            roots.append(real)
    return roots


def allowed_task_failures_path(path: str) -> str | None:
    """Return ``path`` when it is a private CLI-owned failure log."""
    raw = (path or "").strip()
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    name = os.path.basename(raw)
    if not name.startswith(LOG_PREFIX) or not name.endswith(LOG_SUFFIX):
        return None
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    try:
        real_parent = os.path.realpath(os.path.dirname(raw))
        under_root = False
        for root in _allowed_roots():
            try:
                if os.path.commonpath([root, real_parent]) == root:
                    under_root = True
                    break
            except ValueError:
                continue
        if not under_root:
            return None
        parent_stat = os.stat(real_parent)
    except (OSError, ValueError):
        return None
    if not stat.S_ISDIR(parent_stat.st_mode):
        return None
    if parent_stat.st_uid != os.geteuid():
        return None
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        return None
    if not os.path.basename(real_parent).startswith(LOG_PREFIX):
        return None
    return os.path.join(real_parent, name)


def create_task_failures_log(*, directory: str | None = None) -> tuple[str, str]:
    """Private 0o700 directory and empty 0o600 log. Caller deletes both when unused."""
    parent = tempfile.mkdtemp(prefix=LOG_PREFIX, dir=directory)
    os.chmod(parent, 0o700)
    fd, path = tempfile.mkstemp(prefix=LOG_PREFIX, suffix=LOG_SUFFIX, dir=parent)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return parent, path


def write_task_failures(path: str, text: str) -> bool:
    """Replace a CLI-owned log. False means no-op (unsafe path, empty, or I/O)."""
    if not text.strip() or not hasattr(os, "O_NOFOLLOW"):
        return False
    resolved = allowed_task_failures_path(path)
    if resolved is None:
        return False
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_uid != os.geteuid():
            return False
        if stat.S_IMODE(info.st_mode) & 0o077:
            return False
        os.write(fd, text.encode("utf-8"))
        return True
    except OSError:
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


_READ_LIMIT = 128_000


def read_task_failures_log(path: str) -> str:
    """Log text, or empty when the file is missing, unsafe, or only whitespace.

    Open with ``O_NOFOLLOW`` and the same owner/mode checks as the writer.
    The playbook process can see ``PLAIBOOK_TASK_FAILURES_LOG`` and replace
    that path before the CLI reads it. Following a symlink would print the
    target.
    """
    if not path or not hasattr(os, "O_NOFOLLOW"):
        return ""
    resolved = allowed_task_failures_path(path)
    if resolved is None:
        return ""
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return ""
        if info.st_uid != os.geteuid():
            return ""
        if stat.S_IMODE(info.st_mode) & 0o077:
            return ""
        if info.st_size > _READ_LIMIT:
            return ""
        chunks: list[bytes] = []
        remaining = _READ_LIMIT
        while remaining > 0:
            data = os.read(fd, min(65536, remaining))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        text = b"".join(chunks).decode("utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    return text if text.strip() else ""


def discard_empty_task_failure_log(path: str) -> None:
    """Remove an unused log and its private directory."""
    if not path or read_task_failures_log(path):
        return
    parent = os.path.dirname(path)
    try:
        os.unlink(path)
    except OSError:
        return
    try:
        if os.path.basename(parent).startswith(LOG_PREFIX):
            os.rmdir(parent)
    except OSError:
        pass
