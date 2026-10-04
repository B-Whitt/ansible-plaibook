# -*- coding: utf-8 -*-
"""Controller ai-guardian prefix reuse checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "roles" / "review" / "files" / "guardian_prefix.py"


def _load():
    spec = importlib.util.spec_from_file_location("guardian_prefix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reqs(tmp_path: Path, payloads: dict[str, bytes]) -> tuple[list[str], Path]:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    reqs = []
    for index, (name, data) in enumerate(payloads.items()):
        (wheel_dir / f"{name}.whl").write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        req = tmp_path / f"req{index}.txt"
        req.write_text(f"{name}==1 --hash=sha256:{digest}\n", encoding="utf-8")
        reqs.append(str(req))
    return reqs, wheel_dir


def test_reuse_requires_pin_owner_and_wheel_hashes(tmp_path: Path):
    module = _load()
    reqs, wheel_dir = _reqs(tmp_path, {"ai": b"ai-wheel\n", "requests": b"req-wheel\n"})
    prefix = tmp_path / "prefix"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    binary = bindir / "ai-guardian"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    (prefix / "wheels").mkdir()
    for wheel in wheel_dir.glob("*.whl"):
        (prefix / "wheels" / wheel.name).write_bytes(wheel.read_bytes())
    assert module.cache_status(prefix, reqs)["action"] == "rebuild"
    (prefix / ".pin").write_text(module.requirements_pin(reqs) + "\n", encoding="utf-8")
    assert module.cache_status(prefix, reqs)["action"] == "reuse"
    binary.unlink()
    binary.symlink_to("/bin/true")
    assert module.cache_status(prefix, reqs)["action"] == "rebuild"


def test_accept_wheels_rejects_a_hash_mismatch(tmp_path: Path):
    reqs, wheel_dir = _reqs(tmp_path, {"ai": b"ai-wheel\n"})
    (wheel_dir / "ai.whl").write_bytes(b"tampered\n")
    prefix = tmp_path / "prefix"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "accept-wheels", str(prefix), str(wheel_dir), *reqs],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0
    assert "do not match" in completed.stderr


def test_status_command_prints_rebuild_for_a_missing_prefix(tmp_path: Path):
    req = tmp_path / "req.txt"
    req.write_text("ai==1 --hash=sha256:" + hashlib.sha256(b"x").hexdigest() + "\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "status", str(tmp_path / "missing"), str(req)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(completed.stdout)["action"] == "rebuild"
