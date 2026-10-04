#!/usr/bin/env python3
"""Stand-in for pip3 in the guardian cache playbook test.

download copies the wheels from FAKE_WHEEL_SRC into -d.
install writes an executable prefix/bin/ai-guardian that emits [].
FAKE_PIP_FAIL=1 exits before either action. Every invocation is appended
to FAKE_PIP_LOG.
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path


def _opt(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def main() -> None:
    log = Path(os.environ["FAKE_PIP_LOG"])
    with log.open("a", encoding="utf-8") as handle:
        handle.write(sys.argv[1] + "\n")
    if os.environ.get("FAKE_PIP_FAIL") == "1":
        sys.stderr.write("fake pip failed\n")
        sys.exit(1)
    command = sys.argv[1]
    args = sys.argv[2:]
    if command == "download":
        dest = Path(_opt(args, "-d"))
        dest.mkdir(parents=True, exist_ok=True)
        for wheel in Path(os.environ["FAKE_WHEEL_SRC"]).glob("*.whl"):
            shutil.copy(wheel, dest / wheel.name)
        return
    if command == "install":
        target = Path(_opt(args, "--target"))
        target.mkdir(parents=True, exist_ok=True)
        for wheel in Path(os.environ["FAKE_WHEEL_SRC"]).glob("*.whl"):
            if zipfile.is_zipfile(wheel):
                with zipfile.ZipFile(wheel) as archive:
                    archive.extractall(target)
        bindir = target / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        exe = bindir / "ai-guardian"
        exe.write_text(Path(os.environ["FAKE_GUARDIAN"]).read_text(encoding="utf-8"), encoding="utf-8")
        exe.chmod(0o755)
        return
    sys.stderr.write(f"fake pip unused command {command}\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
