"""The KWin-side feed: which strips of screen to watch, and the script itself.

The script has to answer one question continuously -- is the pointer at a dead
edge -- and the tempting way to do that is to give it the layout and let it
work the dead bands out. **That would put the semantic this whole project
turns on into two languages at once**, and the two would drift.

So the geometry stays in Python. It computes a table of *watch strips*: thin
rectangles lying along each dead band, one per band. The script is handed that
table as data and does nothing cleverer than a point-in-rectangle test.

The volume falls out of the same choice. While the pointer is nowhere near a
dead edge the script sends nothing at all; while it is inside a strip it
reports every motion, because the redirect's landing point depends on where
along the edge the pointer is *when the push completes*, not where it was when
it arrived. That is the only period where a stream is wanted, and it is short.

Regenerating the script is how a layout change is handled: recompute the
strips, reload. Nothing caches geometry across a rearrangement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .geometry import (
    Direction,
    Layout,
    Output,
    Point,
    Rect,
    dead_bands,
    outputs_beyond,
    redirect_target,
)

#: How many pixels deep a watch strip is. The pointer pinned at an edge sits
#: exactly on it, so one column would do; a little more absorbs rounding, and
#: arming a pixel early costs nothing because arming is not firing.
DEFAULT_STRIP = 2


@dataclass(frozen=True)
class WatchBand:
    """One dead band, as a rectangle the script can test a point against.

    ``to_output`` and ``facing`` say where a push here goes and how much of
    that display's edge touches this one. They stay in Python: the script is
    handed the rectangle and nothing else.
    """

    index: int
    rect: Rect
    output: str
    direction: Direction
    to_output: str
    facing: float


def watch_bands(layout: Layout, *, strip: int = DEFAULT_STRIP,
                min_length: int = 2,
                inset: int = 2,
                max_slide: Optional[float] = None,
                min_facing: float = 0.0) -> list[WatchBand]:
    """Every dead band that has somewhere to redirect to, as a strip.

    Bands with nothing beyond them -- the outer perimeter of the desktop --
    are left out. Watching them would arm the detector where no redirect can
    ever fire, which at best wastes the stream and at worst teaches the user
    that pushing sometimes does nothing. Bands that ``max_slide`` or
    ``min_facing`` refuse are left out for the same reason.
    """
    bands: list[WatchBand] = []
    for direction in Direction:
        for band in dead_bands(layout, direction, min_length=min_length):
            source = next(o for o in layout.outputs if o.name == band.output)
            for start, end in _runs_by_target(layout, source, band.start,
                                              band.end, direction, inset):
                probe = _band_probe(source.rect, start, end, direction)
                redirect = redirect_target(layout, probe, direction, inset=inset,
                                           max_slide=max_slide,
                                           min_facing=min_facing)
                if redirect is None:
                    continue
                bands.append(
                    WatchBand(
                        index=len(bands),
                        rect=_strip_rect(source.rect, start, end, direction, strip),
                        output=band.output,
                        direction=direction,
                        to_output=redirect.to_output,
                        facing=redirect.facing,
                    )
                )
    return bands


def _runs_by_target(layout: Layout, source: Output, start: int, end: int,
                    direction: Direction, inset: int) -> list[tuple[int, int]]:
    """Split a dead band wherever the display it redirects to changes.

    Nearly every band has one display beyond it and comes back whole. With
    two or more, the nearest one can change partway along the band. Each
    part then has its own facing ratio, and ``min_facing`` may keep one part
    and refuse the other, so each part needs its own strip. Probing only the
    middle of the whole band would get one of the parts wrong.
    """
    if len(outputs_beyond(layout, source, direction)) < 2:
        return [(start, end)]
    runs: list[list] = []
    for at in range(start, end):
        probe = _band_probe(source.rect, at, at + 1, direction)
        redirect = redirect_target(layout, probe, direction, inset=inset)
        to = redirect.to_output if redirect else None
        if runs and runs[-1][2] == to:
            runs[-1][1] = at + 1
        else:
            runs.append([at, at + 1, to])
    return [(run_start, run_end) for run_start, run_end, _ in runs]


def _band_probe(rect: Rect, start: int, end: int, direction: Direction) -> Point:
    """A point in the middle of the band, for asking "does this redirect?"."""
    middle = (start + end - 1) / 2
    if direction is Direction.LEFT:
        return Point(rect.left, middle)
    if direction is Direction.RIGHT:
        return Point(rect.max_x, middle)
    if direction is Direction.UP:
        return Point(middle, rect.top)
    return Point(middle, rect.max_y)


def _strip_rect(rect: Rect, start: int, end: int,
                direction: Direction, strip: int) -> Rect:
    depth = max(1, strip)
    if direction is Direction.LEFT:
        return Rect(rect.left, start, depth, end - start)
    if direction is Direction.RIGHT:
        return Rect(rect.right - depth, start, depth, end - start)
    if direction is Direction.UP:
        return Rect(start, rect.top, end - start, depth)
    return Rect(start, rect.bottom - depth, end - start, depth)


def bands_as_json(bands: Iterable[WatchBand]) -> str:
    return json.dumps(
        [
            {"i": b.index, "x": b.rect.x, "y": b.rect.y,
             "w": b.rect.width, "h": b.rect.height}
            for b in bands
        ],
        separators=(",", ":"),
    )


def feed_script(bands: Sequence[WatchBand], *, bus_name: str, object_path: str,
                interface: str, marker: str = "wcs", trace: bool = False) -> str:
    """The KWin JS script that reports when the pointer is on a watch strip.

    It calls ``Edge(index, x, y)`` on entering a strip, on every motion while
    inside one, and once with ``-1`` on leaving. Outside every strip it is
    silent, which is nearly all of the time.

    ``trace`` makes it say what it sees -- the position it read and the band
    it computed -- to the compositor's journal. Written because the first run
    produced a script that loaded, ran, logged no error and called nothing,
    which is a silence no amount of reading the code resolves: either the
    rectangle test disagrees with the position, or the call fails. Tracing
    asks the script instead of guessing.

    The call is wrapped in a try/catch in both modes. A `callDBus` that
    throws was, until this was written, indistinguishable from one that was
    never reached.
    """
    trace_line = (
        "    ticks += 1;\n"
        "    if (band >= 0 || ticks % 25 === 0) {\n"
        "        print(__MARK__ + ' sees ' + p.x + ',' + p.y + ' -> band ' + band);\n"
        "    }\n"
        if trace else ""
    )
    return (
        "// wayland-cursor-smoother feed -- generated; edits will be overwritten.\n"
        "//\n"
        "// The strips below were computed from the live layout by the daemon.\n"
        "// This script deliberately contains no knowledge of what a dead band\n"
        "// is: duplicating that here would mean two implementations of the one\n"
        "// thing the project has to get right.\n"
        "\n"
        "var BANDS = __BANDS__;\n"
        "var current = -1;\n"
        "\n"
        "function bandAt(p) {\n"
        "    for (var i = 0; i < BANDS.length; i++) {\n"
        "        var b = BANDS[i];\n"
        "        if (p.x >= b.x && p.x < b.x + b.w &&\n"
        "            p.y >= b.y && p.y < b.y + b.h) {\n"
        "            return b.i;\n"
        "        }\n"
        "    }\n"
        "    return -1;\n"
        "}\n"
        "\n"
        "var ticks = 0;\n"
        "\n"
        "function onMoved() {\n"
        "    var p = workspace.cursorPos;\n"
        "    var band = bandAt(p);\n"
        "__TRACE__"
        "    if (band < 0 && current < 0) {\n"
        "        return;  // the common case: nowhere near an edge, say nothing\n"
        "    }\n"
        "    current = band;\n"
        "    try {\n"
        "        callDBus(__SERVICE__, __OBJECT__, __IFACE__,\n"
        "                 'Edge', band, p.x, p.y);\n"
        "    } catch (e) {\n"
        "        print(__MARK__ + ' callDBus threw: ' + e);\n"
        "    }\n"
        "}\n"
        "\n"
        "workspace.cursorPosChanged.connect(onMoved);\n"
        "print(__MARK__ + ' feed watching ' + BANDS.length + ' band(s)');\n"
        "onMoved();\n"
    ).replace("__TRACE__", trace_line) \
     .replace("__BANDS__", bands_as_json(bands)) \
     .replace("__SERVICE__", json.dumps(bus_name)) \
     .replace("__OBJECT__", json.dumps(object_path)) \
     .replace("__IFACE__", json.dumps(interface)) \
     .replace("__MARK__", json.dumps(marker))
