# -*- coding: utf-8 -*-
"""Expose ``plaibook.version_text()`` to review playbooks."""

from __future__ import annotations

from plaibook import version_text


def plaibook_version_line(_unused: str = "") -> str:
    return version_text()


class FilterModule:
    def filters(self):
        return {"plaibook_version_line": plaibook_version_line}
