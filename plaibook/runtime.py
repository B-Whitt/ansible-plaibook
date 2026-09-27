# -*- coding: utf-8 -*-
"""Interpreter details that must stay invisible to ``plai`` users.

A path with a space breaks Ansible module shebangs (the kernel splits
them). pipx on macOS defaults to ``~/Library/Application Support``.
Localhost modules then use a wrapper whose path has no space. An
externally managed interpreter (PEP 668) is not a place to ``pip
install`` at review time.
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path


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
    link_dir = Path.home() / ".local" / "share" / "ansible-plaibook"
    link_dir.mkdir(parents=True, exist_ok=True)
    wrapper = link_dir / "python"
    target = str(exe)
    body = f"#!/bin/sh\nexec { _sh_quote(target) } \"$@\"\n"
    current = wrapper.read_text(encoding="utf-8") if wrapper.is_file() else ""
    if current != body:
        wrapper.write_text(body, encoding="utf-8")
        wrapper.chmod(0o755)
    return str(wrapper)


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _is_this_interpreter(python: str) -> bool:
    try:
        return Path(python).resolve() == Path(sys.executable).resolve()
    except OSError:
        return python == sys.executable
