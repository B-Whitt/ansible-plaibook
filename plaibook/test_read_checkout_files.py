# -*- coding: utf-8 -*-
"""One-shot checkout reader used instead of a per-file SSH stat loop."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "roles" / "review" / "files" / "read_checkout_files.py"


def _run(root: Path, markers: list[str], files: list[str]) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        input=json.dumps({"markers": markers, "files": files}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_reads_markers_and_small_files_and_skips_the_rest(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "big.bin").write_bytes(b"x" * (1048576 + 1))
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside, tmp_path / "linked")
        result = _run(
            tmp_path,
            ["pyproject.toml", "go.mod", "PROJECT"],
            ["src/app.py", "missing.py", "big.bin", "linked", "../outside-secret", "src"],
        )
    finally:
        outside.unlink(missing_ok=True)
    assert result["markers"] == {"pyproject.toml": True, "go.mod": False, "PROJECT": False}
    assert result["go_mod"] is None
    assert result["files"] == {"src/app.py": "print('hi')\n"}
