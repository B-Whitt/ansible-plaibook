#!/usr/bin/env python3
"""Write two tiny wheels and the requirements files that pin them.

argv: destination directory. Creates wheels/*.whl, ai.txt, and requests.txt.
"""

from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path


def _wheel(name: str, module: str, source: bytes) -> bytes:
    dist = f"{module}-0.dist-info"
    files = {
        f"{module}/__init__.py": source,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 0\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
    }
    lines = []
    for rel, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        lines.append(f"{rel},sha256={digest},{len(data)}")
    lines.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(lines) + "\n").encode()
    from io import BytesIO

    raw = BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for rel, data in files.items():
            archive.writestr(rel, data)
    return raw.getvalue()


def main() -> None:
    root = Path(sys.argv[1])
    wheels = root / "wheels"
    wheels.mkdir()
    payloads = (
        ("ai.txt", "ai.whl", "ai-guardian", "ai_guardian", b"x = 1\n"),
        ("requests.txt", "requests.whl", "requests", "requests", b"y = 2\n"),
    )
    for req_name, wheel_name, dist, module, source in payloads:
        data = _wheel(dist, module, source)
        (wheels / wheel_name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        (root / req_name).write_text(f"{dist}==0 --hash=sha256:{digest}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
