"""Display layout geometry and the landing semantics of a redirect.

This module is pure: it knows nothing about KWin, uinput or D-Bus, and it has
no side effects.  Everything here is unit tested, because getting the landing
point wrong makes the whole project useless (see "Goal" in CLAUDE.md): the
pointer must arrive at the nearest valid point, preserving its position along
the edge it left, never at the centre of the target display.

Coordinates are KWin's *logical* global coordinates: the whole-layout space
that ``workspace()->geometry()`` spans, not per-display coordinates and not
physical pixels.

Rectangle convention: ``right``/``bottom`` are exclusive bounds, while
``max_x``/``max_y`` are the last coordinates a pointer may actually occupy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, NamedTuple, Optional, Sequence


class Point(NamedTuple):
    x: float
    y: float


class Direction(Enum):
    """The direction the user is pushing the pointer in."""

    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"

    @property
    def horizontal(self) -> bool:
        return self in (Direction.LEFT, Direction.RIGHT)

    @property
    def opposite(self) -> "Direction":
        return {
            Direction.LEFT: Direction.RIGHT,
            Direction.RIGHT: Direction.LEFT,
            Direction.UP: Direction.DOWN,
            Direction.DOWN: Direction.UP,
        }[self]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        """Exclusive right bound."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Exclusive bottom bound."""
        return self.y + self.height

    @property
    def max_x(self) -> int:
        """Last column a pointer may occupy."""
        return self.x + self.width - 1

    @property
    def max_y(self) -> int:
        """Last row a pointer may occupy."""
        return self.y + self.height - 1

    def contains(self, p: Point) -> bool:
        return self.left <= p.x < self.right and self.top <= p.y < self.bottom

    def clamp(self, p: Point) -> Point:
        """The valid pointer position inside this rect nearest to ``p``."""
        return Point(
            min(max(p.x, self.left), self.max_x),
            min(max(p.y, self.top), self.max_y),
        )

    def inset(self, margin: int) -> "Rect":
        """Shrink by ``margin`` on every side, never past a 1x1 centre.

        A landing point exactly on an output's outer edge is the riskiest
        place to aim for: one pixel of rounding either way and it belongs to
        a different output, or to no output at all.  Aiming slightly inside
        costs nothing perceptually and removes that class of bug.
        """
        if margin <= 0:
            return self
        mx = min(margin, max(0, (self.width - 1) // 2))
        my = min(margin, max(0, (self.height - 1) // 2))
        return Rect(self.x + mx, self.y + my, self.width - 2 * mx, self.height - 2 * my)


@dataclass(frozen=True)
class Output:
    name: str
    rect: Rect


@dataclass(frozen=True)
class Layout:
    outputs: tuple[Output, ...]

    @classmethod
    def of(cls, outputs: Iterable[Output]) -> "Layout":
        return cls(tuple(outputs))

    def output_at(self, p: Point) -> Optional[Output]:
        for o in self.outputs:
            if o.rect.contains(p):
                return o
        return None

    def covers(self, p: Point) -> bool:
        return self.output_at(p) is not None

    @property
    def bounding_box(self) -> Rect:
        if not self.outputs:
            raise ValueError("empty layout has no bounding box")
        left = min(o.rect.left for o in self.outputs)
        top = min(o.rect.top for o in self.outputs)
        right = max(o.rect.right for o in self.outputs)
        bottom = max(o.rect.bottom for o in self.outputs)
        return Rect(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class DeadBand:
    """A stretch of an output edge with no display on the other side.

    ``start``/``end`` are along the edge: y coordinates for a left or right
    edge, x coordinates for a top or bottom edge.  ``end`` is exclusive.
    """

    output: str
    direction: Direction
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def midpoint(self) -> float:
        return (self.start + self.end - 1) / 2


@dataclass(frozen=True)
class Redirect:
    """Where the pointer should be put, and what it cost to get there."""

    target: Point
    to_output: str
    slide: float
    """Distance moved *along* the edge. This is the part the user perceives
    as the pointer sliding; it is the quantity the policy minimises."""
    gap: float
    """Distance moved across the edge, i.e. the width of the jump."""


def _probe_point(rect: Rect, p: Point, direction: Direction) -> Point:
    """The position just past ``rect``'s edge in ``direction``."""
    if direction is Direction.LEFT:
        return Point(rect.left - 1, p.y)
    if direction is Direction.RIGHT:
        return Point(rect.right, p.y)
    if direction is Direction.UP:
        return Point(p.x, rect.top - 1)
    return Point(p.x, rect.bottom)


def _is_beyond(candidate: Rect, source: Rect, direction: Direction) -> bool:
    """True when ``candidate`` lies wholly past ``source``'s edge."""
    if direction is Direction.LEFT:
        return candidate.right <= source.left
    if direction is Direction.RIGHT:
        return candidate.left >= source.right
    if direction is Direction.UP:
        return candidate.bottom <= source.top
    return candidate.top >= source.bottom


def _at_edge(rect: Rect, p: Point, direction: Direction, tol: float) -> bool:
    if direction is Direction.LEFT:
        return p.x <= rect.left + tol
    if direction is Direction.RIGHT:
        return p.x >= rect.max_x - tol
    if direction is Direction.UP:
        return p.y <= rect.top + tol
    return p.y >= rect.max_y - tol


def _along(p: Point, direction: Direction) -> float:
    return p.y if direction.horizontal else p.x


def _across(p: Point, direction: Direction) -> float:
    return p.x if direction.horizontal else p.y


def _interval_subtract(
    base: tuple[int, int], holes: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """``base`` minus the union of ``holes``; all half-open, returned sorted."""
    start, end = base
    remaining = [(start, end)] if start < end else []
    for h_start, h_end in sorted(holes):
        if h_start >= h_end:
            continue
        next_remaining: list[tuple[int, int]] = []
        for r_start, r_end in remaining:
            if h_end <= r_start or h_start >= r_end:
                next_remaining.append((r_start, r_end))
                continue
            if r_start < h_start:
                next_remaining.append((r_start, h_start))
            if h_end < r_end:
                next_remaining.append((h_end, r_end))
        remaining = next_remaining
    return remaining


def dead_bands(
    layout: Layout, direction: Direction, *, min_length: int = 1
) -> list[DeadBand]:
    """Every stretch of every output edge that no other display backs.

    This is the geometric statement of the problem: within one of these
    stretches the pointer has no destination, so no amount of pushing will
    move it.  Compare the result against ``img/motivation.png`` -- on the
    author's layout it should name exactly the marked bands.
    """
    bands: list[DeadBand] = []
    for o in layout.outputs:
        r = o.rect
        if direction.horizontal:
            base = (r.top, r.bottom)
        else:
            base = (r.left, r.right)

        holes: list[tuple[int, int]] = []
        for c in layout.outputs:
            if c is o:
                continue
            cr = c.rect
            if direction is Direction.LEFT:
                if cr.left <= r.left - 1 < cr.right:
                    holes.append((cr.top, cr.bottom))
            elif direction is Direction.RIGHT:
                if cr.left <= r.right < cr.right:
                    holes.append((cr.top, cr.bottom))
            elif direction is Direction.UP:
                if cr.top <= r.top - 1 < cr.bottom:
                    holes.append((cr.left, cr.right))
            else:
                if cr.top <= r.bottom < cr.bottom:
                    holes.append((cr.left, cr.right))

        for start, end in _interval_subtract(base, holes):
            if end - start >= min_length:
                bands.append(DeadBand(o.name, direction, start, end))
    return bands


def redirect_target(
    layout: Layout,
    p: Point,
    direction: Direction,
    *,
    inset: int = 2,
    max_slide: Optional[float] = None,
    edge_tol: float = 1.0,
) -> Optional[Redirect]:
    """Where to put the pointer, or ``None`` to leave it alone.

    ``None`` is returned -- meaning "this is not our business, let KWin do
    what it normally does" -- when the pointer is not against the edge it is
    being pushed at, when a display already backs that stretch of edge, when
    nothing lies beyond it at all, or when the only candidate is further
    along the edge than ``max_slide`` allows.

    Among the displays that do lie beyond the edge, the one needing the
    smallest slide wins, ties broken by the smaller jump.  That ordering *is*
    the Windows semantic: position along the edge is what must be preserved.
    """
    source = layout.output_at(p)
    if source is None:
        return None
    if not _at_edge(source.rect, p, direction, edge_tol):
        return None
    if layout.covers(_probe_point(source.rect, p, direction)):
        # A display already backs this stretch. Normal crossing; not ours.
        return None

    best: Optional[Redirect] = None
    for c in layout.outputs:
        if c is source or not _is_beyond(c.rect, source.rect, direction):
            continue
        target = c.rect.inset(inset).clamp(p)
        slide = abs(_along(target, direction) - _along(p, direction))
        gap = abs(_across(target, direction) - _across(p, direction))
        if max_slide is not None and slide > max_slide:
            continue
        if best is None or (slide, gap) < (best.slide, best.gap):
            best = Redirect(target=target, to_output=c.name, slide=slide, gap=gap)
    return best
