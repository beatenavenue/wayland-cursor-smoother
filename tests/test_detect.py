"""Tests for the push detector.

The detector decides the one thing that separates this feature from an
annoyance: whether the user is *pushing* at a dead edge or merely standing
there. Every branch below is a way of getting that wrong.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.detect import PushDetector, UndoLatch, outward_component  # noqa: E402
from wcs.geometry import Direction, Point  # noqa: E402


class OutwardComponentTest(unittest.TestCase):
    def test_each_direction_takes_its_own_axis_and_sign(self):
        self.assertEqual(outward_component(-5, 0, Direction.LEFT), 5)
        self.assertEqual(outward_component(5, 0, Direction.RIGHT), 5)
        self.assertEqual(outward_component(0, -5, Direction.UP), 5)
        self.assertEqual(outward_component(0, 5, Direction.DOWN), 5)

    def test_pulling_back_is_negative(self):
        self.assertEqual(outward_component(5, 0, Direction.LEFT), -5)
        self.assertEqual(outward_component(0, 5, Direction.UP), -5)

    def test_motion_along_the_edge_counts_for_nothing(self):
        # Sliding down the left edge of a display is not pushing off it.
        self.assertEqual(outward_component(0, 30, Direction.LEFT), 0)
        self.assertEqual(outward_component(30, 0, Direction.DOWN), 0)

    def test_a_diagonal_contributes_its_outward_part(self):
        self.assertEqual(outward_component(-3, 9, Direction.LEFT), 3)


class PushDetectorTest(unittest.TestCase):
    def setUp(self):
        self.detector = PushDetector(threshold=100.0, window=0.3, cooldown=0.5)

    def push(self, count, amount=-10, at=None, step=0.01, dy=0):
        """Feed ``count`` leftward events, returning the times it fired."""
        fired = []
        now = self.now if at is None else at
        for _ in range(count):
            now += step
            if self.detector.motion(amount, dy, now):
                fired.append(now)
        self.now = now
        return fired

    now = 0.0

    # -- the two failure modes that matter --------------------------------

    def test_an_unarmed_detector_never_fires(self):
        # The pointer is somewhere ordinary; pushing the mouse is just using it.
        self.now = 0.0
        self.assertEqual(self.push(100), [])

    def test_merely_arriving_at_the_edge_does_not_fire(self):
        # Arming is the feed saying "pinned here", not "redirect now".
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.assertEqual(self.push(5), [])
        self.assertLess(self.detector.progress, 1.0)

    # -- the threshold -----------------------------------------------------

    def test_it_fires_once_the_accumulated_push_reaches_the_threshold(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.assertEqual(len(self.push(10)), 1)

    def test_it_fires_on_the_event_that_crosses_and_not_before(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.assertEqual(self.push(9), [])
        self.assertEqual(len(self.push(1)), 1)

    def test_a_single_large_motion_can_cross_on_its_own(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.assertTrue(self.detector.motion(-250, 0, 0.01))

    # -- not firing twice --------------------------------------------------

    def test_a_continuing_push_does_not_fire_again_during_the_cooldown(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.assertEqual(len(self.push(10)), 1)
        self.assertEqual(self.push(30), [])  # 0.3s more, still inside cooldown

    def test_it_fires_again_once_the_cooldown_has_passed(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(10)
        self.now += 1.0
        self.assertEqual(len(self.push(10)), 1)

    # -- pulling back ------------------------------------------------------

    def test_pulling_back_spends_the_accumulated_push(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)                      # 90 outward
        self.push(9, amount=+10)          # 90 back
        self.assertEqual(self.detector.progress, 0.0)
        self.assertEqual(self.push(9), [])

    def test_the_accumulator_never_goes_negative(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(50, amount=+10)         # 500 counts the wrong way
        self.assertEqual(self.detector.progress, 0.0)
        # A full push from here still needs the whole threshold, not less.
        self.assertEqual(self.push(9), [])
        self.assertEqual(len(self.push(1)), 1)

    # -- the time window ---------------------------------------------------

    def test_a_pause_longer_than_the_window_discards_the_push(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)
        self.now += 1.0
        self.assertEqual(self.push(9), [])   # starts again from zero

    def test_a_pause_shorter_than_the_window_keeps_it(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)
        self.now += 0.2
        self.assertEqual(len(self.push(1)), 1)

    def test_sliding_along_the_edge_does_not_keep_a_push_alive(self):
        # Zero outward motion must not refresh the window, or a user tracing
        # the edge would keep a stale push warm indefinitely.
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)
        self.push(40, amount=0, dy=8)     # 0.4s of pure vertical travel
        self.assertEqual(self.push(9), [])

    def test_sliding_along_the_edge_never_fires_by_itself(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.assertEqual(self.push(500, amount=0, dy=50), [])

    # -- arming and disarming ----------------------------------------------

    def test_disarming_discards_the_push(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)
        self.detector.disarm(self.now)
        self.detector.arm(Direction.LEFT, self.now)
        self.assertEqual(self.push(9), [])

    def test_rearming_the_same_direction_is_idempotent(self):
        # The feed sends state changes, but a duplicate must not silently
        # throw away a push the user is halfway through.
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)
        self.detector.arm(Direction.LEFT, self.now)
        self.assertEqual(len(self.push(1)), 1)

    def test_arming_a_different_direction_starts_over(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(9)
        self.detector.arm(Direction.UP, self.now)
        self.assertEqual(self.detector.progress, 0.0)

    def test_armed_reports_the_direction(self):
        self.assertIsNone(self.detector.armed)
        self.detector.arm(Direction.RIGHT, 0.0)
        self.assertIs(self.detector.armed, Direction.RIGHT)
        self.detector.disarm(0.0)
        self.assertIsNone(self.detector.armed)

    # -- the other three directions ----------------------------------------

    def test_every_direction_accumulates_its_own_way(self):
        for direction, dx, dy in (
            (Direction.LEFT, -10, 0),
            (Direction.RIGHT, 10, 0),
            (Direction.UP, 0, -10),
            (Direction.DOWN, 0, 10),
        ):
            detector = PushDetector(threshold=100.0)
            detector.arm(direction, 0.0)
            fired = [detector.motion(dx, dy, 0.01 * n) for n in range(1, 11)]
            self.assertEqual(fired.count(True), 1, direction)

    def test_pushing_the_wrong_way_never_fires(self):
        detector = PushDetector(threshold=100.0)
        detector.arm(Direction.LEFT, 0.0)
        fired = [detector.motion(+10, 0, 0.01 * n) for n in range(1, 200)]
        self.assertNotIn(True, fired)

    # -- progress ----------------------------------------------------------

    def test_progress_tracks_the_accumulator_and_clamps(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(5)
        self.assertAlmostEqual(self.detector.progress, 0.5)
        self.push(4)
        self.assertAlmostEqual(self.detector.progress, 0.9)

    def test_progress_is_zero_again_after_firing(self):
        self.detector.arm(Direction.LEFT, 0.0)
        self.now = 0.0
        self.push(10)
        self.assertEqual(self.detector.progress, 0.0)

    def test_a_zero_threshold_does_not_divide_by_zero(self):
        detector = PushDetector(threshold=0.0)
        detector.arm(Direction.LEFT, 0.0)
        self.assertEqual(detector.progress, 1.0)


class RealisticBatchTest(unittest.TestCase):
    """Against the batch sizes measured on the author's hardware.

    `probe_evdev_access.py --watch` reported roughly 2.8 counts per wakeup
    from the TrackPoint and 4.1 from the mouse, so a push is tens of events,
    not one or two. These check the default threshold is reachable in a
    plausible gesture and not reachable by an accidental twitch.
    """

    def test_a_deliberate_push_fires_within_a_reasonable_gesture(self):
        detector = PushDetector()
        detector.arm(Direction.LEFT, 0.0)
        fired = None
        for n in range(1, 200):
            if detector.motion(-3, 0, n * 0.008):
                fired = n
                break
        self.assertIsNotNone(fired)
        self.assertLess(fired * 0.008, 0.5, "a push should not take half a second")

    def test_a_small_twitch_does_not_fire(self):
        detector = PushDetector()
        detector.arm(Direction.LEFT, 0.0)
        fired = [detector.motion(-3, 1, n * 0.008) for n in range(1, 10)]
        self.assertNotIn(True, fired)


if __name__ == "__main__":
    unittest.main()


class UndoLatchTest(unittest.TestCase):
    """Taking a warp back.

    The requirement is stated in terms of the hand, not the screen: having
    discovered the pointer somewhere it did not expect to be, an equal push
    the other way must put it back. So the tests are about how much motion
    the gesture costs and when it stops being available -- never about where
    the pointer is, which is the thing the user is not watching.
    """

    SOURCE = Point(1920, 2159)

    def setUp(self):
        self.latch = UndoLatch(threshold=100.0, window=0.3, lifetime=3.0)

    def back(self, count, amount=10, step=0.01):
        """Feed ``count`` rightward events; return what the latch returned."""
        out = []
        for _ in range(count):
            self.now += step
            result = self.latch.motion(amount, 0, self.now)
            if result is not None:
                out.append(result)
        return out

    now = 0.0

    def test_an_unarmed_latch_never_fires(self):
        self.now = 0.0
        self.assertEqual(self.back(100), [])

    def test_the_same_push_back_takes_it_back(self):
        # 100 counts bought the redirect; 100 counts buy it back. The
        # symmetry is the design: the gesture is its own inverse.
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.assertEqual(self.back(9), [])
        self.assertEqual(self.back(1), [self.SOURCE])

    def test_it_fires_once(self):
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.assertEqual(len(self.back(30)), 1)
        self.assertFalse(self.latch.armed)

    def test_pushing_the_way_the_redirect_went_is_not_a_correction(self):
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.assertEqual(self.back(20, amount=-10), [])

    def test_a_wobble_outward_drains_the_correction(self):
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.back(5)                      # 50 back
        self.assertEqual(self.back(5, amount=-10), [])   # and 50 outward again
        self.assertEqual(self.back(9), [])               # so 90 is not enough
        self.assertEqual(self.back(1), [self.SOURCE])

    def test_motion_along_the_edge_counts_for_nothing(self):
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        for _ in range(50):
            self.now += 0.01
            self.assertIsNone(self.latch.motion(0, 30, self.now))

    def test_a_pause_longer_than_the_window_forgets_the_correction(self):
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.back(9)
        self.now += 0.5                   # longer than window
        self.assertEqual(self.back(9), [])
        self.assertEqual(self.back(1), [self.SOURCE])

    def test_it_expires(self):
        # A push a minute later is a new intention, not a correction.
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.now = 5.0
        self.assertEqual(self.back(30), [])
        self.assertFalse(self.latch.armed)

    def test_a_click_settles_the_matter(self):
        # Acting at the new position means the user meant to be there.
        self.now = 0.0
        self.latch.arm(self.SOURCE, Direction.LEFT, self.now)
        self.latch.disarm()
        self.assertEqual(self.back(30), [])

    def test_each_direction_has_its_own_way_back(self):
        for direction, dx, dy in ((Direction.LEFT, 10, 0),
                                  (Direction.RIGHT, -10, 0),
                                  (Direction.UP, 0, 10),
                                  (Direction.DOWN, 0, -10)):
            latch = UndoLatch(threshold=100.0, window=0.3, lifetime=3.0)
            latch.arm(self.SOURCE, direction, 0.0)
            results = [latch.motion(dx, dy, 0.01 * i) for i in range(1, 11)]
            self.assertEqual(results[-1], self.SOURCE, direction)
