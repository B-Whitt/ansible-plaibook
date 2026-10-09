# -*- coding: utf-8 -*-
"""Failure-log helpers. The playbook assertions live in scripts/run_playbook_tests.sh."""

import os
import stat
import time

from plaibook.task_failures import (
    _MESSAGE_LIMIT,
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
    secret = (
        "https://x-access-token:"
        + "ghs_secret@"
        + "github.com/org/repo.git?token=ghs_secret"
    )
    text = clean_failure_message(f"planned failure {secret}")
    assert "ghs_secret" not in text
    assert "planned failure" in text
    assert "://***@" in text
    assert "token=***" in text
    assert clean_failure_message("x" * 900).endswith("…")
    assert len(clean_failure_message("x" * 900)) == 800
    assert clean_failure_message("") == "task failed"
    assert "\x1b" not in clean_failure_message("boom\x1b[31m")
    colon_password = clean_failure_message(
        "clone https://user:" + "p:ass@" + "github.com/org/repo"
    )
    assert "p:ass" not in colon_password
    assert "://***@" in colon_password
    forms = clean_failure_message(
        "fail password=hunter2 secret=s3cret client_secret=abc signature=sigval "
        "Authorization: Bearer "
        + "bearer-token-value -u alice:s3cret"
    )
    for leaked in ("hunter2", "s3cret", "abc", "sigval", "bearer-token-value"):
        assert leaked not in forms
    assert "password=***" in forms
    assert "Authorization: Bearer ***" in forms
    assert "-u alice:***" in forms
    prefixed = clean_failure_message(
        "GITHUB_TOKEN=valueone CURSOR_API_KEY=valuetwo "
        "AWS_SECRET_ACCESS_KEY=valuethree DB_PASSWORD=valuefour "
        "Authorization: token valuefive"
    )
    for leaked in ("valueone", "valuetwo", "valuethree", "valuefour", "valuefive"):
        assert leaked not in prefixed
    assert "GITHUB_TOKEN=***" in prefixed
    assert "CURSOR_API_KEY=***" in prefixed
    assert "DB_PASSWORD=***" in prefixed
    assert "Authorization: token ***" in prefixed
    quoted = clean_failure_message(
        "PASS"
        + 'WORD="spa ce1" secret=\'spa ce2\' '
        '"token": "spa ce3" '
        "Authorization: Bearer "
        + '"spa ce4" '
        "Authorization: token 'spa ce5' "
        'Authorization: "Bearer spa ce8" '
        '-u "alice:spa ce6" --user \'bob:spa ce7\''
    )
    for leaked in (
        "spa ce1",
        "spa ce2",
        "spa ce3",
        "spa ce4",
        "spa ce5",
        "spa ce6",
        "spa ce7",
        "spa ce8",
        "ce1",
        "ce2",
        "ce3",
        "ce4",
        "ce5",
        "ce6",
        "ce7",
        "ce8",
    ):
        assert leaked not in quoted, leaked
    assert "PASSWORD=***" in quoted
    assert "secret=***" in quoted
    assert '"token": ***' in quoted
    assert "Authorization: Bearer ***" in quoted
    assert "Authorization: token ***" in quoted
    assert 'Authorization: ***' in quoted
    assert "-u ***" in quoted
    assert "--user ***" in quoted
    attached = clean_failure_message(
        'curl -u alice:"alpha beta" --user bob:\'gamma delta\' '
        '--user=carol:"epsilon zeta"'
    )
    for leaked in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
        assert leaked not in attached, leaked
    assert 'curl -u alice:***' in attached
    assert "--user bob:***" in attached
    assert "--user=carol:***" in attached
    flags = clean_failure_message(
        'run --token cli-secret --password "alpha beta" --api-key key-secret'
    )
    for leaked in ("cli-secret", "alpha", "beta", "key-secret"):
        assert leaked not in flags, leaked
    assert "--token ***" in flags
    assert "--password ***" in flags
    assert "--api-key ***" in flags
    suffixed = clean_failure_message(
        'AWS_SECRET_ACCESS_KEY_ID=id-value CURSOR_API_KEY_ID=cursor-id api_key_id="alpha beta"'
    )
    for leaked in ("id-value", "cursor-id", "alpha", "beta"):
        assert leaked not in suffixed, leaked
    assert "AWS_SECRET_ACCESS_KEY_ID=***" in suffixed
    assert "CURSOR_API_KEY_ID=***" in suffixed
    assert "api_key_id=***" in suffixed
    bare = "ghp_" + "abcd5678wxyz"
    pem = (
        "-----BEGIN "
        + "RSA PRIVATE KEY-----\nMIIB\n-----END "
        + "RSA PRIVATE KEY-----"
    )
    loose = clean_failure_message(f"authentication failed: {bare}\n{pem}")
    assert bare not in loose
    assert "PRIVATE KEY" not in loose
    assert "MIIB" not in loose
    assert "***" in loose
    bearer_assign = clean_failure_message("BEARER=real-token")
    assert "real-token" not in bearer_assign
    assert "BEARER=***" in bearer_assign
    attached_short = clean_failure_message(
        'mysql -psecret && mysql -p SECRET && mysql -p "secret" && curl -uuser:S3CRET'
    )
    for leaked in ("secret", "SECRET", "S3CRET", "-psecret"):
        assert leaked not in attached_short, leaked
    assert "mysql -p***" in attached_short
    assert "mysql -p ***" in attached_short
    assert "curl -uuser:***" in attached_short
    empty_user = clean_failure_message("curl -u :S3CRET && curl --user=:S3CRET")
    assert "S3CRET" not in empty_user
    assert "curl -u :***" in empty_user
    assert "curl --user=:***" in empty_user
    clients = clean_failure_message("mysqldump -psecret && psql -p 5432")
    assert "mysqldump -p***" in clients
    assert "psql -p 5432" in clients


def test_short_keys_after_an_underscore_are_redacted():
    text = clean_failure_message(
        "DB_KEY=db-secret export SSH_KEY=ssh-secret STRIPE_KEY: stripe-secret APP_AUTH=app-secret"
    )
    for leaked in ("db-secret", "ssh-secret", "stripe-secret", "app-secret"):
        assert leaked not in text, leaked
    assert "DB_KEY=***" in text
    assert "SSH_KEY=***" in text
    assert "STRIPE_KEY: ***" in text
    assert "APP_AUTH=***" in text
    assert "monkey: banana" in clean_failure_message("monkey: banana")
    assert clean_failure_message("PGPASSWORD=pg-secret") == "PGPASSWORD=***"


def test_mysql_password_flag_after_other_options_is_redacted():
    text = clean_failure_message(
        "mysql -u root -psecret && mysql -h db.local -p secret && mariadb -psecret"
    )
    for leaked in ("secret", "-psecret"):
        assert leaked not in text, leaked
    assert "mysql -u root -p***" in text
    assert "mysql -h db.local -p ***" in text
    assert "mariadb -p***" in text
    assert clean_failure_message("psql -p 5432") == "psql -p 5432"


def test_pem_longer_than_the_message_cap_is_redacted():
    body = "MIIB" * 400
    header = "-----BEGIN " + "RSA PRIVATE KEY-----\n"
    footer = "\n-----END " + "RSA PRIVATE KEY-----"
    text = clean_failure_message(f"ssh failed: {header}{body}{footer}")
    assert "MIIB" not in text
    assert "PRIVATE KEY" not in text
    assert text.startswith("ssh failed:")


def test_bare_github_installation_tokens_are_redacted():
    prefixes = ("ghs_", "gho_", "ghu_", "ghr_", "sk-" + "proj-", "sk_" + "live_", "sk_" + "test_")
    body = " ".join(prefix + "abcd5678wxyz" for prefix in prefixes)
    text = clean_failure_message("fail " + body)
    assert "abcd5678wxyz" not in text
    for prefix in prefixes:
        assert prefix not in text


def test_quoted_secret_cut_by_the_message_cap_is_masked():
    marker = ' password="'
    inside = "alpha beta"
    intro = "a" * (_MESSAGE_LIMIT - len(marker) - len(inside))
    message = intro + marker + inside + ' gamma delta"'
    assert len(intro + marker + inside) == _MESSAGE_LIMIT
    text = clean_failure_message(message)
    for leaked in ("alpha", "beta", "gamma", "delta"):
        assert leaked not in text, leaked
    kept = clean_failure_message('note monkey: "banana split remains"')
    assert "banana split remains" in kept


def test_create_log_removes_partial_directory(tmp_path, monkeypatch):
    real_chmod = os.chmod

    def boom_chmod(path, mode):
        if os.path.basename(path).startswith("plaibook-" + "task-failures-"):
            raise OSError("chmod failed")
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", boom_chmod)
    try:
        create_task_failures_log(directory=str(tmp_path))
    except OSError as exc:
        assert "chmod failed" in str(exc)
    else:
        raise AssertionError("chmod failure must propagate")
    assert list(tmp_path.iterdir()) == []

    def boom_fchmod(_fd, _mode):
        raise OSError("fchmod failed")

    monkeypatch.setattr(os, "chmod", real_chmod)
    monkeypatch.setattr(os, "fchmod", boom_fchmod)
    try:
        create_task_failures_log(directory=str(tmp_path))
    except OSError as exc:
        assert "fchmod failed" in str(exc)
    else:
        raise AssertionError("fchmod failure must propagate")
    assert list(tmp_path.iterdir()) == []


def test_clean_failure_message_keeps_ports_paths_and_ordinary_words():
    text = clean_failure_message(
        "mkdir -p /var/lib/plaibook/cache\n"
        "ssh -p 2222\n"
        "docker run -p 8080:80\n"
        "find . -print0\n"
        "monkey: banana\n"
        "build-utils: version mismatch"
    )
    assert "mkdir -p /var/lib/plaibook/cache" in text
    assert "ssh -p 2222" in text
    assert "docker run -p 8080:80" in text
    assert "find . -print0" in text
    assert "monkey: banana" in text
    assert "build-utils: version mismatch" in text


def test_clean_failure_message_bounds_long_input():
    samples = ("a_" * 8000, "key_a" * 3200)
    for sample in samples:
        started = time.monotonic()
        text = clean_failure_message(sample)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, elapsed
        assert len(text) == 800
        assert text.endswith("…")


def test_read_redacts_pem_when_controls_split_the_header(tmp_path):
    _parent, path = create_task_failures_log(directory=str(tmp_path))
    header = "-----BEGIN " + "\x1b[31m" + "RSA PRIVATE KEY-----"
    footer = "-----END " + "\x1b[0m" + "RSA PRIVATE KEY-----"
    body = "MIIB" * 16
    payload = "\n".join(["ssh failed:", header, body, footer, "after the key"]) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    text = read_task_failures_log(path)
    assert "MIIB" not in text
    assert "PRIVATE KEY" not in text
    assert "after the key" in text
    assert "ssh failed:" in text


def test_unreachable_loop_item_is_recorded():
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "callback_plugins",
        "plaibook_task_failures.py",
    )
    spec = importlib.util.spec_from_file_location("plaibook_task_failures_cb", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class _Task:
        ignore_errors = False
        ignore_unreachable = False

        def get_name(self):
            return "ping each host"

        def get_path(self):
            return "play.yml:10"

    class _Host:
        def get_name(self):
            return "db.example"

    class _Result:
        def __init__(self, payload):
            self._result = payload
            self._task = _Task()
            self._host = _Host()

    callback = module.CallbackModule()
    callback.v2_runner_item_on_failed(
        _Result(
            {
                "unreachable": True,
                "msg": "host down",
                "_ansible_item_result": True,
            }
        )
    )
    callback.v2_runner_item_on_unreachable(
        _Result({"unreachable": True, "msg": "second path down", "_ansible_item_result": True})
    )
    assert len(callback._failures) == 2
    assert callback._failures[0]["kind"] == "unreachable"
    assert callback._failures[0]["message"] == "host down"
    assert callback._failures[1]["kind"] == "unreachable"
    assert "second path down" in callback._failures[1]["message"]


def test_read_redacts_a_multiline_pem_block(tmp_path):
    _parent, path = create_task_failures_log(directory=str(tmp_path))
    body = "\n".join("MIIB" * 16 for _ in range(30))
    payload = (
        "ssh failed:\n-----BEGIN "
        + "RSA PRIVATE KEY-----\n"
        + body
        + "\n-----END "
        + "RSA PRIVATE KEY-----\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    text = read_task_failures_log(path)
    assert "MIIB" not in text
    assert "PRIVATE KEY" not in text
    assert "ssh failed:" in text


def test_read_redacts_a_long_line_within_a_time_bound(tmp_path):
    _parent, path = create_task_failures_log(directory=str(tmp_path))
    payload = ("key_a" * 3200) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    started = time.monotonic()
    text = read_task_failures_log(path)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, elapsed
    assert len(text.splitlines()[0]) == 800
    assert text.splitlines()[0].endswith("…")


def test_read_sanitizes_a_log_the_playbook_overwrote(tmp_path, monkeypatch):
    monkeypatch.setattr("plaibook.task_failures._READ_LIMIT", 4000)
    _parent, path = create_task_failures_log(directory=str(tmp_path))
    poisoned = (
        "\x1b[31mhttps://user:" + "p:ass@" + "github.com/org/repo?password=hunter2\n"
        "Authorization: Bearer " + "bearer-token-value\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, poisoned.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    text = read_task_failures_log(path)
    assert "\x1b" not in text
    assert "p:ass" not in text
    assert "hunter2" not in text
    assert "bearer-token-value" not in text
    assert "://***@" in text


def test_note_failure_sanitizes_task_path_and_host():
    secret = (
        "https://x-access-token:"
        + "ghs_secret@"
        + "github.com/org/repo.git?token=ghs_secret"
    )
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


def test_fifo_log_open_returns_without_blocking(tmp_path):
    _parent, path = create_task_failures_log(directory=str(tmp_path))
    os.unlink(path)
    os.mkfifo(path, 0o600)
    os.chmod(path, 0o600)
    started = time.monotonic()
    assert write_task_failures(path, "planned failure") is False
    assert read_task_failures_log(path) == ""
    assert time.monotonic() - started < 2


def test_hard_linked_log_is_not_read_or_written(tmp_path):
    parent, path = create_task_failures_log(directory=str(tmp_path))
    assert write_task_failures(path, "planned failure\n")
    link = os.path.join(parent, "plaibook-task-failures-link.log")
    os.link(path, link)
    assert os.stat(path).st_nlink == 2
    assert read_task_failures_log(path) == ""
    assert write_task_failures(path, "replacement\n") is False
    os.unlink(link)
    assert "planned failure" in read_task_failures_log(path)
