# -*- coding: utf-8 -*-
"""Interpreter details that must stay invisible to ``plai`` users.

A path with a space breaks Ansible module shebangs (the kernel splits
them). pipx on macOS defaults to ``~/Library/Application Support``.
Localhost modules then use a wrapper whose path has no space. An
externally managed interpreter (PEP 668) is not a place to ``pip
install`` at review time.
"""

from __future__ import annotations

import hashlib
import os
import sys
import sysconfig
import tempfile
from pathlib import Path

# macOS XProtect blocks scripts that appear in /tmp and then run.
_REJECTED_TMP_ROOTS = {Path("/tmp"), Path("/private/tmp")}


def interpreter_is_externally_managed(python: str | None = None) -> bool:
    """True when pip will refuse installs into this base interpreter.

    Virtual environments are not externally managed, even when their
    stdlib path still points at a Homebrew or Debian prefix.
    """
    exe = python or sys.executable
    if _is_virtualenv(exe):
        return False
    if _is_this_interpreter(exe):
        return (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").is_file()
    probe = (
        "import sysconfig\n"
        "from pathlib import Path\n"
        "marker = Path(sysconfig.get_path('stdlib')) / 'EXTERNALLY-MANAGED'\n"
        "raise SystemExit(0 if marker.is_file() else 1)\n"
    )
    import subprocess

    try:
        completed = subprocess.run(
            [exe, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _is_virtualenv(python: str) -> bool:
    if _is_this_interpreter(python):
        return sys.prefix != sys.base_prefix
    probe = "import sys\nraise SystemExit(0 if sys.prefix != sys.base_prefix else 1)\n"
    import subprocess

    try:
        completed = subprocess.run(
            [python, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def controller_python() -> str | None:
    """Path Ansible can put in a module shebang.

    Returns None when ``sys.executable`` has no space. Otherwise returns a
    shell wrapper with no space in its path that execs the real interpreter.
    The wrapper is not a second install and is not put on ``PATH``.
    """
    exe = Path(sys.executable)
    if " " not in str(exe):
        return None
    link_dir = _wrapper_dir()
    if link_dir is None:
        return None
    digest = hashlib.sha256(str(exe).encode()).hexdigest()[:16]
    wrapper = link_dir / f"python-{digest}"
    if " " in str(wrapper):
        return None
    link_dir.mkdir(parents=True, exist_ok=True)
    body = f"#!/bin/sh\nexec { _sh_quote(str(exe)) } \"$@\"\n"
    _write_wrapper(wrapper, body)
    return str(wrapper)


def _wrapper_dir() -> Path | None:
    """A writable directory whose path contains no space.

    ``~/.local/share`` is first. When the home directory itself contains a
    space, use ``TMPDIR`` unless that directory is ``/tmp``.
    """
    candidates = [Path.home() / ".local" / "share" / "ansible-plaibook"]
    tmp = os.environ.get("TMPDIR", "").strip()
    if tmp:
        resolved = Path(tmp).resolve()
        if resolved not in _REJECTED_TMP_ROOTS and " " not in str(resolved):
            candidates.append(resolved / "ansible-plaibook")
    for directory in candidates:
        if " " not in str(directory):
            return directory
    return None


def _write_wrapper(wrapper: Path, body: str) -> None:
    if wrapper.is_file():
        try:
            current = wrapper.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == body and os.access(wrapper, os.X_OK):
            return
    fd, name = tempfile.mkstemp(dir=wrapper.parent, prefix=".python-", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp, 0o755)
        os.replace(tmp, wrapper)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _is_this_interpreter(python: str) -> bool:
    try:
        return Path(python).resolve() == Path(sys.executable).resolve()
    except OSError:
        return python == sys.executable
