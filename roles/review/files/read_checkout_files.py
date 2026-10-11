#!/usr/bin/env python3
"""Read marker existence and diff-file text in one process.

stdin is {"markers": [relative, ...], "files": [relative, ...]}.
argv[1] is the checkout root.
stdout is {"markers": {name: bool}, "go_mod": str|null, "files": {path: text}}.
"""

from __future__ import annotations

import json
import os
import stat
import sys

_MAX_BYTES = 1048576


def _resolve(root: str, rel: object):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\x00" in rel:
        return None
    if ".." in rel.split("/"):
        return None
    path = os.path.join(root, rel)
    try:
        st = os.stat(path, follow_symlinks=True)
    except OSError:
        return None
    real = os.path.realpath(path)
    if real != root and not real.startswith(root + os.sep):
        return None
    return path, st


def _text(path: str, size: int) -> str:
    with open(path, "rb") as handle:
        data = handle.read(min(size, _MAX_BYTES))
    return data.decode("utf-8", errors="replace")


def main() -> None:
    root = os.path.realpath(sys.argv[1])
    request = json.load(sys.stdin)
    markers = {}
    for name in request.get("markers") or []:
        resolved = _resolve(root, name)
        markers[name] = resolved is not None
    go_mod = None
    resolved = _resolve(root, "go.mod")
    if resolved is not None and stat.S_ISREG(resolved[1].st_mode):
        go_mod = _text(resolved[0], resolved[1].st_size)
    files = {}
    for rel in request.get("files") or []:
        resolved = _resolve(root, rel)
        if resolved is None:
            continue
        path, st = resolved
        if not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_BYTES:
            continue
        files[rel] = _text(path, st.st_size)
    json.dump({"markers": markers, "go_mod": go_mod, "files": files}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
