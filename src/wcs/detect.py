"""Deciding when the user is *pushing* against a dead edge.

The pointer being at a dead edge is not enough to act on. Reaching for
something at the left of a display puts the pointer there too, and redirecting
on contact would fling it to another screen every time -- worse than the
problem this project exists to fix. What separates the two is that the user
keeps pushing after the pointer has stopped moving, and the compositor cannot
report that: a pinned pointer emits no position changes.

So the signal comes from the physical device instead. While the feed says
"pinned at this edge", outward travel from the device is accumulated, and a
redirect fires once it passes a threshold. That is the same shape as KWin's
own edge barrier, which accumulates movement into a barrier before letting the
pointer through -- and which users of this desktop are already calibrated to.

Units are **device counts, not pixels.** Pointer acceleration sits between the
two, so there is no fixed conversion; the threshold is a distance the hand
travels, which is the thing being measured anyway. Measured on the author's
hardware, motion arrives in batches of roughly 3-4 counts, so a threshold in
the low hundreds is tens of events rather than one or two.

Nothing here does any I/O, so all of it is unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .geometry import Direction

#: Outward device counts that constitute a push. KWin's own EdgeBarrier
#: defaults to 100 (of pixels, at a different layer), and starting at the same
#: number keeps the feel familiar rather than inventing a new one.
DEFAULT_THRESHOLD = 100.0

#: Seconds without outward motion after which the accumulator resets. Long
#: enough to survive the gap between batches, short enough that two unrelated
#: nudges a second apart do not add up into a redirect.
DEFAULT_WINDOW = 0.3

#: Seconds after firing during which nothing else fires. The feed will disarm
#: us once the pointer has moved, but the warp is not instantaneous and events
#: keep arriving while it happens.
DEFAULT_COOLDOWN = 0.5


def outward_component(dx: float, dy: float, direction: Direction) -> float:
    """How much of a motion pushes *outward* in ``direction``.

    Negative when the user is pulling back. Motion purely along the edge
    yields zero, which is the correct reading: sliding down the left edge of a
    display is not pushing off it.
    """
    if direction is Direction.LEFT:
        return -dx
    if direction is Direction.RIGHT:
        return dx
    if direction is Direction.UP:
        return -dy
    return dy


@dataclass
class PushDetector:
    """Accumulates outward push while armed, and says when it is enough."""

    threshold: float = DEFAULT_THRESHOLD
    window: float = DEFAULT_WINDOW
    cooldown: float = DEFAULT_COOLDOWN

    _direction: Optional[Direction] = field(default=None, init=False)
    _accumulated: float = field(default=0.0, init=False)
    _last_outward_at: float = field(default=float("-inf"), init=False)
    _cooldown_until: float = field(default=float("-inf"), init=False)

    # -- state from the feed ----------------------------------------------

    def arm(self, direction: Direction, now: float) -> None:
        """The pointer is pinned at a dead edge, pushing ``direction``."""
        if direction is not self._direction:
            self._direction = direction
            self._reset(now)

    def disarm(self, now: float) -> None:
        """The pointer is no longer at a dead edge."""
        self._direction = None
        self._reset(now)

    def _reset(self, now: float) -> None:
        self._accumulated = 0.0
        self._last_outward_at = now

    # -- state from the device --------------------------------------------

    def motion(self, dx: float, dy: float, now: float) -> bool:
        """Feed one motion event. ``True`` means redirect, once.

        Returns ``True`` exactly on the event that crosses the threshold; the
        accumulator is cleared and a cooldown started, so a continuing push
        does not fire again immediately.
        """
        if self._direction is None or now < self._cooldown_until:
            return False

        outward = outward_component(dx, dy, self._direction)
        if outward == 0.0:
            # Motion purely along the edge. It neither counts towards a push
            # nor keeps an existing one alive -- a pause spent sliding along
            # the edge should let the accumulator lapse like any other pause.
            return False

        if now - self._last_outward_at > self.window:
            self._accumulated = 0.0
        self._last_outward_at = now
        self._accumulated = max(0.0, self._accumulated + outward)

        if self._accumulated < self.threshold:
            return False
        self._accumulated = 0.0
        self._cooldown_until = now + self.cooldown
        return True

    # -- for diagnostics ---------------------------------------------------

    @property
    def armed(self) -> Optional[Direction]:
        return self._direction

    @property
    def progress(self) -> float:
        """How far towards firing, 0..1. Only useful for showing the user."""
        if self.threshold <= 0:
            return 1.0
        return min(1.0, self._accumulated / self.threshold)
