"""Turning a redirect into motion the eye can follow.

A warp is instantaneous by nature, and on a large multi-display desk that
makes it impossible to check by looking: the pointer is simply somewhere else,
and finding it again means hunting across three screens.  Emitting a stream of
absolute positions instead produces real, continuous motion.

The path is not decoration.  A redirect means "slide along this edge to the
nearest point that has a neighbour, then cross" -- so the waypoints are that
sentence, drawn.  Interpolating straight from the pointer to the landing point
would instead cut the corner through dead space, where no display exists; KWin
resolves such a position against the nearest output and clamps it, so the
pointer would scrabble along an edge rather than travel. ``glide`` therefore
drops any sample that no display covers.

Everything above the emit callback is pure and unit tested, including the
timing schedule: ``glide`` takes its clock and its sleep, so a test can run a
two-second animation instantly and check what it would have done.
"""

from __future__ import annotations

import time
from math import hypot
from typing import Callable, Iterable, Optional, Sequence

from .geometry import Direction, Point, Rect

#: Absolute positions per second. A physical mouse reports at 125Hz or more;
#: matching that order of magnitude is what makes the motion read as motion
#: rather than as a sequence of jumps.
DEFAULT_RATE = 120.0


def smoothstep(t: float) -> float:
    """Ease in and out. Constant speed starts and stops too abruptly to track."""
    return t * t * (3.0 - 2.0 * t)


def redirect_path(source: Point, target: Point, direction: Direction) -> list[Point]:
    """Waypoints from the dead edge to the landing point.

    Slide along the edge first, cross second.  The pointer stays on the
    display it started from until the last moment, which is both what the
    feature means and the only order that keeps every waypoint on a display.
    """
    corner = (
        Point(source.x, target.y) if direction.horizontal else Point(target.x, source.y)
    )
    points = [source]
    for candidate in (corner, target):
        if candidate != points[-1]:
            points.append(candidate)
    return points


def approach_path(rect: Rect, edge_point: Point, direction: Direction,
                  run: int = 500) -> list[Point]:
    """A run-up towards the dead edge, starting ``run`` px inside the display.

    Without it the demonstration begins with the pointer already parked, and
    "it stopped at the edge" is the half of the behaviour that needs showing
    just as much as the redirect does.
    """
    if direction is Direction.LEFT:
        start = Point(min(edge_point.x + run, rect.max_x), edge_point.y)
    elif direction is Direction.RIGHT:
        start = Point(max(edge_point.x - run, rect.left), edge_point.y)
    elif direction is Direction.UP:
        start = Point(edge_point.x, min(edge_point.y + run, rect.max_y))
    else:
        start = Point(edge_point.x, max(edge_point.y - run, rect.top))
    return [start, edge_point] if start != edge_point else [edge_point]


def sample_path(points: Sequence[Point], count: int, *, ease: bool = True) -> list[Point]:
    """``count`` positions along a polyline, spaced by distance travelled.

    Spacing by arc length rather than by waypoint is what keeps the speed
    even: a 258px slide followed by a 3px step would otherwise spend half the
    animation on the 3px.
    """
    if count <= 0:
        return []
    if count == 1 or len(points) < 2:
        return [points[-1]]

    spans = [
        (a, b, hypot(b.x - a.x, b.y - a.y))
        for a, b in zip(points, points[1:])
    ]
    spans = [s for s in spans if s[2] > 0.0]
    total = sum(span[2] for span in spans)
    if total == 0.0:
        return [points[-1]] * count

    out: list[Point] = []
    index = 0
    walked = 0.0
    for step in range(count):
        t = step / (count - 1)
        distance = (smoothstep(t) if ease else t) * total
        while index < len(spans) - 1 and walked + spans[index][2] < distance:
            walked += spans[index][2]
            index += 1
        a, b, length = spans[index]
        local = min(max((distance - walked) / length, 0.0), 1.0)
        out.append(Point(a.x + (b.x - a.x) * local, a.y + (b.y - a.y) * local))
    return out


def glide(
    emit: Callable[[Point], None],
    points: Sequence[Point],
    seconds: float,
    *,
    rate: float = DEFAULT_RATE,
    ease: bool = True,
    keep: Optional[Callable[[Point], bool]] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    """Animate along ``points`` over ``seconds``; return how many were emitted.

    Sleeps are computed against a deadline rather than accumulated, so a slow
    ``emit`` steals from the next interval instead of stretching the whole
    animation.  ``keep`` filters positions no display covers: skipping them
    leaves a gap in an impossible stretch, which is preferable to asking the
    compositor to clamp a position that is nowhere.
    """
    count = max(2, int(round(seconds * rate)))
    samples = sample_path(points, count, ease=ease)
    started = clock()
    emitted = 0
    for step, point in enumerate(samples):
        due = started + seconds * step / (len(samples) - 1)
        delay = due - clock()
        if delay > 0:
            sleep(delay)
        if keep is not None and not keep(point):
            continue
        emit(point)
        emitted += 1
    return emitted
