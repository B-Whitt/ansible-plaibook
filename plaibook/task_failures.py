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

LOG_PREFIX = "plaibook-" + "task-failures-"
LOG_SUFFIX = ".log"
ENV_LOG = "PLAIBOOK_TASK_FAILURES_LOG"
_MESSAGE_LIMIT = 800
_FIELD_LIMIT = 500
# Userinfo may contain extra colons (user:p:ass). Stop at @, slash, or space.
_USERINFO = re.compile(r"://[^@/\s]+@")
# Longer keys first so api_key wins over key.
_CREDENTIAL_KEYS = (
    "access_token|private_token|client_secret|id_token|refresh_token|api_key|"
    "password|passwd|signature|credential|secret|bearer|token|sig|auth|key"
)
# A quoted value may contain spaces. \S+ stops at the first one, so
# A quoted password containing a space used to leave the tail in the log. The quote
# alternatives are disjoint (backslash vs not) and linear.
_QUOTED_VALUE = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
_SECRET_VALUE = rf"(?:{_QUOTED_VALUE}|\S+)"
# A suffix such as _ID still belongs to the credential name:
# AWS_SECRET_ACCESS_KEY_ID=... must not stop at KEY.
_KEY_SUFFIX = r"(?:[_-][A-Za-z0-9]+)*"
_TOKEN_QUERY = re.compile(
    rf"(?i)([?&#](?:{_CREDENTIAL_KEYS}){_KEY_SUFFIX}=)(?:{_QUOTED_VALUE}|[^&#\s]*)",
)
# ENV_STYLE names (GITHUB_TOKEN, DB_PASSWORD) have no word boundary before the key.
# An optional quote sits between a JSON/YAML key and its colon: "token": "...".
_SECRET_ASSIGN = re.compile(
    rf"(?i)((?:[A-Za-z0-9]+_)*(?:{_CREDENTIAL_KEYS}){_KEY_SUFFIX}[\"']?)"
    rf"(\s*[=:]\s*){_SECRET_VALUE}",
)
# A fully quoted header value ("Bearer alpha beta") has no separate scheme token.
_AUTH_QUOTED = re.compile(
    rf"(?i)(authorization\s*[:=]\s*)(?:{_QUOTED_VALUE})",
)
_AUTH_HEADER = re.compile(
    rf"(?i)(authorization\s*[:=]\s*(?:bearer|basic|token)\s+){_SECRET_VALUE}",
)
_BEARER = re.compile(
    rf"(?i)(\bbearer\s+)(?:{_QUOTED_VALUE}|[A-Za-z0-9._~+/=-]{{8,}})",
)
# -u "user:pass word" is one quoted argument. -u alice:"alpha beta" keeps
# the username outside the quotes, so the password quote has to be read
# after the colon. \S+ used to stop inside that quote.
# --user before -u so --user is not parsed as -u plus a username.
# Whitespace or '=' may be absent: curl -uuser:secret.
_DASH_USER_QUOTED = re.compile(
    rf"(?i)((?:--user|-u)(?:\s+|=)?)(?:{_QUOTED_VALUE})",
)
_DASH_USER = re.compile(
    rf"(?i)((?:--user|-u)(?:\s+|=)?)([^\s:]+:\s*){_SECRET_VALUE}",
)
# mysql -pSECRET is attached. Do not treat a letter-only flag such as -print as a password.
_SHORT_PASSWORD = re.compile(
    rf"(?i)(?<![A-Za-z0-9])(-p)(?:{_QUOTED_VALUE}|(?=\S*\d)\S+)",
)
# --token SECRET and --password "alpha beta". Assignment form (--password=SECRET)
# is already covered. Underscores in key names are also hyphens on the CLI.
_CLI_KEY = _CREDENTIAL_KEYS.replace("_", "[-_]")
_CLI_SECRET_OPT = re.compile(
    rf"(?i)(--(?:[A-Za-z0-9]+[-_])*(?:{_CLI_KEY}){_KEY_SUFFIX})(\s+){_SECRET_VALUE}",
)
# A bare token or a PEM block has no assignment delimiter.
_PREFIX_TOKEN = re.compile(
    r"ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|glpat-[A-Za-z0-9_\-]{8,}|"
    r"sk-ant-[A-Za-z0-9_\-]{8,}|AKIA[0-9A-Z]{16}"
)
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
# CSI, OSC, and other ECMA-48 sequences, plus C1 CSI (U+009B).
_ANSI = re.compile(
    r"(?:\x1b[@-Z\\-_]"
    r"|\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[PX^_].*?(?:\x1b\\|\x07)"
    r"|\x9b[0-?]*[ -/]*[@-~])"
)
# C0 except TAB/LF, DEL, and C1. CR is removed so it cannot rewind the line.
_C0_C1 = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _redact_display_text(text: object) -> str:
    raw = text if isinstance(text, str) else ""
    raw = _ANSI.sub("", raw)
    raw = _C0_C1.sub("", raw)
    raw = _USERINFO.sub("://***@", raw)
    raw = _TOKEN_QUERY.sub(r"\1***", raw)
    raw = _AUTH_QUOTED.sub(r"\1***", raw)
    raw = _AUTH_HEADER.sub(r"\1***", raw)
    raw = _BEARER.sub(r"\1***", raw)
    raw = _DASH_USER_QUOTED.sub(r"\1***", raw)
    raw = _DASH_USER.sub(r"\1\2***", raw)
    raw = _SHORT_PASSWORD.sub(r"\1***", raw)
    raw = _CLI_SECRET_OPT.sub(r"\1\2***", raw)
    raw = _SECRET_ASSIGN.sub(r"\1\2***", raw)
    raw = _PEM_BLOCK.sub("***", raw)
    return _PREFIX_TOKEN.sub("***", raw)


def sanitize_failure_field(text: object, *, limit: int = _FIELD_LIMIT) -> str:
    """One task name, path, or host. No extra lines, controls, or URL secrets."""
    raw = _redact_display_text(text).replace("\t", " ").replace("\n", " ")
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = raw[: limit - 1] + "…"
    return raw


def clean_failure_message(text: object, *, limit: int = _MESSAGE_LIMIT) -> str:
    """One failure message, with URL secrets removed and length capped."""
    raw = _redact_display_text(text).strip()
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
            "task": sanitize_failure_field(task) or "unnamed task",
            "message": clean_failure_message(message),
            "path": sanitize_failure_field(path),
            "host": sanitize_failure_field(host),
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
        where = sanitize_failure_field(item.get("path") or "")
        task = sanitize_failure_field(item.get("task") or "") or "unnamed task"
        label = f"{task} ({where})" if where else task
        host = sanitize_failure_field(item.get("host") or "")
        host_prefix = f"{host}: " if host else ""
        lines.append(f"- [{kind}] {host_prefix}{label}")
        message = clean_failure_message(item.get("message") or "")
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
    # Truncate only after the inode checks. O_TRUNC on open would wipe a
    # hard-linked file before st_nlink can reject it.
    flags = (
        os.O_WRONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_nlink != 1:
            return False
        if info.st_uid != os.geteuid():
            return False
        if stat.S_IMODE(info.st_mode) & 0o077:
            return False
        os.ftruncate(fd, 0)
        view = memoryview(text.encode("utf-8"))
        while len(view) > 0:
            written = os.write(fd, view)
            if written <= 0:
                os.ftruncate(fd, 0)
                return False
            view = view[written:]
        return True
    except OSError:
        if fd >= 0:
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
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

    Open with ``O_NOFOLLOW`` and ``O_NONBLOCK`` and the same owner/mode
    checks as the writer. A FIFO would otherwise block in open before
    the regular-file check.
    The playbook process can see ``PLAIBOOK_TASK_FAILURES_LOG`` and replace
    that path before the CLI reads it. Following a symlink would print the
    target.
    """
    if not path or not hasattr(os, "O_NOFOLLOW"):
        return ""
    resolved = allowed_task_failures_path(path)
    if resolved is None:
        return ""
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    try:
        fd = os.open(resolved, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return ""
        if info.st_nlink != 1:
            return ""
        if info.st_uid != os.geteuid():
            return ""
        if stat.S_IMODE(info.st_mode) & 0o077:
            return ""
        truncated = info.st_size > _READ_LIMIT
        chunks: list[bytes] = []
        remaining = _READ_LIMIT
        while remaining > 0:
            data = os.read(fd, min(65536, remaining))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        # The playbook can overwrite this file after the callback writes it.
        # Sanitize again at read time so quiet mode does not print raw bytes.
        text = _redact_display_text(text)
        if truncated:
            text = text.rstrip() + "\n… failure log truncated\n"
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
