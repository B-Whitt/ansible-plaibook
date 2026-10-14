# -*- coding: utf-8 -*-
"""Console entry that refuses an unsupported interpreter before importing Ansible."""

from __future__ import annotations

import sys
from typing import Sequence

from plaibook.runtime import UnsupportedPythonError, require_supported_python


def main(argv: Sequence[str] | None = None) -> int:
    try:
        require_supported_python()
    except UnsupportedPythonError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    from plaibook.cli import main as cli_main

    return cli_main(argv)
