# -*- coding: utf-8 -*-
"""Failure-log helpers. The playbook assertions live in scripts/run_playbook_tests.sh."""

import os
import stat

from plaibook.task_failures import (
    allowed_task_failures_path,
    clean_failure_message,
    create_task_failures_log,
    discard_empty_task_failure_log,
    failure_message_from_result,
    is_loop_aggregate,
    note_failure,
    read_task_failures_log,
    render_task_failures,
    write_task_failures,
)


def test_clean_failure_message_redacts_url_secrets_and_truncates():
    secret = "https://x-access-token:ghs_secret@github.com/org/repo.git?token=ghs_secret"
    text = clean_failure_message(f"planned failure {secret}")
    assert "ghs_secret" not in text
    assert "planned failure" in text
    assert "://***@" in text
    assert "token=***" in text
    assert clean_failure_message("x" * 900).endswith("…")
    assert len(clean_failure_message("x" * 900)) == 800
    assert clean_failure_message("") == "task failed"
    assert "\x1b" not in clean_failure_message("boom\x1b[31m")


def test_note_failure_sanitizes_task_path_and_host():
    secret = "https://x-access-token:ghs_secret@github.com/org/repo.git?token=ghs_secret"
    failures: list[dict[str, str]] = []
    note_failure(
        failures,
        task=f"Clone {secret}\nFAKE FAILURE",
        message="planned failure",
        path=f"{secret}\x1b[31m",
        host=f"host\x1b[2J{secret}",
    )
    report = render_task_failures(failures)
    assert "ghs_secret" not in report
    assert "\nFAKE FAILURE" not in report
    assert "\x1b" not in report
    assert "://***@" in report
    assert "token=***" in report
    assert report.count("\n- ") == 1


def test_render_is_empty_without_failures_and_lists_them_otherwise():
    assert render_task_failures([]) == ""
    failures: list[dict[str, str]] = []
    note_failure(
        failures,
        task="Record a planned failure for the error log",
        message="planned failure for the error log",
        path="tests/test_task_failures_log.yml:12",
        host="localhost",
        ignored=True,
    )
    report = render_task_failures(failures, log_path="/tmp/plaibook-task-failures-x/log.log")
    assert "Task failures (1):" in report
    assert "Record a planned failure for the error log" in report
    assert "[ignored]" in report
    assert "localhost:" in report
    assert report.endswith("\n")


def test_loop_aggregate_is_not_a_single_item():
    assert is_loop_aggregate({"results": [{"_ansible_item_result": True, "failed": True, "msg": "nope"}]})
    assert not is_loop_aggregate({"msg": "Cursor agent failed to start", "failed": True})
    assert failure_message_from_result({"msg": "database is locked", "stdout": "SECRET"}) == "database is locked"
    assert "SECRET" not in failure_message_from_result({"stdout": "SECRET", "exception": "SECRET"})


def test_write_rejects_unsafe_paths_and_roundtrips_a_private_log(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    # gettempdir is cached; point the helper at a private directory we own.
    parent, path = create_task_failures_log(directory=str(tmp_path))
    assert os.path.basename(parent).startswith("plaibook-task-failures-")
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o700
    assert allowed_task_failures_path(path) == path
    assert allowed_task_failures_path("/tmp/plaibook-task-failures-nope.log") is None
    assert allowed_task_failures_path("plaibook-task-failures-rel.log") is None
    report = render_task_failures(
        [
            {
                "task": "wait",
                "message": "database is locked",
                "path": "",
                "host": "",
                "ignored": "true",
                "kind": "ignored",
            }
        ]
    )
    assert write_task_failures(path, report)
    assert "database is locked" in read_task_failures_log(path)

    real_write = os.write

    def short_write(fd: int, data: bytes | memoryview) -> int:
        raw = data.tobytes() if isinstance(data, memoryview) else data
        return real_write(fd, raw[:3])

    monkeypatch.setattr(os, "write", short_write)
    assert write_task_failures(path, "abcdefghijklmnopqrstuvwxyz")
    assert read_task_failures_log(path) == "abcdefghijklmnopqrstuvwxyz"

    def zero_write(fd: int, data: bytes | memoryview) -> int:
        del fd, data
        return 0

    monkeypatch.setattr(os, "write", zero_write)
    assert write_task_failures(path, "still-there") is False
    assert read_task_failures_log(path) == ""
    assert write_task_failures(path, "   ") is False
    os.chmod(parent, 0o755)
    assert allowed_task_failures_path(path) is None
    os.chmod(parent, 0o700)
    secret = tmp_path / "secret.txt"
    secret.write_text("super-secret-token-value", encoding="utf-8")
    os.unlink(path)
    os.symlink(secret, path)
    assert read_task_failures_log(path) == ""
    os.unlink(path)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    discard_empty_task_failure_log(path)
    assert not os.path.exists(path)
    assert not os.path.exists(parent)
    assert secret.read_text(encoding="utf-8") == "super-secret-token-value"


def test_oversized_failure_log_is_truncated_and_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("plaibook.task_failures._READ_LIMIT", 40)
    parent, path = create_task_failures_log(directory=str(tmp_path))
    payload = b"planned failure for the error log\n" * 8
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    text = read_task_failures_log(path)
    assert text.startswith("planned failure")
    assert "ghs_secret" not in text
    assert text.rstrip().endswith("… failure log truncated")
    assert len(text.encode("utf-8")) < len(payload)
    discard_empty_task_failure_log(path)
    assert os.path.exists(path)
    assert os.path.isdir(parent)
