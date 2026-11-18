"""Render the Sphinx roles in the SDK's docstrings (``:class:`~a.B```) as plain code."""

from __future__ import annotations

import re
from typing import Any

import griffe

_ROLE = re.compile(r":(?:class|func|meth|mod|exc|attr|data|obj):`~?([^`]+)`")


def _plain(match: re.Match[str]) -> str:
    target = match.group(1)
    if " <" in target and target.endswith(">"):
        target = target.split(" <", 1)[0]
    return f"`{target.rsplit('.', 1)[-1]}`"


class RstRoles(griffe.Extension):
    def on_instance(self, *, node: Any = None, obj: Any = None, **kwargs: Any) -> None:
        if obj is not None and obj.docstring is not None:
            obj.docstring.value = _ROLE.sub(_plain, obj.docstring.value)
