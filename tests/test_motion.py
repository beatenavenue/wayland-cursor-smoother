"""Tests for the animation: path shape, spacing, filtering and timing.

The emit callback and the clock are both injected, so a two-second animation
runs instantly here and can still be checked for what it would have done.
"""

import sys
import unittest
from math import hypot
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.geometry import Direction, Layout, Output, Point, Rect  # noqa: E402
from wcs.motion import (  # noqa: E402
    approach_path,
    glide,
    redirect_path,
    sample_path,
    smoothstep,
)


class FakeClock:
    """Time only passes when somebody sleeps, or when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


class RedirectPathTest(unittest.TestCase):
    def test_horizontal_slides_along_the_edge_before_crossing(self):
        self.assertEqual(
            redirect_path(Point(1920, 256), Point(1917, 514), Direction.LEFT),
            [Point(1920, 256), Point(1920, 514), Point(1917, 514)],
        )

    def test_vertical_slides_along_the_edge_before_crossing(self):
        self.assertEqual(
            redirect_path(Point(100, 499), Point(200, 502), Direction.DOWN),
            [Point(100, 499), Point(200, 499), Point(200, 502)],
        )

    def test_the_corner_is_dropped_when_there_is_no_slide(self):
        # Nothing to slide along: the whole move is the crossing.
        self.assertEqual(
            redirect_path(Point(1920, 514), Point(1917, 514), Direction.LEFT),
            [Point(1920, 514), Point(1917, 514)],
        )

    def test_the_waypoint_order_keeps_the_pointer_off_dead_space(self):
        # Crossing first would put the middle waypoint at (1917,256), which is
        # on neither display. Sliding first keeps it on DP-4 throughout.
        layout = Layout.of(
            [Output("DP-3", Rect(0, 512, 1920, 1080)), Output("DP-4", Rect(1920, 0, 3840, 2160))]
        )
        for point in redirect_path(Point(1920, 256), Point(1917, 514), Direction.LEFT):
            self.assertTrue(layout.covers(point), point)


class ApproachPathTest(unittest.TestCase):
    RECT = Rect(1920, 0, 3840, 2160)

    def test_the_run_up_starts_inside_the_display(self):
        self.assertEqual(
            approach_path(self.RECT, Point(1920, 256), Direction.LEFT, run=500),
            [Point(2420, 256), Point(1920, 256)],
        )

    def test_a_run_longer_than_the_display_is_clamped(self):
        narrow = Rect(0, 0, 100, 100)
        start, end = approach_path(narrow, Point(0, 50), Direction.LEFT, run=5000)
        self.assertEqual(end, Point(0, 50))
        self.assertTrue(narrow.contains(start))

    def test_every_direction_runs_up_from_the_opposite_side(self):
        self.assertEqual(
            approach_path(self.RECT, Point(5759, 46), Direction.RIGHT, run=500)[0],
            Point(5259, 46),
        )
        self.assertEqual(
            approach_path(self.RECT, Point(3000, 0), Direction.UP, run=500)[0],
            Point(3000, 500),
        )
        self.assertEqual(
            approach_path(self.RECT, Point(3000, 2159), Direction.DOWN, run=500)[0],
            Point(3000, 1659),
        )


class SamplePathTest(unittest.TestCase):
    PATH = [Point(0, 0), Point(0, 100), Point(3, 100)]

    def test_it_starts_at_the_start_and_ends_at_the_end(self):
        samples = sample_path(self.PATH, 50)
        self.assertEqual(samples[0], Point(0, 0))
        self.assertEqual(samples[-1], Point(3, 100))

    def test_the_requested_number_of_samples_comes_back(self):
        self.assertEqual(len(sample_path(self.PATH, 37)), 37)

    def test_without_easing_the_spacing_is_even(self):
        straight = [Point(0, 0), Point(0, 100)]
        steps = self.steps_of(sample_path(straight, 21, ease=False))
        self.assertAlmostEqual(min(steps), max(steps), places=6)

    def test_a_step_across_a_corner_is_shorter_but_never_longer(self):
        # Spacing is even in distance travelled along the path. The one step
        # that spans the corner cuts it, so its straight-line length comes out
        # shorter -- which is geometry, not uneven sampling. What must never
        # happen is a step that is longer than the even spacing.
        samples = sample_path(self.PATH, 21, ease=False)
        steps = self.steps_of(samples)
        even = 103.0 / 20.0
        self.assertLessEqual(max(steps), even + 1e-6)
        self.assertEqual(sum(1 for s in steps if s < even - 1e-6), 1)

    @staticmethod
    def steps_of(samples):
        return [hypot(b.x - a.x, b.y - a.y) for a, b in zip(samples, samples[1:])]

    def test_spacing_follows_distance_not_waypoints(self):
        # The 3px leg is 3/103 of the journey, so it must get roughly that
        # share of the samples -- not half of them.
        on_short_leg = [p for p in sample_path(self.PATH, 103, ease=False) if p.x > 0]
        self.assertLess(len(on_short_leg), 10)

    def test_easing_starts_and_ends_slower_than_the_middle(self):
        samples = sample_path(self.PATH, 41)
        steps = [hypot(b.x - a.x, b.y - a.y) for a, b in zip(samples, samples[1:])]
        self.assertLess(steps[0], steps[len(steps) // 2])
        self.assertLess(steps[-1], steps[len(steps) // 2])

    def test_a_zero_length_path_does_not_divide_by_zero(self):
        self.assertEqual(sample_path([Point(5, 5), Point(5, 5)], 4), [Point(5, 5)] * 4)

    def test_degenerate_counts(self):
        self.assertEqual(sample_path(self.PATH, 0), [])
        self.assertEqual(sample_path(self.PATH, 1), [Point(3, 100)])

    def test_smoothstep_is_symmetric_and_bounded(self):
        self.assertEqual(smoothstep(0.0), 0.0)
        self.assertEqual(smoothstep(1.0), 1.0)
        self.assertAlmostEqual(smoothstep(0.5), 0.5)
        self.assertAlmostEqual(smoothstep(0.25) + smoothstep(0.75), 1.0)


class GlideTest(unittest.TestCase):
    PATH = [Point(0, 0), Point(0, 100), Point(3, 100)]

    def test_it_emits_at_the_requested_rate_for_the_requested_time(self):
        clock = FakeClock()
        seen: list[Point] = []
        emitted = glide(seen.append, self.PATH, 2.0, rate=60,
                        sleep=clock.sleep, clock=clock)
        self.assertEqual(emitted, 120)
        self.assertEqual(len(seen), 120)
        self.assertAlmostEqual(clock.now, 2.0, places=6)

    def test_positions_no_display_covers_are_skipped_not_clamped(self):
        # A path through dead space: only the two ends are on a display.
        layout = Layout.of(
            [Output("a", Rect(0, 0, 10, 10)), Output("b", Rect(100, 0, 10, 10))]
        )
        seen: list[Point] = []
        clock = FakeClock()
        emitted = glide(seen.append, [Point(0, 5), Point(109, 5)], 1.0, rate=30,
                        keep=layout.covers, sleep=clock.sleep, clock=clock)
        self.assertLess(emitted, 30)
        self.assertTrue(all(layout.covers(p) for p in seen))
        # The animation still takes its full time; it is sparse, not shorter.
        self.assertAlmostEqual(clock.now, 1.0, places=6)

    def test_a_slow_emit_steals_from_the_next_interval(self):
        clock = FakeClock()

        def slow(_point):
            clock.now += 0.05  # far longer than one 30Hz interval

        glide(slow, self.PATH, 1.0, rate=30, sleep=clock.sleep, clock=clock)
        # Deadlines are absolute, so once emit overruns there is nothing left
        # to sleep off -- the schedule does not accumulate the overrun.
        self.assertEqual([s for s in clock.sleeps if s > 0], [])
        self.assertGreater(clock.now, 1.0)

    def test_at_least_two_samples_even_for_an_absurd_rate(self):
        clock = FakeClock()
        seen: list[Point] = []
        glide(seen.append, self.PATH, 0.001, rate=0.5, sleep=clock.sleep, clock=clock)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
