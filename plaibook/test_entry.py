# -*- coding: utf-8 -*-
"""The console entry refuses Python 3.15 before importing the CLI."""

from __future__ import annotations

import builtins

from plaibook.entry import main


def test_entrypoint_refuses_python_315_before_importing_cli(monkeypatch, capsys):
    monkeypatch.setattr("plaibook.runtime.sys.version_info", (3, 15, 0))
    imported: list[str] = []
    real_import = builtins.__import__

    def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "plaibook.cli":
            imported.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    code = main(["review"])
    err = capsys.readouterr().err
    assert code == 2
    assert imported == []
    assert "Python 3.15 is not supported" in err
    assert "3.10 through 3.14" in err
