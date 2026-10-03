# -*- coding: utf-8 -*-
"""TTY wait animation for quiet `plai review` (stderr only)."""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import TextIO

from plaibook.summary import sanitize_display_line

# Braille spinner, same family as many CLI waiters (including Cursor).
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_CLEAR_LINE = "\r\033[2K"
_UP1 = "\033[1A"
_DIM = "\033[2m"
_RESET = "\033[0m"
# Ansible mark teal, #5BBDBF.
_ANSIBLE_TEAL = "\033[38;2;91;189;191m"
_ANSI_CSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def spinner_enabled(stream: TextIO | None = None) -> bool:
    """Animate only on a real TTY unless PLAIBOOK_SPINNER=0."""
    if os.environ.get("PLAIBOOK_SPINNER", "1").strip() in ("0", "false", "no"):
        return False
    target = stream if stream is not None else sys.stderr
    return bool(getattr(target, "isatty", lambda: False)())


def _use_color() -> bool:
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}:{secs:02d}"
    return f"{secs}s"


_MAX_WAIT_TEXT = 200


def terminal_columns(stream: TextIO | None = None) -> int:
    """Visible columns. A wrapped line makes \\033[1A land on the wrong row."""
    fileno = getattr(stream, "fileno", None)
    if callable(fileno):
        try:
            columns = os.get_terminal_size(fileno()).columns
            if columns > 0:
                return columns
        except (OSError, ValueError, AttributeError):
            pass
    try:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        columns = 80
    return columns if columns > 0 else 80


def visible_width(text: str) -> int:
    return len(_ANSI_CSI_RE.sub("", text or ""))


def clip_plain(text: str, width: int) -> str:
    """Truncate a single-width-character string to *width* columns."""
    if width <= 0:
        return ""
    raw = text or ""
    if len(raw) <= width:
        return raw
    if width == 1:
        return "…"
    return raw[: width - 1] + "…"


def spinner_lines(frame: str, label: str, elapsed: str, detail: str, columns: int) -> tuple[str, str]:
    """Two plain lines that stay within the terminal, including a 1-column screen."""
    if columns <= 0:
        return "", ""
    budget = columns if columns == 1 else columns - 1
    if budget >= 3:
        line2 = "  " + clip_plain(detail, budget - 2)
    else:
        line2 = clip_plain(detail, budget)

    elapsed = elapsed or ""
    if len(elapsed) >= budget:
        return clip_plain(elapsed, budget), line2
    room = budget - len(elapsed)
    chrome = 4  # frame, space, two spaces before the elapsed time
    if room >= chrome:
        shown = clip_plain(label, room - chrome)
        return f"{frame} {shown}  {elapsed}", line2
    gap = " " * (room - 1)
    return f"{frame}{gap}{elapsed}", line2


def _color_line(plain: str, frame: str, elapsed: str) -> str:
    text = plain
    if frame and text.startswith(frame):
        text = f"{_ANSIBLE_TEAL}{frame}{_RESET}{text[len(frame):]}"
    if elapsed and text.endswith(elapsed):
        text = f"{text[:-len(elapsed)]}{_DIM}{elapsed}{_RESET}"
    return text


def _safe_wait_text(text: str) -> str:
    return sanitize_display_line(text)[:_MAX_WAIT_TEXT]


class WaitSpinner:
    """Rewrite two stderr lines: spinner + label + elapsed, then the current stage."""

    def __init__(
        self,
        label: str,
        stream: TextIO | None = None,
        progress_file: str | Path | None = None,
        detail: str = "setup",
    ) -> None:
        self.label = _safe_wait_text(label)
        self.stream = stream if stream is not None else sys.stderr
        self._enabled = spinner_enabled(self.stream)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._progress_file = Path(progress_file) if progress_file else None
        self._detail = _safe_wait_text(detail) or "setup"
        self._painted_two_lines = False

    def __enter__(self) -> WaitSpinner:
        if not self._enabled:
            self.stream.write(self.label + "\n")
            self.stream.flush()
            return self
        self._started = time.monotonic()
        self.stream.write(_HIDE_CURSOR)
        self.stream.flush()
        # Paint before the thread starts. On a busy runner the thread can
        # be scheduled after the caller has already stopped the spinner.
        self._paint(0)
        self._thread = threading.Thread(target=self._run, name="plaibook-spinner", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._painted_two_lines:
            self.stream.write(_CLEAR_LINE + "\n" + _CLEAR_LINE + _UP1 + _CLEAR_LINE)
        else:
            self.stream.write(_CLEAR_LINE)
        self.stream.write(_SHOW_CURSOR)
        self.stream.flush()

    def _read_detail(self) -> str:
        if self._progress_file is not None:
            try:
                text = self._progress_file.read_text(encoding="utf-8").strip()
                if text:
                    # Keep the last real stage. Path.write_text truncates
                    # before it writes, so a poll in that window is empty
                    # and must not flash the initial "setup" line.
                    updated = _safe_wait_text(text.splitlines()[-1])
                    if updated:
                        self._detail = updated
            except OSError:
                pass
        return self._detail or "setup"

    def _paint(self, i: int) -> None:
        frame = _FRAMES[i % len(_FRAMES)]
        now = time.monotonic()
        elapsed = format_elapsed(now - self._started)
        detail = self._read_detail() or "setup"
        plain1, plain2 = spinner_lines(frame, self.label, elapsed, detail, terminal_columns(self.stream))
        if _use_color():
            line1 = _color_line(plain1, frame, elapsed)
            line2 = f"{_DIM}{plain2}{_RESET}" if plain2 else ""
        else:
            line1, line2 = plain1, plain2
        self.stream.write(_CLEAR_LINE + line1 + "\n" + _CLEAR_LINE + line2 + _UP1)
        self.stream.flush()
        self._painted_two_lines = True

    def _run(self) -> None:
        i = 1
        while not self._stop.wait(0.08):
            self._paint(i)
            i += 1
