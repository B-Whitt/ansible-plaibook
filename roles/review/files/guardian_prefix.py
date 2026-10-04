#!/usr/bin/env python3
"""Decide whether the controller ai-guardian prefix can be reused.

The prefix lives under the reviewer's home directory. A later process
that can write that directory can replace the unpacked binary, so reuse
requires three checks against data that is not the binary itself:

- .pin matches the sha256 of the pinned requirements files in this checkout
- bin/ai-guardian is a regular, owner-executable file owned by this uid
  and is not a symlink
- every sha256 in those requirements files is present as a wheel under
  prefix/wheels

argv:
  status <prefix> <requirements> [requirements...]
  write-pin <prefix> <requirements> [requirements...]
  binary-ok <prefix>
  accept-wheels <prefix> <wheelhouse> <requirements> [requirements...]
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
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


def wheels_match(wheel_dir: Path, req_paths: list[str]) -> bool:
    required = required_hashes(req_paths)
    if not required or not wheel_dir.is_dir():
        return False
    found = set()
    for wheel in wheel_dir.glob("*.whl"):
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest in required:
            found.add(digest)
    return set(required) <= found


def cache_status(prefix: Path, req_paths: list[str]) -> dict:
    pin = requirements_pin(req_paths)
    pin_file = prefix / ".pin"
    if (
        pin_file.is_file()
        and pin_file.read_text(encoding="utf-8").strip() == pin
        and binary_ok(prefix / "bin" / "ai-guardian")
        and wheels_match(prefix / "wheels", req_paths)
    ):
        return {"action": "reuse", "reason": "pin, binary, and wheel hashes match"}
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
