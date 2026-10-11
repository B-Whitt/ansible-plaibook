# -*- coding: utf-8 -*-
"""Controller ai-guardian prefix reuse checks."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
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


def _zip_wheel(module: str, source: bytes) -> bytes:
    dist = f"{module}-0.dist-info"
    files = {
        f"{module}/__init__.py": source,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {module}\nVersion: 0\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
    }
    lines = []
    for rel, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        lines.append(f"{rel},sha256={digest},{len(data)}")
    lines.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(lines) + "\n").encode()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for rel, data in files.items():
            archive.writestr(rel, data)
    return raw.getvalue()


def _installed(tmp_path: Path, payloads: dict[str, bytes]) -> tuple[object, list[str], Path]:
    module = _load()
    wheel_src = tmp_path / "src"
    wheel_src.mkdir()
    reqs = []
    prefix = tmp_path / "prefix"
    cached = prefix / "wheels"
    cached.mkdir(parents=True)
    for index, (name, source) in enumerate(payloads.items()):
        data = _zip_wheel(name, source)
        (wheel_src / f"{name}.whl").write_bytes(data)
        (cached / f"{name}.whl").write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        req = tmp_path / f"req{index}.txt"
        req.write_text(f"{name}==0 --hash=sha256:{digest}\n", encoding="utf-8")
        reqs.append(str(req))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(prefix)
    binary = prefix / "bin" / "ai-guardian"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return module, reqs, prefix


def test_reuse_requires_the_installed_tree_to_match_the_wheel(tmp_path: Path):
    module, reqs, prefix = _installed(tmp_path, {"ai_guardian": b"x = 1\n", "requests": b"y = 2\n"})
    assert module.cache_status(prefix, reqs)["action"] == "rebuild"
    (prefix / ".pin").write_text(module.requirements_pin(reqs) + "\n", encoding="utf-8")
    assert module.cache_status(prefix, reqs)["action"] == "reuse"
    init = prefix / "ai_guardian" / "__init__.py"
    init.write_text("x = 9\n", encoding="utf-8")
    assert module.cache_status(prefix, reqs)["action"] == "rebuild"
    init.write_text("x = 1\n", encoding="utf-8")
    (prefix / "ai_guardian" / "extra.py").write_text("z = 3\n", encoding="utf-8")
    assert module.cache_status(prefix, reqs)["action"] == "rebuild"
    (prefix / "ai_guardian" / "extra.py").unlink()
    binary = prefix / "bin" / "ai-guardian"
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
