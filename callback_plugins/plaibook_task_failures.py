# -*- coding: utf-8 -*-
"""List failed tasks at the end of a play.

Aggregate callback. Does not replace the default stdout callback. Writes
PLAIBOOK_TASK_FAILURES_LOG only when that path is a CLI-owned regular file
under a private temp directory. Always displays the report when a task
failed, including tasks the play ignored or rescued, so a swallowed
failure is still visible. Stable callback API (2.0) for ansible-core
2.16 through 2.19.
"""

from __future__ import annotations

import os

from ansible.errors import AnsibleError
from ansible.plugins.callback import CallbackBase

try:
    from plaibook.task_failures import (
        allowed_task_failures_path,
        failure_message_from_result,
        is_loop_aggregate,
        note_failure,
        render_task_failures,
        write_task_failures,
    )
except ImportError:  # pragma: no cover - checkout without the package installed
    import importlib.util

    _sibling = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "plaibook", "task_failures.py")
    )
    _spec = importlib.util.spec_from_file_location("_plaibook_task_failures", _sibling)
    if _spec is None or _spec.loader is None or not os.path.isfile(_sibling):
        raise
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    allowed_task_failures_path = _mod.allowed_task_failures_path
    failure_message_from_result = _mod.failure_message_from_result
    is_loop_aggregate = _mod.is_loop_aggregate
    note_failure = _mod.note_failure
    render_task_failures = _mod.render_task_failures
    write_task_failures = _mod.write_task_failures


DOCUMENTATION = """
    name: plaibook_task_failures
    type: aggregate
    short_description: List failed Ansible tasks at the end of the play.
    description:
      - Records failed, ignored, and unreachable tasks.
      - Displays the list from v2_playbook_on_stats.
      - When PLAIBOOK_TASK_FAILURES_LOG names a CLI-owned regular file
        (prefix plaibook-task-failures-, under a private temp directory),
        writes the same report there. Other paths are ignored.
      - Does not write a file when nothing failed.
    requirements: []
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "plaibook_task_failures"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._failures: list[dict[str, str]] = []
        raw = (os.environ.get("PLAIBOOK_TASK_FAILURES_LOG") or "").strip()
        self._path = allowed_task_failures_path(raw) or ""

    def _remember(self, result, *, ignored: bool = False, kind: str = "failed") -> None:
        payload = getattr(result, "_result", None)
        if kind == "failed" and is_loop_aggregate(payload):
            return
        task = getattr(result, "_task", None)
        name = "unnamed task"
        path = ""
        if task is not None:
            get_name = getattr(task, "get_name", None)
            if callable(get_name):
                name = str(get_name() or name)
            get_path = getattr(task, "get_path", None)
            if callable(get_path):
                try:
                    path = str(get_path() or "")
                except (AttributeError, OSError, TypeError, ValueError, AnsibleError):
                    path = ""
            if not ignored:
                ignored = bool(getattr(task, "ignore_errors", False))
        host = ""
        host_obj = getattr(result, "_host", None)
        get_host = getattr(host_obj, "get_name", None)
        if callable(get_host):
            host = str(get_host() or "")
        note_failure(
            self._failures,
            task=name,
            message=failure_message_from_result(payload),
            path=path,
            host=host,
            ignored=ignored,
            kind="ignored" if ignored and kind == "failed" else kind,
        )

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._remember(result, ignored=bool(ignore_errors), kind="failed")

    def v2_runner_item_on_failed(self, result):
        self._remember(result, kind="failed")

    def v2_runner_on_unreachable(self, result):
        self._remember(result, kind="unreachable")

    def v2_playbook_on_stats(self, stats):
        del stats
        if not self._failures:
            return
        text = render_task_failures(self._failures, log_path=self._path)
        if not text:
            return
        if self._path:
            write_task_failures(self._path, text)
        self._display.display(text)
