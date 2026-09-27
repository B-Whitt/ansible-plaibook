# -*- coding: utf-8 -*-
"""Spaceless Ansible interpreter and PEP 668 detection."""

from __future__ import annotations

from pathlib import Path

from plaibook.runtime import controller_python, interpreter_is_externally_managed


def test_controller_python_is_unused_when_path_has_no_space(monkeypatch):
    monkeypatch.setattr("plaibook.runtime.sys.executable", "/usr/bin/python3")
    assert controller_python() is None


def test_controller_python_wrapper_quotes_a_spaced_interpreter(tmp_path, monkeypatch):
    real = tmp_path / "Application Support" / "pipx" / "bin" / "python"
    real.parent.mkdir(parents=True)
    real.write_text("")
    monkeypatch.setattr("plaibook.runtime.sys.executable", str(real))
    monkeypatch.setattr("plaibook.runtime.Path.home", lambda: tmp_path)
    wrapper = Path(controller_python())
    text = wrapper.read_text(encoding="utf-8")
    assert " " not in str(wrapper)
    assert text.startswith("#!/bin/sh\n")
    assert f"exec '{real}' \"$@\"\n" == text.split("#!/bin/sh\n", 1)[1]


def test_controller_python_skips_when_home_contains_a_space(tmp_path, monkeypatch):
    real = tmp_path / "Application Support" / "pipx" / "bin" / "python"
    real.parent.mkdir(parents=True)
    real.write_text("")
    home = tmp_path / "Ada Lovelace"
    monkeypatch.setattr("plaibook.runtime.sys.executable", str(real))
    monkeypatch.setattr("plaibook.runtime.Path.home", lambda: home)
    assert controller_python() is None
    assert not (home / ".local").exists()


def test_externally_managed_marker(tmp_path, monkeypatch):
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "EXTERNALLY-MANAGED").write_text("pep 668\n")
    monkeypatch.setattr("plaibook.runtime.sys.prefix", "/usr")
    monkeypatch.setattr("plaibook.runtime.sys.base_prefix", "/usr")
    monkeypatch.setattr("plaibook.runtime.sysconfig.get_path", lambda name: str(stdlib))
    assert interpreter_is_externally_managed()
    (stdlib / "EXTERNALLY-MANAGED").unlink()
    assert not interpreter_is_externally_managed()


def test_virtualenv_is_not_externally_managed(tmp_path, monkeypatch):
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "EXTERNALLY-MANAGED").write_text("pep 668\n")
    monkeypatch.setattr("plaibook.runtime.sys.prefix", str(tmp_path / "venv"))
    monkeypatch.setattr("plaibook.runtime.sys.base_prefix", "/usr")
    monkeypatch.setattr("plaibook.runtime.sysconfig.get_path", lambda name: str(stdlib))
    assert not interpreter_is_externally_managed()
