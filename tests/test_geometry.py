"""Tests for the landing semantics.

The layout used throughout mirrors img/motivation.png: a 4K centre display, a
landscape 1920x1080 to its left sitting lower than the centre's top edge and
higher than its bottom edge, and a portrait 1080x1920 to its right whose
bottom edge stops short of the centre's.  Those three mismatches are exactly
the three bands marked in red in that image.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wcs.geometry import (  # noqa: E402
    DeadBand,
    Direction,
    Layout,
    Output,
    Point,
    Rect,
    _interval_subtract,
    dead_bands,
    facing_ratio,
    redirect_target,
)

LEFT_MON = Output("DP-1", Rect(0, 400, 1920, 1080))       # y 400 .. 1479
CENTRE = Output("DP-2", Rect(1920, 0, 3840, 2160))        # y 0 .. 2159
RIGHT_MON = Output("DP-3", Rect(5760, -200, 1080, 1920))  # y -200 .. 1719
MOTIVATION = Layout.of([LEFT_MON, CENTRE, RIGHT_MON])


class IntervalSubtractTest(unittest.TestCase):
    def test_no_holes(self):
        self.assertEqual(_interval_subtract((0, 10), []), [(0, 10)])

    def test_hole_in_the_middle_splits(self):
        self.assertEqual(_interval_subtract((0, 10), [(4, 6)]), [(0, 4), (6, 10)])

    def test_fully_covered(self):
        self.assertEqual(_interval_subtract((0, 10), [(-5, 15)]), [])

    def test_overlapping_holes_merge(self):
        self.assertEqual(_interval_subtract((0, 10), [(2, 5), (4, 8)]), [(0, 2), (8, 10)])

    def test_empty_holes_ignored(self):
        self.assertEqual(_interval_subtract((0, 10), [(5, 5)]), [(0, 10)])


class RectTest(unittest.TestCase):
    def test_exclusive_and_inclusive_bounds_differ_by_one(self):
        r = Rect(10, 20, 100, 50)
        self.assertEqual((r.right, r.max_x), (110, 109))
        self.assertEqual((r.bottom, r.max_y), (70, 69))

    def test_contains_excludes_the_far_edge(self):
        r = Rect(0, 0, 10, 10)
        self.assertTrue(r.contains(Point(9, 9)))
        self.assertFalse(r.contains(Point(10, 9)))

    def test_clamp_lands_on_the_last_valid_pixel(self):
        r = Rect(0, 0, 10, 10)
        self.assertEqual(r.clamp(Point(99, -99)), Point(9, 0))

    def test_inset_shrinks_all_sides(self):
        self.assertEqual(Rect(0, 0, 100, 100).inset(2), Rect(2, 2, 96, 96))

    def test_inset_never_collapses_a_thin_rect(self):
        thin = Rect(0, 0, 3, 1)
        got = thin.inset(2)
        self.assertGreaterEqual(got.width, 1)
        self.assertGreaterEqual(got.height, 1)
        self.assertTrue(thin.contains(got.clamp(Point(0, 0))))


class DeadBandTest(unittest.TestCase):
    def test_motivation_layout_has_exactly_the_three_marked_bands(self):
        left = dead_bands(MOTIVATION, Direction.LEFT)
        right = dead_bands(MOTIVATION, Direction.RIGHT)
        centre_left = [b for b in left if b.output == CENTRE.name]
        centre_right = [b for b in right if b.output == CENTRE.name]

        self.assertEqual(
            centre_left,
            [
                DeadBand(CENTRE.name, Direction.LEFT, 0, 400),
                DeadBand(CENTRE.name, Direction.LEFT, 1480, 2160),
            ],
        )
        self.assertEqual(
            centre_right,
            [DeadBand(CENTRE.name, Direction.RIGHT, 1720, 2160)],
        )

    def test_an_edge_fully_backed_by_a_neighbour_has_no_band(self):
        # The left monitor's right edge is entirely inside the centre display.
        bands = [b for b in dead_bands(MOTIVATION, Direction.RIGHT) if b.output == LEFT_MON.name]
        self.assertEqual(bands, [])

    def test_the_outer_edge_of_the_layout_is_one_long_band(self):
        bands = [b for b in dead_bands(MOTIVATION, Direction.LEFT) if b.output == LEFT_MON.name]
        self.assertEqual(bands, [DeadBand(LEFT_MON.name, Direction.LEFT, 400, 1480)])

    def test_min_length_filters_slivers(self):
        # "a" is 2px taller than its right-hand neighbour, leaving a 2px band.
        sliver = Layout.of([Output("a", Rect(0, 0, 100, 100)), Output("b", Rect(100, 0, 100, 98))])

        def bands_of_a(min_length):
            return [
                b
                for b in dead_bands(sliver, Direction.RIGHT, min_length=min_length)
                if b.output == "a"
            ]

        self.assertEqual(bands_of_a(1), [DeadBand("a", Direction.RIGHT, 98, 100)])
        self.assertEqual(bands_of_a(10), [])


class RedirectTest(unittest.TestCase):
    def test_upper_dead_band_slides_down_to_the_left_monitors_top(self):
        r = redirect_target(MOTIVATION, Point(1920, 100), Direction.LEFT, inset=2)
        self.assertIsNotNone(r)
        self.assertEqual(r.to_output, LEFT_MON.name)
        # Just inside the left monitor's right edge, at its topmost usable row.
        self.assertEqual(r.target, Point(1917, 402))
        self.assertEqual(r.slide, 302)

    def test_lower_dead_band_slides_up_to_the_left_monitors_bottom(self):
        r = redirect_target(MOTIVATION, Point(1920, 2000), Direction.LEFT, inset=2)
        self.assertEqual(r.to_output, LEFT_MON.name)
        self.assertEqual(r.target, Point(1917, 1477))

    def test_right_dead_band_slides_up_to_the_portrait_monitors_bottom(self):
        r = redirect_target(MOTIVATION, Point(5759, 2000), Direction.RIGHT, inset=2)
        self.assertEqual(r.to_output, RIGHT_MON.name)
        self.assertEqual(r.target, Point(5762, 1717))

    def test_the_landing_point_is_never_the_centre_of_the_target(self):
        target_centre = Point(
            LEFT_MON.rect.x + LEFT_MON.rect.width / 2,
            LEFT_MON.rect.y + LEFT_MON.rect.height / 2,
        )
        for y in (0, 100, 399, 1480, 1900, 2159):
            r = redirect_target(MOTIVATION, Point(1920, y), Direction.LEFT)
            if r is not None:
                self.assertNotEqual(r.target, target_centre)

    def test_the_landing_point_is_always_inside_a_real_output(self):
        for y in range(0, 2160, 7):
            r = redirect_target(MOTIVATION, Point(1920, y), Direction.LEFT)
            if r is not None:
                self.assertTrue(MOTIVATION.covers(r.target), f"y={y} -> {r.target}")

    def test_slide_is_monotonic_in_distance_from_the_live_edge(self):
        # Further into the dead band means a longer slide, never a shorter one.
        previous = -1.0
        for y in range(399, -1, -1):
            r = redirect_target(MOTIVATION, Point(1920, y), Direction.LEFT)
            self.assertIsNotNone(r)
            self.assertGreaterEqual(r.slide, previous)
            previous = r.slide

    def test_no_redirect_where_a_neighbour_already_exists(self):
        self.assertIsNone(redirect_target(MOTIVATION, Point(1920, 800), Direction.LEFT))

    def test_no_redirect_when_the_pointer_is_not_at_the_edge(self):
        self.assertIsNone(redirect_target(MOTIVATION, Point(2500, 100), Direction.LEFT))

    def test_no_redirect_when_nothing_lies_beyond_the_edge(self):
        self.assertIsNone(redirect_target(MOTIVATION, Point(0, 800), Direction.LEFT))

    def test_no_redirect_from_outside_any_output(self):
        self.assertIsNone(redirect_target(MOTIVATION, Point(500, 50), Direction.LEFT))

    def test_pushing_at_an_edge_with_no_band_does_nothing(self):
        # Left monitor's right edge is fully backed, so pushing right is normal.
        self.assertIsNone(redirect_target(MOTIVATION, Point(1919, 800), Direction.RIGHT))

    def test_max_slide_suppresses_a_long_jump(self):
        self.assertIsNone(
            redirect_target(MOTIVATION, Point(1920, 100), Direction.LEFT, max_slide=100)
        )
        self.assertIsNotNone(
            redirect_target(MOTIVATION, Point(1920, 100), Direction.LEFT, max_slide=400)
        )

    def test_the_smallest_slide_wins_over_the_smallest_gap(self):
        # "near" is closer across the edge but needs a big slide; "far" is
        # further away but sits at the pointer's own height.
        src = Output("src", Rect(1000, 0, 1000, 1000))
        near = Output("near", Rect(900, 0, 100, 100))     # y 0..99,  gap 0
        far = Output("far", Rect(0, 400, 100, 200))       # y 400..599, gap 900
        layout = Layout.of([src, near, far])
        r = redirect_target(layout, Point(1000, 500), Direction.LEFT, inset=0)
        self.assertEqual(r.to_output, "far")
        self.assertEqual(r.slide, 0)

    def test_vertical_redirect_preserves_x(self):
        top = Output("top", Rect(0, 0, 500, 500))
        bottom = Output("bottom", Rect(200, 500, 1000, 500))
        layout = Layout.of([top, bottom])
        # x=100 on the top display's bottom edge has nothing below it.
        r = redirect_target(layout, Point(100, 499), Direction.DOWN, inset=0)
        self.assertEqual(r.to_output, "bottom")
        self.assertEqual(r.target, Point(200, 500))
        self.assertEqual(r.slide, 100)
        # x=300 does have a display below, so it is a normal crossing.
        self.assertIsNone(redirect_target(layout, Point(300, 499), Direction.DOWN))


if __name__ == "__main__":
    unittest.main()


class FacingRatioTest(unittest.TestCase):
    """The facing ratio figure, img/facing_ratio_20_vs_80.svg, in numbers."""

    TARGET = Rect(0, 0, 90, 150)

    def test_the_figures_left_example_is_20_percent(self):
        current = Rect(90, 120, 120, 150)   # touches rows 120..149 of 150
        self.assertEqual(facing_ratio(current, self.TARGET, Direction.LEFT), 20.0)

    def test_the_figures_right_example_is_80_percent(self):
        current = Rect(90, 30, 120, 150)    # touches rows 30..149 of 150
        self.assertEqual(facing_ratio(current, self.TARGET, Direction.LEFT), 80.0)

    def test_it_is_measured_on_the_targets_edge_so_it_depends_on_direction(self):
        # The laptop and monitor from img/desk-setup.svg: 48 rows touch, out
        # of the monitor's 186 and the laptop's 130.
        laptop = Rect(50, 180, 190, 130)
        monitor = Rect(240, 42, 296, 186)
        self.assertAlmostEqual(facing_ratio(laptop, monitor, Direction.RIGHT),
                               100 * 48 / 186)
        self.assertAlmostEqual(facing_ratio(monitor, laptop, Direction.LEFT),
                               100 * 48 / 130)

    def test_a_display_that_does_not_touch_is_0(self):
        gap = Rect(80, 0, 100, 150)         # ten columns short of the target
        self.assertEqual(facing_ratio(Rect(100, 0, 100, 150), gap, Direction.LEFT), 0.0)
        self.assertEqual(facing_ratio(Rect(200, 0, 100, 150), self.TARGET,
                                      Direction.LEFT), 0.0)

    def test_a_neighbour_that_overhangs_the_source_loses_the_overhang(self):
        # The left monitor lies wholly within the centre's height. The right
        # one starts 200 rows above it, so 1720 of its 1920 rows touch.
        self.assertEqual(facing_ratio(CENTRE.rect, LEFT_MON.rect, Direction.LEFT), 100.0)
        self.assertAlmostEqual(facing_ratio(CENTRE.rect, RIGHT_MON.rect, Direction.RIGHT),
                               100 * 1720 / 1920)

    def test_vertical_edges_use_widths(self):
        top = Rect(0, 0, 500, 500)
        bottom = Rect(200, 500, 1000, 500)  # 300 of its 1000 columns touch
        self.assertEqual(facing_ratio(top, bottom, Direction.DOWN), 30.0)
        self.assertEqual(facing_ratio(bottom, top, Direction.UP), 60.0)


class MinFacingTest(unittest.TestCase):
    CURRENT = Output("current", Rect(90, 120, 120, 150))
    TARGET = Output("target", Rect(0, 0, 90, 150))
    LAYOUT = Layout.of([TARGET, CURRENT])
    PUSH = Point(90, 200)                   # below the target, pushing left

    def test_the_redirect_reports_its_facing_ratio(self):
        r = redirect_target(self.LAYOUT, self.PUSH, Direction.LEFT)
        self.assertEqual(r.to_output, "target")
        self.assertEqual(r.facing, 20.0)

    def test_below_min_facing_the_pointer_is_left_at_the_wall(self):
        self.assertIsNone(
            redirect_target(self.LAYOUT, self.PUSH, Direction.LEFT, min_facing=50))

    def test_the_threshold_itself_still_redirects(self):
        self.assertIsNotNone(
            redirect_target(self.LAYOUT, self.PUSH, Direction.LEFT, min_facing=20))

    def test_the_default_redirects_even_to_a_display_that_does_not_touch(self):
        # 0 means always redirect, including where the facing ratio is 0.
        src = Output("src", Rect(1000, 0, 1000, 1000))
        far = Output("far", Rect(0, 400, 100, 200))
        layout = Layout.of([src, far])
        r = redirect_target(layout, Point(1000, 100), Direction.LEFT)
        self.assertEqual((r.to_output, r.facing), ("far", 0.0))
        self.assertIsNone(redirect_target(layout, Point(1000, 100), Direction.LEFT,
                                          min_facing=1))

    def test_a_refused_display_is_not_swapped_for_a_further_one(self):
        # "near" is where the pointer would go, and it faces only 10%. "wide"
        # faces 100% but needs a much longer slide. Refusing "near" leaves the
        # pointer at the wall; it does not send it to "wide" instead.
        src = Output("src", Rect(1000, 0, 1000, 1000))
        near = Output("near", Rect(500, -900, 500, 1000))   # 100 of 1000 rows touch
        wide = Output("wide", Rect(500, 600, 500, 400))     # 400 of 400 rows touch
        layout = Layout.of([src, near, wide])
        r = redirect_target(layout, Point(1000, 150), Direction.LEFT, inset=0)
        self.assertEqual((r.to_output, r.facing), ("near", 10.0))
        self.assertIsNone(redirect_target(layout, Point(1000, 150), Direction.LEFT,
                                          inset=0, min_facing=50))
