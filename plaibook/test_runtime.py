# -*- coding: utf-8 -*-
"""Supported interpreter check."""

from __future__ import annotations

import pytest

from plaibook.runtime import UnsupportedPythonError, require_supported_python


def test_python_314_is_supported():
    require_supported_python((3, 14))
    require_supported_python((3, 10))


def test_python_315_is_refused():
    with pytest.raises(UnsupportedPythonError, match="Python 3.15 is not supported") as exc:
        require_supported_python((3, 15))
    assert "3.10 through 3.14" in str(exc.value)


def test_python_316_is_refused():
    with pytest.raises(UnsupportedPythonError, match="Python 3.16 is not supported"):
        require_supported_python((3, 16))
