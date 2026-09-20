"""Discovering the live display layout.

``kscreen-doctor -o`` is the only layout source used here.  It is a normal
user-space command, it reports *logical* geometry (rotation and scale already
applied), and its coordinate space is the one KWin uses globally -- which is
exactly what ``wcs.geometry`` expects.

Reading the layout is deliberately kept out of the compositor: nothing in this
module needs a KWin script, a portal, or any elevated access.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

from .geometry import Layout, Output, Rect

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_OUTPUT = re.compile(r"^Output:\s+(\S+)\s+(\S+)")
_GEOMETRY = re.compile(r"^\s*Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)\s*$")


class LayoutError(RuntimeError):
    pass


def parse_kscreen_doctor(text: str) -> Layout:
    """Parse ``kscreen-doctor -o`` output into a :class:`Layout`.

    Only enabled outputs are included; a disabled output still carries a
    ``Geometry:`` line, and treating it as real would invent displays that
    the pointer can never reach.
    """
    outputs: list[Output] = []
    name: Optional[str] = None
    enabled = False
    rect: Optional[Rect] = None

    def flush() -> None:
        if name is not None and enabled and rect is not None:
            outputs.append(Output(name=name, rect=rect))

    for raw in text.splitlines():
        line = _ANSI.sub("", raw).rstrip()
        m = _OUTPUT.match(line)
        if m:
            flush()
            name, enabled, rect = m.group(2), False, None
            continue
        if name is None:
            continue
        stripped = line.strip()
        if stripped == "enabled":
            enabled = True
        elif stripped == "disabled":
            enabled = False
        else:
            g = _GEOMETRY.match(line)
            if g:
                x, y, w, h = (int(v) for v in g.groups())
                rect = Rect(x, y, w, h)
    flush()

    if not outputs:
        raise LayoutError("no enabled outputs found in kscreen-doctor output")
    return Layout.of(outputs)


def parse_spec(spec: str) -> Layout:
    """Parse a hand-written layout, e.g. ``left:0,312,1920x1080;mid:1920,0,3840x2160``.

    Only for testing this project away from the real machine, or for trying a
    layout before rearranging the physical desk.
    """
    outputs: list[Output] = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.fullmatch(r"([^:]+):(-?\d+),(-?\d+),(\d+)x(\d+)", chunk)
        if not m:
            raise LayoutError(f"cannot parse layout chunk: {chunk!r}")
        outputs.append(
            Output(
                name=m.group(1),
                rect=Rect(*(int(v) for v in m.groups()[1:])),
            )
        )
    if not outputs:
        raise LayoutError("empty layout spec")
    return Layout.of(outputs)


def detect_layout(timeout: float = 10.0) -> Layout:
    """Read the live layout, or raise :class:`LayoutError` explaining why not."""
    if shutil.which("kscreen-doctor") is None:
        raise LayoutError(
            "kscreen-doctor not found; it ships with KScreen "
            "(package libkscreen / kscreen on most distributions)"
        )
    try:
        proc = subprocess.run(
            ["kscreen-doctor", "-o"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LayoutError("kscreen-doctor timed out") from exc
    if proc.returncode != 0:
        raise LayoutError(
            f"kscreen-doctor exited {proc.returncode}: {proc.stderr.strip()}"
        )
    return parse_kscreen_doctor(proc.stdout)
