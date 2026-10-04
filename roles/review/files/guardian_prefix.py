#!/usr/bin/env python3
"""Decide whether the controller ai-guardian prefix can be reused.

The prefix lives under the reviewer's home directory. A later process
that can write that directory can replace an imported module and leave
the wheels and the console script alone, so reuse requires:

- .pin matches the sha256 of the pinned requirements files in this checkout
- bin/ai-guardian is a regular, owner-executable file owned by this uid
  and is not a symlink
- every sha256 in those requirements files is present as a wheel under
  prefix/wheels
- every file in those wheels' RECORD matches the unpacked file under the
  prefix, and no extra .py, .pth, or .so is present

argv:
  status <prefix> <requirements> [requirements...]
  write-pin <prefix> <requirements> [requirements...]
  binary-ok <prefix>
  accept-wheels <prefix> <wheelhouse> <requirements> [requirements...]
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path


def requirements_pin(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def required_hashes(paths: list[str]) -> list[str]:
    hashes = []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            marker = "--hash=sha256:"
            if marker in line:
                hashes.append(line.split(marker, 1)[1].strip())
    return hashes


def binary_ok(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    if info.st_uid != os.getuid():
        return False
    return bool(info.st_mode & stat.S_IXUSR)


def _record_digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _read_regular(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    return path.read_bytes()


def _matching_wheels(wheel_dir: Path, req_paths: list[str]) -> list[Path] | None:
    required = required_hashes(req_paths)
    if not required or not wheel_dir.is_dir():
        return None
    found: dict[str, Path] = {}
    for wheel in wheel_dir.glob("*.whl"):
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest in required:
            found[digest] = wheel
    if any(digest not in found for digest in required):
        return None
    return [found[digest] for digest in required]


def wheels_match(wheel_dir: Path, req_paths: list[str]) -> bool:
    return _matching_wheels(wheel_dir, req_paths) is not None


def install_tree_matches(prefix: Path, wheel_dir: Path, req_paths: list[str]) -> bool:
    """True when the unpacked prefix matches the trusted wheel RECORDs.

    Generated console scripts under bin/ are not in the wheel. Every
    other hashed RECORD path must match, and an extra .py, .pth, or .so
    is a rebuild.
    """
    wheels = _matching_wheels(wheel_dir, req_paths)
    if not wheels:
        return False
    expected: dict[str, str] = {}
    for wheel in wheels:
        try:
            archive = zipfile.ZipFile(wheel)
        except zipfile.BadZipFile:
            return False
        with archive:
            record_name = next((name for name in archive.namelist() if name.endswith(".dist-info/RECORD")), None)
            if record_name is None:
                return False
            for line in archive.read(record_name).decode("utf-8").splitlines():
                if not line.strip():
                    continue
                parts = line.split(",")
                rel = parts[0]
                file_hash = parts[1] if len(parts) > 1 else ""
                if rel.endswith("/RECORD") or rel not in archive.namelist():
                    continue
                if not file_hash.startswith("sha256="):
                    continue
                expected[rel] = file_hash.split("=", 1)[1]
    if not expected:
        return False
    for rel, digest in expected.items():
        data = _read_regular(prefix / rel)
        if data is None or _record_digest(data) != digest:
            return False
    for path in prefix.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        rel = path.relative_to(prefix).as_posix()
        if rel == ".pin" or rel.startswith("wheels/") or rel.startswith("bin/"):
            continue
        if rel not in expected and rel.endswith((".py", ".pth", ".so")):
            return False
    return True


def cache_status(prefix: Path, req_paths: list[str]) -> dict:
    pin = requirements_pin(req_paths)
    pin_file = prefix / ".pin"
    wheel_dir = prefix / "wheels"
    if (
        pin_file.is_file()
        and pin_file.read_text(encoding="utf-8").strip() == pin
        and binary_ok(prefix / "bin" / "ai-guardian")
        and wheels_match(wheel_dir, req_paths)
        and install_tree_matches(prefix, wheel_dir, req_paths)
    ):
        return {"action": "reuse", "reason": "pin, binary, wheels, and installed files match"}
    return {"action": "rebuild", "reason": "prefix is missing or failed verification"}


def accept_wheels(prefix: Path, wheelhouse: Path, req_paths: list[str]) -> None:
    required = required_hashes(req_paths)
    found = {}
    for wheel in wheelhouse.glob("*.whl"):
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest in required:
            found[digest] = wheel
    missing = [digest for digest in required if digest not in found]
    if missing:
        sys.stderr.write("downloaded wheels do not match the pinned requirements hashes\n")
        sys.exit(1)
    dest = prefix / "wheels"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, mode=0o700)
    for digest in required:
        shutil.copy(found[digest], dest / found[digest].name)
    os.chmod(prefix, 0o700)


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: guardian_prefix.py status|write-pin|binary-ok|accept-wheels ...\n")
        sys.exit(2)
    command = sys.argv[1]
    prefix = Path(sys.argv[2])
    if command == "binary-ok":
        if not binary_ok(prefix / "bin" / "ai-guardian"):
            sys.stderr.write("ai-guardian binary is missing, a symlink, or not owned by this user\n")
            sys.exit(1)
        return
    req_paths = sys.argv[3:]
    if command == "status":
        json.dump(cache_status(prefix, req_paths), sys.stdout)
        sys.stdout.write("\n")
        return
    if command == "write-pin":
        prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
        pin_file = prefix / ".pin"
        pin_file.write_text(requirements_pin(req_paths) + "\n", encoding="utf-8")
        os.chmod(pin_file, 0o600)
        os.chmod(prefix, 0o700)
        return
    if command == "accept-wheels":
        if len(sys.argv) < 5:
            sys.stderr.write("accept-wheels requires a wheelhouse and requirements files\n")
            sys.exit(2)
        accept_wheels(prefix, Path(sys.argv[3]), sys.argv[4:])
        return
    sys.stderr.write(f"unknown command {command}\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
